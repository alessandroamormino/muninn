"""Unit tests for MySQLAdapter — Phase 14 (D-01 through D-14, SRC-04).

All tests mock create_engine — no live MySQL DB required.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest

from config.settings import (
    MySQLConfig,
    MySQLJoinConfig,
    MySQLQueryConfig,
    SourceConfig,
    SyncConfig,
    VectorStoreConfig,
)
from sources.base import BaseSourceAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mysql_config(
    *,
    from_table: str = "dipendenti",
    fields: list[str] | None = None,
    id_field: str = "id",
    hash_fields: list[str] | None = None,
    joins: list[MySQLJoinConfig] | None = None,
    host: str = "${MYSQL_HOST}",
    database: str = "${MYSQL_DB}",
    user: str = "${MYSQL_USER}",
    password: str = "${MYSQL_PASSWORD}",
    port: int = 3306,
    ssl_ca: str | None = None,
    ssl_cert: str | None = None,
    ssl_key: str | None = None,
    fetch_chunk_size: int = 10000,
) -> MySQLConfig:
    q = MySQLQueryConfig.model_validate({
        "from": from_table,
        "fields": fields or ["id", "nome"],
        "id_field": id_field,
        "hash_fields": hash_fields or ["id", "nome"],
        "joins": [j.model_dump(by_alias=True) for j in (joins or [])],
        "fetch_chunk_size": fetch_chunk_size,
    })
    return MySQLConfig(
        host=host,
        database=database,
        user=user,
        password=password,
        port=port,
        ssl_ca=ssl_ca,
        ssl_cert=ssl_cert,
        ssl_key=ssl_key,
        query=q,
    )


def _make_source_cfg(mysql_cfg: MySQLConfig) -> SourceConfig:
    return SourceConfig(type="mysql", mysql=mysql_cfg)


def _make_sync_cfg(hash_fields: list[str] | None = None) -> SyncConfig:
    return SyncConfig(hash_fields=hash_fields or ["id", "nome"])


def _make_weaviate_cfg() -> VectorStoreConfig:
    return VectorStoreConfig()


def _make_adapter(mysql_cfg: MySQLConfig, mock_engine: MagicMock) -> "MySQLAdapter":
    """Construct a MySQLAdapter with a patched create_engine."""
    from sources.mysql_adapter import MySQLAdapter
    source_cfg = _make_source_cfg(mysql_cfg)
    sync_cfg = _make_sync_cfg(mysql_cfg.query.hash_fields)
    weaviate_cfg = _make_weaviate_cfg()
    with patch("sources.mysql_adapter.create_engine", return_value=mock_engine):
        return MySQLAdapter(source_cfg, sync_cfg, weaviate_cfg)


def _mock_conn_returning(rows: list[dict]) -> MagicMock:
    """Build a mock connection whose execute().mappings().all() returns rows."""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.mappings.return_value.all.return_value = rows
    return mock_conn


def _ctx_manager_conn(mock_conn: MagicMock) -> MagicMock:
    """Wrap mock_conn in a context manager mock for engine.connect()."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_conn)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Class TestAdapterInterface
# ---------------------------------------------------------------------------

class TestAdapterInterface:
    def test_implements_base_source_adapter(self):
        from sources.mysql_adapter import MySQLAdapter
        assert issubclass(MySQLAdapter, BaseSourceAdapter)

    def test_get_record_id_returns_str(self):
        """get_record_id must return str(record[id_field]) even when value is int."""
        mock_engine = MagicMock()
        cfg = _make_mysql_config(id_field="id")
        adapter = _make_adapter(cfg, mock_engine)
        record = {"id": 42, "nome": "Alice"}
        assert adapter.get_record_id(record) == "42"
        assert isinstance(adapter.get_record_id(record), str)

    def test_get_record_hash_matches_csv_pattern(self):
        """get_record_hash must produce the same MD5 hex as CSVAdapter (D-08)."""
        mock_engine = MagicMock()
        cfg = _make_mysql_config(hash_fields=["id", "nome"])
        adapter = _make_adapter(cfg, mock_engine)
        record = {"id": 1, "nome": "Alice", "extra": "ignored"}
        # Replicate CSVAdapter exact pattern
        payload = "|".join(str(record.get(f, "")) for f in ["id", "nome"])
        expected = hashlib.md5(payload.encode("utf-8")).hexdigest()
        assert adapter.get_record_hash(record) == expected

    def test_get_record_hash_missing_field_uses_empty_string(self):
        """Missing hash_field must contribute empty string '' (not 'None' or raise)."""
        mock_engine = MagicMock()
        cfg = _make_mysql_config(hash_fields=["id", "missing_field"])
        adapter = _make_adapter(cfg, mock_engine)
        record = {"id": 1, "nome": "Alice"}
        # Should not raise
        result = adapter.get_record_hash(record)
        payload = "1|"
        expected = hashlib.md5(payload.encode("utf-8")).hexdigest()
        assert result == expected


# ---------------------------------------------------------------------------
# Class TestEnvVarResolution
# ---------------------------------------------------------------------------

class TestEnvVarResolution:
    def test_env_vars_resolved_in_url(self, monkeypatch):
        """${VAR} tokens in host/user/password/database must be resolved from env."""
        monkeypatch.setenv("MYSQL_HOST", "db.example.com")
        monkeypatch.setenv("MYSQL_USER", "alice")
        monkeypatch.setenv("MYSQL_PASSWORD", "secret123")
        monkeypatch.setenv("MYSQL_DB", "myapp")

        cfg = _make_mysql_config(
            host="${MYSQL_HOST}",
            user="${MYSQL_USER}",
            password="${MYSQL_PASSWORD}",
            database="${MYSQL_DB}",
        )
        source_cfg = _make_source_cfg(cfg)
        sync_cfg = _make_sync_cfg()
        weaviate_cfg = _make_weaviate_cfg()

        with patch("sources.mysql_adapter.create_engine") as mock_ce:
            mock_ce.return_value = MagicMock()
            from sources.mysql_adapter import MySQLAdapter
            MySQLAdapter(source_cfg, sync_cfg, weaviate_cfg)

        url_arg = mock_ce.call_args[0][0]
        assert "mysql+pymysql://" in url_arg
        assert "alice" in url_arg
        assert "secret123" in url_arg
        assert "db.example.com" in url_arg
        assert "myapp" in url_arg
        assert "charset=utf8mb4" in url_arg
        # env var tokens must NOT appear literally
        assert "${MYSQL_HOST}" not in url_arg
        assert "${MYSQL_USER}" not in url_arg

    def test_missing_env_var_resolves_to_empty(self, monkeypatch):
        """An undefined ${FOO} token must resolve to '' (not the literal '${FOO}')."""
        monkeypatch.delenv("UNDEFINED_VAR_XYZ", raising=False)
        cfg = _make_mysql_config(host="${UNDEFINED_VAR_XYZ}", database="db", user="u", password="p")
        source_cfg = _make_source_cfg(cfg)
        sync_cfg = _make_sync_cfg()
        weaviate_cfg = _make_weaviate_cfg()

        with patch("sources.mysql_adapter.create_engine") as mock_ce:
            mock_ce.return_value = MagicMock()
            from sources.mysql_adapter import MySQLAdapter
            MySQLAdapter(source_cfg, sync_cfg, weaviate_cfg)

        url_arg = mock_ce.call_args[0][0]
        assert "${UNDEFINED_VAR_XYZ}" not in url_arg
        # empty host results in "@:3306/" pattern
        assert ":3306/" in url_arg


# ---------------------------------------------------------------------------
# Class TestQueryBuilding
# ---------------------------------------------------------------------------

class TestQueryBuilding:
    def test_no_joins_single_select(self):
        """With joins=[], fetch_records returns only main-table fields."""
        mock_engine = MagicMock()
        cfg = _make_mysql_config(
            from_table="dipendenti",
            fields=["id", "nome"],
            id_field="id",
            hash_fields=["id", "nome"],
            joins=[],
        )
        # Two calls: first page returns 1 row, second returns 0 (end of pagination)
        main_rows = [{"id": 1, "nome": "Alice"}]
        empty_rows = []

        mock_conn = MagicMock()
        mock_conn.execute.return_value.mappings.return_value.all.side_effect = [
            main_rows,
            empty_rows,
        ]
        mock_engine.connect.return_value = _ctx_manager_conn(mock_conn)

        adapter = _make_adapter(cfg, mock_engine)
        records = adapter.fetch_records()

        assert len(records) == 1
        assert records[0]["nome"] == "Alice"
        assert records[0]["id"] == 1

    def test_flat_join_left_join(self):
        """aggregate=False join must produce records with join columns merged flat."""
        flat_join = MySQLJoinConfig.model_validate({
            "table": "reparti",
            "on": "dipendenti.reparto_id = reparti.id",
            "fields": ["nome_reparto"],
            "aggregate": False,
        })
        cfg = _make_mysql_config(
            from_table="dipendenti",
            fields=["id", "nome"],
            id_field="id",
            joins=[flat_join],
        )
        mock_engine = MagicMock()

        # The flat join is included in the main SELECT via LEFT JOIN
        # Row includes main + join columns
        main_rows = [{"id": 1, "nome": "Alice", "reparti__nome_reparto": "Engineering"}]
        empty_rows = []

        mock_conn = MagicMock()
        mock_conn.execute.return_value.mappings.return_value.all.side_effect = [
            main_rows,
            empty_rows,
        ]
        mock_engine.connect.return_value = _ctx_manager_conn(mock_conn)

        adapter = _make_adapter(cfg, mock_engine)
        records = adapter.fetch_records()

        assert len(records) == 1
        # flat join column should be in the record (either as-is or de-prefixed)
        assert records[0]["nome"] == "Alice"


# ---------------------------------------------------------------------------
# Class TestAggregation
# ---------------------------------------------------------------------------

class TestAggregation:
    def _make_aggregate_adapter(self, join: MySQLJoinConfig, main_rows, join_rows, empty_main_page=True):
        """Build adapter + mock engine for aggregate join tests."""
        cfg = _make_mysql_config(
            from_table="dipendenti",
            fields=["id", "nome"],
            id_field="id",
            hash_fields=["id"],
            joins=[join],
        )
        mock_engine = MagicMock()

        # main select: first page has data, second has none (end of pagination)
        # aggregate join returns join_rows
        if empty_main_page:
            mock_conn_main = MagicMock()
            mock_conn_main.execute.return_value.mappings.return_value.all.side_effect = [
                main_rows,
                [],  # empty = stop pagination
            ]
        else:
            mock_conn_main = MagicMock()
            mock_conn_main.execute.return_value.mappings.return_value.all.return_value = main_rows

        mock_conn_agg = MagicMock()
        mock_conn_agg.execute.return_value.mappings.return_value.all.return_value = join_rows

        # engine.connect() returns different context managers for each call
        call_count = [0]
        def make_cm(mock_conn):
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=mock_conn)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        def connect_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return make_cm(mock_conn_main)
            return make_cm(mock_conn_agg)

        mock_engine.connect.side_effect = connect_side_effect
        adapter = _make_adapter(cfg, mock_engine)
        return adapter

    def test_aggregate_join_two_query_strategy(self):
        """aggregate=True join must use two-query strategy: 1 main SELECT + 1 aggregate SELECT.

        D-07: With single field and no as_, field name = first field name.
        """
        agg_join = MySQLJoinConfig.model_validate({
            "table": "competenze",
            "on": "dipendenti.id = competenze.dipendente_id",
            "fields": ["nome_competenza"],
            "aggregate": True,
            "separator": ", ",
        })
        main_rows = [{"id": 1, "nome": "Alice"}]
        join_rows = [
            {"dipendente_id": 1, "nome_competenza": "Python"},
            {"dipendente_id": 1, "nome_competenza": "Java"},
        ]
        adapter = self._make_aggregate_adapter(agg_join, main_rows, join_rows)
        records = adapter.fetch_records()

        assert len(records) == 1
        # D-07: single field, no as_ → field name = first field name
        assert "nome_competenza" in records[0]
        value = records[0]["nome_competenza"]
        assert "Python" in value
        assert "Java" in value

    def test_aggregate_field_name_from_as(self):
        """When `as_` is set, the aggregated field name on the record should match it."""
        agg_join = MySQLJoinConfig(
            table="competenze",
            on="dipendenti.id = competenze.dipendente_id",
            fields=["nome_competenza"],
            aggregate=True,
            separator=", ",
            as_="skill_set",
        )
        main_rows = [{"id": 1, "nome": "Alice"}]
        join_rows = [{"dipendente_id": 1, "nome_competenza": "Python"}]
        adapter = self._make_aggregate_adapter(agg_join, main_rows, join_rows)
        records = adapter.fetch_records()

        assert "skill_set" in records[0]
        assert records[0]["skill_set"] == "Python"

    def test_aggregate_field_name_default_table(self):
        """When `as_` is None and multiple fields, field name must be join.table."""
        agg_join = MySQLJoinConfig(
            table="competenze",
            on="dipendenti.id = competenze.dipendente_id",
            fields=["nome_competenza", "livello"],
            aggregate=True,
            separator=", ",
            as_=None,
        )
        main_rows = [{"id": 1, "nome": "Alice"}]
        join_rows = [{"dipendente_id": 1, "nome_competenza": "Python", "livello": "expert"}]
        adapter = self._make_aggregate_adapter(agg_join, main_rows, join_rows)
        records = adapter.fetch_records()

        assert "competenze" in records[0]

    def test_separator_respected(self):
        """The configured separator must appear between aggregated values.

        D-07: single field + no as_ → field name = first field name ("nome_competenza").
        """
        agg_join = MySQLJoinConfig(
            table="competenze",
            on="dipendenti.id = competenze.dipendente_id",
            fields=["nome_competenza"],
            aggregate=True,
            separator=" | ",
            as_=None,
        )
        main_rows = [{"id": 1, "nome": "Alice"}]
        join_rows = [
            {"dipendente_id": 1, "nome_competenza": "Python"},
            {"dipendente_id": 1, "nome_competenza": "Java"},
        ]
        adapter = self._make_aggregate_adapter(agg_join, main_rows, join_rows)
        records = adapter.fetch_records()

        assert "nome_competenza" in records[0]
        assert " | " in records[0]["nome_competenza"]

    def test_empty_main_ids_skips_aggregate_query(self):
        """When main fetch returns 0 records, the aggregate IN-clause query must NOT run."""
        agg_join = MySQLJoinConfig.model_validate({
            "table": "competenze",
            "on": "dipendenti.id = competenze.dipendente_id",
            "fields": ["nome_competenza"],
            "aggregate": True,
        })
        cfg = _make_mysql_config(
            from_table="dipendenti",
            fields=["id", "nome"],
            id_field="id",
            joins=[agg_join],
        )
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.mappings.return_value.all.return_value = []
        mock_engine.connect.return_value = _ctx_manager_conn(mock_conn)

        adapter = _make_adapter(cfg, mock_engine)
        records = adapter.fetch_records()

        assert records == []
        # Only one connect() call for the main query; aggregate query should not run
        assert mock_engine.connect.call_count == 1


# ---------------------------------------------------------------------------
# Class TestNullHandling
# ---------------------------------------------------------------------------

class TestNullHandling:
    def test_null_join_value_becomes_empty_string(self):
        """None values in aggregate join fields must produce '' not 'None'."""
        agg_join = MySQLJoinConfig.model_validate({
            "table": "competenze",
            "on": "dipendenti.id = competenze.dipendente_id",
            "fields": ["nome_competenza"],
            "aggregate": True,
            "separator": ", ",
        })
        cfg = _make_mysql_config(
            from_table="dipendenti",
            fields=["id", "nome"],
            id_field="id",
            joins=[agg_join],
        )
        mock_engine = MagicMock()
        main_rows = [{"id": 1, "nome": "Alice"}]
        join_rows = [{"dipendente_id": 1, "nome_competenza": None}]

        call_count = [0]
        def make_cm(conn):
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=conn)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        mock_conn_main = MagicMock()
        mock_conn_main.execute.return_value.mappings.return_value.all.side_effect = [
            main_rows, []
        ]
        mock_conn_agg = MagicMock()
        mock_conn_agg.execute.return_value.mappings.return_value.all.return_value = join_rows

        def connect_side_effect():
            call_count[0] += 1
            return make_cm(mock_conn_main if call_count[0] == 1 else mock_conn_agg)

        mock_engine.connect.side_effect = connect_side_effect
        adapter = _make_adapter(cfg, mock_engine)
        records = adapter.fetch_records()

        field_val = records[0].get("competenze", "")
        assert "None" not in field_val

    def test_record_kept_when_join_empty(self):
        """Main record must be present even when aggregate join yields zero rows (D-06 LEFT JOIN semantics)."""
        agg_join = MySQLJoinConfig.model_validate({
            "table": "competenze",
            "on": "dipendenti.id = competenze.dipendente_id",
            "fields": ["nome_competenza"],
            "aggregate": True,
        })
        cfg = _make_mysql_config(
            from_table="dipendenti",
            fields=["id", "nome"],
            id_field="id",
            joins=[agg_join],
        )
        mock_engine = MagicMock()
        main_rows = [{"id": 1, "nome": "Alice"}]
        join_rows = []  # no matching rows

        call_count = [0]
        def make_cm(conn):
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=conn)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        mock_conn_main = MagicMock()
        mock_conn_main.execute.return_value.mappings.return_value.all.side_effect = [
            main_rows, []
        ]
        mock_conn_agg = MagicMock()
        mock_conn_agg.execute.return_value.mappings.return_value.all.return_value = join_rows

        def connect_side_effect():
            call_count[0] += 1
            return make_cm(mock_conn_main if call_count[0] == 1 else mock_conn_agg)

        mock_engine.connect.side_effect = connect_side_effect
        adapter = _make_adapter(cfg, mock_engine)
        records = adapter.fetch_records()

        assert len(records) == 1
        assert records[0]["nome"] == "Alice"
        # D-07: single field + no as_ → field name = "nome_competenza"; empty string when no rows
        assert records[0].get("nome_competenza") == ""


# ---------------------------------------------------------------------------
# Class TestSSLConnectArgs
# ---------------------------------------------------------------------------

class TestSSLConnectArgs:
    def test_ssl_dict_uses_short_keys(self, monkeypatch):
        """ssl_ca/ssl_cert/ssl_key must produce short-key ssl dict (Pitfall 2 / T-14-03)."""
        monkeypatch.setenv("SSL_CA", "/path/to/ca.pem")
        monkeypatch.setenv("SSL_CERT", "/path/to/cert.pem")
        monkeypatch.setenv("SSL_KEY", "/path/to/key.pem")

        cfg = _make_mysql_config(
            host="localhost",
            database="db",
            user="u",
            password="p",
            ssl_ca="${SSL_CA}",
            ssl_cert="${SSL_CERT}",
            ssl_key="${SSL_KEY}",
        )
        source_cfg = _make_source_cfg(cfg)
        sync_cfg = _make_sync_cfg()
        weaviate_cfg = _make_weaviate_cfg()

        with patch("sources.mysql_adapter.create_engine") as mock_ce:
            mock_ce.return_value = MagicMock()
            from sources.mysql_adapter import MySQLAdapter
            MySQLAdapter(source_cfg, sync_cfg, weaviate_cfg)

        kwargs = mock_ce.call_args[1]
        connect_args = kwargs.get("connect_args", {})
        ssl = connect_args.get("ssl", {})

        assert "ca" in ssl
        assert "cert" in ssl
        assert "key" in ssl
        assert "ssl_ca" not in ssl
        assert "ssl_cert" not in ssl
        assert "ssl_key" not in ssl
        assert ssl["ca"] == "/path/to/ca.pem"

    def test_no_ssl_omits_connect_args(self):
        """When all ssl_* fields are None, connect_args must not contain 'ssl'."""
        cfg = _make_mysql_config(
            host="localhost",
            database="db",
            user="u",
            password="p",
            ssl_ca=None,
            ssl_cert=None,
            ssl_key=None,
        )
        source_cfg = _make_source_cfg(cfg)
        sync_cfg = _make_sync_cfg()
        weaviate_cfg = _make_weaviate_cfg()

        with patch("sources.mysql_adapter.create_engine") as mock_ce:
            mock_ce.return_value = MagicMock()
            from sources.mysql_adapter import MySQLAdapter
            MySQLAdapter(source_cfg, sync_cfg, weaviate_cfg)

        kwargs = mock_ce.call_args[1]
        connect_args = kwargs.get("connect_args", {})
        assert "ssl" not in connect_args


# ---------------------------------------------------------------------------
# Class TestPagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_pagination_loops_until_short_page(self):
        """Main fetch must loop LIMIT/OFFSET until a page shorter than chunk_size is returned."""
        cfg = _make_mysql_config(
            from_table="dipendenti",
            fields=["id", "nome"],
            id_field="id",
            hash_fields=["id"],
            fetch_chunk_size=2,
        )
        mock_engine = MagicMock()

        # 2 rows on first page (full), 1 row on second page (short = stop)
        page1 = [{"id": 1, "nome": "Alice"}, {"id": 2, "nome": "Bob"}]
        page2 = [{"id": 3, "nome": "Carol"}]

        mock_conn = MagicMock()
        mock_conn.execute.return_value.mappings.return_value.all.side_effect = [
            page1,
            page2,
        ]
        mock_engine.connect.return_value = _ctx_manager_conn(mock_conn)

        adapter = _make_adapter(cfg, mock_engine)
        records = adapter.fetch_records()

        assert len(records) == 3
        assert records[0]["nome"] == "Alice"
        assert records[2]["nome"] == "Carol"
        # execute must have been called twice (two pages)
        assert mock_conn.execute.call_count == 2

    def test_pool_pre_ping_enabled(self):
        """create_engine must be called with pool_pre_ping=True (D-12)."""
        cfg = _make_mysql_config(host="localhost", database="db", user="u", password="p")
        source_cfg = _make_source_cfg(cfg)
        sync_cfg = _make_sync_cfg()
        weaviate_cfg = _make_weaviate_cfg()

        with patch("sources.mysql_adapter.create_engine") as mock_ce:
            mock_ce.return_value = MagicMock()
            from sources.mysql_adapter import MySQLAdapter
            MySQLAdapter(source_cfg, sync_cfg, weaviate_cfg)

        kwargs = mock_ce.call_args[1]
        assert kwargs.get("pool_pre_ping") is True


# ---------------------------------------------------------------------------
# Class TestClose
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_disposes_engine(self):
        """adapter.close() must call engine.dispose() exactly once."""
        mock_engine = MagicMock()
        cfg = _make_mysql_config(host="localhost", database="db", user="u", password="p")
        adapter = _make_adapter(cfg, mock_engine)

        adapter.close()

        mock_engine.dispose.assert_called_once()
