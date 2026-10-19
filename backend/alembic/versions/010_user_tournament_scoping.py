"""010 — tournament/team scoping columns on the user table (Phase 16).

Adds ``user.tournament_id`` (nullable FK → tournaments.id, null = global
account), ``user.team_id`` (nullable FK → teams.id; required for observer
accounts) and ``user.created_by`` (username of the creating admin).
Both FKs are ON DELETE SET NULL so deleting a tournament/team never
orphans or blocks on user rows — scoping degrades to global/unassigned.

Revision ID: 010_user_tournament_scoping
Revises: 009_master_data_fields
"""

revision = "010_user_tournament_scoping"
down_revision = "009_master_data_fields"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("tournament_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("team_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("created_by", sa.String(length=100), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_tournament_id",
        "user",
        "tournaments",
        ["tournament_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_user_team_id",
        "user",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_user_tournament_id", "user", ["tournament_id"], unique=False)
    op.create_index("ix_user_team_id", "user", ["team_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_team_id", table_name="user")
    op.drop_index("ix_user_tournament_id", table_name="user")
    op.drop_constraint("fk_user_team_id", "user", type_="foreignkey")
    op.drop_constraint("fk_user_tournament_id", "user", type_="foreignkey")
    op.drop_column("user", "created_by")
    op.drop_column("user", "team_id")
    op.drop_column("user", "tournament_id")
