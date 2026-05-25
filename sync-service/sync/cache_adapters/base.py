"""BaseCacheAdapter — tutte le implementazioni di cache devono implementare questa interfaccia."""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseCacheAdapter(ABC):
    @abstractmethod
    def get(
        self,
        q: str,
        collection: str,
        filters: str | None,
        min_score: float | None,
    ) -> dict | None:
        """Cerca un risultato in cache. Restituisce dict o None se assente/scaduto."""

    @abstractmethod
    def set(
        self,
        q: str,
        collection: str,
        filters: str | None,
        min_score: float | None,
        results: dict,
        ttl_seconds: int | None = None,
    ) -> None:
        """Salva un risultato in cache con TTL opzionale."""

    @abstractmethod
    def invalidate_collection(self, collection: str) -> None:
        """Rimuove tutte le entry di cache per la collection specificata."""

    @abstractmethod
    def close(self) -> None:
        """Chiude le risorse (connessioni DB, ecc.)."""
