"""Test NormalizedCacheAdapter — TDD RED phase.

Verifica i comportamenti D-10 dal CONTEXT.md:
- Query morfologicamente equivalenti → stesso hash (cache hit)
- Negazioni → hash diverso (cache miss)
- Lemmi diversi → hash diverso
- spaCy import lazy (non a livello modulo)
"""
from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper: verifica che il modulo normalized.py NON importi spacy a livello
# modulo (D-11: lazy import all'init)
# ---------------------------------------------------------------------------

def test_normalized_module_does_not_import_spacy_at_module_level():
    """Il semplice import del modulo non deve caricare spaCy (D-11)."""
    # Rimuovi spacy dai moduli caricati per simulare ambiente fresco
    spacy_mods = [k for k in sys.modules if k.startswith("spacy")]
    saved = {k: sys.modules.pop(k) for k in spacy_mods}
    # Rimuovi anche il modulo normalized se già importato
    if "sync.cache_adapters.normalized" in sys.modules:
        del sys.modules["sync.cache_adapters.normalized"]
    try:
        import sync.cache_adapters.normalized  # noqa: F401
        # spaCy NON deve essere in sys.modules dopo il semplice import del modulo
        spacy_loaded = any(k.startswith("spacy") and not k.startswith("spacy_") for k in sys.modules)
        assert not spacy_loaded, "spaCy è stato importato a livello modulo — viola D-11"
    finally:
        sys.modules.update(saved)


# ---------------------------------------------------------------------------
# Test normalize_query (D-10)
# ---------------------------------------------------------------------------

def test_normalize_query_morphologically_equivalent():
    """D-10: 'chi ha lavorato in apping?' e 'chi lavora in apping?' → stesso hash."""
    from sync.cache_adapters.normalized import normalize_query

    n1 = normalize_query("chi ha lavorato in apping?")
    n2 = normalize_query("chi lavora in apping?")
    assert n1 == n2, f"Atteso uguale (morfologia equivalente): {n1!r} vs {n2!r}"


def test_normalize_query_negation_preserved():
    """D-10: 'chi NON lavora in apping?' → hash diverso (negazione preservata)."""
    from sync.cache_adapters.normalized import normalize_query

    n_pos = normalize_query("chi lavora in apping?")
    n_neg = normalize_query("chi NON lavora in apping?")
    assert n_neg != n_pos, f"Atteso diverso (negazione): {n_neg!r} vs {n_pos!r}"
    # 'non' deve essere presente nella versione normalizzata con negazione
    assert "non" in n_neg.split(), f"'non' deve essere in {n_neg!r}"


def test_normalize_query_different_lemma():
    """D-10: 'quali dipendenti in apping?' → hash diverso (lemma diverso da 'lavorare')."""
    from sync.cache_adapters.normalized import normalize_query

    n_lavora = normalize_query("chi lavora in apping?")
    n_dipendenti = normalize_query("quali dipendenti in apping?")
    assert n_dipendenti != n_lavora, (
        f"Atteso diverso (lemma diverso): {n_dipendenti!r} vs {n_lavora!r}"
    )


def test_negation_tokens_frozenset():
    """NEGATION_TOKENS deve essere un frozenset con i token attesi (D-09)."""
    from sync.cache_adapters.normalized import NEGATION_TOKENS

    assert isinstance(NEGATION_TOKENS, frozenset), "NEGATION_TOKENS deve essere frozenset"
    required = {"non", "no", "nessuno", "senza", "mai", "né", "nemmeno", "neanche", "niente"}
    assert required <= NEGATION_TOKENS, f"Token mancanti: {required - NEGATION_TOKENS}"


# ---------------------------------------------------------------------------
# Test NormalizedCacheAdapter con SQLite in-memory (tempfile)
# ---------------------------------------------------------------------------

def test_normalized_cache_adapter_get_set_hit():
    """Cache hit quando la query è morfologicamente equivalente."""
    from sync.cache_adapters.normalized import NormalizedCacheAdapter

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "test.db"
        adapter = NormalizedCacheAdapter(path=db, ttl_seconds=300)
        try:
            results = {"hits": [{"nome": "Mario"}], "total": 1}
            # set con query passato 1
            adapter.set("chi ha lavorato in apping?", "Collaboratori", None, None, results)
            # get con query morfologicamente equivalente → hit
            hit = adapter.get("chi lavora in apping?", "Collaboratori", None, None)
            assert hit is not None, "Atteso cache hit per query morfologicamente equivalente"
            assert hit["total"] == 1
        finally:
            adapter.close()


def test_normalized_cache_adapter_negation_miss():
    """Cache miss quando la query ha negazione (hash diverso)."""
    from sync.cache_adapters.normalized import NormalizedCacheAdapter

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "test.db"
        adapter = NormalizedCacheAdapter(path=db, ttl_seconds=300)
        try:
            results = {"hits": [{"nome": "Mario"}], "total": 1}
            adapter.set("chi lavora in apping?", "Collaboratori", None, None, results)
            # negazione → hash diverso → miss
            miss = adapter.get("chi NON lavora in apping?", "Collaboratori", None, None)
            assert miss is None, "Atteso cache miss per query con negazione"
        finally:
            adapter.close()


def test_normalized_cache_adapter_invalidate_collection():
    """invalidate_collection rimuove tutte le entry per la collection."""
    from sync.cache_adapters.normalized import NormalizedCacheAdapter

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "test.db"
        adapter = NormalizedCacheAdapter(path=db, ttl_seconds=300)
        try:
            results = {"hits": [], "total": 0}
            adapter.set("chi lavora in apping?", "Collaboratori", None, None, results)
            adapter.invalidate_collection("Collaboratori")
            miss = adapter.get("chi lavora in apping?", "Collaboratori", None, None)
            assert miss is None, "Atteso cache miss dopo invalidate_collection"
        finally:
            adapter.close()
