# backend/tests/unit/test_phase29_w1_pov_reader.py
"""
Phase 29 (W1) — POV state reader: synthetic measurement + tracker +
monitor wiring.

ALL frames here are SYNTHETIC (drawn with OpenCV in this file: team
bar with 4 cards, HP strips at known fill levels, one highlighted
card, a nameplate with the spectated name). The measurement tests
report reader accuracy against those drawn ground truths -- they say
nothing about real BGMI footage. Real spectator-layout calibration
(nameplate region, highlight marker, HSV ranges) is a scrim task.

Conventions follow test_phase26_monitor_wiring.py.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np
import pytest

from app.services.observer_pilot import POVStateTracker
from app.services.observer_pilot.types import POVPlayerStatus
from app.vision.detectors.hud import TeamStatusReader
from app.vision.detectors.pov_reader import POVRawRead, POVReader, RawCardPOV

W, H = 1920, 1080
NAMES = ["Alpha", "Bravo", "Cobra", "Delta"]
ROSTER = ("team-1", [(f"p{i + 1}", NAMES[i]) for i in range(4)])


def roster_provider(slot):
    return ROSTER if slot == 3 else None


# ---------------------------------------------------------------------------
# Synthetic frame builder (drawn ground truth)
# ---------------------------------------------------------------------------


def _bar_geometry():
    r = TeamStatusReader.DEFAULT_REGION
    x1 = int(round(r.x * W))
    y1 = int(round(r.y * H))
    x2 = int(round((r.x + r.w) * W))
    y2 = int(round((r.y + r.h) * H))
    return x1, y1, x2 - x1, y2 - y1


def _nameplate_geometry():
    r = POVReader.DEFAULT_NAMEPLATE_REGION
    x1 = int(round(r.x * W))
    y1 = int(round(r.y * H))
    x2 = int(round((r.x + r.w) * W))
    y2 = int(round((r.y + r.h) * H))
    return x1, y1, x2 - x1, y2 - y1


def make_frame(statuses, hp_fracs, spectated, nameplate_name, seed=0):
    """Draw one synthetic spectator frame.

    statuses/hp_fracs: per-card ground truth. spectated: card index
    with the bright selection border (-1 = none). nameplate_name:
    text drawn in the nameplate region (None = empty nameplate).
    """
    import cv2

    rng = np.random.default_rng(seed)
    frame = np.full((H, W, 3), (58, 52, 48), dtype=np.uint8)
    frame += rng.integers(0, 6, frame.shape, dtype=np.uint8)
    bx, by, bw, bh = _bar_geometry()
    slot_w = bw / 4
    strip_y = int(bh * (1 - 0.32))  # TeamStatusReader hp_strip_fraction
    for i in range(4):
        cx = int(bx + i * slot_w)
        cw = int(bx + (i + 1) * slot_w) - cx
        st = statuses[i]
        if st == "dead":
            card = rng.integers(80, 110, (bh, cw, 3), dtype=np.uint8)
            g = card[:, :, 0]
            frame[by:by + bh, cx:cx + cw] = np.stack([g, g, g], axis=2)
            cv2.putText(frame, NAMES[i], (cx + 8, by + 42),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2,
                        cv2.LINE_AA)
        else:
            cv2.rectangle(frame, (cx, by), (cx + cw - 1, by + bh - 1),
                          (45, 45, 45), -1)
            cv2.putText(frame, NAMES[i], (cx + 8, by + 42),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 230, 230), 2,
                        cv2.LINE_AA)
            frac = hp_fracs[i]
            if st == "alive":
                cv2.rectangle(frame, (cx, by + strip_y + 30),
                              (cx + int(frac * cw), by + strip_y + 48),
                              (0, 200, 0), -1)
            elif st == "knocked":
                cv2.rectangle(frame, (cx, by + strip_y + 28),
                              (cx + int(frac * cw), by + strip_y + 50),
                              (0, 120, 255), -1)
        color = (255, 255, 255) if i == spectated else (28, 28, 28)
        cv2.rectangle(frame, (cx, by), (cx + cw - 1, by + bh - 1), color, 3)
    nx, ny, nw, nh = _nameplate_geometry()
    cv2.rectangle(frame, (nx, ny), (nx + nw - 1, ny + nh - 1), (25, 25, 25), -1)
    if nameplate_name:
        (tw, th), _ = cv2.getTextSize(
            nameplate_name, cv2.FONT_HERSHEY_SIMPLEX, 1.1, 2)
        cv2.putText(frame, nameplate_name,
                    (nx + (nw - tw) // 2, ny + (nh + th) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2,
                    cv2.LINE_AA)
    return frame


class FakeNameOCR:
    """Injectable OCR double: returns a fixed nameplate read."""

    name = "fake_name_ocr"

    def __init__(self, text=None, conf=0.95):
        self._text = text
        self._conf = conf

    @property
    def available(self):
        return True

    def read_text(self, roi, whitelist):
        if self._text is None:
            return None
        return self._text, self._conf


# ---------------------------------------------------------------------------
# SYNTHETIC MEASUREMENT — reader vs drawn ground truth
# (Measured in this environment 2026-10-22: highlight 12/12, status
# 48/48, HP MAE 0.006. These are synthetic-frame numbers ONLY.)
# ---------------------------------------------------------------------------

_PATTERNS = [
    (["alive", "alive", "alive", "alive"], [0.9, 0.75, 0.6, 1.0]),
    (["alive", "knocked", "alive", "dead"], [0.8, 0.6, 0.5, 0.0]),
    (["knocked", "alive", "dead", "alive"], [0.7, 0.95, 0.0, 0.55]),
    (["dead", "alive", "knocked", "alive"], [0.0, 0.65, 0.8, 0.85]),
]


@pytest.mark.unit
def test_synthetic_measurement_highlight_status_hp():
    reader = POVReader(ocr=FakeNameOCR(None))  # highlight/status path only
    n_frames = 12
    hl_ok = cards_ok = cards_total = 0
    hp_errs = []
    for f in range(n_frames):
        statuses, fracs = _PATTERNS[f % len(_PATTERNS)]
        spectated = f % 4
        frame = make_frame(statuses, fracs, spectated, None, seed=f)
        raw = reader.read(frame, now=1000.0 + f)
        if raw.highlight_index == spectated:
            hl_ok += 1
        for i in range(4):
            cards_total += 1
            if raw.cards[i].status.value == statuses[i]:
                cards_ok += 1
            if statuses[i] in ("alive", "knocked"):
                assert raw.cards[i].hp_fraction is not None
                hp_errs.append(abs(raw.cards[i].hp_fraction - fracs[i]))
    # Synthetic ground truth: every drawn case must read back exactly.
    assert hl_ok == n_frames, f"highlight {hl_ok}/{n_frames}"
    assert cards_ok == cards_total, f"status {cards_ok}/{cards_total}"
    assert hp_errs and (sum(hp_errs) / len(hp_errs)) < 0.03


@pytest.mark.unit
def test_synthetic_measurement_nameplate_ocr_real_tesseract():
    from app.vision.detectors.hud import TesseractEngine

    if not TesseractEngine().available:
        pytest.skip("tesseract not available in this environment")
    reader = POVReader()  # default engine = real Tesseract
    ok = total = 0
    for f in range(8):
        statuses, fracs = _PATTERNS[f % len(_PATTERNS)]
        spectated = f % 4
        frame = make_frame(statuses, fracs, spectated, NAMES[spectated], seed=f)
        raw = reader.read(frame, now=2000.0 + f)
        total += 1
        if raw.nameplate_text and NAMES[spectated].lower() in raw.nameplate_text.lower():
            ok += 1
    assert ok == total, f"nameplate OCR {ok}/{total}"


@pytest.mark.unit
def test_no_highlight_and_no_nameplate_is_unknown_not_a_guess():
    reader = POVReader(ocr=FakeNameOCR(None))
    frame = make_frame(["alive"] * 4, [0.9] * 4, -1, None, seed=99)
    raw = reader.read(frame, now=1.0)
    assert raw.highlight_index is None
    assert raw.nameplate_text is None


@pytest.mark.unit
def test_blank_frame_all_unknown():
    reader = POVReader(ocr=FakeNameOCR(None))
    raw = reader.read(np.zeros((H, W, 3), dtype=np.uint8), now=1.0)
    assert raw.highlight_index is None
    assert all(c.status == POVPlayerStatus.UNKNOWN for c in raw.cards)
    assert all(c.hp_fraction is None for c in raw.cards)


# ---------------------------------------------------------------------------
# Tracker: roster binding, identity rules, HP velocity
# ---------------------------------------------------------------------------


def _raw(cards, highlight=None, nameplate=None, ts=100.0):
    return POVRawRead(
        cards=tuple(
            RawCardPOV(index=i, status=s, confidence=0.9, hp_fraction=hp)
            for i, (s, hp) in enumerate(cards)
        ),
        highlight_index=highlight,
        nameplate_text=nameplate,
        nameplate_confidence=0.95 if nameplate else 0.0,
        observed_at=ts,
    )


_ALIVE4 = [(POVPlayerStatus.ALIVE, 1.0)] * 4


@pytest.mark.unit
def test_tracker_binds_roster_and_both_signals_agree():
    tracker = POVStateTracker(3, "src-3", roster_provider)
    state = tracker.update(_raw(_ALIVE4, highlight=2, nameplate="Cobra"))
    assert state.team_id == "team-1"
    assert state.identity_known is True
    assert state.identity_source == "both"
    assert state.spectated_index == 2
    assert state.players[2].name == "Cobra"
    assert state.players[2].player_id == "p3"
    assert state.players[2].is_spectated is True
    assert state.spectated_player().name == "Cobra"


@pytest.mark.unit
def test_tracker_disagreement_yields_unknown_identity():
    tracker = POVStateTracker(3, "src-3", roster_provider)
    state = tracker.update(_raw(_ALIVE4, highlight=0, nameplate="Bravo"))
    assert state.spectated_index is None
    assert state.identity_known is False
    assert state.identity_source == "none"
    assert all(not p.is_spectated for p in state.players)
    # Roster binding itself still flows -- only the identity is unknown.
    assert state.players[1].name == "Bravo"


@pytest.mark.unit
def test_tracker_single_signal_never_confirms_identity():
    """Phase 33 (K-H5 fix): the full identity matrix at tracker level.

    highlight-only -> UNKNOWN, OCR-only -> UNKNOWN, both agree ->
    known ("both"), disagreement -> UNKNOWN, neither -> UNKNOWN.
    A single signal must never set identity_known or is_spectated,
    even though roster binding (names/ids) still flows to the cards.
    Replaces the pre-Phase-33 test that asserted highlight-only ->
    "highlight" and OCR-only -> "ocr" as confirmed identities."""
    tracker = POVStateTracker(3, "src-3", roster_provider)

    # highlight-only: no OCR at all.
    s1 = tracker.update(_raw(_ALIVE4, highlight=1))
    assert (s1.spectated_index, s1.identity_known, s1.identity_source) == (
        None, False, "none")
    assert all(not p.is_spectated for p in s1.players)
    assert s1.players[1].name == "Bravo"  # roster binding still flows

    # OCR-only: no highlight at all.
    s2 = tracker.update(_raw(_ALIVE4, nameplate="Delta"))
    assert (s2.spectated_index, s2.identity_known, s2.identity_source) == (
        None, False, "none")
    assert all(not p.is_spectated for p in s2.players)

    # Both signals present and agreeing: the ONLY confirming case.
    s3 = tracker.update(_raw(_ALIVE4, highlight=2, nameplate="Cobra"))
    assert (s3.spectated_index, s3.identity_known, s3.identity_source) == (
        2, True, "both")
    assert s3.players[2].is_spectated is True
    assert s3.spectated_player().name == "Cobra"

    # Both present and disagreeing: UNKNOWN (also covered by the
    # dedicated disagreement test; repeated here for the matrix).
    s4 = tracker.update(_raw(_ALIVE4, highlight=0, nameplate="Delta"))
    assert (s4.spectated_index, s4.identity_known, s4.identity_source) == (
        None, False, "none")
    assert all(not p.is_spectated for p in s4.players)

    # Neither signal.
    s5 = tracker.update(_raw(_ALIVE4))
    assert (s5.spectated_index, s5.identity_known, s5.identity_source) == (
        None, False, "none")

    # Highlight plus an OCR text that matches NO roster entry: still
    # UNKNOWN -- an unmatchable nameplate is not a second signal, and
    # must not fall back to a highlight-only confirmation.
    s6 = tracker.update(_raw(_ALIVE4, highlight=1, nameplate="Zulu"))
    assert (s6.spectated_index, s6.identity_known, s6.identity_source) == (
        None, False, "none")

    # Highlight outside the roster plus a matching OCR name: still
    # UNKNOWN -- the out-of-range highlight binds nobody, so there
    # is no agreement to confirm.
    s7 = tracker.update(_raw(_ALIVE4, highlight=9, nameplate="Bravo"))
    assert (s7.spectated_index, s7.identity_known, s7.identity_source) == (
        None, False, "none")


@pytest.mark.unit
def test_tracker_without_roster_never_invents_identity():
    tracker = POVStateTracker(3, "src-3", roster_provider=None)
    state = tracker.update(_raw(_ALIVE4, highlight=1, nameplate="Bravo"))
    assert state.team_id is None
    assert state.identity_known is False
    assert state.spectated_index is None
    assert all(p.player_id is None and p.name is None for p in state.players)
    # Statuses still flow -- only identity is roster-bound.
    assert all(p.status == POVPlayerStatus.ALIVE for p in state.players)


@pytest.mark.unit
def test_tracker_hp_velocity_sign_and_first_sample():
    tracker = POVStateTracker(3, "src-3", roster_provider)
    cards = [(POVPlayerStatus.ALIVE, 1.0), (POVPlayerStatus.ALIVE, 0.5),
             (POVPlayerStatus.ALIVE, 0.5), (POVPlayerStatus.ALIVE, 0.5)]
    s1 = tracker.update(_raw(cards, ts=100.0))
    assert all(p.hp_velocity is None for p in s1.players)  # first sample
    cards2 = [(POVPlayerStatus.ALIVE, 0.8), (POVPlayerStatus.ALIVE, 0.6),
              (POVPlayerStatus.ALIVE, 0.5), (POVPlayerStatus.KNOCKED, None)]
    s2 = tracker.update(_raw(cards2, ts=102.0))
    assert s2.players[0].hp_velocity == pytest.approx(-0.1)   # falling
    assert s2.players[1].hp_velocity == pytest.approx(0.05)   # rising (heal)
    assert s2.players[2].hp_velocity == pytest.approx(0.0)
    assert s2.players[3].hp_velocity is None  # no fraction -> no velocity


@pytest.mark.unit
def test_tracker_status_passthrough_including_unknown():
    tracker = POVStateTracker(3, "src-3", roster_provider)
    cards = [(POVPlayerStatus.ALIVE, 0.9), (POVPlayerStatus.KNOCKED, 0.4),
             (POVPlayerStatus.DEAD, None), (POVPlayerStatus.UNKNOWN, None)]
    state = tracker.update(_raw(cards))
    assert [p.status for p in state.players] == [
        POVPlayerStatus.ALIVE, POVPlayerStatus.KNOCKED,
        POVPlayerStatus.DEAD, POVPlayerStatus.UNKNOWN,
    ]
    assert state.players[2].hp_fraction is None


# ---------------------------------------------------------------------------
# Monitor wiring: bundle field, FeedMonitor state, service accessor, stale
# ---------------------------------------------------------------------------


class _Frame:
    def __init__(self, image):
        self.image = image


def _feed_monitor(frame_holder, roster=roster_provider):
    from app.vision.capture.config import CaptureConfig
    from app.vision.capture.feed_health_monitor import MultiFeedHealthTracker
    from app.vision.multi_feed_monitor import (
        FeedMonitor, FeedReaderBundle, FeedSourceSpec,
    )

    spec = FeedSourceSpec(
        slot_number=3, source_id="aegis-observer-slot-03",
        frame_provider=lambda: _Frame(frame_holder["img"]),
    )
    health = MultiFeedHealthTracker()
    health.register_feed(spec.source_id, spec.slot_number)
    bundle = FeedReaderBundle(
        pov=POVReader(ocr=FakeNameOCR("Bravo")),
    )
    return FeedMonitor(spec=spec, readers=bundle, health=health,
                       config=CaptureConfig(), roster_provider=roster)


@pytest.mark.unit
def test_feed_monitor_pov_state_and_stale_gate():
    holder = {"img": make_frame(["alive"] * 4, [0.9] * 4, 1, "Bravo", seed=5)}
    monitor = _feed_monitor(holder)
    monitor.sample_once(500.0)
    state = monitor.pov_state()
    assert state is not None
    assert state.spectated_index == 1
    assert state.identity_source == "both"  # highlight 1 + OCR "Bravo"
    assert state.stale is False
    assert state.observed_at == 500.0

    # Same pixels again: cached state, marked stale, timestamp NOT moved.
    monitor.sample_once(501.0)
    stale = monitor.pov_state()
    assert stale.stale is True
    assert stale.observed_at == 500.0
    assert monitor.pov_stats()["pov_samples_skipped"] == 1

    # Changed frame: fresh read, stale cleared.
    holder["img"] = make_frame(["alive"] * 4, [0.9] * 4, 2, "Bravo", seed=6)
    monitor.sample_once(502.0)
    fresh = monitor.pov_state()
    assert fresh.stale is False
    assert fresh.observed_at == 502.0
    # Highlight says 2, nameplate still says Bravo(1) -> disagreement.
    assert fresh.spectated_index is None
    assert fresh.identity_known is False


@pytest.mark.unit
def test_feed_monitor_without_pov_reader_has_no_state():
    from app.vision.capture.config import CaptureConfig
    from app.vision.capture.feed_health_monitor import MultiFeedHealthTracker
    from app.vision.multi_feed_monitor import (
        FeedMonitor, FeedReaderBundle, FeedSourceSpec,
    )

    spec = FeedSourceSpec(slot_number=1, source_id="s1",
                          frame_provider=lambda: _Frame(
                              np.zeros((90, 160, 3), dtype=np.uint8)))
    health = MultiFeedHealthTracker()
    health.register_feed("s1", 1)
    monitor = FeedMonitor(spec=spec, readers=FeedReaderBundle(),
                          health=health, config=CaptureConfig())
    monitor.sample_once(1.0)  # pre-Phase-29 behaviour: no crash, no state
    assert monitor.pov_state() is None
    assert monitor.pov_stats()["pov_available"] is False


@pytest.mark.unit
def test_service_pov_state_accessor_by_slot():
    import asyncio

    from app.vision.capture.config import CaptureConfig
    from app.vision.multi_feed_monitor import (
        FeedReaderBundle, FeedSourceSpec, MultiFeedMonitorService,
    )

    async def ingest(event):
        return "ok"

    holder = {"img": make_frame(["alive"] * 4, [0.8] * 4, 0, "Bravo", seed=3)}

    def factory(slot, source_id):
        return FeedReaderBundle(pov=POVReader(ocr=FakeNameOCR("Alpha")))

    service = MultiFeedMonitorService(
        ingest=ingest, config=CaptureConfig(), detector_factory=factory,
        roster_provider=roster_provider,
    )
    service.add_feed(FeedSourceSpec(
        slot_number=3, source_id="aegis-observer-slot-03",
        frame_provider=lambda: _Frame(holder["img"]),
    ))
    assert service.pov_state(3) is None  # not sampled yet
    asyncio.run(service.sample_feed_once("aegis-observer-slot-03"))
    state = service.pov_state(3)
    assert state is not None
    assert state.spectated_index == 0
    assert state.identity_source == "both"
    assert service.pov_state(9) is None  # unmonitored slot
    assert set(service.pov_states()) == {3}
    status = service.status()
    rec = status["feeds"]["aegis-observer-slot-03"]
    assert rec["pov_available"] is True
    assert rec["pov_spectated_index"] == 0
