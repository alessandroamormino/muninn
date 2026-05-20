"""Test-suite conftest for sync-service.

Patches auth.user_store._PWD_CTX to use pbkdf2_sha256 on hosts where bcrypt >= 4.0
is installed. passlib 1.7.4 is not compatible with bcrypt 4.x/5.x (detect_wrap_bug
raises ValueError on passwords > 72 bytes). The Docker container pins bcrypt < 4.0
via requirements.txt; on developer machines with newer bcrypt this swap ensures
tests run without errors and without changing application code.
"""
from __future__ import annotations

import bcrypt as _bcrypt_lib
import pytest
from packaging.version import Version


def _bcrypt_too_new() -> bool:
    try:
        return Version(_bcrypt_lib.__version__) >= Version("4.0.0")
    except Exception:
        return False


if _bcrypt_too_new():
    from passlib.context import CryptContext
    import auth.user_store as _user_store_mod

    _COMPAT_CTX = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
    _user_store_mod._PWD_CTX = _COMPAT_CTX
    # Recompute dummy hash with the new context so verify_password stays consistent
    _user_store_mod._DUMMY_HASH = _COMPAT_CTX.hash("dummy")

# Disable slowapi rate limiting for the test session.
# The _limiter instance in api.auth is a module-level singleton shared across all
# test classes; without disabling it, login calls accumulate and trip the 10/min
# limit mid-suite, causing 429 on subsequent tests.
import api.auth as _auth_mod
_auth_mod._limiter.enabled = False
