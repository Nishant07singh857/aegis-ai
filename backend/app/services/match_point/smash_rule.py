"""AEGIS AI — Smash Rule championship engine (Phase 20, Worker W2 — GAP-3).

Implements the full Smash Rule state machine, verified against the frame-level
evidence in ``docs/DEEP_ANALYSIS/`` (BMSD 2026 Grand Finals, Day 3):

The verbatim Day-3 fullscreen explainer (see
``docs/DEEP_ANALYSIS/india_bmsd2026_gf_verification_and_comparison.md`` §4):

    THE SMASH RULE
    - Match Point Target: After Match 12, the 1st-place team's score + 10
      points sets the Target.
    - To Win: From Match 13 onward, a team must: Reach the Match Point
      Target. Win a Chicken Dinner in a later match.
    - Important: Reaching the Target and winning the Chicken Dinner must
      happen in separate matches.
    - No Winner: If no team completes both conditions by Match 18, the team
      with the most overall points wins.

Verified live execution (BMSD GF Day 3, corrected by 3 independent sources):
target = 126 (= 8BIT's 116 + 10). SOUL reached 126 after M14 — and WON M14 —
but did NOT convert, because reach and win happened in the *same* match.
On match point from M15, SOUL could not convert (M15 K9, M16 WELT, M17 V1BA,
M18 V1BA/Blue Army). The fallback fired live: most total points after
M18 → SOUL 155, champions. The broadcast *skipped the M18 WWCD graphic
entirely* and cut straight to the trophy/celebration — that suppression is
part of this engine's output (``broadcast`` directives).

Design rules for this module:

* Every number comes from configuration (``SmashRuleConfig``). Nothing is
  hardcoded — not the target, not the target-setting match, not the bonus,
  not the final match.
* Pure Python: no DB, no framework imports. In-memory state with
  ``to_dict()`` / ``from_dict()`` round-trip hooks so a future persistence
  layer can save/load without touching the transition logic.
* State transitions are explicit and validated; illegal transitions raise
  :class:`SmashRuleTransitionError`, bad config raises
  :class:`InvalidSmashConfigError`.

States
------
``inactive``            — smash rule not configured for this tournament.
``pre_smash``           — tournament running; target not set yet.
``target_set``          — target computed; no team at/above it yet.
``match_point``         — >= 1 team at/above target; championship undecided.
``champion_converted``  — terminal: a match-point team won a later match.
``champion_fallback``   — terminal: final match done, no conversion;
                          champion = most total points.

The engine also exposes the five observed broadcast beats
(``broadcast_beats`` in the snapshot) so the graphics/director workers know
which beat is due: explainer → race-to-smash chart → leader highlight →
scorebug → fallback ceremony.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

#: All engine states.
STATES: tuple[str, ...] = (
    "inactive",
    "pre_smash",
    "target_set",
    "match_point",
    "champion_converted",
    "champion_fallback",
)

#: Terminal states — no further match results are accepted.
TERMINAL_STATES: tuple[str, ...] = ("champion_converted", "champion_fallback")

#: The five broadcast beats observed live on BMSD GF Day 3
#: (docs/DEEP_ANALYSIS — GAP-3). The engine reports each beat's status so the
#: frontend/director layer knows what to put on screen.
BROADCAST_BEATS: tuple[str, ...] = (
    "explainer",            # fullscreen SMASH RULE explainer (pre-show)
    "race_to_smash_chart",  # "SMASH POINT – N / REQUIRED POINTS" bar chart (breaks)
    "leader_highlight",     # green highlight on the smash-point leader's row
    "scorebug",             # smash scorebug strip over the player-cam mosaic
    "fallback_ceremony",    # points-fallback championship ceremony variant
)


class InvalidSmashConfigError(ValueError):
    """Raised when smash-rule configuration is missing or invalid."""


class SmashRuleTransitionError(RuntimeError):
    """Raised when a match result cannot be applied to the current state."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class SmashRuleConfig:
    """Validated smash-rule configuration.

    All values come from the caller (tournament ``rules`` JSONB, operator
    input, or environment) — the engine never invents them.

    ``target_set_after_match``  match after which the target is set
                                (BMSD 2026: 12).
    ``target_bonus_points``     bonus added to the leader's score to set the
                                target (BMSD 2026: 10 → 116 + 10 = 126).
    ``final_match_number``      last match of the tournament; the fallback
                                fires when it completes without conversion
                                (BMSD 2026: 18).
    ``chickens_after_match_point`` WWCDs needed after hitting match point
                                (kept for compatibility with the documented
                                ``rules.smash`` schema; the observed rule is
                                one chicken in a *later* match).
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        target_set_after_match: int,
        target_bonus_points: int,
        final_match_number: int,
        chickens_after_match_point: int = 1,
    ) -> None:
        self.enabled = bool(enabled)
        self.target_set_after_match = target_set_after_match
        self.target_bonus_points = target_bonus_points
        self.final_match_number = final_match_number
        self.chickens_after_match_point = chickens_after_match_point
        self._validate()

    def _validate(self) -> None:
        for name in (
            "target_set_after_match",
            "target_bonus_points",
            "final_match_number",
            "chickens_after_match_point",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise InvalidSmashConfigError(
                    f"smash config {name!r} must be an integer, got {value!r}."
                )
        if self.target_set_after_match < 1:
            raise InvalidSmashConfigError(
                "smash config 'target_set_after_match' must be >= 1."
            )
        if self.target_bonus_points < 0:
            raise InvalidSmashConfigError(
                "smash config 'target_bonus_points' must be >= 0."
            )
        if self.final_match_number <= self.target_set_after_match:
            raise InvalidSmashConfigError(
                "smash config 'final_match_number' must be greater than "
                "'target_set_after_match' (there must be at least one "
                "smash-window match)."
            )
        if self.chickens_after_match_point < 1:
            raise InvalidSmashConfigError(
                "smash config 'chickens_after_match_point' must be >= 1."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "target_set_after_match": self.target_set_after_match,
            "target_bonus_points": self.target_bonus_points,
            "final_match_number": self.final_match_number,
            "chickens_after_match_point": self.chickens_after_match_point,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SmashRuleConfig":
        try:
            return cls(
                enabled=data.get("enabled", True),
                target_set_after_match=data["target_set_after_match"],
                target_bonus_points=data["target_bonus_points"],
                final_match_number=data["final_match_number"],
                chickens_after_match_point=data.get(
                    "chickens_after_match_point", 1
                ),
            )
        except KeyError as exc:
            raise InvalidSmashConfigError(
                f"smash config missing required key: {exc}."
            ) from exc


class SmashRuleEngine:
    """In-memory Smash Rule championship state machine.

    Drive it with :meth:`configure` once, then :meth:`record_match_result`
    after every completed match. Read everything from :meth:`snapshot`.
    """

    def __init__(self) -> None:
        self._config: Optional[SmashRuleConfig] = None
        self._reset_runtime()

    # ── configuration ────────────────────────────────────────────

    def configure(self, config: SmashRuleConfig) -> Dict[str, Any]:
        """(Re)configure the engine for a tournament. Resets all runtime
        state — a reconfigure is a fresh tournament as far as the engine is
        concerned."""
        self._config = config
        self._reset_runtime()
        return self.snapshot()

    def _reset_runtime(self) -> None:
        self._state: str = (
            "pre_smash" if (self._config and self._config.enabled) else "inactive"
        )
        self._target: Optional[int] = None
        self._target_set_from_match: Optional[int] = None
        self._target_set_from_leader: Optional[Dict[str, Any]] = None
        # team_id -> {"team_id","team_name","total_points","reached_after_match","beats":[...]}
        self._match_point_teams: Dict[str, Dict[str, Any]] = {}
        self._matches_recorded: int = 0
        self._last_match_number: Optional[int] = None
        self._champion: Optional[Dict[str, Any]] = None
        self._broadcast: Dict[str, Any] = self._default_broadcast()
        self._history: List[Dict[str, Any]] = []
        self._updated_at: str = _utcnow_iso()

    @staticmethod
    def _default_broadcast() -> Dict[str, Any]:
        return {
            "suppress_wwcd_graphic": False,
            "jump_to_championship_package": False,
            "ceremony_variant": None,  # "conversion" | "fallback" | None
            "note": "No championship outcome yet.",
        }

    # ── transitions ──────────────────────────────────────────────

    def record_match_result(
        self,
        match_number: int,
        winner_team_id: Optional[str],
        standings: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply a completed match and advance the state machine.

        ``match_number``      1-based match number; must strictly increase.
        ``winner_team_id``    team that won the chicken dinner (may be None
                              when the winner is not identifiable — then no
                              conversion is possible for this match).
        ``standings``         cumulative standings *after* this match, each
                              ``{"team_id", "team_name"?, "total_points"?,
                              "chickens"?, "finishes"?}``.

        Returns the transition record (also appended to history and
        reflected in :meth:`snapshot`).
        """
        if self._config is None or not self._config.enabled:
            raise SmashRuleTransitionError(
                "Smash rule is not configured (state=inactive). Call "
                "configure() with a valid SmashRuleConfig first."
            )
        if self._state in TERMINAL_STATES:
            raise SmashRuleTransitionError(
                f"Champion already decided ({self._state}); no further match "
                "results are accepted."
            )
        if not isinstance(match_number, int) or isinstance(match_number, bool):
            raise SmashRuleTransitionError(
                f"match_number must be an integer, got {match_number!r}."
            )
        if (
            self._last_match_number is not None
            and match_number <= self._last_match_number
        ):
            raise SmashRuleTransitionError(
                f"match_number must strictly increase (last recorded: "
                f"{self._last_match_number}, got {match_number})."
            )
        if not standings:
            raise SmashRuleTransitionError(
                "standings must be a non-empty list to evaluate a match."
            )

        cfg = self._config
        transition: Dict[str, Any] = {
            "match_number": match_number,
            "winner_team_id": winner_team_id,
            "from_state": self._state,
            "events": [],
            "recorded_at": _utcnow_iso(),
        }

        # 1. Set the target once we reach the configured match.
        #    Target = 1st-place team's score after that match + bonus
        #    (BMSD 2026: after M12, 8BIT 116 + 10 = 126).
        if self._state == "pre_smash" and match_number >= cfg.target_set_after_match:
            leader = max(standings, key=lambda row: _num(row.get("total_points")))
            leader_score = int(_num(leader.get("total_points")))
            self._target = leader_score + cfg.target_bonus_points
            self._target_set_from_match = match_number
            self._target_set_from_leader = {
                "team_id": leader.get("team_id"),
                "team_name": leader.get("team_name"),
                "score": leader_score,
            }
            self._state = "target_set"
            transition["events"].append(
                {
                    "type": "target_set",
                    "target": self._target,
                    "leader_team_id": leader.get("team_id"),
                    "leader_score": leader_score,
                    "bonus": cfg.target_bonus_points,
                }
            )

        # 2. Match-point detection: any team at/above the target.
        newly_on_match_point: List[str] = []
        if self._target is not None:
            for row in standings:
                team_id = row.get("team_id")
                if team_id is None:
                    continue
                total = _num(row.get("total_points"))
                if total >= self._target and team_id not in self._match_point_teams:
                    self._match_point_teams[team_id] = {
                        "team_id": team_id,
                        "team_name": row.get("team_name"),
                        "total_points": total,
                        "reached_after_match": match_number,
                        "beats": [],
                    }
                    newly_on_match_point.append(team_id)
                    transition["events"].append(
                        {
                            "type": "match_point_reached",
                            "team_id": team_id,
                            "team_name": row.get("team_name"),
                            "total_points": total,
                            "after_match": match_number,
                        }
                    )
                elif team_id in self._match_point_teams:
                    # Keep the tracked totals fresh as the tournament progresses.
                    self._match_point_teams[team_id]["total_points"] = total
                    if row.get("team_name"):
                        self._match_point_teams[team_id]["team_name"] = row.get(
                            "team_name"
                        )
            if self._match_point_teams and self._state == "target_set":
                self._state = "match_point"

        # 3. Conversion: a match-point team wins a chicken dinner in a match
        #    AFTER the one where it reached the target ("reaching the Target
        #    and winning the Chicken Dinner must happen in separate matches").
        converted: Optional[Dict[str, Any]] = None
        if winner_team_id and winner_team_id in self._match_point_teams:
            record = self._match_point_teams[winner_team_id]
            if record["reached_after_match"] < match_number:
                converted = {
                    "team_id": winner_team_id,
                    "team_name": record.get("team_name"),
                    "total_points": record.get("total_points"),
                    "decided_by": "conversion",
                    "decided_at_match": match_number,
                    "reached_after_match": record["reached_after_match"],
                }
            else:
                # Same-match reach + win does NOT convert (observed: SOUL won
                # M14 and hit 126 in M14, but converted nothing — on match
                # point from M15).
                transition["events"].append(
                    {
                        "type": "same_match_reach_and_win",
                        "team_id": winner_team_id,
                        "note": (
                            "Winner reached the target in this same match; "
                            "conversion requires a chicken dinner in a later "
                            "match. Team is now on match point."
                        ),
                    }
                )

        if converted is not None:
            self._state = "champion_converted"
            self._champion = converted
            self._broadcast = {
                "suppress_wwcd_graphic": False,
                "jump_to_championship_package": False,
                "ceremony_variant": "conversion",
                "note": (
                    f"{converted.get('team_name') or converted['team_id']} "
                    "converted match point — WWCD graphic airs, then the "
                    "championship ceremony."
                ),
            }
            transition["events"].append(
                {"type": "champion_converted", "champion": copy.deepcopy(converted)}
            )
        else:
            # 4. Beat progression: every match-point team that reached in an
            #    earlier match observes one more "beat" without converting.
            for team_id, record in self._match_point_teams.items():
                if record["reached_after_match"] < match_number:
                    record["beats"].append(match_number)

            # 5. Fallback: final match done and nobody converted → champion =
            #    most total points ("If no team completes both conditions by
            #    Match 18, the team with the most overall points wins.").
            #    This also covers the edge case where no team ever reached
            #    match point: then no team completed both conditions either.
            if match_number >= cfg.final_match_number:
                champion_row = self._pick_points_champion(standings)
                decided_by = (
                    "fallback"
                    if self._match_point_teams
                    else "fallback_no_match_point"
                )
                self._champion = {
                    "team_id": champion_row.get("team_id"),
                    "team_name": champion_row.get("team_name"),
                    "total_points": _num(champion_row.get("total_points")),
                    "decided_by": decided_by,
                    "decided_at_match": match_number,
                }
                self._state = "champion_fallback"
                self._broadcast = {
                    "suppress_wwcd_graphic": True,
                    "jump_to_championship_package": True,
                    "ceremony_variant": "fallback",
                    "note": (
                        "No match-point team converted — WWCD graphic is "
                        "SUPPRESSED, cut straight to the trophy/celebration "
                        "championship package (observed live on BMSD Day 3)."
                    ),
                }
                transition["events"].append(
                    {
                        "type": "champion_fallback",
                        "champion": copy.deepcopy(self._champion),
                        "match_point_teams_unconverted": sorted(
                            self._match_point_teams.keys()
                        ),
                    }
                )

        transition["to_state"] = self._state
        self._matches_recorded += 1
        self._last_match_number = match_number
        self._updated_at = _utcnow_iso()
        self._history.append(transition)
        return copy.deepcopy(transition)

    # ── fallback champion selection ──────────────────────────────

    @staticmethod
    def _pick_points_champion(
        standings: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Most total points wins. Deterministic tie-breakers (AEGIS-defined;
        the observed rule only specifies "most overall points"):

        1. most total_points
        2. most chickens (chicken dinners)
        3. most finishes
        4. team_id ascending (final deterministic tie-break)
        """
        def sort_key(row: Dict[str, Any]) -> tuple:
            return (
                -_num(row.get("total_points")),
                -_num(row.get("chickens")),
                -_num(row.get("finishes")),
                str(row.get("team_id") or ""),
            )

        return min(standings, key=sort_key)

    # ── reads ────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Full engine state (safe to send to any viewer / over WS)."""
        teams = sorted(
            self._match_point_teams.values(),
            key=lambda t: (-t["total_points"], str(t["team_id"] or "")),
        )
        teams_out = [
            {
                "team_id": t["team_id"],
                "team_name": t["team_name"],
                "total_points": t["total_points"],
                "reached_after_match": t["reached_after_match"],
                "beats": list(t["beats"]),
                "beats_count": len(t["beats"]),
            }
            for t in teams
        ]
        return {
            "state": self._state,
            "config": self._config.to_dict() if self._config else None,
            "target": self._target,
            "target_set_after_match": (
                self._config.target_set_after_match if self._config else None
            ),
            "target_set_from_match": self._target_set_from_match,
            "target_set_from_leader": copy.deepcopy(self._target_set_from_leader),
            "match_point_teams": teams_out,
            "matches_recorded": self._matches_recorded,
            "last_match_number": self._last_match_number,
            "champion": copy.deepcopy(self._champion),
            "broadcast": copy.deepcopy(self._broadcast),
            "broadcast_beats": self._broadcast_beats(),
            "updated_at": self._updated_at,
        }

    def _broadcast_beats(self) -> Dict[str, Dict[str, str]]:
        """Status of each of the five observed broadcast beats.

        ``due``    — should be on screen / in the rundown now.
        ``active`` — currently live on the broadcast.
        ``done``   — already aired / fired.
        ``n/a``    — not applicable in the current state.
        """
        state = self._state
        beats: Dict[str, Dict[str, str]] = {}
        if state == "inactive":
            statuses = {b: "n/a" for b in BROADCAST_BEATS}
        else:
            statuses = {
                # Fullscreen explainer: pre-show beat, due before the target
                # is set, done once the smash window opens.
                "explainer": (
                    "due" if state == "pre_smash" else "done"
                ),
                # RACE TO SMASH bar chart: break beat once the target exists.
                "race_to_smash_chart": (
                    "due"
                    if state in ("target_set", "match_point")
                    else ("done" if state in TERMINAL_STATES else "n/a")
                ),
                # Green leader highlight + scorebug: live while teams are on
                # match point.
                "leader_highlight": (
                    "active" if state == "match_point" else (
                        "done" if state in TERMINAL_STATES else "n/a"
                    )
                ),
                "scorebug": (
                    "active" if state == "match_point" else (
                        "done" if state in TERMINAL_STATES else "n/a"
                    )
                ),
                # Fallback ceremony: only the fallback path has this beat.
                "fallback_ceremony": (
                    "done"
                    if state == "champion_fallback"
                    else "n/a"
                ),
            }
        labels = {
            "explainer": "Fullscreen SMASH RULE explainer",
            "race_to_smash_chart": "RACE TO SMASH bar chart (breaks)",
            "leader_highlight": "Green smash-leader row highlight",
            "scorebug": "Smash scorebug strip (player-cam mosaic)",
            "fallback_ceremony": "Points-fallback championship ceremony",
        }
        for beat in BROADCAST_BEATS:
            beats[beat] = {"status": statuses[beat], "label": labels[beat]}
        return beats

    def history(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self._history)

    # ── persistence hooks ────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the full engine (config + runtime) for persistence."""
        return {
            "version": 1,
            "config": self._config.to_dict() if self._config else None,
            "state": self._state,
            "target": self._target,
            "target_set_from_match": self._target_set_from_match,
            "target_set_from_leader": copy.deepcopy(self._target_set_from_leader),
            "match_point_teams": copy.deepcopy(self._match_point_teams),
            "matches_recorded": self._matches_recorded,
            "last_match_number": self._last_match_number,
            "champion": copy.deepcopy(self._champion),
            "broadcast": copy.deepcopy(self._broadcast),
            "history": copy.deepcopy(self._history),
            "updated_at": self._updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SmashRuleEngine":
        """Restore an engine previously serialized with :meth:`to_dict`."""
        engine = cls()
        raw_config = data.get("config")
        if raw_config is not None:
            engine._config = SmashRuleConfig.from_dict(raw_config)
        engine._state = data.get("state", "inactive")
        if engine._state not in STATES:
            raise InvalidSmashConfigError(
                f"Unknown smash-rule state {engine._state!r} in saved data."
            )
        engine._target = data.get("target")
        engine._target_set_from_match = data.get("target_set_from_match")
        engine._target_set_from_leader = data.get("target_set_from_leader")
        engine._match_point_teams = copy.deepcopy(data.get("match_point_teams", {}))
        engine._matches_recorded = int(data.get("matches_recorded", 0) or 0)
        engine._last_match_number = data.get("last_match_number")
        engine._champion = data.get("champion")
        engine._broadcast = data.get("broadcast") or engine._default_broadcast()
        engine._history = copy.deepcopy(data.get("history", []))
        engine._updated_at = data.get("updated_at") or _utcnow_iso()
        return engine
