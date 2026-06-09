"""Unit tests for GET /search/suggest endpoint — Phase 23 Plan 04.

Tests cover:
- Returns [] for non-Qdrant engine (graceful Weaviate path)
- Returns 422 for invalid collection name (path traversal guard T-23-04-02)
- Returns [] when collection config not found
- Deduplicates values up to limit
- Respects limit param
- Uses first text_field for suggestion values
- Requires JWT auth (T-23-04-03)
- Returns [] on Qdrant scroll error (no 500)
- Route has explicit @limiter.limit("60/minute") decorator (T-23-04-04)
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.search import router as search_router
from auth.dependencies import get_current_user
from auth.user_store import UserRecord

_FAKE_USER = UserRecord(
    id=1, username="tester", hashed_password="", role="reader",
    totp_secret=None, totp_enabled=False,
    created_at="2026-01-01T00:00:00", is_active=True,
)


def _make_app(vector_store=None, config_root: Path | None = None) -> FastAPI:
    """Create minimal FastAPI app with search_router and mocked state."""
    from api.search import _CONFIG_ROOT as _orig_cfg_root
    app = FastAPI()
    app.include_router(search_router)
    app.state.vector_store = vector_store or MagicMock()
    app.state.cache_store = None
    app.state.history_store = None
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    return app


def _make_config_yaml(tmp_path: Path, collection: str, text_fields: list[str]) -> Path:
    """Write a minimal config.yaml for collection under tmp_path."""
    coll_dir = tmp_path / collection
    coll_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "source": {"type": "csv", "file_path": "./data/test.csv", "id_field": "id", "delimiter": ","},
        "embedding": {"type": "ollama", "model": "qwen3-embedding:4b"},
        "vector_store": {
            "collection": collection,
            "search_mode": "fts",
            "text_fields": text_fields,
            "metadata_fields": [],
        },
        "api": {"output_fields": text_fields, "default_limit": 10, "max_limit": 100},
    }
    config_path = coll_dir / "config.yaml"
    config_path.write_text(yaml.dump(cfg))
    return config_path


def _make_scroll_point(payload: dict):
    p = MagicMock()
    p.payload = payload
    return p


class TestSuggestEndpoint:
    def test_returns_empty_for_non_qdrant_engine(self, monkeypatch, tmp_path):
        """Weaviate engine path returns [] without calling scroll."""
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "weaviate")
        _make_config_yaml(tmp_path, "Products", ["name"])

        import api.search as search_mod
        orig_root = search_mod._CONFIG_ROOT
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/search/suggest?q=tav&collection=Products", headers={"Authorization": "Bearer fake"})

        assert resp.status_code == 200
        assert resp.json() == []
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", orig_root)

    def test_path_traversal_collection_returns_422(self, tmp_path):
        """Invalid collection name (path traversal attempt) → 422."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get(
                "/search/suggest?q=test&collection=../etc/passwd",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 422
        data = resp.json()
        # Either FastAPI validation error or our custom HTTPException
        detail = data.get("detail", "")
        assert "Invalid collection" in str(detail) or "Invalid collection" in str(data)

    def test_missing_config_returns_empty_list(self, tmp_path, monkeypatch):
        """Valid collection name but no config.yaml → returns []."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get(
                "/search/suggest?q=test&collection=NonExistentCollection",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_deduplicated_suggestions(self, tmp_path, monkeypatch):
        """Scroll returns 50 points with duplicated name values → response has unique strings up to limit."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "Products", ["name"])

        # 50 points but only 3 unique names
        points = [_make_scroll_point({"name": f"item_{i % 3}"}) for i in range(50)]
        mock_vs = MagicMock()
        mock_vs._client.scroll.return_value = (points, None)

        app = _make_app(vector_store=mock_vs)
        with TestClient(app) as client:
            resp = client.get(
                "/search/suggest?q=item&collection=Products&limit=10",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        result = resp.json()
        # Deduplicated: only unique values
        assert len(result) == len(set(result))
        assert len(result) <= 3

    def test_respects_limit_param(self, tmp_path, monkeypatch):
        """Scroll returns many unique values → response has exactly limit strings."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "Products", ["name"])

        # 50 unique points
        points = [_make_scroll_point({"name": f"unique_item_{i}"}) for i in range(50)]
        mock_vs = MagicMock()
        mock_vs._client.scroll.return_value = (points, None)

        app = _make_app(vector_store=mock_vs)
        with TestClient(app) as client:
            resp = client.get(
                "/search/suggest?q=unique&collection=Products&limit=3",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        result = resp.json()
        assert len(result) == 3

    def test_uses_first_text_field_for_suggestion_values(self, tmp_path, monkeypatch):
        """text_fields=['name','description'] → response strings come from 'name' payload."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "Products", ["name", "description"])

        points = [
            _make_scroll_point({"name": "good_name", "description": "some desc"}),
        ]
        mock_vs = MagicMock()
        mock_vs._client.scroll.return_value = (points, None)

        app = _make_app(vector_store=mock_vs)
        with TestClient(app) as client:
            resp = client.get(
                "/search/suggest?q=good&collection=Products&limit=5",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        result = resp.json()
        assert "good_name" in result
        assert "some desc" not in result

    def test_unauthenticated_returns_401(self, tmp_path):
        """No Authorization header → 401 (JWT guard active)."""
        from fastapi import FastAPI as _FastAPI
        app = _FastAPI()
        app.include_router(search_router)
        app.state.vector_store = MagicMock()
        app.state.cache_store = None
        app.state.history_store = None
        # Do NOT override get_current_user — let it enforce auth
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/search/suggest?q=test&collection=Products")
        assert resp.status_code == 401

    def test_returns_empty_on_qdrant_error(self, tmp_path, monkeypatch):
        """Qdrant scroll raises → response is [] (no 500)."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "Products", ["name"])

        mock_vs = MagicMock()
        mock_vs._client.scroll.side_effect = RuntimeError("qdrant connection failed")

        app = _make_app(vector_store=mock_vs)
        with TestClient(app) as client:
            resp = client.get(
                "/search/suggest?q=test&collection=Products",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_suggest_route_has_rate_limit_decorator(self):
        """The search_suggest function source contains '60/minute' (T-23-04-04)."""
        from api.search import search_suggest
        src = inspect.getsource(search_suggest)
        assert "60/minute" in src, (
            "Expected @limiter.limit('60/minute') decorator on search_suggest. "
            "Got source:\n" + src[:500]
        )
