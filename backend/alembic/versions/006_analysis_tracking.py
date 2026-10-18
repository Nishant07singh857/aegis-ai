"""006 — drop_events and zone_phases tables (Phase 11 analysis tracking).

Creates the two new tables consumed by ``app/analysis``:
  * ``drop_events`` — one team's landing per match, with contested-by
    team list and nullable drop-survival outcome.
  * ``zone_phases`` — the zone circle (centre + radius) per phase per match.

``rotation_events`` is NOT touched here — the table already exists from
the initial schema and Phase 11 reuses it.

Revision ID: 006_analysis_tracking
Revises: 005_add_user_role
"""

revision = "006_analysis_tracking"
down_revision = "005_add_user_role"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "drop_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("match_id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=False),
        sa.Column("map_name", sa.String(length=32), nullable=True),
        sa.Column("location_x", sa.Float(), nullable=True),
        sa.Column("location_y", sa.Float(), nullable=True),
        sa.Column("named_location", sa.String(length=128), nullable=True),
        sa.Column("timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("contested_by", postgresql.JSONB(), nullable=False),
        sa.Column("survived_drop", sa.Boolean(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_drop_events_match", "drop_events", ["match_id"]
    )
    op.create_index(
        "ix_drop_events_match_team", "drop_events", ["match_id", "team_id"]
    )

    op.create_table(
        "zone_phases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("match_id", sa.String(length=36), nullable=False),
        sa.Column("phase_number", sa.Integer(), nullable=False),
        sa.Column("center_x", sa.Float(), nullable=False),
        sa.Column("center_y", sa.Float(), nullable=False),
        sa.Column("radius", sa.Float(), nullable=False),
        sa.Column("timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # one circle per phase per match
    op.create_index(
        "ix_zone_phases_match_phase",
        "zone_phases",
        ["match_id", "phase_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_zone_phases_match_phase", table_name="zone_phases")
    op.drop_table("zone_phases")
    op.drop_index("ix_drop_events_match_team", table_name="drop_events")
    op.drop_index("ix_drop_events_match", table_name="drop_events")
    op.drop_table("drop_events")
