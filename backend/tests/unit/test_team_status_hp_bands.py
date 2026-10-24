# backend/tests/unit/test_team_status_hp_bands.py
"""
TeamStatusReader HP-band sweep — regression harness for the Phase 29
D-1 root defect (a live low-HP card read as DEAD).

Frames are SYNTHETIC, drawn by the Phase 29 W1 builder
(``test_phase29_w1_pov_reader.make_frame``, 1920x1080,
``TeamStatusReader.DEFAULT_REGION`` team bar). Numbers measured on
those drawn frames say nothing about real BGMI footage; real
spectator calibration remains a scrim task.

BEFORE the fix (measured 2026-10-22 on this builder, slot 0):
  * ALIVE  hp 0.05-0.40 -> DEAD (conf 0.60); ALIVE only from 0.42.
  * KNOCKED hp 0.05-0.40 -> DEAD; KNOCKED only from 0.42.
  * Pilot path (real POVReader + tracker + pilot service, spectated
    ALIVE @ hp 0.35, 6 evaluations): 1x dead_unconfirmed, then the
    2-eval gate passed and R1_SPECTATED_DEAD executed on evals 2-6
    (5 wrong switch-away taps) against a player who was alive.

Root cause (hud.py ``TeamStatusReader._classify_slot``, pre-fix):
  1. ``_is_bar`` required the colour mask to span >= 40% of the card
     width — a partial HP fill IS the band, so <40% HP was
     structurally unclassifiable as alive/knocked.
  2. healthy/knocked ratios were computed over the WHOLE strip
     (bar-height fraction x width fraction), so the 0.10/0.14 ratio
     floors failed partial fills even when the band was present.
  3. DEAD was a fall-through: whole-card fill_ratio == 1.0 and
     whole-card mean_sat < 40 held for every live card (the coloured
     strip is ~3% of the card and dilutes out), so every card
     rejected by (1)/(2) became DEAD instead of UNKNOWN.

AFTER expectations pinned here:
  * every drawn alive/knocked band classifies as its true state and
    NEVER as DEAD, down to hp 0.03 (the lowest fill the builder draws
    as a measurable HSV band: band-region fill 0.008, purity 1.0);
  * hp 0.01-0.02 (the builder draws <= 2 px; zero pixels land in the
    HSV band) is UNKNOWN, not DEAD;
  * controls stay honest: dead card -> DEAD, blank / no-card clutter
    / colour speckle in the strip / a solid full-strip colour block
    -> UNKNOWN.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pytest

from app.vision.detectors.hud import TeamSlotStatus, TeamStatusReader

from test_phase29_w1_pov_reader import FakeNameOCR, NAMES, make_frame

W, H = 1920, 1080
READER = TeamStatusReader()

# HP bands (task spec) with 3 in-band sample points each.
BAND_SAMPLES = [
    ((0.0, 0.1), [0.03, 0.055, 0.09]),
    ((0.1, 0.2), [0.12, 0.15, 0.18]),
    ((0.2, 0.3), [0.22, 0.25, 0.28]),
    ((0.3, 0.35), [0.31, 0.33, 0.345]),
    ((0.35, 0.4), [0.36, 0.38, 0.395]),
    ((0.4, 0.5), [0.42, 0.45, 0.48]),
    ((0.5, 0.75), [0.55, 0.65, 0.72]),
    ((0.75, 1.0), [0.8, 0.9, 1.0]),
]


def _slot0(statuses, fracs, seed=7):
    frame = make_frame(statuses, fracs, 0, NAMES[0], seed=seed)
    return READER.read(frame).slots[0]


# ---------------------------------------------------------------------------
# Sweep: alive / knocked x HP bands
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hp", [hp for _band, samples in BAND_SAMPLES for hp in samples]
)
def test_alive_never_dead_across_hp_bands(hp):
    got = _slot0(["alive"] * 4, [hp, 0.9, 0.9, 0.9])
    assert got == TeamSlotStatus.ALIVE, f"alive hp={hp} read as {got}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "hp", [hp for _band, samples in BAND_SAMPLES for hp in samples]
)
def test_knocked_never_dead_across_hp_bands(hp):
    got = _slot0(["knocked", "alive", "alive", "alive"], [hp, 0.9, 0.9, 0.9])
    assert got == TeamSlotStatus.KNOCKED, f"knocked hp={hp} read as {got}"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["alive", "knocked"])
@pytest.mark.parametrize("hp", [0.01, 0.02])
def test_subpixel_hp_is_unknown_not_dead(state, hp):
    # The W1 builder draws <= 2 px at these fills and zero of them
    # land in the HSV band (measured) — there is no band evidence,
    # and a no-band dark card must not become DEAD either.
    statuses = [state, "alive", "alive", "alive"] if state == "knocked" else [state] * 4
    got = _slot0(statuses, [hp, 0.9, 0.9, 0.9])
    assert got == TeamSlotStatus.UNKNOWN, f"{state} hp={hp} read as {got}"


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dead_card_still_dead():
    got = _slot0(["dead", "alive", "alive", "alive"], [0.0, 0.9, 0.9, 0.9])
    assert got == TeamSlotStatus.DEAD


@pytest.mark.unit
def test_dead_card_hud_builder_still_dead():
    from test_hud_readers import frame_with_team

    status = READER.read(frame_with_team(["dead", "alive", "knocked", "dead"]))
    assert status.slots[0] == TeamSlotStatus.DEAD
    assert status.slots[3] == TeamSlotStatus.DEAD


@pytest.mark.unit
def test_blank_frame_all_unknown():
    status = READER.read(np.zeros((H, W, 3), dtype=np.uint8))
    assert all(s == TeamSlotStatus.UNKNOWN for s in status.slots)


@pytest.mark.unit
def test_no_card_clutter_all_unknown():
    # Background texture only (the make_frame backdrop), no team bar.
    rng = np.random.default_rng(0)
    frame = np.full((H, W, 3), (58, 52, 48), dtype=np.uint8)
    frame += rng.integers(0, 6, frame.shape, dtype=np.uint8)
    status = READER.read(frame)
    assert all(s == TeamSlotStatus.UNKNOWN for s in status.slots)


def _speckle_frame(density, mode, seed=3):
    """Alive-style card chassis for slot 0 whose HP strip is filled
    with scattered colour speckle instead of a band."""
    import cv2

    frame = make_frame(["alive"] * 4, [0.0, 0.9, 0.9, 0.9], 0, NAMES[0], seed=1)
    r = TeamStatusReader.DEFAULT_REGION
    bx, by = int(round(r.x * W)), int(round(r.y * H))
    bw, bh = int(round(r.w * W)), int(round(r.h * H))
    strip_y = int(bh * (1 - 0.32))
    cw = int(bw / 4)
    cv2.rectangle(frame, (bx, by + strip_y), (bx + cw - 1, by + bh - 1),
                  (45, 45, 45), -1)
    rng = np.random.default_rng(seed)
    mask = rng.random((bh - strip_y, cw)) < density
    region = frame[by + strip_y:by + bh, bx:bx + cw]
    if mode == "random":
        cols = rng.integers(0, 255, (bh - strip_y, cw, 3), dtype=np.uint8)
        region[mask] = cols[mask]
    elif mode == "green":
        region[mask] = (0, 200, 0)
    elif mode == "orange":
        region[mask] = (0, 120, 255)
    frame[by + strip_y:by + bh, bx:bx + cw] = region
    return frame


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["green", "orange", "random"])
@pytest.mark.parametrize("density", [0.05, 0.2])
def test_strip_speckle_is_unknown_not_a_state(mode, density):
    status = READER.read(_speckle_frame(density, mode))
    assert status.slots[0] == TeamSlotStatus.UNKNOWN, (
        f"speckle mode={mode} density={density} read as {status.slots[0]}"
    )


@pytest.mark.unit
def test_solid_full_strip_colour_is_unknown():
    # A strip flooded with HP colour has no band structure
    # (concentration ~1, peak at the top) — not an HP bar.
    import cv2

    frame = make_frame(["alive"] * 4, [0.0, 0.9, 0.9, 0.9], 0, NAMES[0], seed=1)
    r = TeamStatusReader.DEFAULT_REGION
    bx, by = int(round(r.x * W)), int(round(r.y * H))
    bw, bh = int(round(r.w * W)), int(round(r.h * H))
    strip_y = int(bh * (1 - 0.32))
    cw = int(bw / 4)
    cv2.rectangle(frame, (bx, by + strip_y), (bx + cw - 1, by + bh - 1),
                  (0, 200, 0), -1)
    status = READER.read(frame)
    assert status.slots[0] == TeamSlotStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Pilot path: REAL reader loop, spectated ALIVE @ hp 0.35
# (pattern mirrors test_phase29_fixes.build; statuses are NOT faked —
# they come from POVReader -> TeamStatusReader on drawn frames)
# ---------------------------------------------------------------------------


class _FakeTapResult:
    def __init__(self, status="verified", taps_sent=1):
        self.status = status
        self.taps_sent = taps_sent
        self.detail = "fake tap"
        self.slot_number = None
        self.to_index = None


class _FakeControl:
    enabled = True
    disabled_slots = set()

    def allows(self, slot):
        return True


class _FakeExecutor:
    def __init__(self):
        self.control = _FakeControl()
        self.calls = []

    def execute(self, decision, now):
        self.calls.append({"to": decision.to_index, "rule": decision.rule})
        return _FakeTapResult()


class _RecordingJournal:
    def __init__(self):
        self.entries = []

    def record(self, entry):
        self.entries.append(dict(entry))
        return f"row-{len(self.entries)}"

    def by_fate(self, fate):
        return [e for e in self.entries if e.get("fate") == fate]


_ROSTER = ("team-1", [(f"p{i + 1}", NAMES[i]) for i in range(4)])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pilot_never_switches_away_from_live_low_hp_player():
    from app.services.observer_pilot import POVStateTracker
    from app.services.observer_pilot.service import (
        ObserverPilotService,
        PilotServiceConfig,
    )
    from app.vision.detectors.pov_reader import POVReader

    reader = POVReader(ocr=FakeNameOCR(NAMES[0]))
    tracker = POVStateTracker(1, "aegis-observer-slot-01",
                              lambda slot: _ROSTER)
    journal = _RecordingJournal()
    executor = _FakeExecutor()
    service = ObserverPilotService(
        monitor=None,
        executor=executor,
        config=PilotServiceConfig(),  # dead_confirm_evals=2 (the D-1 gate)
        mode_provider=lambda: ("full_auto", False),
        engagement_provider=lambda: {"teams": {}},
        phase_provider=lambda: "mid_game",
        journal=journal,
        clock=lambda: 1000.0,
    )
    outcomes = []
    for i in range(6):
        t = 1000.0 + i
        frame = make_frame(["alive"] * 4, [0.35, 0.9, 0.9, 0.9], 0,
                           NAMES[0], seed=10 + i)
        state = tracker.update(reader.read(frame, now=t), observed_at=t)
        assert state.spectated_index == 0
        outcome = await service.evaluate_feed(state, now=t)
        outcomes.append(outcome["outcome"] if outcome else None)
    # The spectated player is ALIVE at 35% HP the whole time: the
    # pilot must take no action, the D-1 gate must never even arm,
    # and no tap may ever be sent.
    assert outcomes == ["no_action"] * 6, outcomes
    assert journal.by_fate("dead_unconfirmed") == []
    assert service.stats["dead_unconfirmed"] == 0
    assert executor.calls == []
