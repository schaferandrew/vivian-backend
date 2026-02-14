"""Structured logging service for Vivian API.

Routes logs to console (development) or HTTP endpoint (staging/production)
based on environment configuration.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from logging import LogRecord
from typing import Optional

import httpx

# Global queue for async HTTP logging
_log_queue: asyncio.Queue = asyncio.Queue()
_http_logger_task: Optional[asyncio.Task] = None


class ConsoleFormatter(logging.Formatter):
    """Custom formatter for console output with colors and structure."""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[41m",  # Red background
        "RESET": "\033[0m",
    }

    def format(self, record: LogRecord) -> str:
        """Format log record with color and structure."""
        level_color = self.COLORS.get(record.levelname, "")
        reset = self.COLORS["RESET"]
        
        # Create a structured log line
        timestamp = datetime.fromtimestamp(record.created).isoformat()
        message = record.getMessage()
        
        # Include extra fields if present
        extras = ""
        if hasattr(record, "duration_ms"):
            extras += f" duration_ms={record.duration_ms}"
        if hasattr(record, "status_code"):
            extras += f" status={record.status_code}"
        if hasattr(record, "method"):
            extras += f" {record.method}"
        if hasattr(record, "path"):
            extras += f" {record.path}"
        
        return f"{level_color}[{record.levelname:8}]{reset} {timestamp} {record.name:30} {message}{extras}"


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add any extra fields
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms
        if hasattr(record, "status_code"):
            log_data["status_code"] = record.status_code
        if hasattr(record, "method"):
            log_data["method"] = record.method
        if hasattr(record, "path"):
            log_data["path"] = record.path
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id
        if hasattr(record, "tool_name"):
            log_data["tool_name"] = record.tool_name
        if hasattr(record, "service"):
            log_data["service"] = record.service
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        try:
            return json.dumps(log_data)
        except (TypeError, ValueError):
            # Fallback to string representation if JSON serialization fails
            return json.dumps({**log_data, "message": str(log_data.get("message", ""))}, default=str)


async def _http_log_sender(endpoint: str) -> None:
    """Background task that sends queued logs to HTTP endpoint.
    
    Batches logs and sends them periodically to avoid overwhelming
    the target endpoint.
    """
    batch: list[str] = []
    batch_size = 10
    timeout = 5.0
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            try:
                # Collect logs with timeout
                try:
                    log_entry = await asyncio.wait_for(
                        _log_queue.get(), timeout=timeout
                    )
                    batch.append(log_entry)
                except asyncio.TimeoutError:
                    pass  # Send what we have if queue is empty for a while
                
                # Send batch when full or timeout
                if batch and (len(batch) >= batch_size or _log_queue.empty()):
                    try:
                        await client.post(
                            endpoint,
                            json={"logs": batch},
                            timeout=timeout,
                        )
                    except (httpx.RequestError, httpx.HTTPError):
                        # Log errors locally instead of raising
                        print(f"Warning: Failed to send logs to {endpoint}", file=sys.stderr)
                    finally:
                        batch.clear()
            except Exception as e:
                print(f"Error in HTTP log sender: {e}", file=sys.stderr)
                await asyncio.sleep(1)


def setup_logging(
    environment: str = "development",
    log_level: str = "INFO",
    logger_endpoint: Optional[str] = None,
    enable_logging: bool = True,
) -> None:
    """Initialize logging configuration.
    
    Args:
        environment: "development", "staging", or "production"
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        logger_endpoint: HTTP endpoint for third-party logging (staging/production)
        enable_logging: Toggle logging on/off globally
    """
    global _http_logger_task
    
    if not enable_logging:
        # Disable all logging
        logging.disable(logging.CRITICAL)
        return
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    if environment == "development":
        # Development: console with colors
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
        console_handler.setFormatter(ConsoleFormatter())
        root_logger.addHandler(console_handler)
    else:
        # Staging/Production: structured JSON to console, optionally to HTTP
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        console_handler.setFormatter(StructuredFormatter())
        root_logger.addHandler(console_handler)
        
        # Start HTTP logger if endpoint provided
        if logger_endpoint and _http_logger_task is None:
            try:
                loop = asyncio.get_event_loop()
                _http_logger_task = loop.create_task(_http_log_sender(logger_endpoint))
                
                # Add queue-based handler for HTTP logging
                _http_handler = logging.Handler()
                _http_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
                
                def emit_http(record: LogRecord) -> None:
                    """Emit log to queue for HTTP sender."""
                    if not enable_logging:
                        return
                    msg = StructuredFormatter().format(record)
                    try:
                        _log_queue.put_nowait(msg)
                    except asyncio.QueueFull:
                        pass  # Drop log if queue is full to avoid blocking
                
                _http_handler.emit = emit_http
                root_logger.addHandler(_http_handler)
            except RuntimeError:
                # Event loop not yet available (e.g., during import)
                pass


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the given name.
    
    Args:
        name: Logger name (typically __name__)
    
    Returns:
        Logger instance with proper configuration
    """
    logger = logging.getLogger(name)
    return logger


def log_with_context(
    logger: logging.Logger,
    level: str,
    message: str,
    **context,
) -> None:
    """Log a message with additional context fields.
    
    Args:
        logger: Logger instance
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        message: Log message
        **context: Additional context fields to include in structured logs
    """
    if not logger.isEnabledFor(getattr(logging, level.upper(), logging.INFO)):
        return
    
    record = logger.makeRecord(
        logger.name,
        getattr(logging, level.upper(), logging.INFO),
        "(unknown file)",
        0,
        message,
        (),
        None,
    )
    
    # Add context fields to record
    for key, value in context.items():
        setattr(record, key, value)
    
    logger.handle(record)
