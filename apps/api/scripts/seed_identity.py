#!/usr/bin/env python3
"""Seed homes/users/memberships for local development."""

from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import import_module
import os
from dataclasses import dataclass
from pathlib import Path
import secrets
import sys

SCRIPT_PATH = Path(__file__).resolve()
if (SCRIPT_PATH.parents[1] / "vivian_api").exists:
    # Running from apps/api/scripts/seed_identity.py
    API_DIR = SCRIPT_PATH.parents[1]
elif (SCRIPT_PATH.parents[1] / "apps" / "api" / "vivian_api").exists:
    # Running from repo-level scripts/seed_identity.py
    API_DIR = SCRIPT_PATH.parents[1] / "apps" / "api"
else:
    API_DIR = SCRIPT_PATH.parents[1]

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# Keep compatibility with Docker env naming.
if not os.environ.get("DATABASE_URL") and os.environ.get("VIVIAN_API_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["VIVIAN_API_DATABASE_URL"]

ROLE_ALIASES = {}
PBKDF2_ITERATIONS = 390000
DEFAULT_CLIENT_STATUSES = ("active", "disabled", "invited")
DEFAULT_MEMBERSHIP_ROLES = ("owner", "parent", "child", "caretaker", "guest", "member")


def _runtime_deps():
    sqlalchemy = import_module("sqlalchemy")
    select = sqlalchemy.select
    SessionLocal = import_module("vivian_api.db.database").SessionLocal
    identity_models = import_module("vivian_api.models.identity_models")
    return {
        "select": select,
        "SessionLocal": SessionLocal,
        "CLIENT_STATUSES": tuple(identity_models.CLIENT_STATUSES),
        "MEMBERSHIP_ROLES": tuple(identity_models.MEMBERSHIP_ROLES),
        "Client": identity_models.Client,
        "Home": identity_models.Home,
        "HomeMembership": identity_models.HomeMembership,
    }


@dataclass
class ClientSeedSpec:
    email: str
    role: str
    is_default_home: bool


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _parse_client_spec(raw_spec: str, membership_roles: tuple[str, ...]) -> ClientSeedSpec:
    # Format: email:role[:default]
    parts = [p.strip() for p in raw_spec.split(":")]
    if len(parts) < 2 or len(parts) > 3:
        raise ValueError(
            f"Invalid --client value '{raw_spec}'. Use email:role[:default]."
        )

    email = _normalize_email(parts[0])
    role = ROLE_ALIASES.get(parts[1].lower(), parts[1].lower())
    if role not in membership_roles:
        raise ValueError(
            f"Invalid role '{parts[1]}'. Allowed roles: "
            f"{', '.join(membership_roles)}."
        )

    is_default = False
    if len(parts) == 3:
        marker = parts[2].lower()
        is_default = marker in {"default", "true", "1", "yes"}

    return ClientSeedSpec(email=email, role=role, is_default_home=is_default)


def _parse_password_map(raw_values: list[str] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in raw_values or []:
        email, sep, password = raw.partition(":")
        if not sep:
            raise ValueError(
                f"Invalid --password value '{raw}'. Use email:plain_password."
            )
        normalized_email = _normalize_email(email)
        if not normalized_email:
            raise ValueError(f"Invalid --password value '{raw}'. Email is required.")
        if not password:
            raise ValueError(f"Invalid --password value '{raw}'. Password is required.")
        mapping[normalized_email] = password
    return mapping


def _dedupe_client_specs(specs: list[ClientSeedSpec]) -> list[ClientSeedSpec]:
    deduped: dict[tuple[str, str], ClientSeedSpec] = {}
    for spec in specs:
        deduped[(spec.email, spec.role)] = spec
    return list(deduped.values())


def _auto_role_specs(
    *,
    membership_roles: tuple[str, ...],
    default_email_domain: str,
) -> list[ClientSeedSpec]:
    specs: list[ClientSeedSpec] = []
    for role in membership_roles:
        local_part = role.replace("_", "-")
        email = f"{local_part}@{default_email_domain}"
        specs.append(
            ClientSeedSpec(
                email=email,
                role=role,
                is_default_home=True,
            )
        )
    return specs


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("utf-8")
    hash_b64 = base64.urlsafe_b64encode(dk).decode("utf-8")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_b64}${hash_b64}"


def _create_home(home_name: str, timezone: str) -> Home:
    deps = _runtime_deps()
    SessionLocal = deps["SessionLocal"]
    Home = deps["Home"]

    with SessionLocal() as db:
        home = Home(name=home_name, timezone=timezone)
        db.add(home)
        db.commit()
        db.refresh(home)
        return home


def _find_or_create_client(
    *,
    email: str,
    status: str,
    plain_password: str | None,
    force_empty_password: bool = False,
) -> tuple[Client, bool]:
    deps = _runtime_deps()
    select = deps["select"]
    SessionLocal = deps["SessionLocal"]
    Client = deps["Client"]

    with SessionLocal() as db:
        client = db.scalar(select(Client).where(Client.email == email))
        created = False
        if client is None:
            client = Client(email=email, status=status)
            if force_empty_password:
                client.password_hash = None
            elif plain_password:
                client.password_hash = _hash_password(plain_password)
            db.add(client)
            db.commit()
            db.refresh(client)
            created = True
            return client, created

        updated = False
        if client.status != status:
            client.status = status
            updated = True
        if force_empty_password and client.password_hash is not None:
            client.password_hash = None
            updated = True
        elif plain_password:
            client.password_hash = _hash_password(plain_password)
            updated = True
        if updated:
            db.commit()
            db.refresh(client)

        return client, created


def _upsert_membership(
    *,
    home_id: str,
    client_id: str,
    role: str,
    is_default_home: bool,
) -> tuple[HomeMembership, bool]:
    deps = _runtime_deps()
    select = deps["select"]
    SessionLocal = deps["SessionLocal"]
    HomeMembership = deps["HomeMembership"]

    with SessionLocal() as db:
        membership = db.scalar(
            select(HomeMembership).where(
                HomeMembership.home_id == home_id,
                HomeMembership.client_id == client_id,
            )
        )
        created = False
        if membership is None:
            membership = HomeMembership(
                home_id=home_id,
                client_id=client_id,
                role=role,
                is_default_home=is_default_home,
            )
            db.add(membership)
            created = True
        else:
            membership.role = role
            membership.is_default_home = is_default_home

        if is_default_home:
            other_defaults = db.scalars(
                select(HomeMembership).where(
                    HomeMembership.client_id == client_id,
                    HomeMembership.home_id != home_id,
                    HomeMembership.is_default_home.is_(True),
                )
            ).all()
            for other in other_defaults:
                other.is_default_home = False

        db.commit()
        db.refresh(membership)
        return membership, created


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed user/home identity rows.")
    parser.add_argument("--home-name", required=True, help="Home display name.")
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="Home timezone (default: UTC).",
    )
    parser.add_argument(
        "--client",
        action="append",
        help="User spec: email:role[:default]. Repeat for multiple users.",
    )
    parser.add_argument(
        "--seed-all-roles",
        action="store_true",
        help=(
            "Auto-create one user for each membership role. "
            "Generated emails use --default-email-domain."
        ),
    )
    parser.add_argument(
        "--default-email-domain",
        default="example.com",
        help="Email domain for auto-generated role members (default: example.com).",
    )
    parser.add_argument(
        "--password",
        action="append",
        help="Optional password mapping: email:plain_password. Repeat as needed.",
    )
    parser.add_argument(
        "--status",
        default="active",
        choices=DEFAULT_CLIENT_STATUSES,
        help="User status for all supplied users (default: active).",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    deps = _runtime_deps()
    membership_roles = deps["MEMBERSHIP_ROLES"]
    client_specs = [
        _parse_client_spec(raw_spec, membership_roles) for raw_spec in (args.client or [])
    ]
    if args.seed_all_roles or not client_specs:
        client_specs.extend(
            _auto_role_specs(
                membership_roles=membership_roles,
                default_email_domain=args.default_email_domain.strip().lower(),
            )
        )
    client_specs = _dedupe_client_specs(client_specs)
    password_map = _parse_password_map(args.password)

    home = _create_home(args.home_name.strip(), args.timezone.strip())
    print(f"Created home: {home.name} ({home.timezone}) id={home.id}")

    for spec in client_specs:
        spec.is_default_home = True
        is_owner = spec.role == "owner"
        plain_password = password_map.get(spec.email) if is_owner else None
        force_empty_password = not is_owner
        if not is_owner and spec.email in password_map:
            print(
                f"Ignoring --password for non-owner role '{spec.role}' "
                f"({spec.email}); password remains empty."
            )

        client, client_created = _find_or_create_client(
            email=spec.email,
            status=args.status,
            plain_password=plain_password,
            force_empty_password=force_empty_password,
        )
        print(
            f"{'Created' if client_created else 'Found'} client: "
            f"{client.email} id={client.id}"
        )

        membership, membership_created = _upsert_membership(
            home_id=home.id,
            client_id=client.id,
            role=spec.role,
            is_default_home=spec.is_default_home,
        )
        print(
            f"{'Created' if membership_created else 'Updated'} membership: "
            f"client={client.email} role={membership.role} "
            f"default={membership.is_default_home} id={membership.id}"
        )

    print("Identity seeding complete.")


if __name__ == "__main__":
    main()
