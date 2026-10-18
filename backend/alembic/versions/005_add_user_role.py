"""005 — Add role column to the user table (Phase 6 RBAC).

Adds ``user.role`` (``admin`` | ``operator`` | ``viewer``) with a
server-side default of ``'viewer'`` so every pre-existing row gets the
least-privileged role on upgrade. Downgrade drops the column.

The column is a plain VARCHAR(20), not a native postgres ENUM, so the
migration runs unchanged on postgres and sqlite.

Revision ID: 005_add_user_role
Revises: 001_initial_schema
"""

revision = "005_add_user_role"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    # server_default backfills existing rows; the Python model default
    # (UserRole.VIEWER) covers ORM inserts. The default is kept at the DB
    # level so raw SQL inserts also land on the safe value.
    op.add_column(
        "user",
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
            server_default="viewer",
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "role")
