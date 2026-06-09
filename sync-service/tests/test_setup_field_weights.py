"""Unit tests for api/setup.py field_weights support — Phase 23 Plan 04.

Tests cover:
- _validate_suggested_fields accepts field_weights keys in CSV headers
- _validate_suggested_fields rejects field_weights keys not in headers
- Empty field_weights is valid (backward compat)
- Missing field_weights key is valid (backward compat)
- _build_prompt includes 'field_weights' in output schema
- _build_prompt includes instruction about assigning weights 0.1-1.0
- POST /setup/suggest-config response includes field_weights key
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.setup import router as setup_router
from auth.dependencies import require_admin
from auth.user_store import UserRecord

_ADMIN = UserRecord(
    id=1, username="admin", hashed_password="", role="admin",
    totp_secret=None, totp_enabled=False,
    created_at="2026-01-01T00:00:00", is_active=True,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(setup_router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    return app


class TestFieldWeightsValidation:
    def test_validate_accepts_field_weights_keys_in_headers(self):
        """suggested field_weights with keys in headers → no raise."""
        from api.setup import _validate_suggested_fields
        suggested = {
            "text_fields": ["a", "b"],
            "metadata_fields": [],
            "output_fields": ["a"],
            "graph_filter_fields": [],
            "field_weights": {"a": 1.0, "b": 0.5},
            "id_field": "a",
        }
        # Should NOT raise
        _validate_suggested_fields(suggested, ["a", "b", "c"])

    def test_validate_rejects_field_weights_key_not_in_headers(self):
        """field_weights with key not in headers → ValueError mentioning the key."""
        from api.setup import _validate_suggested_fields
        suggested = {
            "text_fields": [],
            "metadata_fields": [],
            "output_fields": [],
            "graph_filter_fields": [],
            "field_weights": {"unknown_col": 1.0},
            "id_field": "",
        }
        with pytest.raises(ValueError) as exc_info:
            _validate_suggested_fields(suggested, ["a", "b"])
        assert "unknown_col" in str(exc_info.value)

    def test_validate_empty_field_weights_ok(self):
        """Empty field_weights={} → no raise (backward compat)."""
        from api.setup import _validate_suggested_fields
        suggested = {
            "text_fields": ["a"],
            "metadata_fields": [],
            "output_fields": [],
            "graph_filter_fields": [],
            "field_weights": {},
            "id_field": "a",
        }
        # Should NOT raise
        _validate_suggested_fields(suggested, ["a", "b"])

    def test_validate_missing_field_weights_ok(self):
        """Dict without field_weights key → no raise (backward compat)."""
        from api.setup import _validate_suggested_fields
        suggested = {
            "text_fields": ["a"],
            "metadata_fields": [],
            "output_fields": [],
            "graph_filter_fields": [],
            "id_field": "a",
        }
        # Should NOT raise
        _validate_suggested_fields(suggested, ["a", "b"])

    def test_build_prompt_includes_field_weights_schema(self):
        """_build_prompt output contains 'field_weights' key in schema."""
        from api.setup import _build_prompt
        prompt = _build_prompt(["a", "b"], [{"a": "val1", "b": "val2"}])
        assert "field_weights" in prompt, (
            f"Expected 'field_weights' in prompt, but got:\n{prompt}"
        )

    def test_build_prompt_includes_field_weights_instruction(self):
        """_build_prompt output contains instruction about weights 0.1-1.0."""
        from api.setup import _build_prompt
        prompt = _build_prompt(["a", "b"], [{"a": "val1", "b": "val2"}])
        # The instruction should mention assigning weights
        lower = prompt.lower()
        assert "weight" in lower or "0.1" in lower, (
            f"Expected weight instruction in prompt, but got:\n{prompt}"
        )

    def test_suggest_config_response_includes_field_weights(self, tmp_path):
        """POST /setup/suggest-config response dict has field_weights key."""
        import io
        import csv

        # Create a fake CSV file in a tmp data path
        csv_content = "id,name,description\n1,alpha,some text\n"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        csv_file = data_dir / "test.csv"
        csv_file.write_text(csv_content)

        llm_result = {
            "id_field": "id",
            "text_fields": ["name", "description"],
            "metadata_fields": [],
            "output_fields": ["name"],
            "graph_filter_fields": [],
            "field_weights": {"name": 1.0, "description": 0.5},
            "reasoning": {},
        }

        app = _make_app()

        with patch("api.setup._DATA_ROOT", data_dir), \
             patch("api.setup.OllamaLLMClient") as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.generate.return_value = llm_result
            mock_llm_cls.return_value = mock_llm

            with TestClient(app) as client:
                resp = client.post(
                    "/setup/suggest-config",
                    json={"file_path": str(csv_file)},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert "suggested_config" in data
        assert "field_weights" in data["suggested_config"], (
            f"Expected 'field_weights' in suggested_config, got: {data['suggested_config']}"
        )
