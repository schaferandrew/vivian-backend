"""Create client/home identity tables.

Revision ID: 20260210_0002
Revises: 20260208_0001
Create Date: 2026-02-10 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260210_0002"
down_revision: Union[str, None] = "20260208_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "homes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "clients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'invited')",
            name="ck_clients_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_clients_email"),
    )

    op.create_table(
        "home_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("home_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_default_home", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'parent', 'child', 'caretaker', 'member')",
            name="ck_home_memberships_role",
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["home_id"], ["homes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("home_id", "client_id", name="uq_home_memberships_home_client"),
    )
    op.create_index(
        op.f("ix_home_memberships_home_id"),
        "home_memberships",
        ["home_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_home_memberships_client_id"),
        "home_memberships",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        "uq_home_memberships_default_home_per_client",
        "home_memberships",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("is_default_home"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_home_memberships_default_home_per_client",
        table_name="home_memberships",
    )
    op.drop_index(op.f("ix_home_memberships_client_id"), table_name="home_memberships")
    op.drop_index(op.f("ix_home_memberships_home_id"), table_name="home_memberships")
    op.drop_table("home_memberships")

    op.drop_table("clients")

    op.drop_table("homes")
