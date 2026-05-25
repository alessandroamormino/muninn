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
