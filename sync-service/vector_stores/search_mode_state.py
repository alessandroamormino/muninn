"""search_mode change detection and persistence (VS-06, VS-07, D-09).

Mirrors the model_version.json pattern in weaviate_store/model_version.py.

A single JSON file keyed by collection name stores the active search_mode for
each entity. On startup, detect_search_mode_change() compares the persisted
value against the current config; a mismatch triggers full re-index (D-09).

File path: /app/.sync/search_mode_state.json  (inside the sync_data volume)
File format: {"CollectionName": "hybrid", "OtherEntity": "fts"}

Atomic write: uses tmp file + replace() — identical to model_version.py.
# atomic on POSIX (same note as in model_version.py)

First-run semantics: if the file or the collection key is absent, returns False
(no re-index needed on first sync). write_stored_search_mode() is called inside
QdrantVectorStore.create_index() after collection creation.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: Default path — same volume as model_version.json and sync_logs.db
SEARCH_MODE_STATE_PATH = Path("/app/.sync/search_mode_state.json")


def read_stored_search_mode(
    collection: str,
    path: Path = SEARCH_MODE_STATE_PATH,
) -> str | None:
    """Return the persisted search_mode for collection, or None if absent.

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


def write_stored_search_mode(
    collection: str,
    mode: str,
    path: Path = SEARCH_MODE_STATE_PATH,
) -> None:
    """Persist search_mode for collection (atomic write).

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
    existing[collection] = mode
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing))
    tmp.replace(path)  # atomic on POSIX
    logger.info("Persisted search_mode %r for collection %r to %s", mode, collection, path)


def detect_search_mode_change(
    collection: str,
    current_mode: str,
    path: Path = SEARCH_MODE_STATE_PATH,
) -> bool:
    """Return True if the persisted search_mode differs from current_mode.

    Returns False on first run (stored is None) — no re-index needed.
    Returns True when a mode change is detected — caller must drop_index() before create_index().

    Args:
        collection:   Weaviate/Qdrant collection name (used as JSON key)
        current_mode: mode from current config.yaml (e.g. "fts")
        path:         path to the state file (injectable for testing)
    """
    stored = read_stored_search_mode(collection, path)
    if stored is None:
        # First run or new entity — no re-index needed.
        return False
    return stored != current_mode
