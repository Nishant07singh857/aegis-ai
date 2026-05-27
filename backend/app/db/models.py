# backend/app/db/models.py
"""
AEGIS AI - Complete Database Models
All tables for tournament management, matches, teams, players,
events, fights, highlights, predictions, and more.
"""

import uuid
import enum
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Boolean,
    ForeignKey, Text, Enum as SQLEnum, Index,
    UniqueConstraint, CheckConstraint, BigInteger,
    ARRAY,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from .base import Base

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TournamentStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    LIVE = "live"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class TournamentFormat(str, enum.Enum):
    BATTLE_ROYALE = "battle_royale"
    SCRIM = "scrim"
    CUSTOM_ROOM = "custom_room"
    LEAGUE = "league"

class MatchStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    STARTING = "starting"
    LOBBY = "lobby"
    PLANE = "plane"
    LIVE = "live"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"

class MatchMap(str, enum.Enum):
    ERANGEL = "erangel"
    MIRAMAR = "miramar"
    SANHOK = "sanhok"
    VIKENDI = "vikendi"
    LIVIK = "livik"
    KARAKIN = "karakin"

class EventType(str, enum.Enum):
    MATCH_START = "match_start"
    MATCH_END = "match_end"
    PLANE_PATH = "plane_path"
    FIRST_BLOOD = "first_blood"
    KILL = "kill"
    KNOCK = "knock"
    REVIVE = "revive"
    DEATH = "death"
    TEAM_WIPE = "team_wipe"
    FIGHT_START = "fight_start"
    FIGHT_END = "fight_end"
    ZONE_CHANGE = "zone_change"
    ZONE_DAMAGE = "zone_damage"
    UTILITY_THROW = "utility_throw"
    UTILITY_DAMAGE = "utility_damage"
    HEALING_START = "healing_start"
    HEALING_COMPLETE = "healing_complete"
    VEHICLE_ENTER = "vehicle_enter"
    VEHICLE_EXIT = "vehicle_exit"
    VEHICLE_DESTROY = "vehicle_destroy"
    LOOT_PICKUP = "loot_pickup"
    AIRDROP_LAND = "airdrop_land"
    FLARE_GUN = "flare_gun"
    PLAYER_POSITION = "player_position"
    TEAM_POSITION = "team_position"

class FightIntensity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"
    CLUTCH = "clutch"

class PlayerRole(str, enum.Enum):
    IGL = "igl"           # In-Game Leader
    FRAGGER = "fragger"   # Entry Fragger
    SUPPORT = "support"   # Support Player
    SNIPER = "sniper"     # Designated Marksman
    ANCHOR = "anchor"     # Anchor/Defender
    FLEX = "flex"         # Flexible Role

class BroadcastMode(str, enum.Enum):
    FULL_AUTO = "full_auto"
    ASSISTED = "assisted"
    HYBRID = "hybrid"
    MANUAL = "manual"

class HighlightType(str, enum.Enum):
    SQUAD_WIPE = "squad_wipe"
    CLUTCH = "clutch"
    GRENADE_KILL = "grenade_kill"
    LONG_RANGE = "long_range"
    MULTI_KILL = "multi_kill"
    FINAL_CIRCLE = "final_circle"
    FUNNY_MOMENT = "funny_moment"
    COMEBACK = "comeback"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# USERS & AUTHENTICATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class User(Base):
    """System users - admins, organizers, casters, analysts"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(200), nullable=False)
    avatar_url = Column(String(500), nullable=True)
    
    # Account status
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    
    # Security
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_login_ip = Column(String(45), nullable=True)
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    
    # Profile
    bio = Column(Text, nullable=True)
    timezone = Column(String(50), default="UTC")
    language = Column(String(10), default="en")
    
    # Relationships
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    tournaments = relationship("Tournament", back_populates="created_by_user")
    
    __table_args__ = (
        Index("ix_user_email_active", "email", "is_active"),
        Index("ix_user_username_active", "username", "is_active"),
    )


class ApiKey(Base):
    """API keys for external integrations"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("user.id"), nullable=False)
    name = Column(String(200), nullable=False)
    key_hash = Column(String(255), nullable=False, unique=True)
    key_prefix = Column(String(10), nullable=False)  # First 8 chars for display
    scopes = Column(JSONB, default=list)
    
    is_active = Column(Boolean, default=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="api_keys")
    
    __table_args__ = (
        Index("ix_api_key_hash", "key_hash"),
        Index("ix_api_key_user", "user_id", "is_active"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOURNAMENTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Tournament(Base):
    """Tournament definition"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_by = Column(String(36), ForeignKey("user.id"), nullable=False)
    
    # Basic info
    name = Column(String(200), nullable=False)
    slug = Column(String(200), unique=True, nullable=False, index=True)
    short_name = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    
    # Status & format
    status = Column(SQLEnum(TournamentStatus), default=TournamentStatus.DRAFT, nullable=False, index=True)
    format = Column(SQLEnum(TournamentFormat), default=TournamentFormat.BATTLE_ROYALE, nullable=False)
    
    # Scheduling
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    registration_deadline = Column(DateTime(timezone=True), nullable=True)
    
    # Scoring rules
    points_per_kill = Column(Integer, default=1, nullable=False)
    placement_points = Column(JSONB, default=dict, nullable=False)
    bonus_points = Column(JSONB, default=dict)  # e.g., {"first_blood": 2}
    max_matches = Column(Integer, default=0)
    
    # Meta
    prize_pool = Column(String(100), nullable=True)
    organizer = Column(String(200), nullable=True)
    sponsors = Column(JSONB, default=list)
    rules_document_url = Column(String(500), nullable=True)
    banner_url = Column(String(500), nullable=True)
    
    # Settings
    is_public = Column(Boolean, default=True)
    requires_verification = Column(Boolean, default=False)
    max_teams = Column(Integer, default=32)
    min_teams = Column(Integer, default=8)
    
    # Metadata
    tags = Column(ARRAY(String), default=list)
    metadata = Column(JSONB, default=dict)
    
    # Relationships
    created_by_user = relationship("User", back_populates="tournaments")
    matches = relationship("Match", back_populates="tournament", cascade="all, delete-orphan",
                          order_by="Match.match_number")
    tournament_teams = relationship("TournamentTeam", back_populates="tournament", 
                                   cascade="all, delete-orphan")
    broadcast_sessions = relationship("BroadcastSession", back_populates="tournament",
                                     cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_tournament_status_dates", "status", "start_date", "end_date"),
        Index("ix_tournament_created_by", "created_by", "status"),
        CheckConstraint("end_date > start_date", name="ck_tournament_dates"),
    )


class TournamentTeam(Base):
    """Team registration in a tournament"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tournament_id = Column(String(36), ForeignKey("tournament.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(String(36), ForeignKey("team.id", ondelete="CASCADE"), nullable=False)
    
    # Registration
    registration_date = Column(DateTime(timezone=True), server_default=func.now())
    is_confirmed = Column(Boolean, default=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Tournament performance
    total_points = Column(Integer, default=0)
    total_kills = Column(Integer, default=0)
    total_damage = Column(BigInteger, default=0)
    matches_played = Column(Integer, default=0)
    wwcd_count = Column(Integer, default=0)  # Winner Winner Chicken Dinner
    top3_count = Column(Integer, default=0)
    top5_count = Column(Integer, default=0)
    avg_placement = Column(Float, default=0.0)
    
    # Current rank
    rank = Column(Integer, nullable=True)
    rank_change = Column(Integer, default=0)  # Position change
    
    # Seed/group
    seed = Column(Integer, nullable=True)
    group_name = Column(String(10), nullable=True)
    
    # Relationships
    tournament = relationship("Tournament", back_populates="tournament_teams")
    team = relationship("Team", back_populates="tournament_entries")
    
    __table_args__ = (
        UniqueConstraint("tournament_id", "team_id", name="uq_tournament_team"),
        Index("ix_tt_tournament", "tournament_id", "rank"),
        Index("ix_tt_team", "team_id", "total_points"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEAMS & PLAYERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Team(Base):
    """Esports team"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Basic info
    name = Column(String(100), nullable=False, index=True)
    short_name = Column(String(10), nullable=False, unique=True)
    tag = Column(String(5), nullable=True)
    logo_url = Column(String(500), nullable=True)
    banner_url = Column(String(500), nullable=True)
    
    # Organization
    organization = Column(String(200), nullable=True)
    country = Column(String(50), nullable=True, index=True)
    region = Column(String(50), nullable=True)
    founded_date = Column(DateTime(timezone=True), nullable=True)
    
    # Branding
    primary_color = Column(String(7), default="#FF5733")  # Hex color
    secondary_color = Column(String(7), default="#FFFFFF")
    
    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    
    # Team intelligence (AI-learned)
    playstyle = Column(JSONB, default=dict)  # e.g., {"aggression": 8.5, "rotation_speed": 7.2}
    preferred_compounds = Column(JSONB, default=list)
    common_rotations = Column(JSONB, default=list)
    aggression_score = Column(Float, nullable=True)
    team_chemistry_score = Column(Float, nullable=True)
    average_survival_time = Column(Float, nullable=True)
    
    # Stats (all-time)
    total_matches = Column(Integer, default=0)
    total_wins = Column(Integer, default=0)
    total_kills = Column(Integer, default=0)
    total_damage = Column(BigInteger, default=0)
    
    # Metadata
    social_links = Column(JSONB, default=dict)
    metadata = Column(JSONB, default=dict)
    
    # Relationships
    players = relationship("Player", back_populates="team", cascade="all, delete-orphan")
    tournament_entries = relationship("TournamentTeam", back_populates="team", cascade="all, delete-orphan")
    match_stats = relationship("MatchTeamStats", back_populates="team", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_team_name_active", "name", "is_active"),
        Index("ix_team_region", "region", "is_active"),
    )


class Player(Base):
    """Individual player"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(String(36), ForeignKey("team.id"), nullable=False)
    
    # Identity
    name = Column(String(100), nullable=False)
    ign = Column(String(50), nullable=False, index=True)  # In-Game Name
    real_name = Column(String(200), nullable=True)
    
    # Profile
    avatar_url = Column(String(500), nullable=True)
    country = Column(String(50), nullable=True)
    date_of_birth = Column(DateTime(timezone=True), nullable=True)
    
    # Role
    role = Column(SQLEnum(PlayerRole), nullable=True)
    is_captain = Column(Boolean, default=False)
    
    # BGMI-specific
    bgmi_id = Column(String(20), nullable=True, unique=True)
    bgmi_username = Column(String(100), nullable=True)
    device_model = Column(String(100), nullable=True)  # Phone/tablet model
    sensitivity_settings = Column(JSONB, nullable=True)
    
    # Player intelligence (AI-learned)
    playstyle = Column(JSONB, default=dict)  # e.g., {"playstyle": "aggressive_fragger"}
    preferred_weapons = Column(JSONB, default=list)  # ["M416", "AKM", "AWM"]
    clutch_score = Column(Float, nullable=True)
    average_damage = Column(Float, nullable=True)
    average_kills = Column(Float, nullable=True)
    headshot_percentage = Column(Float, nullable=True)
    survival_rating = Column(Float, nullable=True)
    
    # Stats (all-time)
    total_matches = Column(Integer, default=0)
    total_kills = Column(Integer, default=0)
    total_knocks = Column(Integer, default=0)
    total_damage = Column(BigInteger, default=0)
    total_wins = Column(Integer, default=0)
    mvp_awards = Column(Integer, default=0)
    
    # Status
    is_active = Column(Boolean, default=True)
    
    # Metadata
    social_links = Column(JSONB, default=dict)
    metadata = Column(JSONB, default=dict)
    
    # Relationships
    team = relationship("Team", back_populates="players")
    match_stats = relationship("MatchPlayerStats", back_populates="player", cascade="all, delete-orphan")
    kill_events = relationship("KillEvent", back_populates="killer", cascade="all, delete-orphan",
                              foreign_keys="KillEvent.killer_id")
    
    __table_args__ = (
        Index("ix_player_team", "team_id", "is_active"),
        Index("ix_player_ign", "ign", "is_active"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MATCHES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Match(Base):
    """Individual match in a tournament"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tournament_id = Column(String(36), ForeignKey("tournament.id", ondelete="CASCADE"), nullable=False)
    
    # Match info
    match_number = Column(Integer, nullable=False)
    match_name = Column(String(100), nullable=True)  # e.g., "Grand Finals Match 1"
    map_name = Column(SQLEnum(MatchMap), nullable=False)
    status = Column(SQLEnum(MatchStatus), default=MatchStatus.SCHEDULED, nullable=False, index=True)
    
    # Room details
    custom_room_id = Column(String(50), nullable=True)
    custom_room_password = Column(String(50), nullable=True)
    server_region = Column(String(20), nullable=True)
    
    # Timing
    scheduled_time = Column(DateTime(timezone=True), nullable=False)
    actual_start_time = Column(DateTime(timezone=True), nullable=True)
    actual_end_time = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    
    # Game settings
    lobby_time_seconds = Column(Integer, default=120)
    plane_path_start = Column(JSONB, nullable=True)  # {"x": 1000, "y": 2000}
    plane_path_end = Column(JSONB, nullable=True)
    weather = Column(String(20), default="sunny")
    
    # Results
    winner_team_id = Column(String(36), ForeignKey("team.id"), nullable=True)
    mvp_player_id = Column(String(36), ForeignKey("player.id"), nullable=True)
    total_kills = Column(Integer, default=0)
    total_knocks = Column(Integer, default=0)
    total_damage = Column(BigInteger, default=0)
    total_healing = Column(Integer, default=0)
    
    # Zone data
    zone_history = Column(JSONB, default=list)  # List of zone positions
    final_zone_position = Column(JSONB, nullable=True)
    
    # Production
    recording_path = Column(String(500), nullable=True)
    vod_url = Column(String(500), nullable=True)
    highlights_generated = Column(Boolean, default=False)
    report_generated = Column(Boolean, default=False)
    
    # Metadata
    tags = Column(ARRAY(String), default=list)
    metadata = Column(JSONB, default=dict)
    
    # Relationships
    tournament = relationship("Tournament", back_populates="matches")
    winner_team = relationship("Team", foreign_keys=[winner_team_id])
    mvp_player = relationship("Player", foreign_keys=[mvp_player_id])
    events = relationship("MatchEvent", back_populates="match", cascade="all, delete-orphan")
    fights = relationship("FightRecord", back_populates="match", cascade="all, delete-orphan")
    team_stats = relationship("MatchTeamStats", back_populates="match", cascade="all, delete-orphan")
    player_stats = relationship("MatchPlayerStats", back_populates="match", cascade="all, delete-orphan")
    kill_events = relationship("KillEvent", back_populates="match", cascade="all, delete-orphan")
    broadcast_sessions = relationship("BroadcastSession", back_populates="match")
    
    __table_args__ = (
        UniqueConstraint("tournament_id", "match_number", name="uq_match_tournament_number"),
        Index("ix_match_tournament_status", "tournament_id", "status"),
        Index("ix_match_scheduled_time", "scheduled_time", "status"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MATCH EVENTS (Time-series event data)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MatchEvent(Base):
    """Generic match event - core event table"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id = Column(String(36), ForeignKey("match.id", ondelete="CASCADE"), nullable=False)
    
    # Event identity
    event_type = Column(SQLEnum(EventType), nullable=False, index=True)
    event_subtype = Column(String(50), nullable=True)
    
    # Timing
    timestamp_ms = Column(BigInteger, nullable=False)  # Match time in milliseconds
    real_timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sequence_number = Column(BigInteger, nullable=True)  # For ordering
    
    # Involved entities
    team_id = Column(String(36), nullable=True, index=True)
    player_id = Column(String(36), nullable=True, index=True)
    target_team_id = Column(String(36), nullable=True)
    target_player_id = Column(String(36), nullable=True)
    
    # Location
    location_x = Column(Float, nullable=True)
    location_y = Column(Float, nullable=True)
    location_z = Column(Float, nullable=True)
    zone_phase = Column(Integer, nullable=True)
    
    # Event data (flexible)
    event_data = Column(JSONB, nullable=False, default=dict)
    
    # Processing
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    processing_errors = Column(JSONB, default=list)
    
    # Relationships
    match = relationship("Match", back_populates="events")
    
    __table_args__ = (
        Index("ix_event_match_time", "match_id", "timestamp_ms"),
        Index("ix_event_type_time", "event_type", "timestamp_ms"),
        Index("ix_event_team_time", "team_id", "timestamp_ms"),
        Index("ix_event_player_time", "player_id", "timestamp_ms"),
    )


class KillEvent(Base):
    """Detailed kill events"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id = Column(String(36), ForeignKey("match.id", ondelete="CASCADE"), nullable=False)
    
    # Timing
    timestamp_ms = Column(BigInteger, nullable=False)
    real_timestamp = Column(DateTime(timezone=True), server_default=func.now())
    
    # Killer
    killer_id = Column(String(36), ForeignKey("player.id"), nullable=False)
    killer_team_id = Column(String(36), ForeignKey("team.id"), nullable=False)
    killer_position_x = Column(Float, nullable=True)
    killer_position_y = Column(Float, nullable=True)
    
    # Victim
    victim_id = Column(String(36), ForeignKey("player.id"), nullable=False)
    victim_team_id = Column(String(36), ForeignKey("team.id"), nullable=False)
    victim_position_x = Column(Float, nullable=True)
    victim_position_y = Column(Float, nullable=True)
    
    # Kill details
    weapon = Column(String(50), nullable=False)
    damage = Column(Integer, nullable=False)
    distance = Column(Float, nullable=True)  # Meters
    headshot = Column(Boolean, default=False)
    
    # Context
    zone_phase = Column(Integer, nullable=True)
    fight_id = Column(String(36), nullable=True, index=True)
    
    # Metadata
    match_event_id = Column(String(36), ForeignKey("match_event.id"), nullable=True)
    
    # Relationships
    match = relationship("Match", back_populates="kill_events")
    killer = relationship("Player", foreign_keys=[killer_id], back_populates="kill_events")
    victim = relationship("Player", foreign_keys=[victim_id])
    
    __table_args__ = (
        Index("ix_kill_match_time", "match_id", "timestamp_ms"),
        Index("ix_kill_killer", "killer_id", "timestamp_ms"),
        Index("ix_kill_victim", "victim_id", "timestamp_ms"),
        Index("ix_kill_weapon", "weapon"),
        Index("ix_kill_fight", "fight_id"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FIGHT RECORDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FightRecord(Base):
    """Recorded fight between teams"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id = Column(String(36), ForeignKey("match.id", ondelete="CASCADE"), nullable=False)
    
    # Timing
    start_timestamp_ms = Column(BigInteger, nullable=False)
    end_timestamp_ms = Column(BigInteger, nullable=True)
    duration_ms = Column(BigInteger, nullable=True)
    
    # Participants
    team_ids = Column(JSONB, nullable=False)  # List of team IDs
    player_ids = Column(JSONB, nullable=False)  # List of player IDs involved
    
    # Location
    location_x = Column(Float, nullable=False)
    location_y = Column(Float, nullable=False)
    location_name = Column(String(100), nullable=True)  # e.g., "Pochinki", "Georgopol"
    
    # Fight characteristics
    intensity = Column(SQLEnum(FightIntensity), nullable=False, index=True)
    intensity_score = Column(Float, default=0.0)  # 0-100 numeric score
    
    # Results
    winner_team_id = Column(String(36), ForeignKey("team.id"), nullable=True)
    total_kills = Column(Integer, default=0)
    total_knocks = Column(Integer, default=0)
    total_damage = Column(Integer, default=0)
    total_revives = Column(Integer, default=0)
    
    # Utility usage
    smokes_used = Column(Integer, default=0)
    grenades_used = Column(Integer, default=0)
    molotovs_used = Column(Integer, default=0)
    flashes_used = Column(Integer, default=0)
    
    # AI predictions
    predicted_winner_id = Column(String(36), nullable=True)
    prediction_confidence = Column(Float, nullable=True)
    prediction_timestamp = Column(DateTime(timezone=True), nullable=True)
    
    # Narrative
    is_clutch = Column(Boolean, default=False)
    is_comeback = Column(Boolean, default=False)
    narrative_score = Column(Float, nullable=True)  # Entertainment value
    
    # Metadata
    event_ids = Column(JSONB, default=list)  # Related event IDs
    
    # Relationships
    match = relationship("Match", back_populates="fights")
    winner_team = relationship("Team", foreign_keys=[winner_team_id])
    
    __table_args__ = (
        Index("ix_fight_match_time", "match_id", "start_timestamp_ms"),
        Index("ix_fight_intensity_score", "intensity_score"),
        Index("ix_fight_location", "location_name"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MATCH STATISTICS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MatchTeamStats(Base):
    """Per-team per-match statistics"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id = Column(String(36), ForeignKey("match.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(String(36), ForeignKey("team.id"), nullable=False)
    
    # Results
    placement = Column(Integer, nullable=False)
    eliminated_by_team_id = Column(String(36), nullable=True)
    survival_time_seconds = Column(Integer, default=0)
    
    # Combat stats
    total_kills = Column(Integer, default=0)
    total_knocks = Column(Integer, default=0)
    total_damage = Column(BigInteger, default=0)
    total_damage_taken = Column(BigInteger, default=0)
    headshots = Column(Integer, default=0)
    
    # Survival stats
    revives = Column(Integer, default=0)
    healing_done = Column(Integer, default=0)
    boosts_used = Column(Integer, default=0)
    
    # Utility stats
    smokes_used = Column(Integer, default=0)
    grenades_used = Column(Integer, default=0)
    molotovs_used = Column(Integer, default=0)
    flashes_used = Column(Integer, default=0)
    
    # Movement
    distance_traveled = Column(Float, default=0.0)  # Meters
    vehicles_used = Column(Integer, default=0)
    
    # Points
    placement_points = Column(Integer, default=0)
    kill_points = Column(Integer, default=0)
    bonus_points = Column(Integer, default=0)
    total_points = Column(Integer, default=0)
    
    # Relationships
    match = relationship("Match", back_populates="team_stats")
    team = relationship("Team", back_populates="match_stats")
    
    __table_args__ = (
        UniqueConstraint("match_id", "team_id", name="uq_match_team"),
        Index("ix_mts_match_placement", "match_id", "placement"),
        Index("ix_mts_team_points", "team_id", "total_points"),
    )


class MatchPlayerStats(Base):
    """Per-player per-match statistics"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id = Column(String(36), ForeignKey("match.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(String(36), ForeignKey("player.id"), nullable=False)
    team_id = Column(String(36), ForeignKey("team.id"), nullable=False)
    
    # Combat
    kills = Column(Integer, default=0)
    knocks = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    damage_dealt = Column(Integer, default=0)
    damage_taken = Column(Integer, default=0)
    headshots = Column(Integer, default=0)
    
    # Survival
    survived_time_seconds = Column(Integer, default=0)
    died_at_timestamp_ms = Column(BigInteger, nullable=True)
    revives_performed = Column(Integer, default=0)
    times_knocked = Column(Integer, default=0)
    times_revived = Column(Integer, default=0)
    
    # Items
    healing = Column(Integer, default=0)
    boosts = Column(Integer, default=0)
    smokes = Column(Integer, default=0)
    grenades = Column(Integer, default=0)
    
    # Movement
    distance_traveled = Column(Float, default=0.0)
    vehicles_driven = Column(Integer, default=0)
    
    # Awards
    mvp_score = Column(Float, default=0.0)
    is_mvp = Column(Boolean, default=False)
    
    # Positions (sampled)
    positions_sampled = Column(JSONB, default=list)  # [{x, y, timestamp_ms}]
    
    # Relationships
    match = relationship("Match", back_populates="player_stats")
    player = relationship("Player", back_populates="match_stats")
    
    __table_args__ = (
        UniqueConstraint("match_id", "player_id", name="uq_match_player"),
        Index("ix_mps_match_mvp", "match_id", "mvp_score"),
        Index("ix_mps_player", "player_id", "kills"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HIGHLIGHTS & CLIPS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Highlight(Base):
    """Automatically detected highlights"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id = Column(String(36), ForeignKey("match.id", ondelete="CASCADE"), nullable=False)
    
    # Highlight info
    title = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    highlight_type = Column(SQLEnum(HighlightType), nullable=False, index=True)
    
    # Timing
    start_timestamp_ms = Column(BigInteger, nullable=False)
    end_timestamp_ms = Column(BigInteger, nullable=False)
    duration_ms = Column(BigInteger, nullable=True)
    
    # Involved
    primary_player_id = Column(String(36), ForeignKey("player.id"), nullable=True)
    primary_team_id = Column(String(36), ForeignKey("team.id"), nullable=True)
    involved_players = Column(JSONB, default=list)
    involved_teams = Column(JSONB, default=list)
    
    # Source
    fight_id = Column(String(36), nullable=True, index=True)
    kill_event_ids = Column(JSONB, default=list)
    
    # Quality
    excitement_score = Column(Float, default=0.0)  # 0-100
    view_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
    
    # Processing
    clip_path = Column(String(500), nullable=True)
    thumbnail_path = Column(String(500), nullable=True)
    processed = Column(Boolean, default=False)
    
    # Metadata
    tags = Column(ARRAY(String), default=list)
    metadata = Column(JSONB, default=dict)
    
    # Relationships
    match = relationship("Match")
    
    __table_args__ = (
        Index("ix_highlight_match", "match_id", "highlight_type"),
        Index("ix_highlight_type_score", "highlight_type", "excitement_score"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AI PREDICTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Prediction(Base):
    """AI predictions for various events"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id = Column(String(36), ForeignKey("match.id", ondelete="CASCADE"), nullable=False)
    
    # What's being predicted
    prediction_type = Column(String(50), nullable=False, index=True)  # fight_outcome, winner, zone, etc.
    target_id = Column(String(36), nullable=True)  # fight_id, team_id, etc.
    
    # Prediction
    predicted_value = Column(JSONB, nullable=False)
    confidence = Column(Float, nullable=False)
    
    # Context at prediction time
    context_data = Column(JSONB, default=dict)
    timestamp_ms = Column(BigInteger, nullable=False)
    
    # Actual outcome (filled later)
    actual_value = Column(JSONB, nullable=True)
    was_correct = Column(Boolean, nullable=True)
    error_margin = Column(Float, nullable=True)
    
    # Model info
    model_name = Column(String(100), nullable=True)
    model_version = Column(String(50), nullable=True)
    
    __table_args__ = (
        Index("ix_prediction_match_type", "match_id", "prediction_type"),
        Index("ix_prediction_correct", "prediction_type", "was_correct"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AI COMMENTARY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Commentary(Base):
    """AI-generated commentary records"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id = Column(String(36), ForeignKey("match.id", ondelete="CASCADE"), nullable=False)
    
    # Timing
    timestamp_ms = Column(BigInteger, nullable=False)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Content
    text = Column(Text, nullable=False)
    language = Column(String(10), default="hindi")
    tone = Column(String(20), default="neutral")  # hype, analytical, emotional, neutral
    
    # Voice
    audio_path = Column(String(500), nullable=True)
    voice_id = Column(String(50), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    
    # Context
    trigger_event_type = Column(String(50), nullable=True)
    trigger_event_id = Column(String(36), nullable=True)
    fight_id = Column(String(36), nullable=True)
    
    # Quality
    relevance_score = Column(Float, nullable=True)
    human_rating = Column(Integer, nullable=True)  # 1-5 from feedback
    
    # Metadata
    llm_model = Column(String(100), nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    
    __table_args__ = (
        Index("ix_commentary_match_time", "match_id", "timestamp_ms"),
        Index("ix_commentary_language", "language", "tone"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEAM INTELLIGENCE (Long-term Memory)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TeamIntelligence(Base):
    """Long-term team behavioral intelligence"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(String(36), ForeignKey("team.id"), unique=True, nullable=False)
    
    # Playstyle analysis
    aggression_rating = Column(Float, default=5.0)  # 1-10
    rotation_speed_rating = Column(Float, default=5.0)
    compound_holding_rating = Column(Float, default=5.0)
    third_party_tendency = Column(Float, default=5.0)
    
    # Preferred strategies
    preferred_landing_spots = Column(JSONB, default=list)
    favorite_compounds = Column(JSONB, default=list)
    common_rotation_paths = Column(JSONB, default=list)
    
    # Combat patterns
    average_engagement_distance = Column(Float, nullable=True)
    prefers_long_range = Column(Boolean, default=False)
    prefers_close_combat = Column(Boolean, default=False)
    
    # Utility usage patterns
    average_smokes_per_match = Column(Float, nullable=True)
    average_grenades_per_match = Column(Float, nullable=True)
    
    # Performance
    win_rate_percentage = Column(Float, default=0.0)
    top5_rate_percentage = Column(Float, default=0.0)
    average_kills_per_match = Column(Float, default=0.0)
    average_placement = Column(Float, nullable=True)
    
    # Head-to-head records
    rivalries = Column(JSONB, default=dict)  # {team_id: {wins, losses, last_encounter}}
    
    # Analysis date
    last_updated = Column(DateTime(timezone=True), server_default=func.now())
    matches_analyzed = Column(Integer, default=0)
    
    __table_args__ = (
        Index("ix_ti_aggression", "aggression_rating"),
    )


class PlayerIntelligence(Base):
    """Long-term player behavioral intelligence"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id = Column(String(36), ForeignKey("player.id"), unique=True, nullable=False)
    
    # Skill ratings
    mechanical_skill = Column(Float, default=5.0)  # 1-10
    game_sense = Column(Float, default=5.0)
    communication_rating = Column(Float, default=5.0)
    clutch_rating = Column(Float, default=5.0)
    
    # Weapon proficiency
    weapon_proficiency = Column(JSONB, default=dict)  # {weapon: proficiency_1_10}
    favorite_weapons = Column(JSONB, default=list)
    
    # Playstyle
    playstyle = Column(String(50), nullable=True)  # aggressive_fragger, support, sniper, etc.
    aggressiveness = Column(Float, default=5.0)
    survival_instinct = Column(Float, default=5.0)
    team_player_score = Column(Float, default=5.0)
    
    # Performance
    average_kills = Column(Float, default=0.0)
    average_damage = Column(Float, default=0.0)
    average_survival_time = Column(Float, default=0.0)
    consistency_score = Column(Float, default=5.0)
    
    # Analysis date
    last_updated = Column(DateTime(timezone=True), server_default=func.now())
    matches_analyzed = Column(Integer, default=0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BROADCAST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BroadcastSession(Base):
    """Broadcast session tracking"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tournament_id = Column(String(36), ForeignKey("tournament.id", ondelete="CASCADE"), nullable=False)
    match_id = Column(String(36), ForeignKey("match.id"), nullable=True)
    
    # Session info
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    
    # Mode
    mode = Column(SQLEnum(BroadcastMode), default=BroadcastMode.FULL_AUTO, nullable=False)
    
    # Status
    is_live = Column(Boolean, default=False)
    stream_url = Column(String(500), nullable=True)
    viewer_count = Column(Integer, default=0)
    peak_viewers = Column(Integer, default=0)
    
    # Performance
    total_scene_switches = Column(Integer, default=0)
    manual_overrides = Column(Integer, default=0)
    ai_accuracy_score = Column(Float, nullable=True)
    
    # Errors
    error_count = Column(Integer, default=0)
    error_log = Column(JSONB, default=list)
    
    # Relationships
    tournament = relationship("Tournament", back_populates="broadcast_sessions")
    match = relationship("Match", back_populates="broadcast_sessions")
    
    __table_args__ = (
        Index("ix_bs_tournament", "tournament_id", "started_at"),
        Index("ix_bs_match", "match_id"),
    )


class SceneSwitch(Base):
    """Record of OBS scene switches"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    broadcast_session_id = Column(String(36), ForeignKey("broadcast_session.id"), nullable=False)
    
    timestamp_ms = Column(BigInteger, nullable=False)
    from_scene = Column(String(100), nullable=False)
    to_scene = Column(String(100), nullable=False)
    
    # Decision
    triggered_by = Column(String(20), default="ai")  # ai, manual, scheduled
    agent_id = Column(String(100), nullable=True)
    reason = Column(String(200), nullable=True)
    confidence_score = Column(Float, nullable=True)
    
    # Quality
    was_good_switch = Column(Boolean, nullable=True)  # Human feedback
    
    __table_args__ = (
        Index("ix_ss_session", "broadcast_session_id", "timestamp_ms"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SYSTEM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AuditLog(Base):
    """System audit log"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    user_id = Column(String(36), nullable=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(36), nullable=True)
    
    details = Column(JSONB, default=dict)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    
    __table_args__ = (
        Index("ix_audit_action_time", "action", "created_at"),
        Index("ix_audit_user", "user_id", "created_at"),
    )


class SystemMetric(Base):
    """System performance metrics"""
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    metric_name = Column(String(100), nullable=False, index=True)
    metric_value = Column(Float, nullable=False)
    
    tags = Column(JSONB, default=dict)
    
    __table_args__ = (
        Index("ix_metric_name_time", "metric_name", "created_at"),
    )