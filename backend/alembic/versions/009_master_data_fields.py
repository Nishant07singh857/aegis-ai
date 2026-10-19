"""009 — master-data fields (Phase 13 / W1).

- teams: add ``region`` (VARCHAR(100), nullable) and ``is_active``
  (BOOLEAN, NOT NULL, default TRUE).
- players: add ``is_active`` (BOOLEAN, NOT NULL, default TRUE) and relax
  ``team_id`` to NULLABLE so players can exist unassigned (the admin UI
  supports an "unassigned" roster).

Revision ID: 009_master_data_fields
Revises: 008_observer_slots
"""

revision = "009_master_data_fields"
down_revision = "008_observer_slots"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.add_column("teams", sa.Column("region", sa.String(length=100), nullable=True))
    op.add_column(
        "teams",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "players",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.alter_column("players", "team_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    op.alter_column("players", "team_id", existing_type=sa.String(length=36), nullable=False)
    op.drop_column("players", "is_active")
    op.drop_column("teams", "is_active")
    op.drop_column("teams", "region")
