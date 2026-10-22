"""015 — Director Learning Loop tables (Phase 27, W1).

Creates the six Phase 27 tables:

* ``director_decisions`` — the persisted LiveDirector decision journal.
* ``director_suggestion_fates`` — observed semi_auto suggestion fates
  (approved / rejected / expired / modified).
* ``director_shadow_divergences`` — manual-mode human-vs-AI-shadow
  divergences.
* ``director_decision_labels`` — labels (written by W2's labeler).
* ``director_learning_adjustments`` — learning adjustments (W3).
* ``director_learning_state`` — single-row learning state (W3);
  ``learning_mode`` defaults to ``'suggest'``.

Inspector-guarded like 014: a no-op where a table already exists
(e.g. created via metadata create_all). JSON columns are JSONB on
Postgres, matching the ORM models.

Revision ID: 015_director_learning
Revises: 014_anti_cheat_flags
"""

revision = "015_director_learning"
down_revision = "014_anti_cheat_flags"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402


def _audit_columns():
    """Base-model audit columns (app/db/base.py Base), as in 014."""
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


def _jsonb(name, nullable=True, default=None):
    col = sa.Column(
        name, postgresql.JSONB(astext_type=sa.Text()), nullable=nullable
    )
    return col


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("director_decisions"):
        op.create_table(
            "director_decisions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("mode", sa.String(length=16), nullable=True),
            sa.Column("phase", sa.String(length=32), nullable=True),
            sa.Column("trigger", sa.String(length=64), nullable=True),
            sa.Column("outcome", sa.String(length=32), nullable=True),
            sa.Column("rule", sa.String(length=96), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("margin_required", sa.Float(), nullable=True),
            sa.Column("margin_actual", sa.Float(), nullable=True),
            _jsonb("incumbent"),
            _jsonb("challenger"),
            _jsonb("candidates"),
            sa.Column("suggestion_id", sa.String(length=64), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_director_decisions_ts", "director_decisions", ["ts"])
        op.create_index(
            "ix_director_decisions_mode", "director_decisions", ["mode"]
        )
        op.create_index(
            "ix_director_decisions_outcome", "director_decisions", ["outcome"]
        )
        op.create_index(
            "ix_director_decisions_suggestion_id",
            "director_decisions",
            ["suggestion_id"],
        )

    if not inspector.has_table("director_suggestion_fates"):
        op.create_table(
            "director_suggestion_fates",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("suggestion_id", sa.String(length=64), nullable=False),
            sa.Column("decision_id", sa.String(length=36), nullable=True),
            sa.Column("fate", sa.String(length=16), nullable=False),
            sa.Column("decided_by", sa.String(length=100), nullable=True),
            _jsonb("chosen_alternative"),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_director_suggestion_fates_suggestion_id",
            "director_suggestion_fates",
            ["suggestion_id"],
        )
        op.create_index(
            "ix_director_suggestion_fates_fate",
            "director_suggestion_fates",
            ["fate"],
        )

    if not inspector.has_table("director_shadow_divergences"):
        op.create_table(
            "director_shadow_divergences",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("phase", sa.String(length=32), nullable=True),
            _jsonb("human_action"),
            _jsonb("ai_candidate"),
            sa.Column(
                "shadow_decision_id", sa.String(length=36), nullable=True
            ),
            _jsonb("detail"),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_director_shadow_divergences_ts",
            "director_shadow_divergences",
            ["ts"],
        )
        op.create_index(
            "ix_director_shadow_divergences_kind",
            "director_shadow_divergences",
            ["kind"],
        )
        op.create_index(
            "ix_director_shadow_divergences_shadow_decision_id",
            "director_shadow_divergences",
            ["shadow_decision_id"],
        )

    if not inspector.has_table("director_decision_labels"):
        op.create_table(
            "director_decision_labels",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("decision_id", sa.String(length=36), nullable=False),
            sa.Column("label", sa.String(length=64), nullable=False),
            sa.Column(
                "confidence", sa.Float(), nullable=False, server_default="0.0"
            ),
            sa.Column(
                "confidence_band",
                sa.String(length=8),
                nullable=False,
                server_default="low",
            ),
            _jsonb("evidence", nullable=False),
            sa.Column("labeler_version", sa.String(length=32), nullable=False),
            sa.Column("labeled_at", sa.DateTime(timezone=True), nullable=False),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_director_decision_labels_decision_id",
            "director_decision_labels",
            ["decision_id"],
        )
        op.create_index(
            "ix_director_decision_labels_label",
            "director_decision_labels",
            ["label"],
        )

    if not inspector.has_table("director_learning_adjustments"):
        op.create_table(
            "director_learning_adjustments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("knob", sa.String(length=64), nullable=False),
            _jsonb("bucket", nullable=False),
            sa.Column("before_value", sa.Float(), nullable=False),
            sa.Column("after_value", sa.Float(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            _jsonb("evidence", nullable=False),
            sa.Column("learning_mode", sa.String(length=16), nullable=False),
            sa.Column("applied_by", sa.String(length=100), nullable=True),
            sa.Column("parent_id", sa.String(length=36), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_director_learning_adjustments_ts",
            "director_learning_adjustments",
            ["ts"],
        )
        op.create_index(
            "ix_director_learning_adjustments_status",
            "director_learning_adjustments",
            ["status"],
        )
        op.create_index(
            "ix_director_learning_adjustments_knob",
            "director_learning_adjustments",
            ["knob"],
        )

    if not inspector.has_table("director_learning_state"):
        op.create_table(
            "director_learning_state",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column(
                "learning_mode",
                sa.String(length=16),
                nullable=False,
                server_default="suggest",
            ),
            sa.Column("updated_by", sa.String(length=100), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in (
        "director_learning_state",
        "director_learning_adjustments",
        "director_decision_labels",
        "director_shadow_divergences",
        "director_suggestion_fates",
        "director_decisions",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
