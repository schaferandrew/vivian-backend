"""Add per-home custom MCP server definitions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_custom_server_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("home_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("server_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("command_tokens", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("server_path", sa.String(length=1024), nullable=True),
        sa.Column("default_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("home_id", "server_id", name="ix_mcp_custom_server_definitions_home_id_server_id"),
    )
    op.create_index(
        "ix_mcp_custom_server_definitions_home_id",
        "mcp_custom_server_definitions",
        ["home_id"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO mcp_custom_server_definitions (
            home_id,
            server_id,
            name,
            command_tokens,
            default_enabled,
            source,
            created_at,
            updated_at
        )
        SELECT DISTINCT
            home_id,
            mcp_server_id,
            mcp_server_id,
            '[]'::jsonb,
            true,
            'legacy',
            NOW(),
            NOW()
        FROM mcp_server_settings
        ON CONFLICT (home_id, server_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_custom_server_definitions_home_id", table_name="mcp_custom_server_definitions")
    op.drop_table("mcp_custom_server_definitions")
