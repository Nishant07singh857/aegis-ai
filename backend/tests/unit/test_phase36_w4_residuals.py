# backend/tests/unit/test_phase36_w4_residuals.py
"""
Phase 36 (W4): Phase 34 residuals R-1..R-7 + the pre-existing
``_create_multi_kill_clip`` string-keys TypeError (R-8).

Every residual is pinned with an evidenced / absent / junk matrix,
and the two LIVE residuals (R-6, R-7) are pinned on the REAL replay
path — no mocks of the code under test:

* R-6: real kill events through ``ReplayService.get_highlights``
  (buffer -> HighlightDetector -> trigger_registry -> _sniper_triple)
  AND through ``trigger_registry(...).run(...)`` directly.
* R-7: real ``ReplayQueue.handle_candidate`` (the
  ``replay.candidate`` consumer), built the same way as the
  Phase 25 joint-contract test.

Shared contract (Phase 33 D-2 / Phase 34): a value counts only when
it parses from a real number or a plain numeric string to a finite
value in range; bools, non-numeric strings, dicts/lists, NaN/inf
and missing keys are NO evidence. Counts must additionally be whole
numbers >= their floor.
"""

import re
import time

import pytest

from app.services.replay.buffer import BufferedEvent


# ── fixtures / helpers (same shape as the Phase 34 suite) ─────────


@pytest.fixture
def detector():
    from app.agents.base_agent import AgentConfig
    from app.agents.highlight_agents.clip_detector import ClipDetector
    from app.core.event_bus import EventBus

    return ClipDetector(AgentConfig(agent_id="test-phase36-w4"), EventBus())


def _clip(detector, event_type, data):
    clip = detector.detect_clip(event_type, data, {"match_id": "m1"})
    assert clip is not None
    return clip


def _rendered(clip):
    return f"{clip.title} {clip.description}"


def _kill(event_id, ts, killer, weapon=None, victim="V1"):
    payload = {"killer": killer, "killerTeam": "T1", "victim": victim}
    if weapon is not None:
        payload["weapon"] = weapon
    return BufferedEvent(
        event_id=event_id, ts=ts, type="kill_event", payload=payload
    )


def _sniper_events(weapons=("sniper", "sniper", "sniper"), killer="Snip"):
    now = time.time()
    return [
        _kill(f"s{i}", now - 20.0 + i * 5.0, killer, weapon=w, victim=f"V{i}")
        for i, w in enumerate(weapons)
    ]


def _bare_queue(tmp_path):
    """A real ReplayQueue, no bus — same construction as the Phase 25
    joint-contract test's ``_bare_queue``."""
    from app.services.replay.queue import ReplayQueue
    from app.services.replay.renderer import HighlightRenderer
    from app.services.replay.service import ReplayService

    renderer = HighlightRenderer(
        highlights_dir=tmp_path / "highlights",
        recordings_dir=tmp_path / "recordings",
    )
    svc = ReplayService(window_seconds=3600, renderer=renderer)
    return ReplayQueue(
        svc,
        recorder=None,
        director_provider=lambda: {"mode": "manual", "active_camera": 1},
        phase_provider=lambda: "mid_game",
        fights_provider=lambda: [],
    )


def _candidate(**over):
    payload = {
        "kind": "fight",
        "window_start": 1000.0,
        "window_end": 1010.0,
        "score": 90,
        "moment_id": "m-1",
    }
    payload.update(over)
    return payload


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R-1: _create_comeback_clip — from_position=8 default
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_r1_evidenced_position_renders_and_scores(detector):
    clip = _clip(
        detector, "comeback_win", {"team_name": "Soul", "from_position": 8}
    )
    assert "rises from #8!" in clip.title
    assert clip.excitement_score == 60 + 8 * 3  # 84, as before — now real
    assert clip.quality.value == "excellent"


def test_r1_small_evidenced_position(detector):
    clip = _clip(
        detector, "comeback_win", {"team_name": "Soul", "from_position": 2}
    )
    assert "rises from #2!" in clip.title
    assert clip.excitement_score == 66


@pytest.mark.parametrize("raw", ["8", 8.0])
def test_r1_numeric_string_and_integral_float_are_evidence(detector, raw):
    clip = _clip(
        detector, "comeback_win", {"team_name": "Soul", "from_position": raw}
    )
    assert "rises from #8!" in clip.title
    assert clip.excitement_score == 84


@pytest.mark.parametrize(
    "data",
    [
        {"team_name": "Soul"},  # absent — the old default-8 shape
        {"team_name": "Soul", "from_position": None},  # explicit None
        {"team_name": "Soul", "from_position": 0},
        {"team_name": "Soul", "from_position": 1},  # #1 is no comeback
        {"team_name": "Soul", "from_position": -3},
        {"team_name": "Soul", "from_position": 2.5},
        {"team_name": "Soul", "from_position": "eighth"},
        {"team_name": "Soul", "from_position": True},
        {"team_name": "Soul", "from_position": float("nan")},
        {"team_name": "Soul", "from_position": float("inf")},
        {"team_name": "Soul", "from_position": {}},
        {"team_name": "Soul", "from_position": []},
    ],
)
def test_r1_no_evidence_renders_position_less_at_base(detector, data):
    clip = _clip(detector, "comeback_win", data)
    assert "rises from #" not in clip.title
    assert re.search(r"#\d", clip.title) is None
    assert clip.title == "THE COMEBACK! Soul!"
    assert clip.excitement_score == 60  # zero contribution, no crash


def test_r1_none_from_position_does_not_raise(detector):
    # The old ``60 + None * 3`` raised TypeError (masked by
    # handle_event's bare except). Direct call must not raise.
    clip = detector._create_comeback_clip(
        {"team_name": "Soul", "from_position": None}, {"match_id": "m1"}
    )
    assert clip.excitement_score == 60


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R-2: _create_final_circle_clip — teams_alive=2 default
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.parametrize(
    "teams_alive,expected_excitement",
    [
        (2, 90),  # evidenced last-2: the +20 is a real signal now
        (1, 90),  # evidenced last-1: decided, still <= 2
        ("2", 90),  # plain numeric string counts (shared contract)
        (3, 70),
        (5, 70),
    ],
)
def test_r2_evidenced_teams_alive(detector, teams_alive, expected_excitement):
    clip = _clip(
        detector,
        "final_circle_moment",
        {"winner_team": "Soul", "teams_alive": teams_alive},
    )
    assert clip.excitement_score == expected_excitement
    assert clip.quality.value == (
        "legendary" if expected_excitement == 90 else "good"
    )


@pytest.mark.parametrize(
    "data",
    [
        {"winner_team": "Soul"},  # absent — the old default-2 shape
        {"winner_team": "Soul", "teams_alive": None},  # explicit None
        {"winner_team": "Soul", "teams_alive": 0},
        {"winner_team": "Soul", "teams_alive": -1},
        {"winner_team": "Soul", "teams_alive": 2.5},
        {"winner_team": "Soul", "teams_alive": "two"},
        {"winner_team": "Soul", "teams_alive": True},
        {"winner_team": "Soul", "teams_alive": float("nan")},
        {"winner_team": "Soul", "teams_alive": {}},
    ],
)
def test_r2_no_evidence_scores_base_70_not_legendary(detector, data):
    clip = _clip(detector, "final_circle_moment", data)
    assert clip.excitement_score == 70  # was 90/LEGENDARY from the default
    assert clip.quality.value == "good"


def test_r2_none_teams_alive_does_not_raise(detector):
    # The old ``None <= 2`` raised TypeError on direct call.
    clip = detector._create_final_circle_clip(
        {"winner_team": "Soul", "teams_alive": None}, {"match_id": "m1"}
    )
    assert clip.excitement_score == 70


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R-3: _evidence_weapon allowlist — no family id renders
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.parametrize(
    "family",
    [
        "ar_a",
        "ar_b",
        "smg_compact",
        "sniper_bolt",
        "dmr_long",
        "frag_grenade",  # underscore spelling is the family-id spelling
        "pistol_t",
    ],
)
def test_r3_every_family_id_is_no_evidence(family):
    from app.agents.highlight_agents.clip_detector import ClipDetector

    # Every key of FAMILY_TO_CLASS, as a plain top-level string and
    # nested under weapon — the R-3 hole (Phase 34 only blocked the
    # dict-unwrap path).
    assert ClipDetector._evidence_weapon({"weapon": family}) is None
    assert (
        ClipDetector._evidence_weapon({"weapon": {"name": family}}) is None
    )


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"weapon": "sniper"}, "sniper"),
        ({"weapon": "Sniper"}, "Sniper"),  # verbatim, case preserved
        ({"weapon": "AWM"}, "AWM"),  # pinned evidenced by Phase 34
        ({"weapon": "Kar98K"}, "Kar98K"),
        ({"weapon_name": "M24"}, "M24"),
        ({"weapon": "ar"}, "ar"),
        ({"weapon": "smg"}, "smg"),
        ({"weapon": "dmr"}, "dmr"),
        ({"weapon": "pistol"}, "pistol"),
        ({"weapon": "Frag Grenade"}, "Frag Grenade"),  # class display spelling
        ({"weapon": "frag"}, "frag"),
        ({"weapon": "grenade"}, "grenade"),
        ({"weapon": "sr"}, "sr"),
        ({"weapon": "mini14"}, "mini14"),
        ({"weapon": "sks"}, "sks"),
        ({"weapon": "mk14"}, "mk14"),
    ],
)
def test_r3_evidenced_vocabulary_still_renders(data, expected):
    from app.agents.highlight_agents.clip_detector import ClipDetector

    assert ClipDetector._evidence_weapon(data) == expected


@pytest.mark.parametrize(
    "weapon",
    [
        "M416",  # real gun model, but no producer vocabulary covers it
        "AKM",
        "UMP45",
        "shotgun",
        "lmg",
        "crossbow",
        "pan",
        "molotov",
        "vehicle",
        "ar_a ",
        " AR_A",
    ],
)
def test_r3_unlisted_strings_render_gun_less(weapon):
    from app.agents.highlight_agents.clip_detector import ClipDetector

    assert ClipDetector._evidence_weapon({"weapon": weapon}) is None


def test_r3_family_id_never_renders_in_long_range_clip(detector):
    clip = _clip(
        detector,
        "long_range_kill",
        {"player_name": "Hero", "weapon": "ar_a", "distance": 320},
    )
    assert "ar_a" not in _rendered(clip)
    assert "hits 320m shot!" in clip.title  # distance real, gun absent
    clip = _clip(
        detector,
        "long_range_kill",
        {"player_name": "Hero", "weapon": "frag_grenade", "distance": 320},
    )
    assert "frag_grenade" not in _rendered(clip)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R-4: _create_funny_clip — caller text passthrough dropped
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_r4_caller_title_and_description_are_ignored(detector):
    clip = _clip(
        detector,
        "funny_moment",
        {
            "player_name": "Hero",
            "title": "BREAKING: ScoutOP retires from BGMI!!!",
            "description": "Insider sources confirm the team disbanded.",
        },
    )
    assert "retires" not in _rendered(clip)
    assert "disbanded" not in _rendered(clip)
    assert "Insider" not in _rendered(clip)
    assert clip.title == "Funny Moment! 😂"
    assert clip.description == "You have to see this!"
    assert clip.category.value == "funny_moment"
    assert clip.excitement_score == 35


def test_r4_empty_event_renders_the_same_fixed_text(detector):
    clip = _clip(detector, "funny_moment", {"player_name": "Hero"})
    assert clip.title == "Funny Moment! 😂"
    assert clip.description == "You have to see this!"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R-5: check_weapon_highlight — "(kill-feed icon verified)"
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_r5_frag_description_no_longer_claims_verification(detector):
    clip = detector.check_weapon_highlight(
        {"killer": "Hero", "victim": "Villain", "weapon": "frag_grenade",
         "isKnock": False},
        {},
        now=1000.0,
    )
    assert clip is not None
    assert "verified" not in clip.description.lower()
    assert "(kill-feed icon match)" in clip.description
    assert clip.title.startswith("FRAG OUT!")


def test_r5_sniper_triple_description_no_longer_claims_verification(detector):
    clip = None
    for i in range(3):
        clip = detector.check_weapon_highlight(
            {"killer": "Snip", "victim": f"V{i}", "weapon": "sniper",
             "isKnock": False},
            {},
            now=1000.0 + i * 5.0,
        )
    assert clip is not None
    assert "verified" not in clip.description.lower()
    assert "(kill-feed icon match per kill)" in clip.description
    assert "3 sniper-class eliminations" in clip.description


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R-6 (LIVE): _sniper_triple reason — no "long-range" claim
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_r6_live_path_reason_states_class_not_range(tmp_path):
    """Full live path: buffer -> ReplayService.get_highlights ->
    HighlightDetector -> trigger_registry -> _sniper_triple."""
    from app.services.replay.renderer import HighlightRenderer
    from app.services.replay.service import ReplayService

    renderer = HighlightRenderer(
        highlights_dir=tmp_path / "highlights",
        recordings_dir=tmp_path / "recordings",
    )
    svc = ReplayService(window_seconds=3600, renderer=renderer)
    for event in _sniper_events():
        svc.buffer.record(
            "kill_event", event.payload, ts=event.ts, event_id=event.event_id
        )

    highlights = svc.get_highlights(limit=10)
    triples = [h for h in highlights if h.kind == "sniper_triple"]
    assert len(triples) == 1
    moment = triples[0]
    assert moment.score == 85
    assert "3 sniper-class eliminations" in moment.reason
    assert "long-range" not in moment.reason.lower()
    assert "long range" not in moment.reason.lower()
    assert "320" not in moment.reason and "200" not in moment.reason
    assert moment.player == "Snip"


def test_r6_registry_path_reason_has_no_distance_claim():
    from app.services.replay.highlight_triggers import trigger_registry

    events = _sniper_events()
    trig = next(
        t for t in trigger_registry(events) if t.name == "sniper_triple"
    )
    assert trig.active is True
    moments = trig.run(events)
    assert len(moments) == 1
    assert moments[0].kind == "sniper_triple"
    assert "3 sniper-class eliminations" in moments[0].reason
    assert "long" not in moments[0].reason.lower()


def test_r6_dmr_kills_are_sniper_class_not_long_range():
    from app.services.replay.highlight_triggers import _sniper_triple

    moments = _sniper_triple(_sniper_events(weapons=("dmr", "dmr", "dmr")))
    assert len(moments) == 1
    assert "3 sniper-class eliminations" in moments[0].reason
    assert "long-range" not in moments[0].reason


@pytest.mark.parametrize(
    "weapons",
    [
        ("sniper", "sniper", "ar"),  # only 2 sniper-class kills
        ("sniper", "sniper"),  # only 2 kills
        ("ar", "ar", "ar"),  # no sniper-class kills at all
    ],
)
def test_r6_insufficient_sniper_class_kills_fire_nothing(weapons):
    from app.services.replay.highlight_triggers import _sniper_triple

    assert _sniper_triple(_sniper_events(weapons=weapons)) == []


def test_r6_no_distance_field_exists_on_the_path():
    """Guard: if a distance producer ever lands on kill events, this
    test must be revisited — today no kill payload carries one."""
    events = _sniper_events()
    for event in events:
        assert "distance" not in event.payload
        assert "distance_m" not in event.payload


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R-7 (LIVE): handle_candidate score — missing/0 never becomes 80
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.parametrize(
    "score",
    [None, 0, 0.0, "0", "0.0", -5, -90, "abc", "", "  ", True, False,
     float("nan"), float("inf"), float("-inf"), {}, [], object()],
)
def test_r7_no_score_is_dropped_never_80(tmp_path, score):
    queue = _bare_queue(tmp_path)
    assert queue.handle_candidate(_candidate(score=score)) is None
    assert queue.list_entries() == []


def test_r7_missing_score_key_is_dropped(tmp_path):
    queue = _bare_queue(tmp_path)
    payload = _candidate()
    del payload["score"]
    assert queue.handle_candidate(payload) is None
    assert queue.list_entries() == []


@pytest.mark.parametrize(
    "score,expected",
    [
        (90, 90),  # live publisher shape: int(intensity)
        (1, 1),  # lowest real score still queues at its own value
        ("90", 90),  # plain numeric string parses
        (72.7, 72),  # truncation, as enqueue_moment has always done
        (95.0, 95),
    ],
)
def test_r7_real_score_queues_at_its_own_value(tmp_path, score, expected):
    queue = _bare_queue(tmp_path)
    entry = queue.handle_candidate(_candidate(score=score))
    assert entry is not None
    assert entry.score == expected
    assert entry.kind == "fight"
    assert entry.source == "replay_candidate"


def test_r7_scored_and_scoreless_candidates_are_independent(tmp_path):
    queue = _bare_queue(tmp_path)
    assert queue.handle_candidate(_candidate(score=0)) is None
    entry = queue.handle_candidate(_candidate(score=90, moment_id="m-2"))
    assert entry is not None and entry.score == 90
    assert len(queue.list_entries()) == 1  # only the scored one queued


def test_r7_candidate_score_helper_unit():
    from app.services.replay.queue import _candidate_score

    assert _candidate_score(90) == 90
    assert _candidate_score("90") == 90
    assert _candidate_score(72.7) == 72
    assert _candidate_score(None) is None
    assert _candidate_score(0) is None
    assert _candidate_score(True) is None
    assert _candidate_score("abc") is None
    assert _candidate_score(float("nan")) is None
    assert _candidate_score(-1) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R-8: _create_multi_kill_clip string keys — no more TypeError
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_r8_string_keys_parse_instead_of_raising(detector):
    clip = detector._create_multi_kill_clip(
        {"player_name": "Hero", "kill_count": "3", "time_span": "4.5"},
        {"match_id": "m1"},
    )
    assert clip is not None
    assert "3 kills in 4.5s" in clip.title
    assert clip.excitement_score == 50 + 3 * 10 + 10  # fast window bonus


@pytest.mark.parametrize(
    "data",
    [
        {"player_name": "Hero", "kill_count": "three", "time_span": 4.5},
        {"player_name": "Hero", "kill_count": 3, "time_span": "fast"},
        {"player_name": "Hero", "kill_count": None, "time_span": None},
        {"player_name": "Hero", "kill_count": True, "time_span": 4.5},
        {"player_name": "Hero", "kill_count": 3, "time_span": True},
        {"player_name": "Hero", "kill_count": {}, "time_span": []},
        {"player_name": "Hero", "kill_count": 2.5, "time_span": 4.5},
    ],
)
def test_r8_junk_keys_skip_cleanly_without_raising(detector, data):
    assert (
        detector._create_multi_kill_clip(data, {"match_id": "m1"}) is None
    )


def test_r8_below_threshold_still_skips(detector):
    assert (
        detector._create_multi_kill_clip(
            {"player_name": "Hero", "kill_count": 2, "time_span": 4.5},
            {"match_id": "m1"},
        )
        is None
    )
    assert (
        detector._create_multi_kill_clip(
            {"player_name": "Hero", "kill_count": "2", "time_span": 4.5},
            {"match_id": "m1"},
        )
        is None
    )


def test_r8_int_inputs_unchanged(detector):
    clip = detector._create_multi_kill_clip(
        {"player_name": "Hero", "kill_count": 4, "time_span": 8},
        {"match_id": "m1"},
    )
    assert clip is not None
    assert "4 kills in 8.0s" in clip.title
    assert clip.excitement_score == 90
