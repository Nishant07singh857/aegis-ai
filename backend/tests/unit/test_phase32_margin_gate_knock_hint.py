# backend/tests/unit/test_phase32_margin_gate_knock_hint.py
"""Phase 32 Worker 4 — weapon margin gate + knock hint-labelling.

Task 1 (K-H3): production weapon labels ship ONLY at margin >= 0.15
(abstention ON, owner decision). The gate lives in
``IconMatcher.match_candidates`` via the pure ``gated_weapon`` helper
— the single choke point: the kill-feed parser and the multi-feed
monitor both read ``IconMatch.weapon``, so no downstream consumer can
fetch a raw ungated label.

Task 2 (K-H4): live knocks are hint-labelled, never presented as
confirmed kills. No trusted knock producer exists in the codebase
today, so a pure-vision stream must land 100% unverified; the
verified branch is forward-compatible (knock_confirmed / trusted
human source) and must NOT merge with vision hints.

No network, no real DB, no real frames needed for the gate logic:
the matcher path is probed with a stubbed ``_slide_best`` returning
controlled per-family scores (the production ``match_candidates``
code — ranking, second-score, gate — runs unmodified).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.analytics.points_calculator import PointsCalculator
from app.services.live_stats import LiveStatsService
from app.vision.detectors.icon_matcher import (
    MARGIN_THRESHOLD,
    IconMatch,
    IconMatcher,
    gated_weapon,
)
from app.vision.detectors.killfeed import KillFeedDetector, KillFeedEntry

pytestmark = pytest.mark.unit

MATCH = "match-1"
T0 = 1_000.0


# ══════════════════════════════════════════════════════════════════
# Task 1 — pure gate
# ══════════════════════════════════════════════════════════════════


class TestGatedWeaponPure:
    def test_gate_constant_is_owner_decided_value(self):
        assert MARGIN_THRESHOLD == 0.15

    def test_margin_below_gate_abstains(self):
        # 0.80 - 0.66 = 0.14 -> UNKNOWN, never a guessed gun name.
        assert gated_weapon("ar_a", 0.80, 0.66) is None

    def test_margin_boundary_is_confident(self):
        # 0.80 - 0.65 = 0.15 exactly -> confident (inclusive).
        assert gated_weapon("ar_a", 0.80, 0.65) == "ar"

    def test_margin_above_gate_is_confident(self):
        assert gated_weapon("sniper_bolt", 0.90, 0.60) == "sniper"

    def test_zero_scores_abstain(self):
        assert gated_weapon("ar_a", 0.0, 0.0) is None
        assert gated_weapon("ar_a", 0.0, 0.0, threshold=0.55) is None

    def test_unknown_input_abstains(self):
        assert gated_weapon(None, 0.95, 0.10) is None
        assert gated_weapon("unknown_dome", 0.95, 0.10) is None
        assert gated_weapon("", 0.95, 0.10) is None

    def test_below_score_threshold_abstains_even_with_huge_margin(self):
        # Score gate and margin gate are BOTH required.
        assert gated_weapon("ar_a", 0.54, 0.0) is None

    def test_no_wrong_confident_label_across_confusable_table(self):
        """Gate-after check: across deliberately close/confusable
        cases, the number of confident labels that are NOT the true
        family must be zero. Cases where the winner is wrong AND
        close must abstain, not mislabel."""
        cases = [
            # (winner_family, winner_score, runner_score, true_family)
            ("smg_compact", 0.80, 0.66, "ar_a"),       # close, wrong -> abstain
            ("ar_a", 0.80, 0.79, "smg_compact"),       # near-tie, wrong -> abstain
            ("dmr_long", 0.62, 0.60, "sniper_bolt"),   # close, wrong -> abstain
            ("pistol_t", 0.90, 0.50, "pistol_t"),      # clear, right -> confident
            ("ar_b", 0.88, 0.70, "ar_b"),              # clear, right -> confident
            ("unknown_dome", 0.95, 0.10, "ar_a"),      # unknown win -> abstain
            ("ar_a", 0.40, 0.10, "ar_a"),              # low score -> abstain
        ]
        wrong_confident = 0
        confident_correct = 0
        for winner, score, second, true_family in cases:
            label = gated_weapon(winner, score, second)
            if label is not None:
                # A confident label must name the TRUE family (via
                # class mapping: ar_a/ar_b -> "ar", etc.).
                from app.vision.detectors.icon_matcher import FAMILY_TO_CLASS

                if FAMILY_TO_CLASS.get(true_family) != label:
                    wrong_confident += 1
                else:
                    confident_correct += 1
        assert wrong_confident == 0
        assert confident_correct == 2  # only the two clear, right cases


# ══════════════════════════════════════════════════════════════════
# Task 1 — production matcher path (stubbed slide, real gating code)
# ══════════════════════════════════════════════════════════════════


import numpy as np

#: Dummy crop for stubbed-slide probes: content is irrelevant because
#: _slide_best is stubbed, but _to_gray needs a real image array.
_CROP = np.zeros((24, 170, 3), dtype=np.uint8)


def _stub_matcher(per_family: Dict[str, float]) -> IconMatcher:
    """IconMatcher whose _slide_best returns controlled scores.

    ``available`` is bypassed by stubbing the slide itself; the
    ranking / second-score / margin-gate code under test is the real
    ``match_candidates`` implementation.
    """
    m = IconMatcher.__new__(IconMatcher)
    m.icons_dir = ""
    m.preprocess = "none"
    m.threshold = 0.55
    m.marker_threshold = 0.50
    m.upscale = 4
    m.scales = (1.0,)
    m.margin_threshold = MARGIN_THRESHOLD
    # match_candidates derives its family list from _templates keys.
    m._templates = {fam: [] for fam in per_family}
    m._raw_sizes = {}
    m._loaded = True
    m._available = True
    m._lock = None  # unused: _ensure_loaded short-circuits on _loaded

    def _slide(query, families):
        return {
            fam: (score, (0, 0, 10, 10))
            for fam, score in per_family.items()
        }

    m._slide_best = _slide  # type: ignore[method-assign]
    m._ensure_loaded = lambda: None  # type: ignore[method-assign]
    return m


class TestMatcherMarginGate:
    def test_low_margin_top_candidate_abstains_with_flag(self):
        m = _stub_matcher({"ar_a": 0.80, "smg_compact": 0.66})
        out = m.match_candidates(_CROP, top_n=4)
        top = out[0]
        assert top.family == "ar_a"
        assert top.margin == pytest.approx(0.14)
        assert top.weapon is None
        assert top.weapon_abstained is True
        assert top.weapon_family is None

    def test_boundary_margin_top_candidate_is_confident(self):
        m = _stub_matcher({"ar_a": 0.80, "smg_compact": 0.65})
        top = m.match_candidates(_CROP, top_n=4)[0]
        assert top.margin == pytest.approx(0.15)
        assert top.weapon == "ar"
        assert top.weapon_family == "ar_a"
        assert top.weapon_abstained is False

    def test_second_score_is_per_candidate_not_shared(self):
        # Regression: previously every candidate shared ranked[1] as
        # second_score. The runner-up's best OTHER family is the top
        # family, so its margin must be negative, never confident.
        m = _stub_matcher({"ar_a": 0.80, "smg_compact": 0.66})
        out = m.match_candidates(_CROP, top_n=4)
        assert out[0].second_score == pytest.approx(0.66)
        assert out[1].second_score == pytest.approx(0.80)
        assert out[1].margin == pytest.approx(-0.14)
        assert out[1].weapon is None

    def test_runner_up_never_promoted_to_confident(self):
        # Even a high-scoring runner-up cannot carry a weapon: its
        # margin vs the top family is negative by construction.
        m = _stub_matcher({"ar_a": 0.90, "smg_compact": 0.89})
        out = m.match_candidates(_CROP, top_n=4)
        assert all(c.weapon is None for c in out)

    def test_match_top_only_path_also_gated(self):
        m = _stub_matcher({"ar_a": 0.80, "smg_compact": 0.70})
        result = m.match(_CROP)
        assert result.weapon is None
        assert result.weapon_abstained is True


class TestKillFeedGateWiring:
    def test_detector_default_margin_config_is_0_15(self):
        det = KillFeedDetector()
        assert det.config["icon_margin_threshold"] == 0.15

    def test_detector_passes_margin_to_matcher(self):
        det = KillFeedDetector(icon_margin_threshold=0.25)
        matcher = det._get_matcher()
        assert matcher.margin_threshold == 0.25
        # Default detector gets the owner-decided gate.
        det2 = KillFeedDetector()
        assert det2._get_matcher().margin_threshold == 0.15

    def test_finish_entry_carries_abstention(self):
        det = KillFeedDetector()
        match = IconMatch(
            family="ar_a",
            score=0.80,
            second_score=0.70,
            location=(10, 0, 12, 12),
            weapon=None,
            weapon_family=None,
            weapon_abstained=True,
        )
        entry = det._finish_layout_entry("Scout", "Zgod", False, False, match, 50.0)
        assert entry is not None
        assert entry.weapon is None
        assert entry.weapon_abstained is True
        assert entry.weapon_margin == pytest.approx(0.10)

    def test_entry_to_event_abstention_payload(self):
        det = KillFeedDetector()
        entry = KillFeedEntry(
            killer="Scout",
            victim="Zgod",
            event_type="kill",
            weapon=None,
            is_valid=True,
            icon_family="ar_a",
            weapon_margin=0.10,
            weapon_abstained=True,
        )
        event = det._entry_to_event(entry, MATCH, T0)
        assert event.weapon is None  # bus renders this as "Unknown"
        assert event.payload["weapon_abstained"] is True
        assert event.payload["weapon_margin"] == pytest.approx(0.10)
        assert "weapon_family" not in event.payload

    def test_entry_to_event_confident_payload_unchanged_shape(self):
        det = KillFeedDetector()
        entry = KillFeedEntry(
            killer="Scout",
            victim="Zgod",
            event_type="kill",
            weapon="ar",
            is_valid=True,
            weapon_family="ar_a",
            weapon_confidence=0.85,
            icon_family="ar_a",
            weapon_margin=0.30,
            weapon_abstained=False,
        )
        event = det._entry_to_event(entry, MATCH, T0)
        assert event.weapon == "ar"
        assert event.payload["weapon_abstained"] is False
        assert event.payload["weapon_margin"] == pytest.approx(0.30)


# ══════════════════════════════════════════════════════════════════
# Task 2 — knock hint-labelling (fakes mirror test_live_stats.py)
# ══════════════════════════════════════════════════════════════════


class FakeBus:
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
        return "written"


def make_service():
    bus = FakeBus()
    spy = SpyFlusher()
    calculator = PointsCalculator()
    calculator.configure_from_tournament(
        {"id": "tour-test", "points_per_kill": 1, "placement_points": {"1": 10}}
    )
    svc = LiveStatsService(
        bus=bus, points_calculator=calculator, flusher=spy, flush_every=10
    )
    svc.start()
    return svc, bus, spy


def vision_knock(eid, killer, kt, victim, vt, **kw):
    """Pure-vision knock: killfeed icon marker, NO confirmation."""
    data = {
        "id": eid,
        "timestamp": T0 + 100,
        "match_id": MATCH,
        "killer": killer,
        "killerTeam": kt,
        "victim": victim,
        "victimTeam": vt,
        "weapon": "Unknown",
        "isKnock": True,
        "headshot": False,
        "headshot_confirmed": False,
        "headshot_source": "vision_icon",
        "source": "vision_killfeed",
    }
    data.update(kw)
    return data


async def start_match(bus: FakeBus) -> None:
    await bus.deliver(
        "match_start", {"id": "ms-1", "timestamp": T0, "match_id": MATCH}
    )


class TestKnockHintLabelling:
    async def test_uncorroborated_knock_is_hint_labelled(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "knock_event",
            vision_knock("k1", "Scout", "teamA", "Zgod", "teamC"),
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        scout = players["Scout"]
        assert scout["knocks"] == 1
        assert scout["knocks_verified"] == 0
        assert scout["knocks_unverified"] == 1
        assert scout["knocks_hint"] is True
        assert scout["knocks_status"] == "hint"
        # Finishes untouched: a knock is not a kill.
        assert scout["finishes"] == 0

    async def test_trusted_knock_is_verified_not_hint(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "knock_event",
            vision_knock(
                "k1", "Scout", "teamA", "Zgod", "teamC",
                knock_confirmed=True, source="spotter",
            ),
        )
        players = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}
        scout = players["Scout"]
        assert scout["knocks"] == 1
        assert scout["knocks_verified"] == 1
        assert scout["knocks_unverified"] == 0
        assert scout["knocks_hint"] is False
        assert scout["knocks_status"] == "verified"

    async def test_spotter_source_without_explicit_flag_is_trusted(self):
        # Mirrors the headshot C2 gate: a trusted human source alone
        # trusts the knock (spotter/manual/observer).
        svc, bus, _spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "knock_event",
            vision_knock("k1", "Scout", "teamA", "Zgod", "teamC", source="spotter"),
        )
        scout = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}["Scout"]
        assert scout["knocks_verified"] == 1
        assert scout["knocks_hint"] is False

    async def test_mixed_vision_and_trusted_stays_hint(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "knock_event",
            vision_knock(
                "k1", "Scout", "teamA", "Zgod", "teamC",
                knock_confirmed=True, source="spotter",
            ),
        )
        await bus.deliver(
            "knock_event",
            vision_knock("k2", "Scout", "teamA", "Ninja", "teamB"),
        )
        scout = {p["player_id"]: p for p in svc.player_snapshot(MATCH)}["Scout"]
        assert scout["knocks"] == 2
        assert scout["knocks_verified"] == 1
        assert scout["knocks_unverified"] == 1
        assert scout["knocks_hint"] is True
        assert scout["knocks_status"] == "hint"

    async def test_standings_carry_match_level_knock_hint(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "knock_event",
            vision_knock("k1", "Scout", "teamA", "Zgod", "teamC"),
        )
        payload = svc.live_standings(MATCH)
        assert payload["provisional"] is True
        assert payload["knocks_hint"] is True
        assert payload["knocks_verified"] == 0
        assert payload["knocks_unverified"] == 1
        # Standings points are finishes-only: a knock-only team scores 0.
        team_a = [r for r in payload["standings"] if r["team_id"] == "teamA"][0]
        assert team_a["finishes"] == 0
        assert team_a["total_points"] == 0

    async def test_knocks_never_persisted_and_never_become_kills(self):
        svc, bus, spy = make_service()
        await start_match(bus)
        await bus.deliver(
            "knock_event",
            vision_knock("k1", "Scout", "teamA", "Zgod", "teamC"),
        )
        await bus.deliver(
            "match_end",
            {"id": "me-1", "timestamp": T0 + 900, "match_id": MATCH},
        )
        assert spy.calls, "match_end must trigger a final flush"
        for _match_id, rows in spy.calls:
            for row in rows:
                assert "knocks" not in row
                assert "knocks_verified" not in row
        # The flushed row for Scout carries kills=0 despite the knock.
        scout_rows = [
            r for _m, rows in spy.calls for r in rows if r["player_id"] == "Scout"
        ]
        assert scout_rows and scout_rows[-1]["kills"] == 0

    async def test_no_knocks_means_no_hint(self):
        svc, bus, _spy = make_service()
        await start_match(bus)
        payload = svc.live_standings(MATCH)
        assert payload["knocks_hint"] is False
        assert payload["knocks_verified"] == 0
        assert payload["knocks_unverified"] == 0
