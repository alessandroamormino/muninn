"""Resumable full-sync checkpoint — persists last completed batch to /app/.sync/.

Format:
  /app/.sync/ckpt_{collection_lower}.json
  {"collection": "...", "last_completed_batch": 42, "updated_at": "..."}

  last_completed_batch = -1  →  collection dropped, no batch done yet
  last_completed_batch >= 0  →  batches 0..N done, resume from N+1

Usage:
  ckpt = checkpoint.read(collection)        # None if no checkpoint
  checkpoint.write(collection, batch_num)   # after each completed batch
  checkpoint.delete(collection)             # on successful completion
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR = Path("/app/.sync")


def _path(collection: str) -> Path:
    return _CHECKPOINT_DIR / f"ckpt_{collection.lower()}.json"


def read(collection: str) -> dict | None:
    """Return checkpoint dict or None if not found / corrupted."""
    p = _path(collection)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        logger.info(
            "Checkpoint trovato per %r: last_completed_batch=%d",
            collection, data.get("last_completed_batch", -1),
        )
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Checkpoint corrotto per %r, ignorato: %s", collection, exc)
        return None


def write(collection: str, last_completed_batch: int) -> None:
    """Persist progress. Call after each batch completes embed+upsert."""
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    _path(collection).write_text(
        json.dumps({
            "collection": collection,
            "last_completed_batch": last_completed_batch,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )


def delete(collection: str) -> None:
    """Remove checkpoint after successful sync completion."""
    _path(collection).unlink(missing_ok=True)
    logger.info("Checkpoint eliminato per %r.", collection)
