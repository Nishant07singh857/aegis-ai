# backend/tests/unit/test_phase33_d2_clip_detector.py
"""
Phase 33 Worker 1 (D-2): clip_detector long-range evidence fix.

Regression pins for the invented-default defect: _create_long_range_clip
used to default to weapon="Sniper", distance=200, and the live
commentary path (handle_event -> detect_clip("long_range_kill"))
built its event WITHOUT weapon/distance — so every such clip shipped
an unevidenced "200m Sniper shot!" to viewers.

After the fix, weapon/distance come ONLY from evidence passed into
the event (kill-feed IconMatch weapon / measured distance). With no
evidence the clip renders gun-less / distance-less: no weapon name,
no distance number, no #Sniper hashtag, and no distance contribution
to the excitement score.
"""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def detector():
    from app.agents.base_agent import AgentConfig
    from app.agents.highlight_agents.clip_detector import ClipDetector
    from app.core.event_bus import EventBus

    return ClipDetector(AgentConfig(agent_id="test-d2"), EventBus())


def _long_range(detector, data):
    clip = detector.detect_clip("long_range_kill", data, {"match_id": "m1"})
    assert clip is not None
    assert clip.category.value == "long_range"
    return clip


def _rendered(clip):
    return f"{clip.title} {clip.description}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# No evidence -> gun-less / distance-less, never the old defaults
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_no_evidence_renders_without_weapon_or_distance(detector):
    clip = _long_range(detector, {"player_name": "Hero"})
    rendered = _rendered(clip)
    assert "Sniper" not in rendered and "sniper" not in rendered
    assert "200" not in rendered
    assert not re.search(r"\d", rendered), rendered
    assert "#Sniper" not in clip.hashtags
    # No invented distance means no distance contribution to excitement.
    assert clip.excitement_score == 40


def test_empty_event_renders_without_weapon_or_distance(detector):
    # Exactly what the old commentary path produced: no weapon, no
    # distance keys at all (player falls back to "Unknown", as before).
    clip = _long_range(detector, {})
    rendered = _rendered(clip)
    assert "Sniper" not in rendered and "200" not in rendered
    assert not re.search(r"\d", rendered), rendered
    assert "#Sniper" not in clip.hashtags


@pytest.mark.parametrize(
    "junk_weapon",
    [None, "", "   ", "Unknown", "UNKNOWN", "None", "unknown_awm", "unknown"],
)
def test_junk_weapon_values_are_not_rendered(detector, junk_weapon):
    clip = _long_range(detector, {"player_name": "Hero", "weapon": junk_weapon})
    rendered = _rendered(clip)
    assert "Unknown" not in rendered and "None" not in rendered
    assert "unknown" not in rendered.lower()
    assert "#Sniper" not in clip.hashtags


@pytest.mark.parametrize(
    "junk_distance",
    [None, 0, -50, True, float("nan"), float("inf"), "far", "320m", {}, []],
)
def test_junk_distance_values_are_not_rendered(detector, junk_distance):
    clip = _long_range(
        detector, {"player_name": "Hero", "distance": junk_distance}
    )
    rendered = _rendered(clip)
    assert not re.search(r"\d", rendered), rendered
    assert clip.excitement_score == 40


def test_weapon_family_id_alone_is_not_rendered_as_a_gun(detector):
    # "ar_a" is an internal family id, not a display gun name.
    clip = _long_range(detector, {"player_name": "Hero", "weapon_family": "ar_a"})
    assert "ar_a" not in _rendered(clip)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Real evidence -> correct weapon / distance shown
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_full_evidence_renders_weapon_and_distance(detector):
    clip = _long_range(
        detector,
        {
            "player_name": "ClutchGod",
            "distance": 320,
            "weapon": "AWM",
            "headshot": True,
            "timestamp_ms": 1500000,
        },
    )
    assert "320m AWM" in clip.title
    assert "320 meter headshot with AWM" in clip.description
    # 40 + min(40, 32) + 15 = 87 -> EXCELLENT (same as the old e2e case).
    assert clip.excitement_score == 87
    assert clip.quality.value == "excellent"
    # A specific gun earns no class hashtag (no gun->class map asserted).
    assert "#Sniper" not in clip.hashtags
    assert clip.hashtags == ["#LongRange", "#BGMI"]


def test_distance_only_renders_distance_without_gun(detector):
    clip = _long_range(detector, {"player_name": "Hero", "distance": 275})
    assert "275m shot" in clip.title
    assert "275 meter kill" in clip.description
    assert "Sniper" not in _rendered(clip)
    assert clip.excitement_score == 40 + 27.5


def test_weapon_only_renders_weapon_without_number(detector):
    clip = _long_range(detector, {"player_name": "Hero", "weapon": "Kar98K"})
    assert "Kar98K" in clip.title
    # No distance NUMBER rendered (Kar98K's own digits are the gun's
    # name, not a distance claim): no "<digits>m" pattern anywhere.
    assert not re.search(r"\d+\s*m\b", _rendered(clip)), _rendered(clip)
    assert clip.excitement_score == 40


def test_sniper_class_evidence_earns_sniper_hashtag(detector):
    clip = _long_range(
        detector, {"player_name": "Hero", "weapon": "sniper", "distance": 250}
    )
    assert "250m sniper" in clip.title
    assert "#Sniper" in clip.hashtags


def test_distance_m_alias_and_dict_weapon_are_evidence(detector):
    clip = _long_range(
        detector,
        {
            "player_name": "Hero",
            "distance_m": 210.5,
            "weapon": {"name": "M24"},
        },
    )
    assert "210.5m M24" in clip.title


def test_non_bool_headshot_does_not_invent_a_headshot(detector):
    clip = _long_range(detector, {"player_name": "Hero", "headshot": "false"})
    assert "headshot" not in _rendered(clip)
    assert "kill" in clip.description


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Live commentary path (handle_event) cannot reach a default
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _commentary_record(data):
    return SimpleNamespace(
        event=SimpleNamespace(event_type="commentary.line", data=data)
    )


async def test_commentary_path_without_evidence_gets_gunless_rendering(
    detector, monkeypatch
):
    publish = AsyncMock()
    monkeypatch.setattr(detector.event_bus, "publish", publish)
    await detector.handle_event(
        _commentary_record(
            {
                "text": "What a shot!",
                "clip_event_type": "long_range_kill",
                "player_name": "Hero",
                "match_id": "m1",
            }
        )
    )
    assert publish.await_count == 1
    published = publish.await_args.kwargs["data"]
    assert "Sniper" not in published["title"] and "200" not in published["title"]
    assert not re.search(r"\d", published["title"])
    # The stored clip carries the same gun-less title.
    stored = next(iter(detector.clips.values()))
    assert stored.title == published["title"]


async def test_commentary_path_with_evidence_forwards_it(detector, monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(detector.event_bus, "publish", publish)
    await detector.handle_event(
        _commentary_record(
            {
                "text": "What a shot!",
                "clip_event_type": "long_range_kill",
                "player_name": "Hero",
                "match_id": "m1",
                "weapon": "AWM",
                "distance": 320,
            }
        )
    )
    assert publish.await_count == 1
    published = publish.await_args.kwargs["data"]
    assert "320m AWM" in published["title"]
