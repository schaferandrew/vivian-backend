#!/usr/bin/env python3
"""Preloaded interactive DB shell (Rails console style) for local development."""

from __future__ import annotations

import atexit
import code
import os
from pathlib import Path
import sys

from sqlalchemy import select

SCRIPT_PATH = Path(__file__).resolve()
if (SCRIPT_PATH.parents[1] / "vivian_api").exists:
    # Running from apps/api/scripts/db_shell.py
    API_DIR = SCRIPT_PATH.parents[1]
elif (SCRIPT_PATH.parents[1] / "apps" / "api" / "vivian_api").exists:
    # Running from repo-level scripts/db_shell.py
    API_DIR = SCRIPT_PATH.parents[1] / "apps" / "api"
else:
    API_DIR = SCRIPT_PATH.parents[1]

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# Keep compatibility with Docker env naming.
if not os.environ.get("DATABASE_URL") and os.environ.get("VIVIAN_API_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["VIVIAN_API_DATABASE_URL"]

from vivian_api.db.database import SessionLocal
from vivian_api.models import identity_models

Home = identity_models.Home
HomeMembership = identity_models.HomeMembership
MEMBERSHIP_ROLES = identity_models.MEMBERSHIP_ROLES
User = identity_models.User
AuthSession = getattr(identity_models, "AuthSession", None)


def main() -> None:
    db = SessionLocal()
    atexit.register(db.close)

    def user_by_email(email: str) -> User | None:
        return db.scalar(select(User).where(User.email == email.strip().lower()))

    def memberships_for_user(user: User) -> list[HomeMembership]:
        return db.scalars(
            select(HomeMembership).where(HomeMembership.client_id == user.id)
        ).all()

    locals_dict = {
        "db": db,
        "select": select,
        "User": User,
        "Home": Home,
        "HomeMembership": HomeMembership,
        "MEMBERSHIP_ROLES": MEMBERSHIP_ROLES,
        "user_by_email": user_by_email,
        "memberships_for_user": memberships_for_user,
    }
    if AuthSession is not None:
        locals_dict["AuthSession"] = AuthSession

    banner = """
Vivian DB shell (SQLAlchemy)
Preloaded: db, select, User, Home, HomeMembership, MEMBERSHIP_ROLES
Helpers: user_by_email(email), memberships_for_user(user)

Example:
  user = user_by_email("owner@schafer-hause.com")
  membership = memberships_for_user(user)[0]
  membership.role = "owner"
  db.commit()
""".strip()
    if AuthSession is not None:
        banner += "\nAuthSession model is available as AuthSession."
    else:
        banner += "\nAuthSession model is not present in this branch/schema."

    code.interact(banner=banner, local=locals_dict)


if __name__ == "__main__":
    main()
