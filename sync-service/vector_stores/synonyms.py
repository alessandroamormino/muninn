"""Synonym expansion helpers for Qdrant search (VS-05, D-11, D-12, D-13).

Provides:
  _load_synonyms(config_root, collection) → list[list[str]]
  _expand_query(q, synonym_groups)        → str

Synonyms are per-entity optional files:  configuration/{entity}/synonyms.yaml
Format: list of equivalence groups (bidirectional).

Example synonyms.yaml:
  - [auto, automobile, macchina, vettura]
  - [CV, curriculum, resume]
  - [sviluppatore, developer, programmatore]

Security: _load_synonyms applies a path-traversal guard via _COLLECTION_RE
before constructing the filesystem path — same pattern as api/graph.py T-11-01.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# Path-traversal guard: same pattern as api/graph.py and api/search.py (T-11-01).
# Collection names must be alphanumeric + underscore + hyphen only.
_COLLECTION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _load_synonyms(config_root: Path, collection: str) -> list[list[str]]:
    """Load synonyms.yaml for a named entity.

    Returns empty list if:
    - collection name fails _COLLECTION_RE (path traversal guard)
    - synonyms.yaml does not exist for this entity
    - YAML is malformed or empty

    Args:
        config_root: base configuration directory (e.g. Path("/app/configuration"))
        collection:  entity name (validated — must match ^[a-zA-Z0-9_-]+$)

    Returns:
        list of equivalence groups, each group being a list of synonym strings.
    """
    if not _COLLECTION_RE.match(collection):
        # Reject names with path separators, spaces, or special chars.
        return []
    path = config_root / collection / "synonyms.yaml"
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text()) or []
        return data
    except Exception:  # noqa: BLE001
        return []


def _expand_query(q: str, synonym_groups: list[list[str]]) -> str:
    """Bidirectional token-level synonym expansion.

    For each synonym group where at least one token is present in q (case-insensitive),
    appends all other synonyms in the group that are NOT already in q.

    Example:
        _expand_query("auto rossa", [["auto", "macchina", "automobile"]])
        → "auto rossa macchina automobile"

    Applied before sending q to Qdrant for all search modes (fts, bm25, hybrid).
    Weaviate does not use this function — it has built-in BM25 without synonym support.

    Args:
        q:               raw query string
        synonym_groups:  list of groups from _load_synonyms()

    Returns:
        expanded query string (identical to q if no synonyms match)
    """
    tokens = set(q.lower().split())
    extras: list[str] = []
    for group in synonym_groups:
        group_lower = [s.lower() for s in group]
        if any(t in tokens for t in group_lower):
            for s in group_lower:
                if s not in tokens:
                    extras.append(s)
    if extras:
        return q + " " + " ".join(extras)
    return q
