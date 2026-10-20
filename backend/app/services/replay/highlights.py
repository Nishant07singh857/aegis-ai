# backend/app/services/replay/highlights.py
"""Highlight detection over the replay buffer (Phase 10).

Scores REAL kill events only. The detector never invents moments: if
the buffer has no qualifying kill events, the reel is honestly EMPTY
(build_reel returns []).

Scoring rules (all windows anchored on real event timestamps):
  * multi_kill  — >=3 kills by the SAME player within 10s.
                  3 -> 60 ("Triple kill"), 4 -> 80 ("Quad kill"),
                  5+ -> 95 ("Ace").
  * squad_wipe  — >=4 kills against the SAME victim team within 30s.
                  Always 90: a wiped squad is a broadcast moment.
  * clutch      — a player scores >=2 kills within 10s while being the
                  ONLY scorer on their team in that window (their
                  teammates contributed nothing): a 1vN solo effort.
                  Always 75. Phase 22 (W3, M11): when kill events carry
                  real alive counts (``killer_team_alive`` /
                  ``opponents_alive`` payload fields, supplied by the
                  alive-count reader chain), the clutch is VERIFIED:
                  ``killer_team_alive == 1`` and the player eliminated
                  every remaining opponent (``opponents_alive`` ==
                  window kills) — a verified 1v4 scores 95 and says
                  so. Without alive data the moment still fires but
                  its reason is labelled "Unverified".
  * final_kill  — Phase 22 (W3, M11): the last kill before a
                  ``match_end`` event in the buffer. Score 80. No
                  match-end state in the buffer -> no final kill is
                  claimed (a merely-last kill is not a final kill).

Plug-ready kinds (grenade_multi_kill / sniper_triple / vehicle_kill)
live in ``highlight_triggers.py``: they fire only when kill events
carry truthful weapon/vehicle attribution, which today's feed does
not produce (weapon is honestly None) — see that module.

Scores are 0-100 with a human-readable reason string, ordered
score-descending for the reel.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import structlog

from .buffer import BufferedEvent
from .schemas import Highlight

logger = structlog.get_logger("aegis.replay.highlights")

MULTI_KILL_WINDOW_SECONDS = 10.0
MULTI_KILL_MIN_KILLS = 3
SQUAD_WIPE_WINDOW_SECONDS = 30.0
SQUAD_WIPE_MIN_KILLS = 4
CLUTCH_WINDOW_SECONDS = 10.0
CLUTCH_MIN_KILLS = 2


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@dataclass
class ScoredMoment:
    """One detected highlight moment before schema conversion."""

    kind: str  # "multi_kill" | "squad_wipe" | "clutch"
    score: int
    reason: str
    player: Optional[str] = None
    team: Optional[str] = None
    opponents: List[str] = field(default_factory=list)
    window_start: float = 0.0
    window_end: float = 0.0
    event_ids: List[str] = field(default_factory=list)
    moment_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_highlight(self) -> Highlight:
        return Highlight(
            moment_id=self.moment_id,
            kind=self.kind,  # type: ignore[arg-type]
            score=self.score,
            reason=self.reason,
            player=self.player,
            team=self.team,
            opponents=self.opponents,
            window_start=self.window_start,
            window_end=self.window_end,
            window_start_iso=_iso(self.window_start),
            window_end_iso=_iso(self.window_end),
            event_ids=self.event_ids,
        )


def _kills(events: List[BufferedEvent]) -> List[BufferedEvent]:
    """Real kill events only. knocks are not kills and never score."""
    return sorted(
        (e for e in events if e.type == "kill_event"),
        key=lambda e: e.ts,
    )


class HighlightDetector:
    """Stateless scorer: events in, scored moments out."""

    # ── entry point ────────────────────────────────────────────

    def score_events(self, events: List[BufferedEvent]) -> List[ScoredMoment]:
        """Score every qualifying moment in the given events.

        Returns [] when there is nothing to score — never synthesises.
        """
        kills = _kills(events)
        if not kills:
            return []
        moments: List[ScoredMoment] = []
        moments.extend(self._detect_multi_kills(kills))
        moments.extend(self._detect_squad_wipes(kills))
        moments.extend(self._detect_clutches(kills))
        moments.extend(self._detect_final_kill(events, kills))
        # Phase 22 (W3, M11): plug-ready weapon/vehicle triggers —
        # inactive (fire nothing) until events carry truthful
        # weapon/vehicle attribution; see highlight_triggers.py.
        try:
            from .highlight_triggers import trigger_registry

            for trigger in trigger_registry(events):
                moments.extend(trigger.run(events))
        except Exception:  # pragma: no cover - triggers must not break scoring
            pass
        return moments

    def _detect_final_kill(
        self, events: List[BufferedEvent], kills: List[BufferedEvent]
    ) -> List[ScoredMoment]:
        """Final kill: the last kill at/before match-end state.

        Truthful sources of match-end state in the buffer: an event of
        type ``match_end`` or a kill explicitly flagged ``final_kill``
        by a producer that knows the match ended. Anything else (e.g.
        "the last kill we happen to hold") is NOT claimed as final.
        """
        moments: List[ScoredMoment] = []
        end_ts: Optional[float] = None
        for e in events:
            if e.type == "match_end":
                end_ts = e.ts if end_ts is None else max(end_ts, e.ts)
        flagged = [k for k in kills if k.payload.get("final_kill")]
        candidate: Optional[BufferedEvent] = None
        if flagged:
            candidate = flagged[-1]
        elif end_ts is not None:
            before = [k for k in kills if k.ts <= end_ts]
            candidate = before[-1] if before else None
        if candidate is None:
            return moments
        killer = candidate.payload.get("killer") or "Unknown"
        moments.append(
            ScoredMoment(
                kind="final_kill",
                score=80,
                reason=(
                    f"Final kill — {killer} "
                    f"({candidate.payload.get('killerTeam') or 'Unknown'}) "
                    "lands the match-ending elimination"
                ),
                player=killer,
                team=candidate.payload.get("killerTeam"),
                opponents=[candidate.payload.get("victim") or "Unknown"],
                window_start=candidate.ts,
                window_end=candidate.ts,
                event_ids=[candidate.event_id],
            )
        )
        return moments

    def build_reel(self, events: List[BufferedEvent], limit: int = 10) -> List[Highlight]:
        """Top-N moments ordered by score (then recency)."""
        scored = self.score_events(events)
        scored.sort(key=lambda m: (m.score, m.window_end), reverse=True)
        reel = [m.to_highlight() for m in scored[: max(0, limit)]]
        logger.debug("Highlight reel built", candidates=len(scored), returned=len(reel))
        return reel

    # ── detectors ──────────────────────────────────────────────

    def _detect_multi_kills(self, kills: List[BufferedEvent]) -> List[ScoredMoment]:
        moments: List[ScoredMoment] = []
        by_killer: Dict[str, List[BufferedEvent]] = {}
        for k in kills:
            by_killer.setdefault(k.payload.get("killer") or "Unknown", []).append(k)

        for killer, ks in by_killer.items():
            covered: set[str] = set()
            for i, anchor in enumerate(ks):
                if anchor.event_id in covered:
                    continue
                window = [
                    k for k in ks[i:]
                    if 0.0 <= k.ts - anchor.ts <= MULTI_KILL_WINDOW_SECONDS
                ]
                count = len(window)
                if count < MULTI_KILL_MIN_KILLS:
                    continue
                if count >= 5:
                    score, label = 95, "Ace"
                elif count == 4:
                    score, label = 80, "Quad kill"
                else:
                    score, label = 60, "Triple kill"
                span = window[-1].ts - window[0].ts
                moments.append(
                    ScoredMoment(
                        kind="multi_kill",
                        score=score,
                        reason=(
                            f"{label} by {killer} "
                            f"({window[0].payload.get('killerTeam') or 'Unknown'}) — "
                            f"{count} eliminations in {span:.1f}s"
                        ),
                        player=killer,
                        team=window[0].payload.get("killerTeam"),
                        opponents=[k.payload.get("victim") or "Unknown" for k in window],
                        window_start=window[0].ts,
                        window_end=window[-1].ts,
                        event_ids=[k.event_id for k in window],
                    )
                )
                covered.update(k.event_id for k in window)
        return moments

    def _detect_squad_wipes(self, kills: List[BufferedEvent]) -> List[ScoredMoment]:
        moments: List[ScoredMoment] = []
        by_victim_team: Dict[str, List[BufferedEvent]] = {}
        for k in kills:
            by_victim_team.setdefault(k.payload.get("victimTeam") or "Unknown", []).append(k)

        for victim_team, ks in by_victim_team.items():
            covered: set[str] = set()
            for i, anchor in enumerate(ks):
                if anchor.event_id in covered:
                    continue
                window = [
                    k for k in ks[i:]
                    if 0.0 <= k.ts - anchor.ts <= SQUAD_WIPE_WINDOW_SECONDS
                ]
                if len(window) < SQUAD_WIPE_MIN_KILLS:
                    continue
                killers = sorted({k.payload.get("killer") or "Unknown" for k in window})
                moments.append(
                    ScoredMoment(
                        kind="squad_wipe",
                        score=90,
                        reason=(
                            f"Squad wipe — {victim_team} fully eliminated "
                            f"({len(window)} kills in {window[-1].ts - window[0].ts:.1f}s "
                            f"by {', '.join(killers)})"
                        ),
                        team=victim_team,
                        opponents=killers,
                        window_start=window[0].ts,
                        window_end=window[-1].ts,
                        event_ids=[k.event_id for k in window],
                    )
                )
                covered.update(k.event_id for k in window)
        return moments

    def _detect_clutches(self, kills: List[BufferedEvent]) -> List[ScoredMoment]:
        moments: List[ScoredMoment] = []
        by_killer: Dict[str, List[BufferedEvent]] = {}
        for k in kills:
            by_killer.setdefault(k.payload.get("killer") or "Unknown", []).append(k)

        covered: set[str] = set()
        for killer, ks in by_killer.items():
            for i, anchor in enumerate(ks):
                if anchor.event_id in covered:
                    continue
                window = [
                    k for k in ks[i:]
                    if 0.0 <= k.ts - anchor.ts <= CLUTCH_WINDOW_SECONDS
                ]
                if len(window) < CLUTCH_MIN_KILLS:
                    continue
                team = anchor.payload.get("killerTeam")
                teammates_scored = any(
                    k.payload.get("killer") != killer
                    and k.payload.get("killerTeam") == team
                    for k in kills
                    if window[0].ts <= k.ts <= window[-1].ts
                )
                if teammates_scored:
                    continue  # not a solo effort
                span = window[-1].ts - window[0].ts
                # Phase 22 (W3, M11): alive-count verification. When
                # the anchor kill carries real alive counts, verify:
                # killer's team had exactly 1 alive (the killer) and
                # the window's kills account for every remaining
                # opponent. A failed verification demotes the moment
                # to nothing — claiming 1v4 without the counts, or
                # against the counts, would be fabrication.
                team_alive = anchor.payload.get("killer_team_alive")
                opponents_alive = anchor.payload.get("opponents_alive")
                if team_alive is not None and opponents_alive is not None:
                    try:
                        verified = (
                            int(team_alive) == 1
                            and int(opponents_alive) == len(window)
                        )
                    except (TypeError, ValueError):
                        verified = False
                    if not verified:
                        continue
                    moments.append(
                        ScoredMoment(
                            kind="clutch",
                            score=95,
                            reason=(
                                f"Verified 1v{len(window)} clutch — {killer} "
                                f"({team or 'Unknown'}), sole survivor of the "
                                f"team, eliminated all {len(window)} remaining "
                                f"opponents in {span:.1f}s (alive counts verified)"
                            ),
                            player=killer,
                            team=team,
                            opponents=[k.payload.get("victim") or "Unknown" for k in window],
                            window_start=window[0].ts,
                            window_end=window[-1].ts,
                            event_ids=[k.event_id for k in window],
                        )
                    )
                    covered.update(k.event_id for k in window)
                    continue
                moments.append(
                    ScoredMoment(
                        kind="clutch",
                        score=75,
                        reason=(
                            f"Unverified clutch (alive counts unavailable) — "
                            f"{killer} ({team or 'Unknown'}) holds alone, "
                            f"{len(window)} eliminations in {span:.1f}s with no "
                            f"teammate scoring in the window"
                        ),
                        player=killer,
                        team=team,
                        opponents=[k.payload.get("victim") or "Unknown" for k in window],
                        window_start=window[0].ts,
                        window_end=window[-1].ts,
                        event_ids=[k.event_id for k in window],
                    )
                )
                covered.update(k.event_id for k in window)
        return moments
