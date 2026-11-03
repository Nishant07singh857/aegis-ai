"""018 — revoked static API keys table (Phase 36, W2 / N-4).

Creates ``revoked_api_key`` — the durable half of static-key
revocation (see ``app/db/models/revoked_api_key.py``). Until this
revision revocation lived only in the in-memory set in
``app/api/deps.py`` (Phase 35, M-4): a process restart resurrected
every revoked key unless it had also been removed from
``ADMIN_API_KEYS``. From this revision the startup lifespan seeds
the in-memory cache from this table and every persistent revoke
writes a row here, so a revoked key stays revoked across restarts.

The schema below is diffed against the SQLAlchemy model, not
guessed (measured from the model's ``create_all`` DDL on sqlite):

* columns, order, types, lengths, nullability and PK match the
  model exactly, including the ``Base`` audit columns
  (``created_at`` / ``updated_at`` / ``is_deleted`` /
  ``deleted_at``);
* ``key_digest`` is the SHA-256 hex digest of the raw key (64
  chars) — the raw key is NEVER stored. It is UNIQUE (one row per
  revoked key; re-revoking is a no-op) and indexed;
* ``revoked_at`` uses ``func.now()`` as its server default — the
  same expression the model declares — which compiles to ``now()``
  on Postgres and ``CURRENT_TIMESTAMP`` on sqlite;
* ``is_deleted`` has NO server default, matching the model (the
  ``default=False`` is Python-side only, as in ``create_all``);
* no foreign keys: a revoked static key has no user row (the
  allowlist lives in settings, not in the DB).

Inspector-guarded like 015/016/017: a no-op where the table
already exists (e.g. created via ``create_all``), so existing
deployments are untouched.

Revision ID: 018_revoked_api_keys
Revises: 017_match_stats_tables
"""

revision = "018_revoked_api_keys"
down_revision = "017_match_stats_tables"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def _audit_columns():
    """Base-model audit columns (app/db/base.py Base), as in 015/016/017.

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

    if not inspector.has_table("revoked_api_key"):
        op.create_table(
            "revoked_api_key",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("key_digest", sa.String(length=64), nullable=False),
            sa.Column(
                "revoked_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("revoked_by", sa.String(length=128), nullable=True),
            sa.Column("reason", sa.String(length=500), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id", name="pk_revoked_api_key"),
            sa.UniqueConstraint("key_digest", name="uq_revoked_api_key_key_digest"),
        )
        op.create_index(
            "ix_revoked_api_key_digest", "revoked_api_key", ["key_digest"]
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("revoked_api_key"):
        op.drop_table("revoked_api_key")
