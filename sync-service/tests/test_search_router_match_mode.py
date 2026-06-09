"""Unit tests for ?match_mode= query param forwarding and fuzzy expansion wiring — Phase 23 Plan 04.

Tests cover:
- TestMatchModeOverride: ?match_mode=and|or forwarded to vector_store.search as match_mode_override
- TestFuzzyExpansionWiring: fuzzy expansion called for 1-2 term queries, skipped for 3+ terms
  and for Weaviate engine
"""
from __future__ import annotations

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


def _make_config_yaml(tmp_path: Path, collection: str, text_fields: list[str] | None = None) -> None:
    """Write a minimal config.yaml for collection under tmp_path."""
    text_fields = text_fields or ["name"]
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


def _make_app(vector_store=None) -> FastAPI:
    """Create minimal FastAPI app with search_router and mocked state."""
    app = FastAPI()
    app.include_router(search_router)
    mock_vs = vector_store or MagicMock()
    # Make search return empty list by default
    mock_vs.search.return_value = []
    app.state.vector_store = mock_vs
    app.state.cache_store = None
    app.state.history_store = None
    app.state.embedding_adapter = None
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    return app


class TestMatchModeOverride:
    def test_match_mode_and_forwarded_to_store(self, tmp_path, monkeypatch):
        """GET /search?match_mode=and → vector_store.search called with match_mode_override='and'."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "TestColl")

        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        app = _make_app(vector_store=mock_vs)

        with TestClient(app) as client:
            resp = client.get(
                "/search?q=hello&collection=TestColl&match_mode=and",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        call_kwargs = mock_vs.search.call_args[1]
        assert call_kwargs.get("match_mode_override") == "and"

    def test_match_mode_or_forwarded_to_store(self, tmp_path, monkeypatch):
        """GET /search?match_mode=or → vector_store.search called with match_mode_override='or'."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "TestColl")

        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        app = _make_app(vector_store=mock_vs)

        with TestClient(app) as client:
            resp = client.get(
                "/search?q=hello&collection=TestColl&match_mode=or",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        call_kwargs = mock_vs.search.call_args[1]
        assert call_kwargs.get("match_mode_override") == "or"

    def test_no_match_mode_param_passes_none(self, tmp_path, monkeypatch):
        """Omitted ?match_mode= → vector_store.search called with match_mode_override=None."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "TestColl")

        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        app = _make_app(vector_store=mock_vs)

        with TestClient(app) as client:
            resp = client.get(
                "/search?q=hello&collection=TestColl",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        call_kwargs = mock_vs.search.call_args[1]
        assert call_kwargs.get("match_mode_override") is None

    def test_invalid_match_mode_returns_422(self, tmp_path, monkeypatch):
        """?match_mode=maybe → 422 (FastAPI Literal validation)."""
        import api.search as search_mod
        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "TestColl")

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get(
                "/search?q=hello&collection=TestColl&match_mode=maybe",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 422


class TestFuzzyExpansionWiring:
    """Tests that fuzzy expansion is wired in api/search.py (W1 requirement)."""

    def test_fuzzy_expansion_called_for_single_term_query(self, tmp_path, monkeypatch):
        """Single-term query on Qdrant engine → _apply_fuzzy_expansion is called with non-empty vocab."""
        import api.search as search_mod
        import vector_stores.qdrant_store as qdrant_store_mod
        import vector_stores.fuzzy as fuzzy_mod

        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "TestColl")

        # Pre-populate fuzzy vocab
        qdrant_store_mod._fuzzy_vocab["TestColl"] = frozenset({"tavola", "libro"})

        spy_calls = []
        original_fn = fuzzy_mod._apply_fuzzy_expansion

        def spy_fn(query, vocab, **kwargs):
            spy_calls.append({"query": query, "vocab": vocab})
            return original_fn(query, vocab, **kwargs)

        monkeypatch.setattr(fuzzy_mod, "_apply_fuzzy_expansion", spy_fn)
        monkeypatch.setattr(search_mod, "_apply_fuzzy_expansion", spy_fn)

        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        app = _make_app(vector_store=mock_vs)

        try:
            with TestClient(app) as client:
                resp = client.get(
                    "/search?q=tavolo&collection=TestColl",
                    headers={"Authorization": "Bearer fake"},
                )
            assert resp.status_code == 200
            # Spy should have been called
            assert len(spy_calls) > 0, "Expected _apply_fuzzy_expansion to be called"
            # Vocab should be non-empty
            assert len(spy_calls[0]["vocab"]) > 0, "Expected vocab to be non-empty"
        finally:
            qdrant_store_mod._fuzzy_vocab.pop("TestColl", None)

    def test_fuzzy_expansion_produces_nontrivial_variants(self, tmp_path, monkeypatch):
        """With real _apply_fuzzy_expansion and vocab containing 'tavola', query 'tavolo'
        should produce an expanded query different from the input."""
        import api.search as search_mod
        import vector_stores.qdrant_store as qdrant_store_mod

        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "TestColl")

        # Pre-populate fuzzy vocab with 'tavola' which is Levenshtein-1 from 'tavolo'
        qdrant_store_mod._fuzzy_vocab["TestColl"] = frozenset({"tavola", "libro"})

        captured_query = []
        mock_vs = MagicMock()

        def capture_search(**kwargs):
            captured_query.append(kwargs.get("query", ""))
            return []

        mock_vs.search.side_effect = lambda *args, **kwargs: capture_search(**kwargs) or []
        app = _make_app(vector_store=mock_vs)

        try:
            with TestClient(app) as client:
                resp = client.get(
                    "/search?q=tavolo&collection=TestColl",
                    headers={"Authorization": "Bearer fake"},
                )
            assert resp.status_code == 200
            # The query passed to vector_store.search should differ from raw 'tavolo'
            # if fuzzy expansion found variants
            if captured_query:
                # If vocab has 'tavola' (Levenshtein-1 from 'tavolo'), expansion should fire
                # Only assert if python-Levenshtein is installed
                try:
                    import Levenshtein  # noqa: F401
                    assert captured_query[0] != "tavolo", (
                        f"Expected expanded query to differ from 'tavolo', got: {captured_query[0]}"
                    )
                except ImportError:
                    pytest.skip("python-Levenshtein not installed — skipping expansion assertion")
        finally:
            qdrant_store_mod._fuzzy_vocab.pop("TestColl", None)

    def test_fuzzy_expansion_skipped_for_three_plus_term_query(self, tmp_path, monkeypatch):
        """q='a b c d' → _apply_fuzzy_expansion called but result equals input (Pitfall 5 guard)."""
        import api.search as search_mod
        import vector_stores.qdrant_store as qdrant_store_mod
        import vector_stores.fuzzy as fuzzy_mod

        monkeypatch.setenv("VECTOR_STORE_ENGINE", "qdrant")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "TestColl")

        qdrant_store_mod._fuzzy_vocab["TestColl"] = frozenset({"tavola", "libro", "carta"})

        spy_calls = []
        original_fn = fuzzy_mod._apply_fuzzy_expansion

        def spy_fn(query, vocab, **kwargs):
            result = original_fn(query, vocab, **kwargs)
            spy_calls.append({"query": query, "result": result})
            return result

        monkeypatch.setattr(fuzzy_mod, "_apply_fuzzy_expansion", spy_fn)
        monkeypatch.setattr(search_mod, "_apply_fuzzy_expansion", spy_fn)

        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        app = _make_app(vector_store=mock_vs)

        try:
            with TestClient(app) as client:
                resp = client.get(
                    "/search?q=a+b+c+d&collection=TestColl",
                    headers={"Authorization": "Bearer fake"},
                )
            assert resp.status_code == 200
            # Spy was called but result equals input (Pitfall 5 guard)
            if spy_calls:
                last = spy_calls[-1]
                assert last["result"] == last["query"], (
                    f"Expected fuzzy expansion to be a no-op for 4-term query, "
                    f"but got: {last['result']!r}"
                )
        finally:
            qdrant_store_mod._fuzzy_vocab.pop("TestColl", None)

    def test_fuzzy_expansion_skipped_for_weaviate_engine(self, tmp_path, monkeypatch):
        """VECTOR_STORE_ENGINE=weaviate → _apply_fuzzy_expansion NOT called."""
        import api.search as search_mod
        import vector_stores.fuzzy as fuzzy_mod

        monkeypatch.setenv("VECTOR_STORE_ENGINE", "weaviate")
        monkeypatch.setattr(search_mod, "_CONFIG_ROOT", tmp_path)
        _make_config_yaml(tmp_path, "TestColl")

        spy_calls = []
        original_fn = fuzzy_mod._apply_fuzzy_expansion

        def spy_fn(query, vocab, **kwargs):
            spy_calls.append({"query": query, "vocab": vocab})
            return original_fn(query, vocab, **kwargs)

        monkeypatch.setattr(fuzzy_mod, "_apply_fuzzy_expansion", spy_fn)
        monkeypatch.setattr(search_mod, "_apply_fuzzy_expansion", spy_fn)

        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        app = _make_app(vector_store=mock_vs)

        with TestClient(app) as client:
            resp = client.get(
                "/search?q=tavolo&collection=TestColl",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert len(spy_calls) == 0, (
            f"Expected _apply_fuzzy_expansion NOT to be called for Weaviate engine, "
            f"but was called {len(spy_calls)} times"
        )
