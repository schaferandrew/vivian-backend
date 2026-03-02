"""Tests for /api/v1/link-settings endpoints."""

from __future__ import annotations

import base64
import hashlib
import secrets

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from vivian_api.db.database import Base, get_db
from vivian_api.main import app
from vivian_api.models.identity_models import Home, HomeMembership, User


PBKDF2_ITERATIONS = 390000


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    salt_b64 = base64.urlsafe_b64encode(salt).decode()
    digest_b64 = base64.urlsafe_b64encode(digest).decode()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_b64}${digest_b64}"


def _build_test_client() -> tuple[TestClient, Session, str]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    user = User(email="owner@example.com", password_hash=_hash_password("Pass123!"), status="active")
    home = Home(name="Test Home", timezone="UTC")
    db.add(user)
    db.add(home)
    db.commit()
    db.refresh(user)
    db.refresh(home)
    membership = HomeMembership(home_id=home.id, client_id=user.id, role="owner", is_default_home=True)
    db.add(membership)
    db.commit()

    return TestClient(app), db, home.id


def _get_token(client: TestClient) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "Pass123!"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_list_empty() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)

    resp = client.get("/api/v1/link-settings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []

    db.close()
    app.dependency_overrides.clear()


def test_create_server_url_entry() -> None:
    """server_url key stores a full URL with no port."""
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/link-settings",
        headers=headers,
        json={"key": "server_url", "label": "Home Server", "url": "http://192.168.1.10"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["key"] == "server_url"
    assert data["url"] == "http://192.168.1.10"
    assert data["port"] is None

    db.close()
    app.dependency_overrides.clear()


def test_create_port_based_entry() -> None:
    """App entries store a port; url is null."""
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/link-settings",
        headers=headers,
        json={"key": "jellyfin", "label": "Jellyfin", "port": 8096},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["key"] == "jellyfin"
    assert data["port"] == 8096
    assert data["url"] is None

    db.close()
    app.dependency_overrides.clear()


def test_full_server_config_flow() -> None:
    """Create server_url + two app entries, list returns all three."""
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/v1/link-settings", headers=headers,
                json={"key": "server_url", "label": "Home Server", "url": "http://192.168.1.10"})
    client.post("/api/v1/link-settings", headers=headers,
                json={"key": "jellyfin", "label": "Jellyfin", "port": 8096})
    client.post("/api/v1/link-settings", headers=headers,
                json={"key": "mealie", "label": "Mealie", "port": 9000})

    resp = client.get("/api/v1/link-settings", headers=headers)
    assert resp.status_code == 200
    items = {item["key"]: item for item in resp.json()}
    assert len(items) == 3
    assert items["server_url"]["url"] == "http://192.168.1.10"
    assert items["jellyfin"]["port"] == 8096
    assert items["mealie"]["port"] == 9000

    db.close()
    app.dependency_overrides.clear()


def test_create_duplicate_key_returns_409() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"key": "mealie", "label": "Mealie", "port": 9000}

    client.post("/api/v1/link-settings", headers=headers, json=payload)
    resp = client.post("/api/v1/link-settings", headers=headers, json=payload)
    assert resp.status_code == 409

    db.close()
    app.dependency_overrides.clear()


def test_update_port() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/v1/link-settings", headers=headers,
                json={"key": "jellyfin", "label": "Jellyfin", "port": 8096})

    resp = client.put("/api/v1/link-settings/jellyfin", headers=headers, json={"port": 8920})
    assert resp.status_code == 200
    assert resp.json()["port"] == 8920

    db.close()
    app.dependency_overrides.clear()


def test_update_server_url() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/v1/link-settings", headers=headers,
                json={"key": "server_url", "label": "Home Server", "url": "http://192.168.1.10"})

    resp = client.put("/api/v1/link-settings/server_url", headers=headers,
                      json={"url": "http://192.168.1.20"})
    assert resp.status_code == 200
    assert resp.json()["url"] == "http://192.168.1.20"

    db.close()
    app.dependency_overrides.clear()


def test_update_missing_key_returns_404() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.put("/api/v1/link-settings/nonexistent", headers=headers, json={"label": "X"})
    assert resp.status_code == 404

    db.close()
    app.dependency_overrides.clear()


def test_delete_link_setting() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/v1/link-settings", headers=headers,
                json={"key": "todelete", "label": "Delete Me", "port": 1234})

    assert client.delete("/api/v1/link-settings/todelete", headers=headers).status_code == 204
    assert client.get("/api/v1/link-settings", headers=headers).json() == []

    db.close()
    app.dependency_overrides.clear()


def test_delete_missing_key_returns_404() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.delete("/api/v1/link-settings/ghost", headers=headers)
    assert resp.status_code == 404

    db.close()
    app.dependency_overrides.clear()


def test_unauthenticated_returns_401() -> None:
    client, db, _ = _build_test_client()

    resp = client.get("/api/v1/link-settings")
    assert resp.status_code == 401

    db.close()
    app.dependency_overrides.clear()
