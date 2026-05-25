"""Add position table (option contracts the user holds).

Revision ID: 0004_position
Revises: 0003_analyst_levels
Create Date: 2026-05-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_position"
down_revision: Union[str, None] = "0003_analyst_levels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "position",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("contract_type", sa.String(length=4), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contracts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("entry_premium", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_position_ticker", "position", ["ticker"])
    op.create_index("ix_position_created_at", "position", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_position_created_at", table_name="position")
    op.drop_index("ix_position_ticker", table_name="position")
    op.drop_table("position")
