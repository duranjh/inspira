"""Merge tier_usage / next_steps branch into byok-terms head (#090).

Revision ID: 20260429_0001
Revises: 3e31dfcdb483, 20260428_0002
Create Date: 2026-04-29

Context
-------

Item 1's migration ``20260428_0001_tier_usage_and_business_plan_caps``
set ``down_revision = "20260424_0002"`` instead of the existing
mergepoint ``3e31dfcdb483``. That created a parallel branch from
before the byok+terms merge — surfaced at deploy time on Item 2 part
4/4 (commit ``8160986``) when ``alembic upgrade head`` errored with
"Multiple head revisions are present".

Why prod stayed healthy through Item 1
--------------------------------------

The store-layer retrofit ``_ensure_tier_usage_tables`` in
``store.py:_initialize`` creates the ``tier_usage`` + ``business_plan_usage``
tables on app boot via ``CREATE TABLE IF NOT EXISTS``. So production
got the tables it needed, but ``alembic_version`` stayed pointing at
``3e31dfcdb483`` because the orphan migration was never reachable
from there.

This migration
--------------

Empty merge: joins both heads into a single new head so future
migrations chain cleanly. Same shape as ``3e31dfcdb483_merge_byok_and_terms_branches.py``
(the precedent that merged the byok and terms branches a week ago).

Idempotency on prod apply
-------------------------

Applying this migration runs the chain
``3e31dfcdb483 → 20260428_0001 → 20260428_0002 → 20260429_0001``.
Both ``20260428_0001`` (tier_usage / business_plan_usage) and
``20260428_0002`` (next_steps_artifacts) use ``CREATE TABLE IF NOT
EXISTS`` on the Postgres branch. The retrofit-created tier_usage
tables already exist, so re-creating them is a no-op. The
next_steps_artifacts table is brand new and will be created cleanly
either by alembic (if migration runs first) or by its own retrofit
``_ensure_next_steps_artifacts_table`` (whichever boots the app first).

No schema change here — purely a graph-topology fix.
"""
from __future__ import annotations

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "20260429_0001"
down_revision: Union[str, Sequence[str], None] = (
    "3e31dfcdb483",
    "20260428_0002",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op merge migration."""


def downgrade() -> None:
    """No-op merge migration."""
