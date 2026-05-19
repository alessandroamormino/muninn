"""Unit tests for scheduler.py — cron parsing, lock-conflict skip, manual mode."""
from __future__ import annotations

import threading
import types
from unittest.mock import MagicMock, patch

import pytest

from scheduler import build_scheduler, is_cron_schedule


# ---------------------------------------------------------------------------
# is_cron_schedule
# ---------------------------------------------------------------------------

class TestIsCronSchedule:
    def test_valid_every_6h(self):
        assert is_cron_schedule("0 */6 * * *") is True

    def test_valid_weekly(self):
        assert is_cron_schedule("30 2 * * 1") is True

    def test_manual_disabled(self):
        assert is_cron_schedule("manual") is False

    def test_empty_string(self):
        assert is_cron_schedule("") is False

    def test_too_few_fields(self):
        assert is_cron_schedule("0 */6 * *") is False

    def test_too_many_fields(self):
        assert is_cron_schedule("0 */6 * * * *") is False

    def test_invalid_chars(self):
        assert is_cron_schedule("not a cron") is False


# ---------------------------------------------------------------------------
# build_scheduler — manual disables
# ---------------------------------------------------------------------------

class TestBuildSchedulerManual:
    def test_returns_none_when_manual(self):
        mock_settings = MagicMock()
        mock_settings.sync.schedule = "manual"
        result = build_scheduler(MagicMock(), mock_settings)
        assert result is None

    def test_returns_none_when_empty(self):
        mock_settings = MagicMock()
        mock_settings.sync.schedule = ""
        result = build_scheduler(MagicMock(), mock_settings)
        assert result is None


# ---------------------------------------------------------------------------
# Helper: call the real build_scheduler with mocked APScheduler and capture
# the _scheduled_job closure passed to scheduler.add_job().
# ---------------------------------------------------------------------------

def _build_and_capture_job(app_state, mock_settings):
    """Invoke the real build_scheduler, return (scheduler_mock, job_closure).

    APScheduler modules are patched in sys.modules so no install is needed.
    The _scheduled_job closure is captured via add_job.side_effect and returned
    so callers can invoke it directly to test its behavior.
    """
    captured = {}
    mock_scheduler_instance = MagicMock()

    def capture_add_job(fn, trigger, **kwargs):
        captured["fn"] = fn

    mock_scheduler_instance.add_job.side_effect = capture_add_job

    mock_bg_module = MagicMock()
    mock_bg_module.BackgroundScheduler.return_value = mock_scheduler_instance
    mock_cron_module = MagicMock()

    with patch.dict("sys.modules", {
        "apscheduler.schedulers.background": mock_bg_module,
        "apscheduler.triggers.cron": mock_cron_module,
    }):
        build_scheduler(app_state, mock_settings)

    assert "fn" in captured, "build_scheduler did not call add_job — closure not captured"
    return mock_scheduler_instance, captured["fn"]


# ---------------------------------------------------------------------------
# _scheduled_job — lock free → calls _run_sync_bg with triggered_by='scheduler'
# ---------------------------------------------------------------------------

class TestScheduledJobLockFree:
    def _make_settings(self):
        s = MagicMock()
        s.sync.schedule = "0 */6 * * *"
        s.embedding.model = "test-model"
        s.source.type = "csv"
        s.weaviate.collection = "TestCol"
        return s

    def test_calls_run_sync_bg_when_lock_free(self):
        """_scheduled_job acquires the free lock and calls _run_sync_bg."""
        real_lock = threading.Lock()
        app_state = types.SimpleNamespace(sync_lock=real_lock, log_store=None)

        run_sync_calls = []

        def fake_run_sync_bg(state, mode, triggered_by="api"):
            run_sync_calls.append({"mode": mode, "triggered_by": triggered_by})
            real_lock.release()  # mirrors _run_sync_bg finally block

        with patch("api.sync._run_sync_bg", side_effect=fake_run_sync_bg):
            _, job_fn = _build_and_capture_job(app_state, self._make_settings())
            job_fn()

        assert len(run_sync_calls) == 1
        assert run_sync_calls[0]["triggered_by"] == "scheduler"
        assert run_sync_calls[0]["mode"] == "incremental"

    def test_passes_app_state_to_run_sync_bg(self):
        """app_state passed to build_scheduler is forwarded to _run_sync_bg."""
        real_lock = threading.Lock()
        app_state = types.SimpleNamespace(sync_lock=real_lock, log_store=None)

        received = []

        def fake_run_sync_bg(state, mode, triggered_by="api"):
            received.append(state)
            real_lock.release()

        with patch("api.sync._run_sync_bg", side_effect=fake_run_sync_bg):
            _, job_fn = _build_and_capture_job(app_state, self._make_settings())
            job_fn()

        assert received[0] is app_state


# ---------------------------------------------------------------------------
# _scheduled_job — lock held → skip (no _run_sync_bg call)
# ---------------------------------------------------------------------------

class TestScheduledJobLockConflict:
    def _make_settings(self):
        s = MagicMock()
        s.sync.schedule = "0 */6 * * *"
        s.embedding.model = "test-model"
        s.source.type = "csv"
        s.weaviate.collection = "TestCol"
        return s

    def test_does_not_call_run_sync_bg_when_lock_held(self):
        """_scheduled_job must NOT call _run_sync_bg when the lock is already taken."""
        real_lock = threading.Lock()
        real_lock.acquire()  # simulate an in-progress sync
        app_state = types.SimpleNamespace(sync_lock=real_lock, log_store=None)

        run_sync_calls = []

        def fake_run_sync_bg(state, mode, triggered_by="api"):
            run_sync_calls.append(True)

        with patch("api.sync._run_sync_bg", side_effect=fake_run_sync_bg):
            _, job_fn = _build_and_capture_job(app_state, self._make_settings())
            job_fn()

        assert len(run_sync_calls) == 0, "_run_sync_bg must not be called when lock is held"
        real_lock.release()  # cleanup

    def test_writes_skipped_log_when_log_store_present(self):
        """Skip branch calls log_store.record(status='skipped', reason='sync_already_running')."""
        real_lock = threading.Lock()
        real_lock.acquire()  # simulate an in-progress sync
        mock_log_store = MagicMock()
        app_state = types.SimpleNamespace(sync_lock=real_lock, log_store=mock_log_store)

        with patch("api.sync._run_sync_bg"):
            _, job_fn = _build_and_capture_job(app_state, self._make_settings())
            job_fn()

        mock_log_store.record.assert_called_once()
        kw = mock_log_store.record.call_args.kwargs
        assert kw["status"] == "skipped"
        assert kw["reason"] == "sync_already_running"
        assert kw["took_ms"] == 0
        assert kw["type"] == "scheduled"
        real_lock.release()  # cleanup

    def test_no_crash_when_log_store_absent(self):
        """Skip branch must not crash when log_store is None (plan-01 only mode)."""
        real_lock = threading.Lock()
        real_lock.acquire()
        app_state = types.SimpleNamespace(sync_lock=real_lock, log_store=None)

        with patch("api.sync._run_sync_bg"):
            _, job_fn = _build_and_capture_job(app_state, self._make_settings())
            job_fn()  # should not raise

        real_lock.release()
