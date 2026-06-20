"""EntityStateStore — persiste lo stato active/unloaded per entità in /app/.sync/entity_state.json.

Struttura file:
{
  "CollectionName": {
    "status": "unloaded",
    "snapshot_name": "CollectionName-abc123-2026-06-20.snapshot",
    "updated_at": "2026-06-20T10:00:00+00:00"
  },
  ...
}

SEPARATO da sync_state.json (che contiene gli hash per-record per il sync incrementale,
vedi sync/state_store.py) — questo file traccia solo lo stato applicativo
active/unloaded per entità (D-01), usato dal meccanismo di unload/load Qdrant
(Phase 26: Entity Load/Unload Management).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ENTITY_STATE_PATH = Path("/app/.sync/entity_state.json")


class EntityStateStore:
    """Persiste lo stato active/unloaded per entità su disco tramite un file JSON.

    Ogni operazione set_unloaded()/set_active() riscrive l'intero file con atomic
    write (write-to-tmp → Path.replace) identico al pattern di sync/state_store.py.
    """

    def __init__(self, path: Path = ENTITY_STATE_PATH) -> None:
        self._path = path
        self._data: dict[str, dict] = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_status(self, collection: str) -> str:
        """Restituisce 'active' o 'unloaded'. Default 'active' se assente (D-02)."""
        entry = self._data.get(collection)
        return entry.get("status", "active") if entry else "active"

    def set_unloaded(self, collection: str, snapshot_name: str, updated_at: str) -> None:
        """Segna l'entità come 'unloaded', registrando il nome dello snapshot.

        La scrittura su disco avviene prima della mutazione in memoria: se _save
        solleva OSError, _data rimane invariato (memoria e disco restano consistenti).
        """
        new_data = {
            **self._data,
            collection: {
                "status": "unloaded",
                "snapshot_name": snapshot_name,
                "updated_at": updated_at,
            },
        }
        self._save(new_data)
        self._data = new_data

    def set_active(self, collection: str, updated_at: str) -> None:
        """Segna l'entità come 'active', preservando i campi esistenti (es. snapshot_name)."""
        entry = self._data.get(collection, {})
        new_data = {
            **self._data,
            collection: {**entry, "status": "active", "updated_at": updated_at},
        }
        self._save(new_data)
        self._data = new_data

    def get_snapshot_name(self, collection: str) -> str | None:
        """Restituisce il nome dello snapshot registrato per l'entità, o None."""
        entry = self._data.get(collection)
        return entry.get("snapshot_name") if entry else None

    def all(self) -> dict[str, dict]:
        """Restituisce una copia del dizionario interno completo (usato da GET /collections)."""
        return dict(self._data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, dict]:
        """Carica il file JSON dallo stato persistente. Fallisce silenziosamente."""
        if not self._path.exists():
            return {}
        try:
            with self._path.open("r") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                logger.warning("%s non contiene un dict; inizializzato vuoto.", self._path)
                return {}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Impossibile leggere %s: %s — stato inizializzato vuoto.", self._path, exc)
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        """Salva i dati forniti su disco con atomic write (tmp → replace).

        Accetta i dati da scrivere esplicitamente (non usa self._data) per permettere
        il pattern write-before-mutate: il caller aggiorna self._data solo dopo il
        successo di questa operazione.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        with tmp.open("w") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(self._path)
