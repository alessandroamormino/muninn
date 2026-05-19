"""StateStore — persiste lo stato di sync in /app/.sync/sync_state.json.

Struttura file:
{
  "record_id": {
    "hash": "md5hex",
    "synced_at": "2026-05-10T12:00:00Z",
    "weaviate_uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  },
  ...
}
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_PATH = Path("/app/.sync/sync_state.json")


class StateStore:
    """Persiste lo stato di sync su disco tramite un file JSON.

    Ogni operazione set() riscrive l'intero file con atomic write
    (write-to-tmp → Path.replace) identico al pattern di model_version.py.
    """

    def __init__(self, path: Path = STATE_PATH) -> None:
        self._path = path
        self._data: dict[str, dict] = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, record_id: str) -> dict | None:
        """Restituisce l'entry per record_id, o None se non presente."""
        return self._data.get(record_id)

    def set(self, record_id: str, entry: dict) -> None:
        """Salva/aggiorna l'entry per record_id e persiste su disco.

        La scrittura su disco avviene prima della mutazione in memoria: se _save
        solleva OSError, _data rimane invariato (memoria e disco restano consistenti).
        """
        new_data = {**self._data, record_id: entry}
        self._save(new_data)
        self._data = new_data

    def all(self) -> dict[str, dict]:
        """Restituisce una copia del dizionario interno completo."""
        return dict(self._data)

    def clear(self) -> None:
        """Azzera lo stato in memoria e su disco."""
        self._save({})
        self._data = {}

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
