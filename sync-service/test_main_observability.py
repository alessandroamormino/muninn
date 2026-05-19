"""Test degli endpoint di osservabilità: /health e /info.

Verifica:
- /health 200 quando Weaviate is_live() == True (D-07)
- /health 503 quando Weaviate is_live() == False (D-07)
- /health 503 quando get_client() raise (client non aperto)
- /health 503 quando is_live() raise eccezione
- /info 200 con total_objects valorizzato quando aggregate riesce (D-06)
- /info 200 con total_objects=null quando aggregate raise (D-06 graceful degradation)
- /info 200 con total_objects=null quando get_client() raise
- /info preserva tutte le chiavi pre-esistenti (embedding_model, ecc.)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client():
    """Build a TestClient against the real app object without triggering lifespan.

    TestClient(app) used WITHOUT a `with` block does not run startup/shutdown
    events, so open_client() is not called and we don't need a real Weaviate.
    """
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_returns_200_when_weaviate_is_live(client):
    """is_live()==True -> 200 with {'status': 'ok'} (D-07)."""
    fake_client = MagicMock()
    fake_client.is_live.return_value = True
    with patch.object(main, "get_client", return_value=fake_client):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_returns_503_when_weaviate_not_live(client):
    """is_live()==False -> 503 with {'status': 'weaviate_unreachable'} (D-07)."""
    fake_client = MagicMock()
    fake_client.is_live.return_value = False
    with patch.object(main, "get_client", return_value=fake_client):
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "weaviate_unreachable"}


def test_health_returns_503_when_get_client_raises(client):
    """get_client() raises RuntimeError -> 503 (graceful, no traceback to client)."""
    with patch.object(main, "get_client", side_effect=RuntimeError("not open")):
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "weaviate_unreachable"}


def test_health_returns_503_when_is_live_raises(client):
    """is_live() itself raises -> 503."""
    fake_client = MagicMock()
    fake_client.is_live.side_effect = ConnectionError("network down")
    with patch.object(main, "get_client", return_value=fake_client):
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "weaviate_unreachable"}


# ---------------------------------------------------------------------------
# /info
# ---------------------------------------------------------------------------

def _fake_client_with_total(total: int):
    """Build a fake client whose collections.get(x).aggregate.over_all returns total."""
    fc = MagicMock()
    agg = MagicMock()
    agg.total_count = total
    fc.collections.get.return_value.aggregate.over_all.return_value = agg
    return fc


def test_info_returns_total_objects_from_aggregate(client):
    """aggregate.over_all().total_count==42 -> response.total_objects == 42 (D-06)."""
    with patch.object(main, "get_client", return_value=_fake_client_with_total(42)):
        resp = client.get("/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_objects"] == 42


def test_info_returns_null_total_objects_when_aggregate_raises(client):
    """aggregate.over_all raises -> total_objects is null, status remains 200 (D-06)."""
    fc = MagicMock()
    fc.collections.get.return_value.aggregate.over_all.side_effect = RuntimeError("boom")
    with patch.object(main, "get_client", return_value=fc):
        resp = client.get("/info")
    assert resp.status_code == 200
    assert resp.json()["total_objects"] is None


def test_info_returns_null_total_objects_when_get_client_raises(client):
    """get_client() raises -> total_objects is null, status 200 (graceful)."""
    with patch.object(main, "get_client", side_effect=RuntimeError("not open")):
        resp = client.get("/info")
    assert resp.status_code == 200
    assert resp.json()["total_objects"] is None


def test_info_preserves_all_legacy_keys(client):
    """All pre-existing keys (embedding_model, etc.) must remain in the response."""
    with patch.object(main, "get_client", return_value=_fake_client_with_total(0)):
        resp = client.get("/info")
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
