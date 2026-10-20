# backend/app/services/vision_intelligence/anti_cheat.py
"""Vision-derivable anti-cheat checks (Phase 22, M9).

``referee_agents/cheating_detector.py`` implements 12 cheat types, but
its inputs are aim/movement *telemetry* (snap angles, tracking
smoothness, reaction times, recoil patterns) that no spectator feed
produces — so the detector was feed-dead: correct code, no data, no
review surface.

This module defines, honestly, the subset that CAN run on data the
vision pipeline actually emits, and wires those checks to live events:

  LIVE — ``impossible_position_jump``  (CheatType.TELEPORT)
      Consecutive minimap sightings of the same marker whose implied
      speed exceeds any legitimate movement. With a calibrated
      minimap scale (pixels per meter configured) the speed is real
      m/s and the threshold is 45 m/s (above BGMI's fastest vehicle,
      ~42 m/s). WITHOUT a scale the check still runs in raw minimap
      pixels — a >200px jump inside 1s on a ~250px-wide minimap — but
      at LOW confidence with the uncalibrated units stated in the
      evidence, because the meter value cannot be claimed.

  LIVE — ``kill_feed_rate_anomaly``    (CheatType.DAMAGE_MOD)
      The statistical-outlier check from the original detector
      (kills/minute above ``max_legit_kpm`` = 8), computed from the
      real kill-feed stream instead of dead telemetry: >= 9 kills by
      one killer inside any rolling 60 s window.

  LIVE — ``alive_count_inconsistency`` (CheatType.GHOST_MODE)
      Alive counts cannot rise in a battle royale. Flags: the match
      alive counter increasing, a team's alive count increasing, or a
      player's HP returning from exactly 0 (a dead bar) — a presence
      the game says should not exist. Each is a data impossibility a
      reviewer should look at, whatever its cause (cheat, feed bug,
      or OCR error — the flag says "review this", never "ban this").

Everything else stays DISABLED with its reason in ``DISABLED_CHECKS`` —
the telemetry it needs does not exist in a spectator feed, and faking
those inputs would make the whole system a liar. Flags from the live
checks are review items for humans, exactly like the original
detector's design: nothing here bans anyone.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.vision_intelligence.anti_cheat")

#: Fastest legitimate movement in BGMI (vehicle top speed ~150 km/h).
MAX_LEGIT_SPEED_MPS = 45.0
#: Uncalibrated fallback: a jump larger than this many minimap pixels
#: inside JUMP_WINDOW_S is impossible at any plausible minimap zoom —
#: the minimap widget itself is only ~250px wide at 960x540 capture.
UNCALIBRATED_JUMP_PX = 200.0
JUMP_WINDOW_S = 1.0
#: From cheating_detector.baselines["max_legit_kpm"].
MAX_LEGIT_KILLS_PER_MINUTE = 8


#: Telemetry-only checks: explicitly disabled, with the honest reason.
DISABLED_CHECKS: List[Dict[str, str]] = [
    {
        "check": "aimbot_aim_patterns",
        "cheat_type": "aimbot",
        "status": "disabled",
        "reason": "Needs per-shot aim telemetry (snap angles, tracking "
        "smoothness, headshot/accuracy rates). A spectator feed provides "
        "none of these; the vision pipeline cannot observe aim.",
    },
    {
        "check": "reaction_time",
        "cheat_type": "aimbot",
        "status": "disabled",
        "reason": "Needs engagement reaction-time telemetry (ms between "
        "target visibility and first shot). Spectator video is delayed "
        "30s+ and frame-sampled at ~2.3 FPS, so reaction times measured "
        "from it would be fabricated precision.",
    },
    {
        "check": "wallhack_prefire",
        "cheat_type": "wallhack",
        "reason": "Needs view-direction + occlusion geometry per player "
        "(who could see whom, when). The minimap gives 2D positions "
        "only — no facing, no walls, no line-of-sight.",
        "status": "disabled",
    },
    {
        "check": "esp_knowledge",
        "cheat_type": "esp",
        "status": "disabled",
        "reason": "Would require proving a player acted on information "
        "they could not legitimately have. Position data alone cannot "
        "distinguish ESP from game sense, sound cues or team comms.",
    },
    {
        "check": "speed_hack",
        "cheat_type": "speed_hack",
        "status": "disabled",
        "reason": "Needs ground-truth movement speed in m/s over time. "
        "The vision position stream is uncalibrated minimap pixels; "
        "only the extreme-jump subset is checkable (see "
        "impossible_position_jump).",
    },
    {
        "check": "recoil_script",
        "cheat_type": "recoil_script",
        "status": "disabled",
        "reason": "Needs shot-level recoil/spray pattern telemetry. "
        "No spectator feed exposes per-bullet data.",
    },
    {
        "check": "radar_hack",
        "cheat_type": "radar_hack",
        "status": "disabled",
        "reason": "A client-side information cheat: undetectable from "
        "broadcast video in principle — the spectator minimap is the "
        "game's own, shown to everyone.",
    },
    {
        "check": "damage_mod_statistics",
        "cheat_type": "damage_mod",
        "status": "disabled",
        "reason": "The damage-per-minute outlier needs per-player "
        "damage telemetry. Vision damage is HP-bar deltas for the "
        "observed squad only — not a match-wide damage ledger. (The "
        "kill-rate half of the outlier check IS live: see "
        "kill_feed_rate_anomaly.)",
    },
    {
        "check": "auto_heal",
        "cheat_type": "auto_heal",
        "status": "disabled",
        "reason": "Needs input-timing telemetry (heal start latency "
        "after damage). The healing detector sees heal animations on "
        "the observed screen only, with no input timestamps.",
    },
    {
        "check": "auto_loot",
        "cheat_type": "auto_loot",
        "status": "disabled",
        "reason": "Needs loot-interaction timing/loot-path telemetry. "
        "The loot detector confirms crate presence on screen; it "
        "cannot time a player's pickups.",
    },
    {
        "check": "magic_bullet",
        "cheat_type": "magic_bullet",
        "status": "disabled",
        "reason": "Needs bullet trajectory / hit-registration data "
        "(shots that could not have hit). No such data exists outside "
        "the game server.",
    },
]

#: Live checks run by VisionAntiCheatAnalyzer.
LIVE_CHECKS: List[Dict[str, str]] = [
    {
        "check": "impossible_position_jump",
        "cheat_type": "teleport",
        "status": "live",
        "reason": "Runs on the vision position_update stream (minimap "
        "marker sightings). Speed is real m/s when a minimap scale is "
        "configured; otherwise raw-pixel jumps at low confidence.",
    },
    {
        "check": "kill_feed_rate_anomaly",
        "cheat_type": "damage_mod",
        "status": "live",
        "reason": "Runs on the kill-feed stream: kills/minute above "
        "the detector's max_legit_kpm baseline (8/min).",
    },
    {
        "check": "alive_count_inconsistency",
        "cheat_type": "ghost_mode",
        "status": "live",
        "reason": "Runs on alive_count / team_status / hp_update "
        "streams: alive counts rising, or HP returning from 0, are "
        "impossible in a real match.",
    },
]


def checks_registry() -> List[Dict[str, str]]:
    """The full check registry: live checks + disabled-with-reason."""
    return [dict(c) for c in LIVE_CHECKS] + [dict(c) for c in DISABLED_CHECKS]


def _confidence_level(score: float) -> str:
    """Same bands as cheating_detector._determine_confidence."""
    if score >= 90:
        return "certain"
    if score >= 70:
        return "high"
    if score >= 50:
        return "moderate"
    if score >= 30:
        return "low"
    if score >= 15:
        return "suspicious"
    return "inconclusive"


class VisionAntiCheatAnalyzer:
    """Stateful runner for the vision-derivable checks.

    Pure logic + injected clock: the subscriber feeds it attributed
    observations; every method returns a flag dict (same shape the
    persistence layer stores) or None. It never raises on junk input —
    a malformed observation is simply not evidence.
    """

    def __init__(
        self,
        px_per_meter: Optional[float] = None,
        clock: Optional[Any] = None,
    ):
        # Calibrated minimap scale (px per meter) when the operator has
        # configured one; None = uncalibrated (pixel-mode, low confidence).
        self.px_per_meter = px_per_meter if px_per_meter and px_per_meter > 0 else None
        self._clock = clock or time.time
        # marker identity -> (x, y, ts)
        self._last_positions: Dict[str, tuple] = {}
        # killer identity -> deque of kill timestamps
        self._kill_times: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=64)
        )
        self._kill_flagged_at: Dict[str, float] = {}
        self._global_alive: Optional[int] = None
        self._team_alive: Dict[str, int] = {}
        self._player_zero_hp: Dict[str, bool] = {}
        self.flags_raised = 0

    # ── helpers ──────────────────────────────────────────────────

    def _flag(
        self,
        check: str,
        cheat_type: str,
        score: float,
        description: str,
        evidence: Dict[str, Any],
        match_id: Optional[str] = None,
        player_id: Optional[str] = None,
        player_name: Optional[str] = None,
        team_id: Optional[str] = None,
        team_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.flags_raised += 1
        return {
            "check_name": check,
            "cheat_type": cheat_type,
            "confidence": _confidence_level(score),
            "confidence_score": round(float(score), 1),
            "description": description,
            "evidence": evidence,
            "match_id": match_id,
            "player_id": player_id,
            "player_name": player_name,
            "team_id": team_id,
            "team_name": team_name,
            "detected_at": self._clock(),
        }

    # ── check 1: impossible position jumps ───────────────────────

    def on_position(
        self,
        marker_key: str,
        x: float,
        y: float,
        ts: Optional[float] = None,
        match_id: Optional[str] = None,
        team_id: Optional[str] = None,
        team_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed one marker sighting; flag impossible jumps."""
        now = self._clock() if ts is None else float(ts)
        try:
            point = (float(x), float(y))
        except (TypeError, ValueError):
            return None
        previous = self._last_positions.get(marker_key)
        self._last_positions[marker_key] = (point[0], point[1], now)
        if previous is None:
            return None
        px, py, pts = previous
        dt = now - pts
        if dt <= 0:
            return None
        distance_px = math.hypot(point[0] - px, point[1] - py)

        if self.px_per_meter:
            distance_m = distance_px / self.px_per_meter
            speed = distance_m / dt
            if speed <= MAX_LEGIT_SPEED_MPS:
                return None
            return self._flag(
                check="impossible_position_jump",
                cheat_type="teleport",
                score=75.0,
                description=(
                    f"Marker {marker_key} moved {distance_m:.0f} m in "
                    f"{dt:.1f}s ({speed:.0f} m/s) — above the fastest "
                    f"legitimate vehicle speed (~42 m/s)."
                ),
                evidence={
                    "marker": marker_key,
                    "from_px": [px, py],
                    "to_px": [point[0], point[1]],
                    "distance_px": round(distance_px, 1),
                    "distance_m": round(distance_m, 1),
                    "elapsed_s": round(dt, 2),
                    "speed_mps": round(speed, 1),
                    "px_per_meter": self.px_per_meter,
                    "coordinate_space": "minimap_region_pixels (calibrated)",
                },
                match_id=match_id,
                team_id=team_id,
                team_name=team_name,
            )

        # Uncalibrated: only extreme pixel jumps, honestly low confidence.
        if distance_px > UNCALIBRATED_JUMP_PX and dt <= JUMP_WINDOW_S:
            return self._flag(
                check="impossible_position_jump",
                cheat_type="teleport",
                score=35.0,
                description=(
                    f"Marker {marker_key} jumped {distance_px:.0f} "
                    f"minimap pixels in {dt:.1f}s. The minimap scale is "
                    "uncalibrated, so no meter value is claimed — but "
                    "the jump spans most of the minimap widget, which "
                    "no legitimate movement explains."
                ),
                evidence={
                    "marker": marker_key,
                    "from_px": [px, py],
                    "to_px": [point[0], point[1]],
                    "distance_px": round(distance_px, 1),
                    "elapsed_s": round(dt, 2),
                    "px_per_meter": None,
                    "coordinate_space": "minimap_region_pixels (uncalibrated)",
                },
                match_id=match_id,
                team_id=team_id,
                team_name=team_name,
            )
        return None

    # ── check 2: kill-feed rate anomaly ──────────────────────────

    def on_kill(
        self,
        killer_key: str,
        ts: Optional[float] = None,
        match_id: Optional[str] = None,
        killer_name: Optional[str] = None,
        team_id: Optional[str] = None,
        team_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed one confirmed kill; flag impossible kill rates."""
        if not killer_key:
            return None
        now = self._clock() if ts is None else float(ts)
        times = self._kill_times[killer_key]
        times.append(now)
        window = [t for t in times if now - t <= 60.0]
        if len(window) <= MAX_LEGIT_KILLS_PER_MINUTE:
            return None
        # Debounce: one flag per killer per window, not per kill.
        last_flag = self._kill_flagged_at.get(killer_key, 0.0)
        if now - last_flag < 60.0:
            return None
        self._kill_flagged_at[killer_key] = now
        return self._flag(
            check="kill_feed_rate_anomaly",
            cheat_type="damage_mod",
            score=55.0,
            description=(
                f"{killer_name or killer_key} recorded {len(window)} "
                f"kills in 60s — above the maximum legitimate rate of "
                f"{MAX_LEGIT_KILLS_PER_MINUTE}/min used by the referee "
                "detector's baselines."
            ),
            evidence={
                "killer": killer_key,
                "kills_in_window": len(window),
                "window_seconds": 60,
                "kill_timestamps": [round(t, 1) for t in window],
                "max_legit_kills_per_minute": MAX_LEGIT_KILLS_PER_MINUTE,
            },
            match_id=match_id,
            player_name=killer_name,
            team_id=team_id,
            team_name=team_name,
        )

    # ── check 3: alive-count inconsistencies ─────────────────────

    def on_global_alive_count(
        self,
        players_alive: int,
        ts: Optional[float] = None,
        match_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed the match alive counter; flag increases."""
        try:
            current = int(players_alive)
        except (TypeError, ValueError):
            return None
        previous = self._global_alive
        self._global_alive = current
        if previous is None or current <= previous:
            return None
        return self._flag(
            check="alive_count_inconsistency",
            cheat_type="ghost_mode",
            score=45.0,
            description=(
                f"Match alive count rose from {previous} to {current}. "
                "Players cannot return after elimination; the cause "
                "(cheat, feed error or OCR misread) needs human review."
            ),
            evidence={
                "scope": "match",
                "previous_alive": previous,
                "current_alive": current,
            },
            match_id=match_id,
        )

    def on_team_alive_count(
        self,
        team_id: str,
        team_name: Optional[str],
        alive_count: int,
        ts: Optional[float] = None,
        match_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed one team's alive count; flag increases."""
        try:
            current = int(alive_count)
        except (TypeError, ValueError):
            return None
        previous = self._team_alive.get(team_id)
        self._team_alive[team_id] = current
        if previous is None or current <= previous:
            return None
        return self._flag(
            check="alive_count_inconsistency",
            cheat_type="ghost_mode",
            score=45.0,
            description=(
                f"Team {team_name or team_id} alive count rose from "
                f"{previous} to {current} mid-match, which cannot "
                "happen legitimately."
            ),
            evidence={
                "scope": "team",
                "team_id": team_id,
                "previous_alive": previous,
                "current_alive": current,
            },
            match_id=match_id,
            team_id=team_id,
            team_name=team_name,
        )

    def on_hp_update(
        self,
        player_key: str,
        hp_pct: Optional[float],
        ts: Optional[float] = None,
        match_id: Optional[str] = None,
        team_id: Optional[str] = None,
        team_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed one HP observation; flag HP returning from exactly 0."""
        if hp_pct is None:
            return None
        try:
            hp = float(hp_pct)
        except (TypeError, ValueError):
            return None
        was_zero = self._player_zero_hp.get(player_key, False)
        self._player_zero_hp[player_key] = hp <= 0.0
        if was_zero and hp > 0.0:
            return self._flag(
                check="alive_count_inconsistency",
                cheat_type="ghost_mode",
                score=40.0,
                description=(
                    f"Player slot {player_key} showed HP {hp:.0f}% "
                    "after previously reading exactly 0 (a dead HP "
                    "bar). Either the read was wrong or the player "
                    "returned from elimination — review the footage."
                ),
                evidence={
                    "scope": "player_hp",
                    "player_key": player_key,
                    "previous_hp_pct": 0,
                    "current_hp_pct": hp,
                },
                match_id=match_id,
                player_id=player_key,
                team_id=team_id,
                team_name=team_name,
            )
        return None

    def reset(self) -> None:
        self._last_positions.clear()
        self._kill_times.clear()
        self._kill_flagged_at.clear()
        self._global_alive = None
        self._team_alive.clear()
        self._player_zero_hp.clear()
