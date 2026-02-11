"""Create mcp_server_settings table.

Revision ID: 20260210_0008
Revises: 20260210_0007
Create Date: 2026-02-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260210_0008"
down_revision: Union[str, None] = "20260210_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_server_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("home_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mcp_server_id", sa.String(length=100), nullable=False),
        sa.Column("settings_json", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    
    op.create_index(
        "ix_mcp_server_settings_home_id_server",
        "mcp_server_settings",
        ["home_id", "mcp_server_id"],
        unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_server_settings_home_id_server", table_name="mcp_server_settings")
    op.drop_table("mcp_server_settings")
