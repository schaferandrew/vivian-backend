"""Create chat and chat_message tables.

Revision ID: 20260208_0001
Revises:
Create Date: 2026-02-08 13:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260208_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    table_names = set(inspector.get_table_names())
    index_names = {index["name"] for index in inspector.get_indexes("chats")} if "chats" in table_names else set()

    if "chats" not in table_names:
        op.create_table(
            "chats",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("model", sa.String(length=100), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        table_names.add("chats")
        index_names = set()

    chats_user_idx = op.f("ix_chats_user_id")
    if "chats" in table_names and chats_user_idx not in index_names:
        op.create_index(chats_user_idx, "chats", ["user_id"], unique=False)

    table_names = set(inspector.get_table_names())
    msg_index_names = (
        {index["name"] for index in inspector.get_indexes("chat_messages")}
        if "chat_messages" in table_names
        else set()
    )

    if "chat_messages" not in table_names:
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("chat_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        table_names.add("chat_messages")
        msg_index_names = set()

    chat_messages_idx = op.f("ix_chat_messages_chat_id")
    if "chat_messages" in table_names and chat_messages_idx not in msg_index_names:
        op.create_index(
            chat_messages_idx,
            "chat_messages",
            ["chat_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "chat_messages" in table_names:
        msg_index_names = {index["name"] for index in inspector.get_indexes("chat_messages")}
        chat_messages_idx = op.f("ix_chat_messages_chat_id")
        if chat_messages_idx in msg_index_names:
            op.drop_index(chat_messages_idx, table_name="chat_messages")
        op.drop_table("chat_messages")

    table_names = set(inspector.get_table_names())
    if "chats" in table_names:
        chats_index_names = {index["name"] for index in inspector.get_indexes("chats")}
        chats_user_idx = op.f("ix_chats_user_id")
        if chats_user_idx in chats_index_names:
            op.drop_index(chats_user_idx, table_name="chats")
        op.drop_table("chats")
