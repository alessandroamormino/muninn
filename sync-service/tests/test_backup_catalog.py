"""Tests for backup/catalog.py — BackupCatalog.

Uses tmp_path so no real /app/.sync is needed.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


def _make_entry(
    bundle_id: str,
    collection: str = "Products",
    created_at: str | None = None,
) -> dict:
    return {
        "bundle_id": bundle_id,
        "collection": collection,
        "snapshot_name": f"{bundle_id}.snapshot",
        "s3_keys": {
            "snapshot": f"backups/{bundle_id}.snapshot",
            "state_tar": f"backups/{bundle_id}.tar.gz",
            "manifest": f"backups/{bundle_id}.manifest.json",
        },
        "created_at": created_at or datetime.now(tz=timezone.utc).isoformat(),
        "size_bytes": 1024,
    }


@pytest.fixture()
def cat(tmp_path: Path):
    """Return a BackupCatalog writing to a tmp file."""
    from backup.catalog import BackupCatalog
    return BackupCatalog(path=tmp_path / "backup_catalog.json")


# ---------------------------------------------------------------------------
# add / get / all / remove
# ---------------------------------------------------------------------------

class TestCRUD:
    def test_add_persists_to_disk(self, tmp_path):
        """add() must write the file to disk (not just memory)."""
        from backup.catalog import BackupCatalog
        import json
        path = tmp_path / "cat.json"
        cat = BackupCatalog(path=path)
        entry = _make_entry("bnd-001")
        cat.add(entry)
        data = json.loads(path.read_text())
        assert "bnd-001" in data

    def test_add_then_get(self, cat):
        entry = _make_entry("bnd-002")
        cat.add(entry)
        result = cat.get("bnd-002")
        assert result is not None
        assert result["collection"] == "Products"

    def test_get_missing_returns_none(self, cat):
        assert cat.get("does-not-exist") is None

    def test_all_returns_copy(self, cat):
        cat.add(_make_entry("bnd-003"))
        copy = cat.all()
        copy["injected"] = {}
        # Original must not be mutated
        assert "injected" not in cat.all()

    def test_remove_drops_entry(self, cat, tmp_path):
        """remove() must drop the entry from memory and disk."""
        import json
        cat.add(_make_entry("bnd-004"))
        cat.remove("bnd-004")
        assert cat.get("bnd-004") is None
        # Disk state must reflect the removal
        data = json.loads((tmp_path / "backup_catalog.json").read_text())
        assert "bnd-004" not in data

    def test_remove_nonexistent_noop(self, cat):
        """remove() on an unknown id must not raise."""
        cat.remove("ghost")  # no error

    def test_reload_from_disk(self, tmp_path):
        """A new BackupCatalog instance should load from the existing file."""
        from backup.catalog import BackupCatalog
        path = tmp_path / "cat.json"
        cat1 = BackupCatalog(path=path)
        cat1.add(_make_entry("bnd-005"))
        # Second instance — loads from disk
        cat2 = BackupCatalog(path=path)
        assert cat2.get("bnd-005") is not None


# ---------------------------------------------------------------------------
# bundle_id validation (path-traversal guard)
# ---------------------------------------------------------------------------

class TestBundleIdValidation:
    def test_valid_id_accepted(self, cat):
        cat.add(_make_entry("valid-bundle-001"))
        assert cat.get("valid-bundle-001") is not None

    def test_slash_rejected(self, cat):
        with pytest.raises(ValueError, match="bundle_id"):
            cat.add(_make_entry("../../etc/passwd"))

    def test_dotdot_rejected(self, cat):
        with pytest.raises(ValueError, match="bundle_id"):
            cat.add(_make_entry(".."))

    def test_space_rejected(self, cat):
        with pytest.raises(ValueError, match="bundle_id"):
            cat.add(_make_entry("bad id"))

    def test_special_chars_rejected(self, cat):
        with pytest.raises(ValueError, match="bundle_id"):
            cat.add(_make_entry("bad<id>"))


# ---------------------------------------------------------------------------
# prune — retention/keep_n logic (D-07/BAK-04)
# ---------------------------------------------------------------------------

class TestPrune:
    def _dt(self, minutes_ago: int) -> str:
        return (datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()

    def test_prune_evicts_oldest(self, cat):
        """keep_n=7 over 10 same-collection entries must evict the 3 OLDEST."""
        # Add 10 entries: oldest at minutes_ago=9, newest at minutes_ago=0
        for i in range(10):
            cat.add(_make_entry(f"bnd-p{i:02d}", created_at=self._dt(9 - i)))

        evicted = cat.prune("Products", keep_n=7)

        assert len(evicted) == 3
        # Evicted must be the 3 oldest (bnd-p00, bnd-p01, bnd-p02)
        assert set(evicted) == {"bnd-p00", "bnd-p01", "bnd-p02"}

    def test_prune_keeps_newest_n(self, cat):
        """After prune, only the newest keep_n entries remain in the catalog."""
        for i in range(10):
            cat.add(_make_entry(f"bnd-kn{i:02d}", created_at=self._dt(9 - i)))

        cat.prune("Products", keep_n=7)

        remaining = [k for k in cat.all() if k.startswith("bnd-kn")]
        assert len(remaining) == 7
        # Newest 7: bnd-kn03 through bnd-kn09
        assert set(remaining) == {f"bnd-kn{i:02d}" for i in range(3, 10)}

    def test_prune_does_not_touch_other_collections(self, cat):
        """prune('Products') must leave entries for other collections intact."""
        for i in range(10):
            cat.add(_make_entry(f"bnd-prod{i:02d}", collection="Products", created_at=self._dt(9 - i)))
        # Add an entry for a different collection
        cat.add(_make_entry("bnd-other", collection="Employees"))

        cat.prune("Products", keep_n=7)

        # Employees entry must still be present
        assert cat.get("bnd-other") is not None

    def test_prune_under_keep_n_is_noop(self, cat):
        """prune when entry count ≤ keep_n must evict nothing."""
        for i in range(5):
            cat.add(_make_entry(f"bnd-few{i}"))
        evicted = cat.prune("Products", keep_n=7)
        assert evicted == []
        assert len([k for k in cat.all() if k.startswith("bnd-few")]) == 5

    def test_prune_returns_evicted_ids(self, cat):
        """prune must return exactly the evicted bundle_ids (caller uses them to delete S3 keys)."""
        for i in range(4):
            cat.add(_make_entry(f"bnd-ret{i}", created_at=self._dt(3 - i)))
        evicted = cat.prune("Products", keep_n=2)
        assert set(evicted) == {"bnd-ret0", "bnd-ret1"}

    def test_prune_evicted_removed_from_catalog(self, cat):
        """Entries returned by prune must no longer exist in the catalog."""
        for i in range(5):
            cat.add(_make_entry(f"bnd-rem{i}", created_at=self._dt(4 - i)))
        evicted = cat.prune("Products", keep_n=3)
        for bid in evicted:
            assert cat.get(bid) is None
