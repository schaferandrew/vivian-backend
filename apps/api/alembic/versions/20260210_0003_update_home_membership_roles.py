"""Update home membership role constraint to add guest role.

Revision ID: 20260210_0003
Revises: 20260210_0002
Create Date: 2026-02-10 11:20:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260210_0003"
down_revision: Union[str, None] = "20260210_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_home_memberships_role", "home_memberships", type_="check")
    op.create_check_constraint(
        "ck_home_memberships_role",
        "home_memberships",
        "role IN ('owner', 'parent', 'child', 'caretaker', 'guest', 'member')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_home_memberships_role", "home_memberships", type_="check")
    op.create_check_constraint(
        "ck_home_memberships_role",
        "home_memberships",
        "role IN ('owner', 'parent', 'child', 'caretaker', 'member')",
    )
