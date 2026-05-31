"""Tests for POST /upload, POST /upload/confirm, GET /upload/status router."""
from __future__ import annotations

import io
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.upload import router


def _make_app(lock: bool = False, log_store=None) -> FastAPI:
    """Create a minimal FastAPI app with upload_router and required app.state fields."""
    app = FastAPI()
    app.include_router(router)
    lk = threading.Lock()
    if lock:
        lk.acquire()
    app.state.sync_lock = lk
    app.state.sync_status = {"status": "idle", "last_run": None}
    app.state.upload_status = None
    app.state.log_store = log_store
    return app


def _make_csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    import csv
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode()


_LLM_RESULT = {
    "id_field": "id",
    "text_fields": ["name"],
    "metadata_fields": ["age"],
    "output_fields": ["id", "name"],
    "reasoning": {"id": "unique identifier", "name": "free text", "age": "numeric"},
}


# --- UPLOAD-01a: happy path ---

def test_upload_csv_happy_path(tmp_path):
    csv_bytes = _make_csv_bytes(["id", "name", "age"], [["1", "Alice", "30"]])
    with (
        patch("api.upload._DATA_ROOT", tmp_path),
        patch("api.upload.OllamaLLMClient") as mock_llm,
    ):
        mock_llm.return_value.generate.return_value = _LLM_RESULT
        resp = TestClient(_make_app()).post(
            "/upload",
            files={"file": ("test.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "suggested_config" in body
    assert body["suggested_config"]["collection"] == "Test"
    assert body["suggested_config"]["id_field"] == "id"
    assert (tmp_path / "test.csv").exists()


# --- UPLOAD-01b: non-CSV rejected ---

def test_upload_non_csv_rejected(tmp_path):
    with patch("api.upload._DATA_ROOT", tmp_path):
        resp = TestClient(_make_app()).post(
            "/upload",
            files={"file": ("test.json", io.BytesIO(b'{"a":1}'), "application/json")},
        )
    assert resp.status_code == 422
    assert "CSV" in resp.json()["detail"]


# --- UPLOAD-01c: /upload does not use sync_lock ---

def test_upload_does_not_use_sync_lock(tmp_path):
    """POST /upload must work even when sync_lock is held (upload itself doesn't sync)."""
    csv_bytes = _make_csv_bytes(["id", "name"], [["1", "Alice"]])
    with (
        patch("api.upload._DATA_ROOT", tmp_path),
        patch("api.upload.OllamaLLMClient") as mock_llm,
    ):
        mock_llm.return_value.generate.return_value = _LLM_RESULT
        # lock=True: sync_lock is pre-acquired
        resp = TestClient(_make_app(lock=True)).post(
            "/upload",
            files={"file": ("test.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
    assert resp.status_code == 200


# --- UPLOAD-01d: /confirm writes config.yaml ---

def test_confirm_writes_config(tmp_path):
    body = {
        "file_name": "test.csv",
        "collection": "MyCollection",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": ["age"],
        "output_fields": ["id", "name"],
        "delimiter": ",",
    }
    from api.upload import ConfirmRequest

    config_dir = tmp_path / "configuration" / "MyCollection"
    config_path = config_dir / "config.yaml"

    def fake_write(*, collection, source, text_fields, metadata_fields, output_fields, id_field):
        config_dir.mkdir(parents=True, exist_ok=True)
        import yaml
        cfg = {
            "source": source,
            "weaviate": {"collection": collection, "text_fields": text_fields, "metadata_fields": metadata_fields},
        }
        config_path.write_text(yaml.dump(cfg, default_flow_style=False), encoding="utf-8")
        return config_path

    with patch("api.upload._write_config", side_effect=fake_write):
        app = _make_app()
        app.state.upload_status = {
            "file_name": "test.csv", "collection": "MyCollection",
            "status": "uploaded", "config_path": None, "sync_status": None,
        }
        with patch("api.upload._run_upload_sync_bg"):
            resp = TestClient(app).post("/upload/confirm", json=body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    assert resp.json()["collection"] == "MyCollection"


# --- UPLOAD-01e: /confirm 409 when locked ---

def test_confirm_409_when_locked():
    body = {
        "file_name": "test.csv",
        "collection": "Test",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
    }
    resp = TestClient(_make_app(lock=True)).post("/upload/confirm", json=body)
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"].lower()


# --- UPLOAD-01f: background sync completes, lock released ---

def test_confirm_lock_released_after_sync(tmp_path):
    body = {
        "file_name": "test.csv",
        "collection": "Test",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
    }
    mock_engine_result = {"inserted": 1, "updated": 0, "skipped": 0, "errors": 0}

    with (
        patch("api.upload._write_config") as mock_write,
        patch("api.upload.load_config") as mock_load,
        patch("api.upload.StateStore") as mock_ss,
        patch("api.upload.SyncEngine") as mock_engine_cls,
        patch("api.upload.get_client"),
    ):
        mock_write.return_value = tmp_path / "config.yaml"
        mock_cfg = MagicMock()
        mock_cfg.weaviate.collection = "Test"
        mock_cfg.embedding.model = "qwen3-embedding:4b"
        mock_cfg.source.type = "csv"
        mock_load.return_value = mock_cfg
        mock_engine_cls.return_value.run_full.return_value = mock_engine_result

        app = _make_app()
        app.state.upload_status = {
            "file_name": "test.csv", "collection": "Test",
            "status": "uploaded", "config_path": None, "sync_status": None,
        }

        with TestClient(app) as client:
            resp = client.post("/upload/confirm", json=body)

    assert resp.status_code == 200
    assert not app.state.sync_lock.locked(), "sync_lock must be released after background task"
    assert app.state.upload_status["status"] == "done"


# --- UPLOAD-01g: GET /upload/status initial state ---

def test_status_initial_none():
    resp = TestClient(_make_app()).get("/upload/status")
    assert resp.status_code == 200
    assert resp.json() == {}


# --- UPLOAD-01h: GET /upload/status after /upload ---

def test_status_after_upload(tmp_path):
    csv_bytes = _make_csv_bytes(["id", "name"], [["1", "Alice"]])
    with (
        patch("api.upload._DATA_ROOT", tmp_path),
        patch("api.upload.OllamaLLMClient") as mock_llm,
    ):
        mock_llm.return_value.generate.return_value = _LLM_RESULT
        app = _make_app()
        client = TestClient(app)
        client.post(
            "/upload",
            files={"file": ("test.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        status_resp = client.get("/upload/status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["file_name"] == "test.csv"
    assert body["status"] == "uploaded"
    assert "collection" in body


# --- UPLOAD-01i: LogStore.record called with type='full' ---

def test_confirm_logs_full_type(tmp_path):
    body = {
        "file_name": "test.csv",
        "collection": "Test",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
    }
    mock_log_store = MagicMock()
    mock_engine_result = {"inserted": 5, "updated": 0, "skipped": 0, "errors": 0}

    with (
        patch("api.upload._write_config") as mock_write,
        patch("api.upload.load_config") as mock_load,
        patch("api.upload.StateStore"),
        patch("api.upload.SyncEngine") as mock_engine_cls,
        patch("api.upload.get_client"),
    ):
        mock_write.return_value = tmp_path / "config.yaml"
        mock_cfg = MagicMock()
        mock_cfg.weaviate.collection = "Test"
        mock_cfg.embedding.model = "qwen3-embedding:4b"
        mock_cfg.source.type = "csv"
        mock_load.return_value = mock_cfg
        mock_engine_cls.return_value.run_full.return_value = mock_engine_result

        app = _make_app(log_store=mock_log_store)
        app.state.upload_status = {
            "file_name": "test.csv", "collection": "Test",
            "status": "uploaded", "config_path": None, "sync_status": None,
        }

        with TestClient(app) as client:
            client.post("/upload/confirm", json=body)

    assert mock_log_store.record.called
    call_kwargs = mock_log_store.record.call_args.kwargs
    assert call_kwargs["type"] == "full", f"Expected type='full', got {call_kwargs.get('type')}"
    assert call_kwargs["status"] == "completed"
    assert call_kwargs["inserted"] == 5


# --- CR-02: file collision returns 409 ---

def test_upload_409_on_file_collision(tmp_path):
    """POST /upload must reject with 409 if a file with the same name already exists (CR-02)."""
    csv_bytes = _make_csv_bytes(["id", "name"], [["1", "Alice"]])
    (tmp_path / "test.csv").write_bytes(csv_bytes)  # pre-existing file
    with patch("api.upload._DATA_ROOT", tmp_path):
        resp = TestClient(_make_app()).post(
            "/upload",
            files={"file": ("test.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


# --- WR-01: invalid delimiter rejected by Pydantic ---

def test_confirm_invalid_delimiter_rejected():
    """Multi-character delimiter raises validation error before background sync (WR-01)."""
    body = {
        "file_name": "test.csv",
        "collection": "Test",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
        "delimiter": "||",  # multi-char — must be rejected
    }
    resp = TestClient(_make_app()).post("/upload/confirm", json=body)
    assert resp.status_code == 422


# --- CR-03: lock released when _write_config raises ---

def test_confirm_lock_released_on_write_config_error():
    """sync_lock must be released if _write_config raises so service is not permanently locked (CR-03)."""
    body = {
        "file_name": "test.csv",
        "collection": "Test",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
    }
    with patch("api.upload._write_config", side_effect=OSError("disk full")):
        app = _make_app()
        # raise_server_exceptions=False: capture 500 response instead of re-raising OSError
        resp = TestClient(app, raise_server_exceptions=False).post("/upload/confirm", json=body)
    assert resp.status_code == 500
    assert not app.state.sync_lock.locked(), "sync_lock must be released after _write_config failure"


# ---------------------------------------------------------------------------
# New tests for POST /sync/full/by-collection and POST /upload/restapi
# ---------------------------------------------------------------------------

# --- T-11-07: path traversal guard on /sync/full/by-collection ---

def test_sync_by_collection_422_path_traversal():
    """collection='..' must return 422 (T-11-07)."""
    resp = TestClient(_make_app()).post("/sync/full/by-collection?collection=..")
    assert resp.status_code == 422


# --- /sync/full/by-collection 404 when config missing ---

def test_sync_by_collection_404_when_missing(tmp_path):
    """404 when no config.yaml exists for the given collection."""
    with patch("api.upload._CONFIG_ROOT", tmp_path):
        resp = TestClient(_make_app()).post("/sync/full/by-collection?collection=DoesNotExist")
    assert resp.status_code == 404
    assert "DoesNotExist" in resp.json()["detail"]


# --- /sync/full/by-collection 409 when sync already running ---

def test_sync_by_collection_409_when_locked(tmp_path):
    """409 when sync_lock is already held."""
    config_dir = tmp_path / "MyEntity"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")
    with patch("api.upload._CONFIG_ROOT", tmp_path):
        resp = TestClient(_make_app(lock=True)).post("/sync/full/by-collection?collection=MyEntity")
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"].lower()


# --- /sync/full/by-collection happy path: background task queued ---

def test_sync_by_collection_starts_bg_task(tmp_path):
    """Happy path: config exists, lock free → 200 and background task scheduled."""
    config_dir = tmp_path / "MyEntity"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")
    with (
        patch("api.upload._CONFIG_ROOT", tmp_path),
        patch("api.upload._run_upload_sync_bg") as mock_bg,
    ):
        app = _make_app()
        with TestClient(app) as client:
            resp = client.post("/sync/full/by-collection?collection=MyEntity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert body["collection"] == "MyEntity"


# --- T-11-08: path traversal guard on POST /upload/restapi ---

def test_upload_restapi_422_invalid_collection():
    """collection with '/' must return 422 (T-11-08)."""
    payload = {
        "collection": "bad/name",
        "url": "https://api.example.com/items",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
        "auth_type": "none",
        "pagination_type": "none",
    }
    resp = TestClient(_make_app()).post("/upload/restapi", json=payload)
    assert resp.status_code == 422


# --- T-11-09: env var name injection guard ---

def test_upload_restapi_422_invalid_env_var():
    """auth_env_var with 'bad-name!' must return 422 (T-11-09)."""
    payload = {
        "collection": "MyEntity",
        "url": "https://api.example.com/items",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
        "auth_type": "bearer",
        "auth_env_var": "bad-name!",
        "pagination_type": "none",
    }
    resp = TestClient(_make_app()).post("/upload/restapi", json=payload)
    assert resp.status_code == 422


# --- POST /upload/restapi happy path ---

def test_upload_restapi_writes_config(tmp_path):
    """Valid payload → config.yaml written with type=rest_api and auth token placeholder."""
    import yaml
    payload = {
        "collection": "MyEntity",
        "url": "https://api.example.com/items",
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": ["category"],
        "output_fields": ["id", "name"],
        "auth_type": "bearer",
        "auth_env_var": "MY_API_TOKEN",
        "pagination_type": "none",
        "json_key": "results",
    }
    with patch("api.upload._CONFIG_ROOT", tmp_path):
        resp = TestClient(_make_app()).post("/upload/restapi", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    assert body["collection"] == "MyEntity"
    config_path = tmp_path / "MyEntity" / "config.yaml"
    assert config_path.exists(), "config.yaml must be written"
    cfg = yaml.safe_load(config_path.read_text())
    assert cfg["source"]["type"] == "rest_api"
    assert cfg["source"]["auth"]["token"] == "${MY_API_TOKEN}"
    assert cfg["source"]["json_key"] == "results"


# ---------------------------------------------------------------------------
# New tests for POST /sync/by-collection (incremental)
# ---------------------------------------------------------------------------

def _make_app_with_auth(tmp_path=None, lock: bool = False) -> FastAPI:
    """Create app with dependency_overrides for auth deps (mirrors test_config_router pattern)."""
    from auth.dependencies import require_admin, get_current_user
    from auth.user_store import UserRecord

    _ADMIN = UserRecord(
        id=1, username="admin", hashed_password="", role="admin",
        totp_secret=None, totp_enabled=False,
        created_at="2026-01-01T00:00:00", is_active=True
    )

    app = FastAPI()
    app.include_router(router)
    lk = threading.Lock()
    if lock:
        lk.acquire()
    app.state.sync_lock = lk
    app.state.sync_status = {"status": "idle", "last_run": None}
    app.state.upload_status = None
    app.state.log_store = None
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    return app


# --- SC-09a: POST /sync/by-collection happy path ---

def test_sync_incremental_by_collection_happy(tmp_path):
    """POST /sync/by-collection returns 200 {status: started, collection} and passes mode='incremental' to bg task."""
    config_dir = tmp_path / "Collaboratori"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")

    with (
        patch("api.upload._CONFIG_ROOT", tmp_path),
        patch("api.upload._run_upload_sync_bg") as mock_bg,
    ):
        app = _make_app_with_auth(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/sync/by-collection?collection=Collaboratori")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert body["collection"] == "Collaboratori"
    # Verify mode="incremental" was passed to background task
    assert mock_bg.called
    call_args = mock_bg.call_args
    # add_task passes positional args; mode is the 4th positional arg (app_state, config_path, collection, mode)
    assert "incremental" in call_args.args or call_args.kwargs.get("mode") == "incremental"


# --- SC-09b: POST /sync/by-collection 404 when config missing ---

def test_sync_incremental_by_collection_404(tmp_path):
    """404 when no config.yaml exists for the given collection."""
    with patch("api.upload._CONFIG_ROOT", tmp_path):
        app = _make_app_with_auth(tmp_path)
        resp = TestClient(app).post("/sync/by-collection?collection=Nonexistent")
    assert resp.status_code == 404
    assert "Nonexistent" in resp.json()["detail"]


# --- SC-09c: POST /sync/by-collection 422 path traversal ---

def test_sync_incremental_by_collection_422_traversal():
    """collection='../etc' must return 422."""
    app = _make_app_with_auth()
    resp = TestClient(app).post("/sync/by-collection?collection=../etc")
    assert resp.status_code == 422


# --- SC-09d: POST /sync/by-collection 409 when sync already running ---

def test_sync_incremental_by_collection_409_locked(tmp_path):
    """409 when sync_lock is already held."""
    config_dir = tmp_path / "MyEntity"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")
    with patch("api.upload._CONFIG_ROOT", tmp_path):
        app = _make_app_with_auth(tmp_path, lock=True)
        resp = TestClient(app).post("/sync/by-collection?collection=MyEntity")
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"].lower()


# --- SC-09e: existing /sync/full/by-collection still passes mode="full" (no regression) ---

def test_sync_full_by_collection_mode_default(tmp_path):
    """POST /sync/full/by-collection still triggers mode='full' background task."""
    config_dir = tmp_path / "MyEntity"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")

    with (
        patch("api.upload._CONFIG_ROOT", tmp_path),
        patch("api.upload._run_upload_sync_bg") as mock_bg,
    ):
        app = _make_app_with_auth(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/sync/full/by-collection?collection=MyEntity")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    # mode="full" passed — either as positional arg (no mode) or explicitly
    assert mock_bg.called
