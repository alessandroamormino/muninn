"""Shared entity-active gate — used by /search and all sync endpoints (D-10/D-11).

Single source of truth for the 409 "entity unloaded" check. Do NOT inline this
logic in search.py/sync.py/upload.py — always call _assert_entity_active.
"""
from __future__ import annotations

from fastapi import HTTPException


def _assert_entity_active(state_store, collection: str) -> None:
    """Raise HTTPException(409) if `collection` is 'unloaded' in `state_store`.

    Defensive: if state_store is None (e.g. not wired, legacy tests), does
    nothing — never crashes when the entity state store isn't available.
    """
    if state_store is None:
        return
    status = state_store.get_status(collection)
    if status == "unloaded":
        raise HTTPException(
            status_code=409,
            detail=f"Entity '{collection}' is unloaded — POST /collections/{collection}/load to restore",
        )
