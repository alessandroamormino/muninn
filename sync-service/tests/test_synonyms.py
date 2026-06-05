"""Unit tests for vector_stores.synonyms — VS-05.

Tests cover:
- Bidirectional synonym expansion
- No-match queries unchanged
- Case-insensitive matching
- Missing synonyms.yaml returns empty list
- Valid synonyms.yaml loaded correctly
- Path traversal blocked by _COLLECTION_RE guard
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Import helpers (allows test to be discovered even before implementation exists)
# ---------------------------------------------------------------------------

def _import_synonyms():
    from vector_stores.synonyms import _load_synonyms, _expand_query
    return _load_synonyms, _expand_query


# ---------------------------------------------------------------------------
# _expand_query tests
# ---------------------------------------------------------------------------

class TestExpandQuery:
    def test_expand_query_bidirectional(self):
        """auto → also returns automobile and macchina."""
        _, _expand_query = _import_synonyms()
        groups = [["auto", "automobile", "macchina"]]
        result = _expand_query("auto rossa", groups)
        assert "auto" in result
        assert "automobile" in result
        assert "macchina" in result

    def test_expand_query_no_match(self):
        """No synonym token in query — query unchanged."""
        _, _expand_query = _import_synonyms()
        groups = [["auto", "automobile"]]
        result = _expand_query("bicicletta verde", groups)
        assert result == "bicicletta verde"

    def test_expand_query_case_insensitive(self):
        """Token matching is case-insensitive."""
        _, _expand_query = _import_synonyms()
        groups = [["auto", "macchina"]]
        result = _expand_query("AUTO rossa", groups)
        assert "macchina" in result.lower()

    def test_expand_query_middle_synonym(self):
        """Matching via a non-first group member also expands."""
        _, _expand_query = _import_synonyms()
        groups = [["auto", "automobile", "macchina"]]
        result = _expand_query("macchina sportiva", groups)
        assert "auto" in result
        assert "automobile" in result

    def test_expand_query_multiple_groups(self):
        """Two different groups can both match."""
        _, _expand_query = _import_synonyms()
        groups = [
            ["auto", "macchina"],
            ["developer", "sviluppatore"],
        ]
        result = _expand_query("auto developer", groups)
        assert "macchina" in result
        assert "sviluppatore" in result

    def test_expand_query_no_duplicate_tokens(self):
        """Already-present tokens are not appended again."""
        _, _expand_query = _import_synonyms()
        groups = [["auto", "macchina"]]
        result = _expand_query("auto macchina rossa", groups)
        # "auto" and "macchina" both in query → no extras needed
        assert result == "auto macchina rossa"

    def test_expand_query_empty_groups(self):
        """Empty groups list → identity."""
        _, _expand_query = _import_synonyms()
        result = _expand_query("qualcosa", [])
        assert result == "qualcosa"


# ---------------------------------------------------------------------------
# _load_synonyms tests
# ---------------------------------------------------------------------------

class TestLoadSynonyms:
    def test_load_synonyms_missing_file(self, tmp_path):
        """Missing synonyms.yaml → empty list (no error)."""
        _load_synonyms, _ = _import_synonyms()
        result = _load_synonyms(tmp_path, "NonExistentEntity")
        assert result == []

    def test_load_synonyms_reads_file(self, tmp_path):
        """Valid synonyms.yaml is loaded and returned correctly."""
        _load_synonyms, _ = _import_synonyms()
        entity_dir = tmp_path / "MyEntity"
        entity_dir.mkdir()
        (entity_dir / "synonyms.yaml").write_text(
            "- [auto, macchina, automobile]\n- [CV, curriculum]\n"
        )
        result = _load_synonyms(tmp_path, "MyEntity")
        assert len(result) == 2
        assert set(result[0]) == {"auto", "macchina", "automobile"}
        assert set(result[1]) == {"CV", "curriculum"}

    def test_path_traversal_blocked(self, tmp_path):
        """Collection names failing _COLLECTION_RE → empty list, no filesystem access."""
        _load_synonyms, _ = _import_synonyms()
        # These all fail the regex guard before any Path construction
        assert _load_synonyms(tmp_path, "../../etc") == []
        assert _load_synonyms(tmp_path, "../passwd") == []
        assert _load_synonyms(tmp_path, "foo/bar") == []
        assert _load_synonyms(tmp_path, "foo bar") == []

    def test_load_synonyms_invalid_yaml(self, tmp_path):
        """Malformed YAML → empty list (no crash)."""
        _load_synonyms, _ = _import_synonyms()
        entity_dir = tmp_path / "BadEntity"
        entity_dir.mkdir()
        (entity_dir / "synonyms.yaml").write_text("{ invalid: yaml: [\n")
        result = _load_synonyms(tmp_path, "BadEntity")
        assert result == []

    def test_load_synonyms_empty_file(self, tmp_path):
        """Empty synonyms.yaml → empty list."""
        _load_synonyms, _ = _import_synonyms()
        entity_dir = tmp_path / "EmptyEntity"
        entity_dir.mkdir()
        (entity_dir / "synonyms.yaml").write_text("")
        result = _load_synonyms(tmp_path, "EmptyEntity")
        assert result == []

    def test_load_synonyms_valid_collection_name(self, tmp_path):
        """Valid names with letters, digits, underscores, hyphens are allowed."""
        _load_synonyms, _ = _import_synonyms()
        entity_dir = tmp_path / "My-Entity_123"
        entity_dir.mkdir()
        (entity_dir / "synonyms.yaml").write_text("- [a, b]\n")
        result = _load_synonyms(tmp_path, "My-Entity_123")
        assert result == [["a", "b"]]
