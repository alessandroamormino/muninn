"""BackupCatalog — JSON catalog for backup bundles with retention/prune.

Structurally mirrored from sync/entity_state_store.py:
- JSON file at /app/.sync/backup_catalog.json
- In-memory dict: bundle_id → {collection, snapshot_name, s3_keys, created_at, size_bytes}
- _load: default-on-missing (empty dict if file absent or corrupt)
- _save: atomic tmp → replace (write-before-mutate pattern)

D-07/BAK-04: prune() keeps the newest keep_n bundles per collection,
evicting older ones. Caller is responsible for deleting their S3 keys before
calling remove() on each evicted entry.

T-28-01-02: bundle_id validated against _BUNDLE_ID_RE to prevent path traversal
into S3 keys. Pattern reused from api/upload.py _COLLECTION_RE.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_CATALOG_PATH = Path("/app/.sync/backup_catalog.json")

# ponytail: same pattern as _COLLECTION_RE (api/upload.py) — reused, not duplicated (T-28-01-02)
_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class BackupCatalog:
    """Persists backup bundle metadata atomically and prunes per-collection.

    Thread-safety: same assumptions as EntityStateStore — single-writer via
    the app.state.sync_lock (backup holds the lock during the write window).
    """

    def __init__(self, path: Path = BACKUP_CATALOG_PATH) -> None:
        self._path = path
        self._data: dict[str, dict] = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, entry: dict) -> None:
        """Add a bundle entry to the catalog and persist to disk.

        Validates bundle_id against ^[A-Za-z0-9_-]+$ (path-traversal guard).
        Write is atomic (tmp → replace); memory is mutated only on success.
        """
        bundle_id = entry.get("bundle_id", "")
        if not _BUNDLE_ID_RE.match(bundle_id):
            raise ValueError(
                f"bundle_id {bundle_id!r} must match ^[A-Za-z0-9_-]+$ "
                "(path-traversal guard T-28-01-02)"
            )
        new_data = {**self._data, bundle_id: entry}
        self._save(new_data)
        self._data = new_data

    def get(self, bundle_id: str) -> dict | None:
        """Return the entry for bundle_id or None if absent."""
        return self._data.get(bundle_id)

    def all(self) -> dict[str, dict]:
        """Return a copy of the full catalog dict."""
        return dict(self._data)

    def remove(self, bundle_id: str) -> None:
        """Remove a bundle entry from the catalog and persist.

        No-op if bundle_id is not present.
        """
        if bundle_id not in self._data:
            return
        new_data = {k: v for k, v in self._data.items() if k != bundle_id}
        self._save(new_data)
        self._data = new_data

    def prune(self, collection: str, keep_n: int) -> list[str]:
        """Evict the oldest bundles for collection beyond the keep_n newest.

        Returns the list of evicted bundle_ids (caller should delete their
        S3 keys then call remove() on each — or just let this handle removal).
        Entries for other collections are left untouched.

        This is the genuinely new logic (D-07/BAK-04): sort by created_at
        descending, keep the first keep_n, evict the rest.
        """
        # Filter entries for this collection only
        collection_entries = [
            (bid, entry)
            for bid, entry in self._data.items()
            if entry.get("collection") == collection
        ]
        if len(collection_entries) <= keep_n:
            return []

        # Sort newest first (ISO-8601 strings sort lexicographically)
        collection_entries.sort(key=lambda pair: pair[1].get("created_at", ""), reverse=True)

        keep_ids = {bid for bid, _ in collection_entries[:keep_n]}
        evicted_ids = [bid for bid, _ in collection_entries[keep_n:]]

        # Remove evicted entries atomically in one write
        new_data = {k: v for k, v in self._data.items() if k not in evicted_ids}
        self._save(new_data)
        self._data = new_data

        logger.info(
            "Prune %r: evicted %d bundles (kept %d newest).",
            collection, len(evicted_ids), keep_n,
        )
        return evicted_ids

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, dict]:
        """Load catalog from disk; return {} if file is absent or corrupt."""
        if not self._path.exists():
            return {}
        try:
            with self._path.open("r") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                logger.warning("%s is not a dict — initialised empty.", self._path)
                return {}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read %s: %s — initialised empty.", self._path, exc)
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        """Atomic write: dump to a .tmp file then replace the target."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        with tmp.open("w") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(self._path)


# ---------------------------------------------------------------------------
# Self-check (ponytail: non-trivial loop needs one runnable check, D-07)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from datetime import datetime, timezone, timedelta

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "catalog.json"
        cat = BackupCatalog(path=path)

        def _dt(minutes_ago: int) -> str:
            return (datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()

        # Add 10 entries for Products (oldest = minutes_ago=9)
        for i in range(10):
            cat.add({
                "bundle_id": f"bnd-{i:02d}",
                "collection": "Products",
                "snapshot_name": f"bnd-{i:02d}.snapshot",
                "s3_keys": {},
                "created_at": _dt(9 - i),
                "size_bytes": 100,
            })

        # Add one entry for a different collection
        cat.add({
            "bundle_id": "bnd-other",
            "collection": "Employees",
            "snapshot_name": "bnd-other.snapshot",
            "s3_keys": {},
            "created_at": _dt(0),
            "size_bytes": 50,
        })

        evicted = cat.prune("Products", keep_n=7)

        # Assertions
        assert len(evicted) == 3, f"Expected 3 evicted, got {len(evicted)}: {evicted}"
        assert set(evicted) == {"bnd-00", "bnd-01", "bnd-02"}, f"Wrong evicted set: {evicted}"
        remaining_products = [k for k, v in cat.all().items() if v["collection"] == "Products"]
        assert len(remaining_products) == 7, f"Expected 7 remaining, got {len(remaining_products)}"
        assert cat.get("bnd-other") is not None, "Other collection entry was wrongly evicted"
        for bid in evicted:
            assert cat.get(bid) is None, f"Evicted entry {bid!r} still in catalog"

        print("Self-check PASSED: keep_n=7 over 10 entries evicted the 3 oldest; other collection untouched.")
