"""Add home_link_settings table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-02 00:00:00.000000
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
        "home_link_settings",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("home_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("icon", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), onupdate=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("home_id", "key", name="ix_home_link_settings_home_id_key"),
    )
    op.create_index("ix_home_link_settings_home_id", "home_link_settings", ["home_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_home_link_settings_home_id", table_name="home_link_settings")
    op.drop_table("home_link_settings")
