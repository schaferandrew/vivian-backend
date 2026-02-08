#!/usr/bin/env python3
"""Run Alembic migrations for the API service."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_migration() -> None:
    """Apply all pending Alembic migrations."""
    api_dir = Path(__file__).resolve().parents[1] / "apps" / "api"
    print(f"Running Alembic migrations in {api_dir} ...")

    subprocess.run(
        ["alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=api_dir,
        check=True,
    )

    print("Database migrations applied successfully.")


if __name__ == "__main__":
    run_migration()
