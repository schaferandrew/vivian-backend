"""Add home_api_keys table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "home_api_keys",
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "home_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("homes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("key_prefix", sa.String(20), nullable=False),
        sa.Column(
            "created_by",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_home_api_keys_home_id", "home_api_keys", ["home_id"])
    op.create_index(
        "ix_home_api_keys_key_hash", "home_api_keys", ["key_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_table("home_api_keys")
