"""001 — Initial schema: all 17 tables.

Generated from ``app.db.base.Base.metadata`` (see /tmp/gen001.py); do not
hand-edit the op.* blocks — regenerate them instead.

create_all vs. alembic story
----------------------------
* ``alembic upgrade head`` is the versioned deploy path: it applies this
  revision (and every later one) through real ``op.*`` migrations.
* ``app.db.base.init_db()`` (``Base.metadata.create_all`` with
  ``checkfirst=True``) remains as the idempotent local-dev bootstrap: it
  creates any missing tables plus the ``uuid-ossp``/``pgcrypto``/``pg_trgm``
  extensions. Both derive from the same ``Base.metadata``, so they agree on
  the schema; running ``init_db()`` after ``upgrade head`` is a no-op.

Revision ID: 001_initial_schema
Revises: None (base revision)
"""

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402


def upgrade():
    op.create_table('audit_log',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('user_id', sa.String(length=36)),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=False),
        sa.Column('resource_id', sa.String(length=36)),
        sa.Column('details', postgresql.JSONB(), nullable=False),
        sa.Column('ip_address', sa.String(length=45)),
        sa.Column('user_agent', sa.String(length=500)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_audit_action_time', 'audit_log', ['action', 'created_at'])
    op.create_index('ix_audit_log_action', 'audit_log', ['action'])
    op.create_index('ix_audit_user', 'audit_log', ['user_id', 'created_at'])

    op.create_table('teams',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False, unique=True),
        sa.Column('tag', sa.String(length=32)),
        sa.Column('logo_url', sa.String(length=500)),
        sa.Column('colors', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
    )

    op.create_table('tournaments',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('slug', sa.String(length=200), nullable=False, unique=True),
        sa.Column('description', sa.Text()),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=True)),
        sa.Column('end_date', sa.DateTime(timezone=True)),
        sa.Column('total_matches', sa.Integer(), nullable=False),
        sa.Column('points_per_kill', sa.Integer(), nullable=False),
        sa.Column('placement_points', postgresql.JSONB(), nullable=False),
        sa.Column('rules', postgresql.JSONB(), nullable=False),
        sa.Column('prize_pool', sa.String(length=100)),
        sa.Column('organizer', sa.String(length=200)),
        sa.Column('is_public', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
    )

    op.create_table('user',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False, unique=True),
        sa.Column('username', sa.String(length=100), nullable=False, unique=True),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=200), nullable=False),
        sa.Column('avatar_url', sa.String(length=500)),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('is_superuser', sa.Boolean(), nullable=False),
        sa.Column('is_verified', sa.Boolean(), nullable=False),
        sa.Column('email_verified_at', sa.DateTime(timezone=True)),
        sa.Column('last_login_at', sa.DateTime(timezone=True)),
        sa.Column('last_login_ip', sa.String(length=45)),
        sa.Column('failed_login_attempts', sa.Integer(), nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True)),
        sa.Column('bio', sa.Text()),
        sa.Column('timezone', sa.String(length=50), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_user_email', 'user', ['email'], unique=True)
    op.create_index('ix_user_email_active', 'user', ['email', 'is_active'])
    op.create_index('ix_user_username', 'user', ['username'], unique=True)
    op.create_index('ix_user_username_active', 'user', ['username', 'is_active'])

    op.create_table('api_key',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('key_hash', sa.String(length=255), nullable=False, unique=True),
        sa.Column('key_prefix', sa.String(length=10), nullable=False),
        sa.Column('scopes', postgresql.JSONB(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True)),
        sa.Column('last_used_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='fk_api_key_user_id_user'),
    )
    op.create_index('ix_api_key_hash', 'api_key', ['key_hash'])
    op.create_index('ix_api_key_user', 'api_key', ['user_id', 'is_active'])

    op.create_table('matches',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('tournament_id', sa.String(length=36), nullable=False),
        sa.Column('match_number', sa.Integer(), nullable=False),
        sa.Column('map_name', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('scheduled_time', sa.DateTime(timezone=True)),
        sa.Column('actual_start_time', sa.DateTime(timezone=True)),
        sa.Column('actual_end_time', sa.DateTime(timezone=True)),
        sa.Column('duration_seconds', sa.Integer()),
        sa.Column('custom_room_id', sa.String(length=50)),
        sa.Column('custom_room_password', sa.String(length=50)),
        sa.Column('meta', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['tournament_id'], ['tournaments.id'], name='fk_matches_tournament_id_tournaments'),
    )

    op.create_table('players',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('team_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('ign', sa.String(length=50), nullable=False),
        sa.Column('role', sa.String(length=50)),
        sa.Column('avatar_url', sa.String(length=500)),
        sa.Column('playstyle', postgresql.JSONB(), nullable=False),
        sa.Column('preferred_weapons', postgresql.JSONB(), nullable=False),
        sa.Column('clutch_score', sa.Float()),
        sa.Column('avg_damage', sa.Float()),
        sa.Column('avg_kills', sa.Float()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name='fk_players_team_id_teams'),
    )

    op.create_table('team_intelligence',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('team_id', sa.String(length=36), nullable=False),
        sa.Column('updated_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('aggression_score', sa.Float()),
        sa.Column('chemistry_score', sa.Float()),
        sa.Column('profile', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name='fk_team_intelligence_team_id_teams'),
    )

    op.create_table('broadcast_sessions',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36)),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('started_at_utc', sa.DateTime(timezone=True)),
        sa.Column('ended_at_utc', sa.DateTime(timezone=True)),
        sa.Column('obs_state', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_broadcast_sessions_match_id_matches'),
    )

    op.create_table('commentary',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36), nullable=False),
        sa.Column('timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(length=32), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('meta', postgresql.JSONB(), nullable=False),
        sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_commentary_match_id_matches'),
    )

    op.create_table('fight_events',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36), nullable=False),
        sa.Column('start_timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('end_timestamp_ms', sa.Integer()),
        sa.Column('team_ids', postgresql.JSONB(), nullable=False),
        sa.Column('player_ids', postgresql.JSONB(), nullable=False),
        sa.Column('location_x', sa.Float()),
        sa.Column('location_y', sa.Float()),
        sa.Column('map_name', sa.String(length=32)),
        sa.Column('intensity_score', sa.Float(), nullable=False),
        sa.Column('winner_team_id', sa.String(length=36)),
        sa.Column('total_kills', sa.Integer(), nullable=False),
        sa.Column('total_knocks', sa.Integer(), nullable=False),
        sa.Column('utility_used', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_fight_events_match_id_matches'),
    )

    op.create_table('highlights',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=200)),
        sa.Column('description', sa.Text()),
        sa.Column('start_timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('end_timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('video_path', sa.String(length=500)),
        sa.Column('meta', postgresql.JSONB(), nullable=False),
        sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_highlights_match_id_matches'),
    )

    op.create_table('kill_events',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36), nullable=False),
        sa.Column('timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('killer_player_id', sa.String(length=36), nullable=False),
        sa.Column('victim_player_id', sa.String(length=36), nullable=False),
        sa.Column('meta', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_kill_events_match_id_matches'),
    )

    op.create_table('match_events',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('real_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_data', postgresql.JSONB(), nullable=False),
        sa.Column('team_id', sa.String(length=36)),
        sa.Column('player_id', sa.String(length=36)),
        sa.Column('target_team_id', sa.String(length=36)),
        sa.Column('target_player_id', sa.String(length=36)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_match_events_match_id_matches'),
    )

    op.create_table('predictions',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36), nullable=False),
        sa.Column('timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('prediction_type', sa.String(length=64), nullable=False),
        sa.Column('subject_id', sa.String(length=36)),
        sa.Column('predicted_value', postgresql.JSONB(), nullable=False),
        sa.Column('confidence', sa.Float()),
        sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_predictions_match_id_matches'),
    )

    op.create_table('rotation_events',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36), nullable=False),
        sa.Column('timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.String(length=36), nullable=False),
        sa.Column('meta', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_rotation_events_match_id_matches'),
    )

    op.create_table('utility_events',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('match_id', sa.String(length=36), nullable=False),
        sa.Column('timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('utility_type', sa.String(length=64), nullable=False),
        sa.Column('player_id', sa.String(length=36), nullable=False),
        sa.Column('meta', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], name='fk_utility_events_match_id_matches'),
    )



def downgrade():
    op.drop_table('utility_events')
    op.drop_table('rotation_events')
    op.drop_table('predictions')
    op.drop_table('match_events')
    op.drop_table('kill_events')
    op.drop_table('highlights')
    op.drop_table('fight_events')
    op.drop_table('commentary')
    op.drop_table('broadcast_sessions')
    op.drop_table('team_intelligence')
    op.drop_table('players')
    op.drop_table('matches')
    op.drop_table('api_key')
    op.drop_table('user')
    op.drop_table('tournaments')
    op.drop_table('teams')
    op.drop_table('audit_log')
