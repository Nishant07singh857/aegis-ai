"""Phase 31B (Worker B) — Clutch revival + locks + Payoff pack.

Covers, with no fabricated numbers and no clock reads except injected
timestamps:
  * FightDetector.is_clutch on the LIVE path (promotion payload that
    LiveDirector consumes), including the adversarial cases the task
    demands: 2vN, full team alive, and a solo player NOT in a fight
    must never trigger.
  * The px/metre honesty rule: metres without a calibrated scale are
    dropped, never converted on a guess.
  * The VI combined-count trap: VI's alive_count counts alive+knocked,
    so a knocked-only team must NOT read as a lone survivor — the
    detector counts ALIVE slots only.
  * Pilot R3b solo lock: lock, death-release, fight-end-release,
    revive-release, UNKNOWN fail-closed, precedence over R4.
  * Payoff pack: elimination banner honesty, sole-survivor winner
    resolution, one-shot winner preference as a tie-break ONLY, and
    the two-clock reconciliation (detector 8s vs director 25s).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.agents.base_agent import AgentConfig
from app.agents.fight_agents.fight_detector import (
    CLUTCH_NEAREST_ENEMY_PX,
    FightDetector,
    evaluate_clutch,
    parse_nearest_enemy_px,
)
from app.services.live_director.config import LiveDirectorConfig
from app.services.live_director.payoff import (
    EliminationBanner,
    WinnerPreference,
    apply_winner_preference,
    build_elimination_banner,
    build_winner_preference,
    payoff_clock_note,
    resolve_winner,
)
from app.services.observer_pilot.rules import (
    RULE_FIGHT_LOCK,
    RULE_RETURN_PRIMARY,
    RULE_SOLO_LOCK,
    RULE_SPECTATED_DEAD,
    PilotSignals,
    POVPilotRules,
)
from app.services.observer_pilot.types import (
    FeedPOVState,
    PlayerPOV,
    POVPlayerStatus,
)

ALIVE = POVPlayerStatus.ALIVE
KNOCKED = POVPlayerStatus.KNOCKED
DEAD = POVPlayerStatus.DEAD
UNKNOWN = POVPlayerStatus.UNKNOWN


def make_detector() -> FightDetector:
    bus = AsyncMock()
    bus.publish = AsyncMock(return_value="test-event-id")
    bus.subscribe = Mock(return_value="subscriber-id")
    bus.unsubscribe = Mock(return_value=True)
    return FightDetector(
        config=AgentConfig(agent_id="fight_detector", agent_type="fight_detector"),
        event_bus=bus,
    )


def promote_fight(detector: FightDetector, team_a="teamA", team_b="teamB"):
    """Drive one fight to promotion: kill + 2 HP drops (>= 3 signals,
    2 signal types, probability > 0.7). Returns the active fight id."""
    detector.process_kill_feed(
        killer_id="k1",
        killer_team_id=team_a,
        victim_id="v1",
        victim_team_id=team_b,
        is_knock=False,
    )
    detector.process_hp_change(
        player_id="v1",
        team_id=team_b,
        old_hp=100,
        new_hp=60,
        source_player_id="k1",
        source_team_id=team_a,
    )
    fight_id = detector.process_hp_change(
        player_id="v1",
        team_id=team_b,
        old_hp=60,
        new_hp=30,
        source_player_id="k1",
        source_team_id=team_a,
    )
    assert fight_id is not None
    return fight_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pure clutch evaluation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEvaluateClutchPure:
    def test_lone_survivor_in_fight(self):
        ok, basis = evaluate_clutch(1, fight_active=True)
        assert (ok, basis) == (True, "lone_survivor_in_fight")

    def test_lone_survivor_near_enemy_at_threshold(self):
        ok, basis = evaluate_clutch(
            1, fight_active=False, nearest_enemy_px=CLUTCH_NEAREST_ENEMY_PX
        )
        assert (ok, basis) == (True, "lone_survivor_near_enemy_px")

    def test_lone_survivor_far_enemy_no_fight_is_not_clutch(self):
        ok, basis = evaluate_clutch(
            1, fight_active=False, nearest_enemy_px=CLUTCH_NEAREST_ENEMY_PX + 1
        )
        assert (ok, basis) == (False, None)

    def test_lone_survivor_no_fight_no_proximity_is_not_clutch(self):
        # A solo player NOT in a fight and NOT near an enemy is just
        # surviving — the adversarial "solo not in fight" case.
        assert evaluate_clutch(1, fight_active=False) == (False, None)

    @pytest.mark.parametrize("alive", [0, 2, 3, 4, None, 1.5, True])
    def test_non_lone_survivor_never_fires(self, alive):
        assert evaluate_clutch(alive, fight_active=True) == (False, None)
        assert evaluate_clutch(
            alive, fight_active=False, nearest_enemy_px=10.0
        ) == (False, None)

    def test_proximity_never_vetoes_live_fight_route(self):
        # Distance unobserved or far does not disproof a fight the
        # detector already promoted.
        assert evaluate_clutch(1, fight_active=True, nearest_enemy_px=9999.0)[0]
        assert evaluate_clutch(1, fight_active=True, nearest_enemy_px=None)[0]


class TestNearestEnemyPxParsing:
    def test_pixels_win(self):
        assert parse_nearest_enemy_px(distance_px=42.0) == 42.0

    def test_metres_convert_only_with_scale(self):
        # 40m at 1.5 px/m = 60px — the audit's "40m" in honest px.
        assert parse_nearest_enemy_px(
            distance_m=40.0, px_per_meter=1.5
        ) == pytest.approx(60.0)

    def test_metres_without_scale_are_dropped(self):
        assert parse_nearest_enemy_px(distance_m=40.0) is None
        assert parse_nearest_enemy_px(distance_m=40.0, px_per_meter=0) is None
        assert parse_nearest_enemy_px(distance_m=40.0, px_per_meter=-1) is None

    def test_invalid_readings_are_unknown(self):
        assert parse_nearest_enemy_px(distance_px=-5.0) is None
        assert parse_nearest_enemy_px(distance_px=float("inf")) is None
        assert parse_nearest_enemy_px() is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Detector live path
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDetectorClutchLivePath:
    def test_promotion_payload_carries_clutch(self):
        detector = make_detector()
        detector.note_team_roster("teamB", ["alive", "dead", "dead", "dead"])
        fight_id = promote_fight(detector)
        event = detector._pending_fight_events[-1]
        assert event["fight_id"] == fight_id
        assert event["is_clutch"] is True
        assert event["clutch_team_id"] == "teamB"
        assert event["clutch_basis"] == "lone_survivor_in_fight"
        active = detector.get_active_fights()[0]
        assert active["is_clutch"] is True
        assert active["clutch_team_id"] == "teamB"
        detail = detector.get_fight(fight_id)
        assert detail is not None and detail["is_clutch"] is True

    def test_attested_alive_count_path(self):
        detector = make_detector()
        detector.note_team_alive("teamB", 1)
        promote_fight(detector)
        assert detector._pending_fight_events[-1]["is_clutch"] is True

    def test_invalid_alive_inputs_ignored(self):
        detector = make_detector()
        detector.note_team_alive("teamB", True)  # bool is not "1 alive"
        detector.note_team_alive("teamB", -1)
        detector.note_team_alive("teamB", 1.5)
        promote_fight(detector)
        assert detector._pending_fight_events[-1]["is_clutch"] is False

    def test_adversarial_two_v_n_never_fires(self):
        detector = make_detector()
        detector.note_team_roster("teamA", ["alive", "alive", "dead", "dead"])
        detector.note_team_roster("teamB", ["alive", "alive", "alive", "dead"])
        promote_fight(detector)
        event = detector._pending_fight_events[-1]
        assert event["is_clutch"] is False
        assert event["clutch_team_id"] is None

    def test_adversarial_full_team_never_fires(self):
        detector = make_detector()
        detector.note_team_roster("teamA", ["alive"] * 4)
        detector.note_team_roster("teamB", ["alive"] * 4)
        promote_fight(detector)
        assert detector._pending_fight_events[-1]["is_clutch"] is False

    def test_adversarial_solo_not_in_fight_never_fires(self):
        detector = make_detector()
        detector.note_team_roster("teamC", ["alive", "dead", "dead", "dead"])
        detector.note_nearest_enemy("teamC", distance_px=30.0)
        assert detector.get_active_fights() == []
        assert detector._pending_fight_events == []

    def test_unknown_slot_blocks_clutch_fail_closed(self):
        detector = make_detector()
        detector.note_team_roster(
            "teamB", ["alive", "dead", "dead", "unknown"]
        )
        promote_fight(detector)
        assert detector._pending_fight_events[-1]["is_clutch"] is False

    def test_knocked_only_team_is_not_a_lone_survivor(self):
        # VI's combined alive_count would read this team as "1"
        # (1 knocked). The detector counts ALIVE only: 0 -> no clutch.
        detector = make_detector()
        detector.note_team_roster(
            "teamB", ["knocked", "dead", "dead", "dead"]
        )
        promote_fight(detector)
        assert detector._pending_fight_events[-1]["is_clutch"] is False

    def test_mid_fight_transition_republishes(self):
        detector = make_detector()
        fight_id = promote_fight(detector)
        assert detector._pending_fight_events[-1]["is_clutch"] is False
        detector.note_team_roster("teamB", ["alive", "dead", "dead", "dead"])
        assert len(detector._pending_fight_events) == 2
        update = detector._pending_fight_events[-1]
        assert update["fight_id"] == fight_id
        assert update["is_clutch"] is True
        assert update["clutch_team_id"] == "teamB"

    def test_revive_clears_detector_flag(self):
        detector = make_detector()
        promote_fight(detector)
        detector.note_team_roster("teamB", ["alive", "dead", "dead", "dead"])
        assert detector.get_active_fights()[0]["is_clutch"] is True
        detector.note_team_roster("teamB", ["alive", "alive", "dead", "dead"])
        assert detector.get_active_fights()[0]["is_clutch"] is False
        # No new event on a True -> False transition (the director's
        # OR keeps promotion-time True; documented in payoff.py).
        assert len(detector._pending_fight_events) == 2

    def test_update_keeps_clutch_without_duplicate_event(self):
        detector = make_detector()
        detector.note_team_roster("teamB", ["alive", "dead", "dead", "dead"])
        fight_id = promote_fight(detector)
        detector.update_active_fight(fight_id, "damage", {"damage": 10})
        assert detector.get_active_fights()[0]["is_clutch"] is True
        assert len(detector._pending_fight_events) == 1

    def test_resolve_summary_carries_clutch(self):
        detector = make_detector()
        detector.note_team_roster("teamB", ["alive", "dead", "dead", "dead"])
        fight_id = promote_fight(detector)
        summary = detector.resolve_fight(fight_id)
        assert summary is not None
        assert summary["is_clutch"] is True
        assert summary["clutch_team_id"] == "teamB"
        assert summary["clutch_basis"] == "lone_survivor_in_fight"

    def test_reset_clears_clutch_inputs(self):
        detector = make_detector()
        detector.note_team_roster("teamB", ["alive", "dead", "dead", "dead"])
        detector.reset()
        promote_fight(detector)
        assert detector._pending_fight_events[-1]["is_clutch"] is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pilot R3b solo lock
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def pilot_player(idx, status):
    return PlayerPOV(
        roster_index=idx,
        player_id=f"p{idx}",
        name=f"Player{idx}",
        status=status,
        hp_fraction=1.0 if status == ALIVE else None,
        hp_velocity=None,
        is_spectated=False,
    )


def pilot_state(spectated_idx, statuses):
    players = tuple(
        PlayerPOV(
            roster_index=i,
            player_id=f"p{i}",
            name=f"Player{i}",
            status=status,
            hp_fraction=1.0 if status == ALIVE else None,
            hp_velocity=None,
            is_spectated=(i == spectated_idx),
        )
        for i, status in enumerate(statuses)
    )
    return FeedPOVState(
        slot_number=1,
        source_id="feed-1",
        team_id="teamA",
        observed_at=100.0,
        spectated_index=spectated_idx,
        players=players,
        identity_known=True,
    )


def pilot_signals(fight_active, involvements=(), now=100.0):
    return PilotSignals(
        killfeed_involvements=list(involvements),
        fight_active_on_feed=fight_active,
        phase="mid_game",
        now=now,
    )


class TestPilotSoloLock:
    def test_solo_lock_holds(self):
        rules = POVPilotRules()
        state = pilot_state(0, [ALIVE, DEAD, DEAD, KNOCKED])
        decision, note = rules.decide_with_note(
            state, pilot_signals(True), None, None
        )
        assert decision is None
        assert note is not None and note.blocked_rule == RULE_SOLO_LOCK

    def test_solo_lock_precedes_fight_lock(self):
        # Spectated is fighting AND a lone survivor: the specific
        # R3b note wins over the generic R4 note.
        rules = POVPilotRules()
        state = pilot_state(0, [ALIVE, DEAD, DEAD, DEAD])
        decision, note = rules.decide_with_note(
            state, pilot_signals(True, [(0, "killer", 99.0)]), None, None
        )
        assert decision is None
        assert note is not None and note.blocked_rule == RULE_SOLO_LOCK

    def test_death_releases_solo_lock_via_r1(self):
        rules = POVPilotRules()
        state = pilot_state(0, [DEAD, ALIVE, DEAD, DEAD])
        decision, _note = rules.decide_with_note(
            state, pilot_signals(True), None, None
        )
        assert decision is not None
        assert decision.rule == RULE_SPECTATED_DEAD
        assert decision.to_index == 1

    def test_fight_end_releases_solo_lock(self):
        rules = POVPilotRules()
        state = pilot_state(2, [DEAD, DEAD, ALIVE, KNOCKED])
        decision, note = rules.decide_with_note(
            state, pilot_signals(False), None, None
        )
        assert decision is None
        assert note is None or note.blocked_rule != RULE_SOLO_LOCK

    def test_revive_releases_solo_lock_mid_fight(self):
        rules = POVPilotRules()
        locked = pilot_state(0, [ALIVE, DEAD, DEAD, KNOCKED])
        _d, note = rules.decide_with_note(
            locked, pilot_signals(True), None, None
        )
        assert note is not None and note.blocked_rule == RULE_SOLO_LOCK
        revived = pilot_state(0, [ALIVE, ALIVE, DEAD, KNOCKED])
        decision, note = rules.decide_with_note(
            revived, pilot_signals(True), None, None
        )
        assert decision is None
        assert note is not None and note.blocked_rule != RULE_SOLO_LOCK

    def test_unknown_teammate_blocks_lock_fail_closed(self):
        rules = POVPilotRules()
        state = pilot_state(0, [ALIVE, DEAD, DEAD, UNKNOWN])
        decision, note = rules.decide_with_note(
            state, pilot_signals(True), None, None
        )
        assert decision is None
        assert note is None or note.blocked_rule != RULE_SOLO_LOCK

    def test_non_lone_survivor_gets_no_solo_lock(self):
        rules = POVPilotRules()
        state = pilot_state(0, [ALIVE, ALIVE, DEAD, DEAD])
        _decision, note = rules.decide_with_note(
            state, pilot_signals(True), None, None
        )
        assert note is None or note.blocked_rule != RULE_SOLO_LOCK

    def test_solo_lock_rule_name_exported(self):
        assert RULE_SOLO_LOCK == "R3b_SOLO_LOCK"
        assert RULE_FIGHT_LOCK == "R4_FIGHT_LOCK"
        assert RULE_RETURN_PRIMARY == "R5_RETURN_PRIMARY"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Director config: clutch-incumbent lock
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestClutchLockConfig:
    def test_lock_defaults(self):
        cfg = LiveDirectorConfig()
        assert cfg.clutch_incumbent_lock_enabled is True
        # Normal hold margin (40), NEVER the cheap challenger margin.
        assert cfg.clutch_incumbent_challenge_margin == 40.0
        assert cfg.clutch_incumbent_challenge_margin != 5.0
        assert cfg.payoff_preference_ttl_s == 60.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Payoff pack
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEliminationBanner:
    def test_full_event(self):
        banner = build_elimination_banner(
            {
                "team_id": "teamA",
                "team_tag": "SOUL",
                "placement": 7,
                "finishes": 12,
                "teams_alive": 6,
                "match_id": "m1",
                "event_id": "ev-9",
            }
        )
        assert banner is not None
        assert isinstance(banner, EliminationBanner)
        assert banner.title == "ELIMINATED"
        assert banner.subtitle == "SOUL ELIMINATED • #7"
        assert banner.finishes == 12
        assert banner.teams_alive == 6
        payload = banner.to_lower_third_payload()
        assert payload == {
            "visible": True,
            "title": "ELIMINATED",
            "subtitle": "SOUL ELIMINATED • #7",
            "animKey": "ev-9",
        }

    def test_missing_numbers_are_never_invented(self):
        banner = build_elimination_banner({"team_tag": "SOUL"})
        assert banner is not None
        assert banner.subtitle == "SOUL ELIMINATED"
        assert banner.placement is None
        assert banner.finishes is None
        assert banner.teams_alive is None

    def test_team_id_falls_back_as_display_tag(self):
        banner = build_elimination_banner({"team_id": "teamA"})
        assert banner is not None
        assert banner.subtitle == "teamA ELIMINATED"

    def test_no_team_identifier_yields_no_banner(self):
        assert build_elimination_banner({}) is None
        assert build_elimination_banner({"team_id": "Unknown"}) is None
        assert build_elimination_banner({"placement": 3}) is None

    def test_kills_are_not_relabelled_as_finishes(self):
        banner = build_elimination_banner({"team_tag": "SOUL", "kills": 9})
        assert banner is not None and banner.finishes is None


class TestWinnerPreference:
    def test_sole_survivor_wins(self):
        assert (
            resolve_winner({"teamA": 3, "teamB": 0, "teamC": 0}, ["teamA", "teamB"])
            == "teamA"
        )

    def test_two_survivors_is_ambiguous(self):
        assert resolve_winner({"teamA": 1, "teamB": 2}) is None

    def test_mutual_wipe_is_ambiguous(self):
        assert resolve_winner({"teamA": 0, "teamB": 0}) is None

    def test_unknown_team_is_ambiguous(self):
        assert resolve_winner({"teamA": 1, "teamB": None}) is None
        # A fight team missing from the counts cannot be proven dead.
        assert resolve_winner({"teamA": 1}, ["teamA", "teamB"]) is None
        assert resolve_winner({"teamA": 1, "teamB": True}) is None

    def test_build_preference_one_shot_and_ttl(self):
        pref = build_winner_preference(
            {"fight_id": "fight-1", "teams_involved": ["teamA", "teamB"]},
            {"teamA": 0, "teamB": 2},
            now=1000.0,
            ttl_s=60.0,
        )
        assert pref is not None
        assert isinstance(pref, WinnerPreference)
        assert pref.preferred_team_id == "teamB"
        assert pref.is_active(1059.0) is True
        assert pref.is_active(1061.0) is False
        pref.mark_consumed()
        assert pref.is_active(1001.0) is False

    def test_build_preference_ambiguous_is_none(self):
        assert (
            build_winner_preference(
                {"fight_id": "fight-1", "teams_involved": ["teamA", "teamB"]},
                {"teamA": 1, "teamB": 1},
                now=1000.0,
            )
            is None
        )
        assert (
            build_winner_preference(
                {"teams_involved": ["teamA", "teamB"]},
                {"teamA": 0, "teamB": 1},
                now=1000.0,
            )
            is None
        )

    def test_apply_breaks_exact_ties_only(self):
        pref = WinnerPreference(
            fight_id="fight-1",
            preferred_team_id="teamB",
            created_at=1000.0,
            ttl_s=60.0,
        )
        challengers = [
            {"team_id": "teamA", "score": 50.0},
            {"team_id": "teamB", "score": 50.0},
            {"team_id": "teamC", "score": 40.0},
        ]
        ordered, changed = apply_winner_preference(challengers, pref, 1001.0)
        assert changed is True
        assert [c["team_id"] for c in ordered] == ["teamB", "teamA", "teamC"]
        # Input list is not mutated.
        assert challengers[0]["team_id"] == "teamA"

    def test_apply_never_manufactures_a_switch(self):
        pref = WinnerPreference(
            fight_id="fight-1",
            preferred_team_id="teamB",
            created_at=1000.0,
            ttl_s=60.0,
        )
        behind = [
            {"team_id": "teamA", "score": 60.0},
            {"team_id": "teamB", "score": 50.0},
        ]
        ordered, changed = apply_winner_preference(behind, pref, 1001.0)
        assert changed is False
        assert [c["team_id"] for c in ordered] == ["teamA", "teamB"]
        # Scores are never modified.
        assert ordered[1]["score"] == 50.0

    def test_apply_sole_leader_unchanged(self):
        pref = WinnerPreference(
            fight_id="fight-1",
            preferred_team_id="teamB",
            created_at=1000.0,
            ttl_s=60.0,
        )
        ordered, changed = apply_winner_preference(
            [
                {"team_id": "teamB", "score": 60.0},
                {"team_id": "teamA", "score": 50.0},
            ],
            pref,
            1001.0,
        )
        assert changed is False
        assert ordered[0]["team_id"] == "teamB"

    def test_apply_inactive_or_absent_preference(self):
        ordered, changed = apply_winner_preference(
            [{"team_id": "teamA", "score": 50.0}], None, 1001.0
        )
        assert changed is False
        consumed = WinnerPreference(
            fight_id="f", preferred_team_id="teamA",
            created_at=1000.0, ttl_s=60.0, consumed=True,
        )
        _ordered, changed = apply_winner_preference(
            [{"team_id": "teamA", "score": 50.0}], consumed, 1001.0
        )
        assert changed is False


class TestPayoffClocks:
    def test_clock_reconciliation(self):
        note = payoff_clock_note()
        assert note["detector_resolve_idle_s"] == 8.0
        assert note["detector_hard_resolve_s"] == 16.0
        assert note["director_resolve_idle_s"] == 25.0
        assert note["gap_s"] == 17.0
        assert note["payoff_gates_on"] == "director_25s"
        assert note["fight_ended_producer"] is None
        assert note["winner_switch_transition"] == "hard_cut"
