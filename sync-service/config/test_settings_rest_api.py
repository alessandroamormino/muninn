"""Tests for AuthConfig, PaginationConfig, and extended SourceConfig (Phase 8, SRC-07)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import AuthConfig, PaginationConfig, SourceConfig


class TestAuthConfigDefaults:
    def test_auth_config_defaults(self):
        auth = AuthConfig()
        assert auth.type == "none"
        assert auth.token is None
        assert auth.header_name is None
        assert auth.key is None
        assert auth.param_name is None
        assert auth.username is None
        assert auth.password is None

    def test_auth_config_bearer_keeps_token_literal(self):
        auth = AuthConfig(type="bearer", token="${TMDB_API_KEY}")
        assert auth.token == "${TMDB_API_KEY}"

    def test_auth_config_api_key_header(self):
        auth = AuthConfig(type="api_key_header", header_name="X-Api-Key", key="abc")
        assert auth.type == "api_key_header"
        assert auth.header_name == "X-Api-Key"
        assert auth.key == "abc"

    def test_auth_config_api_key_param(self):
        auth = AuthConfig(type="api_key_param", param_name="api_key", key="xyz")
        assert auth.type == "api_key_param"
        assert auth.param_name == "api_key"

    def test_auth_config_basic(self):
        auth = AuthConfig(type="basic", username="alice", password="hunter2")
        assert auth.username == "alice"
        assert auth.password == "hunter2"


class TestPaginationConfigDefaults:
    def test_pagination_config_defaults(self):
        pag = PaginationConfig()
        assert pag.type == "none"
        assert pag.page_param == "page"
        assert pag.offset_param == "offset"
        assert pag.limit_param == "limit"
        assert pag.page_size == 100
        assert pag.start_page == 1
        assert pag.total_pages_key == "total_pages"
        assert pag.next_key is None

    def test_pagination_config_max_pages_default(self):
        assert PaginationConfig().max_pages == 10000


class TestSourceConfigRestAPI:
    def test_source_config_rest_api_defaults(self):
        src = SourceConfig(type="rest_api")
        assert src.auth.type == "none"
        assert src.pagination.type == "none"
        assert src.params == {}
        assert src.method == "GET"

    def test_source_config_ignores_extra_keys_in_auth(self):
        # Should not raise — extra="ignore" on AuthConfig
        src = SourceConfig(
            type="rest_api",
            auth={"type": "bearer", "token": "x", "unknown_field": "value"},
        )
        assert src.auth.type == "bearer"

    def test_source_config_csv_still_works(self):
        src = SourceConfig(type="csv", file_path="/tmp/x.csv")
        assert src.type == "csv"
        assert src.auth.type == "none"

    def test_source_config_method_literal_post(self):
        src = SourceConfig(type="rest_api", method="POST")
        assert src.method == "POST"

    def test_source_config_method_literal_invalid_raises(self):
        with pytest.raises(ValidationError):
            SourceConfig(type="rest_api", method="DELETE")

    def test_source_config_params_accepts_dict(self):
        src = SourceConfig(type="rest_api", params={"limit": 100, "format": "json"})
        assert src.params["limit"] == 100
        assert src.params["format"] == "json"

    def test_source_config_auth_header_preserved(self):
        # JSONAdapter backward compat — auth_header must still exist
        src = SourceConfig(type="json", auth_header="Bearer old-token")
        assert src.auth_header == "Bearer old-token"

    def test_source_config_pagination_cursor(self):
        src = SourceConfig(
            type="rest_api",
            pagination={"type": "cursor", "next_key": "next"},
        )
        assert src.pagination.type == "cursor"
        assert src.pagination.next_key == "next"
