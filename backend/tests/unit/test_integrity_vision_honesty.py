# backend/tests/unit/test_integrity_vision_honesty.py
"""
Integrity Fix Pack — Worker E1 (Vision Honesty, H20 + H21).

H20: the five Gen-1 detectors wired into VisionCaptureService
(HPBar / Healing / Loot / Minimap / Zone) shipped fabricated constant
confidences (0.7 / 0.65 / increments) although they were never in the
Phase 23 measurement programme, and hp_bar_detector applied BGR colour
ranges to an HSV image (a pure-green full-HP bar matched NOTHING).

These tests pin the fixed behaviour:

* hp_bar HSV regression: synthetic bars in each HP colour are detected
  with the width-derived HP% (before the fix: green = 0 detections,
  dark red = 0 detections — see the fix report).
* Every Gen-1 detector's events carry either an evidence-derived
  confidence (contour fill ratio / element coverage fraction) or an
  explicit basis marker — never a bare constant.
* heal_type falls back to "unknown" (amount 0), never invented
  "first_aid"; HPBarInfo.armor_type defaults to "unknown".
* H21: utility explosion/stun need BOTH the constructor bool AND the
  AEGIS_ENABLE_UNMEASURED_UTILITY_DETECTION env gate; enabling logs a
  loud WARNING; bool alone (or env alone) leaves them OFF.
* The runtime forwards confidence_basis into NormalizedEvent payloads
  (and stamps "unmeasured_heuristic" when a detector states no basis).
"""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.vision.detectors.healing_detector import HealingDetector
from app.vision.detectors.hp_bar_detector import HPBarDetector, HPBarInfo
from app.vision.detectors.loot_detector import LootDetector
from app.vision.detectors.minimap_reader import MinimapReader
from app.vision.detectors.utility_detector import (
    UNMEASURED_UTILITY_ENV_VAR,
    UtilityDetector,
)
from app.vision.detectors.zone_detector import ZoneDetector

ENV = UNMEASURED_UTILITY_ENV_VAR


# ---------------------------------------------------------------------------
# H20 — hp_bar HSV regression
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestHPBarHSVRegression:
    """Before the fix these ranges were BGR triples applied to an HSV
    image: green_full -> 0 detections, darkred_critical -> 0 detections
    (yellow/red only matched accidentally via a wrong range)."""

    @pytest.mark.parametrize(
        "color,display",
        [
            ((0, 255, 0), (0, 255, 0)),        # green = full HP
            ((0, 255, 255), (0, 255, 255)),    # yellow = medium
            ((0, 0, 255), (0, 0, 255)),        # bright red = low
            ((0, 0, 150), (0, 0, 150)),        # dark red = critical
        ],
    )
    def test_each_hp_colour_detected(self, color, display):
        det = HPBarDetector()
        region = np.zeros((600, 200, 3), dtype=np.uint8)
        cv2.rectangle(region, (20, 50), (180, 58), color, -1)  # 161px bar

        infos = det._detect_hp_bars(region, 0, 0)

        assert len(infos) == 1
        info = infos[0]
        # HP% comes from bar width: 161/200 * 100.
        assert info.hp_percentage == pytest.approx(80.5, abs=0.6)
        # Classified as the colour actually drawn.
        assert info.hp_bar_color == display
        # Confidence is the evidence fill ratio of a solid bar ~= 1.0,
        # labelled as such — not the old constant 0.7.
        assert info.confidence == pytest.approx(1.0, abs=0.01)
        assert info.confidence != 0.7
        assert info.confidence_basis == "contour_fill_ratio"

    def test_hp_percentage_scales_with_width(self):
        det = HPBarDetector()
        region = np.zeros((600, 200, 3), dtype=np.uint8)
        cv2.rectangle(region, (20, 50), (100, 58), (0, 255, 0), -1)  # 81px
        infos = det._detect_hp_bars(region, 0, 0)
        assert len(infos) == 1
        assert infos[0].hp_percentage == pytest.approx(40.5, abs=0.6)

    def test_armor_type_default_is_unknown_not_invented_level3(self):
        assert HPBarInfo().armor_type == "unknown"

    @pytest.mark.asyncio
    async def test_detect_payload_carries_confidence_basis(self):
        det = HPBarDetector()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # Green bar inside the HP region (x 2%-10%, y 15%-75%).
        cv2.rectangle(frame, (60, 200), (180, 208), (0, 255, 0), -1)
        result = await det.detect(frame)
        players = result.extra_data["players"]
        assert len(players) == 1
        assert players[0]["confidence_basis"] == "contour_fill_ratio"
        assert players[0]["confidence"] == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# H20 — healing: evidence confidence + "unknown" instead of "first_aid"
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestHealingHonesty:
    def _frame_with_bar(self, color):
        frame = np.zeros((800, 800, 3), dtype=np.uint8)
        # Heal region is x 40%-60%, y 50%-55% of the frame. Bar width
        # 100px of an expected full 128px -> progress ~78% (active).
        cv2.rectangle(frame, (330, 410), (429, 425), color, -1)
        return frame

    @pytest.mark.asyncio
    async def test_white_bar_is_bandage_with_evidence_confidence(self):
        det = HealingDetector()
        result = await det.detect(self._frame_with_bar((255, 255, 255)))
        events = result.extra_data["events"]
        assert len(events) == 1
        # White matches the bandage colour rule — evidence, not default.
        assert events[0]["type"] == "bandage"
        assert events[0]["confidence_basis"] == "contour_fill_ratio"
        assert events[0]["confidence"] == pytest.approx(1.0, abs=0.01)
        assert events[0]["confidence"] != 0.7

    @pytest.mark.asyncio
    async def test_unmatched_colour_is_unknown_with_zero_amount(self):
        det = HealingDetector()
        # Bright cyan-ish bar: passes the brightness threshold but
        # matches NO heal-type colour rule.
        result = await det.detect(self._frame_with_bar((255, 255, 100)))
        events = result.extra_data["events"]
        assert len(events) == 1
        assert events[0]["type"] == "unknown"
        active = det.get_active_heals()
        assert active and active[0].heal_type == "unknown"
        # No invented first_aid amount (75) for an unknown heal.
        assert active[0].heal_amount == 0
        assert active[0].heal_duration == 0.0

    def test_identify_heal_type_no_fallback_to_first_aid(self):
        det = HealingDetector()
        gray = np.array([100, 100, 100], dtype=np.uint8)
        assert det._identify_heal_type(gray) == "unknown"
        green = np.array([0, 255, 0], dtype=np.uint8)
        assert det._identify_heal_type(green) == "first_aid"


# ---------------------------------------------------------------------------
# H20 — loot: fill-ratio confidence
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestLootHonesty:
    def test_death_box_confidence_is_fill_ratio(self):
        det = LootDetector()
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.circle(frame, (300, 300), 10, (0, 255, 0), -1)  # green glow
        boxes = det._detect_death_boxes(frame)
        assert len(boxes) == 1
        # A filled circle fills ~pi/4 of its bbox — clearly not the old
        # constant 0.65, and labelled with its basis.
        assert 0.6 < boxes[0].confidence < 0.95
        assert boxes[0].confidence != 0.65
        assert boxes[0].confidence_basis == "contour_fill_ratio"

    def test_air_drop_confidence_is_fill_ratio(self):
        det = LootDetector()
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.circle(frame, (600, 200), 30, (0, 0, 255), -1)  # red smoke blob
        drops = det._detect_air_drops(frame)
        assert len(drops) == 1
        assert 0.6 < drops[0].confidence < 0.95
        assert drops[0].confidence != 0.7
        assert drops[0].confidence_basis == "contour_fill_ratio"


# ---------------------------------------------------------------------------
# H20 — minimap: coverage-fraction confidence
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestMinimapHonesty:
    def test_confidence_is_coverage_fraction_not_constant(self):
        reader = MinimapReader()
        minimap = np.zeros((300, 500, 3), dtype=np.uint8)
        cv2.circle(minimap, (250, 150), 3, (255, 255, 255), -1)  # self
        cv2.circle(minimap, (280, 140), 3, (0, 255, 0), -1)       # teammate
        data = reader._parse_minimap(minimap, 0, 0, 1920, 1080)
        assert data.player_positions  # markers actually read in HSV now
        # 1 of 3 categories found (players; no zone circles drawn).
        assert data.confidence == pytest.approx(1 / 3, abs=0.01)
        assert data.confidence_basis == "element_coverage_fraction"

    def test_empty_minimap_confidence_is_zero(self):
        reader = MinimapReader()
        minimap = np.zeros((300, 500, 3), dtype=np.uint8)
        data = reader._parse_minimap(minimap, 0, 0, 1920, 1080)
        assert data.confidence == 0.0

    @pytest.mark.asyncio
    async def test_pure_noise_yields_no_markers_or_zones(self):
        # Regression guard for the honest HSV ranges: without the
        # compactness + ring-support gates, uniform random noise
        # produced 100+ fake markers and Hough circle hallucinations.
        reader = MinimapReader()
        rng = np.random.default_rng(99)
        for _ in range(2):
            frame = rng.integers(0, 255, (540, 960, 3), dtype=np.uint8)
            result = await reader.detect(frame)
            assert result.extra_data["player_positions"] == {}
            assert result.extra_data["minimap"]["zone"]["safe"] is None
            assert result.extra_data["confidence"] == 0.0

    def test_drawn_zone_ring_is_found_with_support(self):
        reader = MinimapReader()
        minimap = np.zeros((300, 500, 3), dtype=np.uint8)
        cv2.circle(minimap, (250, 150), 80, (255, 255, 255), 3)
        data = reader._parse_minimap(minimap, 0, 0, 1920, 1080)
        assert data.safe_zone_center is not None
        assert data.safe_zone_radius == pytest.approx(80, abs=6)


# ---------------------------------------------------------------------------
# H20 — zone: coverage-fraction confidence + geometry gate
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestZoneHonesty:
    def _detector(self, safe=None, blue=None, timer=None):
        det = ZoneDetector()
        det._detect_circle = lambda hsv, lo, hi: safe if lo[2] == 200 else blue
        det._read_timer = lambda region: timer
        return det

    def test_safe_circle_only_gives_one_third_coverage(self):
        det = self._detector(safe=((100.0, 100.0), 50.0))
        minimap = np.zeros((300, 500, 3), dtype=np.uint8)
        timer = np.zeros((20, 50, 3), dtype=np.uint8)
        info = det._detect_zones(minimap, timer, 1920, 1080)
        assert info is not None
        assert info.confidence == pytest.approx(1 / 3, abs=0.01)
        assert info.confidence_basis == "signal_coverage_fraction"

    def test_all_signals_give_full_coverage(self):
        det = self._detector(
            safe=((100.0, 100.0), 50.0), blue=((110.0, 100.0), 60.0),
            timer="01:30",
        )
        minimap = np.zeros((300, 500, 3), dtype=np.uint8)
        timer_region = np.zeros((20, 50, 3), dtype=np.uint8)
        info = det._detect_zones(minimap, timer_region, 1920, 1080)
        assert info is not None
        assert info.confidence == pytest.approx(1.0, abs=0.01)
        assert info.zone_shrink_timer == 90

    def test_timer_alone_never_fabricates_a_zone(self):
        det = self._detector(timer="01:30")
        minimap = np.zeros((300, 500, 3), dtype=np.uint8)
        timer_region = np.zeros((20, 50, 3), dtype=np.uint8)
        assert det._detect_zones(minimap, timer_region, 1920, 1080) is None


# ---------------------------------------------------------------------------
# H21 — utility explosion/stun: bool AND env gate, loud warning
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestUtilityEnvGate:
    def test_bool_alone_does_not_enable(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        det = UtilityDetector(
            enable_explosion_detection=True, enable_stun_detection=True
        )
        assert det.enable_explosion_detection is False
        assert det.enable_stun_detection is False

    def test_env_alone_does_not_enable(self, monkeypatch):
        monkeypatch.setenv(ENV, "1")
        det = UtilityDetector()
        assert det.enable_explosion_detection is False
        assert det.enable_stun_detection is False

    def test_bool_and_env_enable_with_loud_warning(self, monkeypatch):
        from structlog.testing import capture_logs

        monkeypatch.setenv(ENV, "1")
        with capture_logs() as logs:
            det = UtilityDetector(
                enable_explosion_detection=True, enable_stun_detection=True
            )
        assert det.enable_explosion_detection is True
        assert det.enable_stun_detection is True
        warnings = [
            entry for entry in logs
            if entry.get("log_level") == "warning"
            and "UNMEASURED" in str(entry.get("event", ""))
        ]
        assert len(warnings) == 2  # one per enabled flag

    def test_blocked_request_also_warns(self, monkeypatch):
        from structlog.testing import capture_logs

        monkeypatch.delenv(ENV, raising=False)
        with capture_logs() as logs:
            UtilityDetector(enable_stun_detection=True)
        blocked = [
            entry for entry in logs
            if entry.get("log_level") == "warning"
            and "blocked_by_env_gate" in str(entry.get("event", ""))
        ]
        assert len(blocked) == 1

    def test_default_off_unchanged(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        det = UtilityDetector()
        assert det.enable_explosion_detection is False
        assert det.enable_stun_detection is False

    @pytest.mark.asyncio
    async def test_stun_functional_only_with_env_gate(self, monkeypatch):
        # Same synthetic in-scene flash sequence as the Phase 23 world
        # tests: with bool-only it must NOT fire; with bool+env it must.
        def run_sequence(det):
            async def _run():
                scene = np.full((360, 640, 3), 60, dtype=np.uint8)
                rng = np.random.default_rng(7)
                scene = rng.integers(30, 90, size=(360, 640, 3), dtype=np.uint8)
                white = np.full((360, 640, 3), 255, dtype=np.uint8)
                await det.detect(scene)
                await det.detect(white)
                result = await det.detect(scene)
                return [e["type"] for e in result.extra_data["events"]]
            return _run()

        monkeypatch.delenv(ENV, raising=False)
        det_bool_only = UtilityDetector(enable_stun_detection=True)
        assert "stun" not in await run_sequence(det_bool_only)

        monkeypatch.setenv(ENV, "1")
        det_gated = UtilityDetector(enable_stun_detection=True)
        types = await run_sequence(det_gated)
        assert "stun" in types


# ---------------------------------------------------------------------------
# Runtime forwarding: confidence_basis reaches the bus payload
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestRuntimeConfidenceBasisForwarding:
    def _service(self, hp_extra):
        from app.vision.capture.config import CaptureConfig
        from app.vision.capture.frame_grabber import CapturedFrame, FrameSource
        from app.vision.detectors.base_detector import (
            DetectionResult,
            DetectionStatus,
        )
        from app.vision.runtime import VisionCaptureService

        class Stub:
            def __init__(self, name, extra=None):
                self.name = name
                self.extra = extra or {}

            async def detect(self, frame, **kwargs):
                return DetectionResult(
                    detector_name=self.name,
                    status=DetectionStatus.SUCCESS,
                    extra_data=self.extra,
                )

        async def ingest(event):  # pragma: no cover - unused here
            return "stub"

        stubs = {
            name: Stub(name)
            for name in (
                "killfeed", "alive_count_reader", "zone_timer_reader",
                "team_status_reader", "utility_detector", "minimap_reader",
                "zone_detector", "healing_detector", "loot_detector",
            )
        }
        stubs["hp_bar_detector"] = Stub("hp_bar_detector", hp_extra)
        svc = VisionCaptureService(
            ingest=ingest,
            config=CaptureConfig(backend="none", detector_intervals={}),
            killfeed=stubs["killfeed"], alive=stubs["alive_count_reader"],
            zone=stubs["zone_timer_reader"], team=stubs["team_status_reader"],
            hp=stubs["hp_bar_detector"], utility=stubs["utility_detector"],
            minimap=stubs["minimap_reader"], zone_geom=stubs["zone_detector"],
            healing=stubs["healing_detector"], loot=stubs["loot_detector"],
        )
        self._frame_cls = CapturedFrame
        self._source = FrameSource.ADB_DEVICE
        return svc

    @pytest.mark.asyncio
    async def test_basis_forwarded_and_defaulted_honestly(self):
        from app.vision.capture.frame_grabber import CapturedFrame, FrameSource

        def player(conf, basis_marker):
            p = {
                "player_id": "player_1", "player_name": "Player_1",
                "hp": int(conf), "hp_pct": conf, "armor": 100,
                "armor_type": "unknown", "boost": 0, "is_knocked": False,
                "confidence": 0.9,
            }
            if basis_marker:
                p["confidence_basis"] = basis_marker
            return p

        frame = CapturedFrame(
            frame_id=0, source_id="test",
            source_type=FrameSource.ADB_DEVICE,
            image=np.zeros((540, 960, 3), np.uint8),
        )

        # Detector states an evidence basis -> forwarded verbatim.
        svc = self._service({"players": [player(80.0, "contour_fill_ratio")]})
        svc._extract_events(await svc._pipeline.process_frame(frame))  # baseline
        svc._hp.extra = {"players": [player(55.0, "contour_fill_ratio")]}
        result = await svc._pipeline.process_frame(frame)
        events = svc._extract_events(result)
        hp_events = [e for e in events if e.event_type.value == "hp_update"]
        assert len(hp_events) == 1
        assert hp_events[0].payload["confidence_basis"] == "contour_fill_ratio"

        # Detector states NO basis -> payload is stamped unmeasured,
        # never left looking like a measurement.
        svc2 = self._service({"players": [player(80.0, None)]})
        svc2._extract_events(await svc2._pipeline.process_frame(frame))  # baseline
        svc2._hp.extra = {"players": [player(55.0, None)]}
        result2 = await svc2._pipeline.process_frame(frame)
        events2 = svc2._extract_events(result2)
        hp_events2 = [e for e in events2 if e.event_type.value == "hp_update"]
        assert len(hp_events2) == 1
        assert hp_events2[0].payload["confidence_basis"] == "unmeasured_heuristic"
