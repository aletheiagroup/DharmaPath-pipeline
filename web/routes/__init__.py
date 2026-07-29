"""web/routes/__init__.py"""
from web.routes.system import router as system_router
from web.routes.chapters import router as chapters_router
from web.routes.panels import router as panels_router
from web.routes.generation import router as generation_router
from web.routes.compile import router as compile_router
from web.routes.assets import router as assets_router
from web.routes.settings import router as settings_router
from web.routes.workspace import router as workspace_router

__all__ = [
    "system_router",
    "chapters_router",
    "panels_router",
    "generation_router",
    "compile_router",
    "assets_router",
    "settings_router",
    "workspace_router",
]
