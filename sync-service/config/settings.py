"""
Config system for smart-search Sync Service.
Reads config.yaml from project root and exposes a typed AppConfig object.
Environment variables (from .env) are available via os.getenv().
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Resolve configuration/config.yaml relative to this file's location.
# Inside the container: __file__ = /app/config/settings.py
#   .parent.parent → /app  → /app/configuration/config.yaml  ✓
# On the host: sync-service/config/settings.py
#   .parent.parent → sync-service/  (not found)
#   .parent.parent.parent → project root → project root/configuration/config.yaml  ✓
_CONFIG_PATH_CONTAINER = Path(__file__).parent.parent / "configuration" / "config.yaml"
_CONFIG_PATH_HOST = Path(__file__).parent.parent.parent / "configuration" / "config.yaml"
_CONFIG_PATH = (
    _CONFIG_PATH_CONTAINER
    if _CONFIG_PATH_CONTAINER.exists()
    else _CONFIG_PATH_HOST
    if _CONFIG_PATH_HOST.exists()
    else Path("configuration") / "config.yaml"
)


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["none", "bearer", "api_key_header", "api_key_param", "basic"] = "none"
    token: str | None = None          # bearer
    header_name: str | None = None    # api_key_header — header name (e.g. "X-Api-Key")
    key: str | None = None            # api_key_header, api_key_param — the key value
    param_name: str | None = None     # api_key_param — query param name (e.g. "api_key")
    username: str | None = None       # basic
    password: str | None = None       # basic


class PaginationConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["none", "offset", "page", "cursor"] = "none"
    # cursor pagination
    next_key: str | None = None
    # page pagination
    page_param: str = "page"
    total_pages_key: str = "total_pages"
    start_page: int = 1
    # offset pagination
    offset_param: str = "offset"
    limit_param: str = "limit"
    page_size: int = 100
    # safety cap (anti-pattern: infinite loop)
    max_pages: int = 10000


class SourceConfig(BaseModel):
    type: Literal["csv", "json", "mysql", "postgresql", "mongodb", "rest_api"] = "csv"
    file_path: str | None = None
    connection_string: str | None = None
    table: str | None = None
    url: str | None = None
    auth_header: str | None = None
    id_field: str = "id"
    json_key: str | None = None
    delimiter: str = ","
    # Phase 8 additions — optional, ignored by all existing adapters
    auth: AuthConfig = Field(default_factory=AuthConfig)
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    method: Literal["GET", "POST"] = "GET"


class EmbeddingConfig(BaseModel):
    type: Literal[
        "weaviate_builtin", "ollama", "sentence_transformer", "openai", "cohere", "voyage"
    ] = "weaviate_builtin"
    model: str = "text2vec-transformers"
    api_key: str | None = Field(default=None)
    endpoint: str | None = Field(default=None)  # used by ollama adapter


class WeaviateConfig(BaseModel):
    collection: str = "Products"
    text_fields: list[str] = Field(default_factory=list)
    metadata_fields: list[str] = Field(default_factory=list)
    # Quantization: "none" | "pq" | "bq"
    # pq  = Product Quantization  — ~32× RAM reduction, ~2-5% quality loss  (consigliato per >100K record)
    # bq  = Binary Quantization   — ~128× RAM reduction, ~10-15% quality loss (solo se RAM è critica)
    # none = nessuna compressione  (default — compatibile con collection esistenti)
    quantization: Literal["none", "pq", "bq"] = "none"


class SyncConfig(BaseModel):
    mode: Literal["incremental", "full"] = "incremental"
    hash_fields: list[str] = Field(default_factory=list)
    schedule: str = "manual"


class ApiConfig(BaseModel):
    output_fields: list[str] = Field(default_factory=list)
    default_limit: int = 10
    max_limit: int = 100
    cache_ttl_seconds: int = 300  # TTL default: 5 minuti (D-10)
    cache_mode: Literal["exact", "normalized", "semantic"] = "exact"  # D-06: default exact
    semantic_cache_threshold: float = 0.90  # solo per semantic mode (D-20)


class GraphConfig(BaseModel):
    filter_fields: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    source: SourceConfig = Field(default_factory=SourceConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    weaviate: WeaviateConfig = Field(default_factory=WeaviateConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)

    # Weaviate URL comes from environment, not config.yaml
    weaviate_url: str = Field(
        default_factory=lambda: os.getenv("WEAVIATE_URL", "http://localhost:8080")
    )


def load_config(path: Path = _CONFIG_PATH) -> AppConfig:
    """Load and validate config.yaml. Raises ValidationError on schema mismatch."""
    with open(path, "r") as fh:
        raw = yaml.safe_load(fh)
    return AppConfig.model_validate(raw)


# Module-level singleton — imported by all other modules
settings: AppConfig = load_config()
