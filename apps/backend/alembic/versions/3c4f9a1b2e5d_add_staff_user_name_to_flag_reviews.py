"""add staff_user_name to flag_reviews

Revision ID: 3c4f9a1b2e5d
Revises: 2b168f4b7153
Create Date: 2026-05-04 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '3c4f9a1b2e5d'
down_revision: Union[str, None] = '2b168f4b7153'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'flag_reviews',
        sa.Column('staff_user_name', sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('flag_reviews', 'staff_user_name')
