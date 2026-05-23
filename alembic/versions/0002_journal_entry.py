"""Add journal_entry table (saved trade ideas + tracked forward prices).

Revision ID: 0002_journal
Revises: 0001_baseline
Create Date: 2026-05-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_journal"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "journal_entry",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("contract_type", sa.String(length=4), nullable=False),
        sa.Column("strike", sa.Float(), nullable=True),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lean", sa.String(length=8), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("intraday_price", sa.Float(), nullable=True),
        sa.Column("next_open_price", sa.Float(), nullable=True),
        sa.Column("next_close_price", sa.Float(), nullable=True),
        sa.Column("day3_price", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="alert"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_journal_entry_ticker", "journal_entry", ["ticker"])
    op.create_index("ix_journal_entry_created_at", "journal_entry", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_journal_entry_created_at", table_name="journal_entry")
    op.drop_index("ix_journal_entry_ticker", table_name="journal_entry")
    op.drop_table("journal_entry")
