"""Tests for Phase 12 — JWT authentication, admin endpoints, role matrix enforcement.

Uses FastAPI TestClient with a minimal in-memory app that mirrors the real app.state
setup (UserStore, RefreshTokenStore, tmp_tokens) without the Weaviate/settings stack.
All tests use real SQLite via tmp_path — no mocking of auth logic.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import pyotp
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user, require_admin
from auth.user_store import RefreshTokenStore, UserRecord, UserStore
from api.auth import _limiter, router as auth_router
from api.admin import router as admin_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_SECRET = "a" * 32  # 32-char secret satisfies the CR-01 startup guard


@pytest.fixture
def store(tmp_path: Path) -> Generator[UserStore, None, None]:
    s = UserStore(tmp_path / "users.db")
    yield s
    s.close()


@pytest.fixture
def token_store(store: UserStore) -> RefreshTokenStore:
    return RefreshTokenStore(store._conn)


def _make_test_app(user_store: UserStore, token_store: RefreshTokenStore) -> FastAPI:
    """Minimal FastAPI app with auth/admin routers and correct app.state."""
    app = FastAPI()
    app.state.user_store = user_store
    app.state.token_store = token_store
    app.state.tmp_tokens = {}
    # Register limiter (slowapi requires this for @_limiter.limit to work)
    app.state.limiter = _limiter

    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


@pytest.fixture
def app(store: UserStore, token_store: RefreshTokenStore) -> FastAPI:
    return _make_test_app(store, token_store)


@pytest.fixture
def client(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("JWT_SECRET", _TEST_SECRET)
    # Patch _JWT_SECRET in both modules that read it at import time
    import api.auth as auth_mod
    import auth.dependencies as dep_mod
    monkeypatch.setattr(auth_mod, "_JWT_SECRET", _TEST_SECRET)
    monkeypatch.setattr(dep_mod, "_JWT_SECRET", _TEST_SECRET)
    return TestClient(app)


@pytest.fixture
def admin_user(store: UserStore) -> UserRecord:
    return store.create_user("admin", "Password1!", "admin")


@pytest.fixture
def reader_user(store: UserStore) -> UserRecord:
    return store.create_user("reader", "Password1!", "reader")


def _login(client: TestClient, username: str = "admin", password: str = "Password1!") -> dict:
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# UserStore unit tests
# ---------------------------------------------------------------------------

class TestUserStore:
    def test_create_and_retrieve(self, store: UserStore) -> None:
        u = store.create_user("alice", "secret", "reader")
        assert u.username == "alice"
        assert u.role == "reader"
        assert u.is_active is True
        assert u.hashed_password != "secret"

    def test_is_empty_true_on_new_store(self, store: UserStore) -> None:
        assert store.is_empty() is True

    def test_is_empty_false_after_insert(self, store: UserStore) -> None:
        store.create_user("alice", "secret", "reader")
        assert store.is_empty() is False

    def test_duplicate_username_raises(self, store: UserStore) -> None:
        store.create_user("alice", "secret", "reader")
        with pytest.raises(ValueError, match="already exists"):
            store.create_user("alice", "other", "admin")

    def test_invalid_role_raises(self, store: UserStore) -> None:
        with pytest.raises(ValueError, match="Invalid role"):
            store.create_user("alice", "secret", "superuser")

    def test_verify_password_correct(self, store: UserStore) -> None:
        store.create_user("alice", "correct", "reader")
        result = store.verify_password("alice", "correct")
        assert result is not None
        assert result.username == "alice"

    def test_verify_password_wrong(self, store: UserStore) -> None:
        store.create_user("alice", "correct", "reader")
        assert store.verify_password("alice", "wrong") is None

    def test_verify_password_unknown_user(self, store: UserStore) -> None:
        # Must not raise — runs dummy bcrypt for timing safety (WR-06)
        assert store.verify_password("ghost", "anything") is None

    def test_verify_password_inactive_user(self, store: UserStore) -> None:
        store.create_user("alice", "correct", "reader")
        store.deactivate_user("alice")
        assert store.verify_password("alice", "correct") is None

    def test_get_by_id(self, store: UserStore) -> None:
        u = store.create_user("alice", "secret", "reader")
        fetched = store.get_by_id(u.id)
        assert fetched is not None
        assert fetched.username == "alice"

    def test_get_by_id_unknown(self, store: UserStore) -> None:
        assert store.get_by_id(999) is None

    def test_update_user_role(self, store: UserStore) -> None:
        store.create_user("alice", "secret", "reader")
        ok = store.update_user("alice", role="admin")
        assert ok is True
        assert store.get_by_username("alice").role == "admin"

    def test_update_user_unknown_returns_false(self, store: UserStore) -> None:
        # CR-04 fix: rowcount must return False for missing rows
        assert store.update_user("ghost", role="admin") is False

    def test_deactivate_user(self, store: UserStore) -> None:
        store.create_user("alice", "secret", "reader")
        assert store.deactivate_user("alice") is True
        assert store.get_by_username("alice").is_active is False

    def test_deactivate_user_unknown_returns_false(self, store: UserStore) -> None:
        # CR-04 fix: rowcount must return False for missing rows
        assert store.deactivate_user("ghost") is False

    def test_list_users(self, store: UserStore) -> None:
        store.create_user("a", "p", "reader")
        store.create_user("b", "p", "admin")
        users = store.list_users()
        assert len(users) == 2
        assert {u.username for u in users} == {"a", "b"}


class TestRefreshTokenStore:
    def test_save_and_get_valid(self, store: UserStore, token_store: RefreshTokenStore) -> None:
        u = store.create_user("alice", "p", "reader")
        exp = (datetime.now(tz=timezone.utc) + timedelta(days=7)).isoformat()
        token_store.save("my-token", u.id, exp)
        row = token_store.get_valid("my-token")
        assert row is not None
        assert row["user_id"] == u.id

    def test_get_valid_expired(self, store: UserStore, token_store: RefreshTokenStore) -> None:
        u = store.create_user("alice", "p", "reader")
        exp = (datetime.now(tz=timezone.utc) - timedelta(seconds=1)).isoformat()
        token_store.save("old-token", u.id, exp)
        assert token_store.get_valid("old-token") is None

    def test_revoke(self, store: UserStore, token_store: RefreshTokenStore) -> None:
        u = store.create_user("alice", "p", "reader")
        exp = (datetime.now(tz=timezone.utc) + timedelta(days=7)).isoformat()
        token_store.save("my-token", u.id, exp)
        assert token_store.revoke("my-token") is True
        assert token_store.get_valid("my-token") is None

    def test_revoke_unknown_returns_false(self, token_store: RefreshTokenStore) -> None:
        # CR-04 fix: rowcount must return False for missing tokens
        assert token_store.revoke("nonexistent-token") is False


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_valid_credentials_returns_tokens(
        self, client: TestClient, admin_user: UserRecord
    ) -> None:
        data = _login(client)
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_wrong_password_returns_401(
        self, client: TestClient, admin_user: UserRecord
    ) -> None:
        r = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

    def test_unknown_user_returns_401(self, client: TestClient) -> None:
        r = client.post("/auth/login", json={"username": "ghost", "password": "anything"})
        assert r.status_code == 401

    def test_inactive_user_returns_401(
        self, client: TestClient, store: UserStore
    ) -> None:
        store.create_user("inactive", "Password1!", "reader")
        store.deactivate_user("inactive")
        r = client.post("/auth/login", json={"username": "inactive", "password": "Password1!"})
        assert r.status_code == 401

    def test_totp_enabled_returns_challenge(
        self, client: TestClient, store: UserStore
    ) -> None:
        u = store.create_user("totp_admin", "Password1!", "admin")
        secret = pyotp.random_base32()
        store.update_user("totp_admin", totp_secret=secret, totp_enabled=True)
        r = client.post("/auth/login", json={"username": "totp_admin", "password": "Password1!"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "totp_required"
        assert "tmp_token" in data


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_valid_refresh_returns_new_tokens(
        self, client: TestClient, admin_user: UserRecord
    ) -> None:
        data = _login(client)
        r = client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert r.status_code == 200
        refreshed = r.json()
        assert "access_token" in refreshed
        assert "refresh_token" in refreshed
        # New refresh token must differ (token rotation — WR-02)
        assert refreshed["refresh_token"] != data["refresh_token"]

    def test_consumed_refresh_token_rejected(
        self, client: TestClient, admin_user: UserRecord
    ) -> None:
        data = _login(client)
        client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
        # Second use of same token must fail (WR-02 rotation)
        r = client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert r.status_code == 401

    def test_invalid_refresh_returns_401(self, client: TestClient) -> None:
        r = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------

class TestLogout:
    def test_logout_revokes_refresh_token(
        self, client: TestClient, admin_user: UserRecord
    ) -> None:
        data = _login(client)
        r = client.post("/auth/logout", json={"refresh_token": data["refresh_token"]})
        assert r.status_code == 200
        assert r.json()["status"] == "logged_out"
        # Revoked token must no longer be valid
        r2 = client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert r2.status_code == 401


# ---------------------------------------------------------------------------
# TOTP confirm flow
# ---------------------------------------------------------------------------

class TestTOTPConfirm:
    def _setup_totp_user(self, store: UserStore) -> tuple[str, str]:
        """Create user with TOTP enabled. Returns (username, totp_secret)."""
        store.create_user("totp_user", "Password1!", "admin")
        secret = pyotp.random_base32()
        store.update_user("totp_user", totp_secret=secret, totp_enabled=True)
        return "totp_user", secret

    def test_valid_totp_confirm_returns_tokens(
        self, client: TestClient, store: UserStore
    ) -> None:
        username, secret = self._setup_totp_user(store)
        # Step 1: login returns tmp_token
        r1 = client.post("/auth/login", json={"username": username, "password": "Password1!"})
        assert r1.json()["status"] == "totp_required"
        tmp_token = r1.json()["tmp_token"]
        # Step 2: confirm with valid TOTP code
        code = pyotp.TOTP(secret).now()
        r2 = client.post("/auth/totp/confirm", json={"tmp_token": tmp_token, "totp_code": code})
        assert r2.status_code == 200
        assert "access_token" in r2.json()
        assert "refresh_token" in r2.json()

    def test_invalid_totp_code_returns_401(
        self, client: TestClient, store: UserStore
    ) -> None:
        username, _ = self._setup_totp_user(store)
        r1 = client.post("/auth/login", json={"username": username, "password": "Password1!"})
        tmp_token = r1.json()["tmp_token"]
        r2 = client.post("/auth/totp/confirm", json={"tmp_token": tmp_token, "totp_code": "000000"})
        assert r2.status_code == 401

    def test_unknown_tmp_token_returns_401(self, client: TestClient) -> None:
        r = client.post("/auth/totp/confirm", json={"tmp_token": "ghost", "totp_code": "123456"})
        assert r.status_code == 401

    def test_tmp_token_consumed_after_success(
        self, client: TestClient, store: UserStore
    ) -> None:
        username, secret = self._setup_totp_user(store)
        r1 = client.post("/auth/login", json={"username": username, "password": "Password1!"})
        tmp_token = r1.json()["tmp_token"]
        code = pyotp.TOTP(secret).now()
        client.post("/auth/totp/confirm", json={"tmp_token": tmp_token, "totp_code": code})
        # Second use of same tmp_token must fail
        r3 = client.post("/auth/totp/confirm", json={"tmp_token": tmp_token, "totp_code": code})
        assert r3.status_code == 401

    def test_totp_enforced_for_reader_too(
        self, client: TestClient, store: UserStore
    ) -> None:
        # WR-05 fix: TOTP enforced for any totp_enabled=True user, not only admins
        store.create_user("totp_reader", "Password1!", "reader")
        secret = pyotp.random_base32()
        store.update_user("totp_reader", totp_secret=secret, totp_enabled=True)
        r = client.post("/auth/login", json={"username": "totp_reader", "password": "Password1!"})
        assert r.status_code == 200
        assert r.json()["status"] == "totp_required"

    def test_expired_tmp_tokens_purged_on_login(
        self, client: TestClient, store: UserStore, app: FastAPI
    ) -> None:
        # CR-05 fix: expired entries removed from tmp_tokens dict
        store.create_user("totp_user2", "Password1!", "admin")
        secret = pyotp.random_base32()
        store.update_user("totp_user2", totp_secret=secret, totp_enabled=True)
        # Manually inject an already-expired entry
        app.state.tmp_tokens["stale"] = {
            "username": "totp_user2",
            "expires_at": datetime.now(tz=timezone.utc) - timedelta(minutes=5),
        }
        assert "stale" in app.state.tmp_tokens
        # Trigger purge via /login
        client.post("/auth/login", json={"username": "totp_user2", "password": "Password1!"})
        assert "stale" not in app.state.tmp_tokens


# ---------------------------------------------------------------------------
# GET /auth/token dependency — JWT validation
# ---------------------------------------------------------------------------

class TestJWTDependency:
    def _protected_app(self, user_store: UserStore) -> tuple[FastAPI, TestClient]:
        """Mini-app with a single protected endpoint for dependency testing."""
        import api.auth as auth_mod
        import auth.dependencies as dep_mod

        app = FastAPI()
        app.state.user_store = user_store
        token_store = RefreshTokenStore(user_store._conn)
        app.state.token_store = token_store
        app.state.tmp_tokens = {}
        app.state.limiter = _limiter

        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

        app.include_router(auth_router)
        app.include_router(admin_router)

        from fastapi import Depends
        @app.get("/protected")
        async def protected(user: UserRecord = Depends(get_current_user)) -> dict:
            return {"username": user.username, "role": user.role}

        @app.get("/admin-only")
        async def admin_only(user: UserRecord = Depends(require_admin)) -> dict:
            return {"ok": True}

        return app, TestClient(app)

    def test_missing_header_returns_401(
        self, store: UserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import auth.dependencies as dep_mod
        monkeypatch.setattr(dep_mod, "_JWT_SECRET", _TEST_SECRET)
        _, client = self._protected_app(store)
        r = client.get("/protected")
        assert r.status_code == 401

    def test_valid_token_passes(
        self, store: UserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import api.auth as auth_mod
        import auth.dependencies as dep_mod
        monkeypatch.setenv("JWT_SECRET", _TEST_SECRET)
        monkeypatch.setattr(auth_mod, "_JWT_SECRET", _TEST_SECRET)
        monkeypatch.setattr(dep_mod, "_JWT_SECRET", _TEST_SECRET)
        store.create_user("alice", "Password1!", "reader")
        app, c = self._protected_app(store)
        login_r = c.post("/auth/login", json={"username": "alice", "password": "Password1!"})
        token = login_r.json()["access_token"]
        r = c.get("/protected", headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["username"] == "alice"

    def test_reader_on_admin_endpoint_returns_403(
        self, store: UserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import api.auth as auth_mod
        import auth.dependencies as dep_mod
        monkeypatch.setenv("JWT_SECRET", _TEST_SECRET)
        monkeypatch.setattr(auth_mod, "_JWT_SECRET", _TEST_SECRET)
        monkeypatch.setattr(dep_mod, "_JWT_SECRET", _TEST_SECRET)
        store.create_user("reader", "Password1!", "reader")
        _, c = self._protected_app(store)
        login_r = c.post("/auth/login", json={"username": "reader", "password": "Password1!"})
        token = login_r.json()["access_token"]
        r = c.get("/admin-only", headers=_auth_header(token))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

class TestAdminUsers:
    def _admin_token(self, client: TestClient, store: UserStore) -> str:
        store.create_user("admin", "Password1!", "admin")
        return _login(client)["access_token"]

    def test_create_reader_user(
        self, client: TestClient, store: UserStore
    ) -> None:
        token = self._admin_token(client, store)
        r = client.post(
            "/admin/users",
            json={"username": "newreader", "password": "Pass1!", "role": "reader"},
            headers=_auth_header(token),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["username"] == "newreader"
        assert data["role"] == "reader"
        assert "hashed_password" not in data

    def test_list_users_excludes_password(
        self, client: TestClient, store: UserStore
    ) -> None:
        token = self._admin_token(client, store)
        r = client.get("/admin/users", headers=_auth_header(token))
        assert r.status_code == 200
        for u in r.json()["users"]:
            assert "hashed_password" not in u

    def test_deactivate_other_user(
        self, client: TestClient, store: UserStore
    ) -> None:
        token = self._admin_token(client, store)
        store.create_user("victim", "Password1!", "reader")
        r = client.delete("/admin/users/victim", headers=_auth_header(token))
        assert r.status_code == 200
        assert store.get_by_username("victim").is_active is False

    def test_deactivate_self_returns_400(
        self, client: TestClient, store: UserStore
    ) -> None:
        # CR-02 fix: admin cannot deactivate own account
        token = self._admin_token(client, store)
        r = client.delete("/admin/users/admin", headers=_auth_header(token))
        assert r.status_code == 400
        assert "own account" in r.json()["detail"].lower()

    def test_deactivate_nonexistent_returns_404(
        self, client: TestClient, store: UserStore
    ) -> None:
        token = self._admin_token(client, store)
        r = client.delete("/admin/users/ghost", headers=_auth_header(token))
        assert r.status_code == 404

    def test_demote_self_returns_400(
        self, client: TestClient, store: UserStore
    ) -> None:
        # CR-03 fix: admin cannot demote own role
        token = self._admin_token(client, store)
        r = client.put(
            "/admin/users/admin",
            json={"role": "reader"},
            headers=_auth_header(token),
        )
        assert r.status_code == 400
        assert "demote" in r.json()["detail"].lower()

    def test_update_other_user_role(
        self, client: TestClient, store: UserStore
    ) -> None:
        token = self._admin_token(client, store)
        store.create_user("victim", "Password1!", "reader")
        r = client.put(
            "/admin/users/victim",
            json={"role": "admin"},
            headers=_auth_header(token),
        )
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_reader_cannot_access_admin_endpoints(
        self, client: TestClient, store: UserStore
    ) -> None:
        store.create_user("admin", "Password1!", "admin")
        store.create_user("reader", "Password1!", "reader")
        token = _login(client, "reader", "Password1!")["access_token"]
        r = client.get("/admin/users", headers=_auth_header(token))
        assert r.status_code == 403

    def test_unauthenticated_admin_returns_401(self, client: TestClient) -> None:
        r = client.get("/admin/users")
        assert r.status_code == 401

    def test_duplicate_username_returns_400(
        self, client: TestClient, store: UserStore
    ) -> None:
        token = self._admin_token(client, store)
        client.post(
            "/admin/users",
            json={"username": "dup", "password": "Pass1!", "role": "reader"},
            headers=_auth_header(token),
        )
        r = client.post(
            "/admin/users",
            json={"username": "dup", "password": "Pass1!", "role": "reader"},
            headers=_auth_header(token),
        )
        assert r.status_code == 400
