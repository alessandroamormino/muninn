"""backup/manager.py — DR bundle orchestration.

run_backup (BAK-01): snapshot the collection via Phase-26 vector_store.snapshot_collection →
read snapshot file from qdrant_snapshots volume → upload to S3 → tar state set (.sync/ +
configuration/) → upload tar → write manifest + catalog entry → prune to keep_n → free local
snapshot.

run_restore (BAK-02): download bundle's snapshot from S3 → place on qdrant_snapshots volume →
call vector_store.restore_collection (create-or-overwrite, no re-embedding).

State-set extraction (SQLite + config) is handled ONLY by run_backup (off-host archive). The
live API restore (run_restore) restores ONLY the Qdrant collection. Full state-set restore is
the offline DR runbook procedure (28-04) — overwriting open SQLite handles in a running
orchestrator is unsafe (D-09).

D-02: reuses Phase-26 snapshot/restore methods — never re-implements Qdrant snapshot logic.
D-04: bundle = snapshot + state tar + manifest, all atomically uploaded before catalog.add.
"""
from __future__ import annotations

import json
import logging
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from backup.catalog import BackupCatalog
from backup.s3_client import S3BackupClient

logger = logging.getLogger(__name__)

# ponytail: fixed key names per bundle — one bundle, three S3 objects
_SNAPSHOT_KEY = "snapshot.snapshot"
_STATE_KEY = "state.tar.gz"
_MANIFEST_KEY = "manifest.json"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def build_bundle_id(collection: str) -> str:
    """Return a catalog-safe bundle identifier: {collection}-{UTC compact timestamp}.

    Format: YYYYMMDDTHHMMSSZ — no dots or colons so it passes BackupCatalog's
    _BUNDLE_ID_RE = ^[A-Za-z0-9_-]+$ path-traversal guard (T-28-02-01).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{collection}-{ts}"


# ---------------------------------------------------------------------------
# run_backup (BAK-01)
# ---------------------------------------------------------------------------

def run_backup(
    vector_store,
    s3: S3BackupClient,
    catalog: BackupCatalog,
    collection: str,
    keep_n: int,
    snapshots_root: str = "/qdrant/snapshots",
    state_roots: Sequence[str] = ("/app/.sync", "/app/configuration"),
) -> dict:
    """Create an off-host DR bundle for collection and apply retention.

    Orchestration (D-04/D-05):
    1. Create Qdrant snapshot via vector_store.snapshot_collection (Phase-26 reuse).
    2. Upload snapshot file from qdrant_snapshots volume to S3.
    3. Tar existing state_roots (stdlib tarfile, ponytail rung-3) and upload.
    4. Compose manifest dict and upload to S3; only THEN call catalog.add
       (ensures no partial state corrupts the catalog on upload failure).
    5. Prune: evict bundles beyond keep_n, delete their S3 keys, remove from catalog.
    6. Free the local snapshot via delete_collection_snapshot (guard: cleanup failure
       must not fail an otherwise-successful backup; only runs after catalog.add).

    Returns the manifest dict.

    The caller (28-03 API endpoint) holds sync_lock during this call so no
    concurrent SQLite writer is active during the state-set tar (D-04 note).
    Credentials never logged (T-28-02-01).
    """
    bundle_id = build_bundle_id(collection)
    prefix = f"backups/{collection}/{bundle_id}"
    snapshot_key = f"{prefix}/{_SNAPSHOT_KEY}"
    state_key = f"{prefix}/{_STATE_KEY}"
    manifest_key = f"{prefix}/{_MANIFEST_KEY}"

    # Step 1: create snapshot — use the RETURNED name verbatim (Pitfall 1)
    snapshot_name = vector_store.snapshot_collection(collection)
    snapshot_path = Path(snapshots_root) / collection / snapshot_name
    logger.info("run_backup: collection=%r bundle=%r snapshot=%r", collection, bundle_id, snapshot_name)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        # Step 2: upload snapshot from volume
        s3.upload(str(snapshot_path), snapshot_key)

        # Step 3: build and upload state tar (stdlib tarfile, ponytail rung-3)
        tar_path = tmp / "state.tar.gz"
        state_contents: list[str] = []
        with tarfile.open(str(tar_path), "w:gz") as tf:
            for root_str in state_roots:
                root = Path(root_str)
                if root.exists():
                    tf.add(str(root), arcname=root.name)
                    state_contents.append(root_str)
        s3.upload(str(tar_path), state_key)

        # Step 4: compose manifest and upload; catalog.add only after all three uploads
        size_bytes = snapshot_path.stat().st_size if snapshot_path.exists() else 0
        manifest: dict = {
            "bundle_id": bundle_id,
            "collection": collection,
            "snapshot_name": snapshot_name,
            "s3_keys": {
                "snapshot": snapshot_key,
                "state": state_key,
                "manifest": manifest_key,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size_bytes": size_bytes,
            "state_contents": state_contents,
        }
        manifest_path = tmp / "manifest.json"
        with manifest_path.open("w") as fh:
            json.dump(manifest, fh)
        s3.upload(str(manifest_path), manifest_key)

        # All three uploads succeeded — safe to register in catalog
        catalog.add(manifest)
    # tempdir cleaned up here (TemporaryDirectory.__exit__ = finally)

    # Step 5: prune — snapshot catalog before prune removes entries (need s3_keys for deletion)
    pre_prune = dict(catalog.all())
    evicted_ids = catalog.prune(collection, keep_n)
    for evicted_id in evicted_ids:
        entry = pre_prune.get(evicted_id, {})
        for s3_key in entry.get("s3_keys", {}).values():
            try:
                s3.delete(s3_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("S3 delete failed for %r: %s", s3_key, exc)
        catalog.remove(evicted_id)  # no-op (prune already removed), explicit per plan
    if evicted_ids:
        logger.info("run_backup: pruned %d old bundle(s) for %r", len(evicted_ids), collection)

    # Step 6: free local snapshot — guard so cleanup failure cannot fail the backup
    try:
        vector_store.delete_collection_snapshot(collection, snapshot_name)
        logger.info("run_backup: freed local snapshot %r", snapshot_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_backup: could not delete local snapshot %r (non-fatal): %s",
                       snapshot_name, exc)

    return manifest


# ---------------------------------------------------------------------------
# run_restore (BAK-02)
# ---------------------------------------------------------------------------

def run_restore(
    vector_store,
    s3: S3BackupClient,
    catalog: BackupCatalog,
    collection: str,
    bundle_id: str,
    snapshots_root: str = "/qdrant/snapshots",
) -> None:
    """Restore a collection from an off-host bundle.

    Downloads the bundle's Qdrant snapshot from S3, places it on the
    qdrant_snapshots volume, then calls vector_store.restore_collection
    (create-or-overwrite, no re-embedding — Phase-26 reuse).

    State-set (SQLite + config) extraction is NOT performed here — that is the
    offline DR runbook procedure (28-04). Overwriting open SQLite handles in a
    running orchestrator is unsafe (D-09).

    Raises ValueError if bundle_id is not found in the catalog or belongs to a
    different collection (T-28-02-01: validated entries only).
    """
    entry = catalog.get(bundle_id)
    if entry is None or entry.get("collection") != collection:
        raise ValueError(
            f"Bundle {bundle_id!r} not found in catalog for collection {collection!r}"
        )

    snapshot_name = entry["snapshot_name"]
    snapshot_s3_key = entry["s3_keys"]["snapshot"]

    # Place snapshot on the qdrant_snapshots volume where Qdrant can read it
    dest = Path(snapshots_root) / collection / snapshot_name
    dest.parent.mkdir(parents=True, exist_ok=True)

    logger.info("run_restore: downloading %r → %s", snapshot_s3_key, dest)
    s3.download(snapshot_s3_key, str(dest))

    # Call Phase-26 restore (create-or-overwrite, no re-embedding)
    logger.info("run_restore: calling restore_collection for %r from %r", collection, snapshot_name)
    vector_store.restore_collection(collection, snapshot_name)
    logger.info("run_restore: collection %r restored from bundle %r", collection, bundle_id)
