"""Test unitari per SemanticCacheAdapter (Phase 13.1 - Wave 3).

Comportamenti testati:
- get() con query contenente token di negazione -> ritorna None senza chiamare Weaviate
- get() con query senza negation, near_vector trova hit -> ritorna risultato SQLite
- get() con query senza negation, nessun hit Weaviate -> ritorna None
- set() chiama embed() per generare il vettore, salva in SQLite E fa upsert in Weaviate _QueryCache
- invalidate_collection() fa DELETE da SQLite E DELETE da Weaviate _QueryCache WHERE collection
- _ensure_query_cache_collection() e' idempotente (no-op se collection esiste gia')
"""
from __future__ import annotations

import json
import pathlib
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helper: build mock Weaviate results (zero-object hit)
# ---------------------------------------------------------------------------

def _make_weaviate_miss():
    res = MagicMock()
    res.objects = []
    return res


def _make_weaviate_hit(cache_key: str):
    obj = MagicMock()
    obj.properties = {"cache_key": cache_key, "query_text": "test"}
    res = MagicMock()
    res.objects = [obj]
    return res


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture()
def mock_cfg():
    cfg = MagicMock()
    cfg.type = "ollama"
    return cfg


# ---------------------------------------------------------------------------
# Test _has_negation (D-17)
# ---------------------------------------------------------------------------

class TestHasNegation:
    def test_import(self):
        from sync.cache_adapters.semantic import _has_negation, NEGATION_TOKENS_SEMANTIC
        assert callable(_has_negation)
        assert isinstance(NEGATION_TOKENS_SEMANTIC, frozenset)

    def test_non_detected(self):
        from sync.cache_adapters.semantic import _has_negation
        assert _has_negation("chi non lavora in apping") is True

    def test_no_detected(self):
        from sync.cache_adapters.semantic import _has_negation
        assert _has_negation("no esperienza") is True

    def test_nessuno_detected(self):
        from sync.cache_adapters.semantic import _has_negation
        assert _has_negation("nessuno ha risposto") is True

    def test_senza_detected(self):
        from sync.cache_adapters.semantic import _has_negation
        assert _has_negation("senza esperienza") is True

    def test_mai_detected(self):
        from sync.cache_adapters.semantic import _has_negation
        assert _has_negation("mai visto prima") is True

    def test_no_negation(self):
        from sync.cache_adapters.semantic import _has_negation
        assert _has_negation("sviluppatori Python senior") is False

    def test_empty_query(self):
        from sync.cache_adapters.semantic import _has_negation
        assert _has_negation("") is False

    def test_case_insensitive(self):
        from sync.cache_adapters.semantic import _has_negation
        assert _has_negation("Chi NON lavora in Apping") is True


# ---------------------------------------------------------------------------
# Test _ensure_query_cache_collection (D-13, D-14)
# ---------------------------------------------------------------------------

class TestEnsureQueryCacheCollection:
    def test_creates_when_missing(self):
        from sync.cache_adapters.semantic import _ensure_query_cache_collection
        mock_client = MagicMock()
        mock_client.collections.exists.return_value = False
        _ensure_query_cache_collection(mock_client)
        mock_client.collections.create.assert_called_once()
        call_kwargs = mock_client.collections.create.call_args
        assert call_kwargs.kwargs["name"] == "_QueryCache" or call_kwargs.args[0] == "_QueryCache"

    def test_noop_when_exists(self):
        from sync.cache_adapters.semantic import _ensure_query_cache_collection
        mock_client = MagicMock()
        mock_client.collections.exists.return_value = True
        _ensure_query_cache_collection(mock_client)
        mock_client.collections.create.assert_not_called()


# ---------------------------------------------------------------------------
# Test get() con negazione (D-17)
# ---------------------------------------------------------------------------

class TestGetWithNegation:
    def test_returns_none_without_calling_embedder(self, tmp_db, mock_cfg):
        from sync.cache_adapters.semantic import SemanticCacheAdapter
        adapter = SemanticCacheAdapter(path=tmp_db, ttl_seconds=60, threshold=0.90, embedding_cfg=mock_cfg)
        result = adapter.get("chi non lavora in apping", "TestCol", None, None)
        assert result is None
        assert adapter._embedder is None, "embedder NON deve essere inizializzato per query con negazione"
        adapter.close()

    def test_returns_none_without_calling_weaviate(self, tmp_db, mock_cfg):
        from sync.cache_adapters.semantic import SemanticCacheAdapter
        adapter = SemanticCacheAdapter(path=tmp_db, ttl_seconds=60, threshold=0.90, embedding_cfg=mock_cfg)
        with patch("sync.cache_adapters.semantic.SemanticCacheAdapter._get_weaviate_client") as mock_wc:
            result = adapter.get("senza esperienza", "TestCol", None, None)
        mock_wc.assert_not_called()
        assert result is None
        adapter.close()


# ---------------------------------------------------------------------------
# Test get() senza negazione -- cache hit (D-15, D-16)
# ---------------------------------------------------------------------------

class TestGetSemanticHit:
    def test_returns_sqlite_result_on_hit(self, tmp_db, mock_cfg):
        from sync.cache_adapters.semantic import SemanticCacheAdapter
        from sync.cache_adapters.exact import make_cache_key

        expected_results = {"results": [{"name": "Alice"}], "cached": False}
        cache_key = make_cache_key("programmatori Python", "TestCol", None, None)

        adapter = SemanticCacheAdapter(path=tmp_db, ttl_seconds=60, threshold=0.90, embedding_cfg=mock_cfg)
        # Pre-popola SQLite con il risultato atteso
        adapter._exact._set_by_key(cache_key, "TestCol", expected_results, 300)

        mock_vector = [0.1, 0.2, 0.3]
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [mock_vector]

        mock_client = MagicMock()
        mock_client.collections.exists.return_value = True  # skip create
        weaviate_result = _make_weaviate_hit(cache_key)
        mock_client.collections.get.return_value.query.near_vector.return_value = weaviate_result

        adapter._embedder = mock_embedder
        with patch("sync.cache_adapters.semantic.SemanticCacheAdapter._get_weaviate_client", return_value=mock_client):
            result = adapter.get("programmatori Python", "TestCol", None, None)

        assert result is not None
        assert result == expected_results
        mock_embedder.embed.assert_called_once_with(["programmatori Python"])
        adapter.close()


# ---------------------------------------------------------------------------
# Test get() senza negazione -- cache miss (nessun hit Weaviate)
# ---------------------------------------------------------------------------

class TestGetSemanticMiss:
    def test_returns_none_on_weaviate_miss(self, tmp_db, mock_cfg):
        from sync.cache_adapters.semantic import SemanticCacheAdapter

        adapter = SemanticCacheAdapter(path=tmp_db, ttl_seconds=60, threshold=0.90, embedding_cfg=mock_cfg)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [[0.1, 0.2, 0.3]]

        mock_client = MagicMock()
        mock_client.collections.exists.return_value = True
        mock_client.collections.get.return_value.query.near_vector.return_value = _make_weaviate_miss()

        adapter._embedder = mock_embedder
        with patch("sync.cache_adapters.semantic.SemanticCacheAdapter._get_weaviate_client", return_value=mock_client):
            result = adapter.get("sviluppatori Python", "TestCol", None, None)

        assert result is None
        adapter.close()


# ---------------------------------------------------------------------------
# Test set() -- SQLite + Weaviate upsert (D-18)
# ---------------------------------------------------------------------------

class TestSet:
    def test_set_saves_to_sqlite_and_upserts_weaviate(self, tmp_db, mock_cfg):
        from sync.cache_adapters.semantic import SemanticCacheAdapter
        from sync.cache_adapters.exact import make_cache_key

        adapter = SemanticCacheAdapter(path=tmp_db, ttl_seconds=60, threshold=0.90, embedding_cfg=mock_cfg)

        results = {"results": [{"name": "Bob"}], "cached": False}
        mock_vector = [0.4, 0.5, 0.6]
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [mock_vector]

        mock_col = MagicMock()
        mock_client = MagicMock()
        mock_client.collections.exists.return_value = True
        mock_client.collections.get.return_value = mock_col

        adapter._embedder = mock_embedder
        with patch("sync.cache_adapters.semantic.SemanticCacheAdapter._get_weaviate_client", return_value=mock_client):
            adapter.set("sviluppatori Python", "TestCol", None, None, results, ttl_seconds=60)

        # SQLite deve contenere il risultato
        key = make_cache_key("sviluppatori Python", "TestCol", None, None)
        stored = adapter._exact._get_by_key(key)
        assert stored is not None
        assert stored == results

        # Weaviate deve ricevere un insert (o replace)
        assert mock_col.data.insert.called or mock_col.data.replace.called
        mock_embedder.embed.assert_called_once_with(["sviluppatori Python"])
        adapter.close()


# ---------------------------------------------------------------------------
# Test invalidate_collection() -- SQLite + Weaviate (D-19)
# ---------------------------------------------------------------------------

class TestInvalidateCollection:
    def test_invalidate_deletes_from_sqlite_and_weaviate(self, tmp_db, mock_cfg):
        from sync.cache_adapters.semantic import SemanticCacheAdapter

        adapter = SemanticCacheAdapter(path=tmp_db, ttl_seconds=60, threshold=0.90, embedding_cfg=mock_cfg)

        # Pre-popola SQLite
        from sync.cache_adapters.exact import make_cache_key
        key = make_cache_key("sviluppatori", "TestCol", None, None)
        adapter._exact._set_by_key(key, "TestCol", {"results": []}, 300)
        # Verifica che ci sia qualcosa in SQLite
        assert adapter._exact._get_by_key(key) is not None

        mock_col = MagicMock()
        mock_client = MagicMock()
        mock_client.collections.exists.return_value = True
        mock_client.collections.get.return_value = mock_col

        with patch("sync.cache_adapters.semantic.SemanticCacheAdapter._get_weaviate_client", return_value=mock_client):
            adapter.invalidate_collection("TestCol")

        # SQLite deve essere vuoto
        assert adapter._exact._get_by_key(key) is None

        # Weaviate deve ricevere delete_many
        mock_col.data.delete_many.assert_called_once()
        adapter.close()
