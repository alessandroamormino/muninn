"""Synonym expansion helpers for Qdrant search (VS-05, D-11, D-12, D-13).

Provides:
  _load_synonyms(config_root, collection) → list[list[str]]
  _expand_query(q, synonym_groups)        → str
  _ensure_omw_downloaded(lang)            → bool   (Phase 23, OMW)
  _get_omw_synonyms(token, lang)          → list[str] (Phase 23, OMW)

Synonyms are per-entity optional files:  configuration/{entity}/synonyms.yaml
Format: list of equivalence groups (bidirectional).

Example synonyms.yaml:
  - [auto, automobile, macchina, vettura]
  - [CV, curriculum, resume]
  - [sviluppatore, developer, programmatore]

Security: _load_synonyms applies a path-traversal guard via _COLLECTION_RE
before constructing the filesystem path — same pattern as api/graph.py T-11-01.

Phase 23 OMW security (T-23-02-01): _ensure_omw_downloaded validates lang against
_OMW_LANG_MAP whitelist before calling wn.download — guards against lang injection.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# Path-traversal guard: same pattern as api/graph.py and api/search.py (T-11-01).
# Collection names must be alphanumeric + underscore + hyphen only.
_COLLECTION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Phase 23: optional wn (Open Multilingual Wordnet) import.
# Degrades gracefully if not installed.
try:
    import wn as _wn  # type: ignore[import]
    _WN_AVAILABLE = True
except ImportError:
    _wn = None  # type: ignore[assignment]
    _WN_AVAILABLE = False

# Whitelist of supported OMW lang codes → wn package ids (T-23-02-01).
# Only languages listed here can trigger a wn.download call.
_OMW_LANG_MAP: dict[str, str] = {
    "it": "omw-iwn:1.4",
    "en": "ewn:2020",
}


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


# ---------------------------------------------------------------------------
# Phase 23 — Open Multilingual Wordnet (OMW) helpers
# ---------------------------------------------------------------------------

def _ensure_omw_downloaded(lang: str) -> bool:
    """Download OMW package for lang if not cached. Returns True if available.

    Security (T-23-02-01): validates lang against _OMW_LANG_MAP whitelist before
    any I/O — mirrors _load_synonyms guard (_COLLECTION_RE) for path traversal.
    Unknown lang codes return False without attempting a download.

    Args:
        lang: language code (e.g. 'it', 'en')

    Returns:
        True if OMW package is available after this call; False on any error,
        missing lang, or if wn is not installed.
    """
    if not _WN_AVAILABLE:
        return False
    package = _OMW_LANG_MAP.get(lang.lower())
    if not package:
        # Unknown lang — whitelist guard, no I/O performed
        return False
    try:
        # wn.download is idempotent: skips silently if already cached
        _wn.download(package, progress=False)
        return True
    except Exception:  # noqa: BLE001 — network error, timeout, permission, etc.
        return False


def _get_omw_synonyms(token: str, lang: str) -> list[str]:
    """Return OMW synonym lemma strings for token in lang (capped at 10).

    Iterates wn.words(token) → synsets → lemmas for the given lang.
    Excludes the input token itself (case-insensitive).
    Deduplicates results preserving insertion order.
    Wrapped in try/except — returns [] on any wn error.

    Args:
        token: query token to look up synonyms for
        lang:  language code passed to wn (e.g. 'it', 'en')

    Returns:
        Up to 10 unique lemma strings (lowercased); empty list if wn unavailable
        or any error occurs.
    """
    if not _WN_AVAILABLE:
        return []
    results: list[str] = []
    try:
        for word in _wn.words(token, lang=lang):
            for synset in word.synsets():
                for lemma in synset.lemmas(lang=lang):
                    l_lower = lemma.lower()
                    if l_lower != token.lower() and l_lower not in results:
                        results.append(l_lower)
    except Exception:  # noqa: BLE001
        pass
    return results[:10]
