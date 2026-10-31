"""017 — match stats tables (Phase 35, W4 / M-8).

Creates ``match_team_stats`` and ``match_player_stats`` — the canonical
per-match result tables owned by ``app/services/match_service.py``
(``MatchTeamStats`` / ``MatchPlayerStats``). Until this revision no
migration created them: they existed only via
``Base.metadata.create_all`` / ``init_db``, so a fresh
``alembic upgrade head`` never created them (verified 2026-10-27: a
scratch DB upgraded to 016 has neither table, and migrations 012/013
only ever ALTER them when already present — they are no-ops when the
tables are absent).

The schema below is diffed against the SQLAlchemy models, not guessed
(measured from the models' ``create_all`` DDL on sqlite, 2026-10-27):

* columns, order, types, lengths, nullability and PKs match the models
  exactly, including the ``Base`` audit columns (``created_at`` /
  ``updated_at`` / ``is_deleted`` / ``deleted_at``);
* ``Mapped[int]`` (non-Optional) columns are NOT NULL, while
  ``total_damage`` / ``damage_dealt`` (``Mapped[Optional[int]]``) are
  nullable — NULL means the feed never delivered real damage, never an
  estimate (Phase 21 honesty rule, migration 013);
* the models declare NO foreign keys and NO secondary indexes, so this
  migration declares none (``match_id`` / ``team_id`` / ``player_id``
  are plain strings, exactly as the models define them);
* ``created_at`` / ``updated_at`` use ``func.now()`` as their server
  default — the same expression the models declare — which compiles to
  ``now()`` on Postgres and ``CURRENT_TIMESTAMP`` on sqlite;
* ``is_deleted`` has NO server default, matching the models (the
  ``default=False`` is Python-side only, as in ``create_all``);
* the 012 columns (``healing``, ``grenades_used``) and the 013 column
  (``clutches``) plus the nullable damage columns are included in the
  CREATE, because 012/013 are no-ops on a fresh database.

Inspector-guarded like 015/016: a no-op where a table already exists
(e.g. created via ``create_all``), so existing deployments are
untouched. A table that exists with a SUBSET of columns is left as-is
(the 012/013 column-add path already covers that shape); only a wholly
absent table is created.

Revision ID: 017_match_stats_tables
Revises: 016_pov_pilot
"""

revision = "017_match_stats_tables"
down_revision = "016_pov_pilot"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def _audit_columns():
    """Base-model audit columns (app/db/base.py Base), as in 015/016.

    ``func.now()`` (not a literal ``now()`` string) so the default
    compiles correctly on both Postgres and sqlite, exactly as
    ``create_all`` renders it from the models.
    """
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("match_team_stats"):
        op.create_table(
            "match_team_stats",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("match_id", sa.String(length=36), nullable=False),
            sa.Column("team_id", sa.String(length=64), nullable=False),
            sa.Column("placement", sa.Integer(), nullable=False),
            sa.Column("total_kills", sa.Integer(), nullable=False),
            sa.Column("total_damage", sa.Integer(), nullable=True),
            sa.Column("total_points", sa.Integer(), nullable=False),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id", name="pk_match_team_stats"),
        )

    if not inspector.has_table("match_player_stats"):
        op.create_table(
            "match_player_stats",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("match_id", sa.String(length=36), nullable=False),
            sa.Column("player_id", sa.String(length=64), nullable=False),
            sa.Column("team_id", sa.String(length=64), nullable=False),
            sa.Column("kills", sa.Integer(), nullable=False),
            sa.Column("damage_dealt", sa.Integer(), nullable=True),
            sa.Column("headshots", sa.Integer(), nullable=False),
            sa.Column("revives_performed", sa.Integer(), nullable=False),
            sa.Column("healing", sa.Integer(), nullable=False),
            sa.Column("grenades_used", sa.Integer(), nullable=False),
            sa.Column("survived_time_seconds", sa.Integer(), nullable=False),
            sa.Column("clutches", sa.Integer(), nullable=False),
            sa.Column("mvp_score", sa.Float(), nullable=False),
            sa.Column("is_mvp", sa.Boolean(), nullable=False),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id", name="pk_match_player_stats"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("match_player_stats"):
        op.drop_table("match_player_stats")
    if inspector.has_table("match_team_stats"):
        op.drop_table("match_team_stats")
