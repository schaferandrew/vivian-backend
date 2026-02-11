"""Tests for utility functions."""

import pytest
import tempfile
from pathlib import Path

from vivian_api.utils import validate_temp_file_path, InvalidFilePathError


class TestValidateTempFilePath:
    """Tests for validate_temp_file_path function."""

    def test_valid_path_in_temp_dir(self):
        """Test that a valid path within temp dir is accepted."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a test file
            test_file = Path(temp_dir) / "test.pdf"
            test_file.write_text("test content")
            
            # Should succeed
            result = validate_temp_file_path(str(test_file), temp_dir)
            assert result == test_file.resolve()

    def test_path_outside_temp_dir_rejected(self):
        """Test that a path outside temp dir is rejected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Try to access a file outside temp dir
            outside_path = "/etc/passwd"
            
            with pytest.raises(InvalidFilePathError) as exc_info:
                validate_temp_file_path(outside_path, temp_dir)
            
            assert "outside allowed directory" in str(exc_info.value).lower()

    def test_path_traversal_rejected(self):
        """Test that path traversal attempts are rejected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a test file
            test_file = Path(temp_dir) / "test.pdf"
            test_file.write_text("test content")
            
            # Try to use .. to escape the temp dir
            traversal_path = str(Path(temp_dir) / ".." / ".." / "etc" / "passwd")
            
            with pytest.raises(InvalidFilePathError) as exc_info:
                validate_temp_file_path(traversal_path, temp_dir)
            
            assert "outside allowed directory" in str(exc_info.value).lower()

    def test_nonexistent_file_raises_filenotfound(self):
        """Test that nonexistent files raise FileNotFoundError."""
        with tempfile.TemporaryDirectory() as temp_dir:
            nonexistent = Path(temp_dir) / "nonexistent.pdf"
            
            with pytest.raises(FileNotFoundError) as exc_info:
                validate_temp_file_path(str(nonexistent), temp_dir)
            
            assert "not found" in str(exc_info.value).lower()

    def test_symlink_escape_rejected(self):
        """Test that symlinks pointing outside temp dir are rejected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Create a symlink pointing outside the temp dir
            symlink_path = temp_path / "escape_link"
            try:
                symlink_path.symlink_to("/etc/passwd")
            except (OSError, NotImplementedError):
                # Skip test if symlinks not supported (e.g., Windows without admin)
                pytest.skip("Symlinks not supported on this system")
            
            with pytest.raises(InvalidFilePathError) as exc_info:
                validate_temp_file_path(str(symlink_path), temp_dir)
            
            assert "outside allowed directory" in str(exc_info.value).lower()

    def test_file_directly_in_temp_root(self):
        """Test that a file directly in temp root is accepted."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = Path(temp_dir) / "root_file.pdf"
            test_file.write_text("test content")
            
            result = validate_temp_file_path(str(test_file), temp_dir)
            assert result == test_file.resolve()

    def test_file_in_subdirectory(self):
        """Test that a file in a subdirectory of temp dir is accepted."""
        with tempfile.TemporaryDirectory() as temp_dir:
            subdir = Path(temp_dir) / "subdir"
            subdir.mkdir()
            test_file = subdir / "test.pdf"
            test_file.write_text("test content")
            
            result = validate_temp_file_path(str(test_file), temp_dir)
            assert result == test_file.resolve()

    def test_nonexistent_temp_dir_rejected(self):
        """Test that a nonexistent temp dir causes an error."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = Path(temp_dir) / "test.pdf"
            test_file.write_text("test content")
        
        # temp_dir is now deleted
        with pytest.raises(InvalidFilePathError) as exc_info:
            validate_temp_file_path(str(test_file), temp_dir)
        
        assert "configuration error" in str(exc_info.value).lower()

    def test_directory_path_rejected(self):
        """Test that a directory path (not a file) is rejected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            subdir = Path(temp_dir) / "subdir"
            subdir.mkdir()
            
            # Try to validate a directory path
            with pytest.raises(InvalidFilePathError) as exc_info:
                validate_temp_file_path(str(subdir), temp_dir)
            
            assert "must be a regular file" in str(exc_info.value).lower()
