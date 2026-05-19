"""Integration-style tests for RestAPIAdapter (Phase 8, SRC-07).

Most tests use mocked HTTP responses with realistic API shapes (PokéAPI, TMDB).
One test (`test_pokeapi_cursor_pagination_live`) makes a real HTTPS call to
PokéAPI but is gated behind the env var SMART_SEARCH_RUN_LIVE_TESTS=1 to keep
the default test suite hermetic. To run it locally:

    SMART_SEARCH_RUN_LIVE_TESTS=1 python -m pytest sources/test_rest_api_integration.py -x -q
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from config.settings import (
    AuthConfig,
    PaginationConfig,
    SourceConfig,
    SyncConfig,
    WeaviateConfig,
)
from sources.rest_api_adapter import RestAPIAdapter


LIVE_TESTS_ENABLED = os.getenv("SMART_SEARCH_RUN_LIVE_TESTS") == "1"


def _make_response(data, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = status < 400
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


# ----------------------------- PokéAPI shape -----------------------------

class TestPokeapiShape:
    """PokéAPI: no auth, cursor pagination via `next` URL, records under `results` key."""

    def test_pokeapi_cursor_pagination_mocked(self):
        """Verify cursor pagination against PokéAPI-shaped mocked responses."""
        src = SourceConfig(
            type="rest_api",
            url="https://pokeapi.co/api/v2/pokemon",
            id_field="name",
            json_key="results",
            params={"limit": 20},
            pagination=PaginationConfig(type="cursor", next_key="next"),
        )
        syn = SyncConfig(hash_fields=["name", "url"])
        wea = WeaviateConfig()

        page1 = {
            "count": 1302,
            "next": "https://pokeapi.co/api/v2/pokemon?offset=20&limit=20",
            "previous": None,
            "results": [
                {"name": f"poke-{i}", "url": f"https://pokeapi.co/api/v2/pokemon/{i}/"}
                for i in range(1, 21)
            ],
        }
        page2 = {
            "count": 1302,
            "next": None,  # terminate after page 2 for the mocked test
            "previous": "https://pokeapi.co/api/v2/pokemon?offset=0&limit=20",
            "results": [
                {"name": f"poke-{i}", "url": f"https://pokeapi.co/api/v2/pokemon/{i}/"}
                for i in range(21, 41)
            ],
        }

        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [_make_response(page1), _make_response(page2)]
            records = RestAPIAdapter(src, syn, wea).fetch_records()

        assert len(records) == 40
        assert records[0]["name"] == "poke-1"
        assert records[-1]["name"] == "poke-40"
        assert mock_get.call_count == 2
        # First call: static params applied (limit=20)
        assert mock_get.call_args_list[0].kwargs["params"] == {"limit": 20}
        # Second call (cursor hop): use next URL verbatim, no static params
        assert mock_get.call_args_list[1].args[0] == (
            "https://pokeapi.co/api/v2/pokemon?offset=20&limit=20"
        )
        assert mock_get.call_args_list[1].kwargs["params"] == {}

    @pytest.mark.skipif(
        not LIVE_TESTS_ENABLED,
        reason="Live network test — set SMART_SEARCH_RUN_LIVE_TESTS=1 to enable",
    )
    def test_pokeapi_cursor_pagination_live(self):
        """Hit the real PokéAPI. Stops after 2 pages via max_pages safety cap."""
        src = SourceConfig(
            type="rest_api",
            url="https://pokeapi.co/api/v2/pokemon",
            id_field="name",
            json_key="results",
            params={"limit": 20},
            pagination=PaginationConfig(
                type="cursor", next_key="next", max_pages=2
            ),
        )
        syn = SyncConfig(hash_fields=["name", "url"])
        wea = WeaviateConfig()

        records = RestAPIAdapter(src, syn, wea).fetch_records()

        # 2 pages * 20 records = 40 records expected
        assert len(records) == 40
        # First record from PokéAPI is canonically "bulbasaur"
        assert records[0]["name"] == "bulbasaur"
        # All records have non-empty name and url
        for r in records:
            assert r.get("name")
            assert r.get("url", "").startswith("https://pokeapi.co/api/v2/pokemon/")


# ----------------------------- TMDB shape --------------------------------

class TestTmdbShape:
    """TMDB: bearer auth or api_key_param, page pagination via `page`/`total_pages`."""

    def test_tmdb_bearer_page_pagination_mocked(self, monkeypatch):
        monkeypatch.setenv("TMDB_API_KEY", "fake-tmdb-token")
        src = SourceConfig(
            type="rest_api",
            url="https://api.themoviedb.org/3/movie/popular",
            id_field="id",
            json_key="results",
            auth=AuthConfig(type="bearer", token="${TMDB_API_KEY}"),
            params={"language": "en-US"},
            pagination=PaginationConfig(
                type="page",
                page_param="page",
                total_pages_key="total_pages",
                start_page=1,
            ),
        )
        syn = SyncConfig(hash_fields=["id", "title"])
        wea = WeaviateConfig()

        page1 = {
            "page": 1,
            "total_pages": 2,
            "total_results": 4,
            "results": [
                {"id": 1, "title": "Movie A"},
                {"id": 2, "title": "Movie B"},
            ],
        }
        page2 = {
            "page": 2,
            "total_pages": 2,
            "total_results": 4,
            "results": [
                {"id": 3, "title": "Movie C"},
                {"id": 4, "title": "Movie D"},
            ],
        }

        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [_make_response(page1), _make_response(page2)]
            records = RestAPIAdapter(src, syn, wea).fetch_records()

        assert len(records) == 4
        assert [r["id"] for r in records] == [1, 2, 3, 4]
        assert mock_get.call_count == 2
        # Both calls must include Authorization: Bearer header with resolved env
        for call in mock_get.call_args_list:
            headers = call.kwargs["headers"]
            assert headers["Authorization"] == "Bearer fake-tmdb-token"
        # Page param must increment
        assert mock_get.call_args_list[0].kwargs["params"]["page"] == 1
        assert mock_get.call_args_list[1].kwargs["params"]["page"] == 2

    def test_tmdb_api_key_param_mocked(self, monkeypatch):
        monkeypatch.setenv("TMDB_API_KEY", "fake-tmdb-key")
        src = SourceConfig(
            type="rest_api",
            url="https://api.themoviedb.org/3/movie/popular",
            id_field="id",
            json_key="results",
            auth=AuthConfig(
                type="api_key_param", param_name="api_key", key="${TMDB_API_KEY}"
            ),
            pagination=PaginationConfig(
                type="page", total_pages_key="total_pages", start_page=1
            ),
        )
        syn = SyncConfig(hash_fields=["id"])
        wea = WeaviateConfig()

        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response(
                {
                    "page": 1,
                    "total_pages": 1,
                    "total_results": 1,
                    "results": [{"id": 42, "title": "Solo Movie"}],
                }
            )
            records = RestAPIAdapter(src, syn, wea).fetch_records()

        assert len(records) == 1
        # api_key resolved from env var and placed in query params
        params = mock_get.call_args.kwargs["params"]
        assert params["api_key"] == "fake-tmdb-key"
        # No Authorization header for api_key_param strategy
        headers = mock_get.call_args.kwargs["headers"]
        assert "Authorization" not in headers


# ------------------ Static params persistence (cross-API) -----------------

class TestStaticParamsPersistence:
    def test_static_params_added_to_every_page_request(self):
        """SC-6: fixed params persist across page-pagination requests."""
        src = SourceConfig(
            type="rest_api",
            url="https://api.example.com/items",
            id_field="id",
            json_key="results",
            params={"language": "en-US", "format": "json"},
            pagination=PaginationConfig(type="page", total_pages_key="total_pages"),
        )
        syn = SyncConfig(hash_fields=["id"])
        wea = WeaviateConfig()

        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(
                    {"results": [{"id": "1"}], "total_pages": 3, "page": 1}
                ),
                _make_response(
                    {"results": [{"id": "2"}], "total_pages": 3, "page": 2}
                ),
                _make_response(
                    {"results": [{"id": "3"}], "total_pages": 3, "page": 3}
                ),
            ]
            RestAPIAdapter(src, syn, wea).fetch_records()

        assert mock_get.call_count == 3
        for call in mock_get.call_args_list:
            params = call.kwargs["params"]
            assert params["language"] == "en-US"
            assert params["format"] == "json"
