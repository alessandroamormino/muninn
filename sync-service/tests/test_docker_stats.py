"""Tests for monitoring/docker_stats.py — pure-function CPU%/mem/net/blkio parsing
and the compose-project self-discovery, against fixture dicts only (no live Docker
daemon required — SC-27-8).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from docker.errors import NotFound

from monitoring.docker_stats import (
    _blkio,
    _cpu_percent,
    _mem_usage_no_cache,
    _net_io,
    get_compose_project,
    parse_container_metrics,
)


def test_cpu_percent_formula():
    stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 1000, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 900},
    }
    assert _cpu_percent(stats) == 200.0  # (200-100)/(1000-900)*2*100


def test_cpu_percent_online_cpus_fallback():
    stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200, "percpu_usage": [1, 1, 1]},
            "system_cpu_usage": 1000,
        },
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 900},
    }
    # online_cpus absent -> falls back to len(percpu_usage) == 3
    assert _cpu_percent(stats) == 300.0  # (200-100)/(1000-900)*3*100


def test_cpu_percent_zero_guard():
    # system_delta <= 0 -> returns 0.0, no ZeroDivisionError
    stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 900, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 900},
    }
    assert _cpu_percent(stats) == 0.0

    # cpu_delta <= 0 -> also returns 0.0
    stats2 = {
        "cpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 900},
    }
    assert _cpu_percent(stats2) == 0.0


def test_mem_usage_no_cache():
    mem_stats = {"usage": 500_000_000, "stats": {"inactive_file": 50_000_000}}
    assert _mem_usage_no_cache(mem_stats) == 450_000_000

    # inactive >= usage -> returns usage unchanged (no negative result)
    mem_stats_edge = {"usage": 100, "stats": {"inactive_file": 100}}
    assert _mem_usage_no_cache(mem_stats_edge) == 100


def test_net_io_sum():
    stats = {
        "networks": {
            "eth0": {"rx_bytes": 1000, "tx_bytes": 2000},
            "eth1": {"rx_bytes": 500, "tx_bytes": 250},
        }
    }
    assert _net_io(stats) == (1500, 2250)

    # networks absent -> (0, 0)
    assert _net_io({}) == (0, 0)
    assert _net_io({"networks": None}) == (0, 0)


def test_blkio_sum():
    stats = {
        "blkio_stats": {
            "io_service_bytes_recursive": [
                {"op": "Read", "value": 4096},
                {"op": "Write", "value": 8192},
                {"op": "Read", "value": 1024},
            ]
        }
    }
    assert _blkio(stats) == (5120, 8192)

    # blkio_stats absent -> (0, 0)
    assert _blkio({}) == (0, 0)


def test_parse_container_metrics():
    fake_stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 1000, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 900},
        "memory_stats": {"usage": 500_000_000, "limit": 2_000_000_000, "stats": {"inactive_file": 50_000_000}},
        "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 2000}},
        "blkio_stats": {"io_service_bytes_recursive": [{"op": "Read", "value": 4096}, {"op": "Write", "value": 8192}]},
    }
    fake_container = MagicMock()
    fake_container.name = "orchestrator"
    fake_container.status = "running"
    fake_container.stats.return_value = fake_stats
    fake_container.attrs = {
        "State": {"StartedAt": "2026-06-21T00:00:00Z", "Health": {"Status": "healthy"}}
    }

    result = parse_container_metrics(fake_container)

    assert result["name"] == "orchestrator"
    assert result["cpu_pct"] == 200.0
    assert result["mem_used"] == 450_000_000
    assert result["mem_limit"] == 2_000_000_000
    assert result["net_rx"] == 1000
    assert result["net_tx"] == 2000
    assert result["blk_read"] == 4096
    assert result["blk_write"] == 8192
    assert result["uptime_s"] is not None
    assert result["status"] == "running"
    assert result["health"] == "healthy"


def test_parse_container_metrics_no_healthcheck():
    """health is None (no KeyError) when container has no healthcheck configured."""
    fake_stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0},
        "precpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0},
        "memory_stats": {},
    }
    fake_container = MagicMock()
    fake_container.name = "vector-db"
    fake_container.status = "running"
    fake_container.stats.return_value = fake_stats
    fake_container.attrs = {"State": {}}  # no StartedAt, no Health

    result = parse_container_metrics(fake_container)

    assert result["health"] is None
    assert result["uptime_s"] is None


def test_get_compose_project_self():
    fake_container = MagicMock()
    fake_container.labels = {"com.docker.compose.project": "smart-search"}

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    assert get_compose_project(fake_client) == "smart-search"


def test_get_compose_project_notfound_fallback():
    fake_container = MagicMock()
    fake_container.id = "abc123def456"
    fake_container.labels = {"com.docker.compose.project": "smart-search"}

    fake_client = MagicMock()
    fake_client.containers.get.side_effect = NotFound("no such container")
    fake_client.containers.list.return_value = [fake_container]

    import socket
    import unittest.mock as mock

    with mock.patch.object(socket, "gethostname", return_value="abc123def456"):
        assert get_compose_project(fake_client) == "smart-search"


def test_get_compose_project_missing_label_raises():
    fake_container = MagicMock()
    fake_container.labels = {}

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    try:
        get_compose_project(fake_client)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
