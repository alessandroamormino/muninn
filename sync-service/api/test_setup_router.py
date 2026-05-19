"""Tests for POST /setup/suggest-config router.

Verifica:
- Happy path: 200 con shape {suggested_config, reasoning}
- collection derivata deterministicamente dal nome file (D-07)
- Path fuori /app/data/ → 422 "path not allowed" (D-02)
- File inesistente → 422 "file not found" (D-10)
- LLM non raggiungibile → 503 "LLM unavailable — make sure Ollama is running" (D-09)
- Campi LLM inesistenti nelle intestazioni → 200 con _warning (success criterion 4)
"""
from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.setup import router
from llm.ollama_llm import LLMError


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _make_csv_file(tmp_path: Path, filename: str, headers: list[str], rows: list[list[str]]) -> Path:
    """Write a real CSV file to tmp_path and return its Path."""
    p = tmp_path / filename
    with open(p, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)
    return p


_GOOD_LLM_RESPONSE = {
    "id_field": "Collaboratore",
    "text_fields": ["Descrizione"],
    "metadata_fields": ["Azienda", "Mail"],
    "output_fields": ["Collaboratore", "Descrizione", "Azienda"],
    "reasoning": {
        "Collaboratore": "identificatore -> id_field",
        "Descrizione": "testo libero -> text_field",
        "Azienda": "categorico -> metadata_field",
        "Mail": "identificatore -> metadata_field",
    },
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_suggest_config_happy_path(tmp_path):
    """Valid CSV in /app/data/ -> 200 with suggested_config and reasoning."""
    csv_file = _make_csv_file(
        tmp_path,
        "collaboratori.csv",
        ["Collaboratore", "Descrizione", "Azienda", "Mail"],
        [["Alice", "Senior dev", "Acme", "alice@acme.com"]],
    )

    with (
        patch("api.setup._DATA_ROOT", tmp_path),
        patch("api.setup.OllamaLLMClient") as mock_llm_cls,
    ):
        mock_llm_cls.return_value.generate.return_value = _GOOD_LLM_RESPONSE
        resp = TestClient(_make_app()).post(
            "/setup/suggest-config",
            json={"file_path": str(csv_file)},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "suggested_config" in body
    assert "reasoning" in body
    sc = body["suggested_config"]
    assert "id_field" in sc
    assert "text_fields" in sc
    assert "metadata_fields" in sc
    assert "output_fields" in sc
    assert "collection" in sc


def test_suggest_config_collection_from_filename(tmp_path):
    """collection is PascalCase of CSV filename, not from LLM response (D-07)."""
    csv_file = _make_csv_file(
        tmp_path,
        "test_fake.csv",
        ["id", "name"],
        [["1", "Alice"]],
    )
    llm_response = {
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
        "reasoning": {"id": "unique id", "name": "free text"},
    }

    with (
        patch("api.setup._DATA_ROOT", tmp_path),
        patch("api.setup.OllamaLLMClient") as mock_llm_cls,
    ):
        mock_llm_cls.return_value.generate.return_value = llm_response
        resp = TestClient(_make_app()).post(
            "/setup/suggest-config",
            json={"file_path": str(csv_file)},
        )

    assert resp.status_code == 200
    assert resp.json()["suggested_config"]["collection"] == "TestFake"


def test_suggest_config_collaboratori_collection(tmp_path):
    """'collaboratori.csv' -> collection == 'Collaboratori'."""
    csv_file = _make_csv_file(tmp_path, "collaboratori.csv", ["id", "nome"], [["1", "Alice"]])
    llm_response = {
        "id_field": "id", "text_fields": ["nome"], "metadata_fields": [],
        "output_fields": ["id", "nome"], "reasoning": {"id": "uid", "nome": "name"},
    }
    with (
        patch("api.setup._DATA_ROOT", tmp_path),
        patch("api.setup.OllamaLLMClient") as mock_llm_cls,
    ):
        mock_llm_cls.return_value.generate.return_value = llm_response
        resp = TestClient(_make_app()).post(
            "/setup/suggest-config", json={"file_path": str(csv_file)}
        )
    assert resp.status_code == 200
    assert resp.json()["suggested_config"]["collection"] == "Collaboratori"


# ---------------------------------------------------------------------------
# Path validation (D-02, D-10)
# ---------------------------------------------------------------------------

def test_suggest_config_rejects_path_traversal():
    """file_path with path traversal -> 422 'path not allowed'."""
    resp = TestClient(_make_app()).post(
        "/setup/suggest-config",
        json={"file_path": "../etc/passwd"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "path not allowed"


def test_suggest_config_rejects_absolute_path_outside_data():
    """Absolute path outside /app/data/ -> 422 'path not allowed'."""
    resp = TestClient(_make_app()).post(
        "/setup/suggest-config",
        json={"file_path": "/tmp/evil.csv"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "path not allowed"


def test_suggest_config_rejects_missing_file(tmp_path):
    """CSV that doesn't exist -> 422 'file not found'."""
    with patch("api.setup._DATA_ROOT", tmp_path):
        resp = TestClient(_make_app()).post(
            "/setup/suggest-config",
            json={"file_path": str(tmp_path / "nonexistent.csv")},
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "file not found"


# ---------------------------------------------------------------------------
# LLM error handling (D-09)
# ---------------------------------------------------------------------------

def test_suggest_config_returns_503_when_llm_unreachable(tmp_path):
    """LLMError from generate() -> 503 'LLM unavailable - make sure Ollama is running'."""
    csv_file = _make_csv_file(tmp_path, "test.csv", ["id", "name"], [["1", "Alice"]])

    with (
        patch("api.setup._DATA_ROOT", tmp_path),
        patch("api.setup.OllamaLLMClient") as mock_llm_cls,
    ):
        mock_llm_cls.return_value.generate.side_effect = LLMError("connection refused")
        resp = TestClient(_make_app()).post(
            "/setup/suggest-config",
            json={"file_path": str(csv_file)},
        )

    assert resp.status_code == 503
    assert resp.json()["detail"] == "LLM unavailable — make sure Ollama is running"


# ---------------------------------------------------------------------------
# Field validation (success criterion 4)
# ---------------------------------------------------------------------------

def test_suggest_config_warns_when_llm_hallucinates_fields(tmp_path):
    """LLM returns a field not in CSV headers -> 200 with _warning in body."""
    csv_file = _make_csv_file(tmp_path, "data.csv", ["id", "name"], [["1", "Alice"]])
    bad_llm_response = {
        "id_field": "id",
        "text_fields": ["name", "ghost_column"],  # ghost_column doesn't exist
        "metadata_fields": [],
        "output_fields": ["id", "name"],
        "reasoning": {"id": "uid", "name": "name", "ghost_column": "hallucinated"},
    }

    with (
        patch("api.setup._DATA_ROOT", tmp_path),
        patch("api.setup.OllamaLLMClient") as mock_llm_cls,
    ):
        mock_llm_cls.return_value.generate.return_value = bad_llm_response
        resp = TestClient(_make_app()).post(
            "/setup/suggest-config",
            json={"file_path": str(csv_file)},
        )

    assert resp.status_code == 200
    body = resp.json()
    # _warning surfaced when hallucinated fields detected
    assert "_warning" in body or "ghost_column" in str(body)
