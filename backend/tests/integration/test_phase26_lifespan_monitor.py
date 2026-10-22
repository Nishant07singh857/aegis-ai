# backend/tests/integration/test_phase26_lifespan_monitor.py
"""
Phase 26 (W1) — the multi-feed monitor in the REAL app lifespan.

Boots the full FastAPI app (TestClient runs the lifespan) with the
event bus backed by fakeredis (real EventBus code, real pub/sub +
streams semantics; this VM has no Redis server) and proves:

  * the lifespan builds and STARTS the shared multi-feed monitor
    (app.state.multi_feed_monitor, running) — it is no longer a side
    effect of the operator-gated vision start;
  * GET /api/v1/feed/vision/status surfaces the monitor (running)
    while the primary vision pipeline is still idle;
  * a capture appearing in the multi-device manager's grabber is
    AUTO-ENROLLED by the monitor (join), and its disappearance drops
    it (leave) — no restart, no operator action;
  * shutdown stops the monitor.

Skips honestly when the app cannot be imported in this environment
(app.main needs fpdf2 — pre-existing Phase 25 note — or fakeredis is
absent). Run under the environment that has them.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import time

import pytest

pytestmark = pytest.mark.integration

fakeredis = pytest.importorskip("fakeredis")


def _patch_redis_from_url(monkeypatch):
    """Point every redis.asyncio Redis.from_url at one shared
    fakeredis server so the EventBus's three clients (main / pubsub /
    consumer) actually see each other."""
    import redis.asyncio as aioredis

    server = fakeredis.FakeServer()

    def fake_from_url(cls, url, **kwargs):
        return fakeredis.aioredis.FakeRedis(server=server)

    monkeypatch.setattr(
        aioredis.Redis, "from_url", classmethod(fake_from_url)
    )


def test_lifespan_monitor_live_and_auto_enrolls(monkeypatch):
    _patch_redis_from_url(monkeypatch)

    try:
        import app.main as main_module
    except Exception as exc:  # e.g. fpdf missing in this environment
        pytest.skip(f"app.main not importable here: {exc}")

    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.api.v1.video.router import multi_device_manager
    from app.services.feed_adapter.service import feed_service

    app = main_module.app
    app.dependency_overrides[get_current_user] = lambda: object()

    with TestClient(app) as client:
        monitor = getattr(app.state, "multi_feed_monitor", None)
        assert monitor is not None, "lifespan did not build the monitor"
        assert monitor.running is True

        # Status surface: monitor visible while vision pipeline idle.
        resp = client.get("/api/v1/feed/vision/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["vision"] == {"running": False}
        assert body["multi_feed_monitor"]["running"] is True
        assert body["multi_feed_monitor"]["feeds_total"] == 0

        # The adapter carries the SAME shared monitor instance.
        adapter = feed_service.get_adapter("vision")
        assert adapter is not None
        assert adapter.shared_monitor is monitor

        # JOIN: a capture appears in the manager's grabber (as it does
        # when the capture manager maps a device to slot 5) -> the
        # monitor adopts it on a later tick, slot derived from the
        # source id, never guessed.
        grabber = multi_device_manager._grabber
        grabber.captures["aegis-observer-slot-05"] = object()
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if "aegis-observer-slot-05" in monitor.feed_source_ids:
                    break
                time.sleep(0.1)
            assert monitor.feed_source_ids == ["aegis-observer-slot-05"]
            feed = monitor.get_feed("aegis-observer-slot-05")
            assert feed is not None and feed.spec.slot_number == 5
        finally:
            # LEAVE: the capture vanishes -> the monitor drops the feed.
            grabber.captures.pop("aegis-observer-slot-05", None)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if monitor.feed_source_ids == []:
                break
            time.sleep(0.1)
        assert monitor.feed_source_ids == []

    # Shutdown stopped the monitor.
    assert monitor.running is False
