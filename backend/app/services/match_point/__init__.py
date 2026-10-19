"""AEGIS AI — match-point service package."""

from .service import (
    MODES,
    SMASH_WS_MESSAGE_TYPE,
    WS_MESSAGE_TYPE,
    InvalidMatchPointConfigError,
    MatchPointService,
    match_point_service,
)
from .smash_rule import (
    BROADCAST_BEATS,
    STATES,
    TERMINAL_STATES,
    InvalidSmashConfigError,
    SmashRuleConfig,
    SmashRuleEngine,
    SmashRuleTransitionError,
)

__all__ = [
    "MODES",
    "WS_MESSAGE_TYPE",
    "SMASH_WS_MESSAGE_TYPE",
    "InvalidMatchPointConfigError",
    "MatchPointService",
    "match_point_service",
    "BROADCAST_BEATS",
    "STATES",
    "TERMINAL_STATES",
    "InvalidSmashConfigError",
    "SmashRuleConfig",
    "SmashRuleEngine",
    "SmashRuleTransitionError",
]
