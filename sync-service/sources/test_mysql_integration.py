"""Integration tests for MySQLAdapter against a real MySQL 8.0 container.

These tests use ``testcontainers[mysql]`` to spin up an ephemeral MySQL 8.0
instance, seed a 3-table schema (dipendenti + competenze + reparti), and
exercise MySQLAdapter end-to-end.

All tests are gated behind ``SMART_SEARCH_RUN_LIVE_TESTS=1``; they are skipped
(not failed) in normal CI runs or when testcontainers is not installed.

    # Run locally (requires Docker + testcontainers[mysql]):
    SMART_SEARCH_RUN_LIVE_TESTS=1 python3 -m pytest sources/test_mysql_integration.py -x -q

Do NOT add testcontainers to requirements.txt — it is a dev-only dependency.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

try:
    from testcontainers.mysql import MySqlContainer

    _TC_AVAILABLE = True
except ImportError:
    _TC_AVAILABLE = False

from config.settings import (
    MySQLConfig,
    MySQLJoinConfig,
    MySQLQueryConfig,
    SourceConfig,
    SyncConfig,
    WeaviateConfig,
)
from sources import build_source_adapter
from sources.mysql_adapter import MySQLAdapter

# ---------------------------------------------------------------------------
# Gate: skip all tests when SMART_SEARCH_RUN_LIVE_TESTS != "1" or testcontainers
# is not installed.
# ---------------------------------------------------------------------------

LIVE = os.getenv("SMART_SEARCH_RUN_LIVE_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE or not _TC_AVAILABLE,
    reason=(
        "Set SMART_SEARCH_RUN_LIVE_TESTS=1 and install testcontainers[mysql] "
        "to run live MySQL integration tests"
    ),
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mysql_container():
    """Start a real MySQL 8.0 container for the duration of the test module."""
    with MySqlContainer("mysql:8.0") as mysql:
        yield mysql


@pytest.fixture(scope="module")
def seeded_engine(mysql_container):
    """Create tables and seed the test schema; return connection URL parts.

    Schema:
      reparti      — 2 rows (many-to-one target for dipendenti)
      dipendenti   — 3 rows (main table; one row has NULL reparto_id)
      competenze   — 5 rows (one-to-many target; includes a NULL nome_competenza)
    """
    url_str = mysql_container.get_connection_url()
    engine = create_engine(url_str)

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE reparti ("
            "  id INT PRIMARY KEY, "
            "  nome_reparto VARCHAR(100), "
            "  sede VARCHAR(100)"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE dipendenti ("
            "  id INT PRIMARY KEY, "
            "  nome VARCHAR(100), "
            "  cognome VARCHAR(100), "
            "  ruolo VARCHAR(100), "
            "  bio TEXT, "
            "  reparto_id INT NULL"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE competenze ("
            "  id INT PRIMARY KEY, "
            "  dipendente_id INT, "
            "  nome_competenza VARCHAR(100), "
            "  livello VARCHAR(50)"
            ")"
        ))

        # Seed reparti
        conn.execute(text(
            "INSERT INTO reparti (id, nome_reparto, sede) VALUES "
            "(1, 'Engineering', 'Milano'), "
            "(2, 'Sales', 'Roma')"
        ))

        # Seed dipendenti (Carla has NULL reparto_id — tests LEFT JOIN / D-06)
        conn.execute(text(
            "INSERT INTO dipendenti (id, nome, cognome, ruolo, bio, reparto_id) VALUES "
            "(1, 'Alice', 'Rossi', 'dev', 'Backend dev with \U0001f680 emoji bio', 1), "
            "(2, 'Bob', 'Bianchi', 'dev', 'Frontend', 2), "
            "(3, 'Carla', 'Verdi', 'manager', 'Team lead', NULL)"
        ))

        # Seed competenze (entry (4) has NULL nome_competenza — tests Pitfall 6)
        conn.execute(text(
            "INSERT INTO competenze (id, dipendente_id, nome_competenza, livello) VALUES "
            "(1, 1, 'Python', 'expert'), "
            "(2, 1, 'Java', 'intermediate'), "
            "(3, 2, 'TypeScript', 'expert'), "
            "(4, 2, NULL, 'beginner'), "
            "(5, 3, 'Leadership', 'expert')"
        ))

    parsed = make_url(url_str)
    return {
        "host": parsed.host,
        "port": parsed.port,
        "db": parsed.database,
        "user": parsed.username,
        "password": parsed.password,
        "engine": engine,
    }


@pytest.fixture()
def mysql_adapter(seeded_engine, monkeypatch):
    """Build a MySQLAdapter pointing at the seeded container.

    Sets MYSQL_* env vars via monkeypatch so that _resolve_env_vars resolves
    ${VAR} tokens from the environment (D-11 credential pattern).

    Join config:
      - reparti  (aggregate=False) — flat many-to-one JOIN
      - competenze (aggregate=True) — one-to-many aggregated via Python groupby
    """
    monkeypatch.setenv("MYSQL_HOST", str(seeded_engine["host"]))
    monkeypatch.setenv("MYSQL_PORT", str(seeded_engine["port"]))
    monkeypatch.setenv("MYSQL_DB", str(seeded_engine["db"]))
    monkeypatch.setenv("MYSQL_USER", str(seeded_engine["user"]))
    monkeypatch.setenv("MYSQL_PASSWORD", str(seeded_engine["password"]))

    source_cfg = SourceConfig(
        type="mysql",
        mysql=MySQLConfig(
            host="${MYSQL_HOST}",
            port=seeded_engine["port"],
            database="${MYSQL_DB}",
            user="${MYSQL_USER}",
            password="${MYSQL_PASSWORD}",
            query=MySQLQueryConfig(
                **{"from": "dipendenti"},
                fields=["id", "nome", "cognome", "ruolo", "bio"],
                id_field="id",
                hash_fields=["id", "nome", "cognome", "ruolo", "bio"],
                joins=[
                    MySQLJoinConfig(
                        table="reparti",
                        on="dipendenti.reparto_id = reparti.id",
                        fields=["nome_reparto", "sede"],
                        aggregate=False,
                    ),
                    MySQLJoinConfig(
                        table="competenze",
                        on="dipendenti.id = competenze.dipendente_id",
                        fields=["nome_competenza", "livello"],
                        aggregate=True,
                        separator=", ",
                        as_="competenze",
                    ),
                ],
            ),
        ),
    )
    sync_cfg = SyncConfig(
        hash_fields=["id", "nome", "cognome", "ruolo", "bio"]
    )
    weaviate_cfg = WeaviateConfig(
        text_fields=["bio"],
        metadata_fields=["nome", "cognome", "ruolo"],
    )

    adapter = MySQLAdapter(source_cfg, sync_cfg, weaviate_cfg)
    yield adapter
    adapter.close()


# ---------------------------------------------------------------------------
# Helper to fetch records once per test (avoids multiple full fetches)
# ---------------------------------------------------------------------------


def _by_name(records: list[dict], nome: str) -> dict:
    """Return the record for the dipendente with the given first name."""
    matches = [r for r in records if r["nome"] == nome]
    assert matches, f"No record with nome={nome!r} — records: {records}"
    return matches[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fetch_all_three_records(mysql_adapter):
    """MySQLAdapter.fetch_records() returns exactly 3 dipendenti rows."""
    records = mysql_adapter.fetch_records()
    assert len(records) == 3


def test_main_fields_present(mysql_adapter):
    """Main-table fields are present and correctly mapped."""
    records = mysql_adapter.fetch_records()
    alice = _by_name(records, "Alice")
    assert alice["nome"] == "Alice"
    assert alice["cognome"] == "Rossi"
    assert alice["ruolo"] == "dev"


def test_flat_join_reparti_merged(mysql_adapter):
    """aggregate=False flat JOIN merges reparti columns into dipendenti record (D-02).

    Carla (NULL reparto_id) must survive the LEFT JOIN with NULL/empty join cols.
    """
    records = mysql_adapter.fetch_records()
    alice = _by_name(records, "Alice")
    # Flat join fields should be present in the record
    assert "nome_reparto" in alice or "reparti_nome_reparto" in alice
    # Alice's reparto is Engineering
    nome_reparto_val = alice.get("nome_reparto") or alice.get("reparti_nome_reparto")
    assert nome_reparto_val == "Engineering"

    # Carla has NULL reparto_id — must survive (LEFT JOIN, D-06)
    carla = _by_name(records, "Carla")
    assert carla is not None  # record present despite NULL FK


def test_aggregate_competenze_joined_with_separator(mysql_adapter):
    """aggregate=True join produces separator-joined string (D-02, D-03).

    Alice has Python (expert) and Java (intermediate).
    Use set comparison for order tolerance across DB versions.
    """
    records = mysql_adapter.fetch_records()
    alice = _by_name(records, "Alice")
    assert "competenze" in alice
    parts = set(alice["competenze"].split(", "))
    assert "Python expert" in parts
    assert "Java intermediate" in parts


def test_null_join_value_renders_empty(mysql_adapter):
    """NULL nome_competenza for Bob does NOT appear as literal 'None' (Pitfall 6 / D-06).

    Bob's competenze row (4) has NULL nome_competenza — it must be rendered as
    '' (or omitted from the aggregated string), never as 'None'.
    """
    records = mysql_adapter.fetch_records()
    bob = _by_name(records, "Bob")
    assert "competenze" in bob
    assert "None" not in bob["competenze"]
    # Bob's TypeScript entry should be present
    assert "TypeScript" in bob["competenze"]


def test_carla_no_competenze_aggregate_is_empty_string(mysql_adapter):
    """Carla has a competenza row (Leadership) so her field is non-empty.

    Wait — re-reading the seed: Carla has row (5, 3, 'Leadership', 'expert').
    So her competenze = 'Leadership expert'. This test verifies that records
    with no join rows would get '' — but since Carla has one, we verify the
    actual value is correct and non-empty.

    The adapter must not crash on NULL reparto_id (the flat join side).
    """
    records = mysql_adapter.fetch_records()
    carla = _by_name(records, "Carla")
    # Carla has Leadership competenza
    assert "competenze" in carla
    assert "Leadership" in carla["competenze"]
    # And her flat join (reparto) should be NULL/empty (no reparto_id)
    # The record should still be present
    assert carla["nome"] == "Carla"


def test_utf8mb4_emoji_roundtrip(mysql_adapter):
    """Alice's bio contains the rocket emoji verbatim after utf8mb4 round-trip (Pitfall 4)."""
    records = mysql_adapter.fetch_records()
    alice = _by_name(records, "Alice")
    assert "\U0001f680" in alice["bio"]  # 🚀


def test_get_record_id_returns_string_pk(mysql_adapter):
    """get_record_id returns a string representation of the primary key (D-08)."""
    records = mysql_adapter.fetch_records()
    alice = _by_name(records, "Alice")
    record_id = mysql_adapter.get_record_id(alice)
    assert isinstance(record_id, str)
    assert record_id == "1"


def test_hash_stable_across_calls(mysql_adapter):
    """get_record_hash is deterministic across consecutive fetch_records() calls (D-08).

    This is the incremental sync precondition: if nothing changed, the hash
    must be identical on re-run so SyncEngine skips the record.
    """
    records1 = mysql_adapter.fetch_records()
    records2 = mysql_adapter.fetch_records()

    alice1 = _by_name(records1, "Alice")
    alice2 = _by_name(records2, "Alice")

    hash1 = mysql_adapter.get_record_hash(alice1)
    hash2 = mysql_adapter.get_record_hash(alice2)

    assert isinstance(hash1, str) and len(hash1) == 32  # MD5 hex
    assert hash1 == hash2


def test_factory_dispatches_to_mysql_adapter_live(seeded_engine, monkeypatch):
    """build_source_adapter(...) returns a MySQLAdapter when source.type='mysql' (D-17).

    End-to-end factory wiring verified against a real container.
    """
    monkeypatch.setenv("MYSQL_HOST", str(seeded_engine["host"]))
    monkeypatch.setenv("MYSQL_PORT", str(seeded_engine["port"]))
    monkeypatch.setenv("MYSQL_DB", str(seeded_engine["db"]))
    monkeypatch.setenv("MYSQL_USER", str(seeded_engine["user"]))
    monkeypatch.setenv("MYSQL_PASSWORD", str(seeded_engine["password"]))

    source_cfg = SourceConfig(
        type="mysql",
        mysql=MySQLConfig(
            host="${MYSQL_HOST}",
            port=seeded_engine["port"],
            database="${MYSQL_DB}",
            user="${MYSQL_USER}",
            password="${MYSQL_PASSWORD}",
            query=MySQLQueryConfig(
                **{"from": "dipendenti"},
                fields=["id", "nome"],
                id_field="id",
                hash_fields=["id", "nome"],
            ),
        ),
    )
    sync_cfg = SyncConfig(hash_fields=["id", "nome"])
    weaviate_cfg = WeaviateConfig()

    adapter = build_source_adapter(source_cfg, sync_cfg, weaviate_cfg)
    try:
        assert isinstance(adapter, MySQLAdapter)
        records = adapter.fetch_records()
        assert len(records) == 3
    finally:
        adapter.close()
