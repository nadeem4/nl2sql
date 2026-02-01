from fastapi import FastAPI

# Import routers
from .routes import query, schema, health

app = FastAPI(title="NL2SQL API", version="0.1.0")

app.include_router(query.router, prefix="/api/v1")
app.include_router(schema.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")

# Add CORS middleware
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)