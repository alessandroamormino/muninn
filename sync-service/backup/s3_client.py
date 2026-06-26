"""S3BackupClient — thin boto3 wrapper for off-host backup transfer.

Supports any S3-compatible endpoint (AWS S3, MinIO, Backblaze) via endpoint_url.
Credentials are resolved from ${VAR} tokens at construction time; never logged.

D-01/D-02: single code path for all providers via endpoint_url.
D-03/D-11: ${VAR} resolution via the existing resolver imported from json_adapter.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import S3BackupConfig
from sources.json_adapter import _resolve_env_vars

if TYPE_CHECKING:
    pass  # boto3 imported lazily below (mirror scheduler.py pattern)

logger = logging.getLogger(__name__)


class S3BackupClient:
    """Thin S3 transfer primitive.

    Constructed from an S3BackupConfig whose fields may carry ${VAR} tokens;
    every credential is resolved from os.environ at construction time.

    Bucket is created on first upload if missing (_ensure_bucket), so the
    initial local MinIO backup works with no manual `mc mb` step.
    """

    def __init__(self, cfg: S3BackupConfig) -> None:
        # Resolve every ${VAR} field — never log the resolved values (T-28-01-01)
        access_key = _resolve_env_vars(cfg.access_key)
        secret_key = _resolve_env_vars(cfg.secret_key)
        self._bucket = _resolve_env_vars(cfg.bucket)

        endpoint_url_raw = _resolve_env_vars(cfg.endpoint_url) if cfg.endpoint_url else ""
        endpoint_url = endpoint_url_raw or None

        region_raw = _resolve_env_vars(cfg.region) if cfg.region else ""
        region = region_raw or None

        # Lazy import so unit tests can monkeypatch sys.modules["boto3"] before importing
        import boto3  # noqa: PLC0415

        self._client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint_url,
            region_name=region,
        )
        # Instance flag — head_bucket called only once per client instance (idempotent guard)
        self._bucket_ensured = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload(self, local_path: str, key: str) -> None:
        """Upload a local file to S3 at the given key.

        Creates the bucket on first call if it does not exist (create-if-missing),
        so no manual bucket bootstrap is needed for a fresh MinIO instance.
        """
        self._ensure_bucket()
        logger.debug("S3 upload: %s → s3://%s/%s", local_path, self._bucket, key)
        self._client.upload_file(local_path, self._bucket, key)

    def download(self, key: str, local_path: str) -> None:
        """Download an S3 object to a local file."""
        logger.debug("S3 download: s3://%s/%s → %s", self._bucket, key, local_path)
        self._client.download_file(self._bucket, key, local_path)

    def list_keys(self, prefix: str) -> list[str]:
        """Return a flat list of S3 object keys under prefix (paginated)."""
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def delete(self, key: str) -> None:
        """Delete a single S3 object."""
        logger.debug("S3 delete: s3://%s/%s", self._bucket, key)
        self._client.delete_object(Bucket=self._bucket, Key=key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_bucket(self) -> None:
        """Create the S3 bucket if it does not exist.

        Uses an instance flag so only the very first upload incurs the
        head_bucket check — subsequent uploads skip it entirely.
        """
        if self._bucket_ensured:
            return
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception as exc:  # noqa: BLE001
            # Detect 404 / NoSuchBucket from any S3-compatible provider
            code = ""
            if hasattr(exc, "response"):
                code = (
                    exc.response.get("Error", {}).get("Code", "")  # type: ignore[attr-defined]
                )
            if code in ("404", "NoSuchBucket"):
                logger.info("Bucket %r not found — creating.", self._bucket)
                self._client.create_bucket(Bucket=self._bucket)
            else:
                raise
        self._bucket_ensured = True
