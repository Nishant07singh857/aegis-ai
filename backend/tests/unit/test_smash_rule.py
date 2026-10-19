"""Unit tests for the Smash Rule championship engine (Phase 20, GAP-3).

Rule semantics verified in docs/DEEP_ANALYSIS/ (BMSD 2026 GF Day 3,
frame-verified + 3 independent sources):

  * Target = 1st-place score after the configured match + bonus (126 = 116 + 10).
  * A team must REACH the target, then WIN a chicken dinner in a LATER match.
  * Same-match reach + win does NOT convert (SOUL won M14 and hit 126 in M14
    -> on match point from M15, not champions).
  * No conversion by the final match -> champion = most total points
    (M18: V1BA won the match; SOUL 155 champions via fallback; the broadcast
    suppressed the WWCD graphic and cut straight to the celebration).
"""

import pytest

from app.services.match_point import (
    MatchPointService,
    SMASH_WS_MESSAGE_TYPE,
    InvalidSmashConfigError,
    SmashRuleConfig,
    SmashRuleEngine,
    SmashRuleTransitionError,
)

# BMSD 2026 configuration: target set after M12, +10 bonus, 18 matches.
BMSD = {
    "target_set_after_match": 12,
    "target_bonus_points": 10,
    "final_match_number": 18,
}


def _std(*rows):
    """Build standings rows: (team_id, team_name, total, chickens, finishes)."""
    out = []
    for r in rows:
        out.append(
            {
                "team_id": r[0],
                "team_name": r[1],
                "total_points": r[2],
                "chickens": r[3] if len(r) > 3 else 0,
                "finishes": r[4] if len(r) > 4 else 0,
            }
        )
    return out


def _engine(**overrides):
    cfg = dict(BMSD)
    cfg.update(overrides)
    engine = SmashRuleEngine()
    engine.configure(SmashRuleConfig(**cfg))
    return engine


# ── configuration ─────────────────────────────────────────────────


def test_config_requires_all_values():
    with pytest.raises(InvalidSmashConfigError):
        SmashRuleConfig.from_dict({"target_set_after_match": 12})
    with pytest.raises(InvalidSmashConfigError):
        SmashRuleConfig(target_set_after_match=12, target_bonus_points=10,
                        final_match_number=12)  # no smash window
    with pytest.raises(InvalidSmashConfigError):
        SmashRuleConfig(target_set_after_match=12, target_bonus_points=-1,
                        final_match_number=18)
    with pytest.raises(InvalidSmashConfigError):
        SmashRuleConfig(target_set_after_match=0, target_bonus_points=10,
                        final_match_number=18)


def test_target_is_computed_not_hardcoded():
    engine = _engine()
    engine.record_match_result(
        12, "soul",
        _std(("8bit", "8Bit", 116, 2, 40), ("soul", "SOUL", 88, 1, 30)),
    )
    snap = engine.snapshot()
    assert snap["target"] == 126  # leader 116 + bonus 10
    assert snap["target_set_from_leader"]["team_id"] == "8bit"
    assert snap["state"] == "target_set"


def test_target_uses_configured_bonus():
    engine = _engine(target_bonus_points=25)
    engine.record_match_result(
        12, "soul",
        _std(("8bit", "8Bit", 116, 2, 40), ("soul", "SOUL", 88, 1, 30)),
    )
    assert engine.snapshot()["target"] == 141


def test_inactive_engine_rejects_results():
    engine = SmashRuleEngine()  # never configured
    with pytest.raises(SmashRuleTransitionError):
        engine.record_match_result(1, "a", _std(("a", "A", 10)))


def test_match_numbers_must_strictly_increase():
    engine = _engine()
    engine.record_match_result(12, "a", _std(("a", "A", 50), ("b", "B", 40)))
    with pytest.raises(SmashRuleTransitionError):
        engine.record_match_result(12, "a", _std(("a", "A", 55), ("b", "B", 40)))
    with pytest.raises(SmashRuleTransitionError):
        engine.record_match_result(5, "a", _std(("a", "A", 55), ("b", "B", 40)))


# ── match-point detection + beat progression ──────────────────────


def test_match_point_detection_and_beats():
    engine = _engine()
    after_m12 = _std(("8bit", "8Bit", 116, 2, 40), ("soul", "SOUL", 88, 1, 30),
                     ("k9", "K9", 90, 0, 35))
    engine.record_match_result(12, "8bit", after_m12)
    assert engine.snapshot()["state"] == "target_set"

    # M13: SOUL reaches 126 but does not win -> on match point.
    after_m13 = _std(("8bit", "8Bit", 121, 2, 45), ("soul", "SOUL", 130, 1, 45),
                     ("k9", "K9", 95, 0, 40))
    engine.record_match_result(13, "k9", after_m13)
    snap = engine.snapshot()
    assert snap["state"] == "match_point"
    teams = {t["team_id"]: t for t in snap["match_point_teams"]}
    assert set(teams) == {"soul"}
    assert teams["soul"]["reached_after_match"] == 13
    assert teams["soul"]["beats"] == []

    # M14: SOUL still on match point, someone else wins -> one beat observed.
    after_m14 = _std(("8bit", "8Bit", 125, 2, 48), ("soul", "SOUL", 138, 1, 50),
                     ("k9", "K9", 110, 1, 52))
    engine.record_match_result(14, "k9", after_m14)
    snap = engine.snapshot()
    teams = {t["team_id"]: t for t in snap["match_point_teams"]}
    assert teams["soul"]["beats"] == [14]
    assert teams["soul"]["beats_count"] == 1
    assert snap["champion"] is None


# ── conversion path ───────────────────────────────────────────────


def test_conversion_wins_championship_immediately():
    engine = _engine()
    engine.record_match_result(
        12, "x", _std(("ldr", "Leader", 116, 2, 40), ("soul", "SOUL", 100, 1, 30)))
    engine.record_match_result(
        13, "other",
        _std(("ldr", "Leader", 120, 2, 42), ("soul", "SOUL", 130, 1, 45),
             ("other", "Other", 90, 1, 30)))
    assert engine.snapshot()["state"] == "match_point"

    # M14: SOUL (on match point since M13) wins -> champion immediately.
    tr = engine.record_match_result(
        14, "soul",
        _std(("ldr", "Leader", 124, 2, 44), ("soul", "SOUL", 153, 2, 58),
             ("other", "Other", 95, 1, 33)))
    snap = engine.snapshot()
    assert snap["state"] == "champion_converted"
    assert snap["champion"]["team_id"] == "soul"
    assert snap["champion"]["decided_by"] == "conversion"
    assert snap["champion"]["decided_at_match"] == 14
    assert snap["broadcast"]["ceremony_variant"] == "conversion"
    assert snap["broadcast"]["suppress_wwcd_graphic"] is False
    assert any(e["type"] == "champion_converted" for e in tr["events"])

    # Terminal: no further results accepted.
    with pytest.raises(SmashRuleTransitionError):
        engine.record_match_result(
            15, "soul", _std(("soul", "SOUL", 160), ("ldr", "Leader", 130)))


def test_same_match_reach_and_win_does_not_convert():
    """SOUL won M14 AND hit 126 in M14 -> not champions; on match point."""
    engine = _engine()
    engine.record_match_result(
        12, "x", _std(("8bit", "8Bit", 116, 2, 40), ("soul", "SOUL", 104, 1, 40)))
    # M14: SOUL wins the chicken AND reaches 126 in the same match.
    tr = engine.record_match_result(
        14, "soul",
        _std(("8bit", "8Bit", 121, 2, 44), ("soul", "SOUL", 127, 2, 53)))
    snap = engine.snapshot()
    assert snap["state"] == "match_point"  # NOT champion_converted
    assert snap["champion"] is None
    assert any(e["type"] == "same_match_reach_and_win" for e in tr["events"])
    teams = {t["team_id"]: t for t in snap["match_point_teams"]}
    assert teams["soul"]["reached_after_match"] == 14
    assert teams["soul"]["beats"] == []  # no beat for the reach match itself

    # M15: SOUL wins again -> NOW it converts.
    engine.record_match_result(
        15, "soul",
        _std(("8bit", "8Bit", 125, 2, 46), ("soul", "SOUL", 150, 3, 66)))
    snap = engine.snapshot()
    assert snap["state"] == "champion_converted"
    assert snap["champion"]["team_id"] == "soul"


def test_conversion_on_final_match_takes_precedence_over_fallback():
    engine = _engine(final_match_number=14)
    engine.record_match_result(
        12, "x", _std(("ldr", "Leader", 116), ("soul", "SOUL", 100)))
    engine.record_match_result(
        13, "other", _std(("ldr", "Leader", 118), ("soul", "SOUL", 130),
                           ("other", "Other", 90)))
    engine.record_match_result(
        14, "soul", _std(("ldr", "Leader", 120), ("soul", "SOUL", 153),
                          ("other", "Other", 95)))
    snap = engine.snapshot()
    assert snap["state"] == "champion_converted"
    assert snap["champion"]["team_id"] == "soul"


def test_unknown_winner_cannot_convert():
    engine = _engine()
    engine.record_match_result(
        12, "x", _std(("ldr", "Leader", 116), ("soul", "SOUL", 100)))
    engine.record_match_result(
        13, "other", _std(("ldr", "Leader", 118), ("soul", "SOUL", 130),
                           ("other", "Other", 95)))
    # Winner not identifiable -> no conversion, match still counts as a beat.
    engine.record_match_result(
        14, None, _std(("ldr", "Leader", 122), ("soul", "SOUL", 136),
                        ("other", "Other", 100)))
    snap = engine.snapshot()
    assert snap["state"] == "match_point"
    teams = {t["team_id"]: t for t in snap["match_point_teams"]}
    assert teams["soul"]["beats"] == [14]


# ── fallback path (the M18 scenario) ──────────────────────────────


def _bmsd_day3_standings():
    """Cumulative standings mirroring the verified BMSD GF Day 3 shape."""
    return {
        12: _std(("8bit", "8Bit", 116, 2, 40), ("soul", "SOUL", 88, 1, 30),
                 ("k9", "K9", 90, 0, 35), ("welt", "WELT", 60, 0, 20),
                 ("v1ba", "V1BA", 70, 1, 25)),
        13: _std(("8bit", "8Bit", 121, 2, 45), ("soul", "SOUL", 104, 1, 40),
                 ("k9", "K9", 113, 1, 48), ("welt", "WELT", 66, 0, 24),
                 ("v1ba", "V1BA", 77, 1, 28)),
        14: _std(("8bit", "8Bit", 130, 2, 49), ("soul", "SOUL", 127, 2, 53),
                 ("k9", "K9", 121, 1, 53), ("welt", "WELT", 70, 0, 27),
                 ("v1ba", "V1BA", 82, 1, 31)),
        15: _std(("8bit", "8Bit", 136, 2, 52), ("soul", "SOUL", 137, 2, 58),
                 ("k9", "K9", 139, 2, 61), ("welt", "WELT", 75, 0, 30),
                 ("v1ba", "V1BA", 86, 1, 33)),
        16: _std(("8bit", "8Bit", 140, 2, 54), ("soul", "SOUL", 143, 2, 61),
                 ("k9", "K9", 144, 2, 64), ("welt", "WELT", 90, 1, 38),
                 ("v1ba", "V1BA", 93, 1, 37)),
        17: _std(("8bit", "8Bit", 143, 2, 55), ("soul", "SOUL", 149, 2, 64),
                 ("k9", "K9", 148, 2, 66), ("welt", "WELT", 95, 1, 41),
                 ("v1ba", "V1BA", 113, 2, 47)),
        18: _std(("8bit", "8Bit", 145, 2, 56), ("soul", "SOUL", 155, 2, 67),
                 ("k9", "K9", 148, 2, 66), ("welt", "WELT", 98, 1, 43),
                 ("v1ba", "V1BA", 131, 4, 55)),
    }


def test_fallback_m18_scenario_soul_wins_on_points():
    """M18: V1BA (Blue Army) wins the match; SOUL + K9 + 8Bit on match point
    but none convert -> fallback -> SOUL 155 champions; WWCD suppressed."""
    engine = _engine()
    table = _bmsd_day3_standings()
    winners = {12: "8bit", 13: "k9", 14: "soul", 15: "k9", 16: "welt",
               17: "v1ba", 18: "v1ba"}
    for m in (12, 13, 14, 15, 16, 17):
        engine.record_match_result(m, winners[m], table[m])

    snap = engine.snapshot()
    assert snap["state"] == "match_point"
    teams = {t["team_id"]: t for t in snap["match_point_teams"]}
    assert set(teams) == {"soul", "k9", "8bit"}
    # Beats: matches observed while on match point without converting.
    assert teams["soul"]["beats"] == [15, 16, 17]
    assert teams["k9"]["beats"] == [16, 17]
    assert teams["8bit"]["beats"] == [15, 16, 17]
    assert snap["champion"] is None

    # M18: V1BA wins (not on match point) -> no conversion -> fallback.
    tr = engine.record_match_result(18, "v1ba", table[18])
    snap = engine.snapshot()
    assert snap["state"] == "champion_fallback"
    assert snap["champion"]["team_id"] == "soul"
    assert snap["champion"]["total_points"] == 155
    assert snap["champion"]["decided_by"] == "fallback"
    assert snap["champion"]["decided_at_match"] == 18
    # Broadcast: WWCD graphic suppressed, straight to the championship.
    assert snap["broadcast"]["suppress_wwcd_graphic"] is True
    assert snap["broadcast"]["jump_to_championship_package"] is True
    assert snap["broadcast"]["ceremony_variant"] == "fallback"
    assert any(e["type"] == "champion_fallback" for e in tr["events"])
    # V1BA reached 126 in M18 itself (same match as their win) -> they are
    # honestly listed as on match point but unconverted, like everyone else.
    assert tr["events"][-1]["match_point_teams_unconverted"] == [
        "8bit", "k9", "soul", "v1ba"]

    # Terminal.
    with pytest.raises(SmashRuleTransitionError):
        engine.record_match_result(19, "soul", table[18])


def test_fallback_with_no_match_point_teams():
    """Nobody ever reaches the target -> most total points still wins."""
    engine = _engine()
    for m in (12, 13, 14, 15, 16, 17, 18):
        engine.record_match_result(
            m, "w",
            _std(("a", "A", 40 + m), ("b", "B", 35 + m), ("w", "W", 20 + m)))
    snap = engine.snapshot()
    assert snap["state"] == "champion_fallback"
    assert snap["champion"]["team_id"] == "a"
    assert snap["champion"]["decided_by"] == "fallback_no_match_point"
    assert snap["match_point_teams"] == []


def test_fallback_tiebreak_is_deterministic():
    """Equal points -> more chickens wins; full tie -> team_id order."""
    engine = _engine()
    rows = _std(("b", "B", 100, 2, 30), ("a", "A", 100, 3, 20),
                ("c", "C", 100, 3, 25))
    for m in range(12, 19):
        engine.record_match_result(m, "w", rows)
    snap = engine.snapshot()
    # a and c both 100 pts / 3 chickens; c has more finishes -> c wins.
    assert snap["champion"]["team_id"] == "c"

    engine2 = _engine()
    rows2 = _std(("b", "B", 100, 1, 10), ("a", "A", 100, 1, 10))
    for m in range(12, 19):
        engine2.record_match_result(m, "w", rows2)
    assert engine2.snapshot()["champion"]["team_id"] == "a"


# ── broadcast beats ───────────────────────────────────────────────


def test_broadcast_beats_follow_the_observed_five():
    engine = _engine()
    beats = engine.snapshot()["broadcast_beats"]
    assert set(beats) == {"explainer", "race_to_smash_chart",
                          "leader_highlight", "scorebug", "fallback_ceremony"}
    # Pre-show: explainer due, rest n/a.
    assert beats["explainer"]["status"] == "due"
    assert beats["leader_highlight"]["status"] == "n/a"

    engine.record_match_result(
        12, "x", _std(("ldr", "Leader", 116), ("soul", "SOUL", 100)))
    beats = engine.snapshot()["broadcast_beats"]
    assert beats["explainer"]["status"] == "done"
    assert beats["race_to_smash_chart"]["status"] == "due"

    engine.record_match_result(
        13, "other", _std(("ldr", "Leader", 118), ("soul", "SOUL", 130),
                           ("other", "Other", 90)))
    beats = engine.snapshot()["broadcast_beats"]
    assert beats["leader_highlight"]["status"] == "active"
    assert beats["scorebug"]["status"] == "active"
    assert beats["fallback_ceremony"]["status"] == "n/a"

    # Fallback fires the ceremony beat.
    for m in (14, 15, 16, 17, 18):
        engine.record_match_result(
            m, "other",
            _std(("ldr", "Leader", 118 + m), ("soul", "SOUL", 130 + m),
                 ("other", "Other", 90 + m)))
    beats = engine.snapshot()["broadcast_beats"]
    assert beats["fallback_ceremony"]["status"] == "done"
    assert beats["leader_highlight"]["status"] == "done"


# ── persistence hooks ─────────────────────────────────────────────


def test_save_load_roundtrip_preserves_everything():
    engine = _engine()
    table = _bmsd_day3_standings()
    for m in (12, 13, 14, 15, 16):
        engine.record_match_result(
            m, {12: "8bit", 13: "k9", 14: "soul", 15: "k9", 16: "welt"}[m],
            table[m])

    saved = engine.to_dict()
    restored = SmashRuleEngine.from_dict(saved)
    assert restored.snapshot() == engine.snapshot()

    # The restored engine continues the tournament correctly.
    restored.record_match_result(17, "v1ba", table[17])
    restored.record_match_result(18, "v1ba", table[18])
    snap = restored.snapshot()
    assert snap["state"] == "champion_fallback"
    assert snap["champion"]["team_id"] == "soul"


def test_from_dict_rejects_unknown_state():
    with pytest.raises(InvalidSmashConfigError):
        SmashRuleEngine.from_dict({"state": "bogus"})


# ── service wiring (Phase 10 service owns the engine) ─────────────


async def test_service_smash_wiring_and_ws_broadcast():
    service = MatchPointService()
    messages = []

    async def listener(message):
        messages.append(message)

    service.add_listener(listener)

    snap = await service.configure_smash(
        enabled=True, target_set_after_match=12, target_bonus_points=10,
        final_match_number=14, updated_by="tester")
    assert snap["state"] == "pre_smash"
    assert snap["config"]["target_bonus_points"] == 10
    assert messages and messages[-1]["type"] == SMASH_WS_MESSAGE_TYPE
    assert messages[-1]["data"]["state"] == "pre_smash"

    # Existing match-point behaviour is untouched by the wiring.
    legacy = await service.evaluate(
        [{"team_id": "t1", "team_name": "Alpha", "total_points": 52.0}])
    assert legacy["last_evaluation"]["on_match_point"][0]["team_id"] == "t1"
    assert legacy["smash"]["state"] == "pre_smash"

    result = await service.record_smash_match_result(
        12, "ldr", _std(("ldr", "Leader", 116), ("soul", "SOUL", 100)),
        updated_by="tester")
    assert result["smash"]["target"] == 126
    assert messages[-1]["type"] == SMASH_WS_MESSAGE_TYPE

    # Save/load hooks round-trip through the service.
    saved = service.save_smash_state()
    service2 = MatchPointService()
    service2.load_smash_state(saved)
    assert service2.smash_status()["target"] == 126

    # Reset clears the engine.
    reset = await service.reset_smash(updated_by="tester")
    assert reset["state"] == "inactive"


async def test_service_smash_config_validation_maps_to_422_inputs():
    service = MatchPointService()
    with pytest.raises(InvalidSmashConfigError):
        await service.configure_smash(target_set_after_match=18,
                                      target_bonus_points=10,
                                      final_match_number=18)
    with pytest.raises(InvalidSmashConfigError):
        await service.configure_smash(bogus_key=1, target_set_after_match=12,
                                      target_bonus_points=10,
                                      final_match_number=18)


async def test_service_smash_defaults_to_opt_in_disabled():
    """Without enabled=True the engine stays inactive (smash is a variant,
    not the default format) — but explicit config values are still kept."""
    service = MatchPointService()
    snap = await service.configure_smash(
        target_set_after_match=12, target_bonus_points=10,
        final_match_number=18, updated_by="tester")
    assert snap["state"] == "inactive"
    assert snap["config"]["target_set_after_match"] == 12
    with pytest.raises(SmashRuleTransitionError):
        await service.record_smash_match_result(
            12, "a", _std(("a", "A", 120)))


async def test_service_smash_transition_errors_surface():
    service = MatchPointService()
    # Not configured -> transition error.
    with pytest.raises(SmashRuleTransitionError):
        await service.record_smash_match_result(
            1, "a", _std(("a", "A", 10)))
