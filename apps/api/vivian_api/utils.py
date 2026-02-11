"""Utility functions for the Vivian API."""

from pathlib import Path


class InvalidFilePathError(ValueError):
    """Raised when a file path is invalid or outside allowed directories."""
    pass


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
        raise InvalidFilePathError(f"Invalid file path: {e}") from e
    
    # Ensure temp root exists
    if not temp_root.exists():
        raise InvalidFilePathError(f"Temp upload directory does not exist: {temp_upload_dir}")
    
    # Check if the resolved path is within the temp upload directory
    # Using 'in parents' check plus equality check for the file being directly in temp_root
    if resolved_path == temp_root or temp_root in resolved_path.parents:
        # Path is valid - within temp directory
        if not resolved_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Ensure it's a regular file, not a directory
        if not resolved_path.is_file():
            raise InvalidFilePathError("Path must be a regular file, not a directory")
        
        return resolved_path
    
    # Path is outside the allowed directory
    raise InvalidFilePathError(
        f"File path is outside temp upload directory. "
        f"Path: {resolved_path}, Allowed directory: {temp_root}"
    )
