"""Test del router sync — POST /sync, POST /sync/full, GET /sync/status.

Verifica:
- POST /sync avvia un sync incrementale e restituisce {status, job}
- POST /sync/full avvia un full re-index e restituisce {status, job}
- GET /sync/status restituisce lo stato corrente
- POST /sync con lock attivo restituisce 409
- POST /sync/full con lock attivo restituisce 409
- Dopo il background task: status=completed, last_run popolato, lock rilasciato
- In caso di errore nel task: status=failed, last_run.error valorizzato, lock rilasciato
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.sync import router


def _make_lock(locked: bool = False) -> threading.Lock:
    """Return a threading.Lock, optionally pre-acquired to simulate a busy state."""
    lock = threading.Lock()
    if locked:
        lock.acquire()
    return lock


def _make_app(engine=None, lock=False):
    """Crea una FastAPI minimale con il router sync."""
    app = FastAPI()
    app.include_router(router)
    app.state.sync_lock = _make_lock(locked=lock)
    app.state.sync_status = {"status": "idle", "last_run": None}
    if engine is None:
        engine = MagicMock()
        engine.run_incremental.return_value = {
            "total": 5,
            "inserted": 3,
            "updated": 2,
            "skipped": 0,
            "errors": 0,
            "timestamp": "2026-05-10T00:00:00+00:00",
        }
        engine.run_full.return_value = {
            "total": 10,
            "inserted": 10,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "timestamp": "2026-05-10T00:00:00+00:00",
        }
    app.state.sync_engine = engine
    return app


# ---------------------------------------------------------------------------
# GET /sync/status
# ---------------------------------------------------------------------------

def test_sync_status_initial_idle():
    """GET /sync/status restituisce idle inizialmente."""
    client = TestClient(_make_app())
    resp = client.get("/sync/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "idle"
    assert data["last_run"] is None


# ---------------------------------------------------------------------------
# POST /sync — incremental
# ---------------------------------------------------------------------------

def test_post_sync_returns_started():
    """POST /sync senza lock restituisce {status: started, job: incremental}."""
    client = TestClient(_make_app())
    resp = client.post("/sync")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert body["job"] == "incremental"


def test_post_sync_background_sets_completed():
    """Dopo il background task, status diventa completed e last_run è popolato.

    TestClient esegue i BackgroundTasks in modo sincrono al termine della request.
    """
    app = _make_app()
    client = TestClient(app)
    client.post("/sync")
    status_resp = client.get("/sync/status")
    data = status_resp.json()
    assert data["status"] == "completed", f"status atteso completed, got: {data}"
    assert data["last_run"] is not None


def test_post_sync_lock_released_after_success():
    """Il lock deve essere rilasciato dopo il completamento con successo."""
    app = _make_app()
    client = TestClient(app)
    client.post("/sync")
    assert not app.state.sync_lock.locked()


def test_post_sync_409_when_locked():
    """POST /sync con lock=True restituisce 409 con detail appropriato."""
    client = TestClient(_make_app(lock=True))
    resp = client.post("/sync")
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /sync/full
# ---------------------------------------------------------------------------

def test_post_sync_full_returns_started():
    """POST /sync/full senza lock restituisce {status: started, job: full}."""
    client = TestClient(_make_app())
    resp = client.post("/sync/full")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert body["job"] == "full"


def test_post_sync_full_background_sets_completed():
    """Dopo il background task full, status diventa completed."""
    app = _make_app()
    client = TestClient(app)
    client.post("/sync/full")
    data = client.get("/sync/status").json()
    assert data["status"] == "completed"
    assert data["last_run"] is not None


def test_post_sync_full_lock_released_after_success():
    """Il lock deve essere rilasciato dopo il completamento full."""
    app = _make_app()
    client = TestClient(app)
    client.post("/sync/full")
    assert not app.state.sync_lock.locked()


def test_post_sync_full_409_when_locked():
    """POST /sync/full con lock=True restituisce 409."""
    client = TestClient(_make_app(lock=True))
    resp = client.post("/sync/full")
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Error handling nel background task
# ---------------------------------------------------------------------------

def test_sync_background_task_failure_sets_failed():
    """Se engine.run_incremental lancia un'eccezione, status diventa failed."""
    engine = MagicMock()
    engine.run_incremental.side_effect = RuntimeError("connessione rotta")
    app = _make_app(engine=engine)
    client = TestClient(app)
    client.post("/sync")
    data = client.get("/sync/status").json()
    assert data["status"] == "failed"
    assert data["last_run"] is not None
    assert "error" in data["last_run"]
    assert "connessione rotta" in data["last_run"]["error"]


def test_sync_lock_released_after_failure():
    """Il lock deve essere rilasciato anche dopo un errore nel background task."""
    engine = MagicMock()
    engine.run_incremental.side_effect = RuntimeError("errore test")
    app = _make_app(engine=engine)
    client = TestClient(app)
    client.post("/sync")
    assert not app.state.sync_lock.locked()


def test_sync_full_background_task_failure_sets_failed():
    """Se engine.run_full lancia, status diventa failed."""
    engine = MagicMock()
    engine.run_full.side_effect = ValueError("collezione non esiste")
    app = _make_app(engine=engine)
    client = TestClient(app)
    client.post("/sync/full")
    data = client.get("/sync/status").json()
    assert data["status"] == "failed"
    assert "error" in data["last_run"]


# ---------------------------------------------------------------------------
# took_ms in sync/status last_run (D-11)
# ---------------------------------------------------------------------------

def test_sync_status_last_run_includes_took_ms():
    """After POST /sync, GET /sync/status last_run has took_ms as int >= 0."""
    app = _make_app()
    client = TestClient(app)
    client.post("/sync")
    data = client.get("/sync/status").json()
    assert data["status"] == "completed"
    assert "took_ms" in data["last_run"], f"took_ms missing from last_run: {data['last_run']}"
    assert isinstance(data["last_run"]["took_ms"], int)
    assert data["last_run"]["took_ms"] >= 0


def test_sync_full_status_last_run_includes_took_ms():
    """After POST /sync/full, GET /sync/status last_run has took_ms as int >= 0."""
    app = _make_app()
    client = TestClient(app)
    client.post("/sync/full")
    data = client.get("/sync/status").json()
    assert data["status"] == "completed"
    assert "took_ms" in data["last_run"]
    assert isinstance(data["last_run"]["took_ms"], int)
    assert data["last_run"]["took_ms"] >= 0


def test_sync_failed_status_last_run_includes_took_ms():
    """After failed sync, GET /sync/status last_run still has took_ms."""
    engine = MagicMock()
    engine.run_incremental.side_effect = RuntimeError("connessione rotta")
    app = _make_app(engine=engine)
    client = TestClient(app)
    client.post("/sync")
    data = client.get("/sync/status").json()
    assert data["status"] == "failed"
    assert "took_ms" in data["last_run"], f"took_ms missing from failed last_run: {data['last_run']}"
    assert isinstance(data["last_run"]["took_ms"], int)
    assert data["last_run"]["took_ms"] >= 0


def test_post_sync_response_has_no_took_ms():
    """POST /sync response does NOT include took_ms — shape is {status, job} only (D-11)."""
    client = TestClient(_make_app())
    resp = client.post("/sync")
    body = resp.json()
    assert "took_ms" not in body


def test_post_sync_full_response_has_no_took_ms():
    """POST /sync/full response does NOT include took_ms (D-11)."""
    client = TestClient(_make_app())
    resp = client.post("/sync/full")
    body = resp.json()
    assert "took_ms" not in body
