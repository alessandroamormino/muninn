"""Test degli endpoint di osservabilità: /health e /info.

Verifica:
- /health 200 quando vector_store.is_live() == True (D-07)
- /health 503 quando vector_store.is_live() == False (D-07)
- /health 503 quando vector_store non è inizializzato
- /health 503 quando is_live() raise eccezione
- /info 200 con total_objects valorizzato quando count() riesce (D-06)
- /info 200 con total_objects=null quando count() raise (D-06 graceful degradation)
- /info 200 con total_objects=null quando vector_store non è inizializzato
- /info preserva tutte le chiavi pre-esistenti (embedding_model, ecc.)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import main
from auth.dependencies import get_current_user
from auth.user_store import UserRecord

_ADMIN = UserRecord(
    id=1, username="admin", hashed_password="", role="admin",
    totp_secret=None, totp_enabled=False,
    created_at="2026-01-01T00:00:00", is_active=True,
)


@pytest.fixture
def client():
    """Build a TestClient against the real app object without triggering lifespan.

    TestClient(app) used WITHOUT a `with` block does not run startup/shutdown
    events, so open_client() is not called and we don't need a real Weaviate.
    """
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _teardown():
    """Reset app state and dependency overrides after each test."""
    yield
    if hasattr(main.app.state, "vector_store"):
        del main.app.state.vector_store
    main.app.dependency_overrides.clear()


@pytest.fixture
def authed_client():
    """TestClient with get_current_user dependency overridden to _ADMIN."""
    main.app.dependency_overrides[get_current_user] = lambda: _ADMIN
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_returns_200_when_weaviate_is_live(client):
    """is_live()==True -> 200 with {'status': 'ok'} (D-07)."""
    mock_vs = MagicMock()
    mock_vs.is_live.return_value = True
    main.app.state.vector_store = mock_vs
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_returns_503_when_weaviate_not_live(client):
    """is_live()==False -> 503 with {'status': 'weaviate_unreachable'} (D-07)."""
    mock_vs = MagicMock()
    mock_vs.is_live.return_value = False
    main.app.state.vector_store = mock_vs
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "weaviate_unreachable"}


def test_health_returns_503_when_get_client_raises(client):
    """No vector_store initialized -> 503 (graceful, no traceback to client)."""
    if hasattr(main.app.state, "vector_store"):
        del main.app.state.vector_store
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "weaviate_unreachable"}


def test_health_returns_503_when_is_live_raises(client):
    """is_live() itself raises -> 503."""
    mock_vs = MagicMock()
    mock_vs.is_live.side_effect = ConnectionError("network down")
    main.app.state.vector_store = mock_vs
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "weaviate_unreachable"}


# ---------------------------------------------------------------------------
# /info
# ---------------------------------------------------------------------------

def test_info_returns_total_objects_from_aggregate(authed_client):
    """count()==42 -> response.total_objects == 42 (D-06)."""
    mock_vs = MagicMock()
    mock_vs.count.return_value = 42
    main.app.state.vector_store = mock_vs
    resp = authed_client.get("/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_objects"] == 42


def test_info_returns_null_total_objects_when_aggregate_raises(authed_client):
    """count() raises -> total_objects is null, status remains 200 (D-06)."""
    mock_vs = MagicMock()
    mock_vs.count.side_effect = RuntimeError("boom")
    main.app.state.vector_store = mock_vs
    resp = authed_client.get("/info")
    assert resp.status_code == 200
    assert resp.json()["total_objects"] is None


def test_info_returns_null_total_objects_when_get_client_raises(authed_client):
    """No vector_store initialized -> total_objects is null, status 200 (graceful)."""
    if hasattr(main.app.state, "vector_store"):
        del main.app.state.vector_store
    resp = authed_client.get("/info")
    assert resp.status_code == 200
    assert resp.json()["total_objects"] is None


def test_info_preserves_all_legacy_keys(authed_client):
    """All pre-existing keys (embedding_model, etc.) must remain in the response."""
    mock_vs = MagicMock()
    mock_vs.count.return_value = 0
    main.app.state.vector_store = mock_vs
    resp = authed_client.get("/info")
    body = resp.json()
    expected_keys = {
        "embedding_model",
        "embedding_type",
        "collection",
        "weaviate_url",
        "sync_mode",
        "sync_schedule",
        "total_objects",
    }
    assert expected_keys.issubset(body.keys()), (
        f"Missing keys in /info response. Got: {set(body.keys())}, "
        f"expected superset of: {expected_keys}"
    )
