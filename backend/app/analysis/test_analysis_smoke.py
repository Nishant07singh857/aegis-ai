"""Smoke tests for Phase 11 analysis stat computations.

Pure-function tests with in-memory fixtures — no database, no network,
no paid APIs. They pin down the exact numbers the API layer returns,
because the services delegate all math to these functions.
"""

import math

import pytest

from app.analysis.service import (
    average,
    classify_tendency,
    latest_position_at_or_before,
    normalized_edge_ratios,
    polyline_distance_m,
    survival_summary,
    top_drop_locations,
    zone_pull_notes,
)


# ── polyline_distance_m ───────────────────────────────────────────────


def test_polyline_distance_345_triangle():
    assert polyline_distance_m([[0, 0], [3, 4]]) == pytest.approx(5.0)


def test_polyline_distance_multi_segment():
    assert polyline_distance_m([[0, 0], [3, 4], [3, 0]]) == pytest.approx(9.0)


def test_polyline_distance_empty_and_single():
    assert polyline_distance_m([]) == 0.0
    assert polyline_distance_m([[1, 1]]) == 0.0


def test_polyline_distance_skips_malformed_points():
    # "bad" has no y; None is not a point — the valid 3-4-5 pair remains.
    assert polyline_distance_m([[0, 0], "bad", None, [3, 4]]) == pytest.approx(5.0)


# ── survival_summary ──────────────────────────────────────────────────


def test_survival_rate_uses_known_outcomes_only():
    s = survival_summary([True, False, None, True])
    assert s["total_drops"] == 4
    assert s["known_outcomes"] == 3
    assert s["survived"] == 2
    assert s["survival_rate"] == pytest.approx(2 / 3)


def test_survival_rate_none_when_nothing_known():
    s = survival_summary([None, None])
    assert s["total_drops"] == 2
    assert s["known_outcomes"] == 0
    assert s["survival_rate"] is None


def test_survival_rate_all_dead():
    s = survival_summary([False, False])
    assert s["survival_rate"] == pytest.approx(0.0)


# ── top_drop_locations ────────────────────────────────────────────────


def test_top_drop_locations_ranking_and_contested():
    drops = [
        {"named_location": "Pochinki", "map_name": "Erangel", "contested_by": ["t2"]},
        {"named_location": "Pochinki", "map_name": "Erangel", "contested_by": []},
        {"named_location": "Pochinki", "map_name": "Erangel", "contested_by": []},
        {"named_location": "Yasnaya", "map_name": "Erangel", "contested_by": ["t3", "t4"]},
        {"named_location": None, "map_name": "Erangel", "contested_by": []},
    ]
    top = top_drop_locations(drops, n=5)
    assert top[0]["named_location"] == "Pochinki"
    assert top[0]["drops"] == 3
    assert top[0]["contested"] == 1
    assert top[1]["named_location"] == "Yasnaya"
    assert top[1]["contested"] == 1
    # unnamed grouped honestly, not dropped
    assert any(r["named_location"] == "unknown" for r in top)


def test_top_drop_locations_respects_n():
    drops = [{"named_location": f"spot-{i}", "map_name": "M", "contested_by": []} for i in range(8)]
    assert len(top_drop_locations(drops, n=5)) == 5


# ── edge-vs-centre ────────────────────────────────────────────────────


def _phases():
    return [
        {"phase_number": 1, "center_x": 4000.0, "center_y": 4000.0,
         "radius": 2000.0, "timestamp_ms": 600_000},
        {"phase_number": 2, "center_x": 4500.0, "center_y": 4200.0,
         "radius": 1000.0, "timestamp_ms": 1_200_000},
    ]


def test_edge_ratios_center_team():
    positions = [{"ts": 500_000, "x": 4000.0, "y": 4000.0}]
    ratios = normalized_edge_ratios(positions, _phases())
    # phase 1: exactly at centre → 0; phase 2: no newer position → last
    # known position reused (500k ≤ 1200k), dist = hypot(500,200)/1000
    assert ratios[0] == pytest.approx(0.0)
    assert ratios[1] == pytest.approx(math.hypot(500, 200) / 1000)


def test_edge_ratios_skips_phases_without_position():
    positions = [{"ts": 2_000_000, "x": 4000.0, "y": 4000.0}]  # after both phases
    assert normalized_edge_ratios(positions, _phases()) == []


def test_classify_tendency_bands():
    assert classify_tendency(None) == "unknown"
    assert classify_tendency(0.2) == "center"
    assert classify_tendency(0.7) == "mid-zone"
    assert classify_tendency(1.0) == "edge"
    assert classify_tendency(1.5) == "outside"


def test_latest_position_at_or_before():
    positions = [
        {"ts": 100, "x": 1.0, "y": 1.0},
        {"ts": 200, "x": 2.0, "y": 2.0},
        {"ts": 300, "x": 3.0, "y": 3.0},
    ]
    assert latest_position_at_or_before(positions, 250)["x"] == 2.0
    assert latest_position_at_or_before(positions, 50) is None
    assert latest_position_at_or_before(positions, 300)["x"] == 3.0


# ── zone pull notes ───────────────────────────────────────────────────


def test_zone_pull_notes_honest_about_missing_data():
    notes = zone_pull_notes(
        _phases(),
        [{"ts": 500_000, "x": 4000.0, "y": 4000.0}],  # known at phase 1 only
    )
    assert len(notes) == 1
    assert "no team position data" in notes[0]
    assert "shifted" in notes[0]


def test_zone_pull_notes_reports_shift_and_move():
    phases = _phases()
    positions = [
        {"ts": 500_000, "x": 4000.0, "y": 4000.0},
        {"ts": 1_100_000, "x": 4500.0, "y": 4200.0},  # followed the pull exactly
    ]
    notes = zone_pull_notes(phases, positions)
    assert len(notes) == 1
    assert "kept pace" in notes[0]
    assert "539m" in notes[0]  # hypot(500, 200) ≈ 538.5 → rounds to 539


# ── average ───────────────────────────────────────────────────────────


def test_average():
    assert average([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert average([]) is None
    assert average([None, None]) is None
