"""Unit tests for RestAPIAdapter (Phase 8, SRC-07)."""
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
from sources.json_adapter import AdapterError


def _make_response(data, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = status < 400
    resp.json.return_value = data
    if status >= 400:
        import requests as _r
        resp.raise_for_status.side_effect = _r.exceptions.HTTPError(f"{status}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _cfgs(**source_overrides):
    defaults = dict(
        type="rest_api",
        url="https://api.example.com/items",
        id_field="id",
        json_key="results",
    )
    defaults.update(source_overrides)
    src = SourceConfig(**defaults)
    syn = SyncConfig(hash_fields=["id", "name"])
    wea = WeaviateConfig()
    return src, syn, wea


class TestAdapterInterface:
    def test_base_adapter_interface_satisfied(self):
        src, syn, wea = _cfgs()
        adapter = RestAPIAdapter(src, syn, wea)
        # Must implement all 4 abstract methods
        assert hasattr(adapter, "fetch_records")
        assert hasattr(adapter, "fetch_new_records")
        assert hasattr(adapter, "get_record_id")
        assert hasattr(adapter, "get_record_hash")

    def test_constructor_requires_url(self):
        from config.settings import SourceConfig
        with pytest.raises(ValueError, match="source.url"):
            RestAPIAdapter(
                SourceConfig(type="rest_api", url=None),
                SyncConfig(),
                WeaviateConfig(),
            )


class TestPaginationNone:
    def test_no_pagination_single_request(self):
        src, syn, wea = _cfgs()
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response(
                {"results": [{"id": "1"}, {"id": "2"}]}
            )
            records = RestAPIAdapter(src, syn, wea).fetch_records()
        assert len(records) == 2
        assert mock_get.call_count == 1


class TestPaginationCursor:
    def test_cursor_pagination_two_pages(self):
        src, syn, wea = _cfgs(
            pagination=PaginationConfig(type="cursor", next_key="next"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(
                    {"results": [{"id": "1"}], "next": "https://api.example.com/items?cursor=abc"}
                ),
                _make_response({"results": [{"id": "2"}], "next": None}),
            ]
            records = RestAPIAdapter(src, syn, wea).fetch_records()
        assert len(records) == 2
        assert mock_get.call_count == 2
        # Verify second call used the cursor URL verbatim
        second_call_url = mock_get.call_args_list[1][0][0]
        assert second_call_url == "https://api.example.com/items?cursor=abc"

    def test_cursor_pagination_stops_on_null_next(self):
        src, syn, wea = _cfgs(
            pagination=PaginationConfig(type="cursor", next_key="next"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response(
                {"results": [{"id": "1"}], "next": None}
            )
            records = RestAPIAdapter(src, syn, wea).fetch_records()
        assert len(records) == 1
        assert mock_get.call_count == 1

    def test_cursor_pagination_does_not_reapply_static_params(self):
        src, syn, wea = _cfgs(
            params={"limit": 100},
            pagination=PaginationConfig(type="cursor", next_key="next"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(
                    {"results": [{"id": "1"}], "next": "https://api.example.com/items?cursor=abc&limit=100"}
                ),
                _make_response({"results": [{"id": "2"}], "next": None}),
            ]
            RestAPIAdapter(src, syn, wea).fetch_records()
        # First call: static params applied
        first_params = mock_get.call_args_list[0].kwargs.get("params", {})
        assert first_params == {"limit": 100}
        # Second call: cursor mode — params dict should be empty
        second_params = mock_get.call_args_list[1].kwargs.get("params", {})
        assert second_params == {}


class TestPaginationPage:
    def test_page_pagination_respects_total_pages(self):
        src, syn, wea = _cfgs(
            pagination=PaginationConfig(
                type="page", page_param="page", total_pages_key="total_pages", start_page=1
            ),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response({"results": [{"id": "1"}], "page": 1, "total_pages": 2}),
                _make_response({"results": [{"id": "2"}], "page": 2, "total_pages": 2}),
            ]
            records = RestAPIAdapter(src, syn, wea).fetch_records()
        assert len(records) == 2
        assert mock_get.call_count == 2
        # Verify page param incremented
        first_params = mock_get.call_args_list[0].kwargs.get("params", {})
        second_params = mock_get.call_args_list[1].kwargs.get("params", {})
        assert first_params["page"] == 1
        assert second_params["page"] == 2

    def test_page_pagination_stops_on_empty_results(self):
        src, syn, wea = _cfgs(
            pagination=PaginationConfig(type="page", total_pages_key="total_pages"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response({"results": [{"id": "1"}], "total_pages": 999}),
                _make_response({"results": [], "total_pages": 999}),
            ]
            records = RestAPIAdapter(src, syn, wea).fetch_records()
        assert len(records) == 1
        assert mock_get.call_count == 2


class TestPaginationOffset:
    def test_offset_pagination_stops_on_empty(self):
        src, syn, wea = _cfgs(
            pagination=PaginationConfig(
                type="offset", offset_param="offset", limit_param="limit", page_size=2
            ),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response({"results": [{"id": "1"}, {"id": "2"}]}),
                _make_response({"results": [{"id": "3"}]}),
                _make_response({"results": []}),
            ]
            records = RestAPIAdapter(src, syn, wea).fetch_records()
        assert len(records) == 3
        assert mock_get.call_count == 3
        offsets = [
            c.kwargs.get("params", {}).get("offset") for c in mock_get.call_args_list
        ]
        assert offsets == [0, 2, 4]


class TestAuthStrategies:
    def test_auth_bearer_sets_header(self, monkeypatch):
        monkeypatch.setenv("TEST_TOKEN", "secret-xyz")
        src, syn, wea = _cfgs(
            auth=AuthConfig(type="bearer", token="${TEST_TOKEN}"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer secret-xyz"

    def test_auth_api_key_header(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "key-abc")
        src, syn, wea = _cfgs(
            auth=AuthConfig(
                type="api_key_header", header_name="X-Custom-Key", key="${TEST_KEY}"
            ),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers.get("X-Custom-Key") == "key-abc"

    def test_auth_api_key_header_default_header_name(self):
        src, syn, wea = _cfgs(
            auth=AuthConfig(type="api_key_header", key="raw-key"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers.get("X-Api-Key") == "raw-key"

    def test_auth_api_key_param(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "key-abc")
        src, syn, wea = _cfgs(
            auth=AuthConfig(
                type="api_key_param", param_name="api_key", key="${TEST_KEY}"
            ),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("api_key") == "key-abc"

    def test_auth_basic(self):
        src, syn, wea = _cfgs(
            auth=AuthConfig(type="basic", username="alice", password="hunter2"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        headers = mock_get.call_args.kwargs.get("headers", {})
        # base64("alice:hunter2") == "YWxpY2U6aHVudGVyMg=="
        assert headers.get("Authorization") == "Basic YWxpY2U6aHVudGVyMg=="

    def test_auth_none_sends_no_authorization(self):
        src, syn, wea = _cfgs()
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert "Authorization" not in headers


class TestStaticParams:
    def test_static_params_sent_every_request(self):
        src, syn, wea = _cfgs(
            params={"format": "json", "per_page": 100},
            pagination=PaginationConfig(type="page", total_pages_key="total_pages"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response({"results": [{"id": "1"}], "total_pages": 2}),
                _make_response({"results": [{"id": "2"}], "total_pages": 2}),
            ]
            RestAPIAdapter(src, syn, wea).fetch_records()
        for call in mock_get.call_args_list:
            params = call.kwargs.get("params", {})
            assert params.get("format") == "json"
            assert params.get("per_page") == 100


class TestEnvResolution:
    def test_env_var_resolution_in_token(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "env-resolved")
        src, syn, wea = _cfgs(
            auth=AuthConfig(type="bearer", token="${MY_TOKEN}"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers["Authorization"] == "Bearer env-resolved"

    def test_env_var_missing_resolves_to_empty(self, monkeypatch):
        monkeypatch.delenv("MISSING_TOKEN", raising=False)
        src, syn, wea = _cfgs(
            auth=AuthConfig(type="bearer", token="${MISSING_TOKEN}"),
        )
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers["Authorization"] == "Bearer "


class TestErrors:
    def test_timeout_raises_adapter_error(self):
        import requests as _r
        src, syn, wea = _cfgs()
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.side_effect = _r.exceptions.Timeout()
            with pytest.raises(AdapterError, match="timed out"):
                RestAPIAdapter(src, syn, wea).fetch_records()

    def test_http_error_raises_adapter_error(self):
        src, syn, wea = _cfgs()
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({}, status=500)
            with pytest.raises(AdapterError):
                RestAPIAdapter(src, syn, wea).fetch_records()

    def test_missing_id_field_raises_value_error(self):
        src, syn, wea = _cfgs(id_field="custom_id")
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response(
                {"results": [{"name": "no_id_field"}]}
            )
            with pytest.raises(ValueError, match="custom_id"):
                RestAPIAdapter(src, syn, wea).fetch_records()

    def test_records_with_empty_id_field_are_skipped(self, caplog):
        src, syn, wea = _cfgs()
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response(
                {"results": [{"id": "1"}, {"id": ""}, {"id": "3"}]}
            )
            records = RestAPIAdapter(src, syn, wea).fetch_records()
        assert [r["id"] for r in records] == ["1", "3"]


class TestRecordIdAndHash:
    def test_get_record_id(self):
        src, syn, wea = _cfgs()
        adapter = RestAPIAdapter(src, syn, wea)
        assert adapter.get_record_id({"id": 42, "name": "x"}) == "42"

    def test_get_record_hash_stable(self):
        src, syn, wea = _cfgs()
        adapter = RestAPIAdapter(src, syn, wea)
        h1 = adapter.get_record_hash({"id": "1", "name": "Alice"})
        h2 = adapter.get_record_hash({"id": "1", "name": "Alice"})
        h3 = adapter.get_record_hash({"id": "1", "name": "Bob"})
        assert h1 == h2
        assert h1 != h3


class TestFactoryDispatch:
    def test_factory_returns_rest_api_adapter(self):
        from sources import build_source_adapter, RestAPIAdapter as RA
        src, syn, wea = _cfgs()
        adapter = build_source_adapter(src, syn, wea)
        assert isinstance(adapter, RA)

    def test_factory_exports_rest_api_adapter(self):
        from sources import RestAPIAdapter as RA
        assert RA is RestAPIAdapter


class TestPostMethod:
    def test_post_method_used_when_configured(self):
        src, syn, wea = _cfgs(method="POST")
        with patch("sources.rest_api_adapter.requests.post") as mock_post, \
             patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_post.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        assert mock_post.call_count == 1
        assert mock_get.call_count == 0


class TestMaxPagesSafetyCap:
    def test_offset_pagination_stops_at_max_pages(self):
        src, syn, wea = _cfgs(
            pagination=PaginationConfig(
                type="offset", page_size=1, max_pages=3
            ),
        )
        # Infinite stream of non-empty results — only max_pages safety stops it
        with patch("sources.rest_api_adapter.requests.get") as mock_get:
            mock_get.return_value = _make_response({"results": [{"id": "1"}]})
            RestAPIAdapter(src, syn, wea).fetch_records()
        # Exactly max_pages requests, no more
        assert mock_get.call_count == 3
