"""
Sync Service — FastAPI entrypoint.

Starts the web server, opens the Weaviate client at startup,
closes it at shutdown, and registers all API routers.
Configuration is loaded from config.yaml via config.settings at import time.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

import re
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from auth.dependencies import get_current_user
from auth.user_store import UserRecord

from api.search import router as search_router
from api.sync import router as sync_router
from api.setup import router as setup_router
from api.logs import router as logs_router
from api.upload import router as upload_router
from api.graph import router as graph_router
from api.auth import router as auth_router, _limiter as auth_limiter
from api.admin import router as admin_router
from api.history import router as history_router
from api.config import router as config_router
from auth.user_store import UserStore, RefreshTokenStore
from scheduler import build_scheduler
from sync.log_store import LogStore
from sync.history_store import HistoryStore
from sync.cache_adapters import build_cache_adapter
from config.settings import _CONFIG_PATH, load_config, settings
from embeddings import build_embedding_adapter
from sync.engine import SyncEngine
from sync.state_store import StateStore
from weaviate_store import (
    open_client,
    close_client,
    create_collection_if_missing,
    check_and_handle_model_change,
    get_client,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    jwt_secret = os.getenv("JWT_SECRET", "")
    if len(jwt_secret) < 32:
        raise RuntimeError(
            "JWT_SECRET env var is missing or too short (min 32 chars). "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    logger.info("sync-service starting; opening Weaviate client...")
    open_client()
    logger.info("Creating Weaviate collection if missing...")
    created = create_collection_if_missing(
        get_client(), settings.weaviate, embedding_type=settings.embedding.type
    )
    if created:
        logger.info("Weaviate collection %r created.", settings.weaviate.collection)
    else:
        logger.info("Weaviate collection %r already present.", settings.weaviate.collection)
    logger.info("Inizializzazione SyncEngine e StateStore...")
    state_store = StateStore()
    logger.info("Checking embedding-model version against persisted state...")
    check_and_handle_model_change(get_client(), settings, state_store=state_store)
    app.state.embedding_adapter = build_embedding_adapter(settings.embedding)
    history_store = HistoryStore(Path("/app/.sync/search_history.db"))
    app.state.history_store = history_store
    logger.info("HistoryStore ready at /app/.sync/search_history.db")
    cache_store = build_cache_adapter(settings)
    app.state.cache_store = cache_store
    logger.info("CacheAdapter ready (mode=%r, ttl=%ds)", settings.api.cache_mode, settings.api.cache_ttl_seconds)
    app.state.sync_engine = SyncEngine(settings, get_client(), state_store, cache_store=cache_store)
    app.state.sync_lock = threading.Lock()
    app.state.sync_status = {"status": "idle", "last_run": None}
    app.state.sync_progress = None  # populated during active sync, cleared on completion
    scheduler = build_scheduler(app.state, settings)
    app.state.scheduler = scheduler
    if scheduler is not None:
        logger.info(
            "APScheduler running — cron: %r. "
            "Schedule changes require container restart (D-07).",
            settings.sync.schedule,
        )
    else:
        logger.info("APScheduler disabled (schedule='manual'). No automatic sync.")
    log_store = LogStore(Path("/app/.sync/sync_logs.db"))
    app.state.log_store = log_store
    logger.info("LogStore ready at /app/.sync/sync_logs.db")
    app.state.upload_status = None  # updated by POST /upload and POST /upload/confirm — D-08
    # Auth stores (D-03, D-05)
    user_store = UserStore(Path("/app/.sync/users.db"))
    token_store = RefreshTokenStore(user_store._conn)
    app.state.user_store = user_store
    app.state.token_store = token_store
    app.state.tmp_tokens = {}  # dict: {tmp_token_str: {username, expires_at}}
    # Seed first admin from env if users.db is empty (D-05)
    if user_store.is_empty():
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "changeme")
        user_store.create_user(admin_username, admin_password, "admin")
        logger.info("First admin user %r seeded from env (D-05).", admin_username)
    else:
        logger.info("UserStore already has users — skipping seed.")
    logger.info("SyncEngine pronto.")
    yield
    # Shutdown
    logger.info("sync-service shutting down; closing Weaviate client...")
    if app.state.scheduler is not None:
        app.state.scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")
    app.state.log_store.close()
    logger.info("LogStore closed.")
    app.state.history_store.close()
    logger.info("HistoryStore closed.")
    app.state.cache_store.close()
    logger.info("CacheStore closed.")
    app.state.user_store.close()
    logger.info("UserStore closed.")
    close_client()


app = FastAPI(
    title="smart-search Sync Service",
    description=(
        "Ingests data from configurable sources, generates vector embeddings, "
        "and exposes a semantic search API backed by Weaviate."
    ),
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = auth_limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Register routers (stubs in Phase 1; real implementations added in later phases)
app.include_router(search_router)
app.include_router(sync_router)
app.include_router(setup_router)
app.include_router(logs_router)
app.include_router(upload_router)
app.include_router(graph_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(history_router)
app.include_router(config_router)


@app.get("/health")
async def health(response: Response) -> dict:
    """Health check. Probes Weaviate is_live(); returns HTTP 503 if unreachable.

    Per CONTEXT.md D-07: makes the docker-compose healthcheck meaningful by
    reporting actual Weaviate state, not just that the FastAPI process is alive.
    """
    try:
        alive = get_client().is_live()
    except Exception:  # noqa: BLE001
        alive = False
    if alive:
        return {"status": "ok"}
    response.status_code = 503
    return {"status": "weaviate_unreachable"}


_INFO_COLLECTION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_INFO_CONFIG_ROOT = _CONFIG_PATH.parent


@app.get("/info")
async def info(
    collection: Optional[str] = Query(None, description="Nome collection (es. 'CollaboratoriDB'). Se omesso usa config globale."),
    _: UserRecord = Depends(get_current_user),
) -> dict:
    """Service info. Adds total_objects from a live aggregate query.

    Per CONTEXT.md D-06: total_objects is null on any Weaviate failure rather
    than raising and surfacing a 5xx. All other keys preserved verbatim.
    """
    if collection is not None:
        if not _INFO_COLLECTION_RE.match(collection):
            raise HTTPException(status_code=422, detail="Invalid collection name")
        config_path = _INFO_CONFIG_ROOT / collection / "config.yaml"
        if not config_path.exists():
            raise HTTPException(status_code=404, detail=f"No config found for collection '{collection}'.")
        cfg = load_config(config_path)
    else:
        cfg = settings

    total_objects: int | None = None
    try:
        agg = (
            get_client()
            .collections.get(cfg.weaviate.collection)
            .aggregate.over_all(total_count=True)
        )
        total_objects = agg.total_count
    except Exception:  # noqa: BLE001
        pass
    return {
        "embedding_model": cfg.embedding.model,
        "embedding_type": cfg.embedding.type,
        "collection": cfg.weaviate.collection,
        "weaviate_url": settings.weaviate_url,
        "sync_mode": cfg.sync.mode,
        "sync_schedule": cfg.sync.schedule,
        "total_objects": total_objects,
    }
