"""FastAPI application factory.

``create_app(settings)`` wires everything: on startup it creates the data directory and applies
pending migrations; it includes the core API routers, every feature router found under
``zfinance.domain``, and finally the SPA catch-all (which must stay last).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from zfinance import __version__, domain
from zfinance.api import health
from zfinance.config import Settings
from zfinance.db import connect
from zfinance.domain.allocation.service import run_startup_proposal_sync
from zfinance.domain.backup.service import run_startup_backup
from zfinance.domain.debts.service import run_startup_sync
from zfinance.domain.levy.service import run_startup_backpayment_sync
from zfinance.domain.payoff.service import run_startup_sync as run_startup_payoff_sync
from zfinance.domain.recurring.service import run_startup_generation
from zfinance.domain.tax.service import ensure_not_plan_item
from zfinance.i18n import api as i18n_api
from zfinance.i18n.errors import localized_http_exception_handler
from zfinance.local_guard import LocalOnlyMiddleware
from zfinance.migrator import migrate
from zfinance.spa import register_spa

logger = logging.getLogger(__name__)


def init_storage(settings: Settings) -> None:
    """Create the data directory if needed and bring the database schema up to date."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logger.info("database: %s", settings.db_path)
    conn = connect(settings.db_path)
    try:
        migrate(conn)
    finally:
        conn.close()


def discover_feature_routers() -> list[APIRouter]:
    """Import ``zfinance.domain.<feature>.api`` for each feature package that has one.

    A feature without an ``api`` module is skipped; an ``api`` module without a ``router``
    attribute is a programming error and raises.
    """
    routers: list[APIRouter] = []
    for info in sorted(pkgutil.iter_modules(domain.__path__), key=lambda i: i.name):
        if not info.ispkg:
            continue
        module_name = f"{domain.__name__}.{info.name}.api"
        if importlib.util.find_spec(module_name) is None:
            continue
        module = importlib.import_module(module_name)
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            raise TypeError(f"{module_name} must define `router = APIRouter(...)`")
        routers.append(router)
    return routers


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Without ``settings`` the environment decides (see ``zfinance.config``)."""
    resolved = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        init_storage(resolved)
        run_startup_backup(resolved)
        run_startup_generation(resolved)
        run_startup_sync(resolved)
        run_startup_backpayment_sync(resolved)
        run_startup_payoff_sync(resolved)
        run_startup_proposal_sync(resolved)
        yield

    app = FastAPI(title="z-finance", version=__version__, lifespan=lifespan)
    app.add_middleware(LocalOnlyMiddleware, extra_hosts=[resolved.host])
    app.state.settings = resolved
    # The tax plan's items change under Taxes & levies only (recurring.api.DeleteGuard).
    app.state.recurring_delete_guards = (ensure_not_plan_item,)
    app.add_exception_handler(StarletteHTTPException, localized_http_exception_handler)
    app.include_router(health.router)
    app.include_router(i18n_api.router)
    for router in discover_feature_routers():
        app.include_router(router)
    register_spa(app, resolved.web_dir)
    return app
