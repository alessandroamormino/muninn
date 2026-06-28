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
    """Generate Italian number (singular/plural) and gender (m/f) variants.

    Number rules:
      -o  -> -i  (libro -> libri)
      -a  -> -e  (tavola -> tavole)
      -e  -> -i  (chiave -> chiavi)
      -i  -> -o, -a, -e  (libri -> libro/libra; commerciali -> commerciale)

    Gender rules (maschile <-> femminile):
      -tore  -> -trice   (collaboratore -> collaboratrice)
      -trice -> -tore    (direttrice -> direttore)
      -essa  -> -e       (dottoressa -> dottore)
      -o     -> -a       (maestro -> maestra)
      -a     -> -o       (maestra -> maestro)
      -e     -> -essa    (dottore -> dottoressa)

    Pure Python — no dependencies. Works regardless of python-Levenshtein availability.
    Gender rules over-generate on non-person nouns (libro -> libra), but those land as
    extra OR terms that simply match nothing — recall up, precision cost negligible on
    the 1-2 term queries this is gated to.

    Args:
        term: input word (lowercased internally)

    Returns:
        List of morphological variants; input term is never included in output.
    """
    t = term.lower()
    variants: list[str] = []
    # number
    if t.endswith("o"):
        variants.append(t[:-1] + "i")
    elif t.endswith("a"):
        variants.append(t[:-1] + "e")
    elif t.endswith("e"):
        variants.append(t[:-1] + "i")
    elif t.endswith("i"):
        # plural -i can come from -o (libro), -a (rare), or -e (commerciale) singulars
        variants.extend([t[:-1] + "o", t[:-1] + "a", t[:-1] + "e"])
    # gender — independent rules: -tore is ambiguous (attore->attrice but
    # dottore->dottoressa), so both forms are emitted; the wrong one matches nothing.
    if t.endswith("tore"):
        variants.append(t[:-4] + "trice")
    if t.endswith("trice"):
        variants.append(t[:-5] + "tore")
    if t.endswith("essa"):
        variants.append(t[:-4] + "e")
    if t.endswith("o"):
        variants.append(t[:-1] + "a")
    elif t.endswith("a"):
        variants.append(t[:-1] + "o")
    elif t.endswith("e"):
        variants.append(t[:-1] + "essa")
    # dedupe, preserve order, exclude the input term itself
    out: list[str] = []
    for v in variants:
        if v != t and v not in out:
            out.append(v)
    return out


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
