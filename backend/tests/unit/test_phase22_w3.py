"""Phase 22 (W3) — partial-items sweep tests.

Covers: M1 backup pool/failover/health/latency-sync, M2 ingest plug
points, M3 trained-model guards + parachute, M4 fight schema + audio
plug, M5 aggression + loot tier, M6 safety unification + final-zone
lock + narrative bonus, M7 zone efficiency, M8 points retirement,
M11 highlight triggers, M12 event names/aliases/emitters, Tech
NVENC/storage.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

pytestmark = pytest.mark.unit


# ── M1/M2: latency sync ──────────────────────────────────────────

def test_latency_sync_offsets_and_tolerance():
    from app.vision.capture.latency_sync import LatencySyncTracker

    tr = LatencySyncTracker()
    tr.record_frame("a", capture_ts=1000.0)
    tr.record_frame("b", capture_ts=1000.2)
    tr.record_frame("c", capture_ts=1001.0)
    report = tr.alignment_report()
    assert report["tolerance_ms"] == 500.0
    assert report["feeds_measured"] == 3
    # median is 1000.2 -> c is +800ms out, beyond tolerance
    assert report["feeds"]["c"]["within_tolerance"] is False
    assert report["all_within_tolerance"] is False
    assert report["feeds"]["c"]["offset_basis"] == "server_grab_time"
    # drift needs >=3 offset samples per feed
    assert report["feeds"]["a"]["drift_ms_per_min"] is None


def test_latency_sync_unknown_feed_report_is_none():
    from app.vision.capture.latency_sync import LatencySyncTracker

    assert LatencySyncTracker().feed_report("nope") is None


# ── M2: ingest plug points ───────────────────────────────────────

def test_ingest_plugins_are_honest_no_feed():
    from app.vision.ingest_plugins import get_ingest_plugin, ingest_plugin_status

    for name in ("webrtc", "ndi_feed"):
        plugin = get_ingest_plugin(name)
        assert plugin is not None
        assert plugin.available() is False
        assert plugin.grab_frame() is None
        assert plugin.status()["implemented"] is False
        assert plugin.status()["blocker"]
    assert set(ingest_plugin_status()) == {"webrtc", "ndi_feed"}


# ── M1: grabber health + sync wiring ─────────────────────────────

async def test_grabber_loop_wires_health_and_sync(monkeypatch):
    from app.vision.capture.frame_grabber import (
        CapturedFrame,
        FrameGrabber,
        FrameSource,
        StreamConfig,
    )

    grabber = FrameGrabber()
    grabber.add_source(
        StreamConfig(
            source_id="s1",
            source_type=FrameSource.ADB_DEVICE,
            device_id="FAKE1",
            target_fps=30,
        )
    )
    assert grabber.health.get_feed_health("s1") is not None

    frame = CapturedFrame(
        frame_id=1, source_id="s1", source_type=FrameSource.ADB_DEVICE,
        image=np.zeros((10, 10, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(grabber, "_open_handle", lambda cfg: object())

    async def _fake_capture(cfg, handle):
        return frame

    monkeypatch.setattr(grabber, "_capture_frame", _fake_capture)
    await grabber.start_capture("s1")
    await asyncio.sleep(0.35)
    await grabber.stop_all()

    stats = grabber.get_stats()
    src = stats["sources"]["s1"]
    assert src["health"] is not None
    assert src["sync"]["samples"] >= 1
    assert stats["feed_health"]["total_feeds"] == 1
    assert stats["latency_sync"]["feeds_measured"] == 1


async def test_grabber_drop_counted_on_missed_frame(monkeypatch):
    from app.vision.capture.frame_grabber import FrameGrabber, FrameSource, StreamConfig

    grabber = FrameGrabber()
    grabber.add_source(
        StreamConfig(
            source_id="s1", source_type=FrameSource.ADB_DEVICE,
            device_id="FAKE1", target_fps=30, reconnect_attempts=10 ** 9,
        )
    )
    monkeypatch.setattr(grabber, "_open_handle", lambda cfg: object())

    async def _no_frame(cfg, handle):
        return None

    monkeypatch.setattr(grabber, "_capture_frame", _no_frame)
    await grabber.start_capture("s1")
    await asyncio.sleep(0.3)
    await grabber.stop_all()
    health = grabber.health.get_feed_health("s1")
    assert health["quality"]["frame_drops"] >= 1


# ── M1: backup pool + failover + elimination ─────────────────────

class _FakeDevice:
    def __init__(self, serial):
        self.serial = serial
        self.model = "FakePhone"
        self.connection_type = "usb"


class _FakeADB:
    def __init__(self, serials):
        self.serials = list(serials)

    async def discover_devices(self):
        return [_FakeDevice(s) for s in self.serials]


class _FakeGrabber:
    def __init__(self):
        self.captures = {}
        self.started = []
        self.sync = SimpleNamespace(
            alignment_report=lambda: {"feeds": {}, "tolerance_ms": 500.0}
        )

    def add_source(self, cfg):
        self.captures[cfg.source_id] = cfg

    def remove_source(self, sid):
        self.captures.pop(sid, None)
        return True

    async def start_capture(self, sid):
        self.started.append(sid)
        return True

    async def stop_capture(self, sid):
        return True

    async def stop_all(self):
        return None

    def get_source_state(self, sid):
        return None

    def get_stats(self):
        return {"sources": {}}

    def get_latest_frame(self, sid):
        return None


async def test_backup_pool_failover_and_elimination():
    from app.vision.capture.multi_device import MultiDeviceCaptureManager

    serials = [f"D{i:02d}" for i in range(1, 19)]  # 16 slots + 2 spares
    adb = _FakeADB(serials)
    mgr = MultiDeviceCaptureManager(
        adb_controller=adb, frame_grabber=_FakeGrabber(),
        label_provider=lambda: asyncio.sleep(0, result={
            n: (f"team-{n}", f"Team {n}") for n in range(1, 17)
        }),
    )
    result = await mgr.discover_and_map()
    assert result["slots_mapped"] == 16
    assert len(result["backup_pool"]) == 2
    await mgr.refresh_labels()

    # Slot 1's device vanishes; after the miss threshold it fails over.
    adb.serials = [s for s in serials if s != "D01"]
    for _ in range(3):
        result = await mgr.discover_and_map()
    assert result["failovers"] == [1]
    slot1 = mgr._slots[1]
    assert slot1.serial in ("D17", "D18")
    assert mgr.failover_events and mgr.failover_events[-1]["reason"] == (
        "slot_failure_auto_failover"
    )

    # Elimination returns the team's device to the backup pool.
    record = await mgr.on_team_eliminated("team-2")
    assert record is not None
    assert mgr._slots[2].serial == ""
    assert "D02" in mgr._backup_serials


# ── M3: trained registration guards + parachute ──────────────────

def test_coco_weights_refused_but_bgmi_names_allowed():
    from app.vision.models.trained_registration import coco_weights_refused

    assert coco_weights_refused("yolov8n.pt") is True
    assert coco_weights_refused("models/yolov8x-seg.pt") is True
    assert coco_weights_refused("models/yolov8m_bgmi.pt") is False
    assert coco_weights_refused("models/bgmi_detector.pt") is False


def test_yolo_registration_absent_without_trained_model(monkeypatch):
    from app.vision.models.trained_registration import resolve_yolo_registration

    monkeypatch.delenv("YOLO_TRAINED_MODEL_PATH", raising=False)
    monkeypatch.setenv("VISION_MODEL_PATH", "yolov8n.pt")  # COCO: refused
    result = resolve_yolo_registration()
    assert result["registered"] is False
    assert result["service"] is None
    assert "COCO" in result["reason"]


def test_bytetrack_adapter_empty_without_detections():
    from app.vision.models.trained_registration import PositionTrackerAdapter

    adapter = PositionTrackerAdapter()
    assert adapter.update([]) == []
    tracks = adapter.update(
        [{"bbox": (10, 10, 50, 80), "confidence": 0.9, "class_id": 0,
          "class_name": "player"}]
    )
    assert isinstance(tracks, list)  # IOU fallback may or may not confirm yet


def test_parachute_phase_from_truthful_signals_only():
    from app.vision.parachute import DropPhase, ParachutePhaseDetector

    d = ParachutePhaseDetector()
    assert d.detect_phase(players_alive=64, kills_so_far=0, match_elapsed_s=30).phase == DropPhase.PLANE
    assert d.detect_phase(players_alive=64, kills_so_far=0, match_elapsed_s=100).phase == DropPhase.PARACHUTE
    assert d.detect_phase(players_alive=64, kills_so_far=1, match_elapsed_s=100).phase == DropPhase.LANDED
    assert d.detect_phase(players_alive=None, kills_so_far=0, match_elapsed_s=100).phase == DropPhase.UNKNOWN


# ── M4: fight schema + audio plug ────────────────────────────────

def test_fight_event_canonical_schema_roundtrip():
    from app.services.feed_adapter.fight_schema import (
        canonical_to_bus_payload,
        fight_event_to_canonical,
    )

    row = SimpleNamespace(
        id="f1", match_id="m1", start_timestamp_ms=1000, end_timestamp_ms=5000,
        team_ids=["t1", "t2"], player_ids=["p1"], location_x=10.0,
        location_y=20.0, map_name="erangel", intensity_score=72.5,
        winner_team_id="t1", total_kills=3, total_knocks=2,
        utility_used={"grenade": 1},
    )
    canonical = fight_event_to_canonical(row)
    payload = canonical_to_bus_payload(canonical)
    assert payload["match_id"] == "m1"
    assert payload["intensity_score"] == 72.5
    assert payload["utility_used"] == {"grenade": 1}
    assert payload["schema_version"] == "1.0"


def test_audio_gunshot_signal_absent_without_producer():
    from app.agents.fight_agents.audio_signal import (
        GunshotReading,
        NullAudioGunshotProvider,
        poll_audio_signal,
    )

    assert NullAudioGunshotProvider().poll() is None
    assert poll_audio_signal(NullAudioGunshotProvider()) is None

    class _Provider:
        def poll(self):
            return GunshotReading(team_id="t1", intensity=55.0, confidence=0.7)

    signal = poll_audio_signal(_Provider())
    assert signal is not None
    assert signal.signal_type.value == "shooting"
    assert signal.value == 55.0


def test_fight_detector_audio_hook_is_noop_by_default():
    from app.agents.base_agent import AgentConfig
    from app.agents.fight_agents.fight_detector import FightDetector

    detector = FightDetector(
        config=AgentConfig(agent_id="fd", agent_type="fight_detector"),
        event_bus=AsyncMock(),
    )
    assert detector.process_audio_gunshots() is None
    assert len(detector.detection_state.signal_buffer) == 0


# ── M5: aggression + loot tier ───────────────────────────────────

def test_aggression_formula_bounds_and_none():
    from app.analytics.aggression import compute_aggression_score

    assert compute_aggression_score(fights=0, matches=3, kills=0) is None
    assert compute_aggression_score(fights=2, matches=0, kills=0) is None
    top = compute_aggression_score(
        fights=6, matches=3, kills=30, early_fights=6,
        scope_max_fights_per_match=2.0, scope_max_kills_per_match=10.0,
    )
    assert top == 1.0
    low = compute_aggression_score(
        fights=1, matches=3, kills=1, early_fights=0,
        scope_max_fights_per_match=2.0, scope_max_kills_per_match=10.0,
    )
    assert 0.0 < low < 0.3


def test_loot_tier_score_none_without_inventory():
    from app.analytics.aggression import loot_tier_score

    assert loot_tier_score(None) is None
    assert loot_tier_score([]) is None
    assert loot_tier_score([[]]) is None
    score = loot_tier_score(
        [[{"type": "ar", "name": "M416", "attachments": ["scope", "grip"]},
          {"type": "armor", "level": 3}]]
    )
    assert score is not None and 0.0 < score <= 1.0


# ── M6: safety unification, final-zone lock, narrative ───────────

def _safety():
    from app.agents.base_agent import AgentConfig
    from app.agents.observer_agents.safety_controller import SafetyController

    return SafetyController(
        config=AgentConfig(agent_id="safety", agent_type="safety"),
        event_bus=AsyncMock(),
    )


def test_safety_constants_unified_with_settings():
    safety = _safety()
    assert safety.safety_config.min_scene_time == 10.0
    assert safety.safety_config.rapid_switch_threshold == 3.0


def test_final_zone_hard_lock_top3_and_top5():
    safety = _safety()
    current = SimpleNamespace(
        feed_type=SimpleNamespace(value="final_zone"), team_id="t1",
        is_clutch=False, is_final_circle=True,
    )
    lesser = SimpleNamespace(
        feed_type=SimpleNamespace(value="team_pov"), team_id="t2",
        is_clutch=False, is_final_circle=False,
    )
    clutch = SimpleNamespace(
        feed_type=SimpleNamespace(value="player_pov"), team_id="t2",
        is_clutch=True, is_final_circle=False,
    )
    safety.safety_state.current_is_final_zone = True
    safety.safety_state.current_scene_started_at = time.time()
    safety.safety_state.teams_alive = 3
    assert safety.validate_switch(current, lesser) is False  # top-3 hard lock
    assert safety.validate_switch(current, clutch) is True   # clutch may take over
    safety.safety_state.teams_alive = 10
    assert safety.validate_switch(current, lesser) is True   # not final zone anymore


def test_director_narrative_continuity_bonus():
    from app.agents.base_agent import AgentConfig
    from app.agents.observer_agents.director_ai import BroadcastScene, ObserverDirector
    from app.agents.observer_agents.pov_selector import FeedType

    director = ObserverDirector(
        config=AgentConfig(agent_id="dir", agent_type="director"),
        event_bus=AsyncMock(),
    )
    director.director_state.current_scene = BroadcastScene(
        feed_type=FeedType.TEAM_POV, team_id="team-story",
    )
    base = director._calculate_current_scene_score()
    director.director_state.previous_scenes.append(
        BroadcastScene(feed_type=FeedType.TEAM_POV, team_id="team-story")
    )
    director.director_state.previous_scenes.append(
        BroadcastScene(feed_type=FeedType.FIGHT, team_id="team-story")
    )
    boosted = director._calculate_current_scene_score()
    assert boosted == pytest.approx(base + 8.0)


# ── M7: zone efficiency ──────────────────────────────────────────

def test_zone_efficiency_formula_and_honesty():
    from app.analytics.zone_efficiency import compute_zone_efficiency, zone_efficiency_report

    assert compute_zone_efficiency([]) is None
    perfect = compute_zone_efficiency(
        [{"in_zone": True, "danger_level": 0.0}] * 4
    )
    assert perfect == 100.0
    outside = compute_zone_efficiency(
        [{"in_zone": False, "distance_to_edge": 200.0, "danger_level": 100.0}]
    )
    assert outside == pytest.approx(100 * (0.3 * 0.5), abs=0.01)
    report = zone_efficiency_report({"t1": [], "t2": [{"in_zone": True}]})
    assert report["teams"]["t1"]["score"] is None
    assert report["teams"]["t1"]["reason"]
    assert report["teams"]["t2"]["samples"] == 1


# ── M8: points retirement ────────────────────────────────────────

def test_placement_points_defaults_are_bmsd_not_prd_trap():
    from app.analytics.points_calculator import PlacementPoints

    pp = PlacementPoints()
    assert pp.get_points(1) == 10
    assert pp.get_points(2) == 0
    assert pp.get_points(16) == 0
    # The PRD table remains enterable as explicit tournament config.
    configured = PlacementPoints.from_placement_map({"1": 15, "2": 12, "3": 10})
    assert configured.get_points(1) == 15
    assert configured.get_points(2) == 12


async def test_points_engine_is_subordinate_adapter():
    from app.intelligence.points_engine import PointsEngine

    engine = PointsEngine()
    assert engine.get_leaderboard() == []
    # The shim must not create standings on its own anymore.
    assert engine.process_kill_event("A", "B") == []
    assert engine.get_leaderboard() == []


async def test_leaderboard_update_emitted_on_standings_change():
    from app.analytics.points_calculator import PointsCalculator

    published = []

    class _Bus:
        async def publish(self, **kwargs):
            published.append(kwargs)
            return "evt-1"

    calc = PointsCalculator()
    calc.set_event_bus(_Bus())
    calc.calculate_match_points(
        "m1",
        [
            {"team_id": "t1", "team_name": "One", "placement": 1, "kills": 5,
             "players_lost": 2},
            {"team_id": "t2", "team_name": "Two", "placement": 2, "kills": 3},
        ],
    )
    await asyncio.sleep(0.1)
    updates = [p for p in published if p["event_type"] == "leaderboard.update"]
    assert len(updates) == 1
    rankings = updates[0]["data"]["rankings"]
    assert rankings[0]["team_id"] == "t1"
    assert rankings[0]["total_points"] == 15  # 10 chicken + 5 finishes


# ── M11: highlight triggers ──────────────────────────────────────

def _kill(eid, ts, killer, team, victim="V", vteam="VT", **payload):
    from app.services.replay.buffer import BufferedEvent

    base = {"killer": killer, "killerTeam": team, "victim": victim, "victimTeam": vteam}
    base.update(payload)
    return BufferedEvent(event_id=eid, ts=ts, type="kill_event", payload=base)


def test_verified_1v4_clutch():
    from app.services.replay.highlights import HighlightDetector

    kills = [
        _kill(f"k{i}", 100.0 + i * 2, "Hero", "T1", victim=f"V{i}", vteam=f"X{i}",
              **({"killer_team_alive": 1, "opponents_alive": 4} if i == 0 else {}))
        for i in range(4)
    ]
    moments = HighlightDetector().score_events(kills)
    clutches = [m for m in moments if m.kind == "clutch"]
    assert len(clutches) == 1
    assert clutches[0].score == 95
    assert "Verified 1v4" in clutches[0].reason


def test_clutch_verification_failure_and_unverified_label():
    from app.services.replay.highlights import HighlightDetector

    # Alive counts say the killer was NOT alone -> no clutch claim.
    kills = [
        _kill(f"k{i}", 100.0 + i * 2, "Hero", "T1", victim=f"V{i}", vteam=f"X{i}",
              **({"killer_team_alive": 2, "opponents_alive": 4} if i == 0 else {}))
        for i in range(2)
    ]
    assert [m for m in HighlightDetector().score_events(kills) if m.kind == "clutch"] == []

    # No alive data -> legacy heuristic fires, honestly labelled.
    kills2 = [_kill(f"u{i}", 100.0 + i * 2, "Hero", "T1", victim=f"V{i}", vteam=f"X{i}") for i in range(2)]
    clutches = [m for m in HighlightDetector().score_events(kills2) if m.kind == "clutch"]
    assert len(clutches) == 1
    assert clutches[0].reason.startswith("Unverified clutch")


def test_final_kill_requires_match_end_state():
    from app.services.replay.buffer import BufferedEvent
    from app.services.replay.highlights import HighlightDetector

    kills = [_kill("k1", 100.0, "Hero", "T1")]
    assert [m for m in HighlightDetector().score_events(kills) if m.kind == "final_kill"] == []
    with_end = kills + [
        BufferedEvent(event_id="e1", ts=101.0, type="match_end", payload={})
    ]
    finals = [m for m in HighlightDetector().score_events(with_end) if m.kind == "final_kill"]
    assert len(finals) == 1
    assert finals[0].player == "Hero"


def test_plug_ready_weapon_triggers_inactive_without_weapon_data():
    from app.services.replay.highlight_triggers import trigger_registry

    plain = [_kill("k1", 100.0, "Hero", "T1")]
    registry = trigger_registry(plain)
    assert all(not t.active for t in registry)
    assert all(t.run(plain) == [] for t in registry)

    grenade = [
        _kill("g1", 100.0, "Hero", "T1", weapon="grenade"),
        _kill("g2", 105.0, "Hero", "T1", victim="V2", weapon="grenade"),
    ]
    registry = trigger_registry(grenade)
    grenade_trigger = next(t for t in registry if t.name == "grenade_multi_kill")
    assert grenade_trigger.active is True
    moments = grenade_trigger.run(grenade)
    assert len(moments) == 1 and moments[0].kind == "grenade_multi_kill"


# ── M12: event names + aliases + insight emitter ─────────────────

def test_event_name_aliases():
    from app.core.event_names import canonical_name, equivalent_names

    assert canonical_name("kill_event") == "kill.occurred"
    assert canonical_name("zone.tick") == "zone.updated"
    assert "team_wipe_event" in equivalent_names("team.eliminated")
    assert canonical_name("leaderboard.update") == "leaderboard.update"
    assert canonical_name("totally_unknown") == "totally_unknown"


async def test_event_bus_alias_dispatch_both_ways():
    from app.core.event_bus import BaseEvent, EventBus, EventRecord

    bus = EventBus()
    received = []

    async def _cb(record):
        received.append(record.event.event_type)

    bus.subscribe("canon-sub", _cb, ["kill.occurred"])
    await bus._dispatch_to_subscribers(
        EventRecord(event=BaseEvent(event_type="kill_event", data={}))
    )
    assert received == ["kill_event"]

    received.clear()
    bus2 = EventBus()
    bus2.subscribe("legacy-sub", _cb, ["kill_event"])
    await bus2._dispatch_to_subscribers(
        EventRecord(event=BaseEvent(event_type="kill.occurred", data={}))
    )
    assert received == ["kill.occurred"]


def test_event_bus_docstring_no_consumer_group_claim():
    import importlib

    # importlib: `app.core.event_bus` attribute access is shadowed by
    # the global EventBus instance exported from app.core.
    bus_module = importlib.import_module("app.core.event_bus")
    doc = bus_module.__doc__ or ""
    assert "no xreadgroup" in doc  # truthful statement present


async def test_insight_emitter():
    from app.core.event_names import emit_insight_generated

    published = []

    class _Bus:
        async def publish(self, **kwargs):
            published.append(kwargs)
            return "evt-9"

    out = await emit_insight_generated(_Bus(), insight={"text": "x"}, match_id="m1")
    assert out == "evt-9"
    assert published[0]["event_type"] == "insight.generated"
    assert await emit_insight_generated(None, insight={"text": "x"}) is None


def test_report_generator_insight_emission_hook():
    from app.analytics.report_generator import ReportGenerator

    published = []

    class _Bus:
        async def publish(self, **kwargs):
            published.append(kwargs)
            return "evt-1"

    gen = ReportGenerator(event_bus=_Bus())
    report = gen.generate_match_report(
        "m1",
        {"match_name": "M", "winner_team": "T1", "mvp": "Hero"},
        [{"kills": 5, "damage": 900, "placement": 1}],
        [{"knocks": 1, "revives": 0, "kills": 5}],
        [],
    )
    assert report.ai_insights  # insights were generated
    assert any(p["event_type"] == "insight.generated" for p in published)


# ── M4: fight schema published from feed adapter ─────────────────

def test_fight_schema_exported_from_feed_adapter_package():
    from app.services.feed_adapter import FightEventCanonical

    event = FightEventCanonical(match_id="m1", start_timestamp_ms=1)
    assert event.team_ids == []


# ── Tech: NVENC + storage ────────────────────────────────────────

def test_encoder_selection_falls_back_without_gpu():
    from app.streaming.encoder import detect_encoder, selected_encoder

    assert detect_encoder("libx264")["encoder"] == "libx264"
    forced = detect_encoder("nvenc")
    if not forced["gpu_present"] or not forced["ffmpeg_has_nvenc"]:
        assert forced["encoder"] == "libx264"
        assert "fell back" in forced["reason"]
    assert selected_encoder("libx264") == "libx264"


def test_storage_local_default_and_s3_plug(tmp_path):
    from app.storage.base import LocalDiskStorage, S3Storage, get_storage

    store = LocalDiskStorage(base_dir=tmp_path / "store")
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    store.put_file("highlights/clip.mp4", src)
    assert store.exists("highlights/clip.mp4")
    out = tmp_path / "out.mp4"
    assert store.get_to_file("highlights/clip.mp4", out) is True
    assert out.read_bytes() == b"video"
    assert store.url("highlights/clip.mp4") is None
    assert store.delete("highlights/clip.mp4") is True

    assert isinstance(get_storage(), LocalDiskStorage)
    with pytest.raises(NotImplementedError):
        S3Storage().exists("anything")
    with pytest.raises(ValueError):
        store.put_file("../escape.mp4", src)


# ── Hygiene: llm_chain gone ──────────────────────────────────────

def test_llm_chain_module_deleted():
    import importlib.util

    assert importlib.util.find_spec("app.ml.commentary.llm_chain") is None
    import app.ml.commentary as pkg

    assert not hasattr(pkg, "LLMChain")
