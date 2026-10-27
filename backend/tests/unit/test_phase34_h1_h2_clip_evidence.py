# backend/tests/unit/test_phase34_h1_h2_clip_evidence.py
"""
Phase 34 (H-1 + H-2): clip_detector evidence gates.

H-1: ``_evidence_weapon`` must never turn a ``weapon_family`` id
(e.g. "ar_a") into a display gun name — at any nesting depth. The
verification report's probe was ``{"weapon": {"weapon_family":
"ar_a"}}`` rendering "ar_a" as a gun; after the fix that shape is
NO evidence.

H-2: the four remaining clip creators (grenade / headshot / clutch /
squad-wipe) had invented defaults (victims=1, headshots=2,
enemy_count=4, time_span=15) that rendered made-up numbers. They now
follow the Phase 33 D-2 pattern: evidence-or-absent rendering on the
shared numeric evidence helpers, no default numbers, and NO
excitement contribution from a missing value.

Evidence-helper contract (shared by all four creators, mirroring
``_evidence_distance``): a value counts only when it parses from a
real number or a plain numeric string to a finite value in range;
bools, non-numeric strings, dicts/lists, NaN/inf and missing keys are
NO evidence. Counts must additionally be whole numbers >= their
floor. A plain numeric string that parses to an integral value
(e.g. "3") therefore counts as evidence — the same rule
``_evidence_distance`` has always applied to "320".
"""

import re
from types import SimpleNamespace

import pytest


@pytest.fixture
def detector():
    from app.agents.base_agent import AgentConfig
    from app.agents.highlight_agents.clip_detector import ClipDetector
    from app.core.event_bus import EventBus

    return ClipDetector(AgentConfig(agent_id="test-phase34"), EventBus())


def _clip(detector, event_type, data):
    clip = detector.detect_clip(event_type, data, {"match_id": "m1"})
    assert clip is not None
    return clip


def _rendered(clip):
    return f"{clip.title} {clip.description}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H-1: _evidence_weapon adversarial matrix
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"weapon": "AWM"}, "AWM"),
        ({"weapon_name": "M24"}, "M24"),
        ({"weapon_class": "sniper"}, "sniper"),
        # Verbatim + stripped, no class<->gun mapping asserted.
        ({"weapon": "  Kar98K  "}, "Kar98K"),
        ({"weapon": "AWM", "weapon_name": "M24"}, "AWM"),
        ({"weapon": None, "weapon_name": "M24"}, "M24"),
        # Dict unwrap: name/label/weapon/weapon_class win over family.
        ({"weapon": {"name": "AWM", "weapon_family": "ar_a"}}, "AWM"),
        ({"weapon": {"label": "Kar98K", "weapon_family": "ar_a"}}, "Kar98K"),
        (
            {"weapon": {"weapon_class": "sniper", "weapon_family": "ar_a"}},
            "sniper",
        ),
        ({"weapon": {"name": None, "label": "M24"}}, "M24"),
        ({"weapon": {"weapon": {"name": "AWM"}}}, "AWM"),
        # Object with a weapon string attribute.
        ({"weapon": SimpleNamespace(weapon="AWM")}, "AWM"),
        ({"weapon": SimpleNamespace(weapon={"name": "M24"})}, "M24"),
    ],
)
def test_evidence_weapon_evidenced_shapes(data, expected):
    from app.agents.highlight_agents.clip_detector import ClipDetector

    assert ClipDetector._evidence_weapon(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        # Report probe, verbatim: nested family id alone.
        {"weapon": {"weapon_family": "ar_a"}},
        # Family id alone, every nesting / key shape.
        {"weapon_family": "ar_a"},
        {"weapon_name": {"weapon_family": "ar_a"}},
        {"weapon_class": {"weapon_family": "ar_a"}},
        {"weapon": {"weapon": {"weapon_family": "ar_a"}}},
        {"weapon": {"label": {"weapon_family": "ar_a"}}},
        {"weapon": {"name": {"weapon_family": "ar_a"}}},
        {"weapon": {"weapon_class": {"weapon_family": "ar_a"}}},
        {"weapon": {"weapon_family": "ar_a", "other": "AWM"}},
        # Dict with no name/label/weapon/weapon_class at all.
        {"weapon": {}},
        {"weapon": {"foo": "AWM"}},
        {"weapon": {"family": "ar_a"}},
        # Missing / empty / sentinel values.
        {},
        {"weapon": None},
        {"weapon": ""},
        {"weapon": "   "},
        {"weapon": "Unknown"},
        {"weapon": "UNKNOWN"},
        {"weapon": "None"},
        {"weapon": "null"},
        {"weapon": "n/a"},
        {"weapon": "unknown_awm"},
        {"weapon": "unknown_sniper"},
        {"weapon": {"name": "unknown_awm"}},
        {"weapon": {"name": "Unknown"}},
        {"weapon": {"name": "", "label": "M24"}},
        # Non-strings: never guessed.
        {"weapon": 123},
        {"weapon": 3.5},
        {"weapon": True},
        {"weapon": ["AWM"]},
        {"weapon": SimpleNamespace()},
        {"weapon": SimpleNamespace(weapon=123)},
        {"weapon": SimpleNamespace(weapon={"weapon_family": "ar_a"})},
        {"weapon": SimpleNamespace(weapon={"name": {"weapon_family": "x"}})},
        {"weapon": SimpleNamespace(weapon="unknown_awm")},
    ],
)
def test_evidence_weapon_family_and_junk_are_no_evidence(data):
    from app.agents.highlight_agents.clip_detector import ClipDetector

    assert ClipDetector._evidence_weapon(data) is None


def test_h1_report_probe_never_renders_family_id(detector):
    # The exact probe from the verification report, through the
    # long-range renderer that used to say "hits 320m ar_a shot!".
    clip = _clip(
        detector,
        "long_range_kill",
        {"player_name": "Hero", "weapon": {"weapon_family": "ar_a"}},
    )
    assert "ar_a" not in _rendered(clip)
    assert "long-range shot" in clip.title


def test_h1_nested_family_with_distance_renders_distanceless_gun(detector):
    clip = _clip(
        detector,
        "long_range_kill",
        {
            "player_name": "Hero",
            "distance": 320,
            "weapon": {"weapon_family": "ar_a"},
        },
    )
    assert "ar_a" not in _rendered(clip)
    assert "320m shot" in clip.title


def test_h1_weapon_class_evidence_renders(detector):
    clip = _clip(
        detector,
        "long_range_kill",
        {"player_name": "Hero", "distance": 250, "weapon_class": "sniper"},
    )
    assert "250m sniper" in clip.title
    assert "#Sniper" in clip.hashtags


def test_h1_dict_under_weapon_class_with_only_family_is_no_evidence(detector):
    clip = _clip(
        detector,
        "long_range_kill",
        {
            "player_name": "Hero",
            "weapon_class": {"weapon_family": "ar_a"},
        },
    )
    assert "ar_a" not in _rendered(clip)
    assert not re.search(r"\d", _rendered(clip))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H-2: grenade clips
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_grenade_fully_evidenced(detector):
    clip = _clip(
        detector,
        "grenade_kill",
        {"player_name": "Hero", "victims": 3, "distance": 80},
    )
    assert "3-man grenade" in clip.title
    assert "takes out 3 players" in clip.description
    # 45 + 3*15 + 10 (distance > 50) = 100.
    assert clip.excitement_score == 100
    assert clip.needs_slow_motion is True


def test_grenade_victims_only(detector):
    clip = _clip(
        detector, "grenade_kill", {"player_name": "Hero", "victims": 2}
    )
    assert "2-man grenade" in clip.title
    assert clip.excitement_score == 45 + 2 * 15
    assert clip.needs_slow_motion is False


def test_grenade_single_victim_is_evidenced(detector):
    # One victim is a real count (the live frag path passes exactly
    # this for a single frag kill/knock); it is not a default.
    clip = _clip(
        detector, "grenade_kill", {"player_name": "Hero", "victims": 1}
    )
    assert "1-man grenade" in clip.title
    assert clip.excitement_score == 60


def test_grenade_distance_only_no_count_rendered(detector):
    clip = _clip(
        detector, "grenade_kill", {"player_name": "Hero", "distance": 80}
    )
    assert "with the grenade" in clip.title
    assert "-man" not in _rendered(clip)
    # No victims contribution; distance > 50 contributes.
    assert clip.excitement_score == 55
    assert clip.needs_slow_motion is False


def test_grenade_fully_absent_no_default_number(detector):
    clip = _clip(detector, "grenade_kill", {"player_name": "Hero"})
    assert clip.title == "KOBE! Hero with the grenade!"
    assert not re.search(r"\d", _rendered(clip)), _rendered(clip)
    assert clip.excitement_score == 45
    assert clip.needs_slow_motion is False


@pytest.mark.parametrize(
    "junk_victims",
    [None, 0, -1, True, False, float("nan"), float("inf"), "three", "2.5", 2.5, {}, []],
)
def test_grenade_junk_victims_are_no_evidence(detector, junk_victims):
    clip = _clip(
        detector,
        "grenade_kill",
        {"player_name": "Hero", "victims": junk_victims},
    )
    assert "-man" not in _rendered(clip)
    assert clip.excitement_score == 45
    assert clip.needs_slow_motion is False


@pytest.mark.parametrize("victims", [3, 3.0, "3", "3.0"])
def test_grenade_numeric_string_and_integral_float_counts(detector, victims):
    clip = _clip(
        detector, "grenade_kill", {"player_name": "Hero", "victims": victims}
    )
    assert "3-man grenade" in clip.title
    assert clip.excitement_score == 90


@pytest.mark.parametrize(
    "junk_distance",
    [0, -10, True, float("nan"), float("inf"), "far", "80m", {}, []],
)
def test_grenade_junk_distance_adds_nothing(detector, junk_distance):
    clip = _clip(
        detector,
        "grenade_kill",
        {"player_name": "Hero", "victims": 2, "distance": junk_distance},
    )
    assert clip.excitement_score == 75


def test_grenade_distance_boundary_and_string(detector):
    clip = _clip(
        detector,
        "grenade_kill",
        {"player_name": "Hero", "victims": 2, "distance": 50},
    )
    assert clip.excitement_score == 75  # > 50 only
    clip = _clip(
        detector,
        "grenade_kill",
        {"player_name": "Hero", "victims": 2, "distance": "80"},
    )
    assert clip.excitement_score == 85
    clip = _clip(
        detector,
        "grenade_kill",
        {"player_name": "Hero", "victims": 2, "distance": 51},
    )
    assert clip.excitement_score == 85


def test_grenade_live_frag_path_still_overwrites_title(detector):
    # check_weapon_highlight passes victims=1 (one evidenced frag
    # event) and distance=0 (no evidence); the clip it builds is
    # re-titled, and its excitement carries no distance contribution.
    clip = detector.check_weapon_highlight(
        {
            "killer": "Hero",
            "victim": "Villain",
            "weapon": "frag_grenade",
            "isKnock": False,
        },
        {},
        now=1000.0,
    )
    assert clip is not None
    assert clip.title.startswith("FRAG OUT!")
    assert "1-man" not in clip.title
    assert clip.excitement_score == 60
    assert clip.needs_slow_motion is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H-2: headshot clips
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_headshot_fully_evidenced(detector):
    clip = _clip(
        detector, "headshot_chain", {"player_name": "Hero", "headshots": 3}
    )
    assert "3 headshots" in clip.title
    assert "3 perfect one-taps" in clip.description
    assert clip.excitement_score == 40 + 3 * 15
    assert clip.quality.value == "excellent"


def test_headshot_single_count_renders(detector):
    clip = _clip(
        detector, "headshot_chain", {"player_name": "Hero", "headshots": 1}
    )
    assert "1 headshots" in clip.title
    assert clip.excitement_score == 55


def test_headshot_fully_absent_is_countless(detector):
    clip = _clip(detector, "headshot_chain", {"player_name": "Hero"})
    assert clip.title == "ONE TAPS! Hero!"
    assert not re.search(r"\d", _rendered(clip)), _rendered(clip)
    assert clip.excitement_score == 40


@pytest.mark.parametrize(
    "junk",
    [None, 0, -2, True, float("nan"), float("inf"), "two", "2.5", 2.5, {}, []],
)
def test_headshot_junk_counts_are_no_evidence(detector, junk):
    clip = _clip(
        detector, "headshot_chain", {"player_name": "Hero", "headshots": junk}
    )
    assert clip.title == "ONE TAPS! Hero!"
    assert clip.excitement_score == 40


@pytest.mark.parametrize("headshots", [2, 2.0, "2"])
def test_headshot_numeric_string_and_integral_float_counts(detector, headshots):
    clip = _clip(
        detector, "headshot_chain", {"player_name": "Hero", "headshots": headshots}
    )
    assert "2 headshots" in clip.title
    assert clip.excitement_score == 70


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H-2: clutch clips
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_clutch_fully_evidenced(detector):
    clip = _clip(
        detector,
        "clutch_win",
        {"player_name": "Hero", "enemy_count": 4, "win_probability": 0.05},
    )
    assert "1v4" in clip.title
    assert "defies 5% odds" in clip.description
    assert "#1v4" in clip.hashtags
    # 80 + 15 (>= 4 enemies) + 5 (< 0.1 odds) = 100.
    assert clip.excitement_score == 100


def test_clutch_count_only_no_odds(detector):
    clip = _clip(
        detector, "clutch_win", {"player_name": "Hero", "enemy_count": 4}
    )
    assert "1v4" in clip.title
    assert "%" not in _rendered(clip)
    assert "#1v4" in clip.hashtags
    assert clip.excitement_score == 95


def test_clutch_odds_only_no_count(detector):
    clip = _clip(
        detector, "clutch_win", {"player_name": "Hero", "win_probability": 0.05}
    )
    assert "1v" not in _rendered(clip)
    assert "defies 5% odds" in clip.description
    assert "#1v4" not in clip.hashtags
    assert clip.excitement_score == 85


def test_clutch_fully_absent_no_invented_1v4(detector):
    clip = _clip(detector, "clutch_win", {"player_name": "Hero"})
    assert clip.title == "IMPOSSIBLE! Hero wins the Clutch!"
    assert "1v" not in _rendered(clip)
    assert "%" not in _rendered(clip)
    assert not re.search(r"\d", _rendered(clip)), _rendered(clip)
    assert clip.hashtags == ["#Clutch", "#BGMI", "#Impossible"]
    assert clip.excitement_score == 80


@pytest.mark.parametrize("enemies", [2, 3, 5])
def test_clutch_other_counts_render_without_1v4_hashtag(detector, enemies):
    clip = _clip(
        detector, "clutch_win", {"player_name": "Hero", "enemy_count": enemies}
    )
    assert f"1v{enemies}" in clip.title
    assert "#1v4" not in clip.hashtags
    expected = 80 + (15 if enemies >= 4 else 0)
    assert clip.excitement_score == expected


@pytest.mark.parametrize(
    "junk_enemies", [None, 0, 1, -1, True, float("nan"), "four", "4.5", 4.5, {}, []]
)
def test_clutch_junk_and_1v1_counts_are_no_evidence(detector, junk_enemies):
    # 1 is below the clutch floor (>= 2): a 1v1 is not a clutch claim.
    clip = _clip(
        detector,
        "clutch_win",
        {"player_name": "Hero", "enemy_count": junk_enemies},
    )
    assert "1v" not in _rendered(clip)
    assert "#1v4" not in clip.hashtags
    assert clip.excitement_score == 80


def test_clutch_numeric_string_count_counts(detector):
    clip = _clip(
        detector, "clutch_win", {"player_name": "Hero", "enemy_count": "4"}
    )
    assert "1v4" in clip.title
    assert "#1v4" in clip.hashtags


@pytest.mark.parametrize(
    "junk_prob",
    [None, 1.0, 1.5, -0.1, True, float("nan"), float("inf"), "low", "0.05x", {}, []],
)
def test_clutch_junk_probabilities_are_no_evidence(detector, junk_prob):
    clip = _clip(
        detector,
        "clutch_win",
        {"player_name": "Hero", "win_probability": junk_prob},
    )
    assert "%" not in _rendered(clip)
    assert clip.excitement_score == 80


def test_clutch_probability_boundaries(detector):
    clip = _clip(
        detector, "clutch_win", {"player_name": "Hero", "win_probability": 0}
    )
    assert "defies 0% odds" in clip.description
    assert clip.excitement_score == 85
    clip = _clip(
        detector, "clutch_win", {"player_name": "Hero", "win_probability": 0.1}
    )
    assert "defies 10% odds" in clip.description
    assert clip.excitement_score == 80  # < 0.1 only
    clip = _clip(
        detector, "clutch_win", {"player_name": "Hero", "win_probability": "0.05"}
    )
    assert "defies 5% odds" in clip.description
    assert clip.excitement_score == 85


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H-2: squad-wipe clips
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_squad_wipe_fully_evidenced(detector):
    clip = _clip(
        detector,
        "squad_wipe",
        {
            "team_name": "Rivals",
            "eliminator": "Hero",
            "time_span": 8,
            "placement": 2,
        },
    )
    assert "in 8.0s at #2 place" in clip.description
    assert clip.duration_seconds == 16  # evidenced time_span + 8
    # 65 + 25 (placement <= 3) + 10 (time < 10) = 100.
    assert clip.excitement_score == 100


def test_squad_wipe_time_only(detector):
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "time_span": 12},
    )
    assert "in 12.0s" in clip.description
    assert "#" not in clip.description
    assert clip.duration_seconds == 20
    assert clip.excitement_score == 65


def test_squad_wipe_placement_only(detector):
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "placement": 1},
    )
    assert "at #1 place" in clip.description
    assert not re.search(r"\d+\.\d+s", clip.description)
    assert clip.duration_seconds == 8.0  # neutral window, not a default span
    assert clip.excitement_score == 90


def test_squad_wipe_fully_absent_no_default_numbers(detector):
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero"},
    )
    assert clip.description == "Hero wipes out Rivals!"
    assert not re.search(r"\d", _rendered(clip)), _rendered(clip)
    # Neutral fixed window — never 15 + 8 = 23 from the old default.
    assert clip.duration_seconds == 8.0
    assert clip.excitement_score == 65


@pytest.mark.parametrize(
    "junk_time",
    [None, 0, -5, True, float("nan"), float("inf"), "fast", "12s", {}, []],
)
def test_squad_wipe_junk_time_span_is_no_evidence(detector, junk_time):
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "time_span": junk_time},
    )
    assert not re.search(r"\d+\.\d+s", clip.description)
    assert clip.duration_seconds == 8.0
    assert clip.excitement_score == 65


def test_squad_wipe_numeric_string_time_span_counts(detector):
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "time_span": "12"},
    )
    assert "in 12.0s" in clip.description
    assert clip.duration_seconds == 20


def test_squad_wipe_time_boundary(detector):
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "time_span": 10},
    )
    assert clip.excitement_score == 65  # < 10 only
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "time_span": 8.5},
    )
    assert "in 8.5s" in clip.description
    assert clip.duration_seconds == 16.5
    assert clip.excitement_score == 75


@pytest.mark.parametrize(
    "junk_placement",
    [None, 0, -1, True, float("nan"), "third", "3.5", 3.5, {}, []],
)
def test_squad_wipe_junk_placement_is_no_evidence(detector, junk_placement):
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "placement": junk_placement},
    )
    assert "#" not in clip.description
    assert clip.excitement_score == 65


def test_squad_wipe_placement_values(detector):
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "placement": 16},
    )
    assert "at #16 place" in clip.description
    assert clip.excitement_score == 65
    clip = _clip(
        detector,
        "squad_wipe",
        {"team_name": "Rivals", "eliminator": "Hero", "placement": "2"},
    )
    assert "at #2 place" in clip.description
    assert clip.excitement_score == 90


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Excitement: junk never contributes (all four creators)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_junk_everywhere_excitement_stays_at_base(detector):
    grenade = _clip(
        detector,
        "grenade_kill",
        {"player_name": "Hero", "victims": "lots", "distance": "far"},
    )
    assert grenade.excitement_score == 45
    headshot = _clip(
        detector, "headshot_chain", {"player_name": "Hero", "headshots": "many"}
    )
    assert headshot.excitement_score == 40
    clutch = _clip(
        detector,
        "clutch_win",
        {
            "player_name": "Hero",
            "enemy_count": "lots",
            "win_probability": "high",
        },
    )
    assert clutch.excitement_score == 80
    wipe = _clip(
        detector,
        "squad_wipe",
        {
            "team_name": "Rivals",
            "eliminator": "Hero",
            "time_span": "ages",
            "placement": "last",
        },
    )
    assert wipe.excitement_score == 65
    assert wipe.duration_seconds == 8.0
