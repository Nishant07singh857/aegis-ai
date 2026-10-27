# backend/app/agents/highlight_agents/clip_detector.py
"""
AEGIS AI - Clip Detector
Automatically detects highlight-worthy moments for clipping.
Identifies squad wipes, clutch plays, multi-kills, and funny moments.
"""

import math
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventPriority, EventRecord

logger = structlog.get_logger("aegis.agents.clip_detector")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HighlightCategory(str, Enum):
    """Categories of highlight clips"""
    SQUAD_WIPE = "squad_wipe"           # Full team elimination
    CLUTCH_WIN = "clutch_win"           # 1vX victory
    MULTI_KILL = "multi_kill"           # 3+ kills in quick succession
    GRENADE_KILL = "grenade_kill"       # Impressive grenade
    LONG_RANGE = "long_range"           # 200m+ kill
    HEADSHOT_CHAIN = "headshot_chain"   # Multiple headshots
    FINAL_CIRCLE = "final_circle"       # Final circle action
    FUNNY_MOMENT = "funny_moment"       # Humorous events
    COMEBACK = "comeback"              # Comeback victory
    RECORD = "record"                  # Record-breaking moment
    TEAM_WIPE_DENIAL = "team_wipe_denial"  # Preventing team wipe

class ClipQuality(str, Enum):
    """Quality assessment of clip"""
    LEGENDARY = "legendary"     # All-time great
    EXCELLENT = "excellent"     # Must-share
    GOOD = "good"              # Worth saving
    DECENT = "decent"          # Nice moment
    AVERAGE = "average"        # Standard play

@dataclass
class HighlightClip:
    """A detected highlight clip"""
    clip_id: str
    category: HighlightCategory
    quality: ClipQuality = ClipQuality.GOOD
    
    # Content
    title: str = ""
    description: str = ""
    
    # Timing
    start_timestamp_ms: float = 0.0
    end_timestamp_ms: float = 0.0
    duration_seconds: float = 0.0
    detected_at: float = field(default_factory=time.time)
    
    # Participants
    primary_player: str = ""
    primary_team: str = ""
    secondary_players: List[str] = field(default_factory=list)
    secondary_teams: List[str] = field(default_factory=list)
    
    # Metrics
    excitement_score: float = 0.0
    viral_potential: float = 0.0  # 0-100
    replay_value: float = 0.0
    
    # Source
    match_id: str = ""
    fight_id: str = ""
    event_ids: List[str] = field(default_factory=list)
    
    # Production
    suggested_angles: List[str] = field(default_factory=list)
    suggested_speed: float = 1.0  # 1.0 = normal, 0.25 = slow-mo
    needs_slow_motion: bool = False
    needs_zoom: bool = False
    
    # Social media
    platform_suitability: Dict[str, float] = field(default_factory=dict)
    hashtags: List[str] = field(default_factory=list)
    thumbnail_timestamp_ms: float = 0.0
    
    # Processing
    is_processed: bool = False
    processed_path: str = ""
    thumbnail_path: str = ""


class ClipDetector(BaseAgent):
    """
    Detects highlight-worthy moments in real-time.
    
    Detection Categories:
    1. Squad Wipes - Full team eliminations
    2. Clutch Wins - 1vX victories
    3. Multi-Kills - 3+ kills in quick succession
    4. Grenade Kills - Impressive explosive plays
    5. Long Range Kills - 200m+ snipes
    6. Headshot Chains - Multiple headshots
    7. Final Circle - Decisive final moments
    8. Funny Moments - Unusual/humorous events
    
    Each clip is scored for:
    - Excitement value
    - Viral potential
    - Platform suitability (YouTube, TikTok, Instagram)
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Detected clips
        self.clips: Dict[str, HighlightClip] = {}
        self.clip_history: deque = deque(maxlen=100)
        
        # Detection buffers
        self.recent_kills: deque = deque(maxlen=20)
        self.recent_damage: deque = deque(maxlen=50)

        # Phase 23 (W2): live weapon-based triggers. Kill-feed events
        # now carry a truthful CLASS-level weapon (icon template
        # matching; None when unconfident — never guessed), so sniper
        # streaks and frag plays can be counted per player as they
        # happen. Windows mirror the replay-service triggers
        # (services/replay/highlight_triggers.py): sniper triple =
        # 3 sniper-class KILLS (knocks don't count) in 60s here (the
        # replay service uses 30s on buffered kill_event only; this
        # agent sees the live stream, so a slightly wider window is
        # used and documented); frag = any frag_grenade kill OR knock.
        self._sniper_kill_times: Dict[str, deque] = {}
        self.SNIPER_WINDOW_SEC = 60.0
        self.SNIPER_TRIPLE_COUNT = 3
        
        # Scoring weights
        self.quality_thresholds = {
            ClipQuality.LEGENDARY: 90,
            ClipQuality.EXCELLENT: 75,
            ClipQuality.GOOD: 55,
            ClipQuality.DECENT: 35,
            ClipQuality.AVERAGE: 15,
        }
        
        # Platform suitability factors
        self.platform_factors = {
            "youtube_shorts": {"max_duration": 60, "aspect_ratio": "9:16", "preferred_categories": ["clutch_win", "multi_kill", "squad_wipe"]},
            "tiktok": {"max_duration": 180, "aspect_ratio": "9:16", "preferred_categories": ["funny_moment", "clutch_win", "grenade_kill"]},
            "instagram_reels": {"max_duration": 90, "aspect_ratio": "9:16", "preferred_categories": ["clutch_win", "multi_kill", "headshot_chain"]},
            "youtube_full": {"max_duration": 600, "aspect_ratio": "16:9", "preferred_categories": ["squad_wipe", "final_circle", "comeback"]},
        }
        
        self._log = logger.bind(component="clip_detector")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLIP DETECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def detect_clip(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        match_context: Dict[str, Any],
        fight_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[HighlightClip]:
        """Detect if an event should become a highlight clip"""
        
        clip = None
        
        if event_type == "squad_wipe":
            clip = self._create_squad_wipe_clip(event_data, match_context)
        elif event_type == "clutch_win":
            clip = self._create_clutch_clip(event_data, match_context)
        elif event_type == "multi_kill":
            clip = self._create_multi_kill_clip(event_data, match_context)
        elif event_type == "grenade_kill":
            clip = self._create_grenade_clip(event_data, match_context)
        elif event_type == "long_range_kill":
            clip = self._create_long_range_clip(event_data, match_context)
        elif event_type == "headshot_chain":
            clip = self._create_headshot_clip(event_data, match_context)
        elif event_type == "final_circle_moment":
            clip = self._create_final_circle_clip(event_data, match_context)
        elif event_type == "funny_moment":
            clip = self._create_funny_clip(event_data, match_context)
        elif event_type == "comeback_win":
            clip = self._create_comeback_clip(event_data, match_context)
        
        if clip:
            self._process_clip(clip)
        
        return clip
    
    def _create_squad_wipe_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create squad wipe highlight clip — evidence-gated (Phase 34, H-2).

        ``time_span`` and ``placement`` come ONLY from evidence passed
        into ``data``; there are no defaults anymore (the old
        ``time_span=15`` / ``placement=16`` invented a "15.0s" and a
        "#16 place"). When ``time_span`` is absent, the description
        carries no duration phrase and ``duration_seconds`` is the
        neutral fixed 8s clip window (the same fixed window grenade
        clips use) — never ``time_span + 8`` from an invented value.
        When ``placement`` is absent, there is no "#N place" phrase.
        Neither missing value contributes to excitement.
        """

        team_name = data.get("team_name", "Unknown")
        eliminator = data.get("eliminator", "Unknown")
        time_span = self._evidence_positive_number(data, ("time_span",))
        placement = self._evidence_positive_count(data, "placement", minimum=1)

        excitement = 65
        if placement is not None and placement <= 3:
            excitement += 25
        if time_span is not None and time_span < 10:
            excitement += 10

        quality = self._determine_quality(excitement)

        if time_span is not None and placement is not None:
            description = (
                f"{eliminator} wipes out {team_name} in {time_span:.1f}s "
                f"at #{placement} place!"
            )
        elif time_span is not None:
            description = f"{eliminator} wipes out {team_name} in {time_span:.1f}s!"
        elif placement is not None:
            description = f"{eliminator} wipes out {team_name} at #{placement} place!"
        else:
            description = f"{eliminator} wipes out {team_name}!"

        clip = HighlightClip(
            clip_id=f"clip_wipe_{int(time.time())}",
            category=HighlightCategory.SQUAD_WIPE,
            quality=quality,
            title=f"SQUAD WIPE! {eliminator} eliminates {team_name}",
            description=description,
            start_timestamp_ms=data.get("start_time_ms", 0) - 5000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 3000,
            duration_seconds=time_span + 8 if time_span is not None else 8.0,
            primary_team=eliminator,
            secondary_teams=[team_name],
            excitement_score=excitement,
            viral_potential=min(95, excitement + 15),
            replay_value=excitement,
            match_id=context.get("match_id", ""),
            fight_id=data.get("fight_id", ""),
            suggested_angles=["eliminator_pov", "overhead", "victim_pov"],
            needs_slow_motion=(quality in [ClipQuality.LEGENDARY, ClipQuality.EXCELLENT]),
            hashtags=["#SquadWipe", "#BGMI", "#Esports"],
        )

        return clip
    
    def _create_clutch_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create clutch win highlight clip — evidence-gated (Phase 34, H-2).

        ``enemy_count`` and ``win_probability`` come ONLY from evidence
        passed into ``data``; there are no defaults anymore (the old
        ``enemy_count=4`` / ``win_probability=0.1`` invented a "1v4"
        and "defies 10% odds"). ``enemy_count`` must be a positive
        int >= 2 — a 1v1 is not a clutch claim, so 1 is no evidence.
        ``win_probability`` must be a finite number in [0, 1). The
        "1vN" phrase renders only when the count is evidenced, the
        odds phrase only when the probability is evidenced, and the
        "#1v4" hashtag only when the evidenced count is exactly 4.
        Neither missing value contributes to excitement.
        """

        player_name = data.get("player_name", "Unknown")
        enemies = self._evidence_positive_count(data, "enemy_count", minimum=2)
        probability = self._evidence_probability(data, "win_probability")

        excitement = 80
        if enemies is not None and enemies >= 4:
            excitement += 15
        if probability is not None and probability < 0.1:
            excitement += 5

        quality = self._determine_quality(excitement)

        if enemies is not None:
            title = f"IMPOSSIBLE! {player_name} wins 1v{enemies} Clutch!"
        else:
            title = f"IMPOSSIBLE! {player_name} wins the Clutch!"

        if enemies is not None and probability is not None:
            description = (
                f"{player_name} defies {probability:.0%} odds to win "
                f"a 1v{enemies} clutch! LEGENDARY!"
            )
        elif enemies is not None:
            description = f"{player_name} wins a 1v{enemies} clutch! LEGENDARY!"
        elif probability is not None:
            description = (
                f"{player_name} defies {probability:.0%} odds to win "
                f"the clutch! LEGENDARY!"
            )
        else:
            description = f"{player_name} wins the clutch! LEGENDARY!"

        hashtags = ["#Clutch", "#BGMI", "#Impossible"]
        if enemies == 4:
            hashtags.insert(1, "#1v4")

        return HighlightClip(
            clip_id=f"clip_clutch_{int(time.time())}",
            category=HighlightCategory.CLUTCH_WIN,
            quality=quality,
            title=title,
            description=description,
            start_timestamp_ms=data.get("start_time_ms", 0) - 8000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 5000,
            duration_seconds=20,
            primary_player=player_name,
            primary_team=data.get("team_name", ""),
            excitement_score=excitement,
            viral_potential=min(100, excitement + 20),
            replay_value=100,
            match_id=context.get("match_id", ""),
            suggested_angles=["clutch_player_pov", "enemy_pov", "overhead"],
            suggested_speed=0.5,
            needs_slow_motion=True,
            hashtags=hashtags,
            platform_suitability={
                "youtube_shorts": 95,
                "tiktok": 98,
                "instagram_reels": 95,
                "youtube_full": 85,
            },
        )
    
    def _create_multi_kill_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[HighlightClip]:
        """Create multi-kill highlight clip.

        Honesty rule (inspection H14): ``kill_count`` and ``time_span``
        must come from the actual event. The previous defaults
        (``kill_count=3``, ``time_span=10``) invented a multi-kill out
        of an event that carried neither field. Missing (or
        sub-multi-kill) fields now mean the event is insufficient:
        skip + log, never invent.
        """

        player_name = data.get("player_name", "Unknown")
        kill_count = data.get("kill_count")
        time_span = data.get("time_span")

        if kill_count is None or time_span is None:
            self._log.warning(
                "multi_kill event skipped: missing kill_count/time_span "
                "(no defaults are invented)",
                kill_count=kill_count,
                time_span=time_span,
            )
            return None
        if kill_count < 3:
            self._log.warning(
                "multi_kill event skipped: kill_count below multi-kill "
                "threshold",
                kill_count=kill_count,
                time_span=time_span,
            )
            return None
        
        excitement = 50 + kill_count * 10
        if time_span < 5:
            excitement += 10
        
        quality = self._determine_quality(excitement)
        
        return HighlightClip(
            clip_id=f"clip_multi_{int(time.time())}",
            category=HighlightCategory.MULTI_KILL,
            quality=quality,
            title=f"RAMPAGE! {player_name} gets {kill_count} kills in {time_span:.1f}s!",
            description=f"{player_name} goes on a rampage with {kill_count} kills in just {time_span:.1f} seconds!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 3000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 2000,
            duration_seconds=time_span + 5,
            primary_player=player_name,
            primary_team=data.get("team_name", ""),
            excitement_score=excitement,
            viral_potential=min(90, excitement + 10),
            replay_value=excitement - 10,
            match_id=context.get("match_id", ""),
            suggested_angles=["player_pov", "follow_action"],
            hashtags=["#MultiKill", "#Rampage", "#BGMI"],
        )
    
    def _create_grenade_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create grenade kill highlight — evidence-gated (Phase 34, H-2).

        ``victims`` and ``distance`` come ONLY from evidence passed
        into ``data``; there are no defaults anymore (the old
        ``victims=1`` / ``distance=0`` invented a "1-man grenade").
        ``victims`` must be a positive int >= 1; ``distance`` is the
        shared ``_evidence_distance`` gate (positive finite only, so
        0 is no evidence). With no evidenced victim count the clip
        renders as a plain grenade with no count, ``needs_slow_motion``
        fires only on an evidenced count >= 3, and neither missing
        value contributes to excitement.
        """

        player_name = data.get("player_name", "Unknown")
        victims = self._evidence_positive_count(data, "victims", minimum=1)
        distance = self._evidence_distance(data)

        excitement = 45
        if victims is not None:
            excitement += victims * 15
        if distance is not None and distance > 50:
            excitement += 10

        quality = self._determine_quality(excitement)

        if victims is not None:
            title = f"KOBE! {player_name} with the {victims}-man grenade!"
            description = (
                f"Perfect grenade from {player_name} takes out {victims} players!"
            )
        else:
            title = f"KOBE! {player_name} with the grenade!"
            description = f"Perfect grenade from {player_name}!"

        return HighlightClip(
            clip_id=f"clip_nade_{int(time.time())}",
            category=HighlightCategory.GRENADE_KILL,
            quality=quality,
            title=title,
            description=description,
            start_timestamp_ms=data.get("timestamp_ms", 0) - 5000,
            end_timestamp_ms=data.get("timestamp_ms", 0) + 3000,
            duration_seconds=8,
            primary_player=player_name,
            primary_team=data.get("team_name", ""),
            excitement_score=excitement,
            viral_potential=min(85, excitement + 15),
            replay_value=excitement,
            match_id=context.get("match_id", ""),
            suggested_angles=["player_pov", "overhead", "explosion_closeup"],
            needs_slow_motion=(victims is not None and victims >= 3),
            hashtags=["#GrenadeKill", "#Nade", "#BGMI"],
            platform_suitability={"tiktok": 90, "youtube_shorts": 85},
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 33 (D-2): EVIDENCE-GATED LONG-RANGE RENDERING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    _NO_WEAPON_TOKENS = frozenset({"", "unknown", "none", "null", "n/a"})

    # Dict-unwrap keys for _evidence_weapon. "weapon_family" is
    # deliberately NOT among them: a family id (e.g. "ar_a") is an
    # internal classifier label, never a display gun name, at any
    # nesting depth.
    _WEAPON_UNWRAP_KEYS = ("name", "label", "weapon", "weapon_class")

    @staticmethod
    def _evidence_weapon(data: Dict[str, Any]) -> Optional[str]:
        """Return the evidenced weapon display name, or None.

        Evidence is a real weapon value passed into the event: the
        kill-feed ``weapon`` (Phase 23/32: class-level, margin-gated
        icon-template match — ``None`` when unconfident, never
        guessed), or an explicit ``weapon_name`` / ``weapon_class``.
        A dict (e.g. an IconMatch-shaped payload) is unwrapped via
        its name/label/weapon/weapon_class keys, at any nesting
        depth; ``weapon_family`` is never an unwrap key, so a dict
        carrying ONLY ``weapon_family`` (e.g. ``{"weapon_family":
        "ar_a"}``) is NO evidence, nested or top-level. An unwrapped
        value that is itself a dict or another non-string with no
        string name yields None — never a guess. An object with a
        ``weapon`` string attribute is accepted the same way.
        Sentinels (``"Unknown"``, ``"None"``, ``"unknown_*``
        abstention glyphs) and non-strings mean NO evidence — the
        caller gets gun-less rendering, never a default gun. The
        value is returned verbatim (stripped); nothing here maps a
        class to a specific gun or vice versa.
        """
        raw: Any = None
        for key in ("weapon", "weapon_name", "weapon_class"):
            if data.get(key) is not None:
                raw = data.get(key)
                break
        if raw is None:
            return None
        # Unwrap nested dicts (bounded depth). The first non-None
        # unwrap key wins at each level; a dict with none of the
        # keys is no evidence. A value that stays a dict past the
        # bound is no evidence either.
        for _ in range(3):
            if not isinstance(raw, dict):
                break
            for key in ClipDetector._WEAPON_UNWRAP_KEYS:
                if raw.get(key) is not None:
                    raw = raw.get(key)
                    break
            else:
                return None
        if isinstance(raw, dict):
            return None
        if not isinstance(raw, str):
            weapon_attr = getattr(raw, "weapon", None)
            if isinstance(weapon_attr, str):
                raw = weapon_attr
            elif isinstance(weapon_attr, dict):
                for key in ClipDetector._WEAPON_UNWRAP_KEYS:
                    if weapon_attr.get(key) is not None:
                        raw = weapon_attr.get(key)
                        break
                else:
                    return None
                if not isinstance(raw, str):
                    return None
            else:
                return None
        value = raw.strip()
        lowered = value.lower()
        if lowered in ClipDetector._NO_WEAPON_TOKENS or lowered.startswith("unknown_"):
            return None
        return value

    @staticmethod
    def _evidence_number(data: Dict[str, Any], keys: tuple) -> Optional[float]:
        """Shared numeric parsing for the evidence helpers.

        Takes the first non-``None`` of ``keys``. Only real numbers
        and plain numeric strings count as parseable; bools,
        non-numeric strings, dicts/lists and missing keys are NO
        evidence. NaN/inf parse but are rejected here, so every
        caller built on this helper is finite-only by construction.
        Range/floor checks are the caller's job.
        """
        raw: Any = None
        for key in keys:
            if data.get(key) is not None:
                raw = data.get(key)
                break
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, str):
            try:
                raw = float(raw.strip())
            except (TypeError, ValueError):
                return None
        if not isinstance(raw, (int, float)):
            return None
        value = float(raw)
        if not math.isfinite(value):
            return None
        return value

    @staticmethod
    def _evidence_positive_number(
        data: Dict[str, Any], keys: tuple
    ) -> Optional[float]:
        """Return the evidenced positive finite number, or None.

        Same parsing contract as ``_evidence_number``; zero and
        negative values are NO evidence.
        """
        value = ClipDetector._evidence_number(data, keys)
        if value is None or value <= 0:
            return None
        return value

    @staticmethod
    def _evidence_positive_count(
        data: Dict[str, Any], key: str, minimum: int = 1
    ) -> Optional[int]:
        """Return the evidenced positive count, or None.

        Same parsing contract as ``_evidence_number`` (plain numeric
        strings that parse to an integral value count, mirroring
        ``_evidence_distance``); the value must additionally be a
        whole number >= ``minimum``. Bools, 0/negative values,
        fractional values, NaN/inf, non-numeric strings, dicts and
        missing keys are NO evidence — never a default count.
        """
        value = ClipDetector._evidence_number(data, (key,))
        if value is None or not value.is_integer() or value < minimum:
            return None
        return int(value)

    @staticmethod
    def _evidence_probability(data: Dict[str, Any], key: str) -> Optional[float]:
        """Return the evidenced probability in [0, 1), or None.

        Same parsing contract as ``_evidence_number``; values
        outside [0, 1) — including exactly 1.0 — are NO evidence.
        """
        value = ClipDetector._evidence_number(data, (key,))
        if value is None or not 0 <= value < 1:
            return None
        return value

    @staticmethod
    def _evidence_distance(data: Dict[str, Any]) -> Optional[float]:
        """Return the evidenced kill distance in metres, or None.

        Evidence is a measured distance passed into the event
        (``distance`` / ``distance_m`` / ``distance_meters``).
        Only finite, positive, real numbers (or plain numeric
        strings) count. Bools, zero/negative values, NaN/inf,
        non-numeric strings and missing keys mean NO evidence —
        the caller gets distance-less rendering, never a default
        number. Fail-closed by design: an unparseable value is
        unknown, not 200. Implemented on the shared
        ``_evidence_positive_number`` gate, so this is the same
        evidence path every other numeric creator uses.
        """
        return ClipDetector._evidence_positive_number(
            data, ("distance", "distance_m", "distance_meters", "distance_metres")
        )

    def _create_long_range_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create long range kill highlight — evidence-gated (D-2).

        The previous defaults (``distance=200``, ``weapon="Sniper"``)
        were live-reachable: the commentary path
        (``handle_event`` → ``detect_clip("long_range_kill", ...)``)
        built its event WITHOUT weapon/distance, so every such clip
        shipped an invented ``"200m Sniper shot!"`` to viewers.
        There are no defaults anymore. Weapon and distance come
        ONLY from evidence passed into ``data`` (kill-feed
        IconMatch weapon / measured distance); when either is
        absent, the clip renders WITHOUT it — gun-less and/or
        distance-less — and the excitement score gets NO
        distance contribution. The ``long_range`` category label
        itself is the caller's event classification, not a measured
        claim, so it is retained; no number and no gun name is ever
        invented to go with it. The signature is unchanged
        (``data, context``), so existing callers keep working —
        they just stop getting invented claims.

        Rendering matrix (``headshot`` only counts when literally
        a bool ``True``; anything else means a plain kill):
        - weapon + distance: ``"hits 320m AWM shot!"``
        - distance only:     ``"hits 320m shot!"`` (no gun)
        - weapon only:        ``"long-range AWM shot!"`` (no number)
        - neither:            ``"long-range shot!"`` (neither)
        ``#Sniper`` is added ONLY when the evidenced weapon is
        exactly the ``"sniper"`` class — a specific gun (e.g. AWM)
        earns no class hashtag, because no gun→class map is
        asserted here.
        """

        player_name = data.get("player_name", "Unknown")
        weapon = self._evidence_weapon(data)
        distance = self._evidence_distance(data)
        headshot = data.get("headshot") is True

        # Excitement: distance contributes ONLY when evidenced.
        excitement = 40
        if distance is not None:
            excitement += min(40, distance / 10)
        if headshot:
            excitement += 15

        quality = self._determine_quality(excitement)

        if weapon is not None and distance is not None:
            dist_str = f"{int(distance)}" if float(distance).is_integer() else f"{distance:g}"
            title = f"INCREDIBLE! {player_name} hits {dist_str}m {weapon} shot!"
            description = (
                f"{player_name} lands an unbelievable {dist_str} meter "
                f"{'headshot' if headshot else 'kill'} with {weapon}!"
            )
        elif distance is not None:
            dist_str = f"{int(distance)}" if float(distance).is_integer() else f"{distance:g}"
            title = f"INCREDIBLE! {player_name} hits {dist_str}m shot!"
            description = (
                f"{player_name} lands an unbelievable {dist_str} meter "
                f"{'headshot' if headshot else 'kill'}!"
            )
        elif weapon is not None:
            title = f"INCREDIBLE! {player_name} lands a long-range {weapon} shot!"
            description = (
                f"{player_name} lands an unbelievable long-range "
                f"{'headshot' if headshot else 'kill'} with {weapon}!"
            )
        else:
            title = f"INCREDIBLE! {player_name} lands a long-range shot!"
            description = (
                f"{player_name} lands an unbelievable long-range "
                f"{'headshot' if headshot else 'kill'}!"
            )

        hashtags = ["#LongRange", "#BGMI"]
        if weapon is not None and weapon.strip().lower() == "sniper":
            hashtags.insert(1, "#Sniper")

        return HighlightClip(
            clip_id=f"clip_range_{int(time.time())}",
            category=HighlightCategory.LONG_RANGE,
            quality=quality,
            title=title,
            description=description,
            start_timestamp_ms=data.get("timestamp_ms", 0) - 4000,
            end_timestamp_ms=data.get("timestamp_ms", 0) + 3000,
            duration_seconds=7,
            primary_player=player_name,
            excitement_score=excitement,
            viral_potential=min(80, excitement),
            replay_value=excitement,
            suggested_angles=["player_scope_view", "bullet_trajectory", "victim_pov"],
            needs_slow_motion=True,
            needs_zoom=True,
            hashtags=hashtags,
        )
    
    def _create_headshot_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create headshot chain highlight — evidence-gated (Phase 34, H-2).

        ``headshots`` comes ONLY from evidence passed into ``data``;
        there is no default anymore (the old ``headshots=2`` invented
        "2 headshots!"). The count must be a positive int >= 1. With
        no evidenced count the clip renders count-less ("ONE TAPS!
        {player}!") and the excitement score gets NO count
        contribution.
        """

        player_name = data.get("player_name", "Unknown")
        headshots = self._evidence_positive_count(data, "headshots", minimum=1)

        excitement = 40
        if headshots is not None:
            excitement += headshots * 15

        if headshots is not None:
            title = f"ONE TAPS! {player_name} with {headshots} headshots!"
            description = (
                f"Clean headshots from {player_name} - {headshots} perfect one-taps!"
            )
        else:
            title = f"ONE TAPS! {player_name}!"
            description = f"Clean headshots from {player_name} - perfect one-taps!"

        return HighlightClip(
            clip_id=f"clip_hs_{int(time.time())}",
            category=HighlightCategory.HEADSHOT_CHAIN,
            quality=self._determine_quality(excitement),
            title=title,
            description=description,
            start_timestamp_ms=data.get("start_time_ms", 0) - 2000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 2000,
            duration_seconds=6,
            primary_player=player_name,
            excitement_score=excitement,
            viral_potential=min(75, excitement + 10),
            suggested_angles=["player_pov", "scope_view"],
            needs_slow_motion=True,
            hashtags=["#Headshot", "#OneTap", "#BGMI"],
        )
    
    def _create_final_circle_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create final circle highlight"""
        
        winner = data.get("winner_team", "Unknown")
        teams_alive = data.get("teams_alive", 2)
        
        excitement = 70
        if teams_alive <= 2:
            excitement += 20
        
        return HighlightClip(
            clip_id=f"clip_final_{int(time.time())}",
            category=HighlightCategory.FINAL_CIRCLE,
            quality=self._determine_quality(excitement),
            title=f"FINAL CIRCLE! {winner} wins it all!",
            description=f"The final circle decides everything - {winner} emerges victorious!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 15000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 8000,
            duration_seconds=25,
            primary_team=winner,
            excitement_score=excitement,
            viral_potential=min(85, excitement + 10),
            replay_value=90,
            suggested_angles=["overhead", "winner_pov", "final_moments"],
            needs_slow_motion=True,
            hashtags=["#FinalCircle", "#Winner", "#BGMI", "#ChickenDinner"],
        )
    
    def _create_funny_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create funny moment highlight"""
        
        return HighlightClip(
            clip_id=f"clip_funny_{int(time.time())}",
            category=HighlightCategory.FUNNY_MOMENT,
            quality=ClipQuality.GOOD,
            title=data.get("title", "Funny Moment! 😂"),
            description=data.get("description", "You have to see this!"),
            start_timestamp_ms=data.get("timestamp_ms", 0) - 5000,
            end_timestamp_ms=data.get("timestamp_ms", 0) + 5000,
            duration_seconds=10,
            primary_player=data.get("player_name", ""),
            excitement_score=35,
            viral_potential=85,
            hashtags=["#Funny", "#BGMI", "#Gaming", "#Fails"],
            platform_suitability={"tiktok": 95, "instagram_reels": 90},
        )
    
    def _create_comeback_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create comeback highlight"""
        
        team_name = data.get("team_name", "Unknown")
        from_position = data.get("from_position", 8)
        
        excitement = 60 + from_position * 3
        
        return HighlightClip(
            clip_id=f"clip_comeback_{int(time.time())}",
            category=HighlightCategory.COMEBACK,
            quality=self._determine_quality(excitement),
            title=f"THE COMEBACK! {team_name} rises from #{from_position}!",
            description=f"An incredible comeback story - {team_name} defies all odds!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 10000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 5000,
            duration_seconds=20,
            primary_team=team_name,
            excitement_score=excitement,
            viral_potential=min(90, excitement + 10),
            replay_value=85,
            hashtags=["#Comeback", "#NeverGiveUp", "#BGMI"],
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 23: LIVE WEAPON TRIGGERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _norm_weapon(raw: Any) -> str:
        """Normalise a bus-payload weapon value for comparison."""
        return str(raw or "").strip().lower().replace(" ", "_")

    def check_weapon_highlight(
        self,
        event_data: Dict[str, Any],
        match_context: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> Optional[HighlightClip]:
        """Live weapon-gated highlight check for one kill/knock event.

        ``event_data`` uses the canonical bus payload shape (killer,
        victim, weapon, isKnock, killerTeam). Returns a processed
        HighlightClip when the event completes a weapon highlight:

        * weapon == "frag_grenade" (kill or knock) -> grenade clip.
        * weapon == "sniper" KILLS: counted per player; the 3rd kill
          inside SNIPER_WINDOW_SEC -> sniper-triple clip.

        Events without a truthful weapon (None / "Unknown") NEVER
        fire — the weapon field is confidence-gated upstream.
        """
        weapon = self._norm_weapon(event_data.get("weapon"))
        if weapon in ("", "unknown", "none"):
            return None
        now = time.time() if now is None else now
        player = str(
            event_data.get("killer")
            or event_data.get("player_name")
            or "Unknown"
        )
        team = str(event_data.get("killerTeam") or event_data.get("team_name") or "")
        is_knock = bool(event_data.get("isKnock"))
        context = match_context or {}

        if weapon == "frag_grenade":
            clip = self._create_grenade_clip(
                {
                    "player_name": player,
                    "team_name": team,
                    "victims": 1,
                    "distance": 0,
                    "timestamp_ms": int(now * 1000),
                },
                context,
            )
            clip.title = f"FRAG OUT! {player} with the frag grenade!"
            clip.description = (
                f"{player} lands a frag-grenade "
                f"{'knock' if is_knock else 'elimination'} on "
                f"{event_data.get('victim') or 'Unknown'} (kill-feed icon verified)."
            )
            self._process_clip(clip)
            return clip

        if weapon == "sniper" and not is_knock:
            times = self._sniper_kill_times.setdefault(player, deque(maxlen=10))
            times.append(now)
            while times and now - times[0] > self.SNIPER_WINDOW_SEC:
                times.popleft()
            if len(times) >= self.SNIPER_TRIPLE_COUNT:
                span = times[-1] - times[0]
                times.clear()
                clip = HighlightClip(
                    clip_id=f"clip_snipertriple_{int(now * 1000)}",
                    category=HighlightCategory.LONG_RANGE,
                    quality=self._determine_quality(85),
                    title=f"SNIPER TRIPLE! {player} — 3 sniper kills in {span:.0f}s!",
                    description=(
                        f"{player} lands 3 sniper-class eliminations within "
                        f"{span:.1f}s (kill-feed icon verified per kill)."
                    ),
                    start_timestamp_ms=int((now - span) * 1000) - 4000,
                    end_timestamp_ms=int(now * 1000) + 3000,
                    duration_seconds=span + 7,
                    primary_player=player,
                    primary_team=team,
                    excitement_score=85,
                    viral_potential=90,
                    replay_value=85,
                    match_id=context.get("match_id", ""),
                    suggested_angles=["player_scope_view", "bullet_trajectory", "victim_pov"],
                    needs_slow_motion=True,
                    needs_zoom=True,
                    hashtags=["#SniperTriple", "#LongRange", "#BGMI"],
                )
                self._process_clip(clip)
                return clip
        return None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLIP PROCESSING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _process_clip(self, clip: HighlightClip) -> None:
        """Process a detected clip"""
        
        # Calculate platform suitability
        clip.platform_suitability = self._calculate_platform_suitability(clip)
        
        # Generate thumbnail timestamp
        clip.thumbnail_timestamp_ms = clip.start_timestamp_ms + (clip.duration_seconds * 0.5 * 1000)
        
        # Store clip
        self.clips[clip.clip_id] = clip
        
        self._log.info(
            f"Clip detected: {clip.title}",
            quality=clip.quality.value,
            excitement=round(clip.excitement_score, 1),
            viral=round(clip.viral_potential, 1),
        )
    
    def _calculate_platform_suitability(self, clip: HighlightClip) -> Dict[str, float]:
        """Calculate how suitable clip is for each platform"""
        
        suitability = {}
        
        for platform, factors in self.platform_factors.items():
            score = 50.0  # Base
            
            # Duration check
            if clip.duration_seconds <= factors["max_duration"]:
                score += 20
            
            # Category preference
            if clip.category.value in factors["preferred_categories"]:
                score += 20
            
            # Excitement bonus
            score += clip.excitement_score * 0.1
            
            # Clutch/comeback are universally good
            if clip.category in [HighlightCategory.CLUTCH_WIN, HighlightCategory.COMEBACK]:
                score += 15
            
            suitability[platform] = min(100, score)
        
        return suitability
    
    def _determine_quality(self, excitement_score: float) -> ClipQuality:
        """Determine clip quality from excitement score"""
        
        for quality, threshold in self.quality_thresholds.items():
            if excitement_score >= threshold:
                return quality
        
        return ClipQuality.AVERAGE
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_top_clips(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top clips by excitement score"""
        
        sorted_clips = sorted(
            self.clips.values(),
            key=lambda c: c.excitement_score,
            reverse=True,
        )[:limit]
        
        return [
            {
                "clip_id": c.clip_id,
                "title": c.title,
                "category": c.category.value,
                "quality": c.quality.value,
                "excitement": round(c.excitement_score, 1),
                "viral_potential": round(c.viral_potential, 1),
                "duration": round(c.duration_seconds, 1),
                "primary_player": c.primary_player,
                "primary_team": c.primary_team,
                "best_platform": max(c.platform_suitability, key=c.platform_suitability.get) if c.platform_suitability else "youtube_shorts",
            }
            for c in sorted_clips
        ]
    
    def get_clips_by_category(self, category: HighlightCategory) -> List[Dict[str, Any]]:
        """Get clips of specific category"""
        return [
            {
                "title": c.title,
                "quality": c.quality.value,
                "excitement": round(c.excitement_score, 1),
            }
            for c in self.clips.values()
            if c.category == category
        ]
    
    def get_clip(self, clip_id: str) -> Optional[Dict[str, Any]]:
        """Get specific clip details"""
        clip = self.clips.get(clip_id)
        if not clip:
            return None
        
        return {
            "clip_id": clip.clip_id,
            "title": clip.title,
            "description": clip.description,
            "category": clip.category.value,
            "quality": clip.quality.value,
            "duration": round(clip.duration_seconds, 1),
            "excitement": round(clip.excitement_score, 1),
            "viral_potential": round(clip.viral_potential, 1),
            "replay_value": round(clip.replay_value, 1),
            "primary_player": clip.primary_player,
            "primary_team": clip.primary_team,
            "suggested_angles": clip.suggested_angles,
            "needs_slow_motion": clip.needs_slow_motion,
            "platform_suitability": clip.platform_suitability,
            "hashtags": clip.hashtags,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get clip detector statistics"""
        return {
            "total_clips": len(self.clips),
            "by_quality": {
                q.value: len([c for c in self.clips.values() if c.quality == q])
                for q in ClipQuality
            },
            "by_category": {
                cat.value: len([c for c in self.clips.values() if c.category == cat])
                for cat in HighlightCategory
            },
            "top_clips": self.get_top_clips(5),
        }
    
    def reset(self) -> None:
        """Reset clip detector"""
        self.clips.clear()
        self.clip_history.clear()
        self.recent_kills.clear()
        self.recent_damage.clear()
        self._log.info("Clip detector reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("clutch_detected")
        self.subscribe_to_event("highlight_moment")
        self.subscribe_to_event("fight_detected")
        # Chain: script generator -> clip detector -> highlight pipeline
        self.subscribe_to_event("commentary.line")
        # Phase 23: live kill/knock stream for the weapon triggers
        # (check_weapon_highlight) — the bus names every feed adapter
        # publishes via NormalizedEvent.to_bus_payload().
        self.subscribe_to_event("kill_event")
        self.subscribe_to_event("knock_event")

    async def tick(self) -> None:
        """Periodic maintenance tick."""
        # Heuristic engine is driven by direct method calls;
        # nothing periodic is required here.

    async def handle_event(self, record: EventRecord) -> None:
        """Handle an event from the event bus.

        commentary.line events run through the real clip pipeline
        (detect_clip) and publish highlight.clip_ready when a clip
        candidate is produced. All paths are exception-guarded.
        """
        try:
            event = getattr(record, "event", None)
            event_type = getattr(event, "event_type", "") if event else ""
            data = getattr(event, "data", {}) if event else {}
            if not isinstance(data, dict):
                return

            if data.get("match_id"):
                self.config.match_id = data["match_id"]

            # Phase 23: weapon-gated live triggers on the kill stream.
            if event_type in ("kill_event", "knock_event"):
                clip = self.check_weapon_highlight(
                    data, {"match_id": data.get("match_id") or self.config.match_id}
                )
                if clip is not None:
                    try:
                        await self.event_bus.publish(
                            event_type="highlight.clip_ready",
                            data={
                                "clip_id": clip.clip_id,
                                "category": clip.category.value,
                                "quality": clip.quality.value,
                                "title": clip.title,
                                "excitement_score": round(clip.excitement_score, 1),
                                "fight_id": data.get("fight_id"),
                                "match_id": data.get("match_id") or self.config.match_id,
                                "weapon": data.get("weapon"),
                                "trigger": "weapon_icon",
                            },
                            priority=EventPriority.LOW,
                            source=self.config.agent_id,
                            match_id=data.get("match_id") or self.config.match_id,
                        )
                    except Exception as e:
                        self._log.warning(f"Failed to publish highlight.clip_ready: {e}")
                return

            if event_type != "commentary.line":
                return

            clip_event_type = str(data.get("clip_event_type") or "multi_kill")
            match_id = data.get("match_id") or self.config.match_id

            # Honesty rule (inspection H14, Phase 33 D-2): kill_count /
            # time_span are taken from the event itself, never
            # defaulted here. If the commentary event does not carry
            # them, detect_clip treats the event as insufficient and
            # produces no clip. Same for long-range evidence: weapon
            # / distance / headshot are forwarded ONLY when the
            # commentary event actually carries them; when absent,
            # _create_long_range_clip renders gun-less /
            # distance-less — the old path built this dict without
            # them at all, so the old 200m/Sniper defaults fired.
            event_data = {
                "player_name": data.get("player_name", "Unknown"),
                "team_name": data.get("team_name", ""),
                "kill_count": data.get("kill_count"),
                "time_span": data.get("time_span"),
                "distance": data.get("distance"),
                "distance_m": data.get("distance_m"),
                "weapon": data.get("weapon"),
                "weapon_name": data.get("weapon_name"),
                "weapon_class": data.get("weapon_class"),
                "headshot": data.get("headshot"),
                "timestamp_ms": int(time.time() * 1000),
                "commentary_text": str(data.get("text", ""))[:300],
                "trigger": data.get("trigger", ""),
                "hype_level": data.get("hype_level", ""),
            }
            match_context = {"match_id": match_id}
            fight_context = {"fight_id": data.get("fight_id", "")}

            clip = self.detect_clip(
                clip_event_type, event_data, match_context, fight_context
            )
            if clip is None:
                return

            try:
                await self.event_bus.publish(
                    event_type="highlight.clip_ready",
                    data={
                        "clip_id": clip.clip_id,
                        "category": clip.category.value,
                        "quality": clip.quality.value,
                        "title": clip.title,
                        "excitement_score": round(clip.excitement_score, 1),
                        "fight_id": data.get("fight_id"),
                        "match_id": match_id,
                    },
                    priority=EventPriority.LOW,
                    source=self.config.agent_id,
                    match_id=match_id,
                )
            except Exception as e:
                self._log.warning(f"Failed to publish highlight.clip_ready: {e}")
        except Exception:
            pass

    async def cleanup(self) -> None:
        """Release agent resources."""
