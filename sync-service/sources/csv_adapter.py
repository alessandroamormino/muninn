"""CSVAdapter — reads records from a local CSV file."""
from __future__ import annotations

import csv
import hashlib
import logging
from datetime import datetime
from pathlib import Path

from sources.base import BaseSourceAdapter
from config.settings import SourceConfig, SyncConfig, VectorStoreConfig

logger = logging.getLogger(__name__)


class CSVAdapter(BaseSourceAdapter):
    def __init__(
        self,
        source_cfg: SourceConfig,
        sync_cfg: SyncConfig,
        weaviate_cfg: VectorStoreConfig,
    ) -> None:
        if not source_cfg.file_path:
            raise ValueError("source.file_path must be set for CSVAdapter")
        self._path = Path(source_cfg.file_path)
        self._id_field = source_cfg.id_field
        self._delimiter = source_cfg.delimiter
        self._hash_fields = sync_cfg.hash_fields
        self._text_fields = weaviate_cfg.text_fields

    def _read_rows(self) -> list[dict]:
        """Read all rows from the CSV. Raises FileNotFoundError if file is absent."""
        if not self._path.exists():
            raise FileNotFoundError(f"CSV file not found: {self._path}")
        with open(self._path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter=self._delimiter)
            # Normalize column names: replace spaces with underscores so Weaviate
            # property names (which forbid spaces) match config field names.
            rows = [{k.replace(" ", "_"): v for k, v in row.items()} for row in reader]
        if rows and self._id_field not in rows[0]:
            cols = list(rows[0].keys())
            raise ValueError(
                f"id_field '{self._id_field}' not found in CSV columns: {cols}"
            )
        return rows

    def _is_valid_record(self, record: dict, index: int) -> bool:
        """Return True if record has a non-empty id_field value. Log and skip otherwise."""
        if not record.get(self._id_field):
            logger.warning(
                "Skipping record at index %d: missing or empty id_field '%s'",
                index,
                self._id_field,
            )
            return False
        return True

    def fetch_records(self) -> list[dict]:
        rows = self._read_rows()
        return [r for i, r in enumerate(rows) if self._is_valid_record(r, i)]

    def fetch_new_records(self, since: datetime) -> list[dict]:
        """Return records newer than `since` by updated_at field, or all records if absent."""
        records = self.fetch_records()
        if records and "updated_at" not in records[0]:
            return records
        result = []
        for record in records:
            raw = record.get("updated_at", "")
            if not raw:
                result.append(record)
                continue
            try:
                updated = datetime.fromisoformat(raw)
                if updated > since:
                    result.append(record)
            except ValueError:
                logger.warning("Cannot parse updated_at '%s', including record", raw)
                result.append(record)
        return result

    def get_record_id(self, record: dict) -> str:
        return str(record[self._id_field])

    def get_record_hash(self, record: dict) -> str:
        payload = "|".join(str(record.get(f, "")) for f in self._hash_fields)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()
