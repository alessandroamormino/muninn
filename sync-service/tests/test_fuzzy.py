"""Unit tests for vector_stores.fuzzy — Phase 23.

Tests cover:
- Levenshtein-1 variants generated and capped at 5
- No variants when term already matches exactly
- Italian morphing: -o/-a/-e/-i suffix rules
- _apply_fuzzy_expansion skips queries > 2 terms (Pitfall 5)
- Empty vocab returns unchanged query
- Graceful fallback when python-Levenshtein not installed (_LEV_AVAILABLE=False)
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Import helper (allows test to be discovered even before implementation exists)
# ---------------------------------------------------------------------------

def _import_fuzzy():
    from vector_stores.fuzzy import (
        _levenshtein_variants,
        _italian_variants,
        _apply_fuzzy_expansion,
    )
    return _levenshtein_variants, _italian_variants, _apply_fuzzy_expansion


# ---------------------------------------------------------------------------
# TestLevenshteinVariants
# ---------------------------------------------------------------------------

class TestLevenshteinVariants:
    def test_returns_within_edit_distance(self):
        """'tavolo' → finds 'tavola' (1 edit) in vocab."""
        _lev, _, _ = _import_fuzzy()
        vocab = {"tavola", "tavolo", "casa", "libro"}
        result = _lev("tavolo", vocab)
        assert "tavola" in result
        assert "tavolo" not in result  # exact match excluded

    def test_capped_at_5(self):
        """Returns at most 5 variants even if vocab has more candidates."""
        _lev, _, _ = _import_fuzzy()
        # single-char strings are all edit-distance 1 from 'a'
        vocab = set("bcdefghijk")
        result = _lev("a", vocab, cap=5)
        assert len(result) <= 5

    def test_empty_vocab_returns_empty(self):
        """Empty vocab → empty list."""
        _lev, _, _ = _import_fuzzy()
        assert _lev("test", set()) == []

    def test_exact_match_excluded(self):
        """The term itself is never included in results."""
        _lev, _, _ = _import_fuzzy()
        vocab = {"test", "tests", "jest", "best"}
        result = _lev("test", vocab)
        assert "test" not in result

    def test_no_variants_when_lev_unavailable(self, monkeypatch):
        """When _LEV_AVAILABLE is False, returns empty list gracefully."""
        _lev, _, _ = _import_fuzzy()
        monkeypatch.setattr("vector_stores.fuzzy._LEV_AVAILABLE", False)
        vocab = {"tavola", "libro"}
        result = _lev("tavolo", vocab)
        assert result == []

    def test_returns_list_of_strings(self):
        """Returns a list of strings (not a set or other type)."""
        _lev, _, _ = _import_fuzzy()
        vocab = {"tavola", "libro"}
        result = _lev("tavolo", vocab)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestItalianVariants
# ---------------------------------------------------------------------------

class TestItalianVariants:
    def test_o_to_i(self):
        """libro → libri (maschile plurale)."""
        _, _it, _ = _import_fuzzy()
        assert "libri" in _it("libro")

    def test_a_to_e(self):
        """tavola → tavole (femminile plurale)."""
        _, _it, _ = _import_fuzzy()
        assert "tavole" in _it("tavola")

    def test_e_to_i(self):
        """chiave → chiavi."""
        _, _it, _ = _import_fuzzy()
        assert "chiavi" in _it("chiave")

    def test_i_to_o_and_a(self):
        """libri → contains both 'libro' and 'libra' (-i → [-o, -a])."""
        _, _it, _ = _import_fuzzy()
        result = _it("libri")
        assert "libro" in result
        assert "libra" in result

    def test_invariant_not_duplicated(self):
        """Output never contains the input term itself."""
        _, _it, _ = _import_fuzzy()
        for term in ["libro", "tavola", "chiave", "libri"]:
            assert term not in _it(term)


# ---------------------------------------------------------------------------
# TestApplyFuzzyExpansion
# ---------------------------------------------------------------------------

class TestApplyFuzzyExpansion:
    def test_skips_long_queries(self):
        """Queries with > 2 terms are returned unchanged (Pitfall 5)."""
        _, _, _apply = _import_fuzzy()
        vocab = {"tavola", "libro", "casa"}
        result = _apply("tavolo legno massiccio vero", vocab)
        assert result == "tavolo legno massiccio vero"

    def test_expands_single_term(self):
        """Single-term query is expanded with Levenshtein-1 variants."""
        _, _, _apply = _import_fuzzy()
        vocab = {"tavola", "tavolo", "libro"}
        result = _apply("tavolo", vocab)
        # 'tavola' is 1 edit from 'tavolo'
        assert "tavola" in result

    def test_expands_two_term_query(self):
        """Two-term query expands per term."""
        _, _, _apply = _import_fuzzy()
        vocab = {"tavola", "libra", "tavolo", "libro"}
        result = _apply("tavolo libro", vocab)
        assert "tavola" in result or "libra" in result

    def test_lang_it_enables_italian_morphing(self):
        """lang='it' enables Italian morphing in addition to Levenshtein."""
        _, _, _apply = _import_fuzzy()
        vocab: set[str] = set()  # no Levenshtein candidates
        result = _apply("tavolo", vocab, lang="it")
        # Italian: tavolo → tavoli
        assert "tavoli" in result

    def test_lang_en_skips_italian_morphing(self):
        """lang='en' skips Italian morphing."""
        _, _, _apply = _import_fuzzy()
        vocab: set[str] = set()
        result = _apply("tavolo", vocab, lang="en")
        # No Italian morph, no Levenshtein (empty vocab) → unchanged
        assert result == "tavolo"

    def test_empty_vocab_lang_en_returns_unchanged(self):
        """Empty vocab + lang='en' → query unchanged."""
        _, _, _apply = _import_fuzzy()
        result = _apply("test", set(), lang="en")
        assert result == "test"

    def test_exact_three_terms_unchanged(self):
        """Exactly 3 terms → unchanged (guard at > 2)."""
        _, _, _apply = _import_fuzzy()
        vocab = {"tavola", "libra"}
        result = _apply("uno due tre", vocab)
        assert result == "uno due tre"
