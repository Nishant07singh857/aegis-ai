# backend/tests/unit/test_replay_fight_state_gate.py
"""Phase 25 Defect #2 — fight-state gate honesty (fail closed on UNKNOWN).

The take gate's docstrings always promised that when fight state is
unknown — no vision-intelligence service wired — the gate is labelled
``fight_state_unknown`` and "no service" is never dressed up as
"no fight". The code never emitted that label: the default provider
returned ``[]`` both for "no service" AND for "service reports no
fights", so ``can_take_now`` silently returned can_take=True when
fight state was actually UNKNOWN.

Contract under test (Optional provider semantics):
  * provider returns ``None``  -> UNKNOWN -> ``fight_state_unknown``
    in blocked_by, can_take False (fail closed), in every mode,
    for both the operator take path and ``maybe_take_auto``.
  * provider returns ``[]``     -> KNOWN empty -> neither
    ``fight_state_unknown`` nor ``fight_on_pgm``; gate clear on the
    fight axis.
  * provider returns ``[fight]`` -> KNOWN active -> ``fight_on_pgm``
    blocks (pre-existing behaviour preserved).
  * the DEFAULT provider returns ``None`` when
    ``get_vision_intelligence_service()`` returns None or raises,
    and the real list when a service exists.
"""

from __future__ import annotations

import time

import pytest

import app.services.vision_intelligence.service as vis_service_module
from app.services.replay.queue import (
    ReplayQueue,
    ReplayTakeRefused,
    _default_fights_provider,
)
from app.services.replay.renderer import HighlightRenderer
from app.services.replay.service import ReplayService

pytestmark = pytest.mark.unit

BASE_TS = time.time() - 120.0

_USE_DEFAULT = object()


class _FakeFightDetector:
    def __init__(self, fights: list) -> None:
        self._fights = fights

    def get_active_fights(self) -> list:
        return list(self._fights)


class _FakeVisionService:
    def __init__(self, fights: list) -> None:
        self.fight_detector = _FakeFightDetector(fights)


def _make_queue(tmp_path, *, mode: str = "manual", phase: str = "mid_game",
                fights_provider=_USE_DEFAULT):
    renderer = HighlightRenderer(
        highlights_dir=tmp_path / "highlights",
        recordings_dir=tmp_path / "recordings",
    )
    svc = ReplayService(window_seconds=3600, renderer=renderer)
    kwargs = dict(
        director_provider=lambda: {"mode": mode, "active_camera": 1, "scene": "main"},
        phase_provider=lambda: phase,
    )
    if fights_provider is not _USE_DEFAULT:
        kwargs["fights_provider"] = fights_provider
    queue = ReplayQueue(svc, recorder=None, **kwargs)
    return svc, queue


def _enqueue(queue: ReplayQueue, **over):
    payload = dict(
        kind="squad_wipe",
        score=90,
        reason="fight-state gate test moment",
        window_start=BASE_TS,
        window_end=BASE_TS + 8.0,
        player="Scout",
        team="Team Soul",
        opponents=["V1"],
    )
    payload.update(over)
    return queue.enqueue_moment(**payload)


# ── the default provider itself ────────────────────────────────


def test_default_provider_returns_none_when_no_service_wired(monkeypatch) -> None:
    monkeypatch.setattr(
        vis_service_module, "get_vision_intelligence_service", lambda: None
    )
    assert _default_fights_provider() is None


def test_default_provider_returns_none_when_getter_raises(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("vision service unavailable")

    monkeypatch.setattr(
        vis_service_module, "get_vision_intelligence_service", _boom
    )
    assert _default_fights_provider() is None


def test_default_provider_returns_none_when_service_read_raises(monkeypatch) -> None:
    class _BrokenDetector:
        def get_active_fights(self):
            raise RuntimeError("detector down")

    class _BrokenService:
        fight_detector = _BrokenDetector()

    monkeypatch.setattr(
        vis_service_module,
        "get_vision_intelligence_service",
        lambda: _BrokenService(),
    )
    assert _default_fights_provider() is None


def test_default_provider_returns_real_list_when_service_wired(monkeypatch) -> None:
    monkeypatch.setattr(
        vis_service_module,
        "get_vision_intelligence_service",
        lambda: _FakeVisionService([]),
    )
    assert _default_fights_provider() == []

    fight = {"fight_id": "f1", "teams": ["A", "B"]}
    monkeypatch.setattr(
        vis_service_module,
        "get_vision_intelligence_service",
        lambda: _FakeVisionService([fight]),
    )
    assert _default_fights_provider() == [fight]


# ── no service wired (default provider fallback) → fail closed ──


def test_no_service_wired_gate_fails_closed_with_fight_state_unknown(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        vis_service_module, "get_vision_intelligence_service", lambda: None
    )
    _svc, queue = _make_queue(tmp_path)  # default fights provider
    gate = queue.can_take_now()
    assert gate["can_take"] is False
    assert "fight_state_unknown" in gate["blocked_by"]
    # Unknown must not be mislabelled as a known fight either.
    assert "fight_on_pgm" not in gate["blocked_by"]
    # The status payload (the API surface) carries the same gate.
    assert "fight_state_unknown" in queue.status()["can_take_now"]["blocked_by"]


async def test_no_service_wired_maybe_take_auto_does_not_take(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        vis_service_module, "get_vision_intelligence_service", lambda: None
    )
    _svc, queue = _make_queue(tmp_path, mode="full_auto")
    entry = _enqueue(queue)
    assert entry.state == "ready"
    assert await queue.maybe_take_auto() is None
    assert queue.get(entry.id).state == "ready"  # offered, never aired


async def test_no_service_wired_operator_take_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        vis_service_module, "get_vision_intelligence_service", lambda: None
    )
    _svc, queue = _make_queue(tmp_path, mode="manual")
    entry = _enqueue(queue)
    with pytest.raises(ReplayTakeRefused) as excinfo:
        await queue.take(entry.id, taken_by="director-1", via="operator")
    assert "fight_state_unknown" in excinfo.value.gate["blocked_by"]
    assert queue.get(entry.id).state == "ready"


# ── service wired → known state ────────────────────────────────


def test_service_wired_zero_fights_gate_clear_on_fight_axis(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        vis_service_module,
        "get_vision_intelligence_service",
        lambda: _FakeVisionService([]),
    )
    _svc, queue = _make_queue(tmp_path)  # default fights provider
    gate = queue.can_take_now()
    assert "fight_state_unknown" not in gate["blocked_by"]
    assert "fight_on_pgm" not in gate["blocked_by"]
    assert gate["blocked_by"] == []
    assert gate["can_take"] is True
    assert gate["active_fights_on_pgm"] == []


def test_service_wired_active_fight_blocks_with_fight_on_pgm(
    tmp_path, monkeypatch
) -> None:
    fight = {"fight_id": "f1", "teams": ["A", "B"], "intensity": 80.0}
    monkeypatch.setattr(
        vis_service_module,
        "get_vision_intelligence_service",
        lambda: _FakeVisionService([fight]),
    )
    _svc, queue = _make_queue(tmp_path)  # default fights provider
    gate = queue.can_take_now()
    assert gate["can_take"] is False
    assert "fight_on_pgm" in gate["blocked_by"]
    assert "fight_state_unknown" not in gate["blocked_by"]
    assert gate["active_fights_on_pgm"] == [fight]


# ── injected providers: Optional semantics flow through ────────


def test_injected_provider_returning_none_is_unknown(tmp_path) -> None:
    _svc, queue = _make_queue(tmp_path, fights_provider=lambda: None)
    gate = queue.can_take_now()
    assert gate["can_take"] is False
    assert "fight_state_unknown" in gate["blocked_by"]
    assert "fight_on_pgm" not in gate["blocked_by"]


async def test_injected_provider_none_blocks_maybe_take_auto(tmp_path) -> None:
    _svc, queue = _make_queue(
        tmp_path, mode="full_auto", fights_provider=lambda: None
    )
    entry = _enqueue(queue)
    assert await queue.maybe_take_auto() is None
    assert queue.get(entry.id).state == "ready"


def test_injected_provider_empty_list_is_known_empty(tmp_path) -> None:
    _svc, queue = _make_queue(tmp_path, fights_provider=lambda: [])
    gate = queue.can_take_now()
    assert gate["can_take"] is True
    assert gate["blocked_by"] == []
    assert "fight_state_unknown" not in gate["blocked_by"]
    assert "fight_on_pgm" not in gate["blocked_by"]


async def test_injected_provider_empty_list_allows_auto_take(tmp_path) -> None:
    _svc, queue = _make_queue(
        tmp_path, mode="full_auto", fights_provider=lambda: []
    )
    entry = _enqueue(queue)
    taken = await queue.maybe_take_auto()
    assert taken is not None and taken.id == entry.id
    assert taken.state == "taken"


def test_injected_provider_with_fight_blocks_fight_on_pgm(tmp_path) -> None:
    fight = {"fight_id": "f9", "teams": ["X", "Y"]}
    _svc, queue = _make_queue(tmp_path, fights_provider=lambda: [fight])
    gate = queue.can_take_now()
    assert gate["can_take"] is False
    assert "fight_on_pgm" in gate["blocked_by"]
    assert "fight_state_unknown" not in gate["blocked_by"]


def test_injected_provider_raising_is_unknown_not_known_empty(tmp_path) -> None:
    def _boom():
        raise RuntimeError("provider down")

    _svc, queue = _make_queue(tmp_path, fights_provider=_boom)
    gate = queue.can_take_now()
    assert gate["can_take"] is False
    assert "fight_state_unknown" in gate["blocked_by"]
