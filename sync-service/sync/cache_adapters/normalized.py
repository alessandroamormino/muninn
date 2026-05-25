"""NormalizedCacheAdapter — spaCy-based query normalization before SHA256 hashing.

Pipeline (D-08): lowercase → spaCy tokenize → lemma → rimozione stopword
                 → PRESERVA NEGATION_TOKENS → sort alfabetico → SHA256.
spaCy importato lazy all'init — non caricato se cache_mode != normalized (D-11).
"""
from __future__ import annotations

import hashlib
import logging
import threading as _threading
from pathlib import Path

from sync.cache_adapters.base import BaseCacheAdapter
from sync.cache_adapters.exact import ExactMatchCacheAdapter
from sync.cache_adapters.exact import _DB_PATH

logger = logging.getLogger(__name__)

# D-09: questi token NON vengono mai rimossi, anche se spaCy li marca is_stop=True
NEGATION_TOKENS: frozenset[str] = frozenset({
    "non", "no", "nessuno", "senza", "mai", "né", "nemmeno", "neanche", "niente"
})

# Singleton spaCy — caricato una sola volta per processo (D-11); Lock per thread-safety
_nlp = None
_nlp_lock = _threading.Lock()


def _get_nlp():
    """Carica il modello spaCy in modo lazy e thread-safe (singleton)."""
    global _nlp
    if _nlp is None:
        with _nlp_lock:
            if _nlp is None:  # double-checked locking
                import spacy  # lazy import — D-11
                _nlp = spacy.load("it_core_news_sm", disable=["parser", "ner"])
                logger.info("spaCy it_core_news_sm caricato (lazy init).")
    return _nlp


def normalize_query(q: str) -> str:
    """Normalizza la query: lemmatizza, rimuove stopwords, preserva negazioni, ordina.

    Pipeline D-08:
    1. lowercase della query intera
    2. tokenizzazione + lemmatizzazione via spaCy it_core_news_sm
    3. rimozione stopwords (token.is_stop) — ma PRESERVA NEGATION_TOKENS (D-09)
    4. sort alfabetico dei token risultanti
    5. join con spazio
    """
    nlp = _get_nlp()
    # T-13.1-04: truncation a 500 char per prevenire DoS su query molto lunghe
    q_trunc = q[:500]
    tokens = [
        t.lemma_.lower()
        for t in nlp(q_trunc.lower())
        if not t.is_stop or t.lower_ in NEGATION_TOKENS
    ]
    return " ".join(sorted(tokens))


def make_normalized_cache_key(
    q: str,
    collection: str,
    filters: str | None,
    min_score: float | None,
) -> str:
    """SHA256 della query normalizzata + parametri di ricerca."""
    norm = normalize_query(q)
    raw = f"{norm}|{collection}|{filters or ''}|{min_score or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


class NormalizedCacheAdapter(BaseCacheAdapter):
    """Cache con normalizzazione spaCy — query morfologicamente equivalenti → stessa entry.

    Usa composizione su ExactMatchCacheAdapter per la logica SQLite (storage + TTL).
    La normalizzazione avviene prima del calcolo del cache key in get() e set().
    """

    def __init__(self, path: Path = _DB_PATH, ttl_seconds: int = 300) -> None:
        # Composizione su ExactMatchCacheAdapter per la logica SQLite
        self._exact = ExactMatchCacheAdapter(path, ttl_seconds=ttl_seconds)
        # Forza il caricamento del modello all'init per fail-fast (errore immediato se modello mancante)
        _get_nlp()
        logger.info("NormalizedCacheAdapter inizializzato (spaCy it_core_news_sm).")

    def get(
        self,
        q: str,
        collection: str,
        filters: str | None,
        min_score: float | None,
    ) -> dict | None:
        """Normalizza la query prima di calcolare la chiave, poi fa lookup SQLite."""
        key = make_normalized_cache_key(q, collection, filters, min_score)
        return self._exact._get_by_key(key)

    def set(
        self,
        q: str,
        collection: str,
        filters: str | None,
        min_score: float | None,
        results: dict,
        ttl_seconds: int | None = None,
    ) -> None:
        """Normalizza la query prima di calcolare la chiave, poi salva in SQLite."""
        key = make_normalized_cache_key(q, collection, filters, min_score)
        self._exact._set_by_key(key, collection, results, ttl_seconds)

    def invalidate_collection(self, collection: str) -> None:
        """Delega all'SQLite interno."""
        self._exact.invalidate_collection(collection)

    def close(self) -> None:
        """Chiude la connessione SQLite interna."""
        self._exact.close()
