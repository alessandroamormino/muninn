"""Tests for backup/manager.py — run_backup and run_restore orchestration.

Uses a real BackupCatalog on tmp_path (per plan spec) with Mock vector_store
and Mock S3BackupClient. Snapshot file existence is simulated by writing a dummy
file under a tmp snapshots_root.

TDD RED: all tests fail (backup.manager does not exist yet).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backup.catalog import BackupCatalog


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

def _seed_catalog(catalog: BackupCatalog, collection: str, count: int, keep_n: int) -> None:
    """Pre-seed catalog with `count` bundles for `collection` (oldest = highest i)."""
    for i in range(count):
        bid = f"{collection}-old{i:02d}"
        dt = (datetime.now(tz=timezone.utc) - timedelta(hours=i + 1)).isoformat()
        catalog.add({
            "bundle_id": bid,
            "collection": collection,
            "snapshot_name": f"snap-old{i:02d}.snapshot",
            "s3_keys": {
                "snapshot": f"backups/{collection}/{bid}/snapshot.snapshot",
                "state": f"backups/{collection}/{bid}/state.tar.gz",
                "manifest": f"backups/{collection}/{bid}/manifest.json",
            },
            "created_at": dt,
            "size_bytes": 100,
            "state_contents": [],
        })


def _make_snapshot(tmp_path: Path, collection: str, name: str) -> Path:
    """Create a dummy snapshot file in a tmp snapshots_root and return its Path."""
    snap_dir = tmp_path / "snapshots" / collection
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / name
    snap_file.write_bytes(b"fake-qdrant-snapshot")
    return snap_file


# ---------------------------------------------------------------------------
# Task 1: run_backup
# ---------------------------------------------------------------------------

class TestBuildBundleId:
    def test_format_matches_pattern(self):
        from backup.manager import build_bundle_id
        result = build_bundle_id("Products")
        assert re.match(r"^Products-\d{8}T\d{6}Z$", result), (
            f"Expected Products-YYYYMMDDTHHMMSSZ, got {result!r}"
        )

    def test_no_dots_or_colons(self):
        """Timestamp must contain no dots or colons (catalog path-traversal guard)."""
        from backup.manager import build_bundle_id
        result = build_bundle_id("MyCollection")
        assert "." not in result and ":" not in result

    def test_matches_bundle_id_re(self):
        """bundle_id must pass BackupCatalog._BUNDLE_ID_RE = ^[A-Za-z0-9_-]+$."""
        from backup.manager import build_bundle_id
        result = build_bundle_id("SomeEntity")
        assert re.match(r"^[A-Za-z0-9_-]+$", result), (
            f"bundle_id {result!r} does not match ^[A-Za-z0-9_-]+$"
        )

    def test_starts_with_collection(self):
        from backup.manager import build_bundle_id
        assert build_bundle_id("Employees").startswith("Employees-")


class TestRunBackupSnapshotReuse:
    """run_backup calls snapshot_collection and uses the RETURNED name verbatim."""

    def test_snapshot_collection_called(self, tmp_path):
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-abc.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-abc.snapshot")

        run_backup(vs, s3, catalog, "Products", keep_n=7,
                   snapshots_root=str(tmp_path / "snapshots"),
                   state_roots=(str(tmp_path / "noexist"),))

        vs.snapshot_collection.assert_called_once_with("Products")

    def test_uses_returned_snapshot_name(self, tmp_path):
        """The snapshot name from vector_store is used in the S3 key, not constructed."""
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snapshot-2024-01-01-00-00-00.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snapshot-2024-01-01-00-00-00.snapshot")

        manifest = run_backup(vs, s3, catalog, "Products", keep_n=7,
                              snapshots_root=str(tmp_path / "snapshots"),
                              state_roots=(str(tmp_path / "noexist"),))

        assert manifest["snapshot_name"] == "snapshot-2024-01-01-00-00-00.snapshot"
        # S3 upload key must use the returned name, not anything constructed
        upload_keys = [c.args[1] for c in s3.upload.call_args_list]
        assert any("snapshot-2024-01-01-00-00-00.snapshot" in k for k in upload_keys), (
            f"Returned snapshot name not in any S3 key: {upload_keys}"
        )


class TestRunBackupS3Keys:
    """Exact S3 key assertions for snapshot, state tar, and manifest."""

    def test_three_upload_keys(self, tmp_path):
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-xyz.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-xyz.snapshot")

        manifest = run_backup(vs, s3, catalog, "Products", keep_n=7,
                              snapshots_root=str(tmp_path / "snapshots"),
                              state_roots=(str(tmp_path / "noexist"),))

        bid = manifest["bundle_id"]
        upload_keys = {c.args[1] for c in s3.upload.call_args_list}
        assert f"backups/Products/{bid}/snapshot.snapshot" in upload_keys
        assert f"backups/Products/{bid}/state.tar.gz" in upload_keys
        assert f"backups/Products/{bid}/manifest.json" in upload_keys

    def test_snapshot_file_path_uses_snapshots_root(self, tmp_path):
        """Snapshot file is read from {snapshots_root}/{collection}/{snapshot_name}."""
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-path.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-path.snapshot")

        run_backup(vs, s3, catalog, "Products", keep_n=7,
                   snapshots_root=str(tmp_path / "snapshots"),
                   state_roots=(str(tmp_path / "noexist"),))

        # First upload call should use the snapshot file path
        first_upload_local = s3.upload.call_args_list[0].args[0]
        expected_local = str(tmp_path / "snapshots" / "Products" / "snap-path.snapshot")
        assert first_upload_local == expected_local


class TestRunBackupManifest:
    """Manifest structure and catalog.add ordering."""

    def test_manifest_fields(self, tmp_path):
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-m.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-m.snapshot")

        manifest = run_backup(vs, s3, catalog, "Products", keep_n=7,
                              snapshots_root=str(tmp_path / "snapshots"),
                              state_roots=(str(tmp_path / "noexist"),))

        for field in ("bundle_id", "collection", "snapshot_name", "s3_keys",
                      "created_at", "size_bytes", "state_contents"):
            assert field in manifest, f"Missing manifest field: {field!r}"
        assert manifest["collection"] == "Products"
        assert manifest["snapshot_name"] == "snap-m.snapshot"
        assert "snapshot" in manifest["s3_keys"]
        assert "state" in manifest["s3_keys"]
        assert "manifest" in manifest["s3_keys"]

    def test_catalog_add_called_after_all_uploads(self, tmp_path):
        """catalog.add must be called ONLY after all three S3 uploads succeed."""
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-ord.snapshot"

        call_order: list[str] = []

        def track_upload(local_path: str, key: str) -> None:
            call_order.append(f"upload:{key.split('/')[-1]}")

        s3 = MagicMock()
        s3.upload.side_effect = track_upload

        real_catalog = BackupCatalog(path=tmp_path / "catalog.json")
        calls_at_add: list[list[str]] = []

        original_add = real_catalog.add
        def tracking_add(entry: dict) -> None:
            calls_at_add.append(list(call_order))
            original_add(entry)

        real_catalog.add = tracking_add  # type: ignore[method-assign]
        _make_snapshot(tmp_path, "Products", "snap-ord.snapshot")

        run_backup(vs, s3, real_catalog, "Products", keep_n=7,
                   snapshots_root=str(tmp_path / "snapshots"),
                   state_roots=(str(tmp_path / "noexist"),))

        assert len(calls_at_add) == 1, "catalog.add must be called exactly once"
        # At the time add was called, all 3 uploads must already be done
        upload_names = calls_at_add[0]
        assert "snapshot.snapshot" in " ".join(upload_names)
        assert "state.tar.gz" in " ".join(upload_names)
        assert "manifest.json" in " ".join(upload_names)

    def test_catalog_has_entry_after_run(self, tmp_path):
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-cat.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-cat.snapshot")

        manifest = run_backup(vs, s3, catalog, "Products", keep_n=7,
                              snapshots_root=str(tmp_path / "snapshots"),
                              state_roots=(str(tmp_path / "noexist"),))

        entry = catalog.get(manifest["bundle_id"])
        assert entry is not None
        assert entry["snapshot_name"] == "snap-cat.snapshot"


class TestRunBackupStateTar:
    """State tar is built from existing state_roots; missing roots skipped."""

    def test_existing_roots_in_state_contents(self, tmp_path):
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-st.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-st.snapshot")

        state_root = tmp_path / ".sync"
        state_root.mkdir()
        (state_root / "sync_state.json").write_text("{}")

        manifest = run_backup(vs, s3, catalog, "Products", keep_n=7,
                              snapshots_root=str(tmp_path / "snapshots"),
                              state_roots=(str(state_root), str(tmp_path / "missing_config")))

        assert str(state_root) in manifest["state_contents"]
        assert str(tmp_path / "missing_config") not in manifest["state_contents"]

    def test_missing_root_skipped_gracefully(self, tmp_path):
        """run_backup does not raise when all state_roots are missing."""
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-skip.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-skip.snapshot")

        # No state_roots exist → should succeed with empty state_contents
        manifest = run_backup(vs, s3, catalog, "Products", keep_n=7,
                              snapshots_root=str(tmp_path / "snapshots"),
                              state_roots=(str(tmp_path / "noexist1"), str(tmp_path / "noexist2")))

        assert manifest["state_contents"] == []


class TestRunBackupPruneRetention:
    """Prune: evicted bundle S3 keys deleted, then catalog.remove called."""

    def test_prune_deletes_s3_keys_of_evicted_bundles(self, tmp_path):
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-new.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")

        # Seed 3 old bundles (i=0 is 1h ago, i=1 is 2h ago, i=2 is 3h ago)
        _seed_catalog(catalog, "Products", count=3, keep_n=2)
        _make_snapshot(tmp_path, "Products", "snap-new.snapshot")

        run_backup(vs, s3, catalog, "Products", keep_n=2,
                   snapshots_root=str(tmp_path / "snapshots"),
                   state_roots=(str(tmp_path / "noexist"),))

        # 3 old + 1 new = 4 total; keep_n=2 → evict 2 oldest (old01 and old02)
        deleted_keys = {c.args[0] for c in s3.delete.call_args_list}
        # old01 (2h ago) and old02 (3h ago) evicted; old00 (1h ago) + new kept
        assert "backups/Products/Products-old01/snapshot.snapshot" in deleted_keys
        assert "backups/Products/Products-old01/state.tar.gz" in deleted_keys
        assert "backups/Products/Products-old01/manifest.json" in deleted_keys
        assert "backups/Products/Products-old02/snapshot.snapshot" in deleted_keys

    def test_no_prune_when_under_keep_n(self, tmp_path):
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-nop.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-nop.snapshot")

        run_backup(vs, s3, catalog, "Products", keep_n=7,
                   snapshots_root=str(tmp_path / "snapshots"),
                   state_roots=(str(tmp_path / "noexist"),))

        s3.delete.assert_not_called()


class TestRunBackupFailureSafety:
    """Failure mid-flow must not corrupt the catalog or delete the local snapshot."""

    def test_upload_failure_does_not_add_to_catalog(self, tmp_path):
        """If S3 upload raises, catalog.add is NOT called."""
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-fail.snapshot"
        s3 = MagicMock()
        s3.upload.side_effect = RuntimeError("S3 error")
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-fail.snapshot")

        with pytest.raises(RuntimeError, match="S3 error"):
            run_backup(vs, s3, catalog, "Products", keep_n=7,
                       snapshots_root=str(tmp_path / "snapshots"),
                       state_roots=(str(tmp_path / "noexist"),))

        assert catalog.all() == {}

    def test_upload_failure_does_not_delete_local_snapshot(self, tmp_path):
        """If upload fails, delete_collection_snapshot is NOT called (local copy preserved)."""
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-preserve.snapshot"
        s3 = MagicMock()
        s3.upload.side_effect = RuntimeError("network error")
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-preserve.snapshot")

        with pytest.raises(RuntimeError):
            run_backup(vs, s3, catalog, "Products", keep_n=7,
                       snapshots_root=str(tmp_path / "snapshots"),
                       state_roots=(str(tmp_path / "noexist"),))

        vs.delete_collection_snapshot.assert_not_called()


class TestRunBackupLocalSnapshotCleanup:
    """After success, local snapshot is freed via delete_collection_snapshot."""

    def test_delete_collection_snapshot_called_after_success(self, tmp_path):
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-cleanup.snapshot"
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-cleanup.snapshot")

        run_backup(vs, s3, catalog, "Products", keep_n=7,
                   snapshots_root=str(tmp_path / "snapshots"),
                   state_roots=(str(tmp_path / "noexist"),))

        vs.delete_collection_snapshot.assert_called_once_with("Products", "snap-cleanup.snapshot")

    def test_delete_snapshot_failure_does_not_raise(self, tmp_path):
        """delete_collection_snapshot failure must not fail an otherwise-successful backup."""
        from backup.manager import run_backup
        vs = MagicMock()
        vs.snapshot_collection.return_value = "snap-clfail.snapshot"
        vs.delete_collection_snapshot.side_effect = RuntimeError("delete failed")
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        _make_snapshot(tmp_path, "Products", "snap-clfail.snapshot")

        # Must not raise — cleanup failure is non-fatal
        manifest = run_backup(vs, s3, catalog, "Products", keep_n=7,
                              snapshots_root=str(tmp_path / "snapshots"),
                              state_roots=(str(tmp_path / "noexist"),))

        assert manifest["bundle_id"] is not None


# ---------------------------------------------------------------------------
# Task 2: run_restore (RED — import from backup.manager will fail until Task 2 GREEN)
# ---------------------------------------------------------------------------

class TestRunRestoreUnknownBundle:
    def test_missing_bundle_raises_value_error(self, tmp_path):
        from backup.manager import run_restore
        vs = MagicMock()
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")

        with pytest.raises(ValueError, match="not found"):
            run_restore(vs, s3, catalog, "Products", "nonexistent-bundle")

    def test_wrong_collection_raises_value_error(self, tmp_path):
        """Bundle exists but for a different collection → ValueError."""
        from backup.manager import run_restore
        vs = MagicMock()
        s3 = MagicMock()
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        catalog.add({
            "bundle_id": "Products-20240101T000000Z",
            "collection": "OtherCollection",
            "snapshot_name": "snap.snapshot",
            "s3_keys": {
                "snapshot": "backups/OtherCollection/Products-20240101T000000Z/snapshot.snapshot",
                "state": "backups/OtherCollection/Products-20240101T000000Z/state.tar.gz",
                "manifest": "backups/OtherCollection/Products-20240101T000000Z/manifest.json",
            },
            "created_at": "2024-01-01T00:00:00+00:00",
            "size_bytes": 100,
            "state_contents": [],
        })

        with pytest.raises(ValueError):
            run_restore(vs, s3, catalog, "Products", "Products-20240101T000000Z")


class TestRunRestoreDownload:
    def _seed_restore_entry(self, catalog: BackupCatalog, collection: str,
                            bundle_id: str, snapshot_name: str) -> None:
        catalog.add({
            "bundle_id": bundle_id,
            "collection": collection,
            "snapshot_name": snapshot_name,
            "s3_keys": {
                "snapshot": f"backups/{collection}/{bundle_id}/snapshot.snapshot",
                "state": f"backups/{collection}/{bundle_id}/state.tar.gz",
                "manifest": f"backups/{collection}/{bundle_id}/manifest.json",
            },
            "created_at": "2024-01-01T00:00:00+00:00",
            "size_bytes": 100,
            "state_contents": [],
        })

    def test_snapshot_downloaded_to_volume_path(self, tmp_path):
        from backup.manager import run_restore
        vs = MagicMock()
        s3 = MagicMock()

        def fake_download(key: str, local_path: str) -> None:
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_bytes(b"snap")

        s3.download.side_effect = fake_download
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        bid = "Products-20240101T000000Z"
        snap = "snap-restore.snapshot"
        self._seed_restore_entry(catalog, "Products", bid, snap)

        run_restore(vs, s3, catalog, "Products", bid,
                    snapshots_root=str(tmp_path / "snapshots"))

        expected_dest = str(tmp_path / "snapshots" / "Products" / snap)
        s3.download.assert_called_once_with(
            f"backups/Products/{bid}/snapshot.snapshot",
            expected_dest,
        )

    def test_collection_dir_created_if_missing(self, tmp_path):
        """dest.parent (collection dir) is created with mkdir -p before download."""
        from backup.manager import run_restore
        vs = MagicMock()
        created_dirs: list[str] = []

        def fake_download(key: str, local_path: str) -> None:
            # Simulate download — record whether parent exists
            created_dirs.append(str(Path(local_path).parent))
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_bytes(b"snap")

        s3 = MagicMock()
        s3.download.side_effect = fake_download
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        bid = "Products-20240101T000001Z"
        snap = "snap-mkdir.snapshot"
        self._seed_restore_entry(catalog, "Products", bid, snap)

        # snapshots_root does NOT contain the collection subdir yet
        run_restore(vs, s3, catalog, "Products", bid,
                    snapshots_root=str(tmp_path / "snapshots"))

        expected_dir = str(tmp_path / "snapshots" / "Products")
        assert expected_dir in created_dirs


class TestRunRestoreCallsRestoreCollection:
    def _seed(self, catalog: BackupCatalog, collection: str,
              bundle_id: str, snapshot_name: str) -> None:
        catalog.add({
            "bundle_id": bundle_id,
            "collection": collection,
            "snapshot_name": snapshot_name,
            "s3_keys": {
                "snapshot": f"backups/{collection}/{bundle_id}/snapshot.snapshot",
                "state": f"backups/{collection}/{bundle_id}/state.tar.gz",
                "manifest": f"backups/{collection}/{bundle_id}/manifest.json",
            },
            "created_at": "2024-01-01T00:00:00+00:00",
            "size_bytes": 100,
            "state_contents": [],
        })

    def test_restore_collection_called_with_snapshot_name(self, tmp_path):
        from backup.manager import run_restore
        vs = MagicMock()
        s3 = MagicMock()

        def fake_download(key: str, local_path: str) -> None:
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_bytes(b"snap")

        s3.download.side_effect = fake_download
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        bid = "Products-20240101T000002Z"
        snap = "snap-rc.snapshot"
        self._seed(catalog, "Products", bid, snap)

        run_restore(vs, s3, catalog, "Products", bid,
                    snapshots_root=str(tmp_path / "snapshots"))

        vs.restore_collection.assert_called_once_with("Products", snap)

    def test_no_state_tar_downloaded(self, tmp_path):
        """run_restore downloads ONLY the snapshot key, not state.tar.gz (runbook's job)."""
        from backup.manager import run_restore
        vs = MagicMock()
        downloaded_keys: list[str] = []

        def fake_download(key: str, local_path: str) -> None:
            downloaded_keys.append(key)
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_bytes(b"snap")

        s3 = MagicMock()
        s3.download.side_effect = fake_download
        catalog = BackupCatalog(path=tmp_path / "catalog.json")
        bid = "Products-20240101T000003Z"
        snap = "snap-nostate.snapshot"
        self._seed(catalog, "Products", bid, snap)

        run_restore(vs, s3, catalog, "Products", bid,
                    snapshots_root=str(tmp_path / "snapshots"))

        # Only snapshot key downloaded — no state.tar.gz
        assert len(downloaded_keys) == 1
        assert downloaded_keys[0].endswith("snapshot.snapshot")
        assert not any("state.tar.gz" in k for k in downloaded_keys)
