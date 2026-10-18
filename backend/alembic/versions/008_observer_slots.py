"""008 — observer_slots table (Phase 12 observer-team mapping).

One row per tournament observer slot (1..16). Slots are lazily seeded
by the observer service on first read; the migration only creates the
table. team_id is nullable with SET NULL so deleting a team frees the
slot instead of failing.

Revision ID: 008_observer_slots
Revises: 007_event_store_and_indexes
"""

revision = "008_observer_slots"
down_revision = "007_event_store_and_indexes"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "observer_slots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observer_slots_slot_number",
        "observer_slots",
        ["slot_number"],
        unique=True,
    )
    op.create_index(
        "ix_observer_slots_team_id",
        "observer_slots",
        ["team_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_observer_slots_team_id", table_name="observer_slots")
    op.drop_index("ix_observer_slots_slot_number", table_name="observer_slots")
    op.drop_table("observer_slots")
