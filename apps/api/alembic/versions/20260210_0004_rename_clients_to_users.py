"""Rename clients table to users.

Revision ID: 20260210_0004
Revises: 20260210_0003
Create Date: 2026-02-10 12:05:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260210_0004"
down_revision: Union[str, None] = "20260210_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("clients", "users")
    op.execute("ALTER TABLE users RENAME CONSTRAINT uq_clients_email TO uq_users_email")
    op.execute("ALTER TABLE users RENAME CONSTRAINT ck_clients_status TO ck_users_status")


def downgrade() -> None:
    op.execute("ALTER TABLE users RENAME CONSTRAINT ck_users_status TO ck_clients_status")
    op.execute("ALTER TABLE users RENAME CONSTRAINT uq_users_email TO uq_clients_email")
    op.rename_table("users", "clients")
