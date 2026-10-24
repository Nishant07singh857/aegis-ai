# backend/app/services/observer_pilot/rules.py
"""Phase 29 (W2) — POV Pilot rules engine: pure deterministic logic.

One question, answered per observer feed: *the camera inside this feed
is on player X — should it move, to whom, and why?*

This module is PURE LOGIC. No vision, no ADB, no I/O, no clock reads:
every timestamp arrives as an input (``PilotSignals.now``,
``last_tap_at``, ``last_fight_signal_at``), so every decision is
reproducible in a unit test and explainable after the fact. The engine
is STATELESS — per-feed history (last tap, last fight signal) is passed
in by the caller (W4's pilot service) on every call; nothing is stored
between calls.

Shared contract
---------------
Input types come from ``.types`` (W1 owns that module:
``POVPlayerStatus`` / ``PlayerPOV`` / ``FeedPOVState``, exactly the
brief's contract shapes). ``POVDecision`` is defined in THIS module —
W1's landed ``types.py`` does not define it, and the decision is the
rules engine's own output type. If ``types.py`` has not landed yet,
this module falls back to local definitions with the IDENTICAL
shapes so the engine and its tests run standalone; once W1's module
exists it is used and the fallback is dead code. The fallback is
removed at final integration if it ever diverges — the contract in
``types.py`` wins for the input types.

Rule order (first match wins) — R0, R1, R3b, R4, R2, R3, R5
------------------------------------------------------
The brief's natural listing is R0–R5, but evaluation order differs in
one place, deliberately:

* R0 NO-EVIDENCE first: unknown identity / unknown spectated player /
  UNKNOWN status means NO action, ever. Guessing a camera move from an
  unread screen is the one unforgivable pilot error.
* R1 SPECTATED_DEAD before the fight lock: a dead player's camera is
  useless even mid-fight, and this is the ONLY rule that bypasses the
  dwell gate.
* R3b SOLO_LOCK (Phase 31B) after R1, before R4: the spectated player
  is the team's lone survivor in an active fight — the camera locks
  on them until the fight ends or they die. It precedes R4 because it
  is the more specific lock (it also holds a lone survivor who is
  repositioning without a fresh personal fight signal, whom R4 alone
  would not lock), and because its note names the reason. Death
  releases it via R1 (evaluated first); fight end releases it because
  the rule re-evaluates statelessly on every call.
* R4 FIGHT_LOCK before R2/R3: if the spectated player is themselves
  fighting, the camera never moves — checking this before the
  "teammate in fight" rules prevents the quiet-check from being read
  backwards (a fighting incumbent is never "quiet"). R4 applies only
  to an ALIVE spectated player: a KNOCKED player may carry a fresh
  "victim" killfeed entry, and counting that as "fighting" would lock
  the camera onto a knocked player forever and R2 could never fire.
* R2 SPECTATED_KNOCKED, then R3 TEAMMATE_IN_FIGHT, then R5
  RETURN_PRIMARY (calmest rule last).

Director-grammar consistency (Phase 25/27)
------------------------------------------
The pilot operates INSIDE one feed; the LiveDirector operates BETWEEN
feeds. They share one grammar instinct: do not thrash during a fight.
The director's ``min_fight_hold_s(phase)`` (30 s loot/early, 45 s
mid_game, 60 s late_game, locked in final_circle) holds a PROGRAM cut;
the pilot's dwell (default 9 s, doubled in the final zone) is the
intra-feed equivalent at POV scale — shorter, because a POV tap inside
an already-selected feed is a smaller editorial act than a program
cut, but phase-sensitive for the same reason: late-game POV churn is
how a director loses the fight they were holding. Phase names are the
LiveDirector/PhaseTracker vocabulary (``final_circle``); the brief's
``final_zone`` spelling is accepted as an alias for the same phase.

Mode policy is NOT implemented here — this engine only decides. Who
may execute a decision (full_auto/clone auto-execute, semi_auto
one-tap approval, manual shadow-only, kill-switch blocks everything)
is W4's contract and is documented in docs/OBSERVER_AUTO_PILOT.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# ── Shared contract types (W1: .types) with an identical-shape
# fallback so this module is importable before types.py lands. ──────
# Integration note (verified against W1's landed types.py): W1's
# module defines POVPlayerStatus / PlayerPOV / FeedPOVState but NOT
# POVDecision — the decision type is the rules engine's own output,
# so POVDecision is defined HERE (exact brief-contract shape) and
# re-exported for W4. The three input types are imported from .types
# whenever that module exists.
try:  # pragma: no cover - exercised by whichever tree state exists
    from .types import FeedPOVState, PlayerPOV, POVPlayerStatus  # noqa: F401

    _TYPES_FROM_W1 = True
except Exception:  # pragma: no cover - fallback until W1 lands
    _TYPES_FROM_W1 = False

    class POVPlayerStatus(str, Enum):
        ALIVE = "alive"
        KNOCKED = "knocked"
        DEAD = "dead"
        UNKNOWN = "unknown"

    @dataclass
    class PlayerPOV:
        roster_index: int  # 0..3
        player_id: Optional[str]
        name: Optional[str]
        status: POVPlayerStatus
        hp_fraction: Optional[float]
        hp_velocity: Optional[float]  # negative = HP falling
        is_spectated: bool

    @dataclass
    class FeedPOVState:
        slot_number: int
        source_id: str
        team_id: Optional[str]
        observed_at: float
        spectated_index: Optional[int]  # None = UNKNOWN
        players: Tuple[PlayerPOV, ...]
        identity_known: bool


@dataclass
class POVDecision:
    """The pilot's output (brief contract shape, exact field order).

    Defined in this module because W1's types.py does not carry it;
    W4 imports it from here (or from .types if a later integration
    moves it there — fields must stay identical either way).
    """

    slot_number: int
    source_id: str
    from_index: Optional[int]
    to_index: int
    rule: str
    reason: str
    evidence: Dict[str, Any]
    decided_at: float


# ── Rule names (stable strings — W4 logs / status surfaces use them) ─
RULE_NO_EVIDENCE = "R0_NO_EVIDENCE"
RULE_SPECTATED_DEAD = "R1_SPECTATED_DEAD"
RULE_SPECTATED_KNOCKED = "R2_SPECTATED_KNOCKED"
RULE_TEAMMATE_IN_FIGHT = "R3_TEAMMATE_IN_FIGHT"
RULE_FIGHT_LOCK = "R4_FIGHT_LOCK"
RULE_RETURN_PRIMARY = "R5_RETURN_PRIMARY"
#: Phase 31B: spectated lone survivor in an active fight — lock on.
RULE_SOLO_LOCK = "R3b_SOLO_LOCK"

#: Phase spellings that count as the final zone for the dwell
#: multiplier. ``final_circle`` is the canonical PhaseTracker /
#: LiveDirector name; ``final_zone`` is the Phase 29 brief's spelling
#: for the same phase and is accepted as an alias.
FINAL_ZONE_PHASES = frozenset({"final_circle", "final_zone", "final"})

#: Killfeed kinds that count for evidence-priority tier 1 (see
#: ``_strongest_fighting``). Any kind counts as fight INVOLVEMENT;
#: knocked/killer are the kinds that mark a player as driving the
#: fight rather than only receiving it.
_PRIORITY_KILLFEED_KINDS = frozenset({"knocked", "killer"})


@dataclass
class PilotConfig:
    """Every pilot threshold, in ONE place (nothing scattered).

    Defaults are the Phase 29 brief's values. These are editorial
    tunables, not measurements — no accuracy claim attaches to them.
    """

    #: Minimum seconds between two pilot taps on the same feed.
    dwell_s: float = 9.0
    #: HP velocity at/below which a player counts as fighting
    #: (fraction-of-bar per second; negative = HP falling).
    hp_drop_velocity: float = -0.05
    #: A fight signal counts as "fresh" inside this window; a player
    #: with no fresh signal and no HP drop is "quiet".
    quiet_window_s: float = 6.0
    #: After the whole team has been quiet this long, the pilot may
    #: return the camera to the primary player.
    return_primary_delay_s: float = 10.0
    #: Dwell multiplier in the final zone (calmer camera late-game,
    #: mirroring the director's longer fight-holds by phase).
    final_zone_dwell_multiplier: float = 2.0
    #: Roster index of the primary player (default 0 = first in roster
    #: order, i.e. the IGL slot by roster convention).
    primary_roster_index: int = 0


@dataclass
class PilotSignals:
    """Per-call signals the rules consume (filled by W4's wiring).

    ``killfeed_involvements`` shape is a LIST of
    ``(roster_index, kind, at)`` tuples, kind in
    ``"killer" | "victim" | "knocked"`` — the monitor's events for
    THIS feed's team only. ``fight_active_on_feed`` is the team-level
    fight flag (LiveDirector engagement); it is evidence, never a
    target selector — a target is only ever chosen from per-player
    evidence. ``phase`` uses the LiveDirector phase vocabulary.
    ``now`` is the injected clock for this evaluation.
    """

    killfeed_involvements: List[Tuple[int, str, float]] = field(
        default_factory=list
    )
    fight_active_on_feed: bool = False
    phase: str = "unknown"
    now: float = 0.0


@dataclass
class PilotNote:
    """Why the pilot did NOT move a feed (the no-action counterpart
    of POVDecision). W4 surfaces this on the pilot status view so a
    still camera is always explained, never silent."""

    slot_number: int
    source_id: str
    reason: str
    blocked_rule: Optional[str]  # rule that WOULD have fired, if any
    evidence: Dict[str, Any]
    at: float


def is_final_zone(phase: Optional[str]) -> bool:
    """True when ``phase`` names the final zone (either spelling)."""
    normalised = (phase or "").strip().lower().replace("-", "_").replace(" ", "_")
    return normalised in FINAL_ZONE_PHASES


def _label(player: PlayerPOV) -> str:
    """``Name (idx N)`` when a name is known, else an honest roster
    label — a name is never invented for the reason string."""
    if player.name:
        return f"{player.name} (idx {player.roster_index})"
    return f"player at roster index {player.roster_index}"


class POVPilotRules:
    """Stateless POV pilot. One instance = one PilotConfig.

    ``decide()`` is the brief's entry point (decision or None).
    ``decide_with_note()`` additionally returns a :class:`PilotNote`
    explaining a no-action outcome; ``explain_no_action()`` is its
    string-only convenience form for status surfaces.
    """

    def __init__(self, config: Optional[PilotConfig] = None):
        self.config = config or PilotConfig()

    # ── public API ───────────────────────────────────────────────

    def decide(
        self,
        state: FeedPOVState,
        signals: PilotSignals,
        last_tap_at: Optional[float],
        last_fight_signal_at: Optional[float],
    ) -> Optional[POVDecision]:
        """First matching rule's decision, or None (see module
        docstring for the order and each rule's contract)."""
        decision, _note = self._evaluate(
            state, signals, last_tap_at, last_fight_signal_at
        )
        return decision

    def decide_with_note(
        self,
        state: FeedPOVState,
        signals: PilotSignals,
        last_tap_at: Optional[float],
        last_fight_signal_at: Optional[float],
    ) -> Tuple[Optional[POVDecision], Optional[PilotNote]]:
        """(decision, None) when a rule fires, else (None, note)
        explaining why not — including dwell blocks, with the numbers
        that blocked it."""
        return self._evaluate(state, signals, last_tap_at, last_fight_signal_at)

    def explain_no_action(
        self,
        state: FeedPOVState,
        signals: PilotSignals,
        last_tap_at: Optional[float],
        last_fight_signal_at: Optional[float],
    ) -> str:
        """Human-readable no-action explanation for status surfaces.
        If a decision IS available, says so and names its rule."""
        decision, note = self._evaluate(
            state, signals, last_tap_at, last_fight_signal_at
        )
        if decision is not None:
            return (
                f"Action available via {decision.rule}: {decision.reason}"
            )
        assert note is not None  # _evaluate always pairs decision/note
        return note.reason

    def effective_dwell_s(self, phase: Optional[str]) -> float:
        """Dwell for a phase: base dwell, multiplied in final zone."""
        if is_final_zone(phase):
            return self.config.dwell_s * self.config.final_zone_dwell_multiplier
        return self.config.dwell_s

    # ── signal helpers ───────────────────────────────────────────

    def _killfeed_for(
        self, signals: PilotSignals, roster_index: int
    ) -> List[Tuple[str, float]]:
        """(kind, at) involvements for one roster index, malformed
        entries ignored — a bad row is absent evidence, never a guess."""
        out: List[Tuple[str, float]] = []
        for entry in signals.killfeed_involvements or []:
            try:
                idx, kind, at = entry
                if int(idx) == roster_index:
                    out.append((str(kind), float(at)))
            except (TypeError, ValueError):
                continue
        return out

    def _fresh_killfeed(
        self, signals: PilotSignals, roster_index: int
    ) -> List[Tuple[str, float]]:
        """Involvements inside the quiet window (fresh fight signal)."""
        window = self.config.quiet_window_s
        now = signals.now
        return [
            (kind, at)
            for kind, at in self._killfeed_for(signals, roster_index)
            if now - at <= window
        ]

    def _is_fighting(self, player: PlayerPOV, signals: PilotSignals) -> bool:
        """"Fighting" = a fresh (<= quiet_window_s) killfeed
        involvement, OR hp_velocity at/below the drop threshold.
        hp_velocity None means that signal is ABSENT — never guessed."""
        if self._fresh_killfeed(signals, player.roster_index):
            return True
        velocity = player.hp_velocity
        return velocity is not None and velocity <= self.config.hp_drop_velocity

    def _strongest_fighting(
        self, candidates: List[PlayerPOV], signals: PilotSignals
    ) -> Optional[PlayerPOV]:
        """Pick the strongest-signal fighting teammate.

        Evidence priority (deterministic):
        1. Freshest knocked/killer killfeed involvement.
        2. Most negative hp_velocity (fastest HP loss); a missing
           velocity ranks below any measured one on this tier.
        3. Lowest roster_index (exact tie-break).

        Proximity is NOT a tier: no per-player proximity evidence is
        available from the spectator screen, so it is never faked —
        see docs/OBSERVER_AUTO_PILOT.md.
        """
        if not candidates:
            return None

        def key(player: PlayerPOV) -> Tuple[int, float, float, int]:
            priority_fresh = [
                at
                for kind, at in self._fresh_killfeed(
                    signals, player.roster_index
                )
                if kind in _PRIORITY_KILLFEED_KINDS
            ]
            if priority_fresh:
                tier1 = (0, -max(priority_fresh))
            else:
                tier1 = (1, 0.0)
            velocity = (
                player.hp_velocity
                if player.hp_velocity is not None
                else float("inf")
            )
            return (tier1[0], tier1[1], velocity, player.roster_index)

        return sorted(candidates, key=key)[0]

    def _dwell_block(
        self,
        state: FeedPOVState,
        signals: PilotSignals,
        last_tap_at: Optional[float],
        rule: str,
    ) -> Optional[PilotNote]:
        """A PilotNote when the dwell gate blocks ``rule``, else None.
        R1 never calls this — it is the one dwell bypass."""
        if last_tap_at is None:
            return None
        effective = self.effective_dwell_s(signals.phase)
        elapsed = signals.now - last_tap_at
        if elapsed >= effective:
            return None
        return PilotNote(
            slot_number=state.slot_number,
            source_id=state.source_id,
            reason=(
                f"{rule} would move the camera, but the dwell gate "
                f"blocks it: {elapsed:.1f}s elapsed since the last "
                f"pilot tap, {effective:.1f}s required "
                f"(phase={signals.phase})."
            ),
            blocked_rule=rule,
            evidence={
                "dwell_s": self.config.dwell_s,
                "effective_dwell_s": effective,
                "final_zone": is_final_zone(signals.phase),
                "last_tap_at": last_tap_at,
                "elapsed_s": elapsed,
                "now": signals.now,
            },
            at=signals.now,
        )

    def _note(
        self,
        state: FeedPOVState,
        signals: PilotSignals,
        reason: str,
        blocked_rule: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> PilotNote:
        return PilotNote(
            slot_number=state.slot_number,
            source_id=state.source_id,
            reason=reason,
            blocked_rule=blocked_rule,
            evidence=evidence or {},
            at=signals.now,
        )

    def _decision(
        self,
        state: FeedPOVState,
        target: PlayerPOV,
        rule: str,
        reason: str,
        evidence: Dict[str, Any],
        signals: PilotSignals,
    ) -> POVDecision:
        return POVDecision(
            slot_number=state.slot_number,
            source_id=state.source_id,
            from_index=state.spectated_index,
            to_index=target.roster_index,
            rule=rule,
            reason=reason,
            evidence=evidence,
            decided_at=signals.now,
        )

    def _base_evidence(
        self,
        state: FeedPOVState,
        signals: PilotSignals,
        spectated: PlayerPOV,
        last_tap_at: Optional[float],
    ) -> Dict[str, Any]:
        return {
            "slot_number": state.slot_number,
            "team_id": state.team_id,
            "phase": signals.phase,
            "fight_active_on_feed": signals.fight_active_on_feed,
            "spectated_index": state.spectated_index,
            "spectated_status": spectated.status.value
            if isinstance(spectated.status, Enum)
            else str(spectated.status),
            "spectated_hp_velocity": spectated.hp_velocity,
            "players": [
                {
                    "roster_index": p.roster_index,
                    "name": p.name,
                    "status": p.status.value
                    if isinstance(p.status, Enum)
                    else str(p.status),
                    "hp_fraction": p.hp_fraction,
                    "hp_velocity": p.hp_velocity,
                }
                for p in state.players
            ],
            "killfeed_involvements": [
                {"roster_index": idx, "kind": kind, "at": at}
                for idx, kind, at in (signals.killfeed_involvements or [])
            ],
            "dwell_s": self.config.dwell_s,
            "effective_dwell_s": self.effective_dwell_s(signals.phase),
            "last_tap_at": last_tap_at,
        }

    # ── the engine ───────────────────────────────────────────────

    def _evaluate(
        self,
        state: FeedPOVState,
        signals: PilotSignals,
        last_tap_at: Optional[float],
        last_fight_signal_at: Optional[float],
    ) -> Tuple[Optional[POVDecision], Optional[PilotNote]]:
        cfg = self.config
        now = signals.now

        # ── R0 NO-EVIDENCE ───────────────────────────────────────
        # Unknown identity, unknown spectated player, or a spectated
        # status of UNKNOWN: no action, ever. Candidate-side UNKNOWN
        # is enforced per rule below (only ALIVE is ever a target).
        if not state.identity_known:
            return None, self._note(
                state, signals,
                "No action: feed identity is not known "
                "(identity_known=False); the pilot never acts on an "
                "unidentified feed.",
                blocked_rule=RULE_NO_EVIDENCE,
                evidence={"identity_known": False},
            )
        if state.spectated_index is None:
            return None, self._note(
                state, signals,
                "No action: the spectated player is unknown "
                "(spectated_index=None); there is no verified camera "
                "position to move from.",
                blocked_rule=RULE_NO_EVIDENCE,
                evidence={"spectated_index": None},
            )
        by_index: Dict[int, PlayerPOV] = {
            p.roster_index: p for p in state.players
        }
        spectated = by_index.get(state.spectated_index)
        if spectated is None:
            return None, self._note(
                state, signals,
                f"No action: spectated_index {state.spectated_index} "
                "is not present in the feed's player list; the POV "
                "state is incomplete.",
                blocked_rule=RULE_NO_EVIDENCE,
                evidence={"spectated_index": state.spectated_index},
            )
        if spectated.status == POVPlayerStatus.UNKNOWN:
            return None, self._note(
                state, signals,
                f"No action: the status of spectated "
                f"{_label(spectated)} is UNKNOWN; the pilot never "
                "acts on an unread player state.",
                blocked_rule=RULE_NO_EVIDENCE,
                evidence={"spectated_status": "unknown"},
            )

        base = self._base_evidence(state, signals, spectated, last_tap_at)
        ordered = sorted(state.players, key=lambda p: p.roster_index)

        # ── R1 SPECTATED_DEAD (dwell BYPASS) ─────────────────────
        if spectated.status == POVPlayerStatus.DEAD:
            target = self._next_alive_after(ordered, spectated.roster_index)
            if target is None:
                return None, self._note(
                    state, signals,
                    f"No action: spectated {_label(spectated)} is "
                    "DEAD and no teammate is ALIVE to move to.",
                    blocked_rule=RULE_SPECTATED_DEAD,
                    evidence=base,
                )
            evidence = dict(base)
            evidence.update(
                {
                    "dwell_bypassed": True,
                    "dwell_bypass_reason": (
                        "R1 bypasses the dwell gate: a dead player's "
                        "camera carries no information."
                    ),
                    "target_status": "alive",
                }
            )
            return self._decision(
                state,
                target,
                RULE_SPECTATED_DEAD,
                f"Spectated player {_label(spectated)} is DEAD; "
                f"moving to next alive teammate {_label(target)}. "
                "Dwell gate bypassed: a dead camera carries no "
                "information.",
                evidence,
                signals,
            ), None

        # ── R3b SOLO LOCK (Phase 31B) ─────────────────────────────
        # Spectated player is the team's lone survivor in an active
        # fight: ALIVE, no other roster player ALIVE, no other roster
        # player UNKNOWN (an unread slot cannot prove lone-survivor
        # status — fail-closed), and the team fight flag is active.
        # The camera stays: no auto-switch off until the fight ends
        # (flag clears, re-evaluated next call — this engine is
        # stateless) or the player dies (R1 above fires first).
        # KNOCKED teammates do NOT break the lock — they cannot fight
        # — and a revive back to ALIVE releases it automatically on
        # the next evaluation. Placed before R4: R4 would also lock a
        # fighting spectated player, but R3b is the specific rule —
        # it also locks a lone survivor repositioning without a fresh
        # personal signal, whom R4 alone would not lock — and its
        # note names the reason for the status surface.
        if (
            spectated.status == POVPlayerStatus.ALIVE
            and signals.fight_active_on_feed
        ):
            others = [
                p
                for p in ordered
                if p.roster_index != spectated.roster_index
            ]
            if all(
                p.status
                in (POVPlayerStatus.DEAD, POVPlayerStatus.KNOCKED)
                for p in others
            ):
                return None, self._note(
                    state, signals,
                    f"No action: spectated {_label(spectated)} is the "
                    "team's lone survivor in an active fight (solo "
                    "lock); the camera stays until the fight ends or "
                    "the player dies.",
                    blocked_rule=RULE_SOLO_LOCK,
                    evidence={
                        **base,
                        "lone_survivor": True,
                        "other_statuses": [
                            {
                                "roster_index": p.roster_index,
                                "status": (
                                    p.status.value
                                    if isinstance(p.status, Enum)
                                    else str(p.status)
                                ),
                            }
                            for p in others
                        ],
                    },
                )

        # ── R4 FIGHT_LOCK (before R2/R3 — see module docstring) ──
        # ALIVE-only: a knocked player's "victim" entry must not lock
        # the camera (that is R2's case, evaluated next).
        if (
            spectated.status == POVPlayerStatus.ALIVE
            and self._is_fighting(spectated, signals)
        ):
            return None, self._note(
                state, signals,
                f"No action: spectated {_label(spectated)} is "
                "fighting; the pilot never moves the camera off a "
                "player who is in the fight (fight lock).",
                blocked_rule=RULE_FIGHT_LOCK,
                evidence={
                    **base,
                    "spectated_fresh_killfeed": [
                        {"kind": kind, "at": at}
                        for kind, at in self._fresh_killfeed(
                            signals, spectated.roster_index
                        )
                    ],
                },
            )

        # ── R2 SPECTATED_KNOCKED ─────────────────────────────────
        if spectated.status == POVPlayerStatus.KNOCKED:
            fighting_alive = [
                p
                for p in ordered
                if p.roster_index != spectated.roster_index
                and p.status == POVPlayerStatus.ALIVE
                and self._is_fighting(p, signals)
            ]
            target = self._strongest_fighting(fighting_alive, signals)
            if target is not None:
                block = self._dwell_block(
                    state, signals, last_tap_at, RULE_SPECTATED_KNOCKED
                )
                if block is not None:
                    return None, block
                evidence = dict(base)
                evidence.update(
                    {
                        "target_fresh_killfeed": [
                            {"kind": kind, "at": at}
                            for kind, at in self._fresh_killfeed(
                                signals, target.roster_index
                            )
                        ],
                        "target_hp_velocity": target.hp_velocity,
                    }
                )
                return self._decision(
                    state,
                    target,
                    RULE_SPECTATED_KNOCKED,
                    f"Spectated player {_label(spectated)} is "
                    f"KNOCKED; moving to alive teammate "
                    f"{_label(target)} who is fighting.",
                    evidence,
                    signals,
                ), None
            # No fighting teammate: fall through — R3 cannot fire
            # (spectated is not ALIVE/quiet), but R5 may still return
            # the camera to the primary once the team is long quiet.

        # ── R3 TEAMMATE_IN_FIGHT ─────────────────────────────────
        if spectated.status == POVPlayerStatus.ALIVE:
            # Spectated quiet is guaranteed here: R4 already returned
            # for a fighting spectated player, so reaching R3 means no
            # fresh killfeed involvement and no HP-drop signal for the
            # spectated player inside the quiet window.
            fighting_teammates = [
                p
                for p in ordered
                if p.roster_index != spectated.roster_index
                and p.status == POVPlayerStatus.ALIVE
                and self._is_fighting(p, signals)
            ]
            target = self._strongest_fighting(fighting_teammates, signals)
            if target is not None:
                block = self._dwell_block(
                    state, signals, last_tap_at, RULE_TEAMMATE_IN_FIGHT
                )
                if block is not None:
                    return None, block
                evidence = dict(base)
                evidence.update(
                    {
                        "spectated_quiet": True,
                        "quiet_window_s": cfg.quiet_window_s,
                        "hp_drop_velocity": cfg.hp_drop_velocity,
                        "target_fresh_killfeed": [
                            {"kind": kind, "at": at}
                            for kind, at in self._fresh_killfeed(
                                signals, target.roster_index
                            )
                        ],
                        "target_hp_velocity": target.hp_velocity,
                        "fighting_candidates": [
                            p.roster_index for p in fighting_teammates
                        ],
                    }
                )
                return self._decision(
                    state,
                    target,
                    RULE_TEAMMATE_IN_FIGHT,
                    f"Teammate {_label(target)} is fighting while "
                    f"spectated {_label(spectated)} is quiet; moving "
                    "the camera to the fight.",
                    evidence,
                    signals,
                ), None

        # ── R5 RETURN_PRIMARY ────────────────────────────────────
        if state.spectated_index != cfg.primary_roster_index:
            primary = by_index.get(cfg.primary_roster_index)
            if (
                primary is not None
                and primary.status == POVPlayerStatus.ALIVE
                and primary.roster_index != spectated.roster_index
            ):
                last_signal = self._last_team_fight_signal_at(
                    signals, last_fight_signal_at
                )
                anyone_fighting = any(
                    self._is_fighting(p, signals) for p in ordered
                )
                quiet_for = (
                    None if last_signal is None else now - last_signal
                )
                quiet_long_enough = (
                    not signals.fight_active_on_feed
                    and not anyone_fighting
                    and (
                        quiet_for is None
                        or quiet_for > cfg.return_primary_delay_s
                    )
                )
                if quiet_long_enough:
                    block = self._dwell_block(
                        state, signals, last_tap_at, RULE_RETURN_PRIMARY
                    )
                    if block is not None:
                        return None, block
                    evidence = dict(base)
                    evidence.update(
                        {
                            "return_primary_delay_s": (
                                cfg.return_primary_delay_s
                            ),
                            "last_fight_signal_at": last_signal,
                            "quiet_for_s": quiet_for,
                        }
                    )
                    return self._decision(
                        state,
                        primary,
                        RULE_RETURN_PRIMARY,
                        f"No fight signal for the team; returning "
                        f"the camera to primary player "
                        f"{_label(primary)}.",
                        evidence,
                        signals,
                    ), None
                return None, self._note(
                    state, signals,
                    "No action: the team is not yet quiet long "
                    f"enough to return to primary "
                    f"{_label(primary)} (return delay "
                    f"{cfg.return_primary_delay_s:.1f}s; quiet for "
                    f"{quiet_for if quiet_for is not None else 'n/a'}"
                    f"{'s' if quiet_for is not None else ''}"
                    f"{'; a fight is active on this feed' if signals.fight_active_on_feed else ''}"
                    f"{'; a teammate is fighting' if anyone_fighting else ''}).",
                    blocked_rule=RULE_RETURN_PRIMARY,
                    evidence={
                        **base,
                        "last_fight_signal_at": last_signal,
                        "quiet_for_s": quiet_for,
                        "anyone_fighting": anyone_fighting,
                    },
                )
            if primary is not None and (
                primary.status != POVPlayerStatus.ALIVE
            ):
                return None, self._note(
                    state, signals,
                    f"No action: primary {_label(primary)} is "
                    f"{primary.status.value if isinstance(primary.status, Enum) else primary.status}, "
                    "not ALIVE; there is no primary camera to return "
                    "to.",
                    blocked_rule=RULE_RETURN_PRIMARY,
                    evidence=base,
                )

        # ── No rule fired ────────────────────────────────────────
        return None, self._note(
            state, signals,
            f"No action: spectated {_label(spectated)} is "
            f"{spectated.status.value if isinstance(spectated.status, Enum) else spectated.status} "
            "and no pilot rule's conditions are met.",
            evidence=base,
        )

    # ── small helpers ────────────────────────────────────────────

    @staticmethod
    def _next_alive_after(
        ordered: List[PlayerPOV], current_index: int
    ) -> Optional[PlayerPOV]:
        """Next ALIVE teammate in roster order after ``current_index``,
        wrapping around once. UNKNOWN/KNOCKED/DEAD are never targets."""
        if not ordered:
            return None
        positions = [p.roster_index for p in ordered]
        try:
            start = positions.index(current_index)
        except ValueError:
            start = -1
        for step in range(1, len(ordered) + 1):
            candidate = ordered[(start + step) % len(ordered)]
            if candidate.roster_index == current_index:
                continue
            if candidate.status == POVPlayerStatus.ALIVE:
                return candidate
        return None

    def _last_team_fight_signal_at(
        self,
        signals: PilotSignals,
        last_fight_signal_at: Optional[float],
    ) -> Optional[float]:
        """Latest known fight-signal time for the whole team: the
        caller-supplied ``last_fight_signal_at`` merged with every
        killfeed involvement timestamp in this call's signals. None
        when no fight signal has ever been recorded."""
        candidates: List[float] = []
        if last_fight_signal_at is not None:
            candidates.append(float(last_fight_signal_at))
        for entry in signals.killfeed_involvements or []:
            try:
                _idx, _kind, at = entry
                candidates.append(float(at))
            except (TypeError, ValueError):
                continue
        return max(candidates) if candidates else None


__all__ = [
    "PilotConfig",
    "PilotSignals",
    "PilotNote",
    "POVDecision",
    "POVPilotRules",
    "is_final_zone",
    "FINAL_ZONE_PHASES",
    "RULE_NO_EVIDENCE",
    "RULE_SPECTATED_DEAD",
    "RULE_SPECTATED_KNOCKED",
    "RULE_TEAMMATE_IN_FIGHT",
    "RULE_FIGHT_LOCK",
    "RULE_RETURN_PRIMARY",
    "RULE_SOLO_LOCK",
]
