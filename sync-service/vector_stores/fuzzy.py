"""Fuzzy (Levenshtein-1) query expansion for Qdrant search (Phase 23).

Provides:
  _levenshtein_variants(term, vocab, max_edits, cap) -> list[str]
  _italian_variants(term)                             -> list[str]
  _apply_fuzzy_expansion(query, vocab, lang, cap)     -> str

Only active for fts/bm25/hybrid Qdrant modes. Only applied for queries
with 1-2 terms (Pitfall 5: longer queries skip fuzzy to avoid score dilution).

Security:
  T-23-02-02: DoS guard — _apply_fuzzy_expansion returns query unchanged when
  len(terms) > 2. Per-term cap of 5 variants limits expansion size.

Graceful fallback: if python-Levenshtein is not installed, _levenshtein_variants
returns [] and _apply_fuzzy_expansion falls back to Italian morphing only (or
returns query unchanged if lang != 'it').
"""
from __future__ import annotations

try:
    import Levenshtein as _lev  # type: ignore[import]
    _LEV_AVAILABLE = True
except ImportError:
    _lev = None  # type: ignore[assignment]
    _LEV_AVAILABLE = False


def _levenshtein_variants(
    term: str,
    vocab: set[str],
    max_edits: int = 1,
    cap: int = 5,
) -> list[str]:
    """Return up to cap vocabulary words within max_edits Levenshtein distance of term.

    Args:
        term:      query token to find variants for
        vocab:     set of known words to search within
        max_edits: maximum edit distance to consider (default: 1)
        cap:       maximum number of variants to return (default: 5)

    Returns:
        List of vocabulary words within max_edits of term (exact match excluded),
        capped at cap results. Returns [] if python-Levenshtein not installed.
    """
    if not _LEV_AVAILABLE:
        return []
    results = [
        w for w in vocab
        if w != term and _lev.distance(term, w) <= max_edits
    ]
    return results[:cap]


def _italian_variants(term: str) -> list[str]:
    """Generate Italian singular/plural variants via 4 suffix rules.

    Rules:
      -o  -> -i  (maschile: libro -> libri)
      -a  -> -e  (femminile: tavola -> tavole)
      -e  -> -i  (chiave -> chiavi)
      -i  -> -o, -a  (libri -> libro, libra)

    Pure Python — no dependencies. Works regardless of python-Levenshtein availability.

    Args:
        term: input word (lowercased internally)

    Returns:
        List of morphological variants; input term is never included in output.
    """
    t = term.lower()
    variants: list[str] = []
    if t.endswith("o"):
        variants.append(t[:-1] + "i")
    elif t.endswith("a"):
        variants.append(t[:-1] + "e")
    elif t.endswith("e"):
        variants.append(t[:-1] + "i")
    elif t.endswith("i"):
        variants.extend([t[:-1] + "o", t[:-1] + "a"])
    return [v for v in variants if v != t]


def _apply_fuzzy_expansion(
    query: str,
    vocab: set[str],
    lang: str = "en",
    cap: int = 5,
) -> str:
    """Expand query with Levenshtein-1 and (if lang='it') Italian morphing variants.

    Guard (T-23-02-02): only applies to 1-2 term queries (Pitfall 5).
    Returns query unchanged if len(terms) > 2 or no variants found.

    Args:
        query: raw query string (split on whitespace into terms)
        vocab: vocabulary set for Levenshtein matching
        lang:  language code — 'it' enables Italian morphing in addition to Levenshtein
        cap:   max Levenshtein variants per term (default: 5)

    Returns:
        Expanded query string (identical to query if no variants found or >2 terms).
    """
    terms = query.split()
    # T-23-02-02: hard guard — skip fuzzy for long queries
    if len(terms) > 2:
        return query
    original_tokens = set(terms)
    extras: list[str] = []
    for term in terms:
        variants = _levenshtein_variants(term, vocab, cap=cap)
        if lang == "it":
            italian = [v for v in _italian_variants(term) if v not in variants]
            variants = variants + italian
        # Exclude variants already present as tokens of the original query
        for v in variants:
            if v not in original_tokens and v not in extras:
                extras.append(v)
    if extras:
        return query + " " + " ".join(extras)
    return query
