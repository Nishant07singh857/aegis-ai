"""012 — player utility stats: healing + grenades (Phase 21 / T5b).

Adds ``healing`` and ``grenades_used`` to ``match_player_stats`` so the
per-player stat lines recorded via MatchService.record_player_stats can
carry the utility counters the agents already track
(utility_tracker.PlayerUtilityStats.healing_done / .grenades_used,
team_tracker player.grenades / .healing_done).

The table predates alembic coverage in some deployments (created via
metadata create_all), so both operations are inspector-guarded: the
migration is a no-op where the table or columns already exist.

Revision ID: 012_player_utility_stats
Revises: 011_stories
"""

revision = "012_player_utility_stats"
down_revision = "011_stories"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def _columns(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _columns("match_player_stats")
    if not existing:
        return  # table not created yet; model default will include them
    if "healing" not in existing:
        op.add_column(
            "match_player_stats",
            sa.Column("healing", sa.Integer(), nullable=False, server_default="0"),
        )
    if "grenades_used" not in existing:
        op.add_column(
            "match_player_stats",
            sa.Column("grenades_used", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    existing = _columns("match_player_stats")
    if "grenades_used" in existing:
        op.drop_column("match_player_stats", "grenades_used")
    if "healing" in existing:
        op.drop_column("match_player_stats", "healing")
