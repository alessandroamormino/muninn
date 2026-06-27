"""Config API router — GET/PUT /config/{collection}, POST /config.

Provides CRUD operations for per-entity config.yaml files stored under
_CONFIG_ROOT/{collection}/config.yaml. Path traversal is guarded by
_COLLECTION_RE imported from api/upload.py (T-14.1-01).
"""
from __future__ import annotations

import logging
import yaml
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.dependencies import get_current_user, require_admin
from auth.user_store import UserRecord

# Import shared symbols from api/upload.py — single source of truth (T-14.1-01)
from api.upload import _write_config, _CONFIG_ROOT, _COLLECTION_RE, _ENV_VAR_RE, _resolve_config_path

logger = logging.getLogger(__name__)
router = APIRouter(tags=["config"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ConfigUpdateRequest(BaseModel):
    yaml: str


class CreateConfigRequest(BaseModel):
    collection: str
    source_type: str
    port: int = 3306
    host_env_var: str
    db_env_var: str
    user_env_var: str
    password_env_var: str
    from_table: str
    fields: list[str]
    id_field: str
    text_fields: list[str]
    metadata_fields: list[str] = []
    output_fields: list[str] = []
    search_mode: str = "hybrid"


# ---------------------------------------------------------------------------
# GET /config/{collection}
# ---------------------------------------------------------------------------

@router.get("/config/{collection}")
async def get_config(
    collection: str,
    _: UserRecord = Depends(get_current_user),
) -> dict:
    """Return raw YAML config for the named entity.

    T-14.1-01: collection param validated by _COLLECTION_RE BEFORE any filesystem I/O.
    T-14.1-06: any authenticated user can read (not admin-only).
    """
    if not _COLLECTION_RE.match(collection):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    config_path = _resolve_config_path(collection)
    if config_path is None:
        raise HTTPException(status_code=404, detail=f"No config found for '{collection}'")
    return {"yaml": config_path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------------
# PUT /config/{collection}
# ---------------------------------------------------------------------------

@router.put("/config/{collection}")
async def update_config(
    collection: str,
    body: ConfigUpdateRequest,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Validate and write YAML config for the named entity.

    T-14.1-01: collection param validated BEFORE any filesystem I/O.
    T-14.1-02: yaml.safe_load validates before write; non-mapping bodies rejected.
    T-14.1-03: safe_load prevents billion-laughs and arbitrary tag abuse.
    T-14.1-06: admin-only write.
    """
    if not _COLLECTION_RE.match(collection):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    config_path = _resolve_config_path(collection)
    if config_path is None:
        raise HTTPException(status_code=404, detail=f"No config found for '{collection}'")
    # T-14.1-02: validate YAML before writing
    try:
        parsed = yaml.safe_load(body.yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}")
    # Pitfall 1: empty string returns None; list is not a mapping
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=422,
            detail="YAML must be a non-empty mapping",
        )
    # Write verbatim — no yaml.dump round-trip so operator-supplied 'on' keys survive (YAML 1.1 pitfall)
    config_path.write_text(body.yaml, encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /config
# ---------------------------------------------------------------------------

@router.post("/config", status_code=status.HTTP_201_CREATED)
async def create_config(
    body: CreateConfigRequest,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Create a new entity directory with config.yaml for MySQL source.

    T-14.1-01: collection name validated by _COLLECTION_RE.
    T-14.1-04: credentials stored as ${VAR} placeholders only (never raw values).
    T-14.1-05: 409 if config already exists (no silent overwrite).
    T-14.1-06: admin-only.
    """
    if not _COLLECTION_RE.match(body.collection):
        raise HTTPException(status_code=422, detail="Invalid collection name")

    # T-14.1-04: validate all env var names (D-19/D-20)
    for name in (body.host_env_var, body.db_env_var, body.user_env_var, body.password_env_var):
        if not _ENV_VAR_RE.match(name):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid env var name: {name!r} (must match ^[A-Z][A-Z0-9_]*$)",
            )

    config_path = _CONFIG_ROOT / body.collection / "config.yaml"
    # T-14.1-05: 409 on duplicate — do NOT overwrite silently
    if config_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Entity '{body.collection}' already exists",
        )

    # Build MySQL source dict with ${VAR} placeholders (D-19/D-20)
    # Fields nested under source.mysql as expected by MySQLConfig/SourceConfig
    source: dict = {
        "type": "mysql",
        "mysql": {
            "host": f"${{{body.host_env_var}}}",
            "port": body.port,
            "database": f"${{{body.db_env_var}}}",
            "user": f"${{{body.user_env_var}}}",
            "password": f"${{{body.password_env_var}}}",
            "query": {
                "from": body.from_table,
                "fields": body.fields,
                "id_field": body.id_field,
                "hash_fields": body.fields,
            },
        },
    }

    _write_config(
        collection=body.collection,
        source=source,
        text_fields=body.text_fields,
        metadata_fields=body.metadata_fields,
        output_fields=body.output_fields,
        id_field=body.id_field,
        search_mode=body.search_mode,
    )

    return {"collection": body.collection}
