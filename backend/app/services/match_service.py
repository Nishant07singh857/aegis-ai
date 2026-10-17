# backend/app/services/match_service.py
"""
AEGIS AI - Match Service

Thin, real service over the match-related SQLAlchemy models. Owns match
lifecycle (create -> schedule -> lobby -> plane -> live -> finish), result
recording with points calculation, event logging, and stats queries.

Model imports are defensive: Match/MatchEvent/Tournament/Team already exist
in ``app.db.models``; MatchStatus/MatchMap/TournamentStatus/MatchTeamStats/
MatchPlayerStats may still be landing in parallel, so each missing name gets
a local fallback. When the real names land in the models package, the
try-imports win and the fallbacks are skipped (coordinator dedups at
integration).
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
# OPTIONAL MODEL IMPORTS (with local fallbacks)
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
        """Per-team results for one match (local fallback model)."""

        __tablename__ = "match_team_stats"

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        match_id: Mapped[str] = mapped_column(String(36), nullable=False)
        team_id: Mapped[str] = mapped_column(String(64), nullable=False)
        placement: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
        total_kills: Mapped[int] = mapped_column(Integer, default=0)
        total_damage: Mapped[int] = mapped_column(Integer, default=0)
        total_points: Mapped[int] = mapped_column(Integer, default=0)

try:
    from ..db.models import MatchPlayerStats
except ImportError:  # pragma: no cover - fallback until models land

    class MatchPlayerStats(Base):
        """Per-player stats for one match (local fallback model)."""

        __tablename__ = "match_player_stats"

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        match_id: Mapped[str] = mapped_column(String(36), nullable=False)
        player_id: Mapped[str] = mapped_column(String(64), nullable=False)
        team_id: Mapped[str] = mapped_column(String(64), nullable=False)
        kills: Mapped[int] = mapped_column(Integer, default=0)
        damage_dealt: Mapped[int] = mapped_column(Integer, default=0)
        headshots: Mapped[int] = mapped_column(Integer, default=0)
        revives_performed: Mapped[int] = mapped_column(Integer, default=0)
        survived_time_seconds: Mapped[int] = mapped_column(Integer, default=0)
        mvp_score: Mapped[float] = mapped_column(Float, default=0.0)
        is_mvp: Mapped[bool] = mapped_column(Boolean, default=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DOMAIN CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Placement points (standard battle-royale scoring)
PLACEMENT_POINTS: Dict[int, int] = {
    1: 15, 2: 12, 3: 10, 4: 8, 5: 6, 6: 4,
    7: 2, 8: 1, 9: 1, 10: 1, 11: 1, 12: 1,
}
POINTS_PER_KILL = 1

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

    @staticmethod
    def _calculate_points(placement: int, kills: int) -> int:
        return PLACEMENT_POINTS.get(placement, 0) + kills * POINTS_PER_KILL

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
        Record per-team results; calculates points = placement + kills.
        Sets the match winner/MVP when provided.
        """
        match = await self._get_match_or_raise(match_id)

        created: List[MatchTeamStats] = []
        for tr in team_results:
            team_id = tr["team_id"]
            placement = int(tr.get("placement", 0))
            kills = int(tr.get("kills", 0))
            damage = int(tr.get("damage", 0))

            # Defensive team lookup (keeps query count predictable)
            team_result = await self.session.execute(
                select(Team).where(Team.id == team_id)
            )
            team_result.scalar_one_or_none()

            stats = MatchTeamStats(
                match_id=match.id,
                team_id=team_id,
                placement=placement,
                total_kills=kills,
                total_damage=damage,
                total_points=self._calculate_points(placement, kills),
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
        """Record per-player stat lines for a match."""
        match = await self._get_match_or_raise(match_id)

        created: List[MatchPlayerStats] = []
        for ps in player_stats:
            stats = MatchPlayerStats(
                match_id=match.id,
                player_id=ps.get("player_id", ""),
                team_id=ps.get("team_id", ""),
                kills=int(ps.get("kills", 0)),
                damage_dealt=int(ps.get("damage_dealt", 0)),
                headshots=int(ps.get("headshots", 0)),
                revives_performed=int(ps.get("revives_performed", 0)),
                survived_time_seconds=int(ps.get("survived_time_seconds", 0)),
                mvp_score=float(ps.get("mvp_score", 0.0)),
                is_mvp=bool(ps.get("is_mvp", False)),
            )
            self.session.add(stats)
            created.append(stats)

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
