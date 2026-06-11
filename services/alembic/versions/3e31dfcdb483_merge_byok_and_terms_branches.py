"""merge BYOK and terms branches

Revision ID: 3e31dfcdb483
Revises: 20260422_0007, 20260424_0002
Create Date: 2026-04-25 03:46:26.711366+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e31dfcdb483'
down_revision: Union[str, Sequence[str], None] = ('20260422_0007', '20260424_0002')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
