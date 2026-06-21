"""Test del router metrics — GET /metrics.

Verifica:
- GET /metrics con admin + client Docker mockato → 200, containers + totals popolati
- GET /metrics quando get_docker_client solleva DockerException → 503 structured body
- GET /metrics senza ruolo admin → 403 (require_admin non reimplementato)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.metrics import router
from auth.dependencies import require_admin
from auth.user_store import UserRecord


def _admin_user() -> UserRecord:
    return UserRecord(
        id=1, username="admin", hashed_password="", role="admin",
        totp_secret=None, totp_enabled=False,
        created_at="2026-01-01T00:00:00", is_active=True,
    )


def _make_admin_app() -> FastAPI:
    """FastAPI minimale con il router metrics e auth bypass come admin."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: _admin_user()
    return app


def _make_fake_client():
    fake_stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 200, "percpu_usage": [1, 1]}, "system_cpu_usage": 1000, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 900},
        "memory_stats": {"usage": 500_000_000, "limit": 2_000_000_000, "stats": {"inactive_file": 50_000_000}},
        "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 2000}},
        "blkio_stats": {"io_service_bytes_recursive": [{"op": "Read", "value": 4096}, {"op": "Write", "value": 8192}]},
    }
    fake_container = MagicMock(status="running")
    fake_container.name = "orchestrator"
    fake_container.stats.return_value = fake_stats
    fake_container.attrs = {"State": {"StartedAt": "2026-06-21T00:00:00Z", "Health": {"Status": "healthy"}}}
    fake_container.labels = {"com.docker.compose.project": "smart-search"}

    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container
    fake_client.containers.list.return_value = [fake_container]
    return fake_client


def test_metrics_returns_containers_and_totals(monkeypatch):
    fake_client = _make_fake_client()
    monkeypatch.setattr("api.metrics.get_docker_client", lambda: fake_client)

    app = _make_admin_app()
    with TestClient(app) as client:
        resp = client.get("/metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["containers"][0]["name"] == "orchestrator"
    # (200-100)/(1000-900)*2*100 = 200.0 — verified-correct formula per 27-01-SUMMARY.md
    # (the plan/research's documented 20.0 was a doc arithmetic error, not a code defect)
    assert body["containers"][0]["cpu_pct"] == 200.0
    assert "totals" in body
    assert body["totals"]["cpu_pct"] == 200.0
    assert body["totals"]["mem_used"] == 450_000_000
    assert body["totals"]["mem_limit"] == 2_000_000_000


def test_metrics_503_when_socket_unavailable(monkeypatch):
    from docker.errors import DockerException

    def _raise():
        raise DockerException("socket not found")

    monkeypatch.setattr("api.metrics.get_docker_client", _raise)
    app = _make_admin_app()
    with TestClient(app) as client:
        resp = client.get("/metrics")

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "docker_unavailable"


def test_metrics_403_for_reader(monkeypatch):
    fake_client = _make_fake_client()
    monkeypatch.setattr("api.metrics.get_docker_client", lambda: fake_client)

    app = FastAPI()
    app.include_router(router)

    def _reject_non_admin():
        raise HTTPException(status_code=403, detail="Accesso riservato agli amministratori")

    app.dependency_overrides[require_admin] = _reject_non_admin

    with TestClient(app) as client:
        resp = client.get("/metrics")

    assert resp.status_code == 403
