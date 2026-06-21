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
from api.entities import router as entities_router
from api.auth import router as auth_router, _limiter as auth_limiter
from api.admin import router as admin_router
from api.history import router as history_router
from api.config import router as config_router
from api.metrics import router as metrics_router
from auth.user_store import UserStore, RefreshTokenStore
from scheduler import build_scheduler
from sync.log_store import LogStore
from sync.history_store import HistoryStore
from sync.cache_adapters import build_cache_adapter
from sync.entity_state_store import EntityStateStore
from config.settings import _CONFIG_PATH, load_config, settings
from embeddings import build_embedding_adapter
from sync.engine import SyncEngine
from sync.state_store import StateStore
from vector_stores import get_vector_store
from vector_stores.base import validate_search_mode_compatibility
from vector_stores.search_mode_state import detect_search_mode_change
from weaviate_store.model_version import check_and_handle_model_change

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

    # --- Vector store engine selection (Phase 15 D-01) ---------------------------
    engine = os.getenv("VECTOR_STORE_ENGINE", "weaviate")
    logger.info("sync-service starting; vector_store_engine=%r url=%r", engine, settings.weaviate_url)
    vector_store = get_vector_store(engine, settings.weaviate_url)

    # --- D-04: fail-fast on incompatible search_mode at startup ------------------
    # Collect all entity configs from configuration/ subdirectories + global config.
    _config_root = _CONFIG_PATH.parent
    _entity_configs: list = [settings]  # always include global config
    if _config_root.exists():
        for _d in _config_root.iterdir():
            if _d.is_dir() and (_d / "config.yaml").exists():
                try:
                    _entity_configs.append(load_config(_d / "config.yaml"))
                except Exception:  # noqa: BLE001
                    pass
    validate_search_mode_compatibility(engine, _entity_configs)  # raises RuntimeError on mismatch

    vector_store.open()
    logger.info("Creating collection if missing...")
    # D-09: detect search_mode change — drop index before re-creating when mode changed.
    # detect_search_mode_change returns False on first run (no file) and when mode unchanged.
    _collection_name = settings.vector_store.collection
    _current_mode = getattr(settings.vector_store, "search_mode", "hybrid")
    if detect_search_mode_change(_collection_name, _current_mode):
        logger.warning(
            "search_mode changed for %r — dropping existing index for full re-index (D-09).",
            _collection_name,
        )
        vector_store.drop_index(_collection_name)
    # Phase 24: detect quantization/on_disk config change (Qdrant engine only).
    # Lazy import avoids ImportError when qdrant-client is not installed (Weaviate deployments).
    # Loop over all entity configs to catch per-entity quantization changes, mirroring the
    # validate_search_mode_compatibility pattern above (WR-02 fix).
    if engine == "qdrant":
        from vector_stores.quantization_state import detect_quantization_change
        for _ecfg in _entity_configs:
            _ecoll = _ecfg.vector_store.collection
            if detect_quantization_change(_ecoll, _ecfg):
                logger.warning(
                    "quantization or on_disk config changed for %r — dropping existing index "
                    "for full re-index (Phase 24).",
                    _ecoll,
                )
                vector_store.drop_index(_ecoll)
    # Probe actual embedding dims so create_index uses the correct vector size.
    # Wrapped in try/except: if Ollama is not yet up at startup the collection
    # likely already exists (normal restart), so wrong dims are harmless here.
    # The critical injection is in engine.run_full() which runs when Ollama is active.
    try:
        _startup_emb = build_embedding_adapter(settings.embedding)
        object.__setattr__(settings.vector_store, "_embedding_dims", _startup_emb.dimensions())
    except Exception as _dims_exc:
        logger.warning(
            "Could not probe embedding dims at startup (collection may use wrong dims if new): %s",
            _dims_exc,
        )
    created = vector_store.create_index(settings)
    if created:
        logger.info("Collection %r created.", settings.vector_store.collection)
    else:
        logger.info("Collection %r already present.", settings.vector_store.collection)
    logger.info("Inizializzazione SyncEngine e StateStore...")
    state_store = StateStore()
    logger.info("Checking embedding-model version against persisted state...")
    # check_and_handle_model_change still uses weaviate_store directly for model_version.json
    # management — this is intentional: model_version tracking is engine-agnostic bookkeeping.
    # For model mismatch re-index, it creates its own SyncEngine internally using weaviate_store;
    # this is acceptable since it only runs on model change (rare startup event).
    check_and_handle_model_change(vector_store, settings, state_store=state_store)
    app.state.embedding_adapter = build_embedding_adapter(settings.embedding)
    history_store = HistoryStore(Path("/app/.sync/search_history.db"))
    app.state.history_store = history_store
    logger.info("HistoryStore ready at /app/.sync/search_history.db")
    cache_store = build_cache_adapter(settings)
    app.state.cache_store = cache_store
    logger.info("CacheAdapter ready (mode=%r, ttl=%ds)", settings.api.cache_mode, settings.api.cache_ttl_seconds)
    app.state.vector_store = vector_store
    app.state.sync_engine = SyncEngine(settings, vector_store, state_store, cache_store=cache_store)

    # Rebuild fuzzy vocab for all fts/bm25 Qdrant collections on startup (vocab is in-memory only).
    if engine == "qdrant" and hasattr(vector_store, "_build_fuzzy_vocab"):
        for _cfg in _entity_configs:
            _mode = getattr(_cfg.vector_store, "search_mode", "hybrid")
            if _mode in ("fts", "bm25"):
                _coll = _cfg.vector_store.collection
                logger.info("Rebuilding fuzzy vocab for %r at startup...", _coll)
                vector_store._build_fuzzy_vocab(_coll)
    app.state.sync_lock = threading.Lock()
    app.state.sync_status = {"status": "idle", "last_run": None}
    app.state.sync_progress = None  # populated during active sync, cleared on completion
    # Phase 26: entity load/unload management (D-08/D-09/D-12).
    app.state.entity_state_store = EntityStateStore()
    app.state.unload_progress = None  # populated during unload/load, SEPARATE from sync_progress (Pitfall 6)
    logger.info("EntityStateStore ready at /app/.sync/entity_state.json")
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
    logger.info("sync-service shutting down; closing vector store...")
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
    vector_store.close()


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
app.include_router(entities_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(history_router)
app.include_router(config_router)
app.include_router(metrics_router)


@app.get("/health")
async def health(request: Request, response: Response) -> dict:
    """Health check. Probes vector store is_live(); returns HTTP 503 if unreachable.

    Per CONTEXT.md D-07: makes the docker-compose healthcheck meaningful by
    reporting actual vector store state, not just that the FastAPI process is alive.
    """
    vector_store = getattr(request.app.state, "vector_store", None)
    alive = False
    if vector_store is not None:
        try:
            alive = vector_store.is_live()
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
    request: Request,
    collection: Optional[str] = Query(None, description="Nome collection (es. 'CollaboratoriDB'). Se omesso usa config globale."),
    _: UserRecord = Depends(get_current_user),
) -> dict:
    """Service info. Adds total_objects from a live count query.

    Per CONTEXT.md D-06: total_objects is null on any vector store failure rather
    than raising and surfacing a 5xx. All other keys preserved verbatim.
    Phase 15 adds: vector_store_engine and search_mode fields.
    """
    if collection is not None:
        if not _INFO_COLLECTION_RE.match(collection):
            raise HTTPException(status_code=422, detail="Invalid collection name")
        # Per-entity path first; fall back to global config.yaml if its collection matches
        config_path = _INFO_CONFIG_ROOT / collection / "config.yaml"
        if not config_path.exists():
            global_path = _INFO_CONFIG_ROOT / "config.yaml"
            if global_path.exists():
                try:
                    gcfg = load_config(global_path)
                    if gcfg.vector_store.collection == collection:
                        config_path = global_path
                    else:
                        raise HTTPException(status_code=404, detail=f"No config found for collection '{collection}'.")
                except HTTPException:
                    raise
                except Exception:  # noqa: BLE001
                    raise HTTPException(status_code=404, detail=f"No config found for collection '{collection}'.")
            else:
                raise HTTPException(status_code=404, detail=f"No config found for collection '{collection}'.")
        cfg = load_config(config_path)
    else:
        cfg = settings

    vector_store = getattr(request.app.state, "vector_store", None)
    total_objects: int | None = None
    if vector_store is not None:
        try:
            total_objects = vector_store.count(cfg.vector_store.collection)
        except Exception:  # noqa: BLE001
            pass

    vector_store_engine = os.getenv("VECTOR_STORE_ENGINE", "weaviate")
    search_mode = getattr(cfg.vector_store, "search_mode", "hybrid")

    return {
        "embedding_model": cfg.embedding.model,
        "embedding_type": cfg.embedding.type,
        "collection": cfg.vector_store.collection,
        "weaviate_url": settings.weaviate_url,           # backward compat
        "vector_store_url": settings.weaviate_url,       # Phase 15 D-02
        "vector_store_engine": vector_store_engine,      # Phase 15
        "search_mode": search_mode,                      # Phase 15
        "sync_mode": cfg.sync.mode,
        "sync_schedule": cfg.sync.schedule,
        "total_objects": total_objects,
        "max_limit": cfg.api.max_limit,
    }
