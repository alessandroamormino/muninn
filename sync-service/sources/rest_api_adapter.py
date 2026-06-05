"""RestAPIAdapter — generic HTTP source adapter (Phase 8, SRC-07)."""
from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime

import requests

from sources.base import BaseSourceAdapter
from sources.json_adapter import AdapterError, _resolve_env_vars
from config.settings import SourceConfig, SyncConfig, VectorStoreConfig

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30  # seconds


class RestAPIAdapter(BaseSourceAdapter):
    def __init__(
        self,
        source_cfg: SourceConfig,
        sync_cfg: SyncConfig,
        weaviate_cfg: VectorStoreConfig,
    ) -> None:
        if not source_cfg.url:
            raise ValueError("RestAPIAdapter requires source.url")
        self._url = source_cfg.url
        self._auth = source_cfg.auth
        self._pagination = source_cfg.pagination
        self._params: dict = dict(source_cfg.params)  # defensive copy
        self._method = source_cfg.method
        self._json_key = source_cfg.json_key
        self._id_field = source_cfg.id_field
        self._hash_fields = sync_cfg.hash_fields

    # ---------- public BaseSourceAdapter API ----------

    def fetch_records(self) -> list[dict]:
        all_records: list[dict] = []
        url = self._url
        page_state = {"page": self._pagination.start_page, "offset": 0}
        is_first_request = True
        pages_fetched = 0

        while True:
            pages_fetched += 1
            if pages_fetched > self._pagination.max_pages:
                logger.warning(
                    "RestAPIAdapter hit max_pages safety cap (%d) — stopping pagination",
                    self._pagination.max_pages,
                )
                break

            headers = self._build_headers()
            # On cursor follow-up hops, do NOT re-apply static or auth params —
            # the next URL already contains them. Only the first request uses static params.
            if self._pagination.type == "cursor" and not is_first_request:
                params: dict = {}
            else:
                params = dict(self._params)
                self._apply_auth_params(params)
                self._apply_pagination_params(params, page_state)

            data = self._do_request(url, headers, params)
            page_records = self._extract_records(data)
            all_records.extend(page_records)

            next_url, should_break = self._advance_pagination(
                data, page_records, page_state, url
            )
            if should_break:
                break
            url = next_url
            is_first_request = False

        self._validate_id_field(all_records)
        return self._filter_valid(all_records)

    def fetch_new_records(self, since: datetime) -> list[dict]:
        """REST APIs generally lack server-side time filters — return all records.
        SyncEngine's hash-based deduplication handles change detection."""
        return self.fetch_records()

    def get_record_id(self, record: dict) -> str:
        return str(record[self._id_field])

    def get_record_hash(self, record: dict) -> str:
        payload = "|".join(str(record.get(f, "")) for f in self._hash_fields)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    # ---------- private helpers ----------

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        auth = self._auth
        if auth.type == "bearer":
            token = _resolve_env_vars(auth.token or "")
            headers["Authorization"] = f"Bearer {token}"
        elif auth.type == "api_key_header":
            key = _resolve_env_vars(auth.key or "")
            header_name = auth.header_name or "X-Api-Key"
            headers[header_name] = key
        elif auth.type == "basic":
            user = _resolve_env_vars(auth.username or "")
            pwd = _resolve_env_vars(auth.password or "")
            encoded = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        # none and api_key_param: no headers added
        return headers

    def _apply_auth_params(self, params: dict) -> None:
        if self._auth.type == "api_key_param":
            param_name = self._auth.param_name or "api_key"
            params[param_name] = _resolve_env_vars(self._auth.key or "")

    def _apply_pagination_params(self, params: dict, page_state: dict) -> None:
        pag = self._pagination
        if pag.type == "page":
            params[pag.page_param] = page_state["page"]
        elif pag.type == "offset":
            params[pag.offset_param] = page_state["offset"]
            params[pag.limit_param] = pag.page_size

    def _do_request(self, url: str, headers: dict, params: dict) -> dict | list:
        try:
            if self._method == "POST":
                response = requests.post(
                    url, headers=headers, params=params, timeout=_REQUEST_TIMEOUT
                )
            else:
                response = requests.get(
                    url, headers=headers, params=params, timeout=_REQUEST_TIMEOUT
                )
            response.raise_for_status()
            try:
                return response.json()
            except (ValueError, requests.exceptions.JSONDecodeError) as exc:
                raise AdapterError(
                    f"Response from {url} is not valid JSON: {exc}"
                ) from exc
        except requests.exceptions.Timeout as exc:
            raise AdapterError(
                f"Request to {url} timed out after {_REQUEST_TIMEOUT} seconds"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AdapterError(f"Failed to fetch records from {url}: {exc}") from exc

    def _extract_records(self, data) -> list[dict]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if self._json_key is None:
                raise ValueError(
                    "REST response is an object but source.json_key is not configured."
                )
            if self._json_key not in data:
                raise ValueError(
                    f"json_key '{self._json_key}' not found in REST response keys: "
                    f"{list(data.keys())}"
                )
            return data[self._json_key]
        raise ValueError(f"Unexpected REST response root type: {type(data).__name__}")

    def _advance_pagination(
        self,
        data,
        page_records: list,
        page_state: dict,
        current_url: str,
    ) -> tuple[str, bool]:
        pag = self._pagination
        if pag.type == "none":
            return current_url, True
        if pag.type == "cursor":
            next_url = data.get(pag.next_key) if isinstance(data, dict) and pag.next_key else None
            if not next_url:
                return current_url, True
            return next_url, False
        if pag.type == "page":
            total_pages = (
                data.get(pag.total_pages_key, 1) if isinstance(data, dict) else 1
            )
            page_state["page"] += 1
            if page_state["page"] > total_pages or not page_records:
                return current_url, True
            return current_url, False
        if pag.type == "offset":
            if not page_records:
                return current_url, True
            page_state["offset"] += pag.page_size
            return current_url, False
        return current_url, True

    def _validate_id_field(self, records: list[dict]) -> None:
        if records and self._id_field not in records[0]:
            raise ValueError(
                f"id_field '{self._id_field}' not found in record keys: "
                f"{list(records[0].keys())}"
            )

    def _filter_valid(self, records: list[dict]) -> list[dict]:
        valid = []
        for i, record in enumerate(records):
            val = record.get(self._id_field)
            if val is None or val == "":
                logger.warning(
                    "Skipping record at index %d: missing or empty id_field '%s'",
                    i,
                    self._id_field,
                )
                continue
            valid.append(record)
        return valid
