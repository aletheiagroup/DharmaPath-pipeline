"""
web/app.py

FastAPI app entrypoint, bootstrapping, and lifespan orchestration.
Initialises stores, services, CORS, rate limiting, and v1 routing.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config.settings import settings as env_settings
from dharmapath.registry.registry import CharacterRegistry
from web.exceptions import register_exception_handlers
from web.routes import (
    assets_router,
    chapters_router,
    compile_router,
    generation_router,
    panels_router,
    settings_router,
    system_router,
    workspace_router,
)
from web.services import (
    AssetService,
    ChapterService,
    CompileService,
    GenerationService,
    ReviewService,
    SettingsService,
    SystemService,
)
from web.store import AsyncTaskManager, MemoryJobStore, MemoryReviewStore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Base directories
_REPO_ROOT = Path(__file__).parent.parent
_SCREENPLAYS_DIR = _REPO_ROOT / env_settings.screenplays_dir
_OUTPUTS_DIR = _REPO_ROOT / env_settings.outputs_dir
_REGISTRY_PATH = _REPO_ROOT / "dharmapath" / "registry" / "characters.json"

# Ensure directories exist
_SCREENPLAYS_DIR.mkdir(parents=True, exist_ok=True)
_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


# ── Lifespan Manager ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Bootstrap shared infrastructure, load the character registry,
    and cleanly save state on exit.
    """
    logger.info("Initializing DharmaPath Content Studio Backend...")

    # 1. Instantiate Stores
    job_store = MemoryJobStore()
    review_store = MemoryReviewStore()
    task_manager = AsyncTaskManager()

    # 2. Instantiate Character Registry (Pipeline Core dependency)
    registry = CharacterRegistry(_REGISTRY_PATH)
    try:
        registry.load()
    except Exception as e:
        logger.error("Failed to load character registry: %s", e)

    # 3. Instantiate Services
    chapter_service = ChapterService(_SCREENPLAYS_DIR, _OUTPUTS_DIR, registry, review_store)
    generation_service = GenerationService(job_store, task_manager, _REGISTRY_PATH, _OUTPUTS_DIR)
    review_service = ReviewService(review_store, _OUTPUTS_DIR)
    compile_service = CompileService(_OUTPUTS_DIR)
    asset_service = AssetService(registry)
    settings_service = SettingsService()
    system_service = SystemService(_OUTPUTS_DIR)

    # Bind services and stores to app.state so dependencies can fetch them
    app.state.job_store = job_store
    app.state.review_store = review_store
    app.state.task_manager = task_manager
    app.state.registry = registry

    app.state.chapter_service = chapter_service
    app.state.generation_service = generation_service
    app.state.review_service = review_service
    app.state.compile_service = compile_service
    app.state.asset_service = asset_service
    app.state.settings_service = settings_service
    app.state.system_service = system_service

    # Load initial reviews for any existing chapters
    try:
        chapters = await chapter_service.list_chapters()
        for ch in chapters:
            sp = chapter_service.get_screenplay(ch.chapter_id)
            await review_store.initialise_chapter(
                ch.chapter_id,
                [p.panel_id for p in sp.panels],
            )
        logger.info("Initialized reviews for %d existing chapters.", len(chapters))
    except Exception as e:
        logger.warning("Could not pre-populate reviews for existing chapters: %s", e)

    yield  # ── Request handling happens here ──

    logger.info("Shutting down DharmaPath Content Studio Backend...")
    # Clean save character registry
    try:
        registry.save()
        logger.info("Character registry saved successfully.")
    except Exception as e:
        logger.error("Failed to save character registry on shutdown: %s", e)


# ── App Bootstrapping ─────────────────────────────────────────────────────────

# Rate limiting using SlowAPI
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="DharmaPath Content Studio API",
    version="1.0.0",
    description="Production-grade internal REST API for DharmaPath comic production pipeline.",
    lifespan=lifespan,
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration
studio_origin = os.environ.get("STUDIO_ORIGIN", "http://localhost:5173")
studio_origin_dev = os.environ.get("STUDIO_ORIGIN_DEV", "http://localhost:3000")
cors_origins = [
    studio_origin,
    studio_origin_dev,
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register custom exception handlers (standardizes error response payloads)
register_exception_handlers(app)


# ── Route Mounting ────────────────────────────────────────────────────────────

# Base API Router (v1 namespace)
from fastapi import APIRouter
v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(system_router)
v1_router.include_router(chapters_router)
v1_router.include_router(panels_router)
v1_router.include_router(generation_router)
v1_router.include_router(compile_router)
v1_router.include_router(assets_router)
v1_router.include_router(settings_router)
v1_router.include_router(workspace_router)

app.include_router(v1_router)


# Root fallback redirecting to docs
from fastapi.responses import RedirectResponse

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/api/v1/docs")
