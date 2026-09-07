import os

from fastapi import FastAPI
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from .routes import query, health, datasource, llm, indexing
from fastapi.middleware.cors import CORSMiddleware
from nl2sql import NL2SQL
from nl2sql.common.logger import configure_logging
from nl2sql.common.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The library no longer configures logging on import, so the application
    # entry point owns it - before anything that logs is constructed.
    configure_logging(
        level="INFO",
        json_format=(settings.observability_exporter == "otlp"),
    )
    app.state.engine = NL2SQL()
    yield


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

_origins = [o.strip() for o in os.getenv("NL2SQL_API_CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=bool(_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


