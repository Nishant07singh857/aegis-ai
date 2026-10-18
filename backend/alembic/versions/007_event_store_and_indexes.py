"""007 — event_store table + indexes on event tables (Phase 12 audit fixes).

* ``event_store`` — durable log for ``EventBus._persist_event``
  (``app/db/models/event_store.py``). No foreign keys by design so
  event persistence can never fail on referential integrity.
* Indexes on the hot query paths of the existing event tables
  (``event_type``, ``match_id``, ``timestamp`` / ``team``), which the
  001 schema shipped without.

Revision ID: 007_event_store_and_indexes
Revises: 006_analysis_tracking
"""

revision = "007_event_store_and_indexes"
down_revision = "006_analysis_tracking"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    # ── event_store table ──────────────────────────────────────────
    op.create_table(
        "event_store",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_version", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("match_id", sa.String(length=36), nullable=True),
        sa.Column("tournament_id", sa.String(length=36), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_event_store_event_id"),
    )
    op.create_index("ix_event_store_type", "event_store", ["event_type"])
    op.create_index("ix_event_store_match", "event_store", ["match_id"])
    op.create_index(
        "ix_event_store_match_type_time",
        "event_store",
        ["match_id", "event_type", "timestamp_ms"],
    )
    op.create_index("ix_event_store_time", "event_store", ["timestamp_ms"])

    # ── indexes on existing event tables (hot query paths) ─────────
    op.create_index(
        "ix_kill_events_match_time", "kill_events", ["match_id", "timestamp_ms"]
    )
    op.create_index(
        "ix_fight_events_match_start", "fight_events", ["match_id", "start_timestamp_ms"]
    )
    op.create_index(
        "ix_match_events_match_type_time",
        "match_events",
        ["match_id", "event_type", "timestamp_ms"],
    )
    op.create_index("ix_match_events_type", "match_events", ["event_type"])
    op.create_index(
        "ix_rotation_events_match_team_time",
        "rotation_events",
        ["match_id", "team_id", "timestamp_ms"],
    )
    op.create_index(
        "ix_utility_events_match_type_time",
        "utility_events",
        ["match_id", "utility_type", "timestamp_ms"],
    )


def downgrade() -> None:
    op.drop_index("ix_utility_events_match_type_time", table_name="utility_events")
    op.drop_index("ix_rotation_events_match_team_time", table_name="rotation_events")
    op.drop_index("ix_match_events_type", table_name="match_events")
    op.drop_index("ix_match_events_match_type_time", table_name="match_events")
    op.drop_index("ix_fight_events_match_start", table_name="fight_events")
    op.drop_index("ix_kill_events_match_time", table_name="kill_events")

    op.drop_index("ix_event_store_time", table_name="event_store")
    op.drop_index("ix_event_store_match_type_time", table_name="event_store")
    op.drop_index("ix_event_store_match", table_name="event_store")
    op.drop_index("ix_event_store_type", table_name="event_store")
    op.drop_table("event_store")
