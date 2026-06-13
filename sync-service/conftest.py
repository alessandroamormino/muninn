"""Stub the PyPI 'weaviate' package so tests run on the host without it installed.

After renaming the local weaviate/ package to weaviate_store/, the PyPI weaviate
package is no longer shadowed. This conftest pre-populates sys.modules so that
`import weaviate` and `import weaviate.classes.*` succeed on the host dev machine
where weaviate-client is not installed (it runs only inside Docker).
"""
import sys
from unittest.mock import MagicMock

for _mod in (
    "weaviate",
    "weaviate.classes",
    "weaviate.classes.config",
    "weaviate.classes.query",
    "weaviate.classes.tenants",
    "weaviate.classes.data",
    "weaviate.exceptions",
):
    sys.modules.setdefault(_mod, MagicMock())

# ---------------------------------------------------------------------------
# Stub openai v2 SDK surface for host tests (host has openai==0.28.1 which
# lacks openai.OpenAI, openai.RateLimitError, etc. required by Phase 25).
# Inside Docker the real openai>=1.0.0 is installed via requirements.txt.
# ---------------------------------------------------------------------------
try:
    import openai as _openai_mod  # noqa: F401
except ImportError:
    _openai_mod = MagicMock()
    sys.modules["openai"] = _openai_mod

# Ensure real Exception subclasses exist on the module (MagicMock instances are
# NOT catchable in `except` blocks, so we define real classes as stubs).
if not hasattr(_openai_mod, "RateLimitError"):
    class _StubRateLimitError(Exception):
        """Stub for openai.RateLimitError (host has v0 SDK without this class)."""
    _openai_mod.RateLimitError = _StubRateLimitError

if not hasattr(_openai_mod, "APIConnectionError"):
    class _StubAPIConnectionError(Exception):
        """Stub for openai.APIConnectionError (host has v0 SDK without this class)."""
    _openai_mod.APIConnectionError = _StubAPIConnectionError

if not hasattr(_openai_mod, "APIStatusError"):
    class _StubAPIStatusError(Exception):
        """Stub for openai.APIStatusError (host has v0 SDK without this class)."""
    _openai_mod.APIStatusError = _StubAPIStatusError

if not hasattr(_openai_mod, "OpenAI"):
    _openai_mod.OpenAI = MagicMock()
