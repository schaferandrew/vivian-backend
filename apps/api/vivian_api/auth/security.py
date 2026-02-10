"""Authentication primitives for password/JWT/session token handling."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivian_api.config import Settings
from vivian_api.models.identity_models import AuthSession, User


PBKDF2_PREFIX = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 390000


class TokenExpiredError(Exception):
    """Raised when JWT token has expired."""


class TokenInvalidError(Exception):
    """Raised when JWT token is invalid."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_password(plain_password: str, password_hash: str | None) -> bool:
    """Verify password against supported hash formats."""
    if not password_hash:
        return False

    # Current seed script format: pbkdf2_sha256$390000$salt_b64$hash_b64
    parts = password_hash.split("$")
    if len(parts) == 4 and parts[0] == PBKDF2_PREFIX:
        _, raw_iterations, salt_b64, expected_b64 = parts
        try:
            iterations = int(raw_iterations)
            salt = _urlsafe_b64decode(salt_b64)
            expected = _urlsafe_b64decode(expected_b64)
        except Exception:
            return False

        computed = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(computed, expected)

    # Future migration path: add Argon2id verifier branch when hashes are introduced.
    if password_hash.startswith("argon2id$"):
        return False

    return False


def hash_refresh_token(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(64)


def hash_password(plain_password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        plain_password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("utf-8")
    hash_b64 = base64.urlsafe_b64encode(dk).decode("utf-8")
    return f"{PBKDF2_PREFIX}${PBKDF2_ITERATIONS}${salt_b64}${hash_b64}"


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    normalized_email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email))
    if not user:
        return None
    if user.status != "active":
        return None
    if not user.password_hash:
        return user if password == "" else None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_access_token(*, user: User, settings: Settings) -> tuple[str, int]:
    if settings.auth_jwt_algorithm != "HS256":
        raise ValueError("Only HS256 is supported")

    expires_delta = timedelta(minutes=settings.auth_access_token_minutes)
    expires_at = _utc_now() + expires_delta
    now_ts = int(_utc_now().timestamp())
    payload: dict[str, Any] = {
        "sub": user.id,
        "email": user.email,
        "type": "access",
        "exp": int(expires_at.timestamp()),
        "iat": now_ts,
    }

    header = {"alg": settings.auth_jwt_algorithm, "typ": "JWT"}
    header_segment = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_segment = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    signature = hmac.new(
        settings.auth_jwt_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    token = f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    if settings.auth_jwt_algorithm != "HS256":
        raise TokenInvalidError("Unsupported JWT algorithm")

    parts = token.split(".")
    if len(parts) != 3:
        raise TokenInvalidError("Malformed JWT")

    header_segment, payload_segment, signature_segment = parts
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    expected_signature = hmac.new(
        settings.auth_jwt_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    actual_signature = _b64url_decode(signature_segment)
    if not hmac.compare_digest(expected_signature, actual_signature):
        raise TokenInvalidError("Invalid JWT signature")

    try:
        header = json.loads(_b64url_decode(header_segment).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except Exception as exc:
        raise TokenInvalidError("Invalid JWT payload") from exc

    if header.get("alg") != settings.auth_jwt_algorithm:
        raise TokenInvalidError("Invalid JWT algorithm")

    exp = int(payload.get("exp", 0))
    now_ts = int(_utc_now().timestamp())
    if exp <= now_ts:
        raise TokenExpiredError("JWT expired")

    return payload


def build_auth_session(
    *,
    user_id: str,
    refresh_token: str,
    settings: Settings,
    user_agent: str | None,
    ip_address: str | None,
) -> AuthSession:
    expires_at = _utc_now() + timedelta(days=settings.auth_refresh_token_days)
    return AuthSession(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        user_agent=user_agent,
        ip_address=ip_address,
        expires_at=expires_at,
    )


def is_session_active(session: AuthSession) -> bool:
    now = _utc_now()
    return session.revoked_at is None and session.expires_at.replace(tzinfo=timezone.utc) > now
