"""Tests for _run_sync_bg logging hooks (D-08).

Verifies that _run_sync_bg calls log_store.record() with correct arguments
on success, failure, and for each trigger/mode combination.
"""
from __future__ import annotations

import threading
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from api.sync import _run_sync_bg


def _make_state(log_store=None):
    """Return an app_state with the lock already acquired (as POST /sync does)."""
    lock = threading.Lock()
    lock.acquire()
    engine = MagicMock()
    return types.SimpleNamespace(
        sync_lock=lock,
        sync_status={},
        sync_engine=engine,
        vector_store=MagicMock(),
        log_store=log_store,
    ), engine


def _mock_settings():
    s = MagicMock()
    s.embedding.model = "test-model"
    s.source.type = "csv"
    s.vector_store.collection = "TestCol"
    return s


@contextmanager
def _patch_sync_bg(engine_mock):
    """Patch load_config and SyncEngine so _run_sync_bg uses the provided mock engine."""
    mock_settings = _mock_settings()
    with patch("api.sync.load_config", return_value=mock_settings), \
         patch("api.sync.SyncEngine", return_value=engine_mock), \
         patch("api.sync.settings", mock_settings):
        yield


class TestRunSyncBgLoggingSuccess:
    def test_records_completed_on_success(self):
        """Successful incremental sync -> log_store.record(status='completed')."""
        mock_log_store = MagicMock()
        state, engine = _make_state(log_store=mock_log_store)
        engine.run_incremental.return_value = {
            "total": 10, "inserted": 8, "updated": 2,
            "skipped": 0, "errors": 0, "timestamp": "2026-01-01T00:00:00Z",
        }

        with _patch_sync_bg(engine):
            _run_sync_bg(state, mode="incremental", triggered_by="api")

        mock_log_store.record.assert_called_once()
        kw = mock_log_store.record.call_args.kwargs
        assert kw["status"] == "completed"
        assert kw["type"] == "incremental"
        assert kw["inserted"] == 8
        assert kw["updated"] == 2
        assert kw["error_message"] is None
        assert kw["reason"] is None

    def test_type_is_scheduled_for_scheduler_trigger(self):
        """triggered_by='scheduler' -> type='scheduled' in log record."""
        mock_log_store = MagicMock()
        state, engine = _make_state(log_store=mock_log_store)
        engine.run_incremental.return_value = {
            "total": 5, "inserted": 5, "updated": 0,
            "skipped": 0, "errors": 0, "timestamp": "2026-01-01T00:00:00Z",
        }

        with patch("api.sync.settings", _mock_settings()):
            _run_sync_bg(state, mode="incremental", triggered_by="scheduler")

        kw = mock_log_store.record.call_args.kwargs
        assert kw["type"] == "scheduled"

    def test_type_is_full_for_full_mode(self):
        """mode='full', triggered_by='api' -> type='full' in log record."""
        mock_log_store = MagicMock()
        state, engine = _make_state(log_store=mock_log_store)
        engine.run_full.return_value = {
            "total": 100, "inserted": 100, "updated": 0,
            "skipped": 0, "errors": 0, "timestamp": "2026-01-01T00:00:00Z",
        }

        with patch("api.sync.settings", _mock_settings()):
            _run_sync_bg(state, mode="full", triggered_by="api")

        kw = mock_log_store.record.call_args.kwargs
        assert kw["type"] == "full"

    def test_took_ms_is_positive(self):
        """took_ms in log record must be a non-negative integer."""
        mock_log_store = MagicMock()
        state, engine = _make_state(log_store=mock_log_store)
        engine.run_incremental.return_value = {
            "total": 1, "inserted": 1, "updated": 0,
            "skipped": 0, "errors": 0, "timestamp": "2026-01-01T00:00:00Z",
        }

        with patch("api.sync.settings", _mock_settings()):
            _run_sync_bg(state, mode="incremental")

        kw = mock_log_store.record.call_args.kwargs
        assert isinstance(kw["took_ms"], int)
        assert kw["took_ms"] >= 0


class TestRunSyncBgLoggingFailure:
    def test_records_failed_on_engine_exception(self):
        """Engine exception -> log_store.record(status='failed', error_message set)."""
        mock_log_store = MagicMock()
        state, engine = _make_state(log_store=mock_log_store)
        engine.run_incremental.side_effect = RuntimeError("Connection lost")

        with _patch_sync_bg(engine):
            _run_sync_bg(state, mode="incremental", triggered_by="api")

        mock_log_store.record.assert_called_once()
        kw = mock_log_store.record.call_args.kwargs
        assert kw["status"] == "failed"
        assert kw["error_message"] == "Connection lost"
        assert kw["errors"] == 1
        assert kw["type"] == "incremental"

    def test_no_crash_when_log_store_absent(self):
        """_run_sync_bg completes without error when log_store is None."""
        state, engine = _make_state(log_store=None)
        engine.run_incremental.return_value = {
            "total": 0, "inserted": 0, "updated": 0,
            "skipped": 0, "errors": 0, "timestamp": "2026-01-01T00:00:00Z",
        }

        with patch("api.sync.settings", _mock_settings()):
            _run_sync_bg(state, mode="incremental")  # must not raise

    def test_lock_always_released(self):
        """Lock is released in finally even when engine raises."""
        state, engine = _make_state(log_store=None)
        engine.run_incremental.side_effect = ValueError("boom")

        with patch("api.sync.settings", _mock_settings()):
            _run_sync_bg(state, mode="incremental")

        # If lock was released, acquiring it again succeeds immediately
        acquired = state.sync_lock.acquire(blocking=False)
        assert acquired, "Lock was not released after exception"
        state.sync_lock.release()
