"""Docker Engine API stats parsing — pure functions + lazy client construction.

Foundation module consumed by the `/metrics` router (Plan 03). Every parsing
helper here is a pure function operating on plain dicts (the JSON shape
returned by `container.stats(stream=False)`), so it is fully testable against
fixture dicts without a live Docker daemon (SC-27-8).

Threat model (T-27-01-EoP): this module calls ONLY read-only Engine API
methods (`from_env`, `containers.get`, `containers.list`, `.stats`) — NEVER
run/create/start/stop/remove/exec. Any future mutating call here is a
security regression.
"""
from __future__ import annotations

import socket
from datetime import datetime, timezone

import docker
from docker.errors import DockerException, NotFound

__all__ = [
    "get_docker_client",
    "get_compose_project",
    "parse_container_metrics",
]


def get_docker_client() -> docker.DockerClient:
    """Lazily construct a DockerClient. Raises DockerException/FileNotFoundError
    if the socket is unavailable — caller (the /metrics route) must catch this
    and degrade gracefully (D-08), never let it propagate to a 500 or crash
    app startup. Must NOT be called from main.py's lifespan() (Pitfall 4)."""
    return docker.from_env()  # defaults to unix:///var/run/docker.sock on Linux


def get_compose_project(client: docker.DockerClient) -> str:
    """Self-discover this container's own compose project label (D-06).
    Falls back to a list-scan if hostname-based lookup fails (Pitfall 3)."""
    try:
        own = client.containers.get(socket.gethostname())
    except NotFound:
        own = next(
            (c for c in client.containers.list() if c.id.startswith(socket.gethostname())),
            None,
        )
        if own is None:
            raise RuntimeError("could not self-identify orchestrator container")
    project = own.labels.get("com.docker.compose.project")
    if not project:
        raise RuntimeError("orchestrator container missing com.docker.compose.project label")
    return project


def _cpu_percent(stats: dict) -> float:
    cpu = stats.get("cpu_stats", {})
    precpu = stats.get("precpu_stats", {})
    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - precpu.get("cpu_usage", {}).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
    online_cpus = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage") or [])
    if system_delta > 0 and cpu_delta > 0 and online_cpus:
        return round((cpu_delta / system_delta) * online_cpus * 100.0, 2)
    return 0.0


def _mem_usage_no_cache(mem_stats: dict) -> int:
    """Subtract page cache from usage — mirrors calculateMemUsageUnixNoCache (docker/cli)."""
    usage = mem_stats.get("usage", 0)
    stats = mem_stats.get("stats", {})
    inactive = stats.get("total_inactive_file", stats.get("inactive_file", 0))
    return usage - inactive if inactive < usage else usage


def _net_io(stats: dict) -> tuple[int, int]:
    networks = stats.get("networks", {}) or {}
    rx = sum(n.get("rx_bytes", 0) for n in networks.values())
    tx = sum(n.get("tx_bytes", 0) for n in networks.values())
    return rx, tx


def _blkio(stats: dict) -> tuple[int, int]:
    entries = (stats.get("blkio_stats", {}) or {}).get("io_service_bytes_recursive") or []
    read = sum(e["value"] for e in entries if e.get("op") == "Read")
    write = sum(e["value"] for e in entries if e.get("op") == "Write")
    return read, write


def parse_container_metrics(container) -> dict:
    """container: docker.models.containers.Container (already `.reload()`ed by caller if status
    freshness matters). Single stats(stream=False) call — no second round-trip needed."""
    stats = container.stats(stream=False)
    mem = stats.get("memory_stats", {})
    net_rx, net_tx = _net_io(stats)
    blk_read, blk_write = _blkio(stats)
    started_at = container.attrs.get("State", {}).get("StartedAt")
    uptime_s = None
    if started_at:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        uptime_s = (datetime.now(timezone.utc) - started).total_seconds()
    return {
        "name": container.name,
        "cpu_pct": _cpu_percent(stats),
        "mem_used": _mem_usage_no_cache(mem),
        "mem_limit": mem.get("limit", 0),
        "net_rx": net_rx,
        "net_tx": net_tx,
        "blk_read": blk_read,
        "blk_write": blk_write,
        "uptime_s": uptime_s,
        "status": container.status,                                   # "running", "exited", ...
        "health": container.attrs.get("State", {}).get("Health", {}).get("Status"),  # may be None
    }
