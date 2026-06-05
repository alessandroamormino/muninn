"""JSONAdapter — reads records from a local JSON file or remote URL."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import requests

from sources.base import BaseSourceAdapter
from config.settings import SourceConfig, SyncConfig, VectorStoreConfig

logger = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class AdapterError(Exception):
    """Raised for unrecoverable adapter errors (network unreachable, timeouts, etc.)."""


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR_NAME} tokens with os.getenv(VAR_NAME, '')."""
    return _ENV_VAR_RE.sub(lambda m: os.getenv(m.group(1), ""), value)


class JSONAdapter(BaseSourceAdapter):
    def __init__(
        self,
        source_cfg: SourceConfig,
        sync_cfg: SyncConfig,
        weaviate_cfg: VectorStoreConfig,
    ) -> None:
        self._url = source_cfg.url
        self._file_path = Path(source_cfg.file_path) if source_cfg.file_path else None
        self._auth_header = source_cfg.auth_header
        self._json_key = source_cfg.json_key
        self._id_field = source_cfg.id_field
        self._hash_fields = sync_cfg.hash_fields
        self._text_fields = weaviate_cfg.text_fields

    def _extract_records(self, data: object) -> list[dict]:
        """Extract records list from parsed JSON (array or object)."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if self._json_key is None:
                raise ValueError(
                    "JSON response is an object but source.json_key is not configured. "
                    "Set source.json_key to the key that holds the records array."
                )
            if self._json_key not in data:
                raise ValueError(
                    f"json_key '{self._json_key}' not found in JSON response keys: "
                    f"{list(data.keys())}"
                )
            return data[self._json_key]
        raise ValueError(f"Unexpected JSON root type: {type(data).__name__}")

    def _validate_id_field(self, records: list[dict]) -> None:
        """Raise ValueError if id_field is absent from first record (fail-fast)."""
        if records and self._id_field not in records[0]:
            raise ValueError(
                f"id_field '{self._id_field}' not found in record keys: "
                f"{list(records[0].keys())}"
            )

    def _filter_valid(self, records: list[dict]) -> list[dict]:
        """Skip records missing id_field value, log a warning for each."""
        valid = []
        for i, record in enumerate(records):
            if not record.get(self._id_field):
                logger.warning(
                    "Skipping record at index %d: missing or empty id_field '%s'",
                    i,
                    self._id_field,
                )
                continue
            valid.append(record)
        return valid

    def _load_from_file(self) -> list[dict]:
        if not self._file_path.exists():
            raise FileNotFoundError(f"JSON file not found: {self._file_path}")
        with open(self._file_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return self._extract_records(data)

    def _load_from_url(self) -> list[dict]:
        headers: dict[str, str] = {}
        if self._auth_header:
            headers["Authorization"] = _resolve_env_vars(self._auth_header)
        try:
            response = requests.get(self._url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.Timeout as exc:
            raise AdapterError(
                f"Request to {self._url} timed out after 30 seconds"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AdapterError(
                f"Failed to fetch records from {self._url}: {exc}"
            ) from exc
        return self._extract_records(data)

    def fetch_records(self) -> list[dict]:
        if self._url:
            records = self._load_from_url()
        elif self._file_path:
            records = self._load_from_file()
        else:
            raise ValueError(
                "JSONAdapter requires either source.url or source.file_path"
            )
        self._validate_id_field(records)
        return self._filter_valid(records)

    def fetch_new_records(self, since: datetime) -> list[dict]:
        """Return records newer than `since` by updated_at, or all if absent."""
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
