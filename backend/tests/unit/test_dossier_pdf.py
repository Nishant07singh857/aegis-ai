"""Unit tests for the dossiers PDF generators (Phase 11, Worker W2).

No database required: both generators accept plain dicts. Every test asserts
the output is a real PDF (%PDF magic, >2KB) and that honest messaging appears
when data is missing.
"""

import pytest

from app.dossiers.pdf import match_report_pdf, team_dossier_pdf
from app.dossiers.service import classify_playstyle


# ── fixtures ──────────────────────────────────────────────────────────


def _fight(i: int, team_a: str, team_b: str, winner: str | None) -> dict:
    return {
        "id": f"fight-{i}",
        "match_id": "match-1",
        "map_name": "Erangel",
        "start_timestamp_ms": 600_000 + i * 120_000,
        "end_timestamp_ms": 660_000 + i * 120_000,
        "duration_ms": 60_000,
        "location_x": 3200.0 + i * 100,
        "location_y": 4100.0 - i * 50,
        "intensity_score": 0.72 + i * 0.02,
        "teams": [
            {"team_id": "t1", "team_name": team_a, "players": []},
            {"team_id": "t2", "team_name": team_b, "players": []},
        ],
        "winner": {"team_id": "t1", "team_name": winner} if winner else None,
        "outcome": f"{winner} won" if winner else "disengaged",
        "third_parties": [],
        "total_kills": 5 + i,
        "total_knocks": 7 + i,
        "utility_used": {"smokes": 3},
    }


def _rich_match_report() -> dict:
    return {
        "match_id": "match-1",
        "tournament_name": "BMSD 2026 Grand Finals",
        "match_number": 3,
        "map_name": "Erangel",
        "played_at": "2026-10-18T10:00:00+00:00",
        "team_summaries": [
            {"team_id": "t1", "team_name": "Team Alpha", "placement": 1,
             "placement_note": "", "finishes": 14, "kills": 14},
            {"team_id": "t2", "team_name": "Team Bravo", "placement": 2,
             "placement_note": "", "finishes": 9, "kills": 9},
            {"team_id": "t3", "team_name": "Team Charlie", "placement": None,
             "placement_note": "not recorded", "finishes": 4, "kills": 4},
        ],
        "fights": [
            _fight(1, "Team Alpha", "Team Bravo", "Team Alpha"),
            _fight(2, "Team Bravo", "Team Charlie", None),
            _fight(3, "Team Alpha", "Team Charlie", "Team Charlie"),
        ],
        "rotation_highlights": [
            {"team": "Team Alpha", "rotations": 5,
             "first_rotation_at_ms": 300_000, "last_rotation_at_ms": 1_500_000},
            {"team": "Team Bravo", "rotations": 4,
             "first_rotation_at_ms": 420_000, "last_rotation_at_ms": 1_400_000},
        ],
        "notes": ["Placements sourced from match.meta['placements']."],
    }


def _empty_match_report() -> dict:
    return {
        "match_id": "match-9",
        "tournament_name": "Unknown tournament",
        "match_number": None,
        "map_name": "Unknown map",
        "played_at": "",
        "team_summaries": [],
        "fights": [],
        "rotation_highlights": [],
        "notes": ["No fight events recorded for this match."],
    }


def _rich_dossier() -> dict:
    return {
        "team_id": "t1",
        "team_name": "Team Alpha",
        "computed_at": "2026-10-18T13:00:00+00:00",
        "data_freshness": "recomputed",
        "matches_sampled": 8,
        "fights_sampled": 22,
        "playstyle": {
            "label": "aggressive",
            "evidence": "2.8 fights/match, aggression_score 0.72",
            "fights_per_match": 2.75,
            "aggression_score": 0.72,
            "matches_sampled": 8,
        },
        "strengths": [
            {"metric": "fight_win_rate",
             "label": "fight win rate 0.62 vs 0.41 tournament avg",
             "team_value": 0.62, "tournament_avg": 0.41, "delta_pct": 51.2},
            {"metric": "kills_per_match",
             "label": "kills per match 9.50 vs 6.20 tournament avg",
             "team_value": 9.5, "tournament_avg": 6.2, "delta_pct": 53.2},
        ],
        "weaknesses": [
            {"metric": "rotations_per_match",
             "label": "rotations per match 2.10 vs 3.40 tournament avg",
             "team_value": 2.1, "tournament_avg": 3.4, "delta_pct": -38.2},
        ],
        "comparison_note": "Compared against the tournament average across 12 team(s).",
        "tendencies": {
            "drop_locations": [
                {"location": "Pochinki", "matches": 4},
                {"location": "Georgopol", "matches": 2},
            ],
            "drop_location_note": "Drop proxy: location_label of each match's first rotation.",
            "rotation_timing": {"early": 6, "mid": 3, "late": 1, "unknown": 0,
                                "label": "early rotator",
                                "note": "6/10 rotations in the early third."},
            "centrality": {"label": "center-leaning",
                           "note": "classified against the median team distance."},
        },
        "head_to_head": [
            {"opponent_team_id": "t2", "opponent_name": "Team Bravo",
             "fights": 5, "won": 3, "lost": 1, "disengaged": 1,
             "kills_for": 18, "kills_against": 11},
            {"opponent_team_id": "t3", "opponent_name": "Team Charlie",
             "fights": 3, "won": 1, "lost": 2, "disengaged": 0,
             "kills_for": 9, "kills_against": 12},
        ],
        "notes": [],
    }


def _thin_dossier() -> dict:
    return {
        "team_id": "t9",
        "team_name": "Team Rookie",
        "computed_at": "2026-10-18T13:00:00+00:00",
        "data_freshness": "insufficient_data",
        "matches_sampled": 0,
        "fights_sampled": 0,
        "playstyle": {
            "label": "insufficient_data",
            "evidence": "no fights and no aggression_score recorded",
            "fights_per_match": None,
            "aggression_score": None,
            "matches_sampled": 0,
        },
        "strengths": [],
        "weaknesses": [],
        "comparison_note": "Insufficient data for strengths/weaknesses.",
        "tendencies": {
            "drop_locations": [],
            "drop_location_note": "No rotation events recorded — drop locations unknown.",
            "rotation_timing": {"label": "unknown", "note": "No rotation events recorded."},
            "centrality": {"label": "unknown",
                           "note": "Only 0 fight(s) with coordinates — centrality unknown."},
        },
        "head_to_head": [],
        "notes": ["Insufficient data: no fights recorded for this team yet."],
    }


def _assert_valid_pdf(blob: bytes) -> None:
    assert isinstance(blob, bytes)
    assert blob[:4] == b"%PDF", "missing %PDF magic header"
    assert len(blob) > 2048, f"PDF suspiciously small: {len(blob)} bytes"
    assert blob.rstrip().endswith(b"%%EOF"), "PDF missing %%EOF trailer"


# ── match report PDFs ───────────────────────────────────────────────


def test_match_report_pdf_rich():
    blob = match_report_pdf(_rich_match_report())
    _assert_valid_pdf(blob)
    assert b"AEGIS AI" in blob
    assert b"Team Alpha" in blob
    assert b"Match Summary" in blob
    assert b"Key Fights" in blob
    assert b"Rotation Highlights" in blob


def test_match_report_pdf_empty_is_honest():
    blob = match_report_pdf(_empty_match_report())
    _assert_valid_pdf(blob)
    assert b"No fight events recorded for this match." in blob
    assert b"No team summaries available" in blob


# ── team dossier PDFs ───────────────────────────────────────────────


def test_team_dossier_pdf_rich():
    blob = team_dossier_pdf("Team Alpha", _rich_dossier())
    _assert_valid_pdf(blob)
    assert b"Team Alpha" in blob
    assert b"AGGRESSIVE" in blob
    assert b"2.8 fights/match" in blob
    assert b"Head-to-Head" in blob
    assert b"Team Bravo" in blob


def test_team_dossier_pdf_thin_is_honest():
    blob = team_dossier_pdf("Team Rookie", _thin_dossier())
    _assert_valid_pdf(blob)
    assert b"INSUFFICIENT DATA" in blob
    assert b"Insufficient data" in blob


def test_team_dossier_pdf_unicode_names_do_not_crash():
    d = _thin_dossier()
    blob = team_dossier_pdf("टीम Alpha \u00e9", d)  # non-latin-1 chars degrade, never crash
    _assert_valid_pdf(blob)


# ── pure service helpers ────────────────────────────────────────────


@pytest.mark.parametrize(
    "aggr,fpm,median,expected",
    [
        (0.80, 3.0, 1.5, "aggressive"),
        (0.20, 0.4, 1.5, "passive"),
        (0.50, 1.5, 1.5, "balanced"),
        (None, None, None, "insufficient_data"),
        (0.80, 0.4, 1.5, "balanced"),  # conflicting signals -> balanced
    ],
)
def test_classify_playstyle(aggr, fpm, median, expected):
    label, evidence = classify_playstyle(aggr, fpm, median)
    assert label == expected
    if expected == "insufficient_data":
        assert "no fights" in evidence
    else:
        assert evidence  # evidence string always present


def test_classify_playstyle_evidence_shows_numbers():
    label, evidence = classify_playstyle(0.72, 2.8, 1.5)
    assert label == "aggressive"
    assert "2.8 fights/match" in evidence
    assert "0.72" in evidence


# ── router wiring ───────────────────────────────────────────────────


def test_router_exposes_expected_routes():
    from app.dossiers.router import router

    paths = sorted({r.path for r in router.routes})
    assert "/dossiers/fights" in paths
    assert "/dossiers/dossier" in paths
    assert "/dossiers/report/match/{match_id}.pdf" in paths
    assert "/dossiers/report/team/{team_id}.pdf" in paths
    assert "/dossiers/ingest/fight" in paths
