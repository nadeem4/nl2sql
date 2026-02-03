from fastapi import FastAPI
from contextlib import asynccontextmanager

from .container import Container
from .routes import query, health, datasource, llm, indexing
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = Container()

    app.state.container = container
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
