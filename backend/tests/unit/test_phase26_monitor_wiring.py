# backend/tests/unit/test_phase26_monitor_wiring.py
"""
Phase 26 (W1) — multi-feed monitor lifespan wiring.

Phase 25 built the monitor but it only existed as a side effect of the
operator-gated vision start. Phase 26 makes ONE monitor lifespan-owned
(app/main.py builds + starts it, ingest = the vision adapter's own
ingest) and hands it to the adapter as a SHARED monitor. These tests
pin the adapter/service/router side of that contract:

  * with a shared monitor attached, connect() builds the primary
    vision service with enable_monitors=False — a second monitor (and
    its second cross-feed gate) is never created;
  * without one, connect() keeps the Phase 25 behaviour
    (enable_monitors=True);
  * the adapter status surfaces the shared monitor even while the
    primary pipeline is idle (the monitor's lifecycle is the app's,
    not the vision pipeline's);
  * FeedService.status() exposes every adapter's status, so
    GET /api/v1/feed/status shows the monitor while the simulator is
    the active adapter;
  * the director decision-log's monitor summary helper reports the
    fleet picture, and None (never a fabrication) when no monitor is
    running in the process.

The lifespan boot itself is proven in
tests/integration/test_phase26_lifespan_monitor.py.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import asyncio

import pytest

from app.services.feed_adapter.service import FeedService
from app.services.feed_adapter.vision_adapter import VisionFeedAdapter
from app.vision.capture.config import CaptureConfig
from app.vision.multi_feed_monitor import MultiFeedMonitorService


class StubIngest:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)
        return f"stub-{len(self.events)}"


def _real_monitor():
    """A real (unstarted) monitor service with no feeds."""
    return MultiFeedMonitorService(
        ingest=StubIngest(), config=CaptureConfig()
    )


class _StubVisionService:
    """Stands in for VisionCaptureService at the adapter seam."""

    def __init__(self):
        self.running = False

        class _Cfg:
            backend = "stub"

        self.config = _Cfg()

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    def status(self):
        return {"running": self.running, "multi_feed_monitor": {"attached": False}}


def _patch_builder(monkeypatch):
    """Patch the builder the adapter imports inside connect()."""
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _StubVisionService()

    import app.vision.runtime as runtime

    monkeypatch.setattr(runtime, "build_vision_service", fake_build)
    return captured


@pytest.mark.unit
def test_adapter_with_shared_monitor_never_builds_a_second(monkeypatch):
    captured = _patch_builder(monkeypatch)
    adapter = VisionFeedAdapter(
        bus=object(),
        multi_device_manager=object(),
        shared_monitor=_real_monitor(),
    )
    asyncio.run(adapter.connect(match_id="m1"))
    assert captured["enable_monitors"] is False
    assert adapter.service is not None and adapter.service.running


@pytest.mark.unit
def test_adapter_without_shared_monitor_keeps_phase25_behaviour(monkeypatch):
    captured = _patch_builder(monkeypatch)
    adapter = VisionFeedAdapter(bus=object(), multi_device_manager=object())
    asyncio.run(adapter.connect(match_id="m1"))
    assert captured["enable_monitors"] is True


@pytest.mark.unit
def test_set_shared_monitor_after_construction(monkeypatch):
    captured = _patch_builder(monkeypatch)
    adapter = VisionFeedAdapter(bus=object(), multi_device_manager=object())
    monitor = _real_monitor()
    adapter.set_shared_monitor(monitor)
    assert adapter.shared_monitor is monitor
    asyncio.run(adapter.connect(match_id="m1"))
    assert captured["enable_monitors"] is False


@pytest.mark.unit
def test_adapter_status_surfaces_shared_monitor_while_idle():
    monitor = _real_monitor()
    adapter = VisionFeedAdapter(bus=object(), shared_monitor=monitor)
    status = adapter.status()
    # Primary pipeline idle and honest about it...
    assert status["vision"] == {"running": False}
    # ...while the lifespan monitor's status is fully visible.
    mfm = status["multi_feed_monitor"]
    assert mfm["running"] is False  # built, not started here
    assert mfm["feeds_total"] == 0
    assert mfm["budget"]["effective_interval_s_per_feed"] is not None
    assert "health_summary" in mfm


@pytest.mark.unit
def test_feed_service_status_exposes_all_adapters():
    service = FeedService()
    adapter = VisionFeedAdapter(bus=object(), shared_monitor=_real_monitor())
    service.register(adapter)
    status = service.status()
    assert "vision" in status["adapters"]
    assert "multi_feed_monitor" in status["adapters"]["vision"]


@pytest.mark.unit
def test_director_monitor_summary_present_and_absent():
    from app.api.v1.director.router import _multi_feed_monitor_summary

    class _State:
        pass

    class _App:
        pass

    class _Req:
        pass

    req = _Req()
    req.app = _App()
    req.app.state = _State()

    # No monitor in this process -> None, never a fabricated summary.
    assert _multi_feed_monitor_summary(req) is None

    req.app.state.multi_feed_monitor = _real_monitor()
    summary = _multi_feed_monitor_summary(req)
    assert summary["running"] is False
    assert summary["feeds_total"] == 0
    assert summary["events_emitted"] == 0
    assert "health_summary" in summary
    assert "budget" in summary
