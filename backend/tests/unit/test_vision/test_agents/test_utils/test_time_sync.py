# backend/tests/unit/test_vision/test_agents/test_utils/test_time_sync.py
"""Unit tests for AEGIS's REAL multi-feed time-sync implementation.

History / honesty note (K-H8 fix, 2026-10-24): the previous version of
this file imported ``app.core.time_sync`` (``TimeSyncManager``,
``TimeSyncConfig``, ``SyncStatus``, ``TimestampNormalizer``). That
module never existed anywhere in the repo — repo-wide grep for
``time_sync`` / ``TimeSyncManager`` / NTP returns zero production hits,
so the old file could only ever die at collection. There is no NTP
calibration, no per-device clock correction, no RTT compensation, and
no corrected-timestamp event ordering in this codebase, and none is
invented here.

What DOES exist is the honest successor: ``LatencySyncTracker`` in
``app.vision.capture.latency_sync`` (Phase 22 / W3). It measures
per-feed grab-time offsets against the fleet median and reports drift
and ±500 ms tolerance — and its own docstring states plainly that it
does NOT re-time frames, because observer phones supply no on-device
timestamps. These tests cover exactly that real behaviour, with fully
deterministic explicit ``capture_ts`` values (no wall-clock dependence,
no network, no NTP).
"""

from __future__ import annotations

import pytest

from app.vision.capture.latency_sync import (
    SYNC_TOLERANCE_MS,
    LatencySyncTracker,
)


@pytest.mark.unit
@pytest.mark.utils
class TestLatencySyncTracker:
    """Test the real LatencySyncTracker (grab-time fleet alignment)."""

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EMPTY / UNKNOWN
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def test_empty_tracker_alignment_report(self):
        report = LatencySyncTracker().alignment_report()
        assert report["tolerance_ms"] == 500.0
        assert report["feeds_measured"] == 0
        assert report["max_abs_offset_ms"] is None
        # No feeds measured -> nothing to claim within tolerance about.
        assert report["all_within_tolerance"] is None
        assert report["feeds"] == {}

    def test_sync_tolerance_constant_is_500ms(self):
        assert SYNC_TOLERANCE_MS == 500.0
        assert LatencySyncTracker().tolerance_ms == 500.0

    def test_unknown_feed_report_is_none(self):
        assert LatencySyncTracker().feed_report("nope") is None

    def test_record_auto_registers_feed(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_1", capture_ts=1000.0)
        report = tracker.feed_report("feed_1")
        assert report is not None
        assert report["samples"] == 1

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OFFSETS vs FLEET MEDIAN
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def test_lone_feed_offset_is_zero(self):
        tracker = LatencySyncTracker()
        tracker.register_feed("feed_1")
        tracker.record_frame("feed_1", capture_ts=1000.0)
        report = tracker.feed_report("feed_1")
        assert report["offset_ms"] == 0.0
        assert report["within_tolerance"] is True

    def test_two_feeds_offsets_are_symmetric_around_median(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0)
        tracker.record_frame("feed_b", capture_ts=1000.4)
        # Median = 1000.2 -> offsets -200 ms / +200 ms.
        assert tracker.feed_report("feed_a")["offset_ms"] == pytest.approx(-200.0)
        assert tracker.feed_report("feed_b")["offset_ms"] == pytest.approx(200.0)
        assert tracker.feed_report("feed_a")["within_tolerance"] is True
        assert tracker.feed_report("feed_b")["within_tolerance"] is True

    def test_offset_exactly_at_tolerance_boundary_is_within(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0)
        tracker.record_frame("feed_b", capture_ts=1001.0)
        # Median = 1000.5 -> offsets exactly -500 ms / +500 ms (<= holds).
        assert tracker.feed_report("feed_b")["offset_ms"] == pytest.approx(500.0)
        assert tracker.feed_report("feed_b")["within_tolerance"] is True

    def test_offset_beyond_tolerance_is_flagged(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0)
        tracker.record_frame("feed_b", capture_ts=1001.2)
        # Median = 1000.6 -> offsets -600 ms / +600 ms.
        assert tracker.feed_report("feed_b")["offset_ms"] == pytest.approx(600.0)
        assert tracker.feed_report("feed_b")["within_tolerance"] is False

        report = tracker.alignment_report()
        assert report["feeds_measured"] == 2
        assert report["max_abs_offset_ms"] == pytest.approx(600.0)
        assert report["all_within_tolerance"] is False

    def test_alignment_report_all_within_when_fleet_is_tight(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0)
        tracker.record_frame("feed_b", capture_ts=1000.1)
        report = tracker.alignment_report()
        assert report["feeds_measured"] == 2
        assert report["all_within_tolerance"] is True
        assert report["max_abs_offset_ms"] == pytest.approx(50.0)

    def test_custom_tolerance_is_respected(self):
        tracker = LatencySyncTracker(tolerance_ms=100.0)
        tracker.record_frame("feed_a", capture_ts=1000.0)
        tracker.record_frame("feed_b", capture_ts=1000.4)
        # 200 ms offset passes the 500 ms default but not a 100 ms window.
        assert tracker.feed_report("feed_b")["within_tolerance"] is False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OFFSET BASIS / REGISTRATION LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def test_offset_basis_is_server_grab_time_without_device_ts(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0)
        report = tracker.feed_report("feed_a")
        assert report["offset_basis"] == "server_grab_time"
        assert report["device_ts"] is None

    def test_offset_basis_is_device_ts_when_supplied(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0, device_ts=999.9)
        report = tracker.feed_report("feed_a")
        assert report["offset_basis"] == "device_ts"
        assert report["device_ts"] == 999.9

    def test_unregister_feed_removes_it_from_reports(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0)
        tracker.record_frame("feed_b", capture_ts=1000.1)
        tracker.unregister_feed("feed_b")
        assert tracker.feed_report("feed_b") is None
        assert tracker.alignment_report()["feeds_measured"] == 1

    def test_sample_count_increments(self):
        tracker = LatencySyncTracker()
        for i in range(3):
            tracker.record_frame("feed_a", capture_ts=1000.0 + i)
        assert tracker.feed_report("feed_a")["samples"] == 3

    def test_reset_clears_all_feeds(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0)
        tracker.reset()
        assert tracker.feed_report("feed_a") is None
        assert tracker.alignment_report()["feeds_measured"] == 0

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DRIFT ESTIMATION (least-squares slope, never invented)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def test_drift_is_none_with_fewer_than_3_samples(self):
        tracker = LatencySyncTracker()
        tracker.record_frame("feed_a", capture_ts=1000.0)
        tracker.record_frame("feed_a", capture_ts=1001.0)
        assert tracker.feed_report("feed_a")["drift_ms_per_min"] is None

    def test_drift_is_none_with_zero_time_span(self):
        tracker = LatencySyncTracker()
        for _ in range(3):
            tracker.record_frame("feed_a", capture_ts=1000.0)
        assert tracker.feed_report("feed_a")["drift_ms_per_min"] is None

    def test_drift_is_zero_for_a_steady_lone_feed(self):
        tracker = LatencySyncTracker()
        for i in range(4):
            tracker.record_frame("feed_a", capture_ts=1000.0 + i * 10)
        # Lone feed: every offset is 0, so the slope is exactly 0.
        assert tracker.feed_report("feed_a")["drift_ms_per_min"] == 0.0

    def test_drift_is_positive_for_a_progressively_lagging_feed(self):
        tracker = LatencySyncTracker()
        # Steady feed + a feed that falls further behind each sample:
        # lagging-feed offsets vs fleet median: 0, +50, +100 ms.
        tracker.record_frame("steady", capture_ts=1000.0)
        tracker.record_frame("lagging", capture_ts=1000.0)
        tracker.record_frame("steady", capture_ts=1010.0)
        tracker.record_frame("lagging", capture_ts=1010.1)
        tracker.record_frame("steady", capture_ts=1020.0)
        tracker.record_frame("lagging", capture_ts=1020.2)
        report = tracker.feed_report("lagging")
        assert report["offset_ms"] == pytest.approx(100.0)
        assert report["drift_ms_per_min"] is not None
        assert report["drift_ms_per_min"] > 0
