"""Main FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vivian_api.config import Settings
from vivian_api.routers import receipts, ledger, integrations
from vivian_api.chat import chat_router, history_router
from vivian_api.models.schemas import HealthCheckResponse


settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    import os
    os.makedirs(settings.temp_upload_dir, exist_ok=True)
    yield
    # Shutdown
    # Cleanup temp files if needed


app = FastAPI(
    title="Vivian Household Agent API",
    description="Local-first household agent with HSA expense tracking",
    version="0.1.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(receipts.router, prefix="/api/v1")
app.include_router(ledger.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(history_router, prefix="/api/v1")


@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """Health check endpoint."""
    return HealthCheckResponse(status="healthy")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Vivian Household Agent API",
        "version": "0.1.0",
        "docs": "/docs"
    }


def main():
    """Main entry point."""
    import uvicorn
    uvicorn.run(
        "vivian_api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )


if __name__ == "__main__":
    main()
