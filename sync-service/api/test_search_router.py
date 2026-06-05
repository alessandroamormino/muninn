"""Test del router search — GET /search.

Verifica:
- Happy path: 200 con shape {query, took_ms, results:[{props..., _score}]}
- Default limit (settings.api.default_limit) quando ?limit assente (D-05)
- Custom limit valido viene passato a vs.search()
- Limit < 1 → 422 (D-05)
- Limit > max_limit → 422 (D-05)
- Query vuota → 422 (FastAPI Query min_length=1)
- Default fields (settings.api.output_fields) quando ?fields assente (D-04)
- Custom fields validi vengono proiettati dalla risposta
- Field non in (text_fields ∪ metadata_fields) → 422 (D-03)
- Mix di field valido + invalido → 422 strict
- _score presente in ogni risultato
- vs.search() raise → 503 con detail 'Search backend unavailable'
- Filter parsing (D-01 through D-09)
- took_ms nella risposta (D-10)
- mode='hybrid' passato di default (D-12)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.search import router
from vector_stores.base import SearchHit


def _make_hits(*items: tuple[dict, float]) -> list[SearchHit]:
    """Build a list of SearchHit from (properties, score) tuples."""
    return [SearchHit(properties=props, score=score) for props, score in items]


def _make_mock_vs(
    search_return: list[SearchHit] | None = None,
    search_side_effect: Exception | None = None,
) -> MagicMock:
    """Return a MagicMock BaseVectorStore."""
    vs = MagicMock()
    if search_side_effect is not None:
        vs.search.side_effect = search_side_effect
    else:
        vs.search.return_value = (
            search_return
            if search_return is not None
            else _make_hits(({"title": "Foo", "overview": "A foo film"}, 0.85))
        )
    return vs


def _make_app() -> FastAPI:
    from auth.dependencies import get_current_user
    from auth.user_store import UserRecord

    _ADMIN = UserRecord(
        id=1, username="admin", hashed_password="", role="admin",
        totp_secret=None, totp_enabled=False,
        created_at="2026-01-01T00:00:00", is_active=True,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    app.state.cache_store = None
    app.state.history_store = None
    return app


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_search_returns_query_and_results_shape():
    """GET /search?q=hello → 200 with body {query, took_ms, results}."""
    vs = _make_mock_vs(_make_hits(
        ({"title": "Foo Movie", "overview": "A foo film"}, 0.9),
        ({"title": "Bar Movie", "overview": "A bar film"}, 0.7),
    ))
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=hello")
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "hello"
    assert isinstance(body["results"], list)
    assert len(body["results"]) == 2


def test_search_includes_score_in_each_result():
    """Each result has _score from SearchHit.score."""
    vs = _make_mock_vs(_make_hits(({"title": "Movie"}, 0.75)))
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=anything")
    body = resp.json()
    assert body["results"][0]["_score"] == pytest.approx(0.75)
    assert "title" in body["results"][0]


# ---------------------------------------------------------------------------
# Limit validation (D-05)
# ---------------------------------------------------------------------------

def test_search_uses_default_limit_when_absent():
    """?limit absent → vs.search() called with limit=settings.api.default_limit (10)."""
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    TestClient(app).get("/search?q=foo")
    assert vs.search.call_args.kwargs["limit"] == 10


def test_search_passes_custom_limit():
    """?limit=5 → vs.search() called with limit=5."""
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    TestClient(app).get("/search?q=foo&limit=5")
    assert vs.search.call_args.kwargs["limit"] == 5


def test_search_rejects_limit_zero():
    """?limit=0 → 422."""
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get("/search?q=foo&limit=0")
    assert resp.status_code == 422
    detail_str = str(resp.json()["detail"]).lower()
    assert "limit" in detail_str or "1" in detail_str


def test_search_rejects_limit_above_max():
    """?limit=101 → 422 (max_limit=100 in config.yaml)."""
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get("/search?q=foo&limit=101")
    assert resp.status_code == 422


def test_search_rejects_empty_query():
    """?q= → 422 (FastAPI Query min_length=1)."""
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get("/search?q=")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Field projection (D-03, D-04)
# ---------------------------------------------------------------------------

def test_search_uses_default_output_fields_when_fields_absent():
    """?fields absent → result contains exactly output_fields ∩ allowed (D-04)."""
    from config.settings import settings as app_settings
    output_fields = list(app_settings.api.output_fields)
    allowed = set(app_settings.vector_store.text_fields) | set(app_settings.vector_store.metadata_fields)
    expected_props = [f for f in output_fields if f in allowed]
    props = {f: f"val-{f}" for f in expected_props}
    vs = _make_mock_vs(_make_hits((props, 0.85)))
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=foo")
    body = resp.json()
    assert len(body["results"]) > 0
    result_keys = set(body["results"][0].keys()) - {"_score"}
    assert result_keys == set(expected_props)


def test_search_passes_custom_valid_fields():
    """?fields=<f1,f2> → result contains exactly {f1, f2} (D-03)."""
    from config.settings import settings as app_settings
    allowed = sorted(
        set(app_settings.vector_store.text_fields) | set(app_settings.vector_store.metadata_fields)
    )
    if len(allowed) < 2:
        pytest.skip("Need at least 2 allowed fields for this test")
    f1, f2 = allowed[0], allowed[1]
    props = {f1: f"val-{f1}", f2: f"val-{f2}"}
    vs = _make_mock_vs(_make_hits((props, 0.85)))
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get(f"/search?q=foo&fields={f1},{f2}")
    body = resp.json()
    assert len(body["results"]) > 0
    result_keys = set(body["results"][0].keys()) - {"_score"}
    assert result_keys == {f1, f2}


def test_search_rejects_unknown_field():
    """?fields=bogus → 422 with detail mentioning invalid field (D-03)."""
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get("/search?q=foo&fields=bogus")
    assert resp.status_code == 422
    detail = str(resp.json()["detail"]).lower()
    assert "bogus" in detail or "invalid" in detail


def test_search_rejects_partial_invalid_fields():
    """?fields=<valid>,bogus → 422 (strict — do not silently drop)."""
    from config.settings import settings as app_settings
    valid = list(app_settings.vector_store.text_fields)[0]
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get(f"/search?q=foo&fields={valid},bogus")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_search_returns_503_when_weaviate_fails():
    """vs.search() raises → 503 with detail 'Search backend unavailable'."""
    vs = _make_mock_vs(search_side_effect=RuntimeError("connection lost"))
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=foo")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Search backend unavailable"


def test_search_returns_503_when_get_client_fails():
    """vector_store.search() raising RuntimeError → 503."""
    vs = _make_mock_vs(search_side_effect=RuntimeError("not open"))
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=foo")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Search backend unavailable"


# ---------------------------------------------------------------------------
# Search mode / vector behaviour (D-12)
# ---------------------------------------------------------------------------

def test_search_hybrid_called_with_alpha():
    """vs.search() is called with mode='hybrid' by default (alpha is an impl detail of WeaviateVectorStore)."""
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    TestClient(app).get("/search?q=foo")
    assert vs.search.call_args.kwargs.get("mode") == "hybrid"


def test_search_hybrid_with_embedding_passes_vector():
    """With embedding_adapter, vs.search() receives non-None query_vector."""
    adapter = MagicMock()
    adapter.embed.return_value = [[0.1] * 10]
    vs = _make_mock_vs()
    app = _make_app()
    app.state.embedding_adapter = adapter
    app.state.vector_store = vs
    TestClient(app).get("/search?q=hello")
    assert vs.search.call_args.kwargs.get("query_vector") is not None
    adapter.embed.assert_called_once_with(["hello"])


def test_search_hybrid_without_embedding_has_no_vector():
    """Without embedding_adapter, vs.search() receives query_vector=None."""
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    TestClient(app).get("/search?q=hello")
    assert vs.search.call_args.kwargs.get("query_vector") is None


def test_search_hybrid_returns_503_on_failure():
    """vs.search() raises with embedding_adapter → 503."""
    adapter = MagicMock()
    adapter.embed.return_value = [[0.1] * 10]
    vs = _make_mock_vs(search_side_effect=RuntimeError("gpu error"))
    app = _make_app()
    app.state.embedding_adapter = adapter
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=foo")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Search backend unavailable"


# ---------------------------------------------------------------------------
# Filter param (D-01 through D-09)
# ---------------------------------------------------------------------------

def test_search_no_filter_passes_none_filters():
    """filter absent → vs.search() receives filters=None."""
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    TestClient(app).get("/search?q=foo")
    assert vs.search.call_args.kwargs.get("filters") is None


def test_search_single_filter_passed_to_weaviate():
    """filter=ValidField:SomeValue → vs.search() called with filters not None."""
    from config.settings import settings as app_settings
    valid_meta = app_settings.vector_store.metadata_fields[0]
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    TestClient(app).get(f"/search?q=foo&filter={valid_meta}:SomeValue")
    assert vs.search.call_args.kwargs.get("filters") is not None


def test_search_multi_filter_passed_to_weaviate():
    """filter=F1:V1,F2:V2 → vs.search() called with filters not None (AND logic)."""
    from config.settings import settings as app_settings
    meta = app_settings.vector_store.metadata_fields
    if len(meta) < 2:
        pytest.skip("Need at least 2 metadata_fields for this test")
    f1, f2 = meta[0], meta[1]
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    TestClient(app).get(f"/search?q=foo&filter={f1}:V1,{f2}:V2")
    assert vs.search.call_args.kwargs.get("filters") is not None


def test_search_filter_colon_in_value_is_200():
    """filter=Campo:valore:con:colonne → 200, split on first colon only (D-02)."""
    from config.settings import settings as app_settings
    valid_meta = app_settings.vector_store.metadata_fields[0]
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get(f"/search?q=foo&filter={valid_meta}:val:with:colons")
    assert resp.status_code == 200


def test_search_filter_missing_colon_returns_422():
    """filter=senza_colon → 422 (D-03)."""
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get("/search?q=foo&filter=senza_colon")
    assert resp.status_code == 422
    assert "Invalid filter format" in resp.json()["detail"]


def test_search_filter_empty_key_returns_422():
    """filter=:Valore → 422 (D-03)."""
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get("/search?q=foo&filter=:Valore")
    assert resp.status_code == 422
    assert "Invalid filter format" in resp.json()["detail"]


def test_search_filter_empty_value_returns_422():
    """filter=Campo: → 422 (D-03)."""
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get("/search?q=foo&filter=Campo:")
    assert resp.status_code == 422
    assert "Invalid filter format" in resp.json()["detail"]


def test_search_filter_unknown_field_returns_422():
    """filter=CampoNonEsistente:foo → 422 with field name in detail (D-05)."""
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get("/search?q=foo&filter=__campo_che_non_esiste__:foo")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "is not in metadata_fields" in detail
    assert "__campo_che_non_esiste__" in detail


def test_search_filter_text_field_not_filterable():
    """filter on text_fields (not metadata_fields) → 422 (D-06)."""
    from config.settings import settings as app_settings
    text_only = [f for f in app_settings.vector_store.text_fields
                 if f not in app_settings.vector_store.metadata_fields]
    if not text_only:
        pytest.skip("No text-only fields available in this config")
    app = _make_app()
    app.state.vector_store = _make_mock_vs()
    resp = TestClient(app).get(f"/search?q=foo&filter={text_only[0]}:foo")
    assert resp.status_code == 422
    assert "is not in metadata_fields" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# took_ms in search response (D-10)
# ---------------------------------------------------------------------------

def test_search_includes_took_ms():
    """GET /search → response body has 'took_ms' as a non-negative integer (D-10)."""
    vs = _make_mock_vs(_make_hits(({"title": "Movie"}, 0.8)))
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=hello")
    assert resp.status_code == 200
    body = resp.json()
    assert "took_ms" in body, f"took_ms missing from response: {body.keys()}"
    assert isinstance(body["took_ms"], int)
    assert body["took_ms"] >= 0


def test_search_response_shape_with_took_ms():
    """GET /search response top-level keys include query, took_ms, results (D-10)."""
    vs = _make_mock_vs()
    app = _make_app()
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=hello")
    body = resp.json()
    assert set(body.keys()) >= {"query", "took_ms", "results"}


def test_search_hybrid_with_embedding_includes_took_ms():
    """Ollama code path (with vector) also includes took_ms (D-10)."""
    adapter = MagicMock()
    adapter.embed.return_value = [[0.1] * 10]
    vs = _make_mock_vs()
    app = _make_app()
    app.state.embedding_adapter = adapter
    app.state.vector_store = vs
    resp = TestClient(app).get("/search?q=hello")
    assert resp.status_code == 200
    body = resp.json()
    assert "took_ms" in body
    assert isinstance(body["took_ms"], int)
    assert body["took_ms"] >= 0
