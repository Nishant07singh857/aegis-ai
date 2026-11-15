"""
Phase 38 (W3) — M-1 + M-4 + M-5.

M-1: the primary/on-air feed must be excluded from the multi-feed
monitor in the production wiring. The lifespan builds the monitor
with ``multi_device_manager=...`` and no static ``exclude_slots``;
the builder derives the live on-air slot from the manager's
``get_on_air`` (unset at boot, moved mid-match by the director), so
the exclusion is live, not frozen at build time.

M-4: ``start_scrim`` must never invent a ``LOBBY-<digits>`` id on the
live path. A real lobby id comes from the caller and is used
verbatim; without one the scrim starts with the lobby honestly
UNSET (``lobby_id`` None, ``lobby_id_unset`` True).

M-5: a clutch clip's description superlative must match its
evidenced quality — LEGENDARY only when the evidence earned it
(enemy_count >= 4 -> excitement 95), EXCELLENT for the unevidenced
base (80). And ``replay_value`` must be the evidenced excitement
score, not a hardcoded 100.

No network, no DB writes, no secrets. app.main is imported under
the teardown-guarded fpdf stub context from the Phase 33 DB-boot
suite (same pattern as test_integrity_operator_state.py).
"""

from __future__ import annotations

import asyncio
import copy
import re
from types import SimpleNamespace

import numpy as np
import pytest

from test_phase33_db_boot_fail_fast import _fpdf_stub_if_absent  # noqa: E402


def _load_main_module():
    with _fpdf_stub_if_absent():
        try:
            import app.main as main_module
        except ImportError as exc:
            pytest.skip(f"app.main not importable here (missing dependency): {exc}")
        return main_module


main_module = _load_main_module()

from app.agents.base_agent import AgentConfig  # noqa: E402
from app.agents.highlight_agents.clip_detector import (  # noqa: E402
    ClipDetector,
    ClipQuality,
)
from app.api.deps import ADMIN_PRINCIPAL  # noqa: E402
from app.core.event_bus import EventBus  # noqa: E402
from app.vision.capture.config import CaptureConfig  # noqa: E402
from app.vision.capture.frame_grabber import (  # noqa: E402
    CapturedFrame,
    FrameSource,
)
from app.vision.detectors.killfeed import KillFeedEntry  # noqa: E402
from app.vision.multi_feed_monitor import (  # noqa: E402
    FeedReaderBundle,
    FeedSourceSpec,
    build_multi_feed_monitor,
)

W, H = 320, 180
LOBBY_FABRICATED_RE = re.compile(r"LOBBY-\d+")


# ---------------------------------------------------------------------------
# M-1 fakes
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


def _make_frame(frame_id=0, kill=False):
    """Synthetic frame; kill=True paints the marker the fake reader keys on."""
    img = np.zeros((H, W, 3), np.uint8)
    if kill:
        img[10:40, 10:120] = (255, 255, 255)
    return CapturedFrame(
        frame_id=frame_id, source_id="synthetic",
        source_type=FrameSource.ADB_DEVICE, image=img,
        width=W, height=H,
    )


class _FakeKillfeedReader:
    """read_entries seam: one fixed entry when the marker is visible."""

    name = "killfeed"

    def read_entries(self, image):
        if float(image[10:40, 10:120].mean()) > 100.0:
            return [KillFeedEntry(
                killer="Alpha", victim="Bravo", event_type="kill",
                confidence=92.0, raw_text="Alpha Bravo", is_valid=True,
            )]
        return []


class FakeGrabber:
    def __init__(self, frames):
        self.captures = {sid: object() for sid in frames}
        self._frames = frames

    def get_latest_frame(self, source_id):
        return self._frames.get(source_id)


class FakeManager:
    """Stands in for MultiDeviceManager at the builder seam: the
    shared grabber, the mapped observer slots, and the live on-air
    slot the director sets/moves."""

    def __init__(self, frames, on_air=None):
        self._grabber = FakeGrabber(frames)
        self._on_air = on_air
        self._slots = {
            int(m.group(1)): SimpleNamespace(
                source_id=sid, serial=f"serial-{sid}",
            )
            for sid in frames
            for m in [re.search(r"slot-(\d+)", sid)]
            if m
        }

    def get_on_air(self):
        return self._on_air

    def set_on_air(self, slot):
        self._on_air = slot


def _make_config(**kw):
    kw.setdefault("backend", "none")
    kw.setdefault("monitor_tick_s", 0.05)
    kw.setdefault("monitor_batch_size", 4)
    kw.setdefault("monitor_stale_after_s", 6.0)
    kw.setdefault("monitor_no_signal_after_s", 6.0)
    kw.setdefault("monitor_vote_window", 3)
    kw.setdefault("monitor_vote_threshold", 2)
    kw.setdefault("monitor_dedup_ttl_s", 30.0)
    return CaptureConfig(**kw)


def _kill_factory(slot, source_id):
    return FeedReaderBundle(killfeed=_FakeKillfeedReader())


def _build_like_lifespan(ingest, manager):
    """Build the monitor exactly as the lifespan does: ingest +
    multi_device_manager + roster_provider, no static exclude_slots."""
    return build_multi_feed_monitor(
        ingest=ingest,
        multi_device_manager=manager,
        roster_provider=None,
        config=_make_config(),
        detector_factory=_kill_factory,
        clock=FakeClock(),
    )


# ---------------------------------------------------------------------------
# M-1: primary/on-air feed excluded from the monitor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestM1OnAirExcluded:
    def test_on_air_slot_excluded_when_built_like_lifespan(self):
        ingest = StubIngest()
        manager = FakeManager(
            {
                "aegis-observer-slot-03": _make_frame(0, kill=True),
                "aegis-observer-slot-05": _make_frame(0, kill=True),
            },
            on_air=3,
        )
        svc = _build_like_lifespan(ingest, manager)
        # The primary slot is in the (effective) exclusion set …
        assert 3 in svc.excluded_slots
        assert 5 not in svc.excluded_slots
        # … and was never adopted as a monitored feed.
        assert svc.feed_source_ids == ["aegis-observer-slot-05"]

    def test_on_air_feed_never_sampled_only_observer_events_emitted(self):
        ingest = StubIngest()
        manager = FakeManager(
            {
                "aegis-observer-slot-03": _make_frame(0, kill=True),
                "aegis-observer-slot-05": _make_frame(0, kill=True),
            },
            on_air=3,
        )
        svc = _build_like_lifespan(ingest, manager)
        # Direct sampling of the excluded feed is a no-op …
        assert asyncio.run(svc.sample_feed_once("aegis-observer-slot-03")) == []
        # … and ticks emit the kill ONLY from the observer feed, even
        # though the on-air feed shows the identical kill marker
        # (2-of-3 vote needs two ticks).
        for _ in range(3):
            asyncio.run(svc.run_tick())
        kills = ingest.of_type("kill")
        assert len(kills) == 1
        assert kills[0].payload["observer_slot"] == 5
        assert kills[0].payload["source_id"] == "aegis-observer-slot-05"

    def test_on_air_switch_mid_match_drops_feed_and_readopts_old_slot(self):
        ingest = StubIngest()
        manager = FakeManager(
            {
                "aegis-observer-slot-03": _make_frame(0),
                "aegis-observer-slot-05": _make_frame(0),
            },
            on_air=3,
        )
        svc = _build_like_lifespan(ingest, manager)
        assert svc.feed_source_ids == ["aegis-observer-slot-05"]

        # Director moves on-air onto slot 5 mid-match: the monitor
        # drops it on the next sync and re-adopts slot 3, now an
        # observer again.
        manager.set_on_air(5)
        svc.sync_from_grabber()
        assert svc.feed_source_ids == ["aegis-observer-slot-03"]
        assert svc.excluded_slots == {5}

        # On-air cleared: slot 5 is an observer again and re-adopts.
        manager.set_on_air(None)
        svc.sync_from_grabber()
        assert sorted(svc.feed_source_ids) == [
            "aegis-observer-slot-03", "aegis-observer-slot-05",
        ]
        assert svc.excluded_slots == set()

    def test_add_feed_rejects_live_on_air_slot(self):
        svc = build_multi_feed_monitor(
            ingest=StubIngest(), config=_make_config(),
            on_air_slot_provider=lambda: 3,
            detector_factory=_kill_factory, clock=FakeClock(),
        )
        with pytest.raises(ValueError):
            svc.add_feed(FeedSourceSpec(
                3, "aegis-observer-slot-03", lambda: None,
            ))
        # A non-on-air slot still adds fine.
        svc.add_feed(FeedSourceSpec(5, "aegis-observer-slot-05", lambda: None))
        assert svc.feed_source_ids == ["aegis-observer-slot-05"]

    def test_no_on_air_means_nothing_excluded(self):
        """Boot case: on-air is unset until the director picks one —
        the monitor watches every observer feed and nothing crashes."""
        ingest = StubIngest()
        manager = FakeManager(
            {
                "aegis-observer-slot-03": _make_frame(0),
                "aegis-observer-slot-05": _make_frame(0),
            },
            on_air=None,
        )
        svc = _build_like_lifespan(ingest, manager)
        assert svc.excluded_slots == set()
        assert sorted(svc.feed_source_ids) == [
            "aegis-observer-slot-03", "aegis-observer-slot-05",
        ]

    def test_raising_on_air_provider_fails_open(self):
        """A broken provider must never take the monitor down: the
        static exclusion set still applies and feeds still adopt."""

        def _boom():
            raise RuntimeError("manager exploded")

        svc = build_multi_feed_monitor(
            ingest=StubIngest(), config=_make_config(),
            frame_grabber=FakeGrabber(
                {"aegis-observer-slot-05": _make_frame(0)}
            ),
            on_air_slot_provider=_boom,
            detector_factory=_kill_factory, clock=FakeClock(),
        )
        assert svc.excluded_slots == set()
        assert svc.feed_source_ids == ["aegis-observer-slot-05"]


# ---------------------------------------------------------------------------
# M-4: start_scrim never invents a lobby id
# ---------------------------------------------------------------------------


class _FakeWSManager:
    """Captures manager.broadcast calls made by handle_action."""

    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, message):
        self.broadcasts.append(message)


@pytest.fixture
def _scrim_state(monkeypatch):
    """Save/restore the global scrim_state around each M-4 test."""
    saved = copy.deepcopy(main_module.MATCH_STATE["scrim_state"])
    yield
    main_module.MATCH_STATE["scrim_state"] = saved


@pytest.fixture
def _ws_manager(monkeypatch):
    fake = _FakeWSManager()
    monkeypatch.setattr(main_module, "manager", fake)
    return fake


@pytest.mark.unit
class TestM4StartScrimNoInventedLobby:
    async def test_no_lobby_id_means_honestly_unset(self, _scrim_state, _ws_manager):
        ack = await main_module.handle_action(
            {"action": "start_scrim"}, user=ADMIN_PRINCIPAL
        )
        assert ack["status"] == "success"
        state = main_module.MATCH_STATE["scrim_state"]
        assert state["active"] is True
        assert state["lobby_id"] is None
        assert state["lobby_id_unset"] is True
        assert ack["lobby_id"] is None
        # No fabricated LOBBY-<digits> anywhere in the broadcast state.
        assert not LOBBY_FABRICATED_RE.search(str(_ws_manager.broadcasts))

    async def test_caller_supplied_lobby_id_used_verbatim(
        self, _scrim_state, _ws_manager
    ):
        ack = await main_module.handle_action(
            {"action": "start_scrim", "lobby_id": "BGMI-ROOM-7X2Q"},
            user=ADMIN_PRINCIPAL,
        )
        assert ack["status"] == "success"
        state = main_module.MATCH_STATE["scrim_state"]
        assert state["lobby_id"] == "BGMI-ROOM-7X2Q"
        assert state["lobby_id_unset"] is False
        assert ack["lobby_id"] == "BGMI-ROOM-7X2Q"

    @pytest.mark.parametrize("junk", ["", "   ", 1234, ["LOBBY-1"], {"id": 1}])
    async def test_junk_lobby_id_is_unset_not_coerced(
        self, _scrim_state, _ws_manager, junk
    ):
        ack = await main_module.handle_action(
            {"action": "start_scrim", "lobby_id": junk}, user=ADMIN_PRINCIPAL
        )
        assert ack["status"] == "success"
        state = main_module.MATCH_STATE["scrim_state"]
        assert state["lobby_id"] is None
        assert state["lobby_id_unset"] is True
        assert not LOBBY_FABRICATED_RE.search(str(_ws_manager.broadcasts))

    async def test_repeated_starts_never_fabricate(
        self, _scrim_state, _ws_manager
    ):
        for _ in range(25):
            await main_module.handle_action(
                {"action": "start_scrim"}, user=ADMIN_PRINCIPAL
            )
        assert not LOBBY_FABRICATED_RE.search(str(_ws_manager.broadcasts))
        assert not LOBBY_FABRICATED_RE.search(
            str(main_module.MATCH_STATE["scrim_state"])
        )


# ---------------------------------------------------------------------------
# M-5: clutch clip description matches its evidenced quality
# ---------------------------------------------------------------------------


@pytest.fixture
def detector():
    return ClipDetector(AgentConfig(agent_id="test-phase38"), EventBus())


def _clutch(detector, data):
    clip = detector.detect_clip("clutch_win", data, {"match_id": "m1"})
    assert clip is not None
    return clip


@pytest.mark.unit
class TestM5ClutchDescriptionMatchesQuality:
    def test_unevidenced_clutch_is_excellent_not_legendary(self, detector):
        clip = _clutch(detector, {"player_name": "Hero"})
        assert clip.quality == ClipQuality.EXCELLENT
        assert clip.excitement_score == 80
        assert "LEGENDARY" not in clip.description
        assert clip.description.endswith("EXCELLENT!")
        assert clip.replay_value == 80

    def test_count_only_clutch_is_excellent_not_legendary(self, detector):
        # enemy_count 2: evidenced clutch, but below the LEGENDARY bar.
        clip = _clutch(detector, {"player_name": "Hero", "enemy_count": 2})
        assert clip.quality == ClipQuality.EXCELLENT
        assert "LEGENDARY" not in clip.description
        assert clip.description.endswith("EXCELLENT!")
        assert clip.replay_value == 80

    def test_odds_only_clutch_is_excellent_not_legendary(self, detector):
        clip = _clutch(
            detector, {"player_name": "Hero", "win_probability": 0.05}
        )
        assert clip.quality == ClipQuality.EXCELLENT
        assert clip.excitement_score == 85
        assert "LEGENDARY" not in clip.description
        assert clip.description.endswith("EXCELLENT!")
        assert clip.replay_value == 85

    def test_evidenced_1v4_clutch_is_legendary(self, detector):
        clip = _clutch(
            detector,
            {"player_name": "Hero", "enemy_count": 4, "win_probability": 0.05},
        )
        assert clip.quality == ClipQuality.LEGENDARY
        assert clip.excitement_score == 100
        assert clip.description.endswith("LEGENDARY!")
        assert clip.replay_value == 100

    def test_evidenced_1v4_without_odds_is_legendary(self, detector):
        clip = _clutch(detector, {"player_name": "Hero", "enemy_count": 4})
        assert clip.quality == ClipQuality.LEGENDARY
        assert clip.excitement_score == 95
        assert clip.description.endswith("LEGENDARY!")
        assert clip.replay_value == 95

    def test_replay_value_never_hardcoded_100(self, detector):
        # Every clutch shape: replay_value == the evidenced excitement,
        # never the old fixed 100 (except when excitement itself is 100).
        cases = [
            ({"player_name": "Hero"}, 80),
            ({"player_name": "Hero", "enemy_count": 2}, 80),
            ({"player_name": "Hero", "win_probability": 0.05}, 85),
            ({"player_name": "Hero", "enemy_count": 4}, 95),
        ]
        for data, expected in cases:
            clip = _clutch(detector, data)
            assert clip.replay_value == expected, data
            assert clip.replay_value == clip.excitement_score, data
