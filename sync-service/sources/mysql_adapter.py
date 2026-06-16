"""MySQLAdapter — reads records from a MySQL/MariaDB database.

Implements BaseSourceAdapter using SQLAlchemy 2.0 Core + PyMySQL driver.
All credentials are resolved from environment variables via ${VAR} tokens (D-11).
Query definition is fully declarative — no raw SQL accepted from external input (D-01, T-14-01).

Two-query strategy for aggregate joins (D-05):
  1. Single LEFT JOIN query for aggregate=false joins (many-to-one).
  2. Separate SELECT per aggregate=true join, results grouped in Python.

Pagination: keyset (WHERE id > :last_id ORDER BY id) — O(1) per page vs LIMIT/OFFSET O(n).
  fetch_records_chunked() yields one chunk at a time; RAM stays at O(chunk_size).
  fetch_records() accumulates all chunks for backward compat with incremental sync.

Security notes:
  - Credentials are resolved from env vars only — never logged (T-14-02).
  - SSL connect_args use short keys ("ca"/"cert"/"key") as required by PyMySQL (Pitfall 2 / T-14-03).
  - Column names come from declarative config (Pydantic-validated) — not from user input.
"""
from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

from config.settings import MySQLConfig, MySQLJoinConfig, SourceConfig, SyncConfig, VectorStoreConfig
from sources.base import BaseSourceAdapter
from sources.json_adapter import AdapterError, _resolve_env_vars

logger = logging.getLogger(__name__)


def _build_engine(cfg: MySQLConfig) -> Engine:
    """Build a SQLAlchemy engine from MySQLConfig with env-var-resolved credentials.

    Logs only host:port/database — NEVER the full URL (T-14-02).
    """
    host = _resolve_env_vars(cfg.host)
    port = cfg.port  # int — not env-var-resolved
    database = _resolve_env_vars(cfg.database)
    user = _resolve_env_vars(cfg.user)
    password = _resolve_env_vars(cfg.password)

    # charset=utf8mb4 required to handle emoji / 4-byte UTF-8 (Pitfall 4)
    url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"

    connect_args: dict[str, Any] = {}
    if cfg.ssl_ca or cfg.ssl_cert or cfg.ssl_key:
        # Short-key format required by PyMySQL (Pitfall 2 / T-14-03):
        # keys must be "ca"/"cert"/"key" — NOT "ssl_ca"/"ssl_cert"/"ssl_key"
        ssl_dict: dict[str, str] = {}
        if cfg.ssl_ca:
            ssl_dict["ca"] = _resolve_env_vars(cfg.ssl_ca)
        if cfg.ssl_cert:
            ssl_dict["cert"] = _resolve_env_vars(cfg.ssl_cert)
        if cfg.ssl_key:
            ssl_dict["key"] = _resolve_env_vars(cfg.ssl_key)
        connect_args["ssl"] = ssl_dict

    # Log only host:port/database — never the URL that contains the password
    logger.info("MySQLAdapter connecting to %s:%s/%s", host, port, database)

    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


def _parse_fk_col(on_clause: str, join_table: str) -> str:
    """Extract the FK column name from a JOIN ON clause.

    Parses "main_table.main_col = join_table.fk_col" → "fk_col".
    Only the right-side column reference (after the '=') is used.
    Raises AdapterError with a descriptive message if the format is not recognised.
    """
    parts = [p.strip() for p in on_clause.split("=")]
    if len(parts) != 2:
        raise AdapterError(
            f"Cannot parse FK column from join.on clause: {on_clause!r}. "
            "Expected format: 'left_table.left_col = right_table.right_col'"
        )
    right = parts[1]
    if "." in right:
        return right.split(".")[-1]
    return right


def _agg_row_value(row: Any, fields: list[str]) -> str:
    """Produce the per-row aggregated string value from the given fields.

    NULL values (None) are rendered as '' — never as literal 'None' (Pitfall 6 / D-06).
    """
    parts = []
    for f in fields:
        v = row[f]
        s = "" if v is None else str(v)
        if s:
            parts.append(s)
    return " ".join(parts)


class MySQLAdapter(BaseSourceAdapter):
    """Source adapter for MySQL/MariaDB databases.

    Reads records declaratively based on MySQLConfig (table, fields, joins).
    Supports flat (aggregate=false) and one-to-many aggregated (aggregate=true) JOINs.
    """

    def __init__(
        self,
        source_cfg: SourceConfig,
        sync_cfg: SyncConfig,
        weaviate_cfg: VectorStoreConfig,
    ) -> None:
        if source_cfg.mysql is None:
            raise ValueError(
                "source.mysql config block required when source.type='mysql'"
            )
        self._cfg = source_cfg.mysql
        self._id_field = self._cfg.query.id_field
        self._hash_fields = sync_cfg.hash_fields
        self._chunk_size = self._cfg.query.fetch_chunk_size
        self._engine: Engine | None = _build_engine(self._cfg)

    # ------------------------------------------------------------------
    # Public BaseSourceAdapter API
    # ------------------------------------------------------------------

    def fetch_records(self) -> list[dict]:
        """Fetch all records from the MySQL source (loads everything into memory).

        Used by run_incremental() and small-dataset callers. For large datasets
        use fetch_records_chunked() to avoid loading all records at once.
        """
        all_records: list[dict] = []
        for chunk in self.fetch_records_chunked(self._chunk_size):
            all_records.extend(chunk)
        return all_records

    def fetch_records_chunked(self, chunk_size: int = 1000) -> Iterator[list[dict]]:
        """Yield chunks of records using keyset pagination (O(1) per page).

        Each chunk has at most chunk_size records. Aggregate joins are applied
        per-chunk so the IN-clause stays small (chunk_size IDs max).
        RAM stays at O(chunk_size) regardless of total dataset size.
        """
        try:
            for chunk in self._fetch_chunked_keyset(chunk_size):
                self._apply_aggregate_joins(chunk)
                yield chunk
        except (OperationalError, DBAPIError) as exc:
            host = _resolve_env_vars(self._cfg.host)
            port = self._cfg.port
            database = _resolve_env_vars(self._cfg.database)
            raise AdapterError(
                f"MySQLAdapter failed to fetch records from {host}:{port}/{database}: {exc}"
            ) from exc

    def count_records(self) -> int | None:
        """Return total row count via SELECT COUNT(*) for accurate progress tracking."""
        cfg = self._cfg.query
        stmt = text(f"SELECT COUNT(*) FROM `{cfg.from_table}`")
        assert self._engine is not None
        with self._engine.connect() as conn:
            return conn.execute(stmt).scalar()

    def fetch_new_records(self, since: datetime) -> list[dict]:
        """Return all records (same as fetch_records).

        SyncEngine's hash comparison handles change detection (D-08 / Pitfall 7).
        Not relying on any `updated_at` column — matches CSVAdapter behaviour.
        """
        return self.fetch_records()

    def get_record_id(self, record: dict) -> str:
        """Return str(record[id_field]) — byte-identical to CSVAdapter (D-08)."""
        return str(record[self._id_field])

    def get_record_hash(self, record: dict) -> str:
        """Return MD5 hex of '|'-joined hash_fields — byte-identical to CSVAdapter (D-08)."""
        payload = "|".join(str(record.get(f, "")) for f in self._hash_fields)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def close(self) -> None:
        """Dispose the SQLAlchemy engine and release connection pool resources."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_chunked_keyset(self, chunk_size: int) -> Iterator[list[dict]]:
        """Yield chunks of main-table rows using keyset pagination.

        Uses WHERE id > :last_id ORDER BY id LIMIT :chunk_size — O(1) per page
        unlike LIMIT/OFFSET which degrades to O(n) as offset grows.

        Assumes id_field is indexed (primary key). Each call opens its own
        connection so idle connections are released back to the pool between pages.

        Column names come from Pydantic-validated config (T-14-01).
        """
        cfg = self._cfg.query
        flat_joins = [j for j in cfg.joins if not j.aggregate]

        # Build SELECT column list
        main_fields_sql = ", ".join(
            f"`{cfg.from_table}`.`{f}`" for f in cfg.fields
        ) if cfg.fields else f"`{cfg.from_table}`.*"

        join_col_parts: list[str] = []
        for jcfg in flat_joins:
            for field in jcfg.fields:
                alias = f"{jcfg.table}__{field}"
                join_col_parts.append(f"`{jcfg.table}`.`{field}` AS `{alias}`")

        all_cols_sql = main_fields_sql
        if join_col_parts:
            all_cols_sql += ", " + ", ".join(join_col_parts)

        join_clauses = ""
        for jcfg in flat_joins:
            join_clauses += f" LEFT JOIN `{jcfg.table}` ON {jcfg.on}"

        base_sql = f"SELECT {all_cols_sql} FROM `{cfg.from_table}`{join_clauses}"
        id_col = f"`{cfg.from_table}`.`{cfg.id_field}`"

        assert self._engine is not None  # guaranteed by __init__

        last_id: Any = None

        while True:
            if last_id is None:
                stmt = text(f"{base_sql} ORDER BY {id_col} LIMIT :limit")
                params: dict = {"limit": chunk_size}
            else:
                stmt = text(
                    f"{base_sql} WHERE {id_col} > :last_id"
                    f" ORDER BY {id_col} LIMIT :limit"
                )
                params = {"last_id": last_id, "limit": chunk_size}

            # One connection per page — released to pool between pages.
            with self._engine.connect() as conn:
                rows = conn.execute(stmt, params).mappings().all()

            if not rows:
                break

            chunk: list[dict] = []
            for row in rows:
                record: dict = {}
                for f in cfg.fields:
                    record[f] = row[f]
                for jcfg in flat_joins:
                    for field in jcfg.fields:
                        alias = f"{jcfg.table}__{field}"
                        if len(jcfg.fields) == 1:
                            target_key = jcfg.as_ or field
                        else:
                            target_key = jcfg.as_ or f"{jcfg.table}_{field}"
                        record[target_key] = row[alias] if alias in row else row.get(field)
                chunk.append(record)

            yield chunk

            if len(rows) < chunk_size:
                break

            last_id = rows[-1][cfg.id_field]

    def _apply_aggregate_joins(self, main_records: list[dict]) -> None:
        """Execute one SELECT per aggregate=true join and merge results into main_records.

        Two-query strategy (D-05): separate SELECT with expanding IN clause,
        then Python-side groupby. Main records are mutated in-place.

        Pitfall 5: skips the IN-clause query entirely when main_records is empty.
        Pitfall 6: NULL join column values become '' (not literal 'None').
        """
        if not main_records:
            return  # Pitfall 5 guard — empty IN clause is invalid SQL

        main_ids = [r[self._id_field] for r in main_records]

        assert self._engine is not None

        for jcfg in self._cfg.query.joins:
            if not jcfg.aggregate:
                continue

            fk_col = _parse_fk_col(jcfg.on, jcfg.table)

            # Build the SELECT with expanding IN bindparam (Pitfall 5)
            fields_sql = ", ".join(f"`{f}`" for f in jcfg.fields)
            stmt = text(
                f"SELECT {fields_sql}, `{fk_col}` FROM `{jcfg.table}` "
                f"WHERE `{fk_col}` IN :ids"
            ).bindparams(bindparam("ids", expanding=True))

            with self._engine.connect() as conn:
                join_rows = conn.execute(stmt, {"ids": main_ids}).mappings().all()

            # Group rows by FK value (as string to match get_record_id output)
            grouped: dict[str, list[str]] = defaultdict(list)
            for jr in join_rows:
                key = str(jr[fk_col])
                row_val = _agg_row_value(jr, jcfg.fields)
                if row_val:  # skip fully-empty rows
                    grouped[key].append(row_val)

            # Determine field name on the main record (D-07)
            if jcfg.as_:
                field_name = jcfg.as_
            elif len(jcfg.fields) == 1:
                field_name = jcfg.fields[0]
            else:
                field_name = jcfg.table

            # Merge into each main record
            for record in main_records:
                rid = str(record[self._id_field])
                record[field_name] = jcfg.separator.join(grouped.get(rid, []))
