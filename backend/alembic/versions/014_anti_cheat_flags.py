"""014 — Anti-cheat review flags (Phase 22).

Adds ``anti_cheat_flags``: persisted flags from the vision-derivable
anti-cheat checks (impossible position jumps, kill-feed rate anomalies,
alive-count inconsistencies), each with its evidence payload, for the
admin/director review UI. Flags are review items only — nothing in this
table bans or penalises a player automatically.

Inspector-guarded like 012/013: a no-op where the table already exists
(e.g. created via metadata create_all).

Revision ID: 014_anti_cheat_flags
Revises: 013_mvp_unification
"""

revision = "014_anti_cheat_flags"
down_revision = "013_mvp_unification"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("anti_cheat_flags"):
        return
    op.create_table(
        "anti_cheat_flags",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("match_id", sa.String(length=64), nullable=True),
        sa.Column("tournament_id", sa.String(length=36), nullable=True),
        sa.Column("check_name", sa.String(length=64), nullable=False),
        sa.Column("cheat_type", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=64), nullable=True),
        sa.Column("player_name", sa.String(length=128), nullable=True),
        sa.Column("team_id", sa.String(length=36), nullable=True),
        sa.Column("team_name", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_at_utc", sa.DateTime(timezone=True), nullable=False),
        # Base-model audit columns (app/db/base.py Base).
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_anti_cheat_flags_match_id", "anti_cheat_flags", ["match_id"]
    )
    op.create_index(
        "ix_anti_cheat_flags_check_name", "anti_cheat_flags", ["check_name"]
    )
    op.create_index(
        "ix_anti_cheat_flags_team_id", "anti_cheat_flags", ["team_id"]
    )
    op.create_index(
        "ix_anti_cheat_flags_status", "anti_cheat_flags", ["status"]
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("anti_cheat_flags"):
        op.drop_table("anti_cheat_flags")
