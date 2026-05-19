"""Unit tests for sync/log_store.py.

Uses a real in-memory (tempfile) SQLite database — no mocking of sqlite3.
Tests D-01 (SQLite stdlib), D-02 (schema), D-03 (filter), D-04 (latest), D-05 (prune).
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import pytest

from sync.log_store import LogStore


def _now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


@pytest.fixture
def store(tmp_path):
    """Fresh LogStore for each test."""
    s = LogStore(tmp_path / ".sync" / "sync_logs.db")
    yield s
    s.close()


def _insert(store: LogStore, *, status: str = "completed", n: int = 1) -> list[int]:
    """Insert *n* rows and return their ids."""
    ids = []
    for _ in range(n):
        now = _now()
        rid = store.record(
            started_at=now,
            finished_at=now,
            type="incremental",
            status=status,
            took_ms=100,
            model="test-model",
            source_type="csv",
            collection="TestCollection",
            inserted=1,
            updated=0,
            skipped_records=0,
            errors=0,
        )
        ids.append(rid)
    return ids


class TestRecord:
    def test_returns_incrementing_id(self, store):
        ids = _insert(store, n=3)
        assert ids == [1, 2, 3]

    def test_row_contains_all_columns(self, store):
        _insert(store)
        rows = store.get_logs()
        assert len(rows) == 1
        row = rows[0]
        expected_keys = {
            "id", "started_at", "finished_at", "type", "status",
            "took_ms", "model", "source_type", "collection",
            "inserted", "updated", "skipped_records", "errors",
            "error_message", "reason",
        }
        assert expected_keys.issubset(set(row.keys()))

    def test_nullable_columns_are_none(self, store):
        _insert(store)
        row = store.get_logs()[0]
        assert row["error_message"] is None
        assert row["reason"] is None

    def test_error_message_stored(self, store):
        now = _now()
        store.record(
            started_at=now, finished_at=now, type="full", status="failed",
            took_ms=50, model="m", source_type="csv", collection="C",
            inserted=0, updated=0, skipped_records=0, errors=1,
            error_message="Connection refused",
        )
        row = store.get_logs()[0]
        assert row["error_message"] == "Connection refused"

    def test_reason_stored_for_skipped(self, store):
        now = _now()
        store.record(
            started_at=now, finished_at=now, type="scheduled", status="skipped",
            took_ms=0, model="m", source_type="csv", collection="C",
            inserted=0, updated=0, skipped_records=0, errors=0,
            reason="sync_already_running",
        )
        row = store.get_logs()[0]
        assert row["reason"] == "sync_already_running"
        assert row["took_ms"] == 0


class TestPruning:
    def test_prunes_to_1000_after_1001_inserts(self, store):
        _insert(store, n=1001)
        rows = store.get_logs(limit=100)
        # get_logs caps at 100 — verify via direct DB count
        cursor = store._conn.execute("SELECT COUNT(*) FROM sync_runs")
        count = cursor.fetchone()[0]
        assert count == 1000, f"Expected 1000 rows after pruning, got {count}"

    def test_keeps_newest_rows(self, store):
        """After pruning, the oldest row (id=1) should be gone."""
        ids = _insert(store, n=1001)
        oldest_id = ids[0]
        cursor = store._conn.execute(
            "SELECT id FROM sync_runs WHERE id = ?", (oldest_id,)
        )
        assert cursor.fetchone() is None, "Oldest row should have been pruned"


class TestGetLogs:
    def test_returns_newest_first(self, store):
        ids = _insert(store, n=3)
        rows = store.get_logs()
        returned_ids = [r["id"] for r in rows]
        # ORDER BY started_at DESC — since we insert with isoformat() timestamps
        # that are monotonically increasing, newest = highest id
        assert returned_ids[0] >= returned_ids[-1]

    def test_limit_respected(self, store):
        _insert(store, n=10)
        rows = store.get_logs(limit=3)
        assert len(rows) == 3

    def test_limit_capped_at_100(self, store):
        _insert(store, n=5)
        # Even if we ask for 999, we get at most 100 (and at most what's in DB)
        rows = store.get_logs(limit=999)
        assert len(rows) == 5  # only 5 in DB

    def test_status_filter_completed(self, store):
        _insert(store, status="completed", n=2)
        _insert(store, status="failed", n=3)
        rows = store.get_logs(status="completed")
        assert all(r["status"] == "completed" for r in rows)
        assert len(rows) == 2

    def test_status_filter_failed(self, store):
        _insert(store, status="completed", n=2)
        _insert(store, status="failed", n=3)
        rows = store.get_logs(status="failed")
        assert len(rows) == 3

    def test_no_filter_returns_all(self, store):
        _insert(store, status="completed", n=2)
        _insert(store, status="failed", n=1)
        rows = store.get_logs(limit=10)
        assert len(rows) == 3


class TestGetLatest:
    def test_returns_none_on_empty(self, store):
        assert store.get_latest() is None

    def test_returns_most_recent(self, store):
        ids = _insert(store, n=3)
        latest = store.get_latest()
        assert latest is not None
        assert latest["id"] == ids[-1]

    def test_returns_dict(self, store):
        _insert(store)
        latest = store.get_latest()
        assert isinstance(latest, dict)


class TestCollectionFilter:
    def test_get_logs_collection_filter(self, store):
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc).isoformat()
        for _ in range(3):
            store.record(
                started_at=now,
                finished_at=now,
                type="full",
                status="completed",
                took_ms=10,
                model="m",
                source_type="csv",
                collection="Foo",
                inserted=1,
                updated=0,
                skipped_records=0,
                errors=0,
            )
        for _ in range(2):
            store.record(
                started_at=now,
                finished_at=now,
                type="full",
                status="completed",
                took_ms=10,
                model="m",
                source_type="csv",
                collection="Bar",
                inserted=1,
                updated=0,
                skipped_records=0,
                errors=0,
            )
        foo_rows = store.get_logs(collection="Foo")
        assert len(foo_rows) == 3
        assert all(r["collection"] == "Foo" for r in foo_rows)
        bar_rows = store.get_logs(collection="Bar")
        assert len(bar_rows) == 2
        combined = store.get_logs(status="completed", collection="Foo")
        assert len(combined) == 3
