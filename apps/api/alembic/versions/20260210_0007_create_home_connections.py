"""Create home_connections table.

Revision ID: 20260210_0007
Revises: 20260210_0006
Create Date: 2026-02-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260210_0007"
down_revision: Union[str, None] = "20260210_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: homes.id and users.id are String(36), not UUID
    op.create_table(
        "home_connections",
        sa.Column("id", sa.String(36), primary_key=True, server_default=sa.text("gen_random_uuid()::text")),
        sa.Column("home_id", sa.String(36), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("connection_type", sa.String(length=50), nullable=False),
        sa.Column("connected_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("provider_email", sa.String(length=255), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    
    op.create_index(
        "ix_home_connections_home_id_provider_type",
        "home_connections",
        ["home_id", "provider", "connection_type"],
        unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_home_connections_home_id_provider_type", table_name="home_connections")
    op.drop_table("home_connections")
