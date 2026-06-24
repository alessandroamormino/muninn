"""RestAPIAdapter — generic HTTP source adapter (Phase 8, SRC-07)."""
from __future__ import annotations

import base64
import hashlib
import logging
import random
import time
from datetime import datetime

import requests

from sources.base import BaseSourceAdapter
from sources.json_adapter import AdapterError, _resolve_env_vars
from config.settings import SourceConfig, SyncConfig, VectorStoreConfig

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30  # seconds

# Rate-limit retry-with-backoff (Phase 16 — needed for rate-limited APIs).
# Different providers signal "back off" with different status codes:
# Discogs uses 429, MusicBrainz uses 503 for its own rate limiter (genuine outages
# also return 503, but backing off and retrying is the right move either way).
_RETRYABLE_STATUS_CODES = {429, 503}
_MAX_RETRIES = 5
_INITIAL_DELAY = 1.0   # seconds before first retry
_MAX_DELAY = 120.0     # cap so a stuck rate-limit window doesn't stall a sync indefinitely
_JITTER_FACTOR = 0.5


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
        self._static_headers = source_cfg.headers
        self._flatten = source_cfg.flatten

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

            # Proactive throttle: space out requests to respect provider rate limits
            # (e.g. MusicBrainz 1 req/sec). Applied before every request so the very
            # first one is delayed too — negligible cost, keeps us strictly under the cap.
            if self._pagination.request_delay > 0:
                time.sleep(self._pagination.request_delay)

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
        valid = self._filter_valid(all_records)
        return self._apply_flatten(valid)

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
        headers: dict[str, str] = {
            name: _resolve_env_vars(value) for name, value in self._static_headers.items()
        }
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
        for attempt in range(_MAX_RETRIES + 1):
            try:
                if self._method == "POST":
                    response = requests.post(
                        url, headers=headers, params=params, timeout=_REQUEST_TIMEOUT
                    )
                else:
                    response = requests.get(
                        url, headers=headers, params=params, timeout=_REQUEST_TIMEOUT
                    )
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                    self._sleep_before_retry(response, attempt, url)
                    continue
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
        raise AdapterError(
            f"Request to {url} rate-limited (HTTP {response.status_code}) "
            f"after {_MAX_RETRIES} retries"
        )

    def _sleep_before_retry(self, response: requests.Response, attempt: int, url: str) -> None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = _INITIAL_DELAY * (2.0 ** attempt)
        else:
            delay = min(_INITIAL_DELAY * (2.0 ** attempt), _MAX_DELAY)
        jitter = delay * _JITTER_FACTOR * (random.random() * 2 - 1)
        sleep_secs = max(0.0, delay + jitter)
        logger.warning(
            "HTTP %d from %s — retry %d/%d, sleeping %.1fs",
            response.status_code,
            url,
            attempt + 1,
            _MAX_RETRIES,
            sleep_secs,
        )
        time.sleep(sleep_secs)

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
                self._get_nested_value(data, pag.total_pages_key, default=1)
                if isinstance(data, dict)
                else 1
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

    def _apply_flatten(self, records: list[dict]) -> list[dict]:
        """Flatten configured nested fields into clean, comma-joined strings.

        For each `field: sub_key` rule, replace record[field] (a list of dicts or a
        single dict) with the extracted sub_key value(s). List → "v1, v2"; single
        dict → "v". Non-matching shapes (scalars, missing field) are left untouched
        so a misconfigured rule can never corrupt a record.
        """
        if not self._flatten:
            return records
        for record in records:
            for field, sub_key in self._flatten.items():
                if field not in record:
                    continue
                value = record[field]
                if isinstance(value, list):
                    parts = [
                        str(item[sub_key])
                        for item in value
                        if isinstance(item, dict) and item.get(sub_key) is not None
                    ]
                    record[field] = ", ".join(parts)
                elif isinstance(value, dict):
                    extracted = value.get(sub_key)
                    record[field] = "" if extracted is None else str(extracted)
        return records

    @staticmethod
    def _get_nested_value(data: dict, dotted_key: str, default=None):
        """Resolve a dot-notation key (e.g. "pagination.pages") against a nested dict.

        Falls back to `default` if any path segment is missing or non-dict,
        e.g. for Discogs-style responses where pagination metadata is nested.
        """
        current = data
        for part in dotted_key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

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
