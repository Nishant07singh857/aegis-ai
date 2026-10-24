# backend/tests/unit/test_integrity_headshot_stats.py
"""Integrity Fix Pack — C2: icon-derived headshots must never persist.

docs/FULL_PROJECT_INSPECTION.md §3 C2 / docs/WEAPON_RECOGNITION.md §2.7:
the vision icon matcher's headshot flag measures 7.7 % precision
(noise) and downstream consumption is banned; the spotter HEADSHOT
toggle is the only trustworthy producer. Before this fix the chain
killfeed payload -> feed_adapter ``to_bus_payload`` -> live_stats
``killer.headshots`` -> ``record_player_stats`` persisted the icon flag
into standings/MVP because no source was distinguished anywhere.

These tests pin the fixed contract end-to-end:

* icon/vision headshot event through the WHOLE path (NormalizedEvent
  -> to_bus_payload -> bus -> LiveStatsService -> flushed rows):
  persisted headshots UNCHANGED, kills/knocks unaffected, the dropped
  flag counted in ``headshots_unverified`` / stats with a logged drop;
* spotter-confirmed headshot (server-stamped at ingest): +1 persisted;
* unmarked / client-spoofed headshot flags are never trusted — trust
  is stamped server-side from the caller's role at POST /feed/ingest;
* H31(b): observer ingest is scoped — own team OK (by name or id),
  another team 403, no assignment 403, viewer 403.

No network, no real DB, no Redis: fake bus + fake flusher in the
test_live_stats style; the ingest endpoint is exercised through a
TestClient with dependency overrides (auth principal + team-name DB).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.analytics.points_calculator import PointsCalculator
from app.services.feed_adapter.schemas import FeedEventType, NormalizedEvent
from app.services.live_stats import FLUSH_WRITTEN, LiveStatsService

pytestmark = pytest.mark.unit

MATCH = "match-c2"
T0 = 2_000.0


# ── fakes (test_live_stats style) ─────────────────────────────────────


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
    def __init__(self) -> None:
        self.calls: List[Any] = []

    async def __call__(self, match_id: str, rows: List[Dict[str, Any]]) -> str:
        self.calls.append((match_id, [dict(r) for r in rows]))
        return FLUSH_WRITTEN


def make_service():
    bus = FakeBus()
    spy = SpyFlusher()
    calc = PointsCalculator()
    calc.configure_from_tournament(
        {"id": "tour-c2", "points_per_kill": 1, "placement_points": {"1": 10}}
    )
    svc = LiveStatsService(
        bus=bus, points_calculator=calc, flusher=spy, flush_every=10
    )
    svc.start()
    return svc, bus, spy


async def start_match(bus: FakeBus) -> None:
    await bus.deliver(
        "match_start", {"id": "ms-1", "timestamp": T0, "match_id": MATCH}
    )


def vision_kill_event(eid: str, *, headshot: bool = True) -> Dict[str, Any]:
    """The REAL vision path: killfeed detector NormalizedEvent -> bus."""
    event = NormalizedEvent(
        event_type=FeedEventType.KILL,
        ts=T0 + 60,
        match_id=MATCH,
        player="Scout",
        team="teamA",
        victim="Jonathan",
        victim_team="teamB",
        weapon="ar",
        source="vision_killfeed",
        payload={
            "detector": "killfeed",
            "raw_text": "Scout Jonathan",
            "ocr_confidence": 91.0,
            "headshot": headshot,
            "headshot_source": "vision_icon",
            "dedup_key": f"dk-{eid}",
            "id": eid,
        },
    )
    payload = event.to_bus_payload()
    payload["id"] = eid
    return payload


def spotter_kill_event(eid: str, *, headshot: bool = True) -> Dict[str, Any]:
    """The REAL spotter path: console event as stamped at ingest."""
    event = NormalizedEvent(
        event_type=FeedEventType.KILL,
        ts=T0 + 60,
        match_id=MATCH,
        player="Scout",
        team="teamA",
        victim="Jonathan",
        victim_team="teamB",
        weapon="M416",
        source="spotter",
        payload={"headshot": headshot, "id": eid},
    )
    payload = event.to_bus_payload()
    payload["id"] = eid
    return payload


# ── bus-payload contract ──────────────────────────────────────────────


class TestBusPayloadContract:
    def test_vision_headshot_flagged_but_not_confirmed(self):
        payload = vision_kill_event("v1")
        # Raw flag stays for hint consumers (highlights/commentary)…
        assert payload["headshot"] is True
        # …but it is NOT confirmed, and its source says why.
        assert payload["headshot_confirmed"] is False
        assert payload["source"] == "vision_killfeed"

    def test_unstamped_vision_payload_still_not_confirmed(self):
        """Even without the explicit stamp, detector markers in the
        payload resolve to the vision source — never trusted."""
        event = NormalizedEvent(
            event_type=FeedEventType.KILL,
            player="Scout",
            victim="Jonathan",
            payload={
                "headshot": True,
                "detector": "killfeed",
                "ocr_confidence": 88.0,
            },
        )
        payload = event.to_bus_payload()
        assert payload["headshot_confirmed"] is False
        assert payload["headshot_source"] == "vision_killfeed"

    def test_unknown_source_never_confirmed(self):
        event = NormalizedEvent(
            event_type=FeedEventType.KNOCK,
            player="Scout",
            victim="Jonathan",
            payload={"headshot": True},
        )
        payload = event.to_bus_payload()
        assert payload["headshot"] is True
        assert payload["headshot_confirmed"] is False
        assert payload["headshot_source"] == "unknown"

    def test_spotter_headshot_confirmed(self):
        payload = spotter_kill_event("s1")
        assert payload["headshot"] is True
        assert payload["headshot_confirmed"] is True
        assert payload["headshot_source"] == "spotter"

    def test_adapter_ingest_stamps_vision_provenance(self):
        """An unstamped event ingested through the VISION adapter gets
        vision provenance from BaseFeedAdapter.ingest — the vision
        path structurally cannot produce a trusted marker."""
        import asyncio

        from app.services.feed_adapter.vision_adapter import VisionFeedAdapter

        captured: Dict[str, Any] = {}

        class PublishBus:
            async def publish(self, *, event_type, data, priority, match_id, source):
                captured.update(data)
                captured["_bus_source"] = source
                return "bus-1"

        adapter = VisionFeedAdapter(bus=PublishBus())
        event = NormalizedEvent(
            event_type=FeedEventType.KILL,
            player="Scout",
            victim="Jonathan",
            payload={"headshot": True},
        )
        asyncio.run(adapter.ingest(event))
        assert event.source == "vision_killfeed"
        assert captured["headshot_confirmed"] is False


# ── live_stats gating (full path) ─────────────────────────────────────


class TestLiveStatsHeadshotGate:
    async def test_icon_only_headshot_never_persists(self):
        svc, bus, spy = make_service()
        await start_match(bus)
        await bus.deliver("kill_event", vision_kill_event("v1", headshot=True))
        await bus.deliver(
            "match_end", {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH}
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        scout = players["Scout"]
        # The kill itself counts exactly as before…
        assert scout["finishes"] == 1
        # …but the icon headshot does NOT reach the persisted counter.
        assert scout["headshots"] == 0
        assert scout["headshots_unverified"] == 1
        assert svc.stats.headshots_dropped_unverified == 1
        # Flushed (persisted) rows: headshots unchanged at 0.
        assert len(spy.calls) == 1
        _mid, rows = spy.calls[0]
        by_player = {r["player_id"]: r for r in rows}
        assert by_player["Scout"]["kills"] == 1
        assert by_player["Scout"]["headshots"] == 0
        assert all("headshots_unverified" not in r for r in rows)
        # Damage honesty untouched: never written by the live writer.
        assert all("damage_dealt" not in r for r in rows)

    async def test_spotter_confirmed_headshot_persists(self):
        svc, bus, spy = make_service()
        await start_match(bus)
        await bus.deliver("kill_event", spotter_kill_event("s1", headshot=True))
        await bus.deliver(
            "kill_event", spotter_kill_event("s2", headshot=False)
        )
        await bus.deliver(
            "match_end", {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH}
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Scout"]["finishes"] == 2
        assert players["Scout"]["headshots"] == 1
        assert svc.stats.headshots_dropped_unverified == 0
        _mid, rows = spy.calls[-1]
        by_player = {r["player_id"]: r for r in rows}
        assert by_player["Scout"]["headshots"] == 1

    async def test_manual_and_observer_sources_trusted(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        for i, source in enumerate(("manual", "observer")):
            data = spotter_kill_event(f"t{i}")
            data["source"] = source
            data["headshot_source"] = source
            data["headshot_confirmed"] = False  # source alone must suffice
            data["victim"] = f"V{i}"
            await bus.deliver("kill_event", data)
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Scout"]["headshots"] == 2

    async def test_unmarked_headshot_flag_dropped(self):
        """A bare ``headshot: True`` with no provenance (the pre-C2
        payload shape) is dropped, not persisted."""
        svc, bus, _spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "kill_event",
            {
                "id": "bare-1",
                "timestamp": T0 + 60,
                "match_id": MATCH,
                "killer": "Scout",
                "killerTeam": "teamA",
                "victim": "Jonathan",
                "victimTeam": "teamB",
                "weapon": "M416",
                "isKnock": False,
                "headshot": True,
            },
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Scout"]["finishes"] == 1
        assert players["Scout"]["headshots"] == 0
        assert svc.stats.headshots_dropped_unverified == 1

    async def test_knock_flow_unaffected_by_gate(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        knock = vision_kill_event("vk1", headshot=True)
        await bus.deliver("knock_event", knock)
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        assert players["Scout"]["knocks"] == 1
        assert players["Scout"]["headshots"] == 0
        assert players["Jonathan"]["times_knocked"] == 1


# ── ingest endpoint: server-side stamp + observer scope (H31b) ──────


class _CaptureBus:
    def __init__(self) -> None:
        self.published: List[Dict[str, Any]] = []

    async def publish(self, *, event_type, data, priority, match_id, source):
        self.published.append(dict(data))
        return f"bus-{len(self.published)}"


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def first(self):
        return self._values[0] if self._values else None


class _FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _FakeScalars(self._values)


class _FakeSession:
    """Stands in for the DB session: resolves team-1 -> 'Team Alpha'."""

    async def execute(self, _stmt):
        return _FakeResult(["Team Alpha"])


async def _fake_get_db():
    yield _FakeSession()


def _ingest_client(principal):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import TokenPayload, get_current_user
    from app.api.v1.feed.router import router as feed_router
    from app.db.session import get_db
    from app.services.feed_adapter import feed_service
    from app.services.feed_adapter.simulator import SimulatorFeedAdapter

    bus = _CaptureBus()
    adapter = SimulatorFeedAdapter(bus=bus)
    saved_adapters = dict(feed_service._adapters)
    saved_active = feed_service._active_id
    feed_service._adapters[adapter.adapter_id] = adapter
    feed_service._active_id = adapter.adapter_id

    app = FastAPI()
    app.include_router(feed_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_db] = _fake_get_db
    client = TestClient(app, raise_server_exceptions=False)
    client._aegis_bus = bus  # type: ignore[attr-defined]
    client._aegis_saved = (saved_adapters, saved_active)  # type: ignore[attr-defined]
    return client


def _restore_feed_service(client) -> None:
    from app.services.feed_adapter import feed_service

    saved_adapters, saved_active = client._aegis_saved  # type: ignore[attr-defined]
    feed_service._adapters = saved_adapters
    feed_service._active_id = saved_active


def _kill_body(team: Any, *, headshot: bool = False, source: Any = None) -> dict:
    event: Dict[str, Any] = {
        "event_type": "kill",
        "match_id": MATCH,
        "player": "Scout",
        "team": team,
        "victim": "Jonathan",
        "victim_team": "Team Bravo",
        "weapon": "M416",
        "payload": {"headshot": headshot},
    }
    if source is not None:
        event["source"] = source
    return {"events": [event]}


class TestIngestEndpoint:
    def test_spotter_ingest_stamps_trusted_source(self):
        from app.api.deps import TokenPayload

        principal = TokenPayload(user_id="sp1", username="spot", role="spotter")
        client = _ingest_client(principal)
        try:
            # Client tries to claim vision provenance / no provenance:
            # the server stamp (spotter) wins either way.
            res = client.post(
                "/api/v1/feed/ingest",
                json=_kill_body("Team Alpha", headshot=True, source="vision_killfeed"),
            )
            assert res.status_code == 200, res.text
            published = client._aegis_bus.published  # type: ignore[attr-defined]
            assert len(published) == 1
            assert published[0]["source"] == "spotter"
            assert published[0]["headshot"] is True
            assert published[0]["headshot_confirmed"] is True
        finally:
            _restore_feed_service(client)

    def test_observer_own_team_by_name_ok(self):
        from app.api.deps import TokenPayload

        principal = TokenPayload(
            user_id="ob1", username="obs", role="observer", team_id="team-1"
        )
        client = _ingest_client(principal)
        try:
            res = client.post(
                "/api/v1/feed/ingest", json=_kill_body("Team Alpha")
            )
            assert res.status_code == 200, res.text
            assert res.json()["ingested"] == 1
            published = client._aegis_bus.published  # type: ignore[attr-defined]
            assert published[0]["source"] == "observer"
        finally:
            _restore_feed_service(client)

    def test_observer_own_team_by_id_ok(self):
        from app.api.deps import TokenPayload

        principal = TokenPayload(
            user_id="ob1", username="obs", role="observer", team_id="team-1"
        )
        client = _ingest_client(principal)
        try:
            res = client.post("/api/v1/feed/ingest", json=_kill_body("team-1"))
            assert res.status_code == 200, res.text
        finally:
            _restore_feed_service(client)

    def test_observer_other_team_forbidden(self):
        from app.api.deps import TokenPayload

        principal = TokenPayload(
            user_id="ob1", username="obs", role="observer", team_id="team-1"
        )
        client = _ingest_client(principal)
        try:
            res = client.post(
                "/api/v1/feed/ingest", json=_kill_body("Team Bravo")
            )
            assert res.status_code == 403, res.text
            # Nothing was published for the out-of-scope event.
            assert client._aegis_bus.published == []  # type: ignore[attr-defined]
        finally:
            _restore_feed_service(client)

    def test_observer_without_assignment_forbidden(self):
        from app.api.deps import TokenPayload

        principal = TokenPayload(user_id="ob2", username="obs2", role="observer")
        client = _ingest_client(principal)
        try:
            res = client.post(
                "/api/v1/feed/ingest", json=_kill_body("Team Alpha")
            )
            assert res.status_code == 403, res.text
        finally:
            _restore_feed_service(client)

    def test_viewer_forbidden(self):
        from app.api.deps import TokenPayload

        principal = TokenPayload(user_id="v1", username="view", role="viewer")
        client = _ingest_client(principal)
        try:
            res = client.post(
                "/api/v1/feed/ingest", json=_kill_body("Team Alpha")
            )
            assert res.status_code == 403, res.text
        finally:
            _restore_feed_service(client)
