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
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("utf-8")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("utf-8")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_b64}${digest_b64}"


def _seed_identity(db: Session) -> dict[str, str]:
    owner = User(
        email="owner@example.com",
        password_hash=_hash_password("ChangeMe123!"),
        status="active",
    )
    member = User(
        email="member@example.com",
        password_hash=None,
        status="active",
    )
    home = Home(name="Demo Home", timezone="UTC")
    db.add(owner)
    db.add(member)
    db.add(home)
    db.commit()
    db.refresh(owner)
    db.refresh(member)
    db.refresh(home)

    owner_membership = HomeMembership(
        home_id=home.id,
        client_id=owner.id,
        role="owner",
        is_default_home=True,
    )
    member_membership = HomeMembership(
        home_id=home.id,
        client_id=member.id,
        role="member",
        is_default_home=True,
    )
    db.add(owner_membership)
    db.add(member_membership)
    db.commit()

    return {
        "owner_user_id": owner.id,
        "owner_email": owner.email,
        "owner_membership_id": owner_membership.id,
        "member_user_id": member.id,
        "member_email": member.email,
        "member_membership_id": member_membership.id,
        "home_id": home.id,
    }


def _build_test_client() -> tuple[TestClient, Session]:
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

    seed_db = TestingSessionLocal()
    _seed_identity(seed_db)

    return TestClient(app), seed_db


def test_login_and_me_success() -> None:
    client, seed_db = _build_test_client()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "ChangeMe123!"},
    )
    assert login_response.status_code == 200
    payload = login_response.json()
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["token_type"] == "bearer"

    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["user"]["email"] == "owner@example.com"
    assert me_payload["default_home"]["role"] == "owner"
    assert len(me_payload["memberships"]) == 1

    seed_db.close()
    app.dependency_overrides.clear()


def test_login_invalid_password_fails() -> None:
    client, seed_db = _build_test_client()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "wrong-password"},
    )
    assert login_response.status_code == 401

    seed_db.close()
    app.dependency_overrides.clear()


def test_refresh_rotates_and_old_token_fails() -> None:
    client, seed_db = _build_test_client()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "ChangeMe123!"},
    )
    first_refresh = login_response.json()["refresh_token"]

    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert refresh_response.status_code == 200
    second_refresh = refresh_response.json()["refresh_token"]
    assert first_refresh != second_refresh

    old_refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert old_refresh_response.status_code == 401

    seed_db.close()
    app.dependency_overrides.clear()


def test_logout_revokes_refresh_token() -> None:
    client, seed_db = _build_test_client()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "ChangeMe123!"},
    )
    refresh_token = login_response.json()["refresh_token"]

    logout_response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
    )
    assert logout_response.status_code == 200
    assert logout_response.json()["success"] is True

    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 401

    seed_db.close()
    app.dependency_overrides.clear()


def test_owner_can_view_and_update_home_settings() -> None:
    client, seed_db = _build_test_client()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "ChangeMe123!"},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    settings_response = client.get("/api/v1/auth/home-settings", headers=headers)
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["home_name"] == "Demo Home"
    assert len(settings_payload["members"]) == 2
    member = next((item for item in settings_payload["members"] if item["email"] == "member@example.com"), None)
    assert member is not None
    assert member["role"] == "member"

    rename_response = client.patch(
        "/api/v1/auth/home-settings",
        json={"home_name": "Smith Household"},
        headers=headers,
    )
    assert rename_response.status_code == 200
    assert rename_response.json()["home_name"] == "Smith Household"

    role_response = client.patch(
        f"/api/v1/auth/home-settings/members/{member['membership_id']}",
        json={"role": "parent"},
        headers=headers,
    )
    assert role_response.status_code == 200
    assert role_response.json()["role"] == "parent"

    seed_db.close()
    app.dependency_overrides.clear()


def test_non_owner_home_settings_access_forbidden() -> None:
    client, seed_db = _build_test_client()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": ""},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    settings_response = client.get("/api/v1/auth/home-settings", headers=headers)
    assert settings_response.status_code == 403

    rename_response = client.patch(
        "/api/v1/auth/home-settings",
        json={"home_name": "Should Fail"},
        headers=headers,
    )
    assert rename_response.status_code == 403

    seed_db.close()
    app.dependency_overrides.clear()


def test_cannot_demote_last_owner() -> None:
    client, seed_db = _build_test_client()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "ChangeMe123!"},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    settings_response = client.get("/api/v1/auth/home-settings", headers=headers)
    assert settings_response.status_code == 200
    owner_membership = next(
        (
            item
            for item in settings_response.json()["members"]
            if item["email"] == "owner@example.com"
        ),
        None,
    )
    assert owner_membership is not None

    role_response = client.patch(
        f"/api/v1/auth/home-settings/members/{owner_membership['membership_id']}",
        json={"role": "member"},
        headers=headers,
    )
    assert role_response.status_code == 400

    seed_db.close()
    app.dependency_overrides.clear()
