"""Test per build_source_adapter factory — TDD RED phase."""
import pytest

from config.settings import SourceConfig, SyncConfig, WeaviateConfig


def _make_cfgs(source_type: str, file_path: str | None = None, **kwargs):
    src = SourceConfig(type=source_type, file_path=file_path, **kwargs)
    syn = SyncConfig()
    wea = WeaviateConfig()
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

    def test_mysql_raises_not_implemented(self):
        from sources import build_source_adapter
        # SourceConfig.type Literal doesn't include 'mysql' but we test the function dispatch
        # We create a fake cfg-like object to avoid Pydantic validation error
        class FakeSrc:
            type = "mysql"
        with pytest.raises(NotImplementedError) as exc_info:
            build_source_adapter(FakeSrc(), SyncConfig(), WeaviateConfig())
        assert "mysql" in str(exc_info.value)

    def test_postgresql_raises_not_implemented(self):
        from sources import build_source_adapter
        class FakeSrc:
            type = "postgresql"
        with pytest.raises(NotImplementedError) as exc_info:
            build_source_adapter(FakeSrc(), SyncConfig(), WeaviateConfig())
        assert "postgresql" in str(exc_info.value)

    def test_unknown_type_raises_not_implemented(self):
        from sources import build_source_adapter
        class FakeSrc:
            type = "unknown_db"
        with pytest.raises(NotImplementedError) as exc_info:
            build_source_adapter(FakeSrc(), SyncConfig(), WeaviateConfig())
        assert "unknown_db" in str(exc_info.value)
