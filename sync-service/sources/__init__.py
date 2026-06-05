"""Source adapters package."""
from __future__ import annotations

from sources.csv_adapter import CSVAdapter
from sources.json_adapter import JSONAdapter
from sources.mysql_adapter import MySQLAdapter
from sources.rest_api_adapter import RestAPIAdapter


def build_source_adapter(
    source_cfg: "SourceConfig",
    sync_cfg: "SyncConfig",
    weaviate_cfg: "VectorStoreConfig",
) -> "BaseSourceAdapter":
    """Factory che restituisce il SourceAdapter corretto in base a source_cfg.type.

    Supporta csv, json, rest_api e mysql. Lancia NotImplementedError per tipi non
    ancora implementati (postgresql, mongodb).
    """
    source_type = source_cfg.type
    if source_type == "csv":
        return CSVAdapter(source_cfg, sync_cfg, weaviate_cfg)
    if source_type == "json":
        return JSONAdapter(source_cfg, sync_cfg, weaviate_cfg)
    if source_type == "rest_api":
        return RestAPIAdapter(source_cfg, sync_cfg, weaviate_cfg)
    if source_type == "mysql":
        return MySQLAdapter(source_cfg, sync_cfg, weaviate_cfg)
    raise NotImplementedError(
        f"source.type={source_type!r} non ancora supportato. "
        "Tipi disponibili: 'csv', 'json', 'rest_api', 'mysql'. "
        "Per PostgreSQL/MongoDB vedere le implementazioni previste in CLAUDE.md."
    )


__all__ = ["CSVAdapter", "JSONAdapter", "MySQLAdapter", "RestAPIAdapter", "build_source_adapter"]
