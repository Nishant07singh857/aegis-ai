# backend/app/services/match_service.py
"""
AEGIS AI - Match Service

Thin, real service over the match-related SQLAlchemy models. Owns match
lifecycle (create -> schedule -> lobby -> plane -> live -> finish), result
recording with points calculation, event logging, and stats queries.

Model imports are defensive: Match/MatchEvent/Tournament/Team already exist
in ``app.db.models``; MatchStatus/MatchMap/TournamentStatus/MatchTeamStats/
MatchPlayerStats do NOT exist in ``app.db.models``.

Phase 22 (W3, Tech) truth-in-labelling: the local definitions below are
therefore NOT dead fallbacks — they are the CANONICAL models/enums for
match stats (the tables are created from these classes). The try-imports
remain so that if canonical models are ever moved into ``app.db.models``,
they win and these definitions are skipped; until then, anyone editing
match-stats fields edits THEM HERE (and the alembic chain), nowhere else.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import Boolean, Float, Integer, String, desc, func, select
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ..db.models import Match, MatchEvent, Team, Tournament

logger = structlog.get_logger("aegis.services.match")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OPTIONAL MODEL IMPORTS — the local definitions below are CANONICAL
# today (app.db.models has no equivalents; see module docstring).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

try:
    from ..db.models import MatchStatus
except ImportError:  # pragma: no cover - fallback until models land

    class MatchStatus(str, enum.Enum):
        SCHEDULED = "scheduled"
        STARTING = "starting"
        LOBBY = "lobby"
        PLANE = "plane"
        LIVE = "live"
        FINISHED = "finished"
        CANCELLED = "cancelled"
        ABANDONED = "abandoned"

try:
    from ..db.models import MatchMap
except ImportError:  # pragma: no cover - fallback until models land

    class MatchMap(str, enum.Enum):
        ERANGEL = "erangel"
        MIRAMAR = "miramar"
        SANHOK = "sanhok"
        VIKENDI = "vikendi"
        LIVIK = "livik"
        KARAKIN = "karakin"

try:
    from ..db.models import TournamentStatus
except ImportError:  # pragma: no cover - fallback until models land

    class TournamentStatus(str, enum.Enum):
        DRAFT = "draft"
        SCHEDULED = "scheduled"
        LIVE = "live"
        PAUSED = "paused"
        COMPLETED = "completed"
        CANCELLED = "cancelled"

try:
    from ..db.models import MatchTeamStats
except ImportError:  # pragma: no cover - fallback until models land

    class MatchTeamStats(Base):
        """Per-team results for one match (CANONICAL model — see module docstring)."""

        __tablename__ = "match_team_stats"

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        match_id: Mapped[str] = mapped_column(String(36), nullable=False)
        team_id: Mapped[str] = mapped_column(String(64), nullable=False)
        placement: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
        total_kills: Mapped[int] = mapped_column(Integer, default=0)
        # Nullable: None = the feed never delivered real damage. Never
        # store an estimate (Phase 21 honesty rule) — see migration 013.
        total_damage: Mapped[Optional[int]] = mapped_column(
            Integer, nullable=True, default=None
        )
        total_points: Mapped[int] = mapped_column(Integer, default=0)

try:
    from ..db.models import MatchPlayerStats
except ImportError:  # pragma: no cover - fallback until models land

    class MatchPlayerStats(Base):
        """Per-player stats for one match (CANONICAL model — see module docstring).

        Phase 21 honesty/unification notes:
        * ``damage_dealt`` is NULLABLE: ``None`` means the feed/agent
          layer never delivered real damage for this player, and the
          API must surface ``null`` ("—") — never 0, never an estimate
          (see alembic 013).
        * ``clutches`` counts clutches WON (mvp_calculator
          ``extract_clutches``); attempts alone never count.
        * ``mvp_score`` is always the canonical PRD formula computed
          by ``mvp_calculator.compute_mvp_score_from_stats`` inside
          :meth:`MatchService.record_player_stats` — callers never
          supply it.
        """

        __tablename__ = "match_player_stats"

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        match_id: Mapped[str] = mapped_column(String(36), nullable=False)
        player_id: Mapped[str] = mapped_column(String(64), nullable=False)
        team_id: Mapped[str] = mapped_column(String(64), nullable=False)
        kills: Mapped[int] = mapped_column(Integer, default=0)
        damage_dealt: Mapped[Optional[int]] = mapped_column(
            Integer, nullable=True, default=None
        )
        headshots: Mapped[int] = mapped_column(Integer, default=0)
        revives_performed: Mapped[int] = mapped_column(Integer, default=0)
        # Phase 21 (T5b): utility counters, populated from the agent
        # counters (utility_tracker.PlayerUtilityStats.healing_done /
        # .grenades_used, team_tracker player.grenades / .healing_done).
        healing: Mapped[int] = mapped_column(Integer, default=0)
        grenades_used: Mapped[int] = mapped_column(Integer, default=0)
        survived_time_seconds: Mapped[int] = mapped_column(Integer, default=0)
        clutches: Mapped[int] = mapped_column(Integer, default=0)
        mvp_score: Mapped[float] = mapped_column(Float, default=0.0)
        is_mvp: Mapped[bool] = mapped_column(Boolean, default=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DOMAIN CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Placement points are NOT hardcoded here. Scoring always comes from the
# active tournament config via app.analytics.points_calculator.PointsCalculator
# (see record_match_results). The module-level table was removed in Phase 13
# because it contradicted both the Krafton BMSD defaults and per-tournament
# configs. Do not reintroduce a hardcoded table.

# Legal status transitions (compared as plain strings; DB returns str)
_STATUS_TRANSITIONS: Dict[str, set] = {
    "scheduled": {"lobby", "plane", "cancelled"},
    "starting": {"lobby", "plane", "cancelled"},
    "lobby": {"plane", "live", "cancelled"},
    "plane": {"live", "cancelled", "abandoned"},
    "live": {"finished", "cancelled", "abandoned"},
    "finished": set(),
    "cancelled": set(),
    "abandoned": set(),
}


def player_stats_from_utility(
    player_id: str,
    team_id: str,
    utility_stats: Any,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a ``record_player_stats`` line from agent utility counters.

    Accepts the real counter shapes (Phase 21/T5b):
      * ``utility_tracker.PlayerUtilityStats`` — ``healing_done``,
        ``grenades_used`` attributes;
      * ``team_tracker`` player records — ``healing_done`` / ``grenades``
        attributes or dict keys;
      * plain dicts with any of those keys.

    Values are copied, never invented: a counter the source does not
    carry stays 0. ``extra`` merges caller-known fields (kills, damage…).
    """
    def _read(*names: str) -> int:
        for name in names:
            if isinstance(utility_stats, dict):
                value = utility_stats.get(name)
            else:
                value = getattr(utility_stats, name, None)
            if value is not None:
                return int(value)
        return 0

    line: Dict[str, Any] = {
        "player_id": player_id,
        "team_id": team_id,
        "healing": _read("healing", "healing_done", "total_healing"),
        "grenades_used": _read("grenades_used", "grenades"),
    }
    line.update(extra)
    return line


def _status_value(status: Any) -> str:
    """Normalize a MatchStatus enum or plain string to its value."""
    return status.value if isinstance(status, enum.Enum) else str(status)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MATCH SERVICE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MatchService:
    """Domain service for matches. Construct with an async DB session."""

    def __init__(self, session: Any):
        self.session = session
        self._log = logger.bind(service="match_service")

    # ── Helpers ─────────────────────────────────────────────────

    async def _get_match_or_raise(self, match_id: str) -> Match:
        result = await self.session.execute(
            select(Match).where(Match.id == match_id)
        )
        match = result.scalar_one_or_none()
        if match is None:
            raise ValueError(f"Match {match_id} not found")
        return match

    # ── Points ──────────────────────────────────────────────────
    # Scoring is tournament-config driven (Phase 13). See record_match_results,
    # which configures a PointsCalculator from the active tournament.
    # The old hardcoded _calculate_points table was removed: it contradicted
    # both the Krafton BMSD defaults (chicken=10, 1/finish) and per-tournament
    # configs. Do not reintroduce hardcoded scoring.

    # ── Creation / retrieval ────────────────────────────────────

    async def create_match(
        self,
        tournament_id: str,
        map_name: Any,
        scheduled_time: datetime,
        custom_room_id: Optional[str] = None,
        custom_room_password: Optional[str] = None,
        match_name: Optional[str] = None,
    ) -> Match:
        """Create a scheduled match; match_number auto-increments."""
        result = await self.session.execute(
            select(Tournament).where(Tournament.id == tournament_id)
        )
        tournament = result.scalar_one_or_none()
        if tournament is None:
            raise ValueError(f"Tournament {tournament_id} not found")

        count_result = await self.session.execute(
            select(func.count())
            .select_from(Match)
            .where(Match.tournament_id == tournament_id)
        )
        existing = count_result.scalar() or 0

        match = Match(
            tournament_id=tournament_id,
            match_number=existing + 1,
            map_name=map_name,
            status=MatchStatus.SCHEDULED,
            scheduled_time=scheduled_time,
            custom_room_id=custom_room_id,
            custom_room_password=custom_room_password,
        )
        if match_name is not None:
            match.match_name = match_name  # type: ignore[attr-defined]

        self.session.add(match)
        await self.session.commit()
        await self.session.refresh(match)
        self._log.info(
            "Match created",
            match_id=match.id,
            match_number=match.match_number,
        )
        return match

    async def get_match(self, match_id: str) -> Match:
        """Get a match by id; raises if missing."""
        return await self._get_match_or_raise(match_id)

    async def list_tournament_matches(self, tournament_id: str) -> List[Match]:
        """List all matches of a tournament in match-number order."""
        result = await self.session.execute(
            select(Match)
            .where(Match.tournament_id == tournament_id)
            .order_by(Match.match_number)
        )
        return list(result.scalars().all())

    # ── Lifecycle ───────────────────────────────────────────────

    async def start_match(self, match_id: str) -> Match:
        """Transition a match to LIVE; records actual start time."""
        match = await self._get_match_or_raise(match_id)
        current = _status_value(match.status)
        if current not in ("scheduled", "lobby", "plane"):
            raise ValueError(f"Cannot start match from status '{current}'")
        match.status = MatchStatus.LIVE
        match.actual_start_time = datetime.utcnow()
        await self.session.commit()
        await self.session.refresh(match)
        self._log.info("Match started", match_id=match_id)
        return match

    async def end_match(self, match_id: str) -> Match:
        """Transition a LIVE match to FINISHED; computes duration."""
        match = await self._get_match_or_raise(match_id)
        if _status_value(match.status) != "live":
            raise ValueError(
                f"Cannot end match from status '{_status_value(match.status)}'"
            )
        now = datetime.utcnow()
        match.status = MatchStatus.FINISHED
        match.actual_end_time = now
        if match.actual_start_time:
            match.duration_seconds = int(
                (now - match.actual_start_time).total_seconds()
            )
        await self.session.commit()
        await self.session.refresh(match)
        self._log.info("Match ended", match_id=match_id)
        return match

    async def cancel_match(self, match_id: str) -> Match:
        """Cancel a match that has not finished."""
        match = await self._get_match_or_raise(match_id)
        if _status_value(match.status) in ("finished", "cancelled", "abandoned"):
            raise ValueError(
                f"Cannot cancel match in terminal status "
                f"'{_status_value(match.status)}'"
            )
        match.status = MatchStatus.CANCELLED
        await self.session.commit()
        await self.session.refresh(match)
        return match

    async def abandon_match(self, match_id: str) -> Match:
        """Abandon a live/in-progress match."""
        match = await self._get_match_or_raise(match_id)
        if _status_value(match.status) not in ("live", "plane"):
            raise ValueError(
                f"Cannot abandon match from status '{_status_value(match.status)}'"
            )
        match.status = MatchStatus.ABANDONED
        await self.session.commit()
        await self.session.refresh(match)
        return match

    async def update_match_status(self, match_id: str, new_status: Any) -> Match:
        """Transition a match to a new status; validates the transition."""
        match = await self._get_match_or_raise(match_id)
        current = _status_value(match.status)
        target = _status_value(new_status)
        if target not in _STATUS_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"Illegal match status transition: {current} -> {target}"
            )
        match.status = new_status
        await self.session.commit()
        await self.session.refresh(match)
        self._log.info(
            "Match status updated", match_id=match_id,
            old_status=current, new_status=target,
        )
        return match

    # ── Results & stats ─────────────────────────────────────────

    async def record_match_results(
        self,
        match_id: str,
        team_results: List[Dict[str, Any]],
        winner_team_id: Optional[str] = None,
        mvp_player_id: Optional[str] = None,
    ) -> List[MatchTeamStats]:
        """
        Record per-team results; calculates points = placement + kills using
        the ACTIVE TOURNAMENT's point system (via PointsCalculator). Falls back
        to the documented Krafton BMSD defaults (chicken=10, 1/finish) when no
        tournament is active. Sets the match winner/MVP when provided.
        """
        match = await self._get_match_or_raise(match_id)

        # Tournament-config-driven scoring (Phase 13). Never raises: a dead or
        # missing database degrades to the documented BMSD defaults.
        from app.analytics.points_calculator import PointsCalculator

        _calc = PointsCalculator()
        await _calc.configure_from_active_tournament(self.session)

        created: List[MatchTeamStats] = []
        for tr in team_results:
            team_id = tr["team_id"]
            placement = int(tr.get("placement", 0))
            kills = int(tr.get("kills", 0))
            # Honest damage (Phase 21): a missing/None damage stays
            # None (unknown) — it is never defaulted or estimated.
            _raw_damage = tr.get("damage", tr.get("total_damage"))
            damage = int(_raw_damage) if _raw_damage is not None else None

            # Defensive team lookup (keeps query count predictable)
            team_result = await self.session.execute(
                select(Team).where(Team.id == team_id)
            )
            team_result.scalar_one_or_none()

            total_points = (
                _calc.placement_points.get_points(placement)
                + kills * _calc.points_per_kill
            )
            stats = MatchTeamStats(
                match_id=match.id,
                team_id=team_id,
                placement=placement,
                total_kills=kills,
                total_damage=damage,
                total_points=total_points,
            )
            self.session.add(stats)
            created.append(stats)

        if winner_team_id:
            match.winner_team_id = winner_team_id  # type: ignore[attr-defined]
        if mvp_player_id:
            match.mvp_player_id = mvp_player_id  # type: ignore[attr-defined]

        await self.session.commit()
        self._log.info(
            "Match results recorded", match_id=match_id,
            teams=len(created), winner=winner_team_id,
        )
        return created

    async def record_player_stats(
        self,
        match_id: str,
        player_stats: List[Dict[str, Any]],
    ) -> List[MatchPlayerStats]:
        """Record per-player stat lines for a match.

        Phase 21 (W2) unification + honesty:
        * ``mvp_score`` is ALWAYS computed here with the single
          canonical producer
          (``mvp_calculator.compute_mvp_score_from_stats``:
          kills*5 + damage*0.05 + revives*3 + survival*0.02
          + clutches*10 + headshots*2). Any caller-supplied
          ``mvp_score`` is ignored — this method is the wiring from
          calculator → DB.
        * ``damage_dealt`` is preserved exactly as delivered: the
          agent layer's honest accounting (``TeamTracker.record_kill``
          damage / HP-drop derived totals). A missing/``None`` damage
          persists as ``None`` so the API returns ``null`` — never 0,
          never an estimate.
        * ``is_mvp``: an explicit caller flag wins; otherwise the
          top canonical scorer is flagged.
        """
        match = await self._get_match_or_raise(match_id)

        from app.agents.analytics_agents.mvp_calculator import (
            compute_mvp_score_from_stats,
            extract_clutches,
        )

        created: List[MatchPlayerStats] = []
        # Phase 21 (T5b): pass healing/grenades only when the stats model
        # actually carries those columns (the fallback model here does;
        # this keeps the call safe if a real model lands without them).
        _table = getattr(MatchPlayerStats, "__table__", None)
        _columns = set(_table.columns.keys()) if _table is not None else set()
        for ps in player_stats:
            extra: Dict[str, Any] = {}
            if "healing" in _columns:
                extra["healing"] = int(
                    ps.get("healing", ps.get("healing_done", 0)) or 0
                )
            if "grenades_used" in _columns:
                extra["grenades_used"] = int(
                    ps.get("grenades_used", ps.get("grenades", 0)) or 0
                )
            if "clutches" in _columns:
                extra["clutches"] = extract_clutches(ps)

            # Damage aliases: damage_dealt (DB shape) or damage (feed
            # shape). Absent/None → None (unknown), never fabricated.
            _raw_damage = ps.get("damage_dealt", ps.get("damage"))
            damage_dealt = (
                int(_raw_damage) if _raw_damage is not None else None
            )
            # Revives / survival aliases mirror the calculator's.
            revives = int(
                ps.get(
                    "revives_performed",
                    ps.get("revives", 0),
                )
                or 0
            )
            survived = int(
                ps.get(
                    "survived_time_seconds",
                    ps.get(
                        "survival_seconds",
                        ps.get("survival_time", 0),
                    ),
                )
                or 0
            )
            kills = int(ps.get("kills", ps.get("finishes", 0)) or 0)
            headshots = int(ps.get("headshots", 0) or 0)
            canonical_input: Dict[str, Any] = {
                "kills": kills,
                "damage": damage_dealt,
                "revives": revives,
                "survival_seconds": survived,
                "clutches": extract_clutches(ps),
                "headshots": headshots,
            }
            stats = MatchPlayerStats(
                match_id=match.id,
                player_id=ps.get("player_id", ""),
                team_id=ps.get("team_id", ""),
                kills=kills,
                damage_dealt=damage_dealt,
                headshots=headshots,
                revives_performed=revives,
                survived_time_seconds=survived,
                mvp_score=compute_mvp_score_from_stats(canonical_input),
                is_mvp=bool(ps.get("is_mvp", False)),
                **extra,
            )
            self.session.add(stats)
            created.append(stats)

        # Flag the MVP when the caller did not: highest canonical score.
        if created and not any(s.is_mvp for s in created):
            top = max(created, key=lambda s: s.mvp_score or 0.0)
            top.is_mvp = True

        await self.session.commit()
        return created

    async def record_match_event(
        self,
        match_id: str,
        event_type: str,
        timestamp_ms: int,
        event_data: Dict[str, Any],
        team_id: Optional[str] = None,
        player_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        target_player_id: Optional[str] = None,
    ) -> MatchEvent:
        """Persist a single match event (kill, knock, zone, ...)."""
        match = await self._get_match_or_raise(match_id)
        event = MatchEvent(
            match_id=match.id,
            event_type=event_type,
            timestamp_ms=timestamp_ms,
            event_data=event_data or {},
            team_id=team_id,
            player_id=player_id,
            target_team_id=target_team_id,
            target_player_id=target_player_id,
        )
        self.session.add(event)
        await self.session.commit()
        return event

    # ── Queries ─────────────────────────────────────────────────

    async def get_match_events(
        self,
        match_id: str,
        limit: int = 100,
        event_type: Optional[str] = None,
    ) -> List[MatchEvent]:
        """Get match events, optionally filtered by type."""
        query = (
            select(MatchEvent)
            .where(MatchEvent.match_id == match_id)
            .order_by(MatchEvent.timestamp_ms)
        )
        if event_type:
            query = query.where(MatchEvent.event_type == event_type)
        result = await self.session.execute(query.limit(limit))
        return list(result.scalars().all())

    async def get_match_stats(self, match_id: str) -> Match:
        """Get the match row carrying aggregate stats fields."""
        return await self._get_match_or_raise(match_id)

    async def get_match_team_stats(self, match_id: str) -> List[MatchTeamStats]:
        """Per-team results ordered by placement."""
        result = await self.session.execute(
            select(MatchTeamStats)
            .where(MatchTeamStats.match_id == match_id)
            .order_by(MatchTeamStats.placement)
        )
        return list(result.scalars().all())

    async def get_match_player_stats(
        self, match_id: str
    ) -> List[MatchPlayerStats]:
        """Per-player stats ordered by MVP score."""
        result = await self.session.execute(
            select(MatchPlayerStats)
            .where(MatchPlayerStats.match_id == match_id)
            .order_by(desc(MatchPlayerStats.mvp_score))
        )
        return list(result.scalars().all())

    async def get_match_mvp(self, match_id: str) -> Optional[MatchPlayerStats]:
        """Return the flagged MVP, else the top scorer."""
        stats = await self.get_match_player_stats(match_id)
        for s in stats:
            if s.is_mvp:
                return s
        return max(stats, key=lambda s: s.mvp_score or 0.0) if stats else None

    async def get_match_summary(self, match_id: str) -> Dict[str, Any]:
        """Comprehensive match summary: match row + ordered team results."""
        match = await self._get_match_or_raise(match_id)
        team_stats = await self.get_match_team_stats(match_id)

        def _val(v: Any) -> Any:
            return v.value if isinstance(v, enum.Enum) else v

        return {
            "match": {
                "id": match.id,
                "match_number": match.match_number,
                "map_name": _val(match.map_name),
                "status": _val(match.status),
                "total_kills": getattr(match, "total_kills", 0) or 0,
                "total_damage": getattr(match, "total_damage", 0) or 0,
                "winner_team_id": getattr(match, "winner_team_id", None),
                "mvp_player_id": getattr(match, "mvp_player_id", None),
                "duration_seconds": match.duration_seconds,
            },
            "team_results": [
                {
                    "team_id": ts.team_id,
                    "placement": ts.placement,
                    "total_kills": ts.total_kills,
                    "total_damage": ts.total_damage,
                    "total_points": ts.total_points,
                }
                for ts in team_stats
            ],
        }

    # ── Validation ──────────────────────────────────────────────

    async def validate_match_schedule(
        self,
        tournament: Tournament,
        scheduled_time: datetime,
    ) -> None:
        """Ensure a match time falls inside the tournament window."""
        start = getattr(tournament, "start_date", None)
        end = getattr(tournament, "end_date", None)
        if start and scheduled_time < start:
            raise ValueError(
                "Match cannot be scheduled before the tournament starts"
            )
        if end and scheduled_time > end:
            raise ValueError(
                "Match cannot be scheduled after the tournament ends"
            )
