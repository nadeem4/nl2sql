from fastapi import FastAPI
from contextlib import asynccontextmanager

from .routes import query, health, datasource, llm, indexing
from fastapi.middleware.cors import CORSMiddleware
from nl2sql import NL2SQL


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = NL2SQL()
    yield


app = FastAPI(
    title="NL2SQL API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(query.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(datasource.router, prefix="/api/v1")
app.include_router(llm.router, prefix="/api/v1")
app.include_router(indexing.router, prefix="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


