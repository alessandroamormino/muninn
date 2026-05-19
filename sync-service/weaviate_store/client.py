"""Weaviate v4 client — module-level singleton.

Open at FastAPI startup via lifespan; close at shutdown. Other modules call
get_client() to retrieve the live connection. Do NOT instantiate clients elsewhere.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import weaviate

from config.settings import settings

logger = logging.getLogger(__name__)

_client = None  # module-level singleton; type: weaviate.WeaviateClient | None


def _parse_host_port(weaviate_url: str) -> tuple[str, int]:
    """Parse 'http://host:port' into ('host', port)."""
    parsed = urlparse(weaviate_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8080
    return host, port


def open_client() -> None:
    """Open the Weaviate v4 client connection. Idempotent — no-op if already open."""
    global _client
    if _client is not None and _client.is_connected():
        logger.info("Weaviate client already open; skipping re-open.")
        return
    host, port = _parse_host_port(settings.weaviate_url)
    logger.info("Opening Weaviate client at %s:%d", host, port)
    _client = weaviate.connect_to_local(host=host, port=port)


def close_client() -> None:
    """Close the Weaviate client. Safe to call when client is not open."""
    global _client
    if _client is None:
        return
    try:
        _client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error closing Weaviate client: %s", exc)
    _client = None


def get_client():
    """Return the live Weaviate v4 client. Raises RuntimeError if not opened."""
    if _client is None:
        raise RuntimeError(
            "Weaviate client is not open. Call open_client() first "
            "(handled automatically by FastAPI lifespan)."
        )
    return _client
