"""Main FastAPI application."""

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from vivian_api.config import Settings
from vivian_api.logging_service import get_logger, setup_logging, log_with_context
from vivian_api.routers import receipts, ledger
from vivian_api.routers import mcp, integrations, mcp_settings
from vivian_api.chat import chat_router, history_router
from vivian_api.auth.router import router as auth_router
from vivian_api.models.schemas import HealthCheckResponse
from vivian_api.services.temp_cleanup import (
    start_cleanup_service,
    stop_cleanup_service,
)


settings = Settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    import os
    
    # Initialize logging
    setup_logging(
        environment=settings.environment,
        log_level=settings.log_level,
        logger_endpoint=settings.logger_endpoint,
        enable_logging=settings.enable_logging,
    )
    logger.info("Logging initialized", extra={"environment": settings.environment})
    
    os.makedirs(settings.temp_upload_dir, exist_ok=True)
    
    # Start temp file cleanup service
    await start_cleanup_service(settings)
    logger.info("Temp cleanup service started")
    
    yield
    
    # Shutdown
    # Stop temp file cleanup service
    await stop_cleanup_service()
    logger.info("Temp cleanup service stopped")


app = FastAPI(
    title="Vivian Household Agent API",
    description="Local-first household agent with HSA expense tracking",
    version="0.1.0",
    lifespan=lifespan
)


# HTTP Request Logging Middleware
class HTTPLoggingMiddleware:
    """Middleware to log HTTP requests and responses."""

    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(self, request: Request, call_next):
        """Process request and response with logging."""
        if not settings.enable_logging:
            return await call_next(request)
        
        # Skip logging for health check to reduce noise
        if request.url.path == "/health":
            return await call_next(request)
        
        start_time = time.time()
        method = request.method
        path = request.url.path
        
        # Get request size for POST/PUT requests (when applicable) without consuming the body
        body_size = 0
        if request.method in ["POST", "PUT"]:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    body_size = int(content_length)
                except ValueError:
                    body_size = 0
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000
        status_code = response.status_code
        
        # Get response headers for content length
        response_size = 0
        if "content-length" in response.headers:
            try:
                response_size = int(response.headers["content-length"])
            except ValueError:
                pass
        
        # Log the request
        level = "INFO" if 200 <= status_code < 400 else "WARNING" if 400 <= status_code < 500 else "ERROR"
        log_with_context(
            logger,
            level,
            f"HTTP {method} {path}",
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=round(duration_ms, 2),
            request_size_bytes=body_size,
            response_size_bytes=response_size,
        )
        
        return response


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP Logging Middleware (after CORS)
app.add_middleware(HTTPLoggingMiddleware)


# Include routers
app.include_router(receipts.router, prefix="/api/v1")
app.include_router(ledger.router, prefix="/api/v1")
app.include_router(mcp.router, prefix="/api/v1")
app.include_router(mcp_settings.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(history_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")


# Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all uncaught exceptions."""
    if settings.enable_logging:
        log_with_context(
            logger,
            "ERROR",
            f"Unhandled exception: {exc.__class__.__name__}",
            method=request.method,
            path=request.url.path,
            exception_type=exc.__class__.__name__,
            exception_message=str(exc),
        )
    
    # Return generic error response
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


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
