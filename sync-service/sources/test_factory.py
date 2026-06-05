"""Test per build_source_adapter factory — TDD RED phase."""
from unittest.mock import MagicMock, patch

import pytest

from config.settings import MySQLConfig, MySQLQueryConfig, SourceConfig, SyncConfig, VectorStoreConfig


def _make_cfgs(source_type: str, file_path: str | None = None, **kwargs):
    src = SourceConfig(type=source_type, file_path=file_path, **kwargs)
    syn = SyncConfig()
    wea = VectorStoreConfig()
    return src, syn, wea


class TestBuildSourceAdapterImport:
    def test_build_source_adapter_importable(self):
        from sources import build_source_adapter  # noqa: F401
        assert callable(build_source_adapter)

    def test_csv_adapter_still_exported(self):
        from sources import CSVAdapter  # noqa: F401
        assert CSVAdapter is not None

    def test_json_adapter_still_exported(self):
        from sources import JSONAdapter  # noqa: F401
        assert JSONAdapter is not None


class TestBuildSourceAdapterDispatch:
    def test_csv_returns_csv_adapter(self, tmp_path):
        from sources import build_source_adapter, CSVAdapter
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("id,name\n1,Alice\n")
        src, syn, wea = _make_cfgs("csv", file_path=str(csv_file))
        adapter = build_source_adapter(src, syn, wea)
        assert isinstance(adapter, CSVAdapter)

    def test_json_returns_json_adapter(self, tmp_path):
        from sources import build_source_adapter, JSONAdapter
        json_file = tmp_path / "test.json"
        json_file.write_text('[{"id": 1, "name": "Alice"}]')
        src, syn, wea = _make_cfgs("json", file_path=str(json_file))
        adapter = build_source_adapter(src, syn, wea)
        assert isinstance(adapter, JSONAdapter)

    def test_mysql_raises_not_implemented_without_mysql_block(self):
        """build_source_adapter with type=mysql but no mysql: block raises ValueError."""
        from sources import build_source_adapter
        src = SourceConfig(type="mysql")  # no mysql: block
        with pytest.raises(ValueError) as exc_info:
            build_source_adapter(src, SyncConfig(), VectorStoreConfig())
        assert "mysql" in str(exc_info.value).lower()

    def test_postgresql_raises_not_implemented(self):
        from sources import build_source_adapter
        class FakeSrc:
            type = "postgresql"
        with pytest.raises(NotImplementedError) as exc_info:
            build_source_adapter(FakeSrc(), SyncConfig(), VectorStoreConfig())
        assert "postgresql" in str(exc_info.value)

    def test_unknown_type_raises_not_implemented(self):
        from sources import build_source_adapter
        class FakeSrc:
            type = "unknown_db"
        with pytest.raises(NotImplementedError) as exc_info:
            build_source_adapter(FakeSrc(), SyncConfig(), VectorStoreConfig())
        assert "unknown_db" in str(exc_info.value)

    def test_mysql_returns_mysql_adapter(self):
        """build_source_adapter with type=mysql and a valid mysql: block returns MySQLAdapter."""
        from sources import build_source_adapter, MySQLAdapter

        q = MySQLQueryConfig.model_validate({
            "from": "dipendenti",
            "fields": ["id", "nome"],
            "id_field": "id",
            "hash_fields": ["id", "nome"],
        })
        mysql_cfg = MySQLConfig(
            host="localhost",
            database="mydb",
            user="user",
            password="pass",
            query=q,
        )
        src = SourceConfig(type="mysql", mysql=mysql_cfg)
        syn = SyncConfig()
        wea = VectorStoreConfig()

        mock_engine = MagicMock()
        with patch("sources.mysql_adapter.create_engine", return_value=mock_engine):
            adapter = build_source_adapter(src, syn, wea)

        assert isinstance(adapter, MySQLAdapter)
