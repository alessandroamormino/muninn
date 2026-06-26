"""Tests for backup/s3_client.py — S3BackupClient.

All boto3 calls are mocked via monkeypatch so these tests run without
a real S3 endpoint and without boto3 installed on the test host.
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock, call, patch

import pytest

from config.settings import S3BackupConfig


# ---------------------------------------------------------------------------
# Helpers — inject a fake boto3 module so we can import s3_client
# without boto3 on the test host
# ---------------------------------------------------------------------------

def _make_fake_boto3(mock_client_instance: MagicMock) -> ModuleType:
    """Return a minimal stub for the boto3 module."""
    fake = ModuleType("boto3")
    fake.client = MagicMock(return_value=mock_client_instance)
    return fake


def _import_s3_client(monkeypatch, mock_client_instance: MagicMock):
    """Import backup.s3_client with boto3 replaced by a stub."""
    fake_boto3 = _make_fake_boto3(mock_client_instance)
    # Patch boto3 in sys.modules so the lazy import inside s3_client picks it up
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    # Force re-import (module may be cached from a prior test)
    if "backup.s3_client" in sys.modules:
        del sys.modules["backup.s3_client"]
    import backup.s3_client as mod
    return mod, fake_boto3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_s3():
    """Return a MagicMock that will stand in for the boto3 S3 client."""
    m = MagicMock()
    # Default: head_bucket succeeds (bucket exists)
    m.head_bucket.return_value = {}
    return m


@pytest.fixture()
def cfg():
    return S3BackupConfig(
        access_key="${S3_ACCESS}",
        secret_key="${S3_SECRET}",
        bucket="${S3_BUCKET}",
        endpoint_url="${S3_ENDPOINT}",
        region="${S3_REGION}",
    )


# ---------------------------------------------------------------------------
# Test: ${VAR} resolution
# ---------------------------------------------------------------------------

class TestCredentialResolution:
    def test_vars_resolved_from_environ(self, monkeypatch, mock_s3, cfg):
        """Constructor must resolve every ${VAR} field from os.environ."""
        monkeypatch.setenv("S3_ACCESS", "mykey")
        monkeypatch.setenv("S3_SECRET", "mysecret")
        monkeypatch.setenv("S3_BUCKET", "mybucket")
        monkeypatch.setenv("S3_ENDPOINT", "http://minio:9000")
        monkeypatch.setenv("S3_REGION", "us-east-1")

        mod, fake_boto3 = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)

        fake_boto3.client.assert_called_once_with(
            "s3",
            aws_access_key_id="mykey",
            aws_secret_access_key="mysecret",
            endpoint_url="http://minio:9000",
            region_name="us-east-1",
        )
        assert client._bucket == "mybucket"

    def test_empty_endpoint_url_becomes_none(self, monkeypatch, mock_s3, cfg):
        """Unset ${VAR} for endpoint_url must pass endpoint_url=None to boto3 (AWS default)."""
        monkeypatch.setenv("S3_ACCESS", "key")
        monkeypatch.setenv("S3_SECRET", "sec")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, fake_boto3 = _import_s3_client(monkeypatch, mock_s3)
        mod.S3BackupClient(cfg)

        _, kwargs = fake_boto3.client.call_args
        assert kwargs["endpoint_url"] is None
        assert kwargs["region_name"] is None

    def test_no_secrets_in_logs(self, monkeypatch, mock_s3, cfg, caplog):
        """Resolved credential values must never appear in log output."""
        monkeypatch.setenv("S3_ACCESS", "supersecretkey")
        monkeypatch.setenv("S3_SECRET", "supersecretval")
        monkeypatch.setenv("S3_BUCKET", "mybucket")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        import logging
        with caplog.at_level(logging.DEBUG):
            mod, _ = _import_s3_client(monkeypatch, mock_s3)
            client = mod.S3BackupClient(cfg)
            client.upload("/tmp/file.snap", "key/file.snap")

        for record in caplog.records:
            assert "supersecretkey" not in record.message
            assert "supersecretval" not in record.message


# ---------------------------------------------------------------------------
# Test: upload + create-bucket-if-missing
# ---------------------------------------------------------------------------

class TestUpload:
    def test_upload_calls_upload_file(self, monkeypatch, mock_s3, cfg):
        """upload() must call boto3 upload_file with the correct args."""
        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        client.upload("/local/snap.file", "prefix/snap.file")

        mock_s3.upload_file.assert_called_once_with("/local/snap.file", "bkt", "prefix/snap.file")

    def test_upload_calls_ensure_bucket_first(self, monkeypatch, mock_s3, cfg):
        """upload() must call head_bucket before upload_file."""
        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        call_order = []
        mock_s3.head_bucket.side_effect = lambda **kw: call_order.append("head")
        mock_s3.upload_file.side_effect = lambda *a, **kw: call_order.append("upload")

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        client.upload("/local/snap.file", "prefix/snap.file")

        assert call_order == ["head", "upload"]

    def test_bucket_created_when_not_exists(self, monkeypatch, cfg):
        """upload() must call create_bucket when head_bucket raises a 404 ClientError."""
        import botocore.exceptions  # type: ignore[import]

        mock_s3 = MagicMock()
        error_response = {"Error": {"Code": "404", "Message": "NoSuchBucket"}}
        mock_s3.head_bucket.side_effect = botocore.exceptions.ClientError(
            error_response, "HeadBucket"
        )

        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        client.upload("/local/snap.file", "prefix/snap.file")

        mock_s3.create_bucket.assert_called_once_with(Bucket="bkt")
        mock_s3.upload_file.assert_called_once()

    def test_bucket_not_created_when_exists(self, monkeypatch, mock_s3, cfg):
        """upload() must NOT call create_bucket when head_bucket succeeds."""
        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        client.upload("/local/snap.file", "prefix/snap.file")

        mock_s3.create_bucket.assert_not_called()

    def test_bucket_check_cached_after_first_upload(self, monkeypatch, mock_s3, cfg):
        """head_bucket must be called only ONCE even after multiple uploads."""
        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        client.upload("/a", "a")
        client.upload("/b", "b")

        assert mock_s3.head_bucket.call_count == 1


# ---------------------------------------------------------------------------
# Test: download
# ---------------------------------------------------------------------------

class TestDownload:
    def test_download_calls_download_file(self, monkeypatch, mock_s3, cfg):
        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        client.download("prefix/snap.file", "/local/snap.file")

        mock_s3.download_file.assert_called_once_with("bkt", "prefix/snap.file", "/local/snap.file")


# ---------------------------------------------------------------------------
# Test: list_keys
# ---------------------------------------------------------------------------

class TestListKeys:
    def test_list_keys_returns_flat_key_list(self, monkeypatch, mock_s3, cfg):
        """list_keys must return a flat list of Key strings from paginated results."""
        page1 = {"Contents": [{"Key": "a/b"}, {"Key": "a/c"}]}
        page2 = {"Contents": [{"Key": "a/d"}]}
        # Simulate paginator
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [page1, page2]
        mock_s3.get_paginator.return_value = mock_paginator

        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        keys = client.list_keys("a/")

        assert keys == ["a/b", "a/c", "a/d"]
        mock_s3.get_paginator.assert_called_once_with("list_objects_v2")
        mock_paginator.paginate.assert_called_once_with(Bucket="bkt", Prefix="a/")

    def test_list_keys_empty_prefix(self, monkeypatch, mock_s3, cfg):
        """list_keys with empty prefix returns all keys."""
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"Contents": [{"Key": "x"}]}]
        mock_s3.get_paginator.return_value = mock_paginator

        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        keys = client.list_keys("")

        assert keys == ["x"]

    def test_list_keys_empty_result(self, monkeypatch, mock_s3, cfg):
        """list_keys returns [] when no objects match prefix."""
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{}]  # no 'Contents' key
        mock_s3.get_paginator.return_value = mock_paginator

        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        assert client.list_keys("missing/") == []


# ---------------------------------------------------------------------------
# Test: delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_calls_delete_object(self, monkeypatch, mock_s3, cfg):
        monkeypatch.setenv("S3_ACCESS", "k")
        monkeypatch.setenv("S3_SECRET", "s")
        monkeypatch.setenv("S3_BUCKET", "bkt")
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        mod, _ = _import_s3_client(monkeypatch, mock_s3)
        client = mod.S3BackupClient(cfg)
        client.delete("prefix/snap.file")

        mock_s3.delete_object.assert_called_once_with(Bucket="bkt", Key="prefix/snap.file")
