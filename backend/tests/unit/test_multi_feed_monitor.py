# backend/tests/unit/test_multi_feed_monitor.py
"""
Phase 25 (W2) — multi-feed parallel killfeed monitoring tests.

Everything runs on synthetic frames + fake readers (the production
readers' own accuracy is covered by the Phase 14/23 suites). What is
proven here is the MONITOR contract:

  * two feeds: a killfeed-visible change on one emits exactly one
    event carrying THAT feed's observer slot + source id
  * voting: 1-of-3 flicker -> nothing; 2-of-3 -> exactly one event
  * failure isolation: a raising/freezing feed never stalls the other
    feed, and health reports it honestly (error / stale_frames /
    no_signal / stopped)
  * cross-feed dedup: the same kill/alive/zone fact seen on many
    feeds reaches ingest once
  * builder: grabber sources adopt only with a derivable slot
  * runtime: VisionCaptureService keeps its single-source behaviour
    and exposes the monitor additively in status()
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import asyncio

import numpy as np
import pytest

from app.services.feed_adapter.schemas import FeedEventType, NormalizedEvent
from app.vision.capture.config import CaptureConfig, load_capture_config
from app.vision.capture.frame_grabber import CapturedFrame, FrameSource
from app.vision.detectors.killfeed import KillFeedEntry
from app.vision.multi_feed_monitor import (
    FeedReaderBundle,
    FeedSourceSpec,
    KillfeedVoter,
    MultiFeedMonitorService,
    build_multi_feed_monitor,
)
from app.vision.runtime import VisionCaptureService, build_vision_service

W, H = 320, 180


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class StubIngest:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)
        return f"stub-{len(self.events)}"

    def of_type(self, etype):
        return [e for e in self.events if e.event_type == etype]


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_frame(frame_id=0, kill=False, seed=0):
    """Synthetic frame; `kill=True` paints the kill marker block the
    fake readers key on; `seed` varies background content (freshness)."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :] = (seed % 50, 0, 0)  # varying content -> fresh signature
    if kill:
        img[10:40, 10:120] = (255, 255, 255)
    return CapturedFrame(
        frame_id=frame_id, source_id="synthetic",
        source_type=FrameSource.ADB_DEVICE, image=img,
        width=W, height=H,
    )


def has_kill_marker(image):
    return float(image[10:40, 10:120].mean()) > 100.0


class FakeKillfeedReader:
    """read_entries seam: one fixed entry when the marker is visible."""

    name = "killfeed"

    def read_entries(self, image):
        if has_kill_marker(image):
            return [KillFeedEntry(
                killer="Alpha", victim="Bravo", event_type="kill",
                confidence=92.0, raw_text="Alpha Bravo", is_valid=True,
            )]
        return []


class FakeLayoutDetector:
    """Mimics the production KillFeedDetector RAW interface (pre-dedup):
    parse_layout_region returns the entry on EVERY frame — only the
    voter can turn that into exactly one event."""

    name = "killfeed"

    def ocr_available(self):
        return True

    def _crop_region(self, image):
        return image

    def parse_layout_region(self, region, frame_h):
        if has_kill_marker(region):
            return [KillFeedEntry(
                killer="Alpha", victim="Bravo", event_type="kill",
                confidence=88.0, raw_text="Alpha Bravo", is_valid=True,
            )]
        return []

    def _preprocess(self, region):
        return None

    def _entry_to_event(self, entry, match_id, now):
        return NormalizedEvent(
            event_type=FeedEventType.KILL, ts=now, match_id=match_id,
            player=entry.killer, victim=entry.victim,
            payload={"detector": self.name, "dedup_key": entry.dedup_key(),
                     "ocr_confidence": entry.confidence},
        )


class FakeAliveReader:
    """poll() seam: emits an update only when the value it is told to
    report changes (the real reader's 2-frame confirm lives in the
    reader and is covered by test_hud_readers)."""

    name = "alive_count_reader"

    def __init__(self):
        self.value = None
        self._emitted = None

    def poll(self, image):
        if self.value is None or self.value == self._emitted:
            return None
        self._emitted = self.value

        class _Upd:
            pass

        upd = _Upd()
        upd.players = self.value
        upd.confidence = 0.9
        upd.engine = "fake"
        return upd


class FakeZoneReader:
    """poll() seam: returns queued zone events one at a time."""

    name = "zone_timer_reader"

    def __init__(self):
        self.queue = []

    def poll(self, image, match_id=None):
        if self.queue:
            return self.queue.pop(0)
        return None


def zone_event(phase, match_id="m-test"):
    return NormalizedEvent(
        event_type=FeedEventType.ZONE_UPDATE, match_id=match_id,
        payload={"zone_phase": phase, "timer_seconds": 120,
                 "phase_source": "inferred_from_timer_reset"},
    )


class FrameHolder:
    """Mutable frame provider: tests swap .frame between samples."""

    def __init__(self, frame=None):
        self.frame = frame
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.frame


def raising_provider():
    raise RuntimeError("device exploded")


def make_config(**kw):
    kw.setdefault("backend", "none")
    kw.setdefault("monitor_tick_s", 0.05)
    kw.setdefault("monitor_batch_size", 4)
    kw.setdefault("monitor_stale_after_s", 6.0)
    kw.setdefault("monitor_no_signal_after_s", 6.0)
    kw.setdefault("monitor_vote_window", 3)
    kw.setdefault("monitor_vote_threshold", 2)
    kw.setdefault("monitor_dedup_ttl_s", 30.0)
    return CaptureConfig(**kw)


def factory_with(readers_by_slot):
    def _factory(slot, source_id):
        return readers_by_slot[slot]
    return _factory


def kill_only_bundle(reader=None):
    return FeedReaderBundle(killfeed=reader or FakeKillfeedReader())


def make_service(ingest, clock=None, config=None, **kw):
    return MultiFeedMonitorService(
        ingest=ingest, config=config or make_config(),
        match_id="m-test", clock=clock or FakeClock(), **kw,
    )


# ---------------------------------------------------------------------------
# Two feeds: identity of emitted events
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestTwoFeedsIdentity:
    async def test_kill_event_carries_owning_slot_and_source(self):
        ingest = StubIngest()
        svc = make_service(ingest, detector_factory=factory_with({
            3: kill_only_bundle(), 5: kill_only_bundle(),
        }))
        holder_a = FrameHolder(make_frame(0, kill=True))
        holder_b = FrameHolder(make_frame(0, kill=False))
        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", holder_a))
        svc.add_feed(FeedSourceSpec(5, "aegis-observer-slot-05", holder_b))

        # First sighting: vote not yet confirmed -> nothing.
        assert await svc.sample_feed_once("aegis-observer-slot-03") == []
        # Second consecutive sighting: exactly one event, slot 3.
        emitted = await svc.sample_feed_once("aegis-observer-slot-03")
        assert len(emitted) == 1
        ev = emitted[0]
        assert ev.event_type == FeedEventType.KILL
        assert ev.player == "Alpha" and ev.victim == "Bravo"
        assert ev.payload["observer_slot"] == 3
        assert ev.payload["source_id"] == "aegis-observer-slot-03"
        assert ev.payload["vision_monitor"] == "multi_feed"
        assert ev.match_id == "m-test"
        # Third sighting of the same entry: voter already emitted it.
        holder_a.frame = make_frame(1, kill=True)
        assert await svc.sample_feed_once("aegis-observer-slot-03") == []
        # The quiet feed never emits.
        for i in range(3):
            holder_b.frame = make_frame(i, kill=False, seed=i + 1)
            assert await svc.sample_feed_once("aegis-observer-slot-05") == []
        assert len(ingest.events) == 1


# ---------------------------------------------------------------------------
# Voting
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestVoting:
    async def _service_with_holder(self):
        ingest = StubIngest()
        svc = make_service(ingest, detector_factory=factory_with({
            3: kill_only_bundle(),
        }))
        holder = FrameHolder(make_frame(0, kill=False))
        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", holder))
        return svc, holder, ingest

    async def test_single_frame_flicker_emits_nothing(self):
        svc, holder, ingest = await self._service_with_holder()
        holder.frame = make_frame(1, kill=True)
        await svc.sample_feed_once("aegis-observer-slot-03")
        holder.frame = make_frame(2, kill=False, seed=2)
        await svc.sample_feed_once("aegis-observer-slot-03")
        holder.frame = make_frame(3, kill=False, seed=3)
        await svc.sample_feed_once("aegis-observer-slot-03")
        assert ingest.of_type(FeedEventType.KILL) == []

    async def test_two_of_three_emits_exactly_once(self):
        svc, holder, ingest = await self._service_with_holder()
        holder.frame = make_frame(1, kill=True)
        await svc.sample_feed_once("aegis-observer-slot-03")   # seen 1
        holder.frame = make_frame(2, kill=False, seed=2)
        await svc.sample_feed_once("aegis-observer-slot-03")   # gap
        holder.frame = make_frame(3, kill=True)
        emitted = await svc.sample_feed_once("aegis-observer-slot-03")  # seen 2 of 3
        assert len(emitted) == 1
        holder.frame = make_frame(4, kill=True)
        assert await svc.sample_feed_once("aegis-observer-slot-03") == []
        assert len(ingest.of_type(FeedEventType.KILL)) == 1

    def test_voter_ttl_rearm(self):
        voter = KillfeedVoter(window=3, threshold=2, ttl_s=30.0)
        from app.vision.multi_feed_monitor import _Candidate

        def cand():
            return _Candidate(key="a|b|kill", make_event=lambda ts: object())

        assert voter.observe([cand()], now=100.0) == []
        assert len(voter.observe([cand()], now=101.0)) == 1
        assert voter.observe([cand()], now=102.0) == []  # within TTL
        # After the TTL the same key can confirm again (new identical kill).
        assert voter.observe([cand()], now=200.0) == []
        assert len(voter.observe([cand()], now=201.0)) == 1

    async def test_production_raw_path_detector_without_dedup(self):
        """A detector returning the entry EVERY frame (no internal
        dedup) still yields exactly one event — the voter gates it."""
        ingest = StubIngest()
        svc = make_service(ingest, detector_factory=factory_with({
            4: FeedReaderBundle(killfeed=FakeLayoutDetector()),
        }))
        holder = FrameHolder(make_frame(0, kill=True))
        svc.add_feed(FeedSourceSpec(4, "aegis-observer-slot-04", holder))
        assert await svc.sample_feed_once("aegis-observer-slot-04") == []
        emitted = await svc.sample_feed_once("aegis-observer-slot-04")
        assert len(emitted) == 1
        assert emitted[0].payload["observer_slot"] == 4
        assert await svc.sample_feed_once("aegis-observer-slot-04") == []
        assert len(ingest.events) == 1


# ---------------------------------------------------------------------------
# Failure isolation + honest health
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestFailureIsolation:
    async def test_raising_feed_never_stalls_healthy_feed(self):
        ingest = StubIngest()
        svc = make_service(ingest, detector_factory=factory_with({
            3: kill_only_bundle(), 5: kill_only_bundle(),
        }))
        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", raising_provider))
        holder_b = FrameHolder(make_frame(0, kill=True))
        svc.add_feed(FeedSourceSpec(5, "aegis-observer-slot-05", holder_b))

        # The raising feed samples to [] and records the failure...
        assert await svc.sample_feed_once("aegis-observer-slot-03") == []
        assert await svc.sample_feed_once("aegis-observer-slot-03") == []
        # ...while the healthy feed still confirms + emits.
        await svc.sample_feed_once("aegis-observer-slot-05")
        emitted = await svc.sample_feed_once("aegis-observer-slot-05")
        assert len(emitted) == 1
        assert emitted[0].payload["observer_slot"] == 5
        # A full scheduler tick over both feeds also survives.
        assert await svc.run_tick() == 2

        status = svc.status()
        bad = status["feeds"]["aegis-observer-slot-03"]
        good = status["feeds"]["aegis-observer-slot-05"]
        assert bad["state"] == "error"
        assert bad["consecutive_errors"] >= 2
        assert "device exploded" in bad["last_error"]
        assert good["state"] == "ok"
        assert good["events_emitted"] == 1

    async def test_frozen_feed_reports_stale_frames(self):
        clock = FakeClock()
        ingest = StubIngest()
        svc = make_service(ingest, clock=clock,
                           detector_factory=factory_with({3: kill_only_bundle()}))
        frozen = make_frame(0, kill=False, seed=7)
        holder = FrameHolder(frozen)
        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", holder))
        await svc.sample_feed_once("aegis-observer-slot-03")
        assert svc.status()["feeds"]["aegis-observer-slot-03"]["state"] == "ok"
        # Same pixels keep arriving, clock runs past the stale window.
        clock.advance(7.0)
        await svc.sample_feed_once("aegis-observer-slot-03")
        health = svc.status()["feeds"]["aegis-observer-slot-03"]
        assert health["state"] == "stale_frames"
        assert health["frames_seen"] == 2

    async def test_no_frame_feed_reports_no_signal(self):
        ingest = StubIngest()
        svc = make_service(ingest, detector_factory=factory_with({
            3: kill_only_bundle(),
        }))
        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", FrameHolder(None)))
        assert await svc.sample_feed_once("aegis-observer-slot-03") == []
        health = svc.status()["feeds"]["aegis-observer-slot-03"]
        assert health["state"] == "no_signal"
        assert health["no_frame_samples"] == 1
        assert ingest.events == []  # no source -> no events, ever

    async def test_disabled_feed_reports_stopped_and_is_not_sampled(self):
        ingest = StubIngest()
        svc = make_service(ingest, detector_factory=factory_with({
            3: kill_only_bundle(),
        }))
        holder = FrameHolder(make_frame(0, kill=True))
        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", holder))
        assert svc.set_feed_enabled("aegis-observer-slot-03", False) is True
        assert await svc.sample_feed_once("aegis-observer-slot-03") == []
        assert holder.calls == 0
        assert svc.status()["feeds"]["aegis-observer-slot-03"]["state"] == "stopped"


# ---------------------------------------------------------------------------
# Cross-feed dedup
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestCrossFeedDedup:
    async def test_same_kill_on_two_feeds_emits_once(self):
        ingest = StubIngest()
        svc = make_service(ingest, detector_factory=factory_with({
            3: kill_only_bundle(), 5: kill_only_bundle(),
        }))
        svc.add_feed(FeedSourceSpec(
            3, "aegis-observer-slot-03", FrameHolder(make_frame(0, kill=True))))
        svc.add_feed(FeedSourceSpec(
            5, "aegis-observer-slot-05", FrameHolder(make_frame(0, kill=True))))
        await svc.sample_feed_once("aegis-observer-slot-03")
        first = await svc.sample_feed_once("aegis-observer-slot-03")
        assert len(first) == 1  # feed 3 confirms first and wins
        await svc.sample_feed_once("aegis-observer-slot-05")
        second = await svc.sample_feed_once("aegis-observer-slot-05")
        assert second == []  # feed 5's vote confirms, the gate suppresses
        assert len(ingest.of_type(FeedEventType.KILL)) == 1
        status = svc.status()
        assert status["cross_feed"]["duplicates_suppressed"] == 1
        assert status["feeds"]["aegis-observer-slot-05"]["duplicates_suppressed"] == 1

    async def test_alive_change_emits_once_across_feeds(self):
        ingest = StubIngest()
        alive_a, alive_b = FakeAliveReader(), FakeAliveReader()
        svc = make_service(ingest, detector_factory=factory_with({
            3: FeedReaderBundle(alive=alive_a),
            5: FeedReaderBundle(alive=alive_b),
        }))
        blank = FrameHolder(make_frame(0, kill=False))
        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", blank))
        svc.add_feed(FeedSourceSpec(5, "aegis-observer-slot-05", blank))
        alive_a.value = 87
        alive_b.value = 87  # the same match-global fact on both feeds
        emitted_a = await svc.sample_feed_once("aegis-observer-slot-03")
        emitted_b = await svc.sample_feed_once("aegis-observer-slot-05")
        assert len(emitted_a) == 1 and emitted_b == []
        alive_events = ingest.of_type(FeedEventType.ALIVE_COUNT)
        assert len(alive_events) == 1
        assert alive_events[0].payload["players_alive"] == 87
        assert alive_events[0].payload["observer_slot"] == 3
        # A genuinely new value emits again.
        alive_a.value = 86
        assert len(await svc.sample_feed_once("aegis-observer-slot-03")) == 1

    async def test_zone_phase_emits_once_and_only_forward(self):
        ingest = StubIngest()
        zone_a, zone_b = FakeZoneReader(), FakeZoneReader()
        svc = make_service(ingest, detector_factory=factory_with({
            3: FeedReaderBundle(zone=zone_a),
            5: FeedReaderBundle(zone=zone_b),
        }))
        blank = FrameHolder(make_frame(0, kill=False))
        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", blank))
        svc.add_feed(FeedSourceSpec(5, "aegis-observer-slot-05", blank))
        zone_a.queue = [zone_event(2)]
        zone_b.queue = [zone_event(2), zone_event(3)]
        assert len(await svc.sample_feed_once("aegis-observer-slot-03")) == 1
        assert await svc.sample_feed_once("aegis-observer-slot-05") == []  # dup phase 2
        assert len(await svc.sample_feed_once("aegis-observer-slot-05")) == 1  # phase 3
        phases = [e.payload["zone_phase"]
                  for e in ingest.of_type(FeedEventType.ZONE_UPDATE)]
        assert phases == [2, 3]


# ---------------------------------------------------------------------------
# Builder + grabber adoption
# ---------------------------------------------------------------------------

class FakeGrabber:
    def __init__(self, frames):
        self.captures = {sid: object() for sid in frames}
        self._frames = frames

    def get_latest_frame(self, source_id):
        return self._frames.get(source_id)


@pytest.mark.vision
class TestBuilder:
    def test_grabber_sources_adopted_only_with_derivable_slot(self):
        ingest = StubIngest()
        grabber = FakeGrabber({
            "aegis-observer-slot-03": make_frame(0),
            "aegis-observer-slot-05": make_frame(0),
            "mystery-source": make_frame(0),  # no slot -> never guessed
        })
        svc = build_multi_feed_monitor(
            ingest, config=make_config(), match_id="m-test",
            frame_grabber=grabber,
            detector_factory=factory_with({3: kill_only_bundle(),
                                           5: kill_only_bundle()}),
            clock=FakeClock(),
        )
        assert sorted(svc.feed_source_ids) == [
            "aegis-observer-slot-03", "aegis-observer-slot-05",
        ]
        feeds = svc.status()["feeds"]
        assert feeds["aegis-observer-slot-03"]["observer_slot"] == 3
        assert feeds["aegis-observer-slot-05"]["observer_slot"] == 5

    def test_excluded_slot_is_never_monitored(self):
        ingest = StubIngest()
        grabber = FakeGrabber({
            "aegis-observer-slot-03": make_frame(0),
            "aegis-observer-slot-05": make_frame(0),
        })
        svc = build_multi_feed_monitor(
            ingest, config=make_config(), frame_grabber=grabber,
            exclude_slots={3},
            detector_factory=factory_with({5: kill_only_bundle()}),
            clock=FakeClock(),
        )
        assert svc.feed_source_ids == ["aegis-observer-slot-05"]

    def test_feed_spec_validates_slot_range(self):
        with pytest.raises(ValueError):
            FeedSourceSpec(0, "aegis-observer-slot-00", FrameHolder(None))
        with pytest.raises(ValueError):
            FeedSourceSpec(17, "aegis-observer-slot-17", FrameHolder(None))

    async def test_no_feeds_is_honest_noop(self):
        ingest = StubIngest()
        svc = make_service(ingest)
        assert await svc.run_tick() == 0
        await svc.start()
        await asyncio.sleep(0.12)
        await svc.stop()
        status = svc.status()
        assert status["feeds_total"] == 0
        assert status["events_emitted"] == 0
        assert ingest.events == []


# ---------------------------------------------------------------------------
# Scheduler: round-robin budget
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestSchedulerBudget:
    async def test_round_robin_respects_batch_size(self):
        ingest = StubIngest()
        config = make_config(monitor_batch_size=2)
        svc = make_service(ingest, config=config, detector_factory=factory_with({
            n: kill_only_bundle() for n in (1, 2, 3, 4, 5)
        }))
        holders = {}
        for n in (1, 2, 3, 4, 5):
            holders[n] = FrameHolder(make_frame(0, kill=False, seed=n))
            svc.add_feed(FeedSourceSpec(
                n, f"aegis-observer-slot-{n:02d}", holders[n]))
        assert await svc.run_tick() == 2
        assert sum(h.calls for h in holders.values()) == 2
        assert await svc.run_tick() == 2
        assert sum(h.calls for h in holders.values()) == 4
        assert await svc.run_tick() == 2  # wraps: 5th feed + rotation
        assert sum(h.calls for h in holders.values()) == 6
        assert all(h.calls >= 1 for h in holders.values())

    def test_budget_summary_states_sixteen_feed_rate(self):
        # Production defaults (CaptureConfig field defaults, NOT the
        # fast test config): tick 0.5 s, batch 4.
        cfg = CaptureConfig(backend="none")
        assert cfg.monitor_tick_s == 0.5 and cfg.monitor_batch_size == 4
        assert cfg.monitor_effective_interval_s(16) == 2.0
        assert cfg.monitor_effective_interval_s(4) == 0.5
        assert cfg.monitor_effective_interval_s(0) == 0.0
        summary = cfg.monitor_budget_summary(16)
        assert summary["effective_samples_per_s_per_feed"] == 0.5

    def test_monitor_env_overrides(self, monkeypatch):
        monkeypatch.setenv("AEGIS_MONITOR_TICK_S", "0.25")
        monkeypatch.setenv("AEGIS_MONITOR_BATCH_SIZE", "2")
        monkeypatch.setenv("AEGIS_MONITOR_VOTE_THRESHOLD", "3")
        cfg = load_capture_config()
        assert cfg.monitor_tick_s == 0.25
        assert cfg.monitor_batch_size == 2
        assert cfg.monitor_vote_threshold == 3
        assert cfg.monitor_effective_interval_s(16) == 2.0


# ---------------------------------------------------------------------------
# Runtime integration (backwards compatible)
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestRuntimeIntegration:
    def test_single_source_status_unchanged_shape(self):
        svc = VisionCaptureService(
            ingest=StubIngest(), config=make_config())
        status = svc.status()
        # Pre-Phase-25 keys all still present...
        for key in ("running", "backend", "detectors", "pipeline", "ocr"):
            assert key in status
        assert len(status["detectors"]) == 10
        # ...and the monitor is additively reported as unattached.
        assert status["multi_feed_monitor"] == {"attached": False}
        assert svc.feed_monitor is None

    async def test_build_vision_service_attaches_monitor(self):
        ingest = StubIngest()
        spec = FeedSourceSpec(
            7, "aegis-observer-slot-07", FrameHolder(None))
        svc = build_vision_service(
            ingest, config=make_config(), match_id="m-test",
            monitor_feeds=[spec],
        )
        assert svc.feed_monitor is not None
        status = svc.status()
        assert status["multi_feed_monitor"]["feeds_total"] == 1
        assert len(status["detectors"]) == 10  # primary pipeline intact

        await svc.start()
        await asyncio.sleep(0.15)
        status = svc.status()
        feed_health = status["multi_feed_monitor"]["feeds"][
            "aegis-observer-slot-07"]
        # No device behind the provider -> honest no_signal, no events.
        assert feed_health["state"] == "no_signal"
        assert ingest.events == []
        await svc.stop()
        assert svc.feed_monitor.running is False

    async def test_primary_slot_excluded_from_monitors(self):
        ingest = StubIngest()
        specs = [
            FeedSourceSpec(3, "aegis-observer-slot-03", FrameHolder(None)),
            FeedSourceSpec(5, "aegis-observer-slot-05", FrameHolder(None)),
        ]
        svc = build_vision_service(
            ingest, config=make_config(), observer_slot=3,
            monitor_feeds=specs,
        )
        # Slot 3 is the primary feed: monitored by the full pipeline,
        # so the parallel monitor must only carry slot 5.
        assert svc.feed_monitor.feed_source_ids == ["aegis-observer-slot-05"]
        assert svc.status()["observer_slot"] == 3
