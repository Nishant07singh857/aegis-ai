# backend/app/db/models/enums.py
"""
AEGIS AI - Shared enumerations.

String-valued enums used across models, services and tests.
Salvaged from the legacy ``app/db/models.py`` monolith (now deleted);
kept here so ``from app.db.models import TournamentStatus`` etc. keep working.
"""

from __future__ import annotations

import enum


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
