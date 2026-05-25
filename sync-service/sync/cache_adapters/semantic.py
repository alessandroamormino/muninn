"""SemanticCacheAdapter -- nearest-neighbor cache usando Weaviate _QueryCache.

Architettura (D-13 a D-20):
- Weaviate collection _QueryCache: vettori delle query cached (no vectorizer -- vettori da Ollama)
- SQLite search_cache: risultati JSON (stessa tabella di ExactMatchCacheAdapter)
- Lookup: embed(query) -> near_vector(_QueryCache, certainty>=threshold) -> leggi SQLite per cache_key
- Negation skip (D-17): query con token in NEGATION_TOKENS -> cache miss immediato
- TTL (D-20): expires_at filter su Weaviate; evict da SQLite se scaduto
- invalidate_collection (D-19): DELETE da SQLite + DELETE da Weaviate _QueryCache WHERE collection
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sync.cache_adapters.base import BaseCacheAdapter
from sync.cache_adapters.exact import ExactMatchCacheAdapter, _DB_PATH, make_cache_key

logger = logging.getLogger(__name__)

# D-17: token di negazione -- se presenti skippano il semantic lookup
NEGATION_TOKENS_SEMANTIC: frozenset[str] = frozenset({
    "non", "no", "nessuno", "senza", "mai", "né", "nemmeno", "neanche", "niente"
})

_QUERY_CACHE_COLLECTION = "_QueryCache"


def _has_negation(q: str) -> bool:
    """Ritorna True se la query contiene almeno un token di negazione (D-17)."""
    tokens = set(q.lower().split())
    return bool(tokens & NEGATION_TOKENS_SEMANTIC)


def _ensure_query_cache_collection(client) -> None:
    """Crea _QueryCache in Weaviate se non esiste. Idempotente (D-13, D-14)."""
    import weaviate.classes.config as _wvc
    if client.collections.exists(_QUERY_CACHE_COLLECTION):
        return
    logger.info("Creazione Weaviate collection %r...", _QUERY_CACHE_COLLECTION)
    client.collections.create(
        name=_QUERY_CACHE_COLLECTION,
        vectorizer_config=_wvc.Configure.Vectorizer.none(),
        properties=[
            _wvc.Property(name="query_text", data_type=_wvc.DataType.TEXT, skip_vectorization=True),
            _wvc.Property(name="collection", data_type=_wvc.DataType.TEXT, skip_vectorization=True),
            _wvc.Property(name="filters", data_type=_wvc.DataType.TEXT, skip_vectorization=True),
            _wvc.Property(name="min_score", data_type=_wvc.DataType.NUMBER, skip_vectorization=True),
            _wvc.Property(name="cache_key", data_type=_wvc.DataType.TEXT, skip_vectorization=True),
            _wvc.Property(name="expires_at", data_type=_wvc.DataType.TEXT, skip_vectorization=True),
        ],
    )
    logger.info("Weaviate collection %r creata.", _QUERY_CACHE_COLLECTION)


class SemanticCacheAdapter(BaseCacheAdapter):
    """Cache semantica: near-vector lookup in Weaviate + result storage in SQLite."""

    def __init__(
        self,
        path: Path = _DB_PATH,
        ttl_seconds: int = 300,
        threshold: float = 0.90,
        embedding_cfg=None,
    ) -> None:
        self._exact = ExactMatchCacheAdapter(path, ttl_seconds=ttl_seconds)
        self._threshold = threshold
        self._embedding_cfg = embedding_cfg  # EmbeddingConfig da settings
        self._embedder = None  # lazy init al primo get/set
        logger.info(
            "SemanticCacheAdapter initialised (threshold=%.2f, ttl=%ds).",
            threshold, ttl_seconds,
        )

    def _get_embedder(self):
        """Lazy init OllamaEmbeddingAdapter (D-15)."""
        if self._embedder is None:
            from embeddings.ollama_adapter import OllamaEmbeddingAdapter
            self._embedder = OllamaEmbeddingAdapter(self._embedding_cfg)
        return self._embedder

    def _get_weaviate_client(self):
        from weaviate_store.client import get_client
        client = get_client()
        _ensure_query_cache_collection(client)
        return client

    def get(self, q: str, collection: str, filters: str | None, min_score: float | None) -> dict | None:
        # D-17: negation skip -- previene falsi positivi semantici
        if _has_negation(q):
            logger.debug("SemanticCache: negation detected in %r -- skip semantic lookup.", q)
            return None

        try:
            # Genera vettore della query corrente
            embedder = self._get_embedder()
            query_vector = embedder.embed([q])[0]

            client = self._get_weaviate_client()
            col = client.collections.get(_QUERY_CACHE_COLLECTION)
            now_iso = datetime.now(tz=timezone.utc).isoformat()

            from weaviate.classes.query import Filter

            # Near-vector lookup con TTL filter e collection filter (D-15, D-20)
            weaviate_filter = (
                Filter.by_property("collection").equal(collection)
                & Filter.by_property("expires_at").greater_than(now_iso)
            )
            results = col.query.near_vector(
                near_vector=query_vector,
                certainty=self._threshold,
                limit=1,
                filters=weaviate_filter,
                return_properties=["cache_key", "query_text"],
            )

            if not results.objects:
                return None

            hit_cache_key = results.objects[0].properties.get("cache_key")
            if not hit_cache_key:
                return None

            # Leggi risultato da SQLite tramite cache_key trovato in Weaviate (D-16)
            cached_result = self._exact._get_by_key(hit_cache_key)
            if cached_result is None:
                return None

            logger.debug(
                "SemanticCache HIT for %r (matched key=%s)", q, hit_cache_key[:12]
            )
            return cached_result

        except Exception as exc:  # noqa: BLE001
            logger.warning("SemanticCacheAdapter.get failed: %s", exc)
            return None

    def set(
        self,
        q: str,
        collection: str,
        filters: str | None,
        min_score: float | None,
        results: dict,
        ttl_seconds: int | None = None,
    ) -> None:
        # D-18: salva in SQLite (come ExactMatch) + upsert vettore in Weaviate
        try:
            ttl = ttl_seconds if ttl_seconds is not None else self._exact._ttl
            now = datetime.now(tz=timezone.utc)
            expires_iso = (now + timedelta(seconds=ttl)).isoformat()

            # 1. Calcola cache_key deterministic (SHA256 exact, non normalizzato)
            key = make_cache_key(q, collection, filters, min_score)

            # 2. Salva risultato in SQLite via metodo protetto
            self._exact._set_by_key(key, collection, results, ttl)

            # 3. Genera vettore e fai upsert in Weaviate _QueryCache (D-18)
            embedder = self._get_embedder()
            query_vector = embedder.embed([q])[0]

            client = self._get_weaviate_client()
            col = client.collections.get(_QUERY_CACHE_COLLECTION)

            import weaviate.exceptions
            # UUID deterministico per idempotenza: namespace + "semantic:" + key
            obj_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"semantic:{key}"))
            obj_props = {
                "query_text": q,
                "collection": collection,
                "filters": filters or "",
                "min_score": float(min_score) if min_score is not None else 0.0,
                "cache_key": key,
                "expires_at": expires_iso,
            }
            # Upsert idempotente: insert -> se gia' esiste (409) -> replace per aggiornare TTL
            try:
                col.data.insert(
                    properties=obj_props,
                    vector=query_vector,
                    uuid=obj_uuid,
                )
            except weaviate.exceptions.UnexpectedStatusCodeError:
                col.data.replace(
                    uuid=obj_uuid,
                    properties=obj_props,
                    vector=query_vector,
                )
            logger.debug("SemanticCache SET key=%s expires=%s", key[:12], expires_iso)

        except Exception as exc:  # noqa: BLE001
            logger.warning("SemanticCacheAdapter.set failed: %s", exc)

    def invalidate_collection(self, collection: str) -> None:
        # D-19: DELETE da SQLite + DELETE da Weaviate _QueryCache WHERE collection
        # 1. SQLite -- delega a ExactMatch
        self._exact.invalidate_collection(collection)

        # 2. Weaviate _QueryCache -- cancella tutti i vettori per questa collection
        try:
            from weaviate.classes.query import Filter
            client = self._get_weaviate_client()
            col = client.collections.get(_QUERY_CACHE_COLLECTION)
            col.data.delete_many(
                where=Filter.by_property("collection").equal(collection)
            )
            logger.info(
                "SemanticCache: invalidated Weaviate _QueryCache entries for collection %r",
                collection,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SemanticCacheAdapter.invalidate_collection Weaviate failed: %s", exc
            )

    def close(self) -> None:
        self._exact.close()
