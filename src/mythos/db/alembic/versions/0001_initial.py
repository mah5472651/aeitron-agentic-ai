"""Initial Mythos schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-10
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from src.mythos.db.migration_runner import expand_psql_includes


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path("src/mythos/db/migrations/0001_initial.sql")
    op.execute(expand_psql_includes(sql_path.read_text(encoding="utf-8"), base_dir=Path.cwd()))


def downgrade() -> None:
    raise RuntimeError("Mythos production migrations are forward-only")
