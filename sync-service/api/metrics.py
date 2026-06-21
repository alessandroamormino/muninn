"""Metrics API router — GET /metrics.

Live Docker stats for the whole compose stack. Admin-only (D-07). Calls into
`monitoring.docker_stats` (Plan 01) to self-discover the compose project,
list its containers, and parse per-container CPU/mem/net/blkio stats.

No BackgroundTasks/lock needed — /metrics has no mutating side effect, unlike
/sync and /collections/{name}/unload|load. Mirrors api/sync.py's GET /sync/status
shape (simple synchronous read).
"""
from __future__ import annotations

import logging

from docker.errors import DockerException
from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import require_admin
from auth.user_store import UserRecord
from monitoring.docker_stats import get_docker_client, get_compose_project, parse_container_metrics

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/metrics")
async def metrics(_: UserRecord = Depends(require_admin)) -> dict:
    """Live Docker stats for the whole compose stack (SC-27-1, SC-27-2). Admin-only (D-07).

    Degrades gracefully (D-08/SC-27-6): if the Docker socket is unavailable, returns 503 with
    a structured error body rather than a generic 500 or crashing the orchestrator process.
    """
    try:
        client = get_docker_client()
        project = get_compose_project(client)
        containers = client.containers.list(filters={"label": f"com.docker.compose.project={project}"})
        results = [parse_container_metrics(c) for c in containers]
    except (DockerException, FileNotFoundError, RuntimeError) as exc:
        logger.warning("Docker stats unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": "docker_unavailable", "message": str(exc)},
        )

    totals = {
        "cpu_pct": round(sum(c["cpu_pct"] for c in results), 2),
        "mem_used": sum(c["mem_used"] for c in results),
        "mem_limit": max((c["mem_limit"] for c in results), default=0),  # host RAM ceiling, not summed
    }
    return {"containers": results, "totals": totals}
