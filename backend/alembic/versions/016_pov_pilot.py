"""016 — Observer Auto-Pilot POV journal table (Phase 29, W4).

Creates ``pov_pilot_decisions`` — the durable POV pilot journal:
executed taps (with TapResult verification), semi_auto suggestions
and their fates, manual/clone-grabbed shadow decisions, manual
overrides and failures. See
``app/services/observer_pilot/models.py`` for why this is a sibling
table and not rows inside the director-learning journal.

Inspector-guarded like 015: a no-op where the table already exists
(e.g. created via metadata create_all). JSON columns are JSONB on
Postgres, matching the ORM model.

Revision ID: 016_pov_pilot
Revises: 015_director_learning
"""

revision = "016_pov_pilot"
down_revision = "015_director_learning"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402


def _audit_columns():
    """Base-model audit columns (app/db/base.py Base), as in 015."""
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("pov_pilot_decisions"):
        op.create_table(
            "pov_pilot_decisions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("slot_number", sa.Integer(), nullable=False),
            sa.Column("source_id", sa.String(length=128), nullable=True),
            sa.Column("team_id", sa.String(length=64), nullable=True),
            sa.Column("from_index", sa.Integer(), nullable=True),
            sa.Column("to_index", sa.Integer(), nullable=True),
            sa.Column("rule", sa.String(length=64), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column(
                "evidence", postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column("mode", sa.String(length=16), nullable=True),
            sa.Column("fate", sa.String(length=16), nullable=False),
            sa.Column("suggestion_id", sa.String(length=64), nullable=True),
            sa.Column("verification", sa.String(length=32), nullable=True),
            sa.Column("taps_sent", sa.Integer(), nullable=True),
            sa.Column("decided_by", sa.String(length=100), nullable=True),
            sa.Column("decided_at_epoch", sa.Float(), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_pov_pilot_decisions_ts", "pov_pilot_decisions", ["ts"]
        )
        op.create_index(
            "ix_pov_pilot_decisions_slot_number",
            "pov_pilot_decisions",
            ["slot_number"],
        )
        op.create_index(
            "ix_pov_pilot_decisions_mode", "pov_pilot_decisions", ["mode"]
        )
        op.create_index(
            "ix_pov_pilot_decisions_fate", "pov_pilot_decisions", ["fate"]
        )
        op.create_index(
            "ix_pov_pilot_decisions_suggestion_id",
            "pov_pilot_decisions",
            ["suggestion_id"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("pov_pilot_decisions"):
        op.drop_table("pov_pilot_decisions")
