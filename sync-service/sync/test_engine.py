"""Test per SyncEngine — TDD RED phase.

Testa run_incremental e run_full con mock di upsert_records e client Weaviate.

Nota: il pacchetto PyPI 'weaviate' non e' installato nell'env locale (gira in Docker).
Per questo motivo UpsertResult viene costruito come namedtuple/dataclass-like object
tramite una classe helper, senza importare da weaviate.upsert direttamente.
"""
from __future__ import annotations

import pathlib
import tempfile
import uuid
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from config.settings import AppConfig, SourceConfig, SyncConfig, WeaviateConfig, EmbeddingConfig


# ---------------------------------------------------------------------------
# Helper: UpsertResult-compatible object (stessa struttura di weaviate/upsert.py)
# ---------------------------------------------------------------------------
@dataclass
class _UpsertResult:
    inserted: int
    updated: int
    skipped: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


def _make_app_cfg(csv_path: str) -> AppConfig:
    src = SourceConfig(type="csv", file_path=csv_path, id_field="id")
    syn = SyncConfig(hash_fields=["id", "name", "description"])
    wea = WeaviateConfig(
        collection="TestCol",
        text_fields=["name", "description"],
        metadata_fields=["id"],
    )
    emb = EmbeddingConfig(type="weaviate_builtin", model="text2vec-transformers")
    return AppConfig(source=src, sync=syn, weaviate=wea, embedding=emb)


@pytest.fixture
def csv_file(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("id,name,description\n1,Alice,Test\n2,Bob,Example\n")
    return str(f)


@pytest.fixture
def state_store(tmp_path):
    from sync.state_store import StateStore
    return StateStore(path=tmp_path / "state.json")


@pytest.fixture
def app_cfg(csv_file):
    return _make_app_cfg(csv_file)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.collections.exists.return_value = False
    return client


class TestSyncEngineImport:
    def test_sync_engine_importable(self):
        from sync.engine import SyncEngine  # noqa: F401
        assert SyncEngine is not None

    def test_sync_engine_has_run_incremental(self):
        from sync.engine import SyncEngine
        assert callable(getattr(SyncEngine, "run_incremental", None))

    def test_sync_engine_has_run_full(self):
        from sync.engine import SyncEngine
        assert callable(getattr(SyncEngine, "run_full", None))


class TestSyncEngineInit:
    def test_init_accepts_three_params(self, app_cfg, mock_client, state_store):
        from sync.engine import SyncEngine
        engine = SyncEngine(app_cfg, mock_client, state_store)
        assert engine is not None


def _fake_uuid(source_type: str, record_id: str):
    """Versione locale di compute_record_uuid per i test (evita import weaviate)."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"{source_type}:{record_id}")


class TestRunIncremental:
    def test_first_run_all_records_are_delta(self, app_cfg, mock_client, state_store):
        """Prima run: stato vuoto -> tutti i record sono in delta."""
        from sync.engine import SyncEngine
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine = SyncEngine(app_cfg, mock_client, state_store)
            stats = engine.run_incremental()
        assert stats["inserted"] == 2
        assert stats["skipped"] == 0
        assert stats["total"] == 2
        assert mock_upsert.call_count == 1

    def test_second_run_all_skipped(self, app_cfg, mock_client, state_store):
        """Seconda run con dati invariati: tutto skipped."""
        from sync.engine import SyncEngine
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine = SyncEngine(app_cfg, mock_client, state_store)
            engine.run_incremental()  # prima run

            mock_upsert.reset_mock()
            mock_upsert.return_value = _UpsertResult(inserted=0, updated=0, skipped=0)
            stats2 = engine.run_incremental()  # seconda run

        assert stats2["skipped"] == 2
        assert stats2["inserted"] == 0
        assert stats2["updated"] == 0
        last_call_records = mock_upsert.call_args[0][1]
        assert last_call_records == [], f"Expected empty delta on second run, got {last_call_records}"

    def test_upsert_called_once_with_delta_list(self, app_cfg, mock_client, state_store):
        """upsert_records deve essere chiamato UNA volta sola (non per ogni record)."""
        from sync.engine import SyncEngine
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine = SyncEngine(app_cfg, mock_client, state_store)
            engine.run_incremental()
        assert mock_upsert.call_count == 1
        call_args = mock_upsert.call_args
        records_arg = call_args[0][1]  # secondo positional arg
        assert isinstance(records_arg, list)
        assert len(records_arg) == 2

    def test_state_updated_after_upsert(self, app_cfg, mock_client, state_store):
        """StateStore deve essere aggiornato per ogni record upsertato."""
        from sync.engine import SyncEngine
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine = SyncEngine(app_cfg, mock_client, state_store)
            engine.run_incremental()
        state1 = state_store.get("1")
        state2 = state_store.get("2")
        assert state1 is not None
        assert state2 is not None
        assert "hash" in state1
        assert "synced_at" in state1
        assert "weaviate_uuid" in state1

    def test_return_dict_has_required_keys(self, app_cfg, mock_client, state_store):
        """run_incremental deve restituire dict con total, inserted, updated, skipped, errors, timestamp."""
        from sync.engine import SyncEngine
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine = SyncEngine(app_cfg, mock_client, state_store)
            stats = engine.run_incremental()
        for key in ("total", "inserted", "updated", "skipped", "errors", "timestamp"):
            assert key in stats, f"chiave '{key}' mancante in {stats}"

    def test_changed_record_is_updated(self, app_cfg, csv_file, mock_client, state_store):
        """Se un record cambia tra due run, deve essere nel delta della seconda run."""
        from sync.engine import SyncEngine
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine = SyncEngine(app_cfg, mock_client, state_store)
            engine.run_incremental()  # prima run

            # Modifica il file CSV
            with open(csv_file, "w") as f:
                f.write("id,name,description\n1,Alice,Modified\n2,Bob,Example\n")

            mock_upsert.reset_mock()
            mock_upsert.return_value = _UpsertResult(inserted=0, updated=1, skipped=0)
            stats2 = engine.run_incremental()

        assert stats2["skipped"] == 1  # record 2 invariato
        call_args = mock_upsert.call_args
        records_arg = call_args[0][1]
        assert len(records_arg) == 1  # solo il delta (record 1 modificato)


class TestRunFull:
    """I test run_full iniettano _create_collection_fn come MagicMock per evitare
    la dipendenza dal pacchetto PyPI weaviate (non installato localmente).
    """

    def _make_engine_with_mock_create(self, app_cfg, mock_client, state_store):
        """Helper: crea SyncEngine con _create_collection_fn e _write_model_version_fn mockati."""
        from sync.engine import SyncEngine
        engine = SyncEngine(app_cfg, mock_client, state_store)
        engine._create_collection_fn = MagicMock()
        engine._write_model_version_fn = MagicMock()
        return engine, engine._create_collection_fn

    def test_run_full_drops_and_recreates_collection(self, app_cfg, mock_client, state_store):
        """run_full deve drop la collezione se esiste e ricrearla."""
        mock_client.collections.exists.return_value = True
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine, mock_create = self._make_engine_with_mock_create(app_cfg, mock_client, state_store)
            engine.run_full()
        mock_client.collections.delete.assert_called_once_with("TestCol")
        mock_create.assert_called_once()

    def test_run_full_clears_state(self, app_cfg, mock_client, state_store):
        """run_full deve azzerare lo StateStore prima di fare upsert."""
        state_store.set("old_record", {"hash": "abc", "synced_at": "2024-01-01", "weaviate_uuid": "xyz"})

        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine, _ = self._make_engine_with_mock_create(app_cfg, mock_client, state_store)
            engine.run_full()
        assert state_store.get("old_record") is None

    def test_run_full_returns_stats_dict(self, app_cfg, mock_client, state_store):
        """run_full deve restituire dict con le stesse chiavi di run_incremental."""
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine, _ = self._make_engine_with_mock_create(app_cfg, mock_client, state_store)
            stats = engine.run_full()
        for key in ("total", "inserted", "updated", "skipped", "errors", "timestamp"):
            assert key in stats

    def test_run_full_no_delete_if_collection_missing(self, app_cfg, mock_client, state_store):
        """run_full NON deve chiamare collections.delete se la collezione non esiste."""
        mock_client.collections.exists.return_value = False
        with patch("sync.engine.upsert_records") as mock_upsert, \
             patch("sync.engine.compute_record_uuid", side_effect=_fake_uuid):
            mock_upsert.return_value = _UpsertResult(inserted=2, updated=0, skipped=0)
            engine, _ = self._make_engine_with_mock_create(app_cfg, mock_client, state_store)
            engine.run_full()
        mock_client.collections.delete.assert_not_called()
