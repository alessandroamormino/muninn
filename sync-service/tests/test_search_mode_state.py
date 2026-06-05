"""Unit tests for vector_stores.search_mode_state — VS-06, VS-07.

Tests cover:
- Write/read roundtrip
- Multiple collections in one file
- Missing file returns None
- Missing collection key returns None
- Atomic write (no partial write)
- Mode change detection
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _import_state():
    from vector_stores.search_mode_state import (
        read_stored_search_mode,
        write_stored_search_mode,
        detect_search_mode_change,
    )
    return read_stored_search_mode, write_stored_search_mode, detect_search_mode_change


class TestReadWriteRoundtrip:
    def test_write_read_roundtrip(self, tmp_path):
        """Writing then reading returns the same mode."""
        read, write, _ = _import_state()
        state_path = tmp_path / "search_mode_state.json"
        write("MyCol", "hybrid", path=state_path)
        assert read("MyCol", path=state_path) == "hybrid"

    def test_write_multiple_collections(self, tmp_path):
        """Multiple collections coexist in one JSON file."""
        read, write, _ = _import_state()
        state_path = tmp_path / "search_mode_state.json"
        write("ColA", "bm25", path=state_path)
        write("ColB", "fts", path=state_path)
        assert read("ColA", path=state_path) == "bm25"
        assert read("ColB", path=state_path) == "fts"

    def test_overwrite_existing_collection(self, tmp_path):
        """Writing a new mode for an existing collection overwrites it."""
        read, write, _ = _import_state()
        state_path = tmp_path / "search_mode_state.json"
        write("ColA", "hybrid", path=state_path)
        write("ColA", "vector", path=state_path)
        assert read("ColA", path=state_path) == "vector"


class TestReadMissing:
    def test_read_missing_file(self, tmp_path):
        """Read on a non-existent file returns None (no crash)."""
        read, _, _ = _import_state()
        assert read("X", path=tmp_path / "nonexistent_state.json") is None

    def test_read_missing_collection_key(self, tmp_path):
        """File exists but collection key absent → None."""
        read, write, _ = _import_state()
        state_path = tmp_path / "search_mode_state.json"
        write("ColA", "hybrid", path=state_path)
        assert read("ColB", path=state_path) is None


class TestAtomicWrite:
    def test_atomic_write_file_exists(self, tmp_path):
        """After write, file exists and is valid JSON."""
        read, write, _ = _import_state()
        state_path = tmp_path / "search_mode_state.json"
        write("MyCollection", "fts", path=state_path)
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data == {"MyCollection": "fts"}

    def test_no_tmp_file_left_over(self, tmp_path):
        """Temporary .json.tmp file is renamed (atomic) — no leftover."""
        _, write, _ = _import_state()
        state_path = tmp_path / "search_mode_state.json"
        write("MyCollection", "bm25", path=state_path)
        tmp_file = state_path.with_suffix(".json.tmp")
        assert not tmp_file.exists()

    def test_creates_parent_directories(self, tmp_path):
        """Parent directory created automatically if missing."""
        _, write, _ = _import_state()
        deep_path = tmp_path / "nested" / "dir" / "state.json"
        write("Col", "hybrid", path=deep_path)
        assert deep_path.exists()


class TestModeChangeDetection:
    def test_mode_change_detected(self, tmp_path):
        """stored=hybrid, config=fts → detect_search_mode_change returns True."""
        _, write, detect = _import_state()
        state_path = tmp_path / "state.json"
        write("MyCol", "hybrid", path=state_path)
        assert detect("MyCol", "fts", path=state_path) is True

    def test_no_mode_change(self, tmp_path):
        """stored=fts, config=fts → detect returns False."""
        _, write, detect = _import_state()
        state_path = tmp_path / "state.json"
        write("MyCol", "fts", path=state_path)
        assert detect("MyCol", "fts", path=state_path) is False

    def test_first_run_no_change(self, tmp_path):
        """Missing file (first run) → detect returns False (no re-index needed)."""
        _, _, detect = _import_state()
        state_path = tmp_path / "nonexistent.json"
        assert detect("MyCol", "hybrid", path=state_path) is False

    def test_missing_collection_key_no_change(self, tmp_path):
        """File exists but collection not recorded → False (first run for this entity)."""
        _, write, detect = _import_state()
        state_path = tmp_path / "state.json"
        write("OtherCol", "vector", path=state_path)
        assert detect("NewCol", "fts", path=state_path) is False
