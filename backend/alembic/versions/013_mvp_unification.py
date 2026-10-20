"""013 — MVP unification + damage honesty (Phase 21 / W2).

* ``match_player_stats.clutches``: clutches WON per player per match —
  the ``clutches`` term of the single canonical PRD MVP formula
  (``mvp_calculator.compute_mvp_score``: kills*5 + damage*0.05
  + revives*3 + survival*0.02 + clutches*10 + headshots*2).
* ``match_player_stats.damage_dealt`` and
  ``match_team_stats.total_damage`` become NULLABLE: ``NULL`` means the
  feed/agent layer never delivered real damage, and the API surfaces
  ``null`` ("—") instead of 0 or an estimate. Existing rows keep their
  recorded values; only the constraint relaxes.

The stats tables predate full alembic coverage in some deployments
(created via metadata create_all), so every operation is
inspector-guarded, mirroring 012: the migration is a no-op where a
table/column is absent or already in the target shape.

Revision ID: 013_mvp_unification
Revises: 012_player_utility_stats
"""

revision = "013_mvp_unification"
down_revision = "012_player_utility_stats"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def _columns(table: str) -> dict:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return {}
    return {c["name"]: c for c in inspector.get_columns(table)}


def upgrade() -> None:
    player_cols = _columns("match_player_stats")
    if player_cols:
        if "clutches" not in player_cols:
            op.add_column(
                "match_player_stats",
                sa.Column(
                    "clutches",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            )
        damage_col = player_cols.get("damage_dealt")
        if damage_col is not None and not damage_col.get("nullable", True):
            op.alter_column(
                "match_player_stats",
                "damage_dealt",
                existing_type=sa.Integer(),
                nullable=True,
            )

    team_cols = _columns("match_team_stats")
    if team_cols:
        total_damage_col = team_cols.get("total_damage")
        if total_damage_col is not None and not total_damage_col.get(
            "nullable", True
        ):
            op.alter_column(
                "match_team_stats",
                "total_damage",
                existing_type=sa.Integer(),
                nullable=True,
            )


def downgrade() -> None:
    # Note: rows recorded with NULL damage cannot be restored to a
    # number honestly; downgrade backfills NULLs to 0 (documented data
    # loss of the "unknown" distinction) before re-tightening.
    player_cols = _columns("match_player_stats")
    if player_cols:
        if "damage_dealt" in player_cols:
            op.execute(
                "UPDATE match_player_stats SET damage_dealt = 0 "
                "WHERE damage_dealt IS NULL"
            )
            op.alter_column(
                "match_player_stats",
                "damage_dealt",
                existing_type=sa.Integer(),
                nullable=False,
            )
        if "clutches" in player_cols:
            op.drop_column("match_player_stats", "clutches")

    team_cols = _columns("match_team_stats")
    if team_cols and "total_damage" in team_cols:
        op.execute(
            "UPDATE match_team_stats SET total_damage = 0 "
            "WHERE total_damage IS NULL"
        )
        op.alter_column(
            "match_team_stats",
            "total_damage",
            existing_type=sa.Integer(),
            nullable=False,
        )
