#!/usr/bin/env python3
"""Preloaded interactive DB shell (Rails console style) for local development."""

from __future__ import annotations

import argparse
import atexit
import code
import os
from pathlib import Path
import sys

from sqlalchemy import select

SCRIPT_PATH = Path(__file__).resolve()
if (SCRIPT_PATH.parents[1] / "vivian_api").exists():
    # Running from apps/api/scripts/db_shell.py
    API_DIR = SCRIPT_PATH.parents[1]
elif (SCRIPT_PATH.parents[1] / "apps" / "api" / "vivian_api").exists():
    # Running from repo-level scripts/db_shell.py
    API_DIR = SCRIPT_PATH.parents[1] / "apps" / "api"
else:
    API_DIR = SCRIPT_PATH.parents[1]

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# Keep compatibility with Docker env naming.
if not os.environ.get("DATABASE_URL") and os.environ.get("VIVIAN_API_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["VIVIAN_API_DATABASE_URL"]

from vivian_api.db.database import Base, SessionLocal
import vivian_api.models  # noqa: F401 — registers all models with Base

# Auto-discover every mapped model class from Base.
models = {
    mapper.class_.__name__: mapper.class_
    for mapper in Base.registry.mappers
}

# Pull out the ones we know for typed helpers.
User = models.get("User")
Home = models.get("Home")
HomeMembership = models.get("HomeMembership")

from vivian_api.models.identity_models import MEMBERSHIP_ROLES


def main() -> None:
    parser = argparse.ArgumentParser(description="Vivian DB shell")
    parser.add_argument("--sandbox", action="store_true", help="Roll back all changes on exit")
    args = parser.parse_args()

    db = SessionLocal()
    atexit.register(db.close)

    def user_by_email(email: str):
        return db.scalar(select(User).where(User.email == email.strip().lower()))

    def memberships_for_user(user):
        return db.scalars(select(HomeMembership).where(HomeMembership.client_id == user.id)).all()

    def all_users():
        return db.scalars(select(User)).all()

    def all_homes():
        return db.scalars(select(Home)).all()

    locals_dict = {
        "db": db,
        "select": select,
        "MEMBERSHIP_ROLES": MEMBERSHIP_ROLES,
        "user_by_email": user_by_email,
        "memberships_for_user": memberships_for_user,
        "all_users": all_users,
        "all_homes": all_homes,
        **models,
    }

    model_names = ", ".join(sorted(models))
    banner = f"""
Vivian DB shell (SQLAlchemy)
Models: {model_names}
Helpers: user_by_email(email), memberships_for_user(user), all_users(), all_homes()
Relationships: user.memberships, user.homes, membership.home, membership.client

Example:
  user = user_by_email("owner@example.com")
  user.homes
  user.memberships[0].role = "owner"
  db.commit()
""".strip()

    if args.sandbox:
        db.begin_nested()  # savepoint
        atexit.register(db.rollback)
        banner += "\n[SANDBOX] All changes will be rolled back on exit."

    try:
        from IPython import embed
        embed(user_ns=locals_dict, banner1=banner)
    except ImportError:
        code.interact(banner=banner, local=locals_dict)


if __name__ == "__main__":
    main()
