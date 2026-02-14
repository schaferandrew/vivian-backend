"""Tests for the global exception handler."""

from fastapi import HTTPException
from fastapi.testclient import TestClient

from vivian_api.main import app

client = TestClient(app)


def test_http_exception_returns_proper_status_code():
    """Test that HTTPException returns the correct status code, not 500."""
    # Test a non-existent endpoint which should return 404
    response = client.get("/api/v1/nonexistent-endpoint")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_validation_error_returns_422():
    """Test that validation errors return 422, not 500."""
    # This would require an endpoint with validation, let's test with invalid auth
    response = client.post("/api/v1/auth/login", json={})
    # Should return 422 for validation error or 400 for bad request, not 500
    assert response.status_code in [400, 422]
    assert response.status_code != 500


def test_health_check_still_works():
    """Test that health check endpoint still works."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root_endpoint_still_works():
    """Test that root endpoint still works."""
    response = client.get("/")
    assert response.status_code == 200
    assert "name" in response.json()
