# backend/tests/unit/test_live_stats.py
"""Phase 24 (W6) — live stats & standings writer.

Covers the required behaviours of ``app.services.live_stats``:

* synthetic bus stream → per-player finishes / knocks / headshots /
  revives accumulate correctly;
* duplicate event ids counted once (source-id AND bus-id redelivery);
* survival computed when death is known, null when it is not;
* standings math matches the tournament-configured PointsCalculator;
* provisional labelling present on the live table;
* match_end triggers a final flush through the writer boundary, and
  flushed rows NEVER carry damage;
* revive honesty: W2's payload names no reviver, so nobody is credited
  with a performed revive — team-level + times_revived only;
* persistence precedence: the MatchServiceLiveFlusher yields to the
  post-match manual flow (foreign player rows / any team results),
  deleting ONLY its own provisional rows;
* empty stream → no rows, no standings, no errors, no writer calls.

No network, no real DB, no Redis: a fake bus delivers records straight
to the subscribed callback, and the flusher is tested against fake
sessions in the established test_mvp_unification style.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.analytics.points_calculator import PointsCalculator
from app.services.live_stats import (
    FLUSH_SKIPPED_FINALISED,
    FLUSH_WRITTEN,
    LiveStatsService,
    MatchServiceLiveFlusher,
)

pytestmark = pytest.mark.unit

MATCH = "match-1"
T0 = 1_000.0


# ── fakes ─────────────────────────────────────────────────────────────


class FakeBus:
    """Captures subscriptions; deliver() invokes matching callbacks."""

    def __init__(self) -> None:
        self.subs: Dict[str, Any] = {}

    def subscribe(self, subscriber_id, callback, event_types, **kwargs):
        self.subs[subscriber_id] = (callback, list(event_types))
        return subscriber_id

    def unsubscribe(self, subscriber_id):
        self.subs.pop(subscriber_id, None)
        return True

    async def deliver(
        self,
        event_type: str,
        data: Dict[str, Any],
        *,
        match_id: Optional[str] = MATCH,
        bus_event_id: Optional[str] = None,
    ) -> None:
        record = SimpleNamespace(
            event=SimpleNamespace(
                event_id=bus_event_id or str(uuid.uuid4()),
                event_type=event_type,
                data=data,
                match_id=match_id,
                timestamp_ms=int(T0 * 1000),
            )
        )
        for callback, types in list(self.subs.values()):
            if event_type in types:
                await callback(record)


class SpyFlusher:
    """Writer at the service boundary: records every flush."""

    def __init__(self, outcome: str = FLUSH_WRITTEN) -> None:
        self.calls: List[Any] = []
        self.outcome = outcome

    async def __call__(self, match_id: str, rows: List[Dict[str, Any]]) -> str:
        self.calls.append((match_id, [dict(r) for r in rows]))
        return self.outcome


def make_service(flush_every: int = 10, calculator: Optional[PointsCalculator] = None):
    bus = FakeBus()
    spy = SpyFlusher()
    if calculator is None:
        calculator = PointsCalculator()
        calculator.configure_from_tournament(
            {"id": "tour-test", "points_per_kill": 1, "placement_points": {"1": 10}}
        )
    svc = LiveStatsService(
        bus=bus, points_calculator=calculator, flusher=spy, flush_every=flush_every
    )
    svc.start()
    return svc, bus, spy


def kill_event(
    eid: str,
    killer: str,
    killer_team: str,
    victim: str,
    victim_team: str,
    *,
    headshot: bool = False,
    ts: float = T0 + 100,
) -> Dict[str, Any]:
    """Feed-adapter to_bus_payload() shape (headshot always present).

    Integrity C2: bus payloads now also carry provenance. This helper
    simulates the TRUSTED producer — a spotter-confirmed event as
    stamped server-side at ingest — so ``headshot_confirmed`` mirrors
    the flag. Icon-derived (vision) events carry
    ``headshot_confirmed=False`` and are covered in
    test_integrity_headshot_stats.py.
    """
    return {
        "id": eid,
        "timestamp": ts,
        "match_id": MATCH,
        "killer": killer,
        "killerTeam": killer_team,
        "victim": victim,
        "victimTeam": victim_team,
        "weapon": "M416",
        "isKnock": False,
        "headshot": headshot,
        "headshot_confirmed": headshot,
        "headshot_source": "spotter",
        "source": "spotter",
    }


def knock_event(eid: str, killer: str, kt: str, victim: str, vt: str, **kw):
    data = kill_event(eid, killer, kt, victim, vt, **kw)
    data["isKnock"] = True
    return data


async def start_match(bus: FakeBus, ts: float = T0) -> None:
    await bus.deliver(
        "match_start", {"id": "ms-1", "timestamp": ts, "match_id": MATCH}
    )


# ── accumulation ──────────────────────────────────────────────────────


class TestAccumulation:
    async def test_finishes_knocks_headshots_accumulate(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "kill_event",
            kill_event("e1", "Scout", "teamA", "Jonathan", "teamB", headshot=True, ts=T0 + 60),
        )
        await bus.deliver(
            "kill_event",
            kill_event("e2", "Scout", "teamA", "Mavi", "teamB", ts=T0 + 120),
        )
        await bus.deliver(
            "knock_event",
            knock_event("e3", "Scout", "teamA", "Zgod", "teamC", headshot=True, ts=T0 + 180),
        )
        await bus.deliver(
            "knock_event",
            knock_event("e4", "Ninja", "teamB", "Scout", "teamA", ts=T0 + 200),
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        scout = players["Scout"]
        assert scout["finishes"] == 2
        assert scout["knocks"] == 1
        assert scout["headshots"] == 2  # one kill + one knock, both flagged
        assert scout["times_knocked"] == 1
        assert players["Jonathan"]["finishes"] == 0
        assert players["Zgod"]["times_knocked"] == 1
        assert players["Ninja"]["knocks"] == 1
        assert players["Ninja"]["headshots"] == 0

    async def test_duplicate_event_ids_counted_once(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        data = kill_event("dup-1", "Scout", "teamA", "Jonathan", "teamB")
        # Same source id, NEW bus event id (re-ingest / redelivery).
        await bus.deliver("kill_event", data, bus_event_id="bus-1")
        await bus.deliver("kill_event", dict(data), bus_event_id="bus-2")
        # Same bus record redelivered with an id-less payload variant.
        await bus.deliver("kill_event", data, bus_event_id="bus-1")
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Scout"]["finishes"] == 1
        assert svc.stats.deduped >= 1

    async def test_revive_credits_nobody_as_performer(self):
        """W2 revive_event: revived player named, reviver unknown."""
        svc, bus, spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "knock_event",
            knock_event("k1", "Ninja", "teamB", "Scout", "teamA", ts=T0 + 50),
        )
        # Exact W2 payload shape (vision_intelligence/service.py).
        await bus.deliver(
            "revive_event",
            {
                "inferred": True,
                "source": "team_status_transition",
                "team_id": "teamA",
                "team_name": "Soul",
                "player": "Scout",
                "slot_index": 1,
                "knock_duration_s": 12.0,
                "timestamp": T0 + 62,
                "match_id": MATCH,
            },
            bus_event_id="rev-1",
        )
        # Id-less payload redelivered on the same bus record: deduped.
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Scout"]["times_revived"] == 1
        standings = svc.live_standings(MATCH)
        team_a = next(r for r in standings["standings"] if r["team_id"] == "teamA")
        assert team_a["revives"] == 1
        # No performer credit anywhere: snapshot says unknown, flushed
        # rows carry revives_performed == 0 for every player.
        assert all(p["revives_performed"] is None for p in players.values())
        rows = svc.player_stat_rows(MATCH)
        assert all(r["revives_performed"] == 0 for r in rows)

    async def test_unknown_killer_counts_team_finish_only(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        data = kill_event("e9", "Unknown", "teamA", "Jonathan", "teamB")
        await bus.deliver("kill_event", data)
        standings = svc.live_standings(MATCH)
        team_a = next(r for r in standings["standings"] if r["team_id"] == "teamA")
        assert team_a["finishes"] == 1  # team table honest…
        players = svc.player_snapshot(MATCH)
        assert all(p["player_id"] != "Unknown" for p in players)  # …no invented player


# ── survival ──────────────────────────────────────────────────────────


class TestSurvival:
    async def test_survival_from_death_and_match_end(self):
        svc, bus, _spy = make_service()
        await start_match(bus, ts=T0)
        await bus.deliver(
            "kill_event",
            kill_event("e1", "Scout", "teamA", "Jonathan", "teamB", ts=T0 + 300),
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Jonathan"]["survival_seconds"] == 300
        # Scout is still alive in a running match: not yet knowable.
        assert players["Scout"]["survival_seconds"] is None
        await bus.deliver(
            "match_end", {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH}
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Scout"]["survival_seconds"] == 900
        assert players["Jonathan"]["survival_seconds"] == 300

    async def test_survival_null_without_match_start(self):
        svc, bus, _spy = make_service()
        # No match_start observed: finishes count, survival stays null.
        await bus.deliver(
            "kill_event",
            kill_event("e1", "Scout", "teamA", "Jonathan", "teamB", ts=T0 + 300),
        )
        await bus.deliver(
            "match_end", {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH}
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Jonathan"]["survival_seconds"] is None
        assert players["Scout"]["survival_seconds"] is None
        rows = svc.player_stat_rows(MATCH)
        assert all("survived_time_seconds" not in r for r in rows)


# ── standings ─────────────────────────────────────────────────────────


class TestLiveStandings:
    async def _two_team_stream(self, bus: FakeBus) -> None:
        await start_match(bus)
        for i in range(5):  # teamA: 5 finishes
            await bus.deliver(
                "kill_event",
                kill_event(f"a{i}", "Scout", "teamA", f"B{i}", "teamB", ts=T0 + 10 * i),
            )
        for i in range(3):  # teamB: 3 finishes
            await bus.deliver(
                "kill_event",
                kill_event(f"b{i}", "Ninja", "teamB", f"A{i}", "teamA", ts=T0 + 100 + 10 * i),
            )

    async def test_mid_match_math_and_provisional_labelling(self):
        calc = PointsCalculator()
        calc.configure_from_tournament(
            {"id": "tour-x", "points_per_kill": 2, "placement_points": {"1": 10, "2": 6}}
        )
        svc, bus, _spy = make_service(calculator=calc)
        await self._two_team_stream(bus)
        payload = svc.live_standings(MATCH)
        assert payload["live"] is True
        assert payload["provisional"] is True
        assert payload["placement_known"] is False
        assert payload["match_ended"] is False
        rows = {r["team_id"]: r for r in payload["standings"]}
        # Mid-match: kill points only (2/finish), no placement invented.
        assert rows["teamA"]["placement"] is None
        assert rows["teamA"]["placement_points"] == 0
        assert rows["teamA"]["kill_points"] == 10
        assert rows["teamA"]["total_points"] == 10
        assert rows["teamB"]["total_points"] == 6
        assert payload["standings"][0]["team_id"] == "teamA"
        assert payload["standings"][0]["rank"] == 1

    async def test_placement_once_stream_determines_it(self):
        calc = PointsCalculator()
        calc.configure_from_tournament(
            {"id": "tour-x", "points_per_kill": 2, "placement_points": {"1": 10, "2": 6}}
        )
        svc, bus, _spy = make_service(calculator=calc)
        await self._two_team_stream(bus)
        await bus.deliver(
            "team_wipe_event",
            {"id": "w1", "timestamp": T0 + 500, "match_id": MATCH, "team": "teamB", "eliminated_by": "Scout"},
        )
        # Still mid-match: placement is NOT claimed before match end.
        mid = svc.live_standings(MATCH)
        assert mid["placement_known"] is False
        await bus.deliver(
            "match_end", {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH}
        )
        payload = svc.live_standings(MATCH)
        assert payload["placement_known"] is True
        rows = {r["team_id"]: r for r in payload["standings"]}
        # Winner teamA: 10 placement + 5*2 kill = 20. teamB: 6 + 3*2 = 12.
        # Cross-check against the calculator itself (no forked math).
        assert rows["teamA"]["placement"] == 1
        assert rows["teamA"]["wwcd"] == 1
        assert rows["teamA"]["placement_points"] == calc.placement_points.get_points(1)
        assert rows["teamA"]["total_points"] == 10 + 5 * calc.points_per_kill
        assert rows["teamB"]["placement"] == 2
        assert rows["teamB"]["total_points"] == 6 + 3 * calc.points_per_kill

    async def test_placement_withheld_when_elimination_incomplete(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "kill_event", kill_event("e1", "Scout", "teamA", "P1", "teamB")
        )
        await bus.deliver(
            "kill_event", kill_event("e2", "Zgod", "teamC", "P2", "teamB")
        )
        await bus.deliver(
            "team_wipe_event",
            {"id": "w1", "timestamp": T0 + 500, "match_id": MATCH, "team": "teamB"},
        )
        await bus.deliver(
            "match_end", {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH}
        )
        payload = svc.live_standings(MATCH)
        # 3 known teams, only 1 wiped: winner NOT determined.
        assert payload["placement_known"] is False
        assert all(r["placement"] is None for r in payload["standings"])
        assert all(r["wwcd"] == 0 for r in payload["standings"])


# ── flushing ──────────────────────────────────────────────────────────


class TestFlushing:
    async def test_match_end_final_flush_and_no_damage(self):
        svc, bus, spy = make_service(flush_every=100)
        await start_match(bus)
        await bus.deliver(
            "kill_event",
            kill_event("e1", "Scout", "teamA", "Jonathan", "teamB", headshot=True, ts=T0 + 60),
        )
        assert spy.calls == []  # cadence not reached, no premature write
        await bus.deliver(
            "match_end", {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH}
        )
        assert len(spy.calls) == 1
        match_id, rows = spy.calls[0]
        assert match_id == MATCH
        by_player = {r["player_id"]: r for r in rows}
        assert by_player["Scout"]["kills"] == 1
        assert by_player["Scout"]["headshots"] == 1
        # Damage is NEVER written by the live writer.
        assert all(r.get("damage_dealt") is None for r in rows)
        assert all("damage" not in r for r in rows)
        # The match is sealed: later events produce no further flush.
        await bus.deliver(
            "kill_event",
            kill_event("e2", "Scout", "teamA", "Mavi", "teamB", ts=T0 + 950),
        )
        assert len(spy.calls) == 1

    async def test_incremental_flush_cadence(self):
        svc, bus, spy = make_service(flush_every=3)
        await start_match(bus)
        for i in range(2):
            await bus.deliver(
                "kill_event",
                kill_event(f"e{i}", "Scout", "teamA", f"V{i}", "teamB", ts=T0 + i),
            )
        assert spy.calls == []
        await bus.deliver(
            "kill_event",
            kill_event("e2", "Scout", "teamA", "V2", "teamB", ts=T0 + 3),
        )
        assert len(spy.calls) == 1
        _mid, rows = spy.calls[0]
        scout = next(r for r in rows if r["player_id"] == "Scout")
        assert scout["kills"] == 3

    async def test_empty_stream_no_rows_no_standings_no_errors(self):
        svc, bus, spy = make_service()
        payload = svc.live_standings(MATCH)
        assert payload["standings"] == []
        assert payload["players"] == []
        assert payload["live"] is True and payload["provisional"] is True
        assert svc.player_stat_rows(MATCH) == []
        assert spy.calls == []
        assert svc.stats.handler_errors == 0
        # A match that starts and ends with no scoring events still
        # produces no writer call and no rows.
        await start_match(bus)
        await bus.deliver(
            "match_end", {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH}
        )
        assert spy.calls == []
        assert svc.stats.handler_errors == 0


# ── flusher precedence (manual flow wins) ─────────────────────────────


class FakeScalars:
    def __init__(self, values: List[Any]) -> None:
        self._values = values

    def all(self) -> List[Any]:
        return list(self._values)


class FakeResult:
    def __init__(self, values: List[Any]) -> None:
        self._values = values

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._values)


class FakeSession:
    """Minimal AsyncSession stand-in: selects return staged id lists,
    DELETE statements are recorded, commits counted."""

    def __init__(self, player_ids=(), team_ids=()) -> None:
        self.player_ids = list(player_ids)
        self.team_ids = list(team_ids)
        self.deletes: List[str] = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        text = str(stmt).lower()
        if text.startswith("delete"):
            self.deletes.append(text)
            return FakeResult([])
        if "match_team_stats" in text:
            return FakeResult(self.team_ids)
        return FakeResult(self.player_ids)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class FakeWriterService:
    """MatchService stand-in: records calls, returns id'd rows."""

    def __init__(self, session) -> None:
        self.session = session
        self.calls: List[Any] = []
        self._counter = 0

    async def record_player_stats(self, match_id, rows):
        self.calls.append((match_id, [dict(r) for r in rows]))
        created = []
        for _ in rows:
            self._counter += 1
            created.append(SimpleNamespace(id=f"live-row-{self._counter}"))
        return created


def make_flusher(session: FakeSession):
    writers: List[FakeWriterService] = []

    def factory(sess):
        writer = FakeWriterService(sess)
        writers.append(writer)
        return writer

    flusher = MatchServiceLiveFlusher(
        session_factory=lambda: session, match_service_factory=factory
    )
    return flusher, writers


ROWS = [{"player_id": "Scout", "team_id": "teamA", "kills": 3}]


class TestFlusherPrecedence:
    async def test_clean_write_then_own_snapshot_replacement(self):
        session = FakeSession()
        flusher, writers = make_flusher(session)
        assert await flusher(MATCH, ROWS) == FLUSH_WRITTEN
        assert len(writers[0].calls) == 1
        # Second flush: the DB now holds ONLY our first write's ids.
        session.player_ids = ["live-row-1"]
        assert await flusher(MATCH, ROWS) == FLUSH_WRITTEN
        assert len(session.deletes) == 1  # our own rows replaced
        assert len(writers) == 2 and len(writers[1].calls) == 1

    async def test_foreign_player_rows_finalise_match(self):
        session = FakeSession()
        flusher, writers = make_flusher(session)
        assert await flusher(MATCH, ROWS) == FLUSH_WRITTEN
        # Manual post-match entry appears (a row we did not create).
        session.player_ids = ["live-row-1", "manual-row-9"]
        outcome = await flusher(MATCH, ROWS)
        assert outcome == FLUSH_SKIPPED_FINALISED
        assert len(writers) == 1  # writer NOT called again
        assert len(session.deletes) == 1  # only our provisional row removed

    async def test_team_results_finalise_match(self):
        session = FakeSession(team_ids=["team-result-1"])
        flusher, writers = make_flusher(session)
        outcome = await flusher(MATCH, ROWS)
        assert outcome == FLUSH_SKIPPED_FINALISED
        assert writers == []  # record_player_stats never called

    async def test_flusher_failure_never_raises(self):
        def bad_factory():
            raise RuntimeError("db down")

        flusher = MatchServiceLiveFlusher(session_factory=bad_factory)
        outcome = await flusher(MATCH, ROWS)
        assert outcome == "failed"
