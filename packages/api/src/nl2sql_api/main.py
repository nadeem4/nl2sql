import asyncio
import contextlib
import logging
from typing import Tuple

from fastapi import FastAPI
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from .routes import query, health, datasource, llm, indexing
from nl2sql import NL2SQL, configure_logging, load_env_file
from .auth import warn_if_body_role_trusted

logger = logging.getLogger("nl2sql_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # nl2sql.yaml is NL2SQL_CONFIG's file, else ./nl2sql.yaml. The .env next
    # to it is loaded first and never overrides a variable already set.
    load_env_file()
    # The library no longer configures logging on import, so the application
    # entry point owns it - before anything that logs is constructed, at
    # NL2SQL_LOG_LEVEL (else INFO), as text or, with NL2SQL_LOG_FORMAT=json, JSON lines.
    configure_logging()
    engine = NL2SQL.from_config()
    # Refuse to start when a required datasource cannot connect (EngineNotReady);
    # an optional one that fails leaves the API serving the others.
    try:
        engine.check()
    except Exception:
        engine.close()
        raise
    warn_if_body_role_trusted(engine.config.api)
    app.state.engine = engine
    # Retention: once now, then once a day while the API serves.
    await asyncio.to_thread(_purge, engine)
    purging = asyncio.create_task(purge_daily(engine))
    # Dashboard schedules run only inside a long-running process; this is one (wave_c.md, D132).
    scheduling = asyncio.create_task(run_schedules(engine))
    yield
    for task in (purging, scheduling):
        task.cancel()
    for task in (purging, scheduling):
        with contextlib.suppress(asyncio.CancelledError):
            await task
    engine.close()


# How often the API applies retention (engine.storage.purge()) after the purge at startup.
PURGE_INTERVAL_S = 24 * 60 * 60
# How often the API looks for dashboard schedules that are due (engine.dashboards.run_scheduled()).
SCHEDULE_INTERVAL_S = 60


def _purge(engine) -> None:
    """``engine.storage.purge()``, logged; a failure is logged and never stops the API."""
    try:
        counts = engine.storage.purge()
    except Exception:
        logger.warning("Storage purge failed; it is tried again in a day.", exc_info=True)
        return
    logger.info("Storage purge: %s", ", ".join(f"{slot} {n}" for slot, n in counts.items()) or "nothing to purge")


async def purge_daily(engine, interval_s: float = PURGE_INTERVAL_S) -> None:
    """Calls ``engine.storage.purge()`` every ``interval_s`` seconds until cancelled."""
    while True:
        await asyncio.sleep(interval_s)
        await asyncio.to_thread(_purge, engine)


def _run_scheduled(engine) -> None:
    """``engine.dashboards.run_scheduled()``, logged; a failure is logged and never stops the API."""
    try:
        runs = engine.dashboards.run_scheduled()
    except Exception:
        logger.warning("The scheduled dashboard refresh failed; it is tried again in a minute.", exc_info=True)
        return
    if runs:
        logger.info("Scheduled refresh: %s", ", ".join(run.dashboard_id for run in runs))


async def run_schedules(engine, interval_s=None) -> None:
    """Calls ``engine.dashboards.run_scheduled()`` every ``interval_s`` seconds until cancelled."""
    while True:
        await asyncio.sleep(SCHEDULE_INTERVAL_S if interval_s is None else interval_s)
        await asyncio.to_thread(_run_scheduled, engine)


def _app_version() -> str:
    """Return the installed nl2sql-api version.

    Read from the distribution metadata so the OpenAPI spec always matches the
    version in pyproject.toml, with no second place to bump at release time. A
    source checkout where the distribution is not installed falls back instead
    of failing to import.
    """
    try:
        return version("nl2sql-api")
    except PackageNotFoundError:
        return "0.0.0"


class ConfigCORSMiddleware:
    """CORS for the origins in the engine's config (``api.cors_origins``).

    The engine is built in the lifespan, after the middleware stack exists, so
    the origins are read per request from ``app.state.engine``. With none
    configured, no cross-origin request is allowed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._cors: dict = {}

    def _for(self, origins: Tuple[str, ...]) -> ASGIApp:
        if origins not in self._cors:
            self._cors[origins] = CORSMiddleware(
                self.app,
                allow_origins=list(origins),
                allow_credentials=bool(origins),
                allow_methods=["*"],
                allow_headers=["*"],
            )
        return self._cors[origins]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        engine = getattr(scope["app"].state, "engine", None)
        config = getattr(engine, "config", None)
        origins = tuple(config.api.cors_origins) if config is not None else ()
        await self._for(origins)(scope, receive, send)


app = FastAPI(
    title="NL2SQL API",
    version=_app_version(),
    lifespan=lifespan,
)

app.include_router(query.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(datasource.router, prefix="/api/v1")
app.include_router(llm.router, prefix="/api/v1")
app.include_router(indexing.router, prefix="/api/v1")

app.add_middleware(ConfigCORSMiddleware)
