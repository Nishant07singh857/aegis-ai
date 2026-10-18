# backend/tests/unit/test_observer_visuals.py
"""Unit tests for Phase 10 observer visuals services.

Covers:
  * PiPService: default state, partial set(), validation of
    inset_position / source keys, WS broadcast shape
    {"type": "pip.state", "data": {<full state>}}.
  * RundownService: add/update/reorder/remove segments, advance
    (start + step + past-end error), reset, WS broadcast shape
    {"type": "rundown.state", "data": {<full state>}}, current-index
    tracking across reorder/remove.

No network, no DB, no real credentials.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.pip import PiPService, PiPStateError
from app.services.pip.pip_service import WS_MESSAGE_TYPE as PIP_WS_TYPE
from app.services.rundown import RundownError, RundownService
from app.services.rundown.rundown_service import WS_MESSAGE_TYPE as RD_WS_TYPE


class _Listener:
    """Collects broadcast messages."""

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def __call__(self, message: Dict[str, Any]) -> None:
        self.messages.append(message)


# ──────────────────────────────────────────────────────────────
# PiP service
# ──────────────────────────────────────────────────────────────

pytestmark = pytest.mark.unit


def test_pip_default_state() -> None:
    service = PiPService()
    state = service.snapshot()
    assert state["enabled"] is False
    assert state["inset_position"] == "bottom_right"
    assert state["main_source"] == "output"
    assert state["inset_source"] == "map-cam"
    assert state["updated_at"]


async def test_pip_set_partial_and_broadcast() -> None:
    service = PiPService()
    listener = _Listener()
    service.add_listener(listener)

    state = await service.set(
        enabled=True, inset_position="top_left", updated_by="op-1"
    )
    assert state["enabled"] is True
    assert state["inset_position"] == "top_left"
    assert state["updated_by"] == "op-1"
    # Partial update: untouched fields keep their values.
    assert state["main_source"] == "output"
    assert state["inset_source"] == "map-cam"

    assert len(listener.messages) == 1
    frame = listener.messages[0]
    assert frame["type"] == PIP_WS_TYPE == "pip.state"
    assert frame["data"] == state


async def test_pip_set_second_update_keeps_enabled() -> None:
    service = PiPService()
    await service.set(enabled=True, updated_by="op-1")
    state = await service.set(inset_position="bottom_left", updated_by="op-1")
    assert state["enabled"] is True
    assert state["inset_position"] == "bottom_left"


async def test_pip_rejects_bad_inset_position() -> None:
    service = PiPService()
    with pytest.raises(PiPStateError):
        await service.set(inset_position="center", updated_by="op-1")
    # State untouched after a rejected write.
    assert service.snapshot()["inset_position"] == "bottom_right"


async def test_pip_rejects_bad_source_key() -> None:
    service = PiPService()
    with pytest.raises(PiPStateError):
        await service.set(main_source="twitch.com/stream", updated_by="op-1")
    with pytest.raises(PiPStateError):
        await service.set(inset_source="not-a-source", updated_by="op-1")
    assert service.snapshot()["main_source"] == "output"


async def test_pip_no_broadcast_on_rejected_write() -> None:
    service = PiPService()
    listener = _Listener()
    service.add_listener(listener)
    with pytest.raises(PiPStateError):
        await service.set(inset_position="nowhere", updated_by="op-1")
    assert listener.messages == []


def test_pip_listener_add_remove() -> None:
    service = PiPService()
    listener = _Listener()
    service.add_listener(listener)
    service.add_listener(listener)  # deduped
    service.remove_listener(listener)
    assert len(service._listeners) == 0


# ──────────────────────────────────────────────────────────────
# Rundown service
# ──────────────────────────────────────────────────────────────


async def _make_rundown() -> RundownService:
    service = RundownService()
    await service.add_segment(
        title="Pre-show desk", planned_duration_s=600, updated_by="producer"
    )
    await service.add_segment(
        title="Map 1 — Erangel",
        planned_duration_s=1800,
        notes="Flight path analysis first.",
        updated_by="producer",
    )
    await service.add_segment(
        title="Break", planned_duration_s=300, updated_by="producer"
    )
    return service


def test_rundown_add_segments_orders_them() -> None:
    service = RundownService()
    assert service.snapshot()["segments"] == []
    assert service.snapshot()["current_index"] == -1


async def test_rundown_add_order_and_ids() -> None:
    service = await _make_rundown()
    segments = service.snapshot()["segments"]
    assert [s["order"] for s in segments] == [0, 1, 2]
    assert [s["title"] for s in segments] == [
        "Pre-show desk",
        "Map 1 — Erangel",
        "Break",
    ]
    ids = [s["id"] for s in segments]
    assert len(set(ids)) == 3 and all(ids)
    assert segments[1]["notes"] == "Flight path analysis first."


async def test_rundown_add_validates_input() -> None:
    service = RundownService()
    with pytest.raises(RundownError):
        await service.add_segment(title="  ", planned_duration_s=60)
    with pytest.raises(RundownError):
        await service.add_segment(title="X", planned_duration_s=0)
    with pytest.raises(RundownError):
        await service.add_segment(title="X", planned_duration_s=-30)
    assert service.snapshot()["segments"] == []


async def test_rundown_add_at_explicit_order() -> None:
    service = await _make_rundown()
    seg = await service.add_segment(
        title="Opening bumper", planned_duration_s=60, order=0
    )
    titles = [s["title"] for s in service.snapshot()["segments"]]
    assert titles[0] == "Opening bumper"
    assert seg["order"] == 0
    assert [s["order"] for s in service.snapshot()["segments"]] == [0, 1, 2, 3]


async def test_rundown_update_segment() -> None:
    service = await _make_rundown()
    seg_id = service.snapshot()["segments"][0]["id"]
    updated = await service.update_segment(
        seg_id, title="Pre-show (extended)", planned_duration_s=900
    )
    assert updated["title"] == "Pre-show (extended)"
    assert updated["planned_duration_s"] == 900
    assert updated["id"] == seg_id


async def test_rundown_update_unknown_id() -> None:
    service = await _make_rundown()
    with pytest.raises(RundownError):
        await service.update_segment("nope", title="X")


async def test_rundown_advance_starts_and_steps() -> None:
    service = await _make_rundown()
    listener = _Listener()
    service.add_listener(listener)

    state = await service.advance(updated_by="producer")
    assert state["current_index"] == 0
    assert state["started_at"] is not None
    assert state["segment_started_at"] is not None

    state = await service.advance(updated_by="producer")
    assert state["current_index"] == 1
    titles = [s["title"] for s in state["segments"]]
    assert titles[state["current_index"]] == "Map 1 — Erangel"

    frames = [m for m in listener.messages if m["type"] == RD_WS_TYPE]
    assert len(frames) == 2
    assert frames[-1]["data"]["current_index"] == 1


async def test_rundown_advance_empty_and_past_end() -> None:
    service = RundownService()
    with pytest.raises(RundownError):
        await service.advance()

    service = await _make_rundown()
    await service.advance()
    await service.advance()
    await service.advance()
    assert service.snapshot()["current_index"] == 2
    with pytest.raises(RundownError):
        await service.advance()
    # Failed advance leaves the pointer alone.
    assert service.snapshot()["current_index"] == 2


async def test_rundown_reset_rewinds_but_keeps_segments() -> None:
    service = await _make_rundown()
    await service.advance()
    state = await service.reset(updated_by="producer")
    assert state["current_index"] == -1
    assert state["started_at"] is None
    assert state["segment_started_at"] is None
    assert len(state["segments"]) == 3


async def test_rundown_reorder_moves_segment() -> None:
    service = await _make_rundown()
    first_id = service.snapshot()["segments"][0]["id"]
    state = await service.reorder(first_id, 2, updated_by="producer")
    titles = [s["title"] for s in state["segments"]]
    assert titles == ["Map 1 — Erangel", "Break", "Pre-show desk"]
    assert [s["order"] for s in state["segments"]] == [0, 1, 2]


async def test_rundown_reorder_follows_current_segment() -> None:
    service = await _make_rundown()
    await service.advance()  # on "Pre-show desk" (index 0)
    first_id = service.snapshot()["segments"][0]["id"]
    state = await service.reorder(first_id, 2, updated_by="producer")
    assert state["current_index"] == 2
    assert state["segments"][state["current_index"]]["id"] == first_id


async def test_rundown_reorder_unknown_id() -> None:
    service = await _make_rundown()
    with pytest.raises(RundownError):
        await service.reorder("nope", 0)


async def test_rundown_remove_drops_segment() -> None:
    service = await _make_rundown()
    seg_id = service.snapshot()["segments"][1]["id"]
    state = await service.remove(seg_id, updated_by="producer")
    titles = [s["title"] for s in state["segments"]]
    assert titles == ["Pre-show desk", "Break"]
    assert [s["order"] for s in state["segments"]] == [0, 1]


async def test_rundown_remove_current_segment_slides_pointer() -> None:
    service = await _make_rundown()
    await service.advance()  # index 0
    seg_id = service.snapshot()["segments"][0]["id"]
    state = await service.remove(seg_id, updated_by="producer")
    # The segment that slid into place 0 is now current.
    assert state["current_index"] == 0
    assert state["segments"][0]["title"] == "Map 1 — Erangel"


async def test_rundown_remove_all_rewinds() -> None:
    service = RundownService()
    seg = await service.add_segment(title="Only", planned_duration_s=60)
    await service.advance()
    state = await service.remove(seg["id"], updated_by="producer")
    assert state["segments"] == []
    assert state["current_index"] == -1
    assert state["started_at"] is None


async def test_rundown_broadcast_shape_on_every_write() -> None:
    service = RundownService()
    listener = _Listener()
    service.add_listener(listener)
    seg = await service.add_segment(title="A", planned_duration_s=60)
    await service.update_segment(seg["id"], notes="n")
    await service.reorder(seg["id"], 0)
    await service.advance()
    await service.reset()
    assert len(listener.messages) == 5
    assert all(m["type"] == RD_WS_TYPE == "rundown.state" for m in listener.messages)
    for message in listener.messages:
        data = message["data"]
        assert "segments" in data
        assert "current_index" in data
        assert "segment_started_at" in data
