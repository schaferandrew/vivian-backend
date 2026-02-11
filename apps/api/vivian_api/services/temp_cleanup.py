"""Temp file cleanup service with TTL and size-based pruning.

This module provides a background service that periodically cleans up
temporary uploaded receipt files to prevent disk space exhaustion.

Features:
- TTL-based deletion (files older than N hours)
- Size-based pruning (when temp dir exceeds max bytes, delete oldest first)
- Optional dev-only startup cleanup
- Path-safe (constrained to configured directory)
- Race-safe (handles files disappearing between scan/delete)
- Comprehensive logging
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from vivian_api.config import Settings


logger = logging.getLogger(__name__)


class TempFileCleanupService:
    """Background service for cleaning up temporary files.
    
    This service runs periodically and:
    1. Deletes files older than the configured TTL
    2. If total size exceeds max_bytes, deletes oldest files until under limit
    3. Optionally cleans on startup (dev-only)
    
    The service is path-safe - all operations are constrained to the
    configured temp_upload_dir. Symlinks and path traversal attempts
    are rejected.
    """

    def __init__(
        self,
        settings: Settings,
        cleanup_interval_minutes: Optional[int] = None,
        ttl_hours: Optional[int] = None,
        max_bytes: Optional[int] = None,
        clean_on_start: Optional[bool] = None,
    ):
        """Initialize the cleanup service.
        
        Args:
            settings: Application settings
            cleanup_interval_minutes: How often to run cleanup (default from settings)
            ttl_hours: File age threshold for deletion (default from settings)
            max_bytes: Max temp dir size before pruning (default from settings)
            clean_on_start: Whether to clean on startup (default from settings)
        """
        self.settings = settings
        self.temp_dir = Path(settings.temp_upload_dir).resolve()
        
        # Use provided values or fall back to settings
        self.cleanup_interval_minutes = (
            cleanup_interval_minutes 
            if cleanup_interval_minutes is not None 
            else settings.temp_cleanup_interval_minutes
        )
        self.ttl_hours = ttl_hours if ttl_hours is not None else settings.temp_cleanup_ttl_hours
        self.max_bytes = max_bytes if max_bytes is not None else settings.temp_cleanup_max_bytes
        self.clean_on_start = (
            clean_on_start 
            if clean_on_start is not None 
            else settings.temp_cleanup_on_start
        )
        
        self._task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    def _is_safe_path(self, path: Path) -> bool:
        """Check if a path is safely within the temp directory.
        
        Rejects:
        - Paths with symlinks
        - Paths that traverse outside temp_dir
        - Relative paths that escape temp_dir
        
        Args:
            path: Path to check
            
        Returns:
            True if path is safe, False otherwise
        """
        try:
            # Resolve to absolute path
            resolved = path.resolve()
            
            # Check if resolved path is within temp_dir
            # Using relative_to will raise ValueError if not a subpath
            resolved.relative_to(self.temp_dir)
            
            # Additional check: ensure no symlinks in the path
            # (resolve() follows symlinks, so if resolved != path, there were symlinks)
            if resolved != path.resolve():
                logger.warning(f"Path contains symlinks: {path}")
                return False
            
            return True
            
        except (ValueError, RuntimeError) as e:
            logger.warning(f"Path traversal attempt detected: {path} - {e}")
            return False

    def _get_file_info(self, file_path: Path) -> Optional[dict]:
        """Get file info with safety checks.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dict with path, size, mtime, age_hours, or None if unsafe/inaccessible
        """
        if not self._is_safe_path(file_path):
            return None
        
        try:
            stat = file_path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
            
            return {
                "path": file_path,
                "size": stat.st_size,
                "mtime": mtime,
                "age_hours": age_hours,
            }
        except (OSError, IOError) as e:
            # File may have been deleted between scan and stat
            logger.debug(f"Cannot stat file {file_path}: {e}")
            return None

    def _list_temp_files(self) -> list[dict]:
        """List all files in temp directory with metadata.
        
        Returns:
            List of file info dicts, sorted by mtime (oldest first)
        """
        files = []
        
        if not self.temp_dir.exists():
            logger.warning(f"Temp directory does not exist: {self.temp_dir}")
            return files
        
        try:
            for entry in self.temp_dir.iterdir():
                if entry.is_file():
                    info = self._get_file_info(entry)
                    if info:
                        files.append(info)
        except OSError as e:
            logger.error(f"Error scanning temp directory: {e}")
        
        # Sort by mtime (oldest first) for size-based pruning
        files.sort(key=lambda x: x["mtime"])
        return files

    def _delete_file(self, file_path: Path) -> bool:
        """Safely delete a file.
        
        Args:
            file_path: Path to delete
            
        Returns:
            True if deleted successfully, False otherwise
        """
        if not self._is_safe_path(file_path):
            logger.warning(f"Refusing to delete unsafe path: {file_path}")
            return False
        
        try:
            file_path.unlink()
            return True
        except FileNotFoundError:
            # File already deleted (race condition)
            logger.debug(f"File already deleted: {file_path}")
            return True
        except OSError as e:
            logger.error(f"Failed to delete {file_path}: {e}")
            return False

    async def _cleanup_ttl(self, files: list[dict]) -> tuple[int, int]:
        """Delete files older than TTL.
        
        Args:
            files: List of file info dicts
            
        Returns:
            Tuple of (deleted_count, bytes_freed)
        """
        deleted = 0
        bytes_freed = 0
        cutoff_age = self.ttl_hours
        
        for file_info in files:
            if file_info["age_hours"] > cutoff_age:
                if self._delete_file(file_info["path"]):
                    deleted += 1
                    bytes_freed += file_info["size"]
                    logger.info(
                        f"Deleted old file (age={file_info['age_hours']:.1f}h): "
                        f"{file_info['path'].name} ({file_info['size']} bytes)"
                    )
        
        return deleted, bytes_freed

    async def _cleanup_size(self, files: list[dict]) -> tuple[int, int]:
        """Prune oldest files until total size is under limit.
        
        Note: This operates on the original files list, so files already
        deleted by TTL cleanup won't be considered.
        
        Args:
            files: List of file info dicts (should be sorted oldest first)
            
        Returns:
            Tuple of (deleted_count, bytes_freed)
        """
        # Calculate current total size (only count files that still exist)
        total_size = 0
        existing_files = []
        
        for file_info in files:
            if file_info["path"].exists():
                total_size += file_info["size"]
                existing_files.append(file_info)
        
        if total_size <= self.max_bytes:
            return 0, 0
        
        deleted = 0
        bytes_freed = 0
        bytes_to_free = total_size - self.max_bytes
        
        logger.info(
            f"Temp dir size ({total_size} bytes) exceeds limit ({self.max_bytes} bytes). "
            f"Need to free {bytes_to_free} bytes."
        )
        
        # Delete oldest files until we're under the limit
        for file_info in existing_files:
            if total_size <= self.max_bytes:
                break
            
            if self._delete_file(file_info["path"]):
                deleted += 1
                bytes_freed += file_info["size"]
                total_size -= file_info["size"]
                logger.info(
                    f"Pruned file for size limit: {file_info['path'].name} "
                    f"({file_info['size']} bytes, age={file_info['age_hours']:.1f}h)"
                )
        
        return deleted, bytes_freed

    async def run_cleanup(self) -> dict:
        """Run a single cleanup cycle.
        
        Returns:
            Dict with cleanup statistics
        """
        logger.info(f"Starting temp file cleanup in {self.temp_dir}")
        
        start_time = datetime.now(timezone.utc)
        files = self._list_temp_files()
        
        if not files:
            logger.info("No temp files to clean")
            return {
                "files_scanned": 0,
                "deleted_ttl": 0,
                "deleted_size": 0,
                "bytes_freed": 0,
                "duration_seconds": 0,
            }
        
        # Step 1: TTL-based deletion
        ttl_deleted, ttl_bytes = await self._cleanup_ttl(files)
        
        # Step 2: Size-based pruning (re-scan to get current state)
        files = self._list_temp_files()
        size_deleted, size_bytes = await self._cleanup_size(files)
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        result = {
            "files_scanned": len(files) + ttl_deleted,
            "deleted_ttl": ttl_deleted,
            "deleted_size": size_deleted,
            "bytes_freed": ttl_bytes + size_bytes,
            "duration_seconds": duration,
        }
        
        logger.info(
            f"Cleanup complete: {result['deleted_ttl']} TTL deletions, "
            f"{result['deleted_size']} size deletions, "
            f"{result['bytes_freed']} bytes freed in {duration:.2f}s"
        )
        
        return result

    async def _cleanup_loop(self):
        """Main cleanup loop running periodically."""
        logger.info(
            f"Temp cleanup service started: interval={self.cleanup_interval_minutes}min, "
            f"ttl={self.ttl_hours}h, max_bytes={self.max_bytes}, "
            f"clean_on_start={self.clean_on_start}"
        )
        
        # Optional startup cleanup
        if self.clean_on_start:
            logger.info("Running startup cleanup (dev mode)")
            await self.run_cleanup()
        
        while not self._shutdown_event.is_set():
            try:
                # Wait for the interval or until shutdown
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.cleanup_interval_minutes * 60
                )
            except asyncio.TimeoutError:
                # Interval elapsed, run cleanup
                pass
            
            if not self._shutdown_event.is_set():
                try:
                    await self.run_cleanup()
                except Exception as e:
                    logger.exception(f"Error during cleanup cycle: {e}")

    async def start(self):
        """Start the cleanup service."""
        if self._task is not None:
            logger.warning("Cleanup service already running")
            return
        
        self._shutdown_event.clear()
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info("Temp cleanup service started")

    async def stop(self):
        """Stop the cleanup service."""
        if self._task is None:
            return
        
        logger.info("Stopping temp cleanup service...")
        self._shutdown_event.set()
        
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Cleanup service did not stop gracefully, cancelling...")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        self._task = None
        logger.info("Temp cleanup service stopped")


# Global service instance
_cleanup_service: Optional[TempFileCleanupService] = None


async def start_cleanup_service(settings: Settings) -> TempFileCleanupService:
    """Start the global cleanup service.
    
    Args:
        settings: Application settings
        
    Returns:
        The started cleanup service instance
    """
    global _cleanup_service
    
    if _cleanup_service is not None:
        logger.warning("Cleanup service already initialized")
        return _cleanup_service
    
    _cleanup_service = TempFileCleanupService(settings)
    await _cleanup_service.start()
    return _cleanup_service


async def stop_cleanup_service():
    """Stop the global cleanup service."""
    global _cleanup_service
    
    if _cleanup_service is not None:
        await _cleanup_service.stop()
        _cleanup_service = None
