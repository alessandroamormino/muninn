"""Tests for MySQLConfig, MySQLQueryConfig, MySQLJoinConfig Pydantic models (Phase 14, D-16)."""
from __future__ import annotations

import yaml
import pytest

from config.settings import AppConfig, SourceConfig


def _app_config(yaml_text: str) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(yaml_text))


class TestMySQLConfigMinimal:
    def test_mysql_config_parses_minimal_yaml(self):
        raw = """
source:
  type: mysql
  mysql:
    host: localhost
    database: mydb
    user: myuser
    password: mypassword
    query:
      from: dipendenti
      fields: [id, nome, cognome]
      id_field: id
"""
        cfg = _app_config(raw)
        assert cfg.source.mysql is not None
        assert cfg.source.mysql.host == "localhost"
        assert cfg.source.mysql.port == 3306
        assert cfg.source.mysql.database == "mydb"
        assert cfg.source.mysql.query.from_table == "dipendenti"
        assert cfg.source.mysql.query.fields == ["id", "nome", "cognome"]

    def test_mysql_join_alias_from_and_as(self):
        """YAML with literal `from:` and `as:` must populate from_table / as_ via Pydantic alias.

        NOTE: PyYAML (YAML 1.1) treats bare `on:` and `as:` keys as booleans.
        In real config.yaml files these keys must be quoted. Tests use quoted form.
        """
        raw = """
source:
  type: mysql
  mysql:
    host: localhost
    database: mydb
    user: myuser
    password: mypassword
    query:
      from: competenze
      fields: [id, nome_competenza]
      id_field: id
      joins:
        - table: reparti
          "on": "competenze.reparto_id = reparti.id"
          fields: [nome_reparto]
          "as": nome_reparto_alias
"""
        cfg = _app_config(raw)
        assert cfg.source.mysql.query.from_table == "competenze"
        join = cfg.source.mysql.query.joins[0]
        assert join.as_ == "nome_reparto_alias"
        assert join.table == "reparti"

    def test_mysql_join_defaults(self):
        """Join with only table/on/fields must default aggregate=False, separator=', ', as_=None.

        NOTE: `on:` key must be quoted in YAML to avoid YAML 1.1 boolean interpretation.
        """
        raw = """
source:
  type: mysql
  mysql:
    host: db.internal
    database: hr
    user: admin
    password: secret
    query:
      from: dipendenti
      fields: [id, nome]
      id_field: id
      joins:
        - table: reparti
          "on": "dipendenti.reparto_id = reparti.id"
          fields: [nome_reparto]
"""
        cfg = _app_config(raw)
        join = cfg.source.mysql.query.joins[0]
        assert join.aggregate is False
        assert join.separator == ", "
        assert join.as_ is None
        assert join.fields == ["nome_reparto"]

    def test_mysql_config_extra_keys_ignored(self):
        """Extra unknown keys inside mysql: and each join entry are silently ignored.

        NOTE: `on:` key must be quoted in YAML to avoid YAML 1.1 boolean interpretation.
        """
        raw = """
source:
  type: mysql
  mysql:
    host: localhost
    database: mydb
    user: myuser
    password: mypassword
    unknown_top_key: should_be_ignored
    query:
      from: dipendenti
      fields: [id]
      id_field: id
      joins:
        - table: reparti
          "on": "dipendenti.reparto_id = reparti.id"
          fields: [nome_reparto]
          future_unknown_key: ignored_too
"""
        cfg = _app_config(raw)
        assert cfg.source.mysql is not None
        assert cfg.source.mysql.query.from_table == "dipendenti"

    def test_source_config_without_mysql_unchanged(self):
        """A CSV config must still parse correctly; cfg.source.mysql must be None."""
        raw = """
source:
  type: csv
  file_path: /data/myfile.csv
  id_field: id
  delimiter: ","
"""
        cfg = _app_config(raw)
        assert cfg.source.type == "csv"
        assert cfg.source.mysql is None
