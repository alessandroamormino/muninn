"""Tests per StateStore — fase RED (TDD).

I test coprono:
- get su id inesistente → None
- set/get round-trip
- persistenza su disco dopo set
- reload da disco (sopravvivenza al riavvio)
- overwrite stessa chiave mantiene solo l'ultima
- all() restituisce il dict completo
- clear() azzera memoria e disco
- file inesistente non crasha
- JSON malformato non crasha (log warning, restituisce {})
- atomic write: il file tmp viene usato e sostituito
"""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from sync.state_store import StateStore, STATE_PATH


class TestStatePath:
    def test_default_state_path(self):
        """STATE_PATH deve puntare a /app/.sync/sync_state.json."""
        assert STATE_PATH == pathlib.Path("/app/.sync/sync_state.json")


class TestStateStoreBasic:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = self._tmp.name
        self.path = pathlib.Path(self.tmp_dir) / "test_state.json"
        self.store = StateStore(path=self.path)

    def teardown_method(self):
        self._tmp.cleanup()

    def test_get_missing_returns_none(self):
        assert self.store.get("nonexistent") is None

    def test_set_and_get(self):
        self.store.set("rec-1", {"hash": "abc", "synced_at": "2026-05-10T00:00:00Z", "weaviate_uuid": "uuid-1"})
        entry = self.store.get("rec-1")
        assert entry is not None
        assert entry["hash"] == "abc"
        assert entry["synced_at"] == "2026-05-10T00:00:00Z"
        assert entry["weaviate_uuid"] == "uuid-1"

    def test_set_persists_to_disk(self):
        self.store.set("rec-1", {"hash": "abc", "synced_at": "2026-05-10T00:00:00Z", "weaviate_uuid": "uuid-1"})
        assert self.path.exists(), "Il file JSON deve essere creato su disco dopo set()"
        with self.path.open() as fh:
            disk = json.load(fh)
        assert "rec-1" in disk

    def test_reload_from_disk(self):
        self.store.set("rec-1", {"hash": "abc", "synced_at": "2026-05-10T00:00:00Z", "weaviate_uuid": "uuid-1"})
        # Crea nuova istanza sullo stesso path
        store2 = StateStore(path=self.path)
        entry = store2.get("rec-1")
        assert entry is not None, "Lo stato deve sopravvivere a ricaricamento da disco"
        assert entry["hash"] == "abc"

    def test_set_overwrites_existing_key(self):
        self.store.set("rec-1", {"hash": "old", "synced_at": "2026-05-10T00:00:00Z", "weaviate_uuid": "uuid-1"})
        self.store.set("rec-1", {"hash": "new", "synced_at": "2026-05-10T01:00:00Z", "weaviate_uuid": "uuid-1"})
        assert self.store.get("rec-1")["hash"] == "new"

    def test_all_returns_full_dict(self):
        self.store.set("rec-1", {"hash": "a", "synced_at": "t", "weaviate_uuid": "u1"})
        self.store.set("rec-2", {"hash": "b", "synced_at": "t", "weaviate_uuid": "u2"})
        result = self.store.all()
        assert len(result) == 2
        assert "rec-1" in result
        assert "rec-2" in result

    def test_all_returns_copy(self):
        """all() deve restituire una copia, non il riferimento interno."""
        self.store.set("rec-1", {"hash": "a", "synced_at": "t", "weaviate_uuid": "u1"})
        result = self.store.all()
        result["injected"] = {}
        assert self.store.get("injected") is None

    def test_clear_empties_memory(self):
        self.store.set("rec-1", {"hash": "a", "synced_at": "t", "weaviate_uuid": "u1"})
        self.store.clear()
        assert self.store.get("rec-1") is None
        assert self.store.all() == {}

    def test_clear_empties_disk(self):
        self.store.set("rec-1", {"hash": "a", "synced_at": "t", "weaviate_uuid": "u1"})
        self.store.clear()
        assert self.path.exists(), "Il file deve esistere anche dopo clear()"
        with self.path.open() as fh:
            assert json.load(fh) == {}

    def test_two_consecutive_sets_same_id_keep_last(self):
        self.store.set("x", {"hash": "1", "synced_at": "t", "weaviate_uuid": "u"})
        self.store.set("x", {"hash": "2", "synced_at": "t", "weaviate_uuid": "u"})
        assert self.store.get("x")["hash"] == "2"
        assert len(self.store.all()) == 1


class TestStateStoreEdgeCases:
    def test_missing_file_does_not_crash(self):
        """Creare StateStore su un path inesistente non deve crashare."""
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "nonexistent" / "state.json"
            # Il file e la dir non esistono
            store = StateStore(path=p)
            assert store.get("x") is None

    def test_malformed_json_does_not_crash(self):
        """JSON malformato deve produrre stato vuoto, non un'eccezione."""
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "state.json"
            p.write_text("NOT_VALID_JSON")
            store = StateStore(path=p)
            assert store.get("x") is None
            assert store.all() == {}

    def test_non_dict_json_does_not_crash(self):
        """JSON valido ma non dict (es. lista) deve produrre stato vuoto."""
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "state.json"
            p.write_text(json.dumps([1, 2, 3]))
            store = StateStore(path=p)
            assert store.all() == {}

    def test_atomic_write_no_tmp_left_after_set(self):
        """Dopo set(), il file .json.tmp non deve rimanere sul disco."""
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "state.json"
            store = StateStore(path=p)
            store.set("rec", {"hash": "h", "synced_at": "t", "weaviate_uuid": "u"})
            tmp_file = p.with_suffix(".json.tmp")
            assert not tmp_file.exists(), "Il file .json.tmp non deve restare dopo set()"

    def test_set_creates_parent_directory(self):
        """set() deve creare la directory padre se non esiste."""
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "nested" / "dir" / "state.json"
            store = StateStore(path=p)
            store.set("rec", {"hash": "h", "synced_at": "t", "weaviate_uuid": "u"})
            assert p.exists()
