"""BaseSourceAdapter — all source adapters must implement this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


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
