"""BaseSourceAdapter — all source adapters must implement this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterator


class BaseSourceAdapter(ABC):
    @abstractmethod
    def fetch_records(self) -> list[dict]:
        """Return all records from the source."""

    @abstractmethod
    def fetch_new_records(self, since: datetime) -> list[dict]:
        """Return only records created or modified after `since`."""

    @abstractmethod
    def get_record_id(self, record: dict) -> str:
        """Return the unique identifier for a record."""

    @abstractmethod
    def get_record_hash(self, record: dict) -> str:
        """Return an MD5/SHA hash of the fields relevant for change detection."""

    def fetch_records_chunked(self, chunk_size: int = 1000) -> Iterator[list[dict]]:
        """Yield records in chunks of chunk_size.

        Default implementation loads all records via fetch_records() then splits.
        Adapters with large datasets (e.g. MySQLAdapter) override this to stream
        chunks directly from the source without loading everything into memory.
        """
        records = self.fetch_records()
        for i in range(0, len(records), chunk_size):
            yield records[i: i + chunk_size]
