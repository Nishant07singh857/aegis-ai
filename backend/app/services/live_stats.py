# backend/app/services/live_stats.py
"""Live stats & standings writer (Phase 24, W6).

Closes the gap recorded in docs/PROJECT_INVENTORY.md §11.1 and
docs/SPECTATOR_COVERAGE.md rows 17/20/26: ``MatchService.record_player_stats``
— the only writer of per-player match stats — had NO live caller, so live
standings rode the simulator/demo path and the MVP terms for headshots
(×2) and revives (×3) starved at 0.

This service subscribes to the event bus ITSELF and turns the real event
stream into (a) persisted per-player stats via
``MatchService.record_player_stats`` (called as designed — never
rewritten here) and (b) a live, honestly-labelled standings table via
the tournament-configured :class:`PointsCalculator`.

Spectator-only inputs; nothing fabricated:

* **Damage is NEVER written.** No dealt-damage producer exists from a
  spectator feed (docs/SPECTATOR_COVERAGE.md §3 L1: HP drops measure
  damage *taken* by the observed side, never damage *dealt* by an
  attacker). Flushed rows therefore omit ``damage_dealt`` entirely, so
  ``record_player_stats`` persists it as ``NULL`` and the API keeps
  rendering "—".
* **Revives:** W2's ``revive_event`` payload names the REVIVED player
  (``player``/``team_id``, inferred from a KNOCKED→ALIVE team-status
  transition) but the reviver is NOT observed. The
  ``MatchPlayerStats`` schema carries only ``revives_performed`` — there
  is no ``times_revived`` column — so this writer credits NOBODY with a
  performed revive. It keeps the honest remainder in the accumulator:
  the revived player's ``times_revived`` and a team-level revive count
  surfaced as standings context (never as points).
* **Knocks** accumulate per player in memory for the live payload, but
  the stats schema has no knocks column, so they are not persisted
  (the player-stats API already labels knocks ``null`` for this reason).
* **Knocks are HINT-labelled (Phase 32, K-H4).** Every knock event
  from the spectator stream is uncorroborated by default: measured
  knock precision is P61.2%/R90.8% (docs/WEAPON_RECOGNITION.md —
  "hint, not ground truth"), so the live payload NEVER presents a
  knock as a confirmed fact. ``player_snapshot`` carries
  ``knocks`` (total observed) split into ``knocks_verified`` (only
  events with a trusted human provenance — ``knock_confirmed`` or a
  spotter/manual/observer ``source``) and ``knocks_unverified``,
  plus ``knocks_hint=True`` whenever any unverified knock exists
  for that player and a match-level ``knocks_hint`` on standings.
  No trusted knock producer exists in the codebase today (unlike
  headshots, there is no knock_confirmed producer), so in practice
  ALL live knocks are currently unverified — the verified branch
  exists so a future trusted producer is distinguished, not merged.
  Knocks never feed points: standings score finishes (kills) only,
  and the MVP formula does not score knocks either.
* **Headshots are source-gated (Integrity C2).** The killfeed icon
  matcher sets the same ``headshot`` flag as the spotter toggle, but
  its measured precision is 7.7 % (docs/WEAPON_RECOGNITION.md §2.7 —
  downstream consumption banned). Only events carrying a trusted
  human provenance (``headshot_confirmed`` from the feed contract, or
  an explicit spotter/manual/observer ``source``) increment the
  persisted ``headshots`` counter. Icon-derived flags are dropped
  from the stats with a counted, logged reason and accumulate only
  in the separate ``headshots_unverified`` live counter, which is
  never flushed to the DB and never feeds standings/MVP.
* **Placement** is computed ONLY when the stream truly determines it:
  at/after ``match_end``, when every known team but one has a
  ``team_wipe_event``. Otherwise placement stays ``None`` for every row,
  placement points stay 0, and final placement is left to the post-match
  flow. Mid-match tables are labelled ``live: true, provisional: true``.
* **Survival** is ``death_ts - match_start_ts`` for a player with a
  death event, ``match_end_ts - match_start_ts`` for a player still
  alive at match end, and ``None`` (unknown) when the match start was
  never observed or the match is still running. The persisted column is
  ``NOT NULL DEFAULT 0`` (pre-existing schema), so an unknown survival
  persists as 0 via ``record_player_stats``' own defaulting; the
  accumulator and the live payload keep the honest ``None``.

Persistence precedence — post-match manual entry WINS:

``record_player_stats`` is INSERT-only (no upsert), so this writer
tracks the exact row ids it created per match. Every flush first reads
the match's existing player-stat rows (and any team-result rows from
``record_match_results``):

* rows the writer did not create (or ANY ``match_team_stats`` row) mean
  the manual/final flow has written → the match is FINALISED for live
  writing: the writer deletes ONLY its own provisional rows, writes
  nothing further, and seals the match;
* otherwise it deletes ONLY its own previous rows and re-writes the
  full accumulated snapshot through ``record_player_stats``.

Manual rows are never deleted or overwritten. After the ``match_end``
final flush the match is sealed for live writing either way.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

import structlog

from app.analytics.points_calculator import PointsCalculator
from app.core.event_bus import EventRecord
from app.services.feed_adapter.schemas import HEADSHOT_TRUSTED_SOURCES

logger = structlog.get_logger("aegis.live_stats")

SUBSCRIBER_ID = "live_stats"

#: Bus event types this writer consumes (legacy bus names; the bus
#: resolves canonical aliases — kill.occurred / knock.occurred /
#: team.eliminated — to these subscribers at dispatch time).
LIVE_STATS_EVENT_TYPES = [
    "kill_event",
    "knock_event",
    "revive_event",
    "match_start",
    "match_end",
    "team_wipe_event",
]

#: Canonical → legacy normalisation for publishers using dotted names.
_TYPE_NORMALISE = {
    "kill.occurred": "kill_event",
    "knock.occurred": "knock_event",
    "team.eliminated": "team_wipe_event",
    "team_wipe": "team_wipe_event",
}

_UNKNOWN = {"", "unknown", "none"}


def _clean(value: Any) -> Optional[str]:
    """Return a usable identifier string, or None for missing/Unknown."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text.lower() not in _UNKNOWN else None


def _first(data: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        cleaned = _clean(data.get(key))
        if cleaned is not None:
            return cleaned
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ACCUMULATOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class PlayerAccum:
    player_id: str
    team_id: str = ""
    kills: int = 0
    knocks: int = 0  # knocks inflicted, TOTAL observed (live-only; no DB column)
    #: Phase 32: of ``knocks``, only those from a trusted human
    #: provenance (see _knock_trusted). The remainder is
    #: knocks - knocks_verified == knocks_unverified == HINT, never
    #: a confirmed fact. No trusted knock producer exists today, so
    #: this is 0 for every pure-vision stream.
    knocks_verified: int = 0
    headshots: int = 0  # TRUSTED (human-confirmed) only — persisted (C2)
    #: Icon-derived headshot flags dropped from the stats (C2). Kept
    #: only as a live diagnostic counter; never flushed, never scored.
    headshots_unverified: int = 0
    times_knocked: int = 0
    times_revived: int = 0  # revived BY a team-mate (performer unknown)
    death_ts: Optional[float] = None


@dataclass
class MatchAccum:
    match_id: str
    start_ts: Optional[float] = None
    end_ts: Optional[float] = None
    ended: bool = False
    sealed: bool = False
    players: Dict[str, PlayerAccum] = field(default_factory=dict)
    team_names: Dict[str, str] = field(default_factory=dict)
    #: Finishes whose TEAM is known but whose killer is not — counted
    #: for the team table, never attributed to an invented player.
    team_extra_finishes: Dict[str, int] = field(default_factory=dict)
    team_revives: Dict[str, int] = field(default_factory=dict)
    elimination_order: List[str] = field(default_factory=list)
    seen_event_ids: Set[str] = field(default_factory=set)
    events_since_flush: int = 0

    def register_team(self, team_id: Optional[str], team_name: Optional[str]) -> None:
        if team_id is None:
            return
        if team_name:
            self.team_names.setdefault(team_id, team_name)
        else:
            self.team_names.setdefault(team_id, team_id)

    def player(self, player_id: str, team_id: str = "") -> PlayerAccum:
        existing = self.players.get(player_id)
        if existing is None:
            existing = PlayerAccum(player_id=player_id, team_id=team_id)
            self.players[player_id] = existing
        elif team_id and not existing.team_id:
            existing.team_id = team_id
        return existing

    def known_teams(self) -> Set[str]:
        teams = set(self.team_names)
        teams |= {p.team_id for p in self.players.values() if p.team_id}
        teams |= set(self.elimination_order)
        return teams

    def team_finishes(self) -> Dict[str, int]:
        totals: Dict[str, int] = {}
        for p in self.players.values():
            if p.team_id:
                totals[p.team_id] = totals.get(p.team_id, 0) + p.kills
        for team_id, extra in self.team_extra_finishes.items():
            totals[team_id] = totals.get(team_id, 0) + extra
        return totals


def survival_seconds(acc: MatchAccum, player: PlayerAccum) -> Optional[int]:
    """Survival for one player, or None when not honestly knowable."""
    if acc.start_ts is None:
        return None
    if player.death_ts is not None:
        end = player.death_ts
    elif acc.ended and acc.end_ts is not None:
        end = acc.end_ts
    else:
        return None  # still alive in a running match: not yet knowable
    return max(0, int(end - acc.start_ts))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PERSISTENCE (precedence-aware flusher)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#: Flush outcomes.
FLUSH_WRITTEN = "written"
FLUSH_SKIPPED_FINALISED = "skipped_finalised"
FLUSH_FAILED = "failed"


def _default_session_factory() -> Callable[[], Any]:
    from app.db.base import AsyncSessionLocal

    return AsyncSessionLocal


class MatchServiceLiveFlusher:
    """Writes accumulated snapshots through ``MatchService`` safely.

    Implements the precedence contract in the module docstring: the
    post-match manual flow (foreign player-stat rows, or any
    ``record_match_results`` team rows) wins; live rows are provisional
    and are removed — never left to duplicate or contradict manual data.
    """

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        match_service_factory: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        self._session_factory = session_factory
        self._match_service_factory = match_service_factory
        #: match_id -> row ids THIS writer created (the only rows it
        #: may ever delete or replace).
        self._written_ids: Dict[str, Set[str]] = {}
        self._log = logger.bind(component="live_stats_flusher")

    def _factory(self) -> Callable[[], Any]:
        if self._session_factory is None:
            self._session_factory = _default_session_factory()
        return self._session_factory

    def _match_service(self, session: Any) -> Any:
        if self._match_service_factory is not None:
            return self._match_service_factory(session)
        from app.services.match_service import MatchService

        return MatchService(session)

    async def _existing_player_stat_ids(
        self, session: Any, match_id: str
    ) -> Set[str]:
        from sqlalchemy import select

        from app.services.match_service import MatchPlayerStats

        result = await session.execute(
            select(MatchPlayerStats.id).where(
                MatchPlayerStats.match_id == match_id
            )
        )
        return {str(v) for v in result.scalars().all()}

    async def _has_team_results(self, session: Any, match_id: str) -> bool:
        from sqlalchemy import select

        from app.services.match_service import MatchTeamStats

        result = await session.execute(
            select(MatchTeamStats.id)
            .where(MatchTeamStats.match_id == match_id)
            .limit(1)
        )
        return len(result.scalars().all()) > 0

    async def _delete_ids(self, session: Any, ids: Set[str]) -> None:
        if not ids:
            return
        from sqlalchemy import delete

        from app.services.match_service import MatchPlayerStats

        await session.execute(
            delete(MatchPlayerStats).where(MatchPlayerStats.id.in_(sorted(ids)))
        )

    async def __call__(self, match_id: str, rows: List[Dict[str, Any]]) -> str:
        """Flush one snapshot. Returns a FLUSH_* outcome; never raises."""
        try:
            factory = self._factory()
            async with factory() as session:
                existing = await self._existing_player_stat_ids(session, match_id)
                own = set(self._written_ids.get(match_id, set()))
                foreign = existing - own
                finalised = bool(foreign) or await self._has_team_results(
                    session, match_id
                )
                if finalised:
                    # Manual/final flow has written — it wins. Remove
                    # ONLY our provisional rows and stop.
                    if own:
                        await self._delete_ids(session, own)
                        await session.commit()
                    self._written_ids.pop(match_id, None)
                    self._log.info(
                        "live_flush_skipped_finalised",
                        match_id=match_id,
                        foreign_player_rows=len(foreign),
                    )
                    return FLUSH_SKIPPED_FINALISED
                if own:
                    # Replace our own provisional snapshot wholesale
                    # (record_player_stats' commit persists the delete).
                    await self._delete_ids(session, own)
                created = await self._match_service(session).record_player_stats(
                    match_id, rows
                )
                self._written_ids[match_id] = {
                    str(obj.id) for obj in created if getattr(obj, "id", None)
                }
                return FLUSH_WRITTEN
        except Exception as exc:  # persistence must never break the bus
            self._log.warning(
                "live_flush_failed", match_id=match_id, error=str(exc)[:300]
            )
            return FLUSH_FAILED


#: Async callable (match_id, rows) -> FLUSH_* outcome.
Flusher = Callable[[str, List[Dict[str, Any]]], Any]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SERVICE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class LiveStatsStats:
    received: int = 0
    counted: int = 0
    deduped: int = 0
    handler_errors: int = 0
    flushes: int = 0
    #: Headshot flags dropped because their source is not a trusted
    #: human producer (Integrity C2) — counted, never silently lost.
    headshots_dropped_unverified: int = 0
    #: Phase 32: knock events that arrived WITHOUT trusted provenance
    #: (hint-labelled, never confirmed). Counted, never hidden.
    knocks_unverified: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)


class LiveStatsService:
    """Bus subscriber accumulating live per-player stats + standings."""

    def __init__(
        self,
        bus: Any = None,
        *,
        points_calculator: Optional[PointsCalculator] = None,
        flusher: Optional[Flusher] = None,
        session_factory: Optional[Callable[[], Any]] = None,
        flush_every: int = 10,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._calculator = points_calculator or PointsCalculator()
        self._session_factory = session_factory
        if flusher is None:
            flusher = MatchServiceLiveFlusher(session_factory=session_factory)
        self._flusher: Flusher = flusher
        self._flush_every = max(1, int(flush_every))
        self._calculator_configured = points_calculator is not None
        self._matches: Dict[str, MatchAccum] = {}
        self._current_match_id: Optional[str] = None
        self._started = False
        self.stats = LiveStatsStats()
        self._log = logger.bind(component="live_stats")

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the live event types on the bus. Idempotent."""
        if self._started or self._bus is None:
            return
        self._bus.subscribe(
            subscriber_id=SUBSCRIBER_ID,
            callback=self.handle_record,
            event_types=list(LIVE_STATS_EVENT_TYPES),
        )
        self._started = True
        self._log.info("live_stats_subscribed", event_types=LIVE_STATS_EVENT_TYPES)

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._bus.unsubscribe(SUBSCRIBER_ID)
        except Exception:  # pragma: no cover - defensive
            pass
        self._started = False

    @property
    def running(self) -> bool:
        return self._started

    # ── event intake ─────────────────────────────────────────────

    def _accum(self, match_id: str) -> MatchAccum:
        acc = self._matches.get(match_id)
        if acc is None:
            acc = MatchAccum(match_id=match_id)
            self._matches[match_id] = acc
        return acc

    @staticmethod
    def _event_ts(record: Any, data: Dict[str, Any]) -> Optional[float]:
        raw = data.get("timestamp")
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw)
        event = getattr(record, "event", None)
        ts_ms = getattr(event, "timestamp_ms", None) if event else None
        if isinstance(ts_ms, (int, float)) and ts_ms > 0:
            return float(ts_ms) / 1000.0
        return None

    async def handle_record(self, record: EventRecord) -> None:
        """Bus callback: route one event record. Never raises."""
        event = getattr(record, "event", None)
        event_type = getattr(event, "event_type", "") if event else ""
        data = getattr(event, "data", None) if event else None
        if not isinstance(data, dict):
            return
        event_type = _TYPE_NORMALISE.get(event_type, event_type)
        self.stats.received += 1
        self.stats.by_type[event_type] = self.stats.by_type.get(event_type, 0) + 1
        try:
            match_id = (
                getattr(event, "match_id", None)
                or data.get("match_id")
                or self._current_match_id
            )
            if match_id is None:
                # Without any match identity there is nothing honest to
                # accumulate against; a placeholder match is never invented.
                if event_type not in ("match_start",):
                    return
                match_id = f"live-{uuid.uuid4().hex[:8]}"
            match_id = str(match_id)
            acc = self._accum(match_id)

            # Dedupe by event id: the source payload id survives bus
            # redelivery/re-ingest (new bus event_id, same source id),
            # so prefer it; the bus event_id covers id-less payloads
            # (e.g. revive_event) on same-record redelivery.
            dedupe_keys = [
                k
                for k in (
                    _clean(data.get("id")) or _clean(data.get("event_id")),
                    _clean(getattr(event, "event_id", None)),
                )
                if k
            ]
            if dedupe_keys and dedupe_keys[0] in acc.seen_event_ids:
                self.stats.deduped += 1
                return
            for key in dedupe_keys:
                acc.seen_event_ids.add(key)

            if acc.sealed:
                return

            ts = self._event_ts(record, data)
            handler = self._handlers().get(event_type)
            if handler is not None:
                await handler(acc, data, ts)
                self.stats.counted += 1
        except Exception as exc:  # the subscriber must not break dispatch
            self.stats.handler_errors += 1
            self._log.error(
                "live_stats_handler_error",
                event_type=event_type,
                error=str(exc)[:300],
            )

    def _handlers(self) -> Dict[str, Any]:
        return {
            "kill_event": self._on_kill,
            "knock_event": self._on_knock,
            "revive_event": self._on_revive,
            "team_wipe_event": self._on_team_wipe,
            "match_start": self._on_match_start,
            "match_end": self._on_match_end,
        }

    # ── per-type handlers ────────────────────────────────────────

    async def _on_match_start(
        self, acc: MatchAccum, data: Dict[str, Any], ts: Optional[float]
    ) -> None:
        if acc.start_ts is None:
            acc.start_ts = ts if ts is not None else self._clock()
        acc.ended = False
        self._current_match_id = acc.match_id
        await self._configure_calculator()

    async def _on_match_end(
        self, acc: MatchAccum, data: Dict[str, Any], ts: Optional[float]
    ) -> None:
        if acc.end_ts is None:
            acc.end_ts = ts if ts is not None else self._clock()
        acc.ended = True
        await self._flush(acc, final=True)

    @staticmethod
    def _headshot_trusted(data: Dict[str, Any]) -> bool:
        """Whether a flagged headshot may be persisted (Integrity C2).

        Trusted iff the feed contract's confirmed marker is set, or the
        payload carries an explicit trusted human source. The raw
        ``headshot`` flag alone is NEVER sufficient: the vision icon
        matcher sets it at 7.7 % measured precision
        (docs/WEAPON_RECOGNITION.md §2.7).
        """
        if data.get("headshot_confirmed") is True:
            return True
        source = str(
            data.get("headshot_source") or data.get("source") or ""
        ).strip().lower()
        return source in HEADSHOT_TRUSTED_SOURCES

    def _count_headshot(
        self, acc: MatchAccum, killer: PlayerAccum, data: Dict[str, Any]
    ) -> None:
        """Apply the C2 source gate to one flagged kill/knock event."""
        if data.get("headshot") is not True:
            return
        if self._headshot_trusted(data):
            killer.headshots += 1
            return
        # Dropped with a counted, logged reason — the same honesty
        # pattern VisionIntelligence uses for unmapped events. The
        # flag survives only in the unverified live counter.
        killer.headshots_unverified += 1
        self.stats.headshots_dropped_unverified += 1
        self._log.info(
            "headshot_dropped_unverified_source",
            match_id=acc.match_id,
            player=killer.player_id,
            source=str(
                data.get("headshot_source") or data.get("source") or "unknown"
            ),
            reason=(
                "headshot flag is not from a trusted human producer "
                "(spotter/manual/observer); icon-derived precision is "
                "7.7% per docs/WEAPON_RECOGNITION.md §2.7"
            ),
        )

    async def _on_kill(
        self, acc: MatchAccum, data: Dict[str, Any], ts: Optional[float]
    ) -> None:
        killer_id, killer_team, killer_name = self._actor(data, "killer")
        victim_id, victim_team, victim_name = self._actor(data, "victim")
        acc.register_team(killer_team, killer_name)
        acc.register_team(victim_team, victim_name)
        if killer_id is not None:
            killer = acc.player(killer_id, killer_team or "")
            killer.kills += 1
            self._count_headshot(acc, killer, data)
        elif killer_team is not None:
            acc.team_extra_finishes[killer_team] = (
                acc.team_extra_finishes.get(killer_team, 0) + 1
            )
        if victim_id is not None:
            victim = acc.player(victim_id, victim_team or "")
            if victim.death_ts is None:
                victim.death_ts = ts if ts is not None else self._clock()
        acc.events_since_flush += 1
        await self._maybe_flush(acc)

    @staticmethod
    def _knock_trusted(data: Dict[str, Any]) -> bool:
        """Whether a knock counts as VERIFIED (Phase 32, K-H4).

        Mirrors the headshot C2 gate: an explicit confirmed marker,
        or a trusted human source (spotter/manual/observer). The
        vision killfeed (source ``vision_killfeed``, no
        ``knock_confirmed``) is NEVER trusted — its knock precision
        is P61.2% (hint, not ground truth). No knock_confirmed
        producer exists in the codebase today, so this branch is
        forward-compatible: it distinguishes a future trusted
        producer instead of silently merging it with vision hints.
        """
        if data.get("knock_confirmed") is True:
            return True
        source = str(
            data.get("knock_source") or data.get("source") or ""
        ).strip().lower()
        return source in HEADSHOT_TRUSTED_SOURCES

    async def _on_knock(
        self, acc: MatchAccum, data: Dict[str, Any], ts: Optional[float]
    ) -> None:
        killer_id, killer_team, killer_name = self._actor(data, "killer")
        victim_id, victim_team, victim_name = self._actor(data, "victim")
        acc.register_team(killer_team, killer_name)
        acc.register_team(victim_team, victim_name)
        if killer_id is not None:
            killer = acc.player(killer_id, killer_team or "")
            killer.knocks += 1
            if self._knock_trusted(data):
                killer.knocks_verified += 1
            else:
                self.stats.knocks_unverified += 1
            self._count_headshot(acc, killer, data)
        if victim_id is not None:
            victim = acc.player(victim_id, victim_team or "")
            victim.times_knocked += 1
        acc.events_since_flush += 1
        await self._maybe_flush(acc)

    async def _on_revive(
        self, acc: MatchAccum, data: Dict[str, Any], ts: Optional[float]
    ) -> None:
        # W2 revive_event shape: {player: <revived>, team_id, team_name,
        # inferred, source, ...}. The reviver is NOT in the payload —
        # nobody is credited with a performed revive (see docstring).
        revived_id = _first(
            data, "revived_player_id", "revived_player", "player_id", "player"
        )
        team_id = _first(data, "team_id", "revived_team_id")
        team_name = _first(data, "team_name", "revived_team")
        acc.register_team(team_id, team_name)
        if team_id is not None:
            acc.team_revives[team_id] = acc.team_revives.get(team_id, 0) + 1
        if revived_id is not None:
            revived = acc.player(revived_id, team_id or "")
            revived.times_revived += 1

    async def _on_team_wipe(
        self, acc: MatchAccum, data: Dict[str, Any], ts: Optional[float]
    ) -> None:
        team_id = _first(
            data,
            "eliminated_team_id",
            "victim_team_id",
            "victimTeamId",
            "team_id",
        ) or _first(data, "victimTeam", "team")
        acc.register_team(team_id, _first(data, "team_name"))
        if team_id is not None and team_id not in acc.elimination_order:
            acc.elimination_order.append(team_id)
        await self._flush(acc, final=False)

    @staticmethod
    def _actor(
        data: Dict[str, Any], role: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Resolve (player_id, team_id, team_name) for killer/victim.

        Explicit id fields win; the display name doubles as the id only
        when no id field exists (feed/spotter payloads carry IGNs, which
        is how the existing player-stats read path resolves them).
        """
        if role == "killer":
            player_id = _first(
                data, "killer_player_id", "killer_id", "player_id"
            ) or _first(data, "killer")
            team_id = _first(
                data, "killerTeamId", "killer_team_id", "killerTeamID", "team_id"
            ) or _first(data, "killerTeam", "killer_team")
            team_name = _first(data, "killerTeamName", "killer_team_name")
            if team_name is None and _first(data, "killerTeamId", "killer_team_id"):
                team_name = _first(data, "killerTeam")
        else:
            player_id = _first(
                data, "victim_player_id", "victim_id"
            ) or _first(data, "victim")
            team_id = _first(
                data, "victimTeamId", "victim_team_id", "victimTeamID"
            ) or _first(data, "victimTeam", "victim_team")
            team_name = _first(data, "victimTeamName", "victim_team_name")
            if team_name is None and _first(data, "victimTeamId", "victim_team_id"):
                team_name = _first(data, "victimTeam")
        return player_id, team_id, team_name

    # ── calculator configuration ─────────────────────────────────

    async def _configure_calculator(self) -> None:
        """Load the active tournament's scoring (Phase 13 config).

        Only when a session factory exists; an injected calculator
        (tests) is left untouched. Never raises — the calculator itself
        degrades to the documented Krafton BMSD defaults.
        """
        if self._session_factory is None or self._calculator_configured:
            return
        try:
            async with self._session_factory() as session:
                await self._calculator.configure_from_active_tournament(session)
            self._calculator_configured = True
        except Exception as exc:  # pragma: no cover - defensive
            self._log.warning(
                "live_stats_calculator_configure_failed", error=str(exc)[:200]
            )

    # ── flushing ─────────────────────────────────────────────────

    async def _maybe_flush(self, acc: MatchAccum) -> None:
        if acc.events_since_flush >= self._flush_every:
            await self._flush(acc, final=False)

    async def _flush(self, acc: MatchAccum, *, final: bool) -> None:
        rows = self.player_stat_rows(acc.match_id)
        if rows:
            outcome = await self._flusher(acc.match_id, rows)
            self.stats.flushes += 1
            acc.events_since_flush = 0
            if outcome == FLUSH_SKIPPED_FINALISED:
                acc.sealed = True
        if final:
            acc.sealed = True

    # ── snapshots / reads ────────────────────────────────────────

    def player_stat_rows(self, match_id: str) -> List[Dict[str, Any]]:
        """Accumulated rows in ``record_player_stats`` input shape.

        ``damage_dealt`` is deliberately ABSENT (never written — see
        module docstring). ``revives_performed`` is honestly 0 for every
        player: the revive producer never names a performer.
        ``survived_time_seconds`` is included only when known.
        """
        acc = self._matches.get(match_id)
        if acc is None:
            return []
        rows: List[Dict[str, Any]] = []
        for p in acc.players.values():
            row: Dict[str, Any] = {
                "player_id": p.player_id,
                "team_id": p.team_id,
                "kills": p.kills,
                "headshots": p.headshots,
                "revives_performed": 0,
            }
            survived = survival_seconds(acc, p)
            if survived is not None:
                row["survived_time_seconds"] = survived
            rows.append(row)
        return rows

    def player_snapshot(self, match_id: str) -> List[Dict[str, Any]]:
        """Full live per-player view (incl. non-persisted counters)."""
        acc = self._matches.get(match_id)
        if acc is None:
            return []
        return [
            {
                "player_id": p.player_id,
                "team_id": p.team_id,
                "finishes": p.kills,
                # Phase 32 knock hint-labelling (K-H4): knocks is the
                # TOTAL observed, never a confirmed count. Verified
                # (trusted human provenance) vs unverified (spectator
                # hint, P61.2%) is split explicitly, and knocks_hint
                # is True whenever any unverified knock exists, so a
                # UI can never render the total as confirmed kills.
                "knocks": p.knocks,
                "knocks_verified": p.knocks_verified,
                "knocks_unverified": p.knocks - p.knocks_verified,
                "knocks_hint": (p.knocks - p.knocks_verified) > 0,
                "knocks_status": (
                    "hint"
                    if (p.knocks - p.knocks_verified) > 0
                    else ("verified" if p.knocks_verified > 0 else "none")
                ),
                # Trusted human-confirmed headshots only (C2) — this
                # is the number that is persisted and scored.
                "headshots": p.headshots,
                # Icon-derived flags dropped from the stats (C2):
                # diagnostic only, never persisted or scored.
                "headshots_unverified": p.headshots_unverified,
                "times_knocked": p.times_knocked,
                "times_revived": p.times_revived,
                "revives_performed": None,  # performer never observed
                "damage_dealt": None,  # no dealt-damage producer (L1)
                "survival_seconds": survival_seconds(acc, p),
            }
            for p in acc.players.values()
        ]

    def _placements(self, acc: MatchAccum) -> Optional[Dict[str, int]]:
        """Placement per team — ONLY when the stream determines it.

        Requires match end + every known team but one eliminated. The
        elimination order then fixes every placement (first wiped =
        last place) and the sole survivor is the winner.
        """
        if not acc.ended:
            return None
        teams = acc.known_teams()
        if len(teams) < 2 or len(acc.elimination_order) != len(teams) - 1:
            return None
        placements: Dict[str, int] = {}
        n = len(teams)
        for idx, team_id in enumerate(acc.elimination_order):
            placements[team_id] = n - idx
        for team_id in teams:
            if team_id not in placements:
                placements[team_id] = 1
        return placements

    def live_standings(self, match_id: Optional[str] = None) -> Dict[str, Any]:
        """Live standings payload (honestly labelled provisional).

        Points reuse the tournament-configured PointsCalculator exactly
        as ``MatchService.record_match_results`` does: placement points
        from the calculator's table + finishes × points-per-kill. No
        bonus rules are invented for the live table.
        """
        resolved = match_id or self._current_match_id
        acc = self._matches.get(resolved) if resolved else None
        if acc is None:
            # Fall back to the most recently touched match with data.
            for candidate in reversed(list(self._matches.values())):
                if candidate.players or candidate.known_teams():
                    acc = candidate
                    break
        if acc is None:
            return {
                "match_id": resolved,
                "live": True,
                "provisional": True,
                "placement_known": False,
                "match_ended": False,
                "config_source": self._calculator.config_source,
                "points_per_kill": self._calculator.points_per_kill,
                "standings": [],
                "players": [],
                # Phase 32: no knocks observed at all here.
                "knocks_hint": False,
                "knocks_verified": 0,
                "knocks_unverified": 0,
            }
        finishes = acc.team_finishes()
        placements = self._placements(acc)
        rows: List[Dict[str, Any]] = []
        for team_id in acc.known_teams():
            kills = finishes.get(team_id, 0)
            placement = placements.get(team_id) if placements else None
            place_pts = (
                self._calculator.placement_points.get_points(placement)
                if placement
                else 0
            )
            kill_pts = kills * self._calculator.points_per_kill
            rows.append(
                {
                    "team_id": team_id,
                    "team_name": acc.team_names.get(team_id, team_id),
                    "finishes": kills,
                    "placement": placement,
                    "placement_points": place_pts,
                    "kill_points": kill_pts,
                    "total_points": place_pts + kill_pts,
                    "wwcd": 1 if placement == 1 else 0,
                    # Standings context only — never points.
                    "revives": acc.team_revives.get(team_id, 0),
                    "eliminated": team_id in acc.elimination_order,
                }
            )
        # Rank: total desc, finishes desc (the calculator's first
        # tiebreaker), then team_id for a stable order.
        rows.sort(key=lambda r: (-r["total_points"], -r["finishes"], r["team_id"]))
        for idx, row in enumerate(rows, start=1):
            row["rank"] = idx
        players = self.player_snapshot(acc.match_id)
        # Phase 32: match-level knock hint — True whenever ANY
        # player's knocks include an uncorroborated (vision) knock.
        # Points above are finishes-only; knocks never score here.
        knocks_verified = sum(pl["knocks_verified"] for pl in players)
        knocks_unverified = sum(pl["knocks_unverified"] for pl in players)
        return {
            "match_id": acc.match_id,
            "live": True,
            "provisional": True,
            "placement_known": placements is not None,
            "match_ended": acc.ended,
            "config_source": self._calculator.config_source,
            "points_per_kill": self._calculator.points_per_kill,
            "standings": rows,
            "players": players,
            "knocks_hint": knocks_unverified > 0,
            "knocks_verified": knocks_verified,
            "knocks_unverified": knocks_unverified,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "running": self._started,
            "subscriber_id": SUBSCRIBER_ID,
            "event_types": list(LIVE_STATS_EVENT_TYPES),
            "current_match_id": self._current_match_id,
            "matches_tracked": len(self._matches),
            "stats": {
                "received": self.stats.received,
                "counted": self.stats.counted,
                "deduped": self.stats.deduped,
                "handler_errors": self.stats.handler_errors,
                "flushes": self.stats.flushes,
                "headshots_dropped_unverified": (
                    self.stats.headshots_dropped_unverified
                ),
                "knocks_unverified": self.stats.knocks_unverified,
                "by_type": dict(self.stats.by_type),
            },
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SINGLETON (wired in app/main.py lifespan)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_service: Optional[LiveStatsService] = None


def build_live_stats_service(
    bus: Any, session_factory: Optional[Callable[[], Any]] = None
) -> LiveStatsService:
    """Build the production service over the shared session factory."""
    global _service
    if session_factory is None:
        session_factory = _default_session_factory()
    _service = LiveStatsService(
        bus=bus,
        session_factory=session_factory,
        flusher=MatchServiceLiveFlusher(session_factory=session_factory),
    )
    return _service


def get_live_stats_service() -> Optional[LiveStatsService]:
    return _service
