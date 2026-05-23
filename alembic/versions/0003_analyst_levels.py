"""Add analyst_levels table (trusted weekly support/resistance levels).

Revision ID: 0003_analyst_levels
Revises: 0002_journal
Create Date: 2026-05-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_analyst_levels"
down_revision: Union[str, None] = "0002_journal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analyst_levels",
        sa.Column("ticker", sa.String(length=16), primary_key=True),
        sa.Column("clb36", sa.Float(), nullable=True),
        sa.Column("weekly_cpl", sa.Float(), nullable=True),
        sa.Column("resistances", JSONB(), nullable=False, server_default="[]"),
        sa.Column("supports", JSONB(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("analyst_levels")
