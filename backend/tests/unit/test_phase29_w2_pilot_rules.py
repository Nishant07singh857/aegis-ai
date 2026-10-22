"""Phase 29 (W2) — POV Pilot rules engine proof tests.

Pure deterministic logic: every timestamp is injected, no clock, no
vision, no ADB. The shared contract types are imported from W1's
``observer_pilot.types``; if that module is absent the tests fall back
to local stubs with the identical shapes (the rules module has the
same fallback, and str-Enum value equality keeps the two compatible).

Covered: every rule's positive + negative case, the rule order
(R0, R1, R4, R2, R3, R5), the thrash probe from the brief
(t=0 knock -> switch, t=3 knock -> dwell blocks, t=9.5 -> switch),
the R1 dwell bypass, the R4 fight lock, R0 zero-decision behaviour,
multi-teammate evidence priority, the final-zone dwell x2, and the
R5 return-primary timing boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

import pytest

try:  # W1's shared contract (preferred)
    from app.services.observer_pilot.types import (
        FeedPOVState,
        PlayerPOV,
        POVPlayerStatus,
    )
except Exception:  # pragma: no cover - stub until W1 lands
    class POVPlayerStatus(str, Enum):
        ALIVE = "alive"
        KNOCKED = "knocked"
        DEAD = "dead"
        UNKNOWN = "unknown"

    @dataclass
    class PlayerPOV:
        roster_index: int
        player_id: Optional[str] = None
        name: Optional[str] = None
        status: POVPlayerStatus = POVPlayerStatus.UNKNOWN
        hp_fraction: Optional[float] = None
        hp_velocity: Optional[float] = None
        is_spectated: bool = False

    @dataclass
    class FeedPOVState:
        slot_number: int
        source_id: str
        team_id: Optional[str] = None
        observed_at: float = 0.0
        spectated_index: Optional[int] = None
        players: Tuple[PlayerPOV, ...] = ()
        identity_known: bool = False


from app.services.observer_pilot.rules import (
    RULE_FIGHT_LOCK,
    RULE_RETURN_PRIMARY,
    RULE_SPECTATED_DEAD,
    RULE_SPECTATED_KNOCKED,
    RULE_TEAMMATE_IN_FIGHT,
    PilotConfig,
    PilotSignals,
    POVPilotRules,
    is_final_zone,
)

NAMES = {0: "Alpha", 1: "GodL", 2: "Nova", 3: "Rex"}
ALIVE = POVPlayerStatus.ALIVE
KNOCKED = POVPlayerStatus.KNOCKED
DEAD = POVPlayerStatus.DEAD
UNKNOWN = POVPlayerStatus.UNKNOWN


def player(
    idx: int,
    status: POVPlayerStatus = ALIVE,
    hp_velocity: Optional[float] = None,
    hp_fraction: Optional[float] = 0.9,
) -> PlayerPOV:
    return PlayerPOV(
        roster_index=idx,
        player_id=f"p{idx}",
        name=NAMES[idx],
        status=status,
        hp_fraction=hp_fraction,
        hp_velocity=hp_velocity,
        is_spectated=False,
    )


def feed(
    spectated: Optional[int] = 0,
    players: Optional[List[PlayerPOV]] = None,
    identity_known: bool = True,
    now: float = 100.0,
) -> FeedPOVState:
    if players is None:
        players = [player(i) for i in range(4)]
    return FeedPOVState(
        slot_number=3,
        source_id="feed-3",
        team_id="team-a",
        observed_at=now,
        spectated_index=spectated,
        players=tuple(players),
        identity_known=identity_known,
    )


def signals(
    now: float = 100.0,
    killfeed: Optional[List[Tuple[int, str, float]]] = None,
    fight_active: bool = False,
    phase: str = "mid_game",
) -> PilotSignals:
    return PilotSignals(
        killfeed_involvements=list(killfeed or []),
        fight_active_on_feed=fight_active,
        phase=phase,
        now=now,
    )


@pytest.fixture()
def pilot() -> POVPilotRules:
    return POVPilotRules()


# ── R0 NO-EVIDENCE ───────────────────────────────────────────────


def test_r0_identity_unknown_blocks_even_dead_spectated(pilot):
    st = feed(
        spectated=1,
        players=[player(0), player(1, DEAD), player(2), player(3)],
        identity_known=False,
    )
    assert pilot.decide(st, signals(), None, None) is None


def test_r0_spectated_index_none_blocks(pilot):
    st = feed(
        spectated=None,
        players=[player(0), player(1, DEAD), player(2), player(3)],
    )
    assert pilot.decide(st, signals(), None, None) is None


def test_r0_spectated_status_unknown_blocks(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0, UNKNOWN),
            player(1, hp_velocity=-0.4),
            player(2),
            player(3),
        ],
    )
    assert pilot.decide(st, signals(killfeed=[(1, "killer", 99.0)]), None, None) is None


def test_r0_unknown_identity_zero_decisions_across_inputs(pilot):
    """No input shape may produce a decision when identity is unknown."""
    scenarios = [
        # dead spectated (would be R1)
        feed(spectated=1, identity_known=False,
             players=[player(0), player(1, DEAD), player(2), player(3)]),
        # knocked spectated + fighting teammate (would be R2)
        feed(spectated=0, identity_known=False,
             players=[player(0, KNOCKED), player(1, hp_velocity=-0.3),
                      player(2), player(3)]),
        # quiet spectated + fighting teammate (would be R3)
        feed(spectated=0, identity_known=False,
             players=[player(0), player(1, hp_velocity=-0.3),
                      player(2), player(3)]),
        # long-quiet team off primary (would be R5)
        feed(spectated=2, identity_known=False),
    ]
    for st in scenarios:
        assert pilot.decide(st, signals(), None, 0.0) is None


# ── R1 SPECTATED_DEAD ────────────────────────────────────────────


def test_r1_dead_moves_to_next_alive(pilot):
    st = feed(
        spectated=1,
        players=[player(0), player(1, DEAD), player(2), player(3)],
    )
    d = pilot.decide(st, signals(now=100.0), None, None)
    assert d is not None
    assert d.rule == RULE_SPECTATED_DEAD
    assert d.from_index == 1 and d.to_index == 2
    assert d.slot_number == 3 and d.source_id == "feed-3"
    assert d.decided_at == 100.0
    assert "GodL (idx 1)" in d.reason and "Nova (idx 2)" in d.reason
    assert "DEAD" in d.reason


def test_r1_bypasses_dwell(pilot):
    st = feed(
        spectated=1,
        players=[player(0), player(1, DEAD), player(2), player(3)],
    )
    # Last tap 1s ago — deep inside the 9s dwell. R1 must still fire.
    d = pilot.decide(st, signals(now=100.0), last_tap_at=99.0,
                     last_fight_signal_at=None)
    assert d is not None and d.to_index == 2
    assert d.evidence["dwell_bypassed"] is True


def test_r1_wraps_around_roster(pilot):
    st = feed(
        spectated=3,
        players=[player(0), player(1), player(2, KNOCKED), player(3, DEAD)],
    )
    d = pilot.decide(st, signals(), None, None)
    assert d is not None and d.to_index == 0


def test_r1_skips_knocked_dead_unknown_teammates(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0, DEAD),
            player(1, KNOCKED),
            player(2, UNKNOWN),
            player(3),
        ],
    )
    d = pilot.decide(st, signals(), None, None)
    assert d is not None and d.to_index == 3


def test_r1_no_alive_teammate_no_decision(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0, DEAD),
            player(1, DEAD),
            player(2, KNOCKED),
            player(3, DEAD),
        ],
    )
    assert pilot.decide(st, signals(), None, None) is None


# ── R4 FIGHT_LOCK ────────────────────────────────────────────────


def test_r4_spectated_fighting_hp_drop_locks(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0, hp_velocity=-0.20),          # spectated fighting
            player(1, hp_velocity=-0.40),          # teammate fighting harder
            player(2),
            player(3),
        ],
    )
    decision, note = pilot.decide_with_note(st, signals(), None, None)
    assert decision is None
    assert note is not None and note.blocked_rule == RULE_FIGHT_LOCK


def test_r4_spectated_fighting_killfeed_locks(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1, hp_velocity=-0.4), player(2), player(3)],
    )
    sig = signals(now=100.0, killfeed=[(0, "killer", 99.0), (1, "killer", 99.5)])
    assert pilot.decide(st, sig, None, None) is None


def test_r4_stale_killfeed_does_not_lock(pilot):
    """A killfeed entry older than the quiet window is not a fight."""
    st = feed(
        spectated=0,
        players=[player(0), player(1, hp_velocity=-0.4), player(2), player(3)],
    )
    sig = signals(now=100.0, killfeed=[(0, "killer", 80.0)])  # 20s old
    d = pilot.decide(st, sig, None, None)
    assert d is not None and d.rule == RULE_TEAMMATE_IN_FIGHT


# ── R2 SPECTATED_KNOCKED ─────────────────────────────────────────


def test_r2_knocked_moves_to_fighting_teammate_hp(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0, KNOCKED),
            player(1, hp_velocity=-0.25),
            player(2),
            player(3),
        ],
    )
    d = pilot.decide(st, signals(), None, None)
    assert d is not None
    assert d.rule == RULE_SPECTATED_KNOCKED
    assert d.to_index == 1
    assert "KNOCKED" in d.reason


def test_r2_knocked_moves_to_fighting_teammate_killfeed(pilot):
    st = feed(
        spectated=0,
        players=[player(0, KNOCKED), player(1), player(2), player(3)],
    )
    sig = signals(now=100.0, killfeed=[(2, "knocked", 98.0)])
    d = pilot.decide(st, sig, None, None)
    assert d is not None and d.rule == RULE_SPECTATED_KNOCKED
    assert d.to_index == 2


def test_r2_knocked_no_fighting_teammate_no_decision(pilot):
    st = feed(
        spectated=1,
        players=[player(0), player(1, KNOCKED), player(2), player(3)],
    )
    # Recent fight signal keeps R5 from firing in place of R2.
    assert pilot.decide(st, signals(now=100.0), None, 98.0) is None


def test_r2_dwell_blocks_and_note_explains(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0, KNOCKED),
            player(1, hp_velocity=-0.25),
            player(2),
            player(3),
        ],
    )
    decision, note = pilot.decide_with_note(
        st, signals(now=100.0), last_tap_at=97.0, last_fight_signal_at=None
    )
    assert decision is None
    assert note is not None
    assert note.blocked_rule == RULE_SPECTATED_KNOCKED
    assert "dwell" in note.reason.lower()
    assert note.evidence["effective_dwell_s"] == 9.0


# ── R3 TEAMMATE_IN_FIGHT ─────────────────────────────────────────


def test_r3_quiet_spectated_teammate_fighting_moves(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1), player(2, hp_velocity=-0.12), player(3)],
    )
    d = pilot.decide(st, signals(), None, None)
    assert d is not None
    assert d.rule == RULE_TEAMMATE_IN_FIGHT
    assert d.to_index == 2
    assert d.evidence["spectated_quiet"] is True
    assert d.evidence["target_hp_velocity"] == -0.12


def test_r3_hp_velocity_none_is_absent_not_fighting(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1, hp_velocity=None), player(2), player(3)],
    )
    assert pilot.decide(st, signals(), None, None) is None


def test_r3_hp_velocity_above_threshold_not_fighting(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1, hp_velocity=-0.01), player(2), player(3)],
    )
    assert pilot.decide(st, signals(), None, None) is None


def test_r3_knocked_or_unknown_teammate_never_targeted(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0),
            player(1, KNOCKED, hp_velocity=-0.5),
            player(2, UNKNOWN, hp_velocity=-0.5),
            player(3),
        ],
    )
    assert pilot.decide(st, signals(), None, None) is None


# ── Evidence priority (R3 multi-teammate) ────────────────────────


def test_priority_freshest_priority_killfeed_wins(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1), player(2), player(3)],
    )
    sig = signals(
        now=100.0,
        killfeed=[(2, "knocked", 95.0), (3, "killer", 99.0)],
    )
    d = pilot.decide(st, sig, None, None)
    assert d is not None and d.to_index == 3


def test_priority_killfeed_beats_hp_velocity(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0),
            player(1),                              # killer 2s ago, HP steady
            player(2, hp_velocity=-0.50),           # HP crashing, no killfeed
            player(3),
        ],
    )
    sig = signals(now=100.0, killfeed=[(1, "killer", 98.0)])
    d = pilot.decide(st, sig, None, None)
    assert d is not None and d.to_index == 1


def test_priority_most_negative_velocity_second_tier(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0),
            player(1, hp_velocity=-0.08),
            player(2),
            player(3, hp_velocity=-0.30),
        ],
    )
    d = pilot.decide(st, signals(), None, None)
    assert d is not None and d.to_index == 3


def test_priority_exact_tie_lowest_roster_index(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1), player(2), player(3)],
    )
    sig = signals(
        now=100.0,
        killfeed=[(3, "killer", 98.0), (1, "killer", 98.0)],  # same instant
    )
    d = pilot.decide(st, sig, None, None)
    assert d is not None and d.to_index == 1


# ── Thrash probe (brief sequence) ────────────────────────────────


def test_thrash_probe_back_to_back_knocks(pilot):
    """t=0 A knocked -> switch to B. t=3 B knocked, C fighting ->
    dwell blocks. t=9.5 dwell over -> switch to C."""
    # t=0: A (idx0) spectated + knocked; B (idx1) fighting.
    st0 = feed(
        spectated=0,
        players=[
            player(0, KNOCKED),
            player(1, hp_velocity=-0.20),
            player(2),
            player(3),
        ],
        now=0.0,
    )
    d0 = pilot.decide(st0, signals(now=0.0), last_tap_at=None,
                      last_fight_signal_at=None)
    assert d0 is not None and d0.to_index == 1 and d0.rule == RULE_SPECTATED_KNOCKED
    last_tap = 0.0  # the executed tap

    # t=3: camera now on B, B knocked; C (idx2) fighting.
    st3 = feed(
        spectated=1,
        players=[
            player(0),
            player(1, KNOCKED),
            player(2, hp_velocity=-0.20),
            player(3),
        ],
        now=3.0,
    )
    d3, note3 = pilot.decide_with_note(
        st3, signals(now=3.0), last_tap_at=last_tap, last_fight_signal_at=3.0
    )
    assert d3 is None  # dwell: only 3s of 9s elapsed
    assert note3 is not None and note3.blocked_rule == RULE_SPECTATED_KNOCKED

    # t=9.5: dwell satisfied (9.5 >= 9.0) -> switch to C.
    d95 = pilot.decide(
        st3, signals(now=9.5), last_tap_at=last_tap, last_fight_signal_at=3.0
    )
    assert d95 is not None and d95.to_index == 2


# ── Final-zone dwell ─────────────────────────────────────────────


def test_final_zone_dwell_doubled_blocks_at_base_dwell(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1, hp_velocity=-0.2), player(2), player(3)],
    )
    # 10s elapsed: past the 9s base dwell, short of the 18s final dwell.
    for phase in ("final_circle", "final_zone"):
        d, note = pilot.decide_with_note(
            st, signals(now=100.0, phase=phase),
            last_tap_at=90.0, last_fight_signal_at=None,
        )
        assert d is None, phase
        assert note is not None and note.evidence["effective_dwell_s"] == 18.0


def test_final_zone_dwell_satisfied_after_double(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1, hp_velocity=-0.2), player(2), player(3)],
    )
    d = pilot.decide(
        st, signals(now=100.0, phase="final_circle"),
        last_tap_at=81.5, last_fight_signal_at=None,
    )
    assert d is not None and d.to_index == 1


def test_non_final_phase_uses_base_dwell(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1, hp_velocity=-0.2), player(2), player(3)],
    )
    d = pilot.decide(
        st, signals(now=100.0, phase="mid_game"),
        last_tap_at=90.0, last_fight_signal_at=None,
    )
    assert d is not None and d.to_index == 1


def test_is_final_zone_spellings(pilot):
    assert is_final_zone("final_circle")
    assert is_final_zone("final_zone")
    assert is_final_zone("Final Circle") is True  # case/space normalised
    assert is_final_zone("mid_game") is False
    assert pilot.effective_dwell_s("final_circle") == 18.0
    assert pilot.effective_dwell_s("early") == 9.0


# ── R5 RETURN_PRIMARY ────────────────────────────────────────────


def test_r5_returns_to_primary_after_quiet(pilot):
    st = feed(spectated=2)  # everyone alive + quiet, camera off primary
    d = pilot.decide(st, signals(now=100.0), None, last_fight_signal_at=85.0)
    assert d is not None
    assert d.rule == RULE_RETURN_PRIMARY
    assert d.to_index == 0


def test_r5_boundary_exact_delay_does_not_fire(pilot):
    st = feed(spectated=2)
    # Quiet for EXACTLY the 10s delay: not strictly more -> no return.
    assert pilot.decide(st, signals(now=100.0), None, 90.0) is None
    # Just past the boundary -> fires.
    d = pilot.decide(st, signals(now=100.0), None, 89.9)
    assert d is not None and d.rule == RULE_RETURN_PRIMARY


def test_r5_no_signal_ever_recorded_counts_as_long_quiet(pilot):
    st = feed(spectated=3)
    d = pilot.decide(st, signals(now=100.0), None, None)
    assert d is not None and d.to_index == 0


def test_r5_recent_killfeed_blocks_return(pilot):
    st = feed(spectated=2)
    sig = signals(now=100.0, killfeed=[(2, "victim", 99.0)])
    # The victim entry is also stale-for-R5 purposes via timestamp merge:
    # last signal = 99.0, quiet for 1s < 10s. (Idx2's own entry is inside
    # the quiet window, so this is additionally not an R3/R4 case: the
    # spectated player IS fighting by definition — assert no R5 either
    # way by checking the rule is not R5 if a decision exists.)
    d = pilot.decide(st, sig, None, 50.0)
    assert d is None or d.rule != RULE_RETURN_PRIMARY


def test_r5_primary_dead_no_return(pilot):
    st = feed(
        spectated=2,
        players=[player(0, DEAD), player(1), player(2), player(3)],
    )
    assert pilot.decide(st, signals(now=100.0), None, 50.0) is None


def test_r5_already_on_primary_no_decision(pilot):
    st = feed(spectated=0)
    assert pilot.decide(st, signals(now=100.0), None, 50.0) is None


def test_r5_fight_active_on_feed_blocks(pilot):
    st = feed(spectated=2)
    sig = signals(now=100.0, fight_active=True)
    assert pilot.decide(st, sig, None, 50.0) is None


def test_r5_dwell_applies(pilot):
    st = feed(spectated=2)
    decision, note = pilot.decide_with_note(
        st, signals(now=100.0), last_tap_at=98.0, last_fight_signal_at=50.0
    )
    assert decision is None
    assert note is not None and note.blocked_rule == RULE_RETURN_PRIMARY


def test_r5_custom_primary_index():
    pilot = POVPilotRules(PilotConfig(primary_roster_index=2))
    st = feed(spectated=1)
    d = pilot.decide(st, signals(now=100.0), None, 50.0)
    assert d is not None and d.to_index == 2


# ── explain / determinism ────────────────────────────────────────


def test_explain_no_action_dwell_text(pilot):
    st = feed(
        spectated=0,
        players=[
            player(0, KNOCKED),
            player(1, hp_velocity=-0.25),
            player(2),
            player(3),
        ],
    )
    text = pilot.explain_no_action(
        st, signals(now=100.0), last_tap_at=97.0, last_fight_signal_at=None
    )
    assert "dwell" in text.lower()


def test_explain_no_action_names_available_decision(pilot):
    st = feed(
        spectated=1,
        players=[player(0), player(1, DEAD), player(2), player(3)],
    )
    text = pilot.explain_no_action(st, signals(), None, None)
    assert RULE_SPECTATED_DEAD in text


def test_decide_with_note_decision_has_no_note(pilot):
    st = feed(
        spectated=1,
        players=[player(0), player(1, DEAD), player(2), player(3)],
    )
    decision, note = pilot.decide_with_note(st, signals(), None, None)
    assert decision is not None and note is None


def test_engine_is_deterministic_and_stateless(pilot):
    st = feed(
        spectated=0,
        players=[player(0), player(1, hp_velocity=-0.2), player(2), player(3)],
    )
    sig = signals(now=100.0)
    first = pilot.decide(st, sig, None, None)
    second = pilot.decide(st, sig, None, None)
    assert first == second


def test_config_defaults_match_brief():
    cfg = PilotConfig()
    assert cfg.dwell_s == 9.0
    assert cfg.hp_drop_velocity == -0.05
    assert cfg.quiet_window_s == 6.0
    assert cfg.return_primary_delay_s == 10.0
    assert cfg.final_zone_dwell_multiplier == 2.0
    assert cfg.primary_roster_index == 0
