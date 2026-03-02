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


def test_create_and_list() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = client.post(
        "/api/v1/link-settings",
        headers=headers,
        json={"key": "jellyfin", "label": "Jellyfin", "url": "http://192.168.1.10:8096"},
    )
    assert create_resp.status_code == 201
    data = create_resp.json()
    assert data["key"] == "jellyfin"
    assert data["label"] == "Jellyfin"
    assert data["url"] == "http://192.168.1.10:8096"
    assert data["icon"] is None

    list_resp = client.get("/api/v1/link-settings", headers=headers)
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["key"] == "jellyfin"

    db.close()
    app.dependency_overrides.clear()


def test_create_duplicate_key_returns_409() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"key": "mealie", "label": "Mealie", "url": "http://192.168.1.10:9000"}

    client.post("/api/v1/link-settings", headers=headers, json=payload)
    resp = client.post("/api/v1/link-settings", headers=headers, json=payload)
    assert resp.status_code == 409

    db.close()
    app.dependency_overrides.clear()


def test_update_link_setting() -> None:
    client, db, _ = _build_test_client()
    token = _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        "/api/v1/link-settings",
        headers=headers,
        json={"key": "photos", "label": "Photos", "url": "http://old.example.com"},
    )

    update_resp = client.put(
        "/api/v1/link-settings/photos",
        headers=headers,
        json={"label": "My Photos", "url": "http://new.example.com", "icon": "camera"},
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["label"] == "My Photos"
    assert data["url"] == "http://new.example.com"
    assert data["icon"] == "camera"

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

    client.post(
        "/api/v1/link-settings",
        headers=headers,
        json={"key": "todelete", "label": "Delete Me", "url": "http://example.com"},
    )

    del_resp = client.delete("/api/v1/link-settings/todelete", headers=headers)
    assert del_resp.status_code == 204

    list_resp = client.get("/api/v1/link-settings", headers=headers)
    assert list_resp.json() == []

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
