"""Weaviate package: client singleton, schema creation, upsert helpers, and model-version detection."""
from .client import get_client, open_client, close_client
from .schema import create_collection_if_missing
from .upsert import upsert_records, compute_record_uuid, UpsertResult
from .model_version import (
    check_and_handle_model_change,
    read_stored_model,
    write_stored_model,
    MODEL_VERSION_PATH,
)

__all__ = [
    "get_client",
    "open_client",
    "close_client",
    "create_collection_if_missing",
    "upsert_records",
    "compute_record_uuid",
    "UpsertResult",
    "check_and_handle_model_change",
    "read_stored_model",
    "write_stored_model",
    "MODEL_VERSION_PATH",
]
