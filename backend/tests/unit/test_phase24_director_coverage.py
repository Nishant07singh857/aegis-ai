# backend/tests/unit/test_phase24_director_coverage.py
"""Phase 24 (W3) — match-phase director coverage tests.

Covers: MatchClock (gap row 19), PhaseTracker + ParachutePhaseDetector
wiring (row 10), phase-aware director suggestions, FlightPathService +
predicted-drop corridor math (row 10), flight/POI APIs, and the additive
feed-schema types + spotter headshot flag (rows 6/12/28, D1/D5/D7).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


class FakeBus:
    def __init__(self):
        self._running = True
        self.subscriptions = {}
        self.published = []

    def subscribe(self, subscriber_id, callback, event_types):
        self.subscriptions[subscriber_id] = (callback, list(event_types))
        return subscriber_id

    def unsubscribe(self, subscriber_id):
        self.subscriptions.pop(subscriber_id, None)
        return True

    async def publish(self, event_type, data, **kwargs):
        self.published.append((event_type, data))
        return "evt-1"


def _record(event_type: str, data: dict | None = None, match_id="m-1"):
    from app.core.event_bus import BaseEvent, EventRecord

    return EventRecord(
        event=BaseEvent(
            event_type=event_type, data=data or {}, match_id=match_id, source="test"
        )
    )


# ── MatchClock ────────────────────────────────────────────────────


class TestMatchClock:
    def test_not_started_elapsed_is_none(self):
        from app.services.match_clock import MatchClock

        clock = MatchClock(clock=FakeClock())
        assert clock.state == "not_started"
        assert clock.elapsed_s() is None
        assert clock.snapshot()["elapsed_s"] is None
        assert clock.snapshot()["clock_basis"] == "feed_elapsed_spectator_delayed"

    def test_start_end_freezes_elapsed(self):
        from app.services.match_clock import MatchClock

        fake = FakeClock()
        clock = MatchClock(clock=fake)
        clock.handle_event("match_start", match_id="m-9")
        fake.advance(125.0)
        assert clock.state == "live"
        assert clock.elapsed_s() == 125.0
        clock.handle_event("match_end")
        assert clock.state == "ended"
        fake.advance(600.0)
        assert clock.elapsed_s() == 125.0  # frozen, never keeps counting

    def test_match_end_without_start_stays_not_started(self):
        from app.services.match_clock import MatchClock

        clock = MatchClock(clock=FakeClock())
        clock.handle_event("match_end")
        assert clock.state == "not_started"
        assert clock.elapsed_s() is None

    async def test_bus_subscription_and_record(self):
        from app.services.match_clock import MatchClock

        fake = FakeClock()
        clock = MatchClock(clock=fake)
        bus = FakeBus()
        clock.start(bus)
        assert "match_clock" in bus.subscriptions
        await clock.handle_record(_record("match_start"))
        assert clock.state == "live"
        clock.start(bus)  # idempotent
        clock.stop()
        assert "match_clock" not in bus.subscriptions


# ── PhaseTracker ──────────────────────────────────────────────────


def _tracker(fake: FakeClock | None = None):
    from app.services.match_clock import MatchClock
    from app.services.phase_tracker import PhaseTracker

    fake = fake or FakeClock()
    clock = MatchClock(clock=fake)
    published = []

    async def _publish(event_type, data):
        published.append((event_type, data))

    tracker = PhaseTracker(match_clock=clock, publish=_publish)
    return tracker, clock, fake, published


class TestPhaseTracker:
    async def test_unknown_without_signals(self):
        tracker, clock, _fake, _pub = _tracker()
        clock.begin(match_id="m-1")
        # No alive_count yet -> detector says unknown, never guessed.
        assert tracker.current_phase() == "unknown"

    async def test_plane_then_parachute_then_loot(self):
        tracker, clock, fake, published = _tracker()
        clock.begin(match_id="m-1")
        await tracker.handle_event("alive_count_update", {"players_alive": 64})
        assert tracker.current_phase() == "plane"
        fake.advance(90.0)
        await tracker.evaluate()
        assert tracker.current_phase() == "parachute"
        # First kill ends the drop phase -> landed -> loot window.
        await tracker.handle_event("kill_event", {"killer": "A", "victim": "B"})
        assert tracker.current_phase() == "loot"
        phases = [d["phase"] for _t, d in published]
        assert "plane" in phases and "loot" in phases

    async def test_late_and_final_derivation(self):
        tracker, clock, fake, _pub = _tracker()
        clock.begin(match_id="m-1")
        await tracker.handle_event("kill_event", {})
        fake.advance(1000.0)
        await tracker.handle_event("alive_count_update", {"players_alive": 20})
        assert tracker.current_phase() == "mid_game"
        await tracker.handle_event("alive_count_update", {"players_alive": 16})
        assert tracker.current_phase() == "late_game"
        await tracker.handle_event("alive_count_update", {"players_alive": 8})
        assert tracker.current_phase() == "final_circle"

    async def test_zone_phase_drives_late_and_final(self):
        tracker, clock, fake, _pub = _tracker()
        clock.begin(match_id="m-1")
        await tracker.handle_event("kill_event", {})
        fake.advance(1000.0)
        await tracker.handle_event("alive_count_update", {"players_alive": 40})
        await tracker.handle_event("zone_event", {"phase": 5})
        assert tracker.current_phase() == "late_game"
        await tracker.handle_event("zone_event", {"zone_phase": 7})
        assert tracker.current_phase() == "final_circle"

    async def test_ended_phase(self):
        tracker, clock, _fake, _pub = _tracker()
        clock.begin(match_id="m-1")
        clock.finish()
        assert tracker.current_phase() == "ended"


# ── Director phase suggestions ────────────────────────────────────


class TestPhaseSuggestions:
    def test_plan_by_phase(self):
        from app.services.director_service import DirectorService

        plan = DirectorService.phase_suggestion_plan
        drop = plan("plane")
        assert any(s["payload"].get("scene") == "map_cam" for s in drop)
        assert all("plane" in s["reason"] for s in drop)
        assert plan("mid_game") == []  # existing fight logic owns mid-game
        late = plan("late_game")
        assert any(s["payload"].get("scene") == "standings" for s in late)
        final = plan("final_circle")
        assert final and all(
            s["payload"].get("scene") != "map_cam" for s in final
        )
        assert final[0]["payload"]["scene"] == "killfeed_focus"
        assert plan("unknown") == [] and plan("not_started") == []

    async def test_propose_only_in_semi_auto(self):
        from app.services.director_service import DirectorService

        svc = DirectorService()
        # full_auto default: proposals are a silent no-op.
        assert await svc.propose_phase_suggestions({"phase": "plane"}) == []
        await svc.set_mode("semi_auto")
        created = await svc.propose_phase_suggestions({"phase": "final_circle"})
        assert len(created) == 1
        assert created[0]["payload"] == {"scene": "killfeed_focus"}
        assert "final_circle" in created[0]["reason"]
        # No map-cam suggestion exists anywhere in the queue.
        assert all(
            s["payload"].get("scene") != "map_cam" for s in svc.list_suggestions()
        )


# ── FlightPathService ─────────────────────────────────────────────


class FakePoiDb:
    POIS = {
        ("erangel", "Alpha"): {"name": "Alpha", "map": "erangel", "x": 0.1, "y": 0.1, "type": "city", "tier": 1},
        ("erangel", "Beta"): {"name": "Beta", "map": "erangel", "x": 0.9, "y": 0.9, "type": "city", "tier": 1},
        ("erangel", "Mid"): {"name": "Mid", "map": "erangel", "x": 0.5, "y": 0.52, "type": "town", "tier": 2},
        ("erangel", "Far"): {"name": "Far", "map": "erangel", "x": 0.1, "y": 0.9, "type": "town", "tier": 2},
    }

    def by_name(self, map_id, name):
        return self.POIS.get((map_id, name))

    def all_pois(self, map_id):
        return [p for (m, _), p in self.POIS.items() if m == map_id]

    def nearest(self, map_id, x, y, max_radius=0.05):
        return None

    def maps(self):
        return ["erangel"]


class TestFlightPath:
    async def test_set_path_resolves_and_labels_director_set(self):
        from app.services.flight_path import FlightPathService

        svc = FlightPathService(poi_db=FakePoiDb())
        assert svc.snapshot()["is_set"] is False
        snap = await svc.set_path(
            map_id="erangel", start_poi="Alpha", end_poi="Beta", updated_by="op"
        )
        assert snap["is_set"] is True
        assert snap["source"] == "director-set"
        assert snap["start"] == {"name": "Alpha", "x": 0.1, "y": 0.1}
        assert snap["end"]["x"] == 0.9

    async def test_unknown_poi_keeps_name_null_coords(self):
        from app.services.flight_path import FlightPathService

        svc = FlightPathService(poi_db=FakePoiDb())
        snap = await svc.set_path(
            map_id="erangel", start_poi="Nowhere", end_poi="Beta"
        )
        assert snap["start"] == {"name": "Nowhere", "x": None, "y": None}
        # No coordinates -> corridor predictions honestly impossible.
        assert svc.predicted_drops([{"team_id": "t1", "named_location": "Mid"}]) == []

    async def test_set_path_validation(self):
        from app.services.flight_path import FlightPathService

        svc = FlightPathService(poi_db=FakePoiDb())
        with pytest.raises(ValueError):
            await svc.set_path(map_id="", start_poi="Alpha", end_poi="Beta")

    async def test_predicted_drops_corridor(self):
        from app.services.flight_path import FlightPathService

        svc = FlightPathService(poi_db=FakePoiDb())
        await svc.set_path(map_id="erangel", start_poi="Alpha", end_poi="Beta")
        rows = [
            {"team_id": "t1", "team_name": "One", "named_location": "Mid", "contested_by": ["t2"]},
            {"team_id": "t1", "team_name": "One", "named_location": "Mid", "contested_by": []},
            {"team_id": "t2", "team_name": "Two", "named_location": "Far", "contested_by": []},
            {"team_id": "t3", "team_name": "Three", "named_location": None,
             "location_x": 0.52, "location_y": 0.48, "contested_by": []},
        ]
        predicted = svc.predicted_drops(rows)
        by_team = {p["team_id"]: p for p in predicted}
        assert set(by_team) == {"t1", "t3"}  # Far is off-corridor
        assert by_team["t1"]["drop_count"] == 2
        assert by_team["t1"]["usual_poi"] == "Mid"
        assert by_team["t1"]["contested"] is True
        assert by_team["t1"]["contested_drops"] == 1
        assert by_team["t3"]["usual_poi"] is None  # coords only, no POI name

    async def test_predicted_drops_no_history_empty(self):
        from app.services.flight_path import FlightPathService

        svc = FlightPathService(poi_db=FakePoiDb())
        await svc.set_path(map_id="erangel", start_poi="Alpha", end_poi="Beta")
        assert svc.predicted_drops([]) == []

    def test_corridor_math(self):
        from app.services.flight_path import point_to_segment_distance

        # Point on the line -> 0; perpendicular offset -> exact distance.
        assert point_to_segment_distance(0.5, 0.5, 0.0, 0.0, 1.0, 1.0) == pytest.approx(0.0)
        assert point_to_segment_distance(0.5, 0.6, 0.0, 0.5, 1.0, 0.5) == pytest.approx(0.1)


# ── Feed schema additions ─────────────────────────────────────────


class TestFeedSchemaAdditions:
    def test_new_event_types_and_bus_map(self):
        from app.services.feed_adapter.schemas import BUS_EVENT_TYPE_MAP, FeedEventType

        assert FeedEventType.AIRDROP_SPOTTED.value == "airdrop_spotted"
        assert FeedEventType.FLARE_USED.value == "flare_used"
        assert BUS_EVENT_TYPE_MAP[FeedEventType.AIRDROP_SPOTTED] == "airdrop_spotted"
        assert BUS_EVENT_TYPE_MAP[FeedEventType.FLARE_USED] == "flare_used"
        # Pre-existing types untouched (15 originals + 2 new).
        assert len(FeedEventType) == 17

    def test_headshot_flag_reaches_bus_payload(self):
        from app.services.feed_adapter.schemas import FeedEventType, NormalizedEvent

        ev = NormalizedEvent(
            event_type=FeedEventType.KILL, player="A", victim="B",
            payload={"headshot": True},
        )
        payload = ev.to_bus_payload()
        assert payload["headshot"] is True
        ev2 = NormalizedEvent(event_type=FeedEventType.KNOCK, player="A", victim="B")
        assert ev2.to_bus_payload()["headshot"] is False

    def test_utility_smoke_tag_shape(self):
        from app.services.feed_adapter.schemas import FeedEventType, NormalizedEvent

        ev = NormalizedEvent(
            event_type=FeedEventType.UTILITY, payload={"utility_type": "smoke"}
        )
        assert ev.to_bus_payload()["utility_type"] == "smoke"


# ── Flight / POI APIs ─────────────────────────────────────────────


def _api_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import TokenPayload, get_current_user, require_operator
    from app.api.v1.flight import router as flight_router
    from app.api.v1.poi import router as poi_router

    app = FastAPI()
    app.include_router(flight_router, prefix="/api/v1")
    app.include_router(poi_router, prefix="/api/v1")
    principal = TokenPayload(user_id="u1", username="op", role="operator")
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[require_operator] = lambda: principal
    return TestClient(app)


class TestFlightPoiApis:
    def test_clock_endpoint_honest_before_start(self):
        client = _api_client()
        body = client.get("/api/v1/flight/clock").json()
        assert body["state"] in ("not_started", "live", "ended")
        assert "elapsed_s" in body

    def test_poi_list_and_nearest(self):
        client = _api_client()
        body = client.get("/api/v1/poi", params={"map": "erangel"}).json()
        assert body["map"] == "erangel"
        names = {p["name"] for p in body["pois"]}
        assert "Pochinki" in names
        near = client.get(
            "/api/v1/poi/nearest", params={"map": "erangel", "x": 0.44, "y": 0.53}
        ).json()
        assert near["poi"] is not None
        assert near["poi"]["name"] == "Pochinki"

    def test_set_and_get_flight_path(self, monkeypatch):
        import app.api.v1.flight as flight_api
        from app.services.flight_path import FlightPathService

        monkeypatch.setattr(
            flight_api, "flight_path_service",
            FlightPathService(poi_db=FakePoiDb()),
        )
        client = _api_client()
        res = client.post(
            "/api/v1/flight/path",
            json={"map_id": "erangel", "start_poi": "Alpha", "end_poi": "Beta"},
        )
        assert res.status_code == 200
        assert res.json()["flight_path"]["source"] == "director-set"
        body = client.get("/api/v1/flight/path").json()
        assert body["flight_path"]["start_poi"] == "Alpha"
        assert "clock" in body and "phase" in body
        bad = client.post(
            "/api/v1/flight/path",
            json={"map_id": "", "start_poi": "Alpha", "end_poi": "Beta"},
        )
        assert bad.status_code == 422

    def test_predicted_drops_without_path_is_empty(self, monkeypatch):
        import app.api.v1.flight as flight_api

        fresh = SimpleNamespace(
            snapshot=lambda: {"is_set": False, "map_id": None},
            predicted_drops=lambda rows, corridor_half_width=0.12: [],
        )
        monkeypatch.setattr(flight_api, "flight_path_service", fresh)
        client = _api_client()
        body = client.get("/api/v1/flight/predicted-drops").json()
        assert body["predicted_drops"] == []
