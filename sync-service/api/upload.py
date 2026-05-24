"""Upload API router — POST /upload, POST /upload/confirm, GET /upload/status,
POST /upload/restapi, POST /sync/full/by-collection."""
from __future__ import annotations

import datetime as _dt
import logging
import re
import time
import yaml
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, field_validator

from auth.dependencies import get_current_user, require_admin
from auth.user_store import UserRecord

from config.settings import _CONFIG_PATH, load_config, settings
from sync.engine import SyncEngine
from sync.state_store import StateStore
from weaviate_store import get_client

# Reuse LLM pipeline — D-10 mandates no duplication
from api.setup import (
    _collection_from_filename,
    _read_csv_sample,
    _build_prompt,
    _validate_suggested_fields,
)
from llm.ollama_llm import LLMError, OllamaLLMClient

logger = logging.getLogger(__name__)
router = APIRouter()

_DATA_ROOT = Path("/app/data")
_ALLOWED_CONTENT_TYPES = {"text/csv", "application/csv", "text/plain", "application/octet-stream"}
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB hard cap (CR-01)

# WR-04: derive config root from settings.py three-hop resolution instead of hardcoding /app/configuration
_CONFIG_ROOT = _CONFIG_PATH.parent  # /app/configuration (container) or project_root/configuration (host)

# T-11-07/T-11-08: path traversal guard for collection names
_COLLECTION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
# T-11-09: env var name validation — only valid uppercase identifiers allowed
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ConfirmRequest(BaseModel):
    file_name: str          # original uploaded filename, e.g. "collaboratori.csv"
    collection: str         # PascalCase collection name, e.g. "Collaboratori"
    id_field: str
    text_fields: list[str]
    metadata_fields: list[str]
    output_fields: list[str]
    delimiter: str = ","

    @field_validator("delimiter")
    @classmethod
    def _single_char(cls, v: str) -> str:
        """WR-01: csv.reader rejects multi-character delimiters with csv.Error."""
        if len(v) != 1:
            raise ValueError("delimiter must be exactly one character")
        return v


async def _stream_to_file(upload: UploadFile, dest: Path) -> None:
    """CR-01: stream upload in chunks with a hard size cap to prevent OOM."""
    written = 0
    with dest.open("wb") as fh:
        while chunk := await upload.read(65536):
            written += len(chunk)
            if written > _MAX_UPLOAD_BYTES:
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds maximum allowed size ({_MAX_UPLOAD_BYTES // 1_048_576} MB)",
                )
            fh.write(chunk)


class ConfirmRestApiRequest(BaseModel):
    """Request body for POST /upload/restapi — REST API source configuration."""
    collection: str
    url: str
    id_field: str
    text_fields: list[str]
    metadata_fields: list[str]
    output_fields: list[str]
    auth_type: str = "none"          # none | bearer | api_key_header | api_key_param | basic
    auth_env_var: str | None = None  # env var NAME only — never the value (D-19/D-20)
    auth_header_name: str | None = None
    auth_param_name: str | None = None
    pagination_type: str = "none"   # none | offset | page | cursor
    pagination_next_key: str | None = None
    json_key: str | None = None

    @field_validator("collection")
    @classmethod
    def _valid_collection(cls, v: str) -> str:
        if not _COLLECTION_RE.match(v):
            raise ValueError("collection must match ^[a-zA-Z0-9_-]+$")
        return v

    @field_validator("auth_env_var")
    @classmethod
    def _valid_env_var(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _ENV_VAR_RE.match(v):
            raise ValueError("auth_env_var must be an uppercase env var name (e.g. TMDB_BEARER_TOKEN)")
        return v


def _write_config(
    *,
    collection: str,
    source: dict,
    text_fields: list[str],
    metadata_fields: list[str],
    output_fields: list[str],
    id_field: str,
) -> Path:
    """Write per-entity config.yaml. Creates directory if needed. D-01/D-02.

    Accepts a pre-built source dict so it can handle both csv and rest_api sources.
    """
    config_dir = _CONFIG_ROOT / collection   # WR-04: use resolved root, not hardcoded /app/configuration
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"
    cfg_dict = {
        "source": source,
        "embedding": {
            "type":     settings.embedding.type,
            "model":    settings.embedding.model,
            "endpoint": settings.embedding.endpoint,
        },
        "weaviate": {
            "collection":      collection,
            "text_fields":     text_fields,
            "metadata_fields": metadata_fields,
        },
        "sync": {
            "mode":        "full",
            "hash_fields": [id_field],
            "schedule":    "manual",
        },
        "api": {
            "output_fields":  output_fields,
            "default_limit":  settings.api.default_limit,
            "max_limit":      settings.api.max_limit,
        },
    }
    config_path.write_text(yaml.dump(cfg_dict, default_flow_style=False), encoding="utf-8")
    return config_path


def _run_upload_sync_bg(app_state, config_path: Path, collection_hint: str = "") -> None:
    """Background task for upload-triggered full sync.

    IMPORTANT differences from _run_sync_bg:
    - Uses load_config(config_path) — NOT the global settings singleton (D-04)
    - Creates a fresh SyncEngine — does NOT touch app_state.sync_engine (D-03)
    - Uses per-entity StateStore path — NOT the default /app/.sync/sync_state.json (A3)
    - Always calls run_full() — D-06
    - LogStore.record() uses temp_cfg fields, not settings.* — avoids Pitfall 5
    - Releases sync_lock in finally — mirrors sync.py line 96
    - collection_hint: best-effort fallback for error-path logging (WR-02)
    """
    _t0 = time.perf_counter()
    _started_at = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
    _log_store = getattr(app_state, "log_store", None)

    # --- Progress tracking (mirrors _run_sync_bg in api/sync.py) ------------
    app_state.sync_progress = {"phase": "fetching", "total": 0, "done": 0, "percent": 0.0,
                               "elapsed_seconds": 0, "eta_seconds": None}

    def _on_progress(phase: str, done: int, total: int) -> None:
        elapsed = time.perf_counter() - _t0
        percent = round(done / total * 100, 1) if total > 0 else 0.0
        eta = int(elapsed / done * (total - done)) if done > 0 else None
        app_state.sync_progress = {
            "phase": phase,
            "total": total,
            "done": done,
            "percent": percent,
            "elapsed_seconds": int(elapsed),
            "eta_seconds": eta,
        }
    # -------------------------------------------------------------------------

    try:
        temp_cfg = load_config(config_path)                        # D-04
        collection_lower = temp_cfg.weaviate.collection.lower()
        # Per-entity StateStore: avoids wiping global sync_state.json (A3)
        temp_state = StateStore(Path("/app/.sync") / f"state_{collection_lower}.json")
        engine = SyncEngine(temp_cfg, get_client(), temp_state)   # D-03/D-04

        if app_state.upload_status:
            app_state.upload_status["status"] = "syncing"

        result = engine.run_full(on_progress=_on_progress)        # always full — D-06

        app_state.sync_status = {"status": "completed", "last_run": {**result}}
        if app_state.upload_status:
            app_state.upload_status["status"] = "done"
            app_state.upload_status["sync_status"] = app_state.sync_status

        took_ms = int((time.perf_counter() - _t0) * 1000)
        app_state.sync_status["last_run"]["took_ms"] = took_ms

        if _log_store is not None:
            _log_store.record(
                started_at=_started_at,
                finished_at=_dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
                type="full",                              # D-07
                status="completed",
                took_ms=took_ms,
                model=temp_cfg.embedding.model,           # temp_cfg, NOT settings — Pitfall 5
                source_type=temp_cfg.source.type,
                collection=temp_cfg.weaviate.collection,
                inserted=result.get("inserted", 0),
                updated=result.get("updated", 0),
                skipped_records=result.get("skipped", 0),
                errors=result.get("errors", 0),
                error_message=None,
                reason=None,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Upload sync failed: %s", exc, exc_info=True)
        took_ms = int((time.perf_counter() - _t0) * 1000)
        app_state.sync_status = {
            "status": "failed",
            "last_run": {
                "error": str(exc),
                "took_ms": took_ms,
                "timestamp": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
            },
        }
        if app_state.upload_status:
            app_state.upload_status["status"] = "failed"
        if _log_store is not None:
            _log_store.record(
                started_at=_started_at,
                finished_at=_dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
                type="full",
                status="failed",
                took_ms=took_ms,
                model="",
                source_type="csv",
                collection=collection_hint,  # WR-02: best-effort fallback when temp_cfg unavailable
                inserted=0,
                updated=0,
                skipped_records=0,
                errors=1,
                error_message=str(exc),
                reason=None,
            )
    finally:
        app_state.sync_progress = None  # clear progress when done
        app_state.sync_lock.release()   # mirrors sync.py line 96


@router.post("/upload")
async def upload_file(
    request: Request,
    file: Annotated[UploadFile, File(description="CSV file to index")],
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Receive a CSV file, save to /app/data/, run LLM suggest-config, return suggestion."""
    # Content-type check — D-11: CSV only
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail="Only CSV files are accepted")

    # CR-02: reject if a file with this name is already indexed to avoid silent data loss.
    # Layer 1: strip any directory prefix to prevent path traversal (WR-03).
    filename = Path(file.filename or "upload.csv").name
    dest = _DATA_ROOT / filename

    # Layer 2: resolve + relative_to guards against symlink escape even if .name is ever removed.
    try:
        dest.resolve().relative_to(_DATA_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=422, detail="path not allowed")

    if dest.exists():
        raise HTTPException(
            status_code=409,
            detail=f"A file named '{filename}' already exists. Delete or rename it before re-uploading.",
        )

    # CR-01: stream in chunks with a 500 MB hard cap — prevents OOM on large uploads.
    await _stream_to_file(file, dest)

    # --- LLM suggest-config pipeline (D-10: import from api/setup.py) ---
    headers, rows = _read_csv_sample(dest)
    if not headers:
        raise HTTPException(status_code=422, detail="CSV file has no columns")

    collection = _collection_from_filename(filename)
    llm = OllamaLLMClient(settings.embedding)
    prompt = _build_prompt(headers, rows)

    try:
        llm_result = llm.generate(prompt)
    except LLMError as exc:
        logger.warning("LLM call failed: %s", exc)
        raise HTTPException(status_code=503, detail="LLM unavailable — make sure Ollama is running")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("upload LLM call failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="LLM unavailable — make sure Ollama is running")

    try:
        _validate_suggested_fields(llm_result, headers)
    except ValueError as exc:
        logger.warning("LLM suggested non-existent fields: %s", exc)
        llm_result["_warning"] = str(exc)

    suggested_config = {
        "id_field":        llm_result.get("id_field", ""),
        "collection":      collection,
        "text_fields":     llm_result.get("text_fields", []),
        "metadata_fields": llm_result.get("metadata_fields", []),
        "output_fields":   llm_result.get("output_fields", []),
        "delimiter":       ",",
        "file_name":       filename,
    }

    # Update in-memory status — D-08/D-09
    request.app.state.upload_status = {
        "file_name":   filename,
        "collection":  collection,
        "status":      "uploaded",
        "config_path": None,
        "sync_status": None,
    }

    response: dict = {"suggested_config": suggested_config, "reasoning": llm_result.get("reasoning", {})}
    if "_warning" in llm_result:
        response["_warning"] = llm_result["_warning"]
    return response


@router.post("/upload/confirm")
async def confirm_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    body: ConfirmRequest,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Write per-entity config.yaml and start a full sync in background. D-05/D-06."""
    # CR-03: lock_acquired flag ensures only one code path owns the release.
    # If _write_config raises, we release here. If add_task succeeds, the background
    # task's finally owns the release — we clear the flag so the except branch skips it.
    lock_acquired = False
    try:
        if not request.app.state.sync_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Sync already in progress")
        lock_acquired = True
        config_path = _write_config(
            collection=body.collection,
            source={
                "type": "csv",
                "file_path": f"./data/{body.file_name}",
                "id_field": body.id_field,
                "delimiter": body.delimiter,
            },
            text_fields=body.text_fields,
            metadata_fields=body.metadata_fields,
            output_fields=body.output_fields,
            id_field=body.id_field,
        )                                                          # D-01/D-02
        request.app.state.upload_status = request.app.state.upload_status or {}
        request.app.state.upload_status["status"] = "confirmed"   # update after lock — Pitfall 4
        request.app.state.upload_status["config_path"] = str(config_path)
        request.app.state.sync_status = request.app.state.sync_status or {}
        request.app.state.sync_status["status"] = "running"
        background_tasks.add_task(_run_upload_sync_bg, request.app.state, config_path, body.collection)
        lock_acquired = False  # background task now owns the lock release
    except Exception:
        if lock_acquired:
            request.app.state.sync_lock.release()
        raise
    return {"status": "started", "collection": body.collection, "config_path": str(config_path)}


@router.get("/upload/status")
async def upload_status(request: Request, _: UserRecord = Depends(get_current_user)) -> dict:
    """Returns in-memory upload state. None before first upload. D-08/D-09."""
    return request.app.state.upload_status or {}


@router.post("/upload/restapi")
async def create_restapi_entity(request: Request, body: ConfirmRestApiRequest, _: UserRecord = Depends(require_admin)) -> dict:
    """Write per-entity config.yaml for a REST API source. D-19/D-20: stores only env var NAME."""
    # Build auth dict — D-20: token is a ${VAR} placeholder, never the actual value
    auth_dict: dict = {"type": body.auth_type}
    if body.auth_type == "bearer" and body.auth_env_var:
        auth_dict["token"] = f"${{{body.auth_env_var}}}"
    elif body.auth_type == "api_key_header" and body.auth_env_var:
        auth_dict["header_name"] = body.auth_header_name or "X-Api-Key"
        auth_dict["key"] = f"${{{body.auth_env_var}}}"
    elif body.auth_type == "api_key_param" and body.auth_env_var:
        auth_dict["param_name"] = body.auth_param_name or "api_key"
        auth_dict["key"] = f"${{{body.auth_env_var}}}"
    # Build pagination dict
    pag_dict: dict = {"type": body.pagination_type}
    if body.pagination_type == "cursor" and body.pagination_next_key:
        pag_dict["next_key"] = body.pagination_next_key
    source: dict = {
        "type": "rest_api",
        "url": body.url,
        "id_field": body.id_field,
        "auth": auth_dict,
        "pagination": pag_dict,
    }
    if body.json_key:
        source["json_key"] = body.json_key
    config_path = _write_config(
        collection=body.collection,
        source=source,
        text_fields=body.text_fields,
        metadata_fields=body.metadata_fields,
        output_fields=body.output_fields,
        id_field=body.id_field,
    )
    return {"status": "created", "collection": body.collection, "config_path": str(config_path)}


@router.post("/sync/full/by-collection")
async def trigger_full_sync_by_collection(
    request: Request,
    background_tasks: BackgroundTasks,
    collection: Annotated[str, Query(min_length=1)],
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Trigger a full sync for a specific collection. T-11-07: validates collection name."""
    if not _COLLECTION_RE.match(collection):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    config_path = _CONFIG_ROOT / collection / "config.yaml"
    if not config_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No config found for collection '{collection}'",
        )
    lock_acquired = False
    try:
        if not request.app.state.sync_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Sync already in progress")
        lock_acquired = True
        request.app.state.upload_status = request.app.state.upload_status or {}
        request.app.state.upload_status["status"] = "syncing"
        request.app.state.sync_status = request.app.state.sync_status or {}
        request.app.state.sync_status["status"] = "running"
        background_tasks.add_task(_run_upload_sync_bg, request.app.state, config_path, collection)
        lock_acquired = False  # background task now owns the lock release
    except Exception:
        if lock_acquired:
            request.app.state.sync_lock.release()
        raise
    return {"status": "started", "collection": collection}
