"""Add port column and make url nullable on home_link_settings.

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-02 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("home_link_settings", "url", nullable=True)
    op.add_column("home_link_settings", sa.Column("port", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("home_link_settings", "port")
    op.alter_column("home_link_settings", "url", nullable=False)
