"""quantization config change detection and persistence (Phase 24).

Mirrors search_mode_state.py exactly, adapted for Qdrant quantization config.

A single JSON file keyed by collection name stores the quantization key for
each entity. On startup, detect_quantization_change() compares the persisted
compound key against the current config; a mismatch triggers full re-index.

Compound key format: "{quant_type}|{on_disk}" (e.g. "sq|True", "none|False")

File path: /app/.sync/quantization_state.json  (inside the sync_data volume)
File format: {"CollectionName": "none|False", "OtherEntity": "sq|True"}

Atomic write: uses tmp file + replace() — identical to search_mode_state.py.
# atomic on POSIX (same note as in search_mode_state.py)

First-run semantics: if the file or the collection key is absent, returns False
(no re-index needed on first sync). write_stored_quantization_key() is called
inside QdrantVectorStore.create_index() after collection creation.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Default path — same volume as model_version.json and sync_logs.db
QUANTIZATION_STATE_PATH = Path("/app/.sync/quantization_state.json")


def _quant_key(cfg: Any) -> str:
    """Compute compound quantization key from cfg.

    Combines quantization type and on_disk flag into a stable string key.
    Used for change detection: stored key vs. current key.

    Examples:
        "none|False"   — no quantization, vectors in RAM (default)
        "sq|True"      — Scalar Quantization + vectors on disk (memmap)
        "bq|False"     — Binary Quantization, vectors in RAM
    """
    qdrant_opts = getattr(cfg.vector_store, "qdrant_opts", None)
    q_type = (
        getattr(getattr(qdrant_opts, "quantization", None), "type", "none")
        if qdrant_opts else "none"
    )
    on_disk = bool(getattr(qdrant_opts, "on_disk", False)) if qdrant_opts else False
    return f"{q_type}|{on_disk}"


def read_stored_quantization_key(
    collection: str,
    path: Path = QUANTIZATION_STATE_PATH,
) -> str | None:
    """Return the persisted quantization key for collection, or None if absent.

    Returns None when:
    - The file does not exist (first run)
    - The collection key is not in the file (new entity)
    - The file is corrupted/unreadable
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data.get(collection)
    except Exception:  # noqa: BLE001
        return None


def write_stored_quantization_key(
    collection: str,
    key: str,
    path: Path = QUANTIZATION_STATE_PATH,
) -> None:
    """Persist quantization key for collection (atomic write).

    Creates the parent directory if missing.
    Merges with existing entries — other collection entries are preserved.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            pass
    existing[collection] = key
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing))
    tmp.replace(path)  # atomic on POSIX
    logger.info("Persisted quantization_key %r for collection %r to %s", key, collection, path)


def detect_quantization_change(
    collection: str,
    cfg: Any,
    path: Path = QUANTIZATION_STATE_PATH,
) -> bool:
    """Return True if the persisted quantization key differs from current config.

    Returns False on first run (stored is None) — no re-index needed.
    Returns True when a quantization/on_disk change is detected — caller must
    drop_index() before create_index().

    Args:
        collection: Qdrant collection name (used as JSON key)
        cfg:        AppConfig with cfg.vector_store.qdrant_opts (current config)
        path:       path to the state file (injectable for testing)
    """
    stored = read_stored_quantization_key(collection, path)
    if stored is None:
        # First run or new entity — no re-index needed.
        return False
    current = _quant_key(cfg)
    return stored != current
