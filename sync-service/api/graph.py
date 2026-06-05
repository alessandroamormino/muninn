"""Graph API router — GET /graph/{collection}, GET /collections.

GET /graph/{collection}:
  - Validates collection name against _COLLECTION_RE (path-traversal guard, T-11-01).
  - Fetches up to max_nodes vectors from Weaviate using collection.iterator(include_vector=True).
  - Runs UMAP (2D reduction) + sklearn.cluster.HDBSCAN (clustering).
  - Derives K-NN edges from UMAP's internal knn graph (reducer.graph_) — avoids N Weaviate roundtrips.
  - Names clusters via Ollama qwen2.5:3b with graceful fallback (D-15, Pitfall 5).
  - Returns {"nodes": [...], "edges": [...], "clusters": [...]}.
  - DoS guard: max_nodes capped at 2000 (T-11-02).
  - Error detail not leaked to client (T-11-05).

GET /collections:
  - Scans _CONFIG_ROOT for subdirectories containing config.yaml.
  - Returns {"collections": [...]} sorted alphabetically.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import numpy as np
import umap
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sklearn.cluster import HDBSCAN

from auth.dependencies import get_current_user
from auth.user_store import UserRecord

from api.setup import _sanitize_cell
from config.settings import _CONFIG_PATH, AppConfig, load_config, settings
from llm.ollama_llm import LLMError, OllamaLLMClient

logger = logging.getLogger(__name__)
router = APIRouter()
_CONFIG_ROOT = _CONFIG_PATH.parent  # configuration/ dir (container) or project_root/configuration/ (host)

# Path-traversal guard: only alphanumerics, underscores, hyphens allowed (T-11-01).
_COLLECTION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Override default qwen2.5:1.5b — graph needs larger model for cluster naming (D-15).
_GRAPH_LLM_MODEL = "qwen2.5:3b"


def _load_collection_config(collection: str) -> AppConfig:
    """Load config.yaml for a specific collection, falling back to global settings."""
    path = _CONFIG_ROOT / collection / "config.yaml"
    if path.exists():
        try:
            return load_config(path)
        except Exception:  # noqa: BLE001
            pass
    return settings


def _validate_collection_name(name: str) -> None:
    """Raise HTTPException(422) if name is not a safe collection identifier.

    Called BEFORE any filesystem or Weaviate access (T-11-01).
    """
    if not _COLLECTION_RE.match(name):
        raise HTTPException(status_code=422, detail="Invalid collection name")


@router.get("/collections")
async def list_collections(_: UserRecord = Depends(get_current_user)) -> dict:
    """List all configured collections by scanning configuration/ subdirectories.

    Returns {"collections": [{name, source_type, is_global}, ...]} sorted alphabetically.
    Each directory under _CONFIG_ROOT that contains a config.yaml is a valid collection.
    The root configuration/config.yaml (global fallback) is appended last with is_global=True,
    only when no per-entity directory already claims its collection name.
    source_type is read from the config; falls back to "unknown" on parse error.
    """
    if not _CONFIG_ROOT.exists():
        return {"collections": []}
    items = []
    for d in sorted(_CONFIG_ROOT.iterdir()):
        if not (d.is_dir() and (d / "config.yaml").exists()):
            continue
        source_type = "unknown"
        try:
            cfg = load_config(d / "config.yaml")
            source_type = cfg.source.type
        except Exception:  # noqa: BLE001
            pass
        items.append({"name": d.name, "source_type": source_type, "is_global": False})

    global_yaml = _CONFIG_ROOT / "config.yaml"
    if global_yaml.exists():
        try:
            gcfg = load_config(global_yaml)
            gname = gcfg.vector_store.collection
            if not any(it["name"] == gname for it in items):
                items.append({"name": gname, "source_type": gcfg.source.type, "is_global": True})
        except Exception:  # noqa: BLE001
            pass

    return {"collections": items}


@router.get("/graph/{collection}")
async def get_graph(
    request: Request,
    collection: str,
    max_nodes: int = Query(default=2000, ge=10, le=2000),
    _: UserRecord = Depends(get_current_user),
) -> dict:
    """Compute and return a knowledge graph for the given collection.

    Steps:
    1. Validate collection name (regex guard — T-11-01, BEFORE any I/O).
    2. Fetch up to max_nodes vectors via BaseVectorStore.get_vectors_for_graph()
       (T-11-02 DoS cap). Returns None for FTS-only collections (D-10).
    3. UMAP 2D dimensionality reduction (Pitfall 3: n_neighbors guard).
    4. sklearn HDBSCAN clustering (Pitfall 7: no standalone hdbscan).
    5. K-NN edges from reducer.graph_ (avoids N roundtrips).
    6. LLM cluster naming via Ollama qwen2.5:3b (D-15), graceful fallback (Pitfall 5).

    Returns {"nodes": [...], "edges": [...], "clusters": [...]}.
    """
    # Step 1: path-traversal guard BEFORE any I/O (T-11-01, T-15-02)
    _validate_collection_name(collection)

    col_settings = _load_collection_config(collection)

    try:
        # Step 2: fetch vectors via abstraction layer (engine-agnostic)
        vector_store = request.app.state.vector_store
        raw_points = vector_store.get_vectors_for_graph(collection, max_nodes=max_nodes)

        # D-10: FTS-only collections have no dense vectors — graph is disabled.
        if raw_points is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Knowledge Graph non disponibile con search_mode: fts. "
                    "Cambia modalità a hybrid o vector per abilitarlo."
                ),
            )

        vectors: list[np.ndarray] = [
            np.asarray(pt["vector"], dtype=np.float32) for pt in raw_points
        ]
        records: list[dict] = [pt["payload"] for pt in raw_points]

        if len(vectors) < 10:
            raise HTTPException(
                status_code=422,
                detail="Collection has too few records for graph generation (minimum 10)",
            )

        arr = np.stack(vectors)

        # Step 3: UMAP 2D — n_neighbors guard (Pitfall 3)
        n_neighbors = min(15, len(arr) - 1)
        reducer = umap.UMAP(
            n_components=2,
            n_neighbors=n_neighbors,
            min_dist=0.1,
            random_state=42,
        )
        coords = reducer.fit_transform(arr)

        # Step 4: HDBSCAN clustering (sklearn, avoids Cython build — Pitfall 7)
        clusterer = HDBSCAN(min_cluster_size=5)
        labels = clusterer.fit_predict(coords)

        # Step 5: K-NN edges from UMAP's internal knn graph (avoids 2000 Weaviate roundtrips)
        edges: list[dict] = []
        try:
            knn = reducer.graph_.tocoo()
            seen: set[tuple[int, int]] = set()
            for i, j in zip(knn.row, knn.col):
                if i == j:
                    continue
                a, b = (int(i), int(j)) if i < j else (int(j), int(i))
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                edges.append({"source": records[a]["id"], "target": records[b]["id"]})
        except Exception:  # noqa: BLE001
            # Graph is still usable without edges
            edges = []

        # Compute node degree for radius scaling
        degree: dict[str, int] = {}
        for e in edges:
            degree[e["source"]] = degree.get(e["source"], 0) + 1
            degree[e["target"]] = degree.get(e["target"], 0) + 1
        max_deg = max(degree.values(), default=1)

        nodes = []
        for idx, rec in enumerate(records):
            d = degree.get(rec["id"], 0)
            radius = 6 + int(14 * (d / max_deg)) if max_deg else 6
            nodes.append({
                "id": rec["id"],
                "x": float(coords[idx][0]),
                "y": float(coords[idx][1]),
                "cluster": int(labels[idx]),
                "radius": radius,
                "props": {k: v for k, v in rec.items() if k != "id"},
            })

        # Step 6: LLM cluster naming with graceful fallback (D-15, Pitfall 5)
        # Use a dedicated helper to POST with qwen2.5:3b without mutating the
        # module-level _LLM_MODEL constant (thread-unsafe).
        def _call_graph_llm(llm_client: OllamaLLMClient, prompt: str) -> dict:
            import json as _json  # noqa: PLC0415

            import requests as _req  # noqa: PLC0415

            try:
                resp = _req.post(
                    llm_client._generate_url,
                    json={
                        "model": _GRAPH_LLM_MODEL,
                        "prompt": prompt,
                        "format": "json",
                        "stream": False,
                    },
                    timeout=120,
                )
            except _req.exceptions.RequestException as exc:
                raise LLMError(f"Ollama request failed: {exc}") from exc
            if not resp.ok:
                raise LLMError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}")
            raw = resp.json().get("response", "")
            try:
                return _json.loads(raw)
            except _json.JSONDecodeError as exc:
                raise LLMError(f"Ollama response is not valid JSON: {raw[:200]}") from exc

        try:
            _graph_llm: OllamaLLMClient | None = OllamaLLMClient(settings.embedding)
        except Exception:  # noqa: BLE001
            _graph_llm = None

        cluster_ids = sorted({int(lbl) for lbl in labels if int(lbl) != -1})
        clusters = []
        for cid in cluster_ids:
            size = int(sum(1 for lbl in labels if int(lbl) == cid))
            sample_titles = [
                _sanitize_cell(str(next(iter(n["props"].values()), n["id"])))
                for n in nodes[:5]
                if n["cluster"] == cid
            ]
            name = f"Cluster {cid}"
            if _graph_llm is not None:
                prompt = (
                    "Suggest a short 2-4 word label for a cluster of records. "
                    f"Examples: {sample_titles}. "
                    'Respond in JSON: {"name": "..."}'
                )
                try:
                    result = _call_graph_llm(_graph_llm, prompt)
                    if isinstance(result, dict) and result.get("name"):
                        name = str(result["name"])[:50]
                except Exception:  # noqa: BLE001
                    pass  # keep fallback name "Cluster {cid}"
            clusters.append({"id": cid, "name": name, "size": size})

        # Weaviate lowercases the first letter of every property name.
        # Normalize filter_fields to match actual node prop keys so the
        # frontend lookup (n.props[field]) resolves correctly.
        filter_fields = [
            f[0].lower() + f[1:] if f else f
            for f in col_settings.graph.filter_fields
        ]

        return {
            "nodes": nodes,
            "edges": edges,
            "clusters": clusters,
            "filter_fields": filter_fields,
        }

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # T-11-05: log full detail server-side, return only generic message to client
        logger.error(
            "graph generation failed for %r: %s", collection, exc, exc_info=True
        )
        raise HTTPException(status_code=503, detail="Graph generation failed")
