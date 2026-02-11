"""Utility functions for the Vivian API."""

import logging
from pathlib import Path


logger = logging.getLogger(__name__)


class InvalidFilePathError(ValueError):
    """Raised when a file path is invalid or outside allowed directories."""


def validate_temp_file_path(file_path: str, temp_upload_dir: str) -> Path:
    """Validate that a file path is within the allowed temp upload directory.
    
    This prevents path traversal attacks where a malicious client could supply
    a path like "/etc/passwd" or "../../sensitive/file" to read arbitrary files.
    
    Args:
        file_path: The file path to validate (from client request)
        temp_upload_dir: The allowed temp upload directory root
        
    Returns:
        The resolved absolute Path object if valid
        
    Raises:
        InvalidFilePathError: If the path is outside the temp upload directory
        FileNotFoundError: If the file doesn't exist or is not a regular file
    """
    # Convert to Path objects and resolve to absolute paths (following symlinks)
    try:
        resolved_path = Path(file_path).resolve()
        temp_root = Path(temp_upload_dir).resolve()
    except (OSError, RuntimeError) as e:
        raise InvalidFilePathError("Invalid file path") from e
    
    # Ensure temp root exists
    if not temp_root.exists():
        raise InvalidFilePathError("Temp upload directory configuration error")
    
    # Check if the resolved path is within the temp upload directory
    # Using 'in parents' check plus equality check for the file being directly in temp_root
    if resolved_path == temp_root or temp_root in resolved_path.parents:
        # Path is valid - within temp directory
        if not resolved_path.exists():
            logger.warning("Temp file not found during validation (may have been deleted)")
            raise FileNotFoundError("File not found")
        
        # Ensure it's a regular file, not a directory
        if not resolved_path.is_file():
            logger.warning("Attempted to validate directory path as file")
            raise InvalidFilePathError("Path must be a regular file, not a directory")
        
        return resolved_path
    
    # Path is outside the allowed directory - potential security issue
    logger.warning(
        "Path traversal attempt blocked: attempted to access file outside temp directory",
        extra={"temp_root": str(temp_root)}
    )
    raise InvalidFilePathError("File path is outside allowed directory")
