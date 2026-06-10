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
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class HnswConfig(BaseModel):
    """HNSW vector index tuning (Phase 13.2). All fields optional -- None = Weaviate server default (64).

    NOTE on mutability after collection creation:
      - ef is MUTABLE post-creation via:
          collection.config.update(vector_index_config=Reconfigure.VectorIndex.hnsw(ef=N))
      - max_connections is IMMUTABLE -- changing it requires a full re-index.
    """
    model_config = ConfigDict(extra="ignore")
    ef: int | None = None
    max_connections: int | None = None


class MySQLJoinConfig(BaseModel):
    """Declarative JOIN config for a single table join in MySQLAdapter (Phase 14, D-16)."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    table: str
    on: str
    fields: list[str] = Field(default_factory=list)
    aggregate: bool = False
    separator: str = ", "
    as_: Optional[str] = Field(default=None, alias="as")


class MySQLQueryConfig(BaseModel):
    """Query definition for MySQLAdapter (table, fields, id_field, hash_fields, joins)."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    from_table: str = Field(alias="from")
    fields: list[str] = Field(default_factory=list)
    id_field: str = "id"
    hash_fields: list[str] = Field(default_factory=list)
    joins: list[MySQLJoinConfig] = Field(default_factory=list)
    fetch_chunk_size: int = 10000


class MySQLConfig(BaseModel):
    """Connection + query config for MySQLAdapter (Phase 14, D-16)."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    host: str = "localhost"
    port: int = 3306
    database: str
    user: str
    password: str
    ssl_ca: Optional[str] = None
    ssl_cert: Optional[str] = None
    ssl_key: Optional[str] = None
    query: MySQLQueryConfig


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
    # Phase 14 addition — optional; None for all non-MySQL adapters
    mysql: MySQLConfig | None = None


class EmbeddingConfig(BaseModel):
    type: Literal[
        "weaviate_builtin", "ollama", "sentence_transformer", "openai", "cohere", "voyage"
    ] = "weaviate_builtin"
    model: str = "text2vec-transformers"
    api_key: str | None = Field(default=None)
    endpoint: str | None = Field(default=None)  # used by ollama adapter


class FtsConfig(BaseModel):
    """Full-text search configuration per entity (Phase 15 — Qdrant fts mode).

    language: stemmer language for Qdrant TextIndexParams (Snowball).
    Accepted values: en, it, de, fr, es, pt, nl, ru, sv, fi, da, no, hu,
                     ro, tr, ar, armenian, basque, catalan, ga, gl, hi, hy (and more).
    Default: "en" — Qdrant default.
    """
    model_config = ConfigDict(extra="ignore")
    language: str = "en"
    match_mode: Literal["and", "or"] = "and"  # Phase 23: AND/OR filter mode
    use_omw: bool = False  # Phase 23: download OMW at sync time


class VectorStoreConfig(BaseModel):
    collection: str = "Products"
    # Phase 23: text_fields accepts list[str] OR dict[str, float].
    # list[str] → normalized to {field: 1.0 for field in list} by validator below.
    # dict[str, float] → stored as-is (per-field BM25 boost weights).
    text_fields: dict[str, float] = Field(default_factory=dict)

    @field_validator("text_fields", mode="before")
    @classmethod
    def normalize_text_fields(cls, v: list | dict) -> dict:
        """Normalize list[str] to dict[str, float] with equal weights 1.0.

        Backward compat: existing list-format configs parse transparently.
        (Pitfall 6: after this change, callers must NOT use text_fields[0];
         use next(iter(text_fields)) or list(text_fields.keys())[0] instead.)
        """
        if isinstance(v, list):
            return {f: 1.0 for f in v}
        return v
    metadata_fields: list[str] = Field(default_factory=list)
    # Quantization: "none" | "pq" | "bq" | "sq"  (Phase 13.2 added "sq")
    # pq  = Product Quantization  -- ~32x RAM reduction, ~2-5% quality loss   (>100K records)
    # bq  = Binary Quantization   -- ~128x RAM reduction, ~10-15% quality loss (RAM critical)
    # sq  = Scalar Quantization   -- ~4x RAM reduction, ~1-2% quality loss    (10K-100K records)
    # none = no compression (default -- compatible with existing collections)
    quantization: Literal["none", "pq", "bq", "sq"] = "none"
    # HNSW tuning -- optional. All None = use Weaviate server defaults (ef=64, max_connections=64).
    # ef is mutable post-creation; max_connections requires full re-index to change.
    hnsw: HnswConfig = Field(default_factory=HnswConfig)
    # Search mode (Phase 15 — per entity). Weaviate supports: hybrid, vector, bm25.
    # Qdrant additionally supports: fts. Default: "hybrid" (richest behavior, backward compat).
    # Incompatible combinations rejected at startup with RuntimeError (D-04).
    search_mode: Literal["hybrid", "vector", "bm25", "fts"] = "hybrid"
    # FTS language config (Phase 15 — Qdrant fts mode only; ignored for Weaviate).
    fts: FtsConfig = Field(default_factory=FtsConfig)


class SyncConfig(BaseModel):
    mode: Literal["incremental", "full"] = "incremental"
    hash_fields: list[str] = Field(default_factory=list)
    schedule: str = "manual"


class ApiConfig(BaseModel):
    output_fields: list[str] = Field(default_factory=list)
    default_limit: int = 10
    max_limit: int | None = None
    cache_ttl_seconds: int = 300  # TTL default: 5 minuti (D-10)
    cache_mode: Literal["exact", "normalized", "semantic"] = "exact"  # D-06: default exact
    semantic_cache_threshold: float = 0.90  # solo per semantic mode (D-20)


class GraphConfig(BaseModel):
    filter_fields: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    source: SourceConfig = Field(default_factory=SourceConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)

    # Vector store URL — VECTOR_STORE_URL takes precedence (Phase 15 D-02).
    # Falls back to legacy WEAVIATE_URL for backward compatibility.
    weaviate_url: str = Field(
        default_factory=lambda: os.getenv(
            "VECTOR_STORE_URL", os.getenv("WEAVIATE_URL", "http://localhost:8080")
        )
    )


def load_config(path: Path = _CONFIG_PATH) -> AppConfig:
    """Load and validate config.yaml. Raises ValidationError on schema mismatch."""
    with open(path, "r") as fh:
        raw = yaml.safe_load(fh)
    return AppConfig.model_validate(raw)


# Module-level singleton — imported by all other modules
settings: AppConfig = load_config()
