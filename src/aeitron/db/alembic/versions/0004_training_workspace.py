"""Training workspace control plane.

Revision ID: 0004_training_workspace
Revises: 0003_task_retry
"""

from __future__ import annotations

from pathlib import Path

from alembic import op


revision = "0004_training_workspace"
down_revision = "0003_task_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path("src/aeitron/db/migrations/0004_training_workspace.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("Aeitron production migrations are forward-only")
