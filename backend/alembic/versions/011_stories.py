"""011 — AI storyteller tables (Phase 19).

Adds ``stories`` (generated/dismissed/triggered data stories with the
structured payload the animated-graphics frontend renders) and
``story_settings`` (per-tournament default template + animation style per
story type; tournament_id NULL = global default).

Revision ID: 011_stories
Revises: 010_user_tournament_scoping
"""

revision = "011_stories"
down_revision = "010_user_tournament_scoping"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "stories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("match_id", sa.String(length=36), nullable=True),
        sa.Column("tournament_id", sa.String(length=36), nullable=True),
        sa.Column("story_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("triggered_by", sa.String(length=100), nullable=True),
        sa.Column("generated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("triggered_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tournament_id"], ["tournaments.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stories_match_id", "stories", ["match_id"], unique=False)
    op.create_index(
        "ix_stories_tournament_id", "stories", ["tournament_id"], unique=False
    )
    op.create_index("ix_stories_story_type", "stories", ["story_type"], unique=False)
    op.create_index("ix_stories_status", "stories", ["status"], unique=False)

    op.create_table(
        "story_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tournament_id", sa.String(length=36), nullable=True),
        sa.Column("story_type", sa.String(length=64), nullable=False),
        sa.Column("template", sa.String(length=128), nullable=False),
        sa.Column("animation_style", sa.String(length=128), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tournament_id"], ["tournaments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_story_settings_lookup",
        "story_settings",
        ["tournament_id", "story_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_story_settings_lookup", table_name="story_settings")
    op.drop_table("story_settings")
    op.drop_index("ix_stories_status", table_name="stories")
    op.drop_index("ix_stories_story_type", table_name="stories")
    op.drop_index("ix_stories_tournament_id", table_name="stories")
    op.drop_index("ix_stories_match_id", table_name="stories")
    op.drop_table("stories")
