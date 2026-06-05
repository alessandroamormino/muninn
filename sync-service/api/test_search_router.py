"""Test del router search — GET /search.

Verifica:
- Happy path: 200 con shape {query, took_ms, results:[{props..., _score}]}
- Default limit (settings.api.default_limit) quando ?limit assente (D-05)
- Custom limit valido viene passato a hybrid()
- Limit < 1 → 422 (D-05)
- Limit > max_limit → 422 (D-05)
- Query vuota → 422 (FastAPI Query min_length=1)
- Default fields (settings.api.output_fields) quando ?fields assente (D-04)
- Custom fields validi vengono passati a return_properties
- Field non in (text_fields ∪ metadata_fields) → 422 (D-03)
- Mix di field valido + invalido → 422 strict
- _score presente in ogni risultato
- hybrid() raise → 503 con detail 'Search backend unavailable'
- Filter parsing (D-01 through D-09)
- took_ms nella risposta (D-10)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.search import router


def _make_obj(properties: dict, score: float):
    """Build a mock Weaviate hybrid result object."""
    obj = MagicMock()
    obj.properties = properties
    obj.metadata.score = score
    return obj


def _make_results(objects):
    """Build a mock hybrid() return value with .objects attribute."""
    res = MagicMock()
    res.objects = objects
    return res


def _make_app():
    app = FastAPI()
    app.include_router(router)
    return app


def _fake_client_factory(hybrid_return=None, hybrid_side_effect=None):
    """Return a mock get_client() for the no-embedding-adapter path (hybrid, no vector)."""
    client = MagicMock()
    collection = MagicMock()
    if hybrid_side_effect is not None:
        collection.query.hybrid.side_effect = hybrid_side_effect
    else:
        collection.query.hybrid.return_value = (
            hybrid_return
            if hybrid_return is not None
            else _make_results([_make_obj({"id": "1", "name": "Foo"}, 0.85)])
        )
    client.collections.get.return_value = collection
    return client, collection


def _fake_client_factory_with_embedding(hybrid_return=None, hybrid_side_effect=None):
    """Return a mock get_client() + embedding_adapter for the Ollama (with vector) code path."""
    client = MagicMock()
    collection = MagicMock()
    if hybrid_side_effect is not None:
        collection.query.hybrid.side_effect = hybrid_side_effect
    else:
        collection.query.hybrid.return_value = (
            hybrid_return
            if hybrid_return is not None
            else _make_results([_make_obj({"id": "1", "name": "Foo"}, 0.85)])
        )
    client.collections.get.return_value = collection
    embedding_adapter = MagicMock()
    embedding_adapter.embed.return_value = [[0.1] * 10]
    return client, collection, embedding_adapter


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_search_returns_query_and_results_shape():
    """GET /search?q=hello → 200 with body {query, took_ms, results}."""
    client, _ = _fake_client_factory(
        hybrid_return=_make_results([
            _make_obj({"id": "1", "name": "Foo", "price": 10.0}, 0.9),
            _make_obj({"id": "2", "name": "Bar", "price": 20.0}, 0.7),
        ])
    )
    with patch("api.search.get_client", return_value=client):
        resp = TestClient(_make_app()).get("/search?q=hello")
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "hello"
    assert isinstance(body["results"], list)
    assert len(body["results"]) == 2


def test_search_includes_score_in_each_result():
    """Each result has _score from obj.metadata.score."""
    client, _ = _fake_client_factory(
        hybrid_return=_make_results([_make_obj({"id": "1"}, 0.75)])
    )
    with patch("api.search.get_client", return_value=client):
        resp = TestClient(_make_app()).get("/search?q=anything")
    body = resp.json()
    assert body["results"][0]["_score"] == pytest.approx(0.75)
    assert body["results"][0]["id"] == "1"


# ---------------------------------------------------------------------------
# Limit validation (D-05)
# ---------------------------------------------------------------------------

def test_search_uses_default_limit_when_absent():
    """?limit absent → hybrid() called with settings.api.default_limit."""
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get("/search?q=foo")
    _, kwargs = collection.query.hybrid.call_args
    assert kwargs["limit"] == 10


def test_search_passes_custom_limit():
    """?limit=5 → hybrid() called with limit=5."""
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get("/search?q=foo&limit=5")
    _, kwargs = collection.query.hybrid.call_args
    assert kwargs["limit"] == 5


def test_search_rejects_limit_zero():
    """?limit=0 → 422."""
    resp = TestClient(_make_app()).get("/search?q=foo&limit=0")
    assert resp.status_code == 422
    detail_str = str(resp.json()["detail"]).lower()
    assert "limit" in detail_str or "1" in detail_str


def test_search_rejects_limit_above_max():
    """?limit=101 → 422 (max_limit=100 in config.yaml)."""
    resp = TestClient(_make_app()).get("/search?q=foo&limit=101")
    assert resp.status_code == 422


def test_search_rejects_empty_query():
    """?q= → 422 (FastAPI Query min_length=1)."""
    resp = TestClient(_make_app()).get("/search?q=")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Field projection (D-03, D-04)
# ---------------------------------------------------------------------------

def test_search_uses_default_output_fields_when_fields_absent():
    """?fields absent → return_properties == settings.api.output_fields (D-04)."""
    from config.settings import settings as app_settings
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get("/search?q=foo")
    _, kwargs = collection.query.hybrid.call_args
    assert kwargs["return_properties"] == list(app_settings.api.output_fields)


def test_search_passes_custom_valid_fields():
    """?fields=<f1,f2> → return_properties=[f1, f2] (D-03)."""
    from config.settings import settings as app_settings
    allowed = sorted(
        set(app_settings.vector_store.text_fields) | set(app_settings.vector_store.metadata_fields)
    )
    if len(allowed) < 2:
        pytest.skip("Need at least 2 allowed fields for this test")
    f1, f2 = allowed[0], allowed[1]
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get(f"/search?q=foo&fields={f1},{f2}")
    _, kwargs = collection.query.hybrid.call_args
    assert set(kwargs["return_properties"]) == {f1, f2}


def test_search_rejects_unknown_field():
    """?fields=bogus → 422 with detail mentioning invalid field (D-03)."""
    resp = TestClient(_make_app()).get("/search?q=foo&fields=bogus")
    assert resp.status_code == 422
    detail = str(resp.json()["detail"]).lower()
    assert "bogus" in detail or "invalid" in detail


def test_search_rejects_partial_invalid_fields():
    """?fields=<valid>,bogus → 422 (strict — do not silently drop)."""
    from config.settings import settings as app_settings
    valid = list(app_settings.vector_store.text_fields)[0]
    resp = TestClient(_make_app()).get(f"/search?q=foo&fields={valid},bogus")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_search_returns_503_when_weaviate_fails():
    """hybrid() raises → 503 with detail 'Search backend unavailable'."""
    client, _ = _fake_client_factory(hybrid_side_effect=RuntimeError("connection lost"))
    with patch("api.search.get_client", return_value=client):
        resp = TestClient(_make_app()).get("/search?q=foo")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Search backend unavailable"


def test_search_returns_503_when_get_client_fails():
    """get_client() raising RuntimeError → 503."""
    with patch("api.search.get_client", side_effect=RuntimeError("not open")):
        resp = TestClient(_make_app()).get("/search?q=foo")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Search backend unavailable"


# ---------------------------------------------------------------------------
# Hybrid search behaviour
# ---------------------------------------------------------------------------

def test_search_hybrid_called_with_alpha():
    """hybrid() is called with alpha=0.5 (balanced BM25 + semantic)."""
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get("/search?q=foo")
    _, kwargs = collection.query.hybrid.call_args
    assert kwargs.get("alpha") == 0.5


def test_search_hybrid_with_embedding_passes_vector():
    """With embedding_adapter, hybrid() receives vector= kwarg."""
    client, collection, adapter = _fake_client_factory_with_embedding()
    app = _make_app()
    app.state.embedding_adapter = adapter
    with patch("api.search.get_client", return_value=client):
        TestClient(app).get("/search?q=hello")
    _, kwargs = collection.query.hybrid.call_args
    assert kwargs.get("vector") is not None
    adapter.embed.assert_called_once_with(["hello"])


def test_search_hybrid_without_embedding_has_no_vector():
    """Without embedding_adapter, hybrid() is called without vector= kwarg."""
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get("/search?q=hello")
    _, kwargs = collection.query.hybrid.call_args
    assert "vector" not in kwargs


def test_search_hybrid_returns_503_on_failure():
    """hybrid() raises with embedding_adapter → 503."""
    client, _, adapter = _fake_client_factory_with_embedding(
        hybrid_side_effect=RuntimeError("gpu error")
    )
    app = _make_app()
    app.state.embedding_adapter = adapter
    with patch("api.search.get_client", return_value=client):
        resp = TestClient(app).get("/search?q=foo")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Search backend unavailable"


# ---------------------------------------------------------------------------
# Filter param (D-01 through D-09)
# ---------------------------------------------------------------------------

def test_search_no_filter_passes_none_filters():
    """filter absent → hybrid() receives filters=None."""
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get("/search?q=foo")
    _, kwargs = collection.query.hybrid.call_args
    assert kwargs.get("filters") is None


def test_search_single_filter_passed_to_weaviate():
    """filter=ValidField:SomeValue → hybrid() called with filters not None."""
    from config.settings import settings as app_settings
    valid_meta = app_settings.vector_store.metadata_fields[0]
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get(f"/search?q=foo&filter={valid_meta}:SomeValue")
    _, kwargs = collection.query.hybrid.call_args
    assert kwargs.get("filters") is not None


def test_search_multi_filter_passed_to_weaviate():
    """filter=F1:V1,F2:V2 → hybrid() called with filters not None (AND logic)."""
    from config.settings import settings as app_settings
    meta = app_settings.vector_store.metadata_fields
    if len(meta) < 2:
        pytest.skip("Need at least 2 metadata_fields for this test")
    f1, f2 = meta[0], meta[1]
    client, collection = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        TestClient(_make_app()).get(f"/search?q=foo&filter={f1}:V1,{f2}:V2")
    _, kwargs = collection.query.hybrid.call_args
    assert kwargs.get("filters") is not None


def test_search_filter_colon_in_value_is_200():
    """filter=Campo:valore:con:colonne → 200, split on first colon only (D-02)."""
    from config.settings import settings as app_settings
    valid_meta = app_settings.vector_store.metadata_fields[0]
    client, _ = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        resp = TestClient(_make_app()).get(f"/search?q=foo&filter={valid_meta}:val:with:colons")
    assert resp.status_code == 200


def test_search_filter_missing_colon_returns_422():
    """filter=senza_colon → 422 (D-03)."""
    resp = TestClient(_make_app()).get("/search?q=foo&filter=senza_colon")
    assert resp.status_code == 422
    assert "Invalid filter format" in resp.json()["detail"]


def test_search_filter_empty_key_returns_422():
    """filter=:Valore → 422 (D-03)."""
    resp = TestClient(_make_app()).get("/search?q=foo&filter=:Valore")
    assert resp.status_code == 422
    assert "Invalid filter format" in resp.json()["detail"]


def test_search_filter_empty_value_returns_422():
    """filter=Campo: → 422 (D-03)."""
    resp = TestClient(_make_app()).get("/search?q=foo&filter=Campo:")
    assert resp.status_code == 422
    assert "Invalid filter format" in resp.json()["detail"]


def test_search_filter_unknown_field_returns_422():
    """filter=CampoNonEsistente:foo → 422 with field name in detail (D-05)."""
    resp = TestClient(_make_app()).get("/search?q=foo&filter=__campo_che_non_esiste__:foo")
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
    resp = TestClient(_make_app()).get(f"/search?q=foo&filter={text_only[0]}:foo")
    assert resp.status_code == 422
    assert "is not in metadata_fields" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# took_ms in search response (D-10)
# ---------------------------------------------------------------------------

def test_search_includes_took_ms():
    """GET /search → response body has 'took_ms' as a non-negative integer (D-10)."""
    client, _ = _fake_client_factory(
        hybrid_return=_make_results([_make_obj({"id": "1"}, 0.8)])
    )
    with patch("api.search.get_client", return_value=client):
        resp = TestClient(_make_app()).get("/search?q=hello")
    assert resp.status_code == 200
    body = resp.json()
    assert "took_ms" in body, f"took_ms missing from response: {body.keys()}"
    assert isinstance(body["took_ms"], int)
    assert body["took_ms"] >= 0


def test_search_response_shape_with_took_ms():
    """GET /search response top-level keys include query, took_ms, results (D-10)."""
    client, _ = _fake_client_factory()
    with patch("api.search.get_client", return_value=client):
        resp = TestClient(_make_app()).get("/search?q=hello")
    body = resp.json()
    assert set(body.keys()) >= {"query", "took_ms", "results"}


def test_search_hybrid_with_embedding_includes_took_ms():
    """Ollama code path (with vector) also includes took_ms (D-10)."""
    client, _, adapter = _fake_client_factory_with_embedding()
    app = _make_app()
    app.state.embedding_adapter = adapter
    with patch("api.search.get_client", return_value=client):
        resp = TestClient(app).get("/search?q=hello")
    assert resp.status_code == 200
    body = resp.json()
    assert "took_ms" in body
    assert isinstance(body["took_ms"], int)
    assert body["took_ms"] >= 0
