"""Tests for GET /config/{collection}, PUT /config/{collection}, POST /config router."""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import require_admin, get_current_user
from auth.user_store import UserRecord

# config.py does not exist yet — import will fail until GREEN phase
# This import is intentional for TDD RED phase
from api.config import router


_ADMIN = UserRecord(
    id=1, username="admin", hashed_password="", role="admin",
    totp_secret=None, totp_enabled=False,
    created_at="2026-01-01T00:00:00", is_active=True
)


def _make_app_with_auth() -> FastAPI:
    """Create a minimal FastAPI app with config router and auth bypass."""
    app = FastAPI()
    app.include_router(router)
    app.state.sync_lock = threading.Lock()
    app.state.sync_status = {"status": "idle", "last_run": None}
    app.state.upload_status = None
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    return app


# ---------------------------------------------------------------------------
# Test 1: GET /config/{collection} happy path
# ---------------------------------------------------------------------------

def test_get_config_happy(tmp_path):
    """GET /config/Collaboratori returns 200 + {yaml: <file contents>}."""
    config_dir = tmp_path / "configuration" / "Collaboratori"
    config_dir.mkdir(parents=True)
    yaml_content = "source:\n  type: csv\n  file_path: ./data/test.csv\n"
    (config_dir / "config.yaml").write_text(yaml_content, encoding="utf-8")

    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).get("/config/Collaboratori")

    assert resp.status_code == 200
    body = resp.json()
    assert "yaml" in body
    assert body["yaml"] == yaml_content


# ---------------------------------------------------------------------------
# Test 2: GET /config/{collection} 404 for unknown entity
# ---------------------------------------------------------------------------

def test_get_config_404(tmp_path):
    """GET /config/UnknownEntity returns 404."""
    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).get("/config/UnknownEntity")

    assert resp.status_code == 404
    assert "UnknownEntity" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 3: GET /config 422 on path traversal
# ---------------------------------------------------------------------------

def test_get_config_422_traversal(tmp_path):
    """GET /config with collection containing traversal chars returns 422."""
    # FastAPI may reject certain URL patterns at routing level,
    # but we test the validator by injecting a bad collection param directly
    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        # Use percent-encoded path traversal — FastAPI routes to handler
        resp = TestClient(_make_app_with_auth()).get("/config/..etc")

    # ..etc is valid chars except '.' repeated — regex ^[a-zA-Z0-9_-]+$ rejects it
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test 4: PUT /config/{collection} valid YAML writes to disk
# ---------------------------------------------------------------------------

def test_put_config_valid(tmp_path):
    """PUT /config/Collaboratori with valid YAML returns 200 + {ok: true}; file updated on disk."""
    config_dir = tmp_path / "configuration" / "Collaboratori"
    config_dir.mkdir(parents=True)
    old_content = "source:\n  type: csv\n"
    (config_dir / "config.yaml").write_text(old_content, encoding="utf-8")

    new_yaml = (
        "source:\n"
        "  type: mysql\n"
        "  host: ${MYSQL_HOST}\n"
        "  query:\n"
        "    from: users\n"
        "    \"on\": \"users.id = roles.user_id\"\n"
    )

    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).put(
            "/config/Collaboratori",
            json={"yaml": new_yaml},
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify file was written verbatim
    written = (config_dir / "config.yaml").read_text(encoding="utf-8")
    assert written == new_yaml

    # YAML 1.1 round-trip note: server writes raw text (never round-trips through yaml.dump)
    # so a join 'on' key supplied by the operator survives intact
    assert '"on"' in written, "Quoted 'on' key must survive verbatim in written file"

    # Verify yaml.safe_load round-trip preserves 'on' key as string (not boolean True)
    parsed = yaml.safe_load(yaml.dump({"on": "table.id = other.id"}))
    assert parsed["on"] == "table.id = other.id", "yaml.dump must quote bare 'on' key"


# ---------------------------------------------------------------------------
# Test 5: PUT /config/{collection} invalid YAML returns 422
# ---------------------------------------------------------------------------

def test_put_config_invalid_yaml(tmp_path):
    """PUT /config/Collaboratori with invalid YAML returns 422 mentioning 'Invalid YAML'."""
    config_dir = tmp_path / "configuration" / "Collaboratori"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")

    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).put(
            "/config/Collaboratori",
            json={"yaml": "key: : value: bad"},
        )

    assert resp.status_code == 422
    assert "Invalid YAML" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 6: PUT /config/{collection} empty YAML rejected
# ---------------------------------------------------------------------------

def test_put_config_empty_yaml_rejected(tmp_path):
    """PUT /config/Collaboratori with empty YAML body returns 422 (safe_load returns None)."""
    config_dir = tmp_path / "configuration" / "Collaboratori"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")

    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).put(
            "/config/Collaboratori",
            json={"yaml": ""},
        )

    assert resp.status_code == 422
    assert "mapping" in resp.json()["detail"].lower() or "YAML" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 7: PUT /config/{collection} YAML list (non-mapping) rejected
# ---------------------------------------------------------------------------

def test_put_config_non_mapping_rejected(tmp_path):
    """PUT with YAML list (not mapping) returns 422."""
    config_dir = tmp_path / "configuration" / "Collaboratori"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")

    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).put(
            "/config/Collaboratori",
            json={"yaml": "- a\n- b\n"},
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test 8: PUT /config/{collection} 404 when entity missing
# ---------------------------------------------------------------------------

def test_put_config_404_missing_entity(tmp_path):
    """PUT /config/Ghost returns 404 when entity directory does not exist."""
    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).put(
            "/config/Ghost",
            json={"yaml": "source:\n  type: csv\n"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 9: POST /config creates new entity
# ---------------------------------------------------------------------------

def test_post_config_creates(tmp_path):
    """POST /config creates new entity dir + config.yaml; YAML contains ${MYSQL_HOST}."""
    config_root = tmp_path / "configuration"
    config_root.mkdir()

    payload = {
        "collection": "NewEntity",
        "source_type": "mysql",
        "port": 3306,
        "host_env_var": "MYSQL_HOST",
        "db_env_var": "MYSQL_DB",
        "user_env_var": "MYSQL_USER",
        "password_env_var": "MYSQL_PASSWORD",
        "from_table": "users",
        "fields": ["id", "name"],
        "id_field": "id",
        "text_fields": ["name"],
        "metadata_fields": [],
        "output_fields": ["id", "name"],
    }

    with (
        patch("api.config._CONFIG_ROOT", config_root),
        patch("api.upload._CONFIG_ROOT", config_root),
    ):
        resp = TestClient(_make_app_with_auth()).post("/config", json=payload)

    assert resp.status_code == 201
    assert resp.json() == {"collection": "NewEntity"}

    config_path = config_root / "NewEntity" / "config.yaml"
    assert config_path.exists(), "config.yaml must be created"

    written = config_path.read_text(encoding="utf-8")
    assert "${MYSQL_HOST}" in written, "Host must be stored as ${MYSQL_HOST} placeholder"
    assert "${MYSQL_PASSWORD}" in written, "Password must be stored as placeholder"


# ---------------------------------------------------------------------------
# Test 10: POST /config 409 on duplicate
# ---------------------------------------------------------------------------

def test_post_config_409_duplicate(tmp_path):
    """POST /config with existing collection name returns 409."""
    config_root = tmp_path / "configuration"
    existing_dir = config_root / "Collaboratori"
    existing_dir.mkdir(parents=True)
    (existing_dir / "config.yaml").write_text("source:\n  type: csv\n", encoding="utf-8")

    payload = {
        "collection": "Collaboratori",
        "source_type": "mysql",
        "port": 3306,
        "host_env_var": "MYSQL_HOST",
        "db_env_var": "MYSQL_DB",
        "user_env_var": "MYSQL_USER",
        "password_env_var": "MYSQL_PASSWORD",
        "from_table": "users",
        "fields": ["id"],
        "id_field": "id",
        "text_fields": [],
        "metadata_fields": [],
        "output_fields": [],
    }

    with (
        patch("api.config._CONFIG_ROOT", config_root),
        patch("api.upload._CONFIG_ROOT", config_root),
    ):
        resp = TestClient(_make_app_with_auth()).post("/config", json=payload)

    assert resp.status_code == 409
    assert "Collaboratori" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 11: POST /config 422 on invalid collection name
# ---------------------------------------------------------------------------

def test_post_config_422_invalid_name(tmp_path):
    """POST /config with collection='../etc' returns 422."""
    payload = {
        "collection": "../etc",
        "source_type": "mysql",
        "port": 3306,
        "host_env_var": "MYSQL_HOST",
        "db_env_var": "MYSQL_DB",
        "user_env_var": "MYSQL_USER",
        "password_env_var": "MYSQL_PASSWORD",
        "from_table": "users",
        "fields": ["id"],
        "id_field": "id",
        "text_fields": [],
        "metadata_fields": [],
        "output_fields": [],
    }

    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).post("/config", json=payload)

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test 12: POST /config 422 on invalid env var name
# ---------------------------------------------------------------------------

def test_post_config_422_invalid_env_var(tmp_path):
    """POST /config with host_env_var='lowercase' returns 422."""
    payload = {
        "collection": "ValidName",
        "source_type": "mysql",
        "port": 3306,
        "host_env_var": "lowercase",  # does not match _ENV_VAR_RE
        "db_env_var": "MYSQL_DB",
        "user_env_var": "MYSQL_USER",
        "password_env_var": "MYSQL_PASSWORD",
        "from_table": "users",
        "fields": ["id"],
        "id_field": "id",
        "text_fields": [],
        "metadata_fields": [],
        "output_fields": [],
    }

    with patch("api.config._CONFIG_ROOT", tmp_path / "configuration"):
        resp = TestClient(_make_app_with_auth()).post("/config", json=payload)

    assert resp.status_code == 422
