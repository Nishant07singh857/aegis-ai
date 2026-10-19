# backend/tests/unit/test_vision/test_kill_feed_ocr.py
"""
Kill feed OCR detector tests (Phase 14 W4 rewrite).

The pre-Phase-14 ``KillFeedOCR`` (EasyOCR backend, ``_parse_kill_entry``,
``known_players`` dict, weapon-from-brackets guessing) was retired in
Phase 14 W2: the EasyOCR backend could not even be imported, and the
weapon/known-player logic violated the never-guess rule.

``app.vision.detectors.kill_feed_ocr`` is now a thin deprecated shim that
re-exports the production ``KillFeedDetector`` (Tesseract, word-boundary
parse, SHA1 dedup, honest ``None`` weapon). These tests pin the SHIM
contract -- i.e. the new API as seen through the old import path -- so
existing imports keep working and stay covered.

End-to-end OCR accuracy is measured separately in
``backend/tests/test_killfeed_detector.py`` (synthetic frames, 90%).
"""

import pytest
import numpy as np

from app.vision.detectors.kill_feed_ocr import (
    KillFeedDetector,
    KillFeedEntry,
    KillFeedOCR,
    KNOWN_WEAPON_TEXT,
    DEFAULT_KILL_FEED_REGION,
)


@pytest.mark.unit
@pytest.mark.vision
class TestKillFeedOCRShim:
    """The deprecated alias still resolves to the production detector."""

    def test_alias_is_production_detector(self):
        assert KillFeedOCR is KillFeedDetector

    def test_default_region_preserved(self):
        assert DEFAULT_KILL_FEED_REGION == (0.62, 0.02, 0.99, 0.30)

    def test_weapon_reference_list_exported(self):
        assert "m416" in KNOWN_WEAPON_TEXT
        assert "awm" in KNOWN_WEAPON_TEXT


@pytest.mark.unit
@pytest.mark.vision
class TestKillFeedParsing:
    """parse_text: word-boundary action anchor, never guesses names."""

    @pytest.fixture
    def detector(self):
        return KillFeedDetector()

    def test_parse_knock(self, detector):
        entry = detector.parse_text("SoulMortal knocked GodLJonathan", 90.0)
        assert entry is not None and entry.is_valid
        assert (entry.killer, entry.victim, entry.event_type) == (
            "SoulMortal", "GodLJonathan", "knock")

    def test_parse_kill_variations(self, detector):
        for text in ("AX eliminated BX", "AX killed BX", "AX finished BX"):
            entry = detector.parse_text(text, 90.0)
            assert entry is not None and entry.event_type == "kill", text

    def test_parse_knock_variations(self, detector):
        for text in ("AX knocked BX", "AX knock BX", "AX downed BX"):
            entry = detector.parse_text(text, 90.0)
            assert entry is not None and entry.event_type == "knock", text

    def test_headshot_flag(self, detector):
        entry = detector.parse_text("AX eliminated BX headshot", 90.0)
        assert entry is not None and entry.headshot is True

    def test_killer_named_with_kill_substring_not_misclassified(self, detector):
        # Word boundaries: a killer literally named "SkillzOP" must not be
        # misclassified by the "kill" substring.
        entry = detector.parse_text("SkillzOP knocked VictimX", 90.0)
        assert entry is not None
        assert entry.killer == "SkillzOP" and entry.event_type == "knock"

    def test_no_action_word_returns_none(self, detector):
        assert detector.parse_text("Welcome to the lobby", 90.0) is None

    def test_garbage_returns_none(self, detector):
        for text in ("", "   ", "ab", "loneword", "!!! ###"):
            assert detector.parse_text(text, 90.0) is None, text

    def test_weapon_is_honestly_none(self, detector):
        # Weapon icons are never template-matched or guessed.
        entry = detector.parse_text("AX eliminated BX", 90.0)
        assert entry is not None and entry.weapon is None

    def test_dedup_key_stable(self):
        e1 = KillFeedEntry(killer="A", victim="B", event_type="knock")
        e2 = KillFeedEntry(killer="A", victim="B", event_type="knock")
        assert e1.dedup_key() == e2.dedup_key()
        e3 = KillFeedEntry(killer="A", victim="C", event_type="knock")
        assert e1.dedup_key() != e3.dedup_key()


@pytest.mark.unit
@pytest.mark.vision
class TestKillFeedDetectorLifecycle:
    @pytest.fixture
    def detector(self):
        return KillFeedDetector()

    def test_detection_regions(self, detector):
        regions = detector.get_detection_regions(1920, 1080)
        assert len(regions) == 1
        assert regions[0].label == "kill_feed"

    def test_blank_frame_no_events_no_crash(self, detector):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        assert detector.parse_frame(frame) == []

    def test_dedup_across_frames(self, detector):
        now = 1000.0
        assert detector._remember(
            KillFeedEntry(killer="A", victim="B", event_type="knock",
                          is_valid=True), now) is True
        # Same kill within TTL -> duplicate, not emitted.
        assert detector._remember(
            KillFeedEntry(killer="A", victim="B", event_type="knock",
                          is_valid=True), now + 5.0) is False
        # After TTL -> new again.
        assert detector._remember(
            KillFeedEntry(killer="A", victim="B", event_type="knock",
                          is_valid=True), now + 31.0) is True

    def test_dedup_table_bounded(self, detector):
        det = KillFeedDetector(dedup_max_keys=10, dedup_ttl_sec=3600.0)
        for i in range(25):
            det._remember(KillFeedEntry(killer=f"P{i}", victim="V",
                                       event_type="knock", is_valid=True),
                          now=1000.0 + i)
        assert len(det._seen) <= 10

    def test_clear_dedup(self, detector):
        detector._remember(KillFeedEntry(killer="A", victim="B",
                                        event_type="knock", is_valid=True),
                           now=1000.0)
        detector.clear_dedup()
        assert detector._seen == {}

    def test_reset(self, detector):
        detector.reset()  # BaseDetector contract: no crash
        assert detector.get_stats()["total_detections"] == 0

    @pytest.mark.asyncio
    async def test_detect_blank_frame(self, detector):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = await detector.detect(frame)
        assert result.detector_name == "killfeed"
        assert result.extra_data["events"] == []
