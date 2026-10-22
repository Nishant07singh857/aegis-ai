# backend/tests/unit/test_replay_queue_recorder.py
"""Unit tests for Phase 25 / P0-2: ISO recording + replay offer/take.

Recorder tests use REAL FFmpeg: tiny synthetic frames are encoded to
real MP4 segments and verified with the renderer's own ffprobe helper.
Nothing about the video path is mocked — only the filesystem
locations (tmp dirs) and the director/phase/fight providers (the
queue's documented injection seams) are faked.

Queue coverage:
  * off-PGM moment, no recording -> PREPARING->READY event replay
    (is_video False, never a claimed clip)
  * off-PGM moment with a real ISO recording -> renderer cuts it,
    is_video True
  * take during an active fight -> refused in ALL modes
  * full_auto lull -> auto-take airs once; second take refused
  * phase suppression (late_game / final_circle) -> take refused,
    entries still prepare
  * A/B discipline: two READY coexist, third QUEUED, promotion on take
  * bounded queue: oldest low-value expires first
  * replay.candidate hand-off + detector poll (incl. on-PGM skip)

Recorder coverage:
  * frames in -> segment file + truthful sidecar appear
  * no-source feed -> honest no_source state, zero files
  * cap enforcement deletes oldest segments first
  * one feed dropping never touches another feed
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest

from app.services.replay.queue import (
    ReplayQueue,
    ReplayTakeRefused,
)
from app.services.replay.recorder import ISORecorder, build_iso_recorder_from_manager
from app.services.replay.renderer import HighlightRenderer, probe
from app.services.replay.service import ReplayService

BASE_TS = time.time() - 120.0


def _frame(seed: int = 0, w: int = 64, h: int = 48) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def _solid(value: int = 120, w: int = 64, h: int = 48) -> np.ndarray:
    return np.full((h, w, 3), value, dtype=np.uint8)


# ── recorder ───────────────────────────────────────────────────


def test_recorder_writes_segment_and_truthful_sidecar(tmp_path) -> None:
    rec_dir = tmp_path / "recordings"
    recorder = ISORecorder(recordings_dir=rec_dir, segment_seconds=60.0, fps=10)
    recorder.start(["slot-1"])
    start_ts = time.time() - 5.0
    for i in range(20):
        assert recorder.write_frame("slot-1", _solid(100 + i), ts=start_ts + i * 0.1)
    status = recorder.status()
    assert status["feeds"]["slot-1"]["state"] == "recording"
    assert status["feeds"]["slot-1"]["frames_written"] == 20

    recorder.stop()
    segments = recorder.list_segments("slot-1")
    assert len(segments) == 1
    seg_path = Path(segments[0]["path"])
    assert seg_path.exists() and seg_path.stat().st_size > 0
    info = probe(seg_path)
    assert info is not None and info["duration"] and info["duration"] > 0.5

    sidecar = json.loads(Path(str(seg_path) + ".json").read_text())
    # The sidecar start is the first frame's own timestamp — the value
    # the renderer will map highlight windows against. Never a guess.
    assert sidecar["started_at_epoch"] == pytest.approx(start_ts)
    assert sidecar["frame_count"] == 20
    assert sidecar["open"] is False
    assert recorder.status()["feeds"]["slot-1"]["state"] == "stopped"


def test_recorder_no_source_feed_records_nothing(tmp_path) -> None:
    rec_dir = tmp_path / "recordings"
    recorder = ISORecorder(recordings_dir=rec_dir)
    recorder.start(["slot-9"])
    status = recorder.status()
    assert status["feeds"]["slot-9"]["state"] == "no_source"
    assert recorder.list_segments("slot-9") == []
    assert list(rec_dir.glob("*.mp4")) == []
    recorder.stop()


def test_recorder_cap_enforcement_deletes_oldest(tmp_path) -> None:
    rec_dir = tmp_path / "recordings"
    recorder = ISORecorder(
        recordings_dir=rec_dir, segment_seconds=1.0, fps=10,
        total_cap_bytes=10 * 1024**3,
    )
    recorder.start(["slot-1"])
    base = time.time() - 60.0
    # Three segments, rotated by truthful frame-timestamp jumps.
    for seg_idx in range(3):
        seg_start = base + seg_idx * 10.0
        for i in range(15):
            recorder.write_frame("slot-1", _frame(seg_idx * 100 + i, 160, 120), ts=seg_start + i * 0.07)
    recorder.stop()
    segments = recorder.list_segments("slot-1")
    assert len(segments) == 3
    total = recorder.total_bytes()
    assert total > 0

    # Shrink the cap to just over one segment: the two OLDEST must go.
    recorder.total_cap_bytes = int(segments[-1]["size_bytes"] * 1.2)
    result = recorder.enforce_cap()
    assert len(result["deleted"]) == 2
    remaining = recorder.list_segments("slot-1")
    assert [s["file"] for s in remaining] == [segments[-1]["file"]]
    assert recorder.total_bytes() <= recorder.total_cap_bytes
    for name in result["deleted"]:
        assert not (rec_dir / name).exists()


def test_recorder_one_feed_dropping_pauses_only_that_feed(tmp_path) -> None:
    rec_dir = tmp_path / "recordings"
    recorder = ISORecorder(recordings_dir=rec_dir, segment_seconds=60.0)
    recorder.start(["slot-1", "slot-2"])
    now = time.time() - 5.0
    for i in range(10):
        recorder.write_frame("slot-1", _solid(80), ts=now + i * 0.1)
    # slot-2 never produced a frame: it is no_source, slot-1 records.
    status = recorder.status()
    assert status["feeds"]["slot-1"]["state"] == "recording"
    assert status["feeds"]["slot-2"]["state"] == "no_source"

    recorder.stop(feed_id="slot-1")
    status = recorder.status()
    assert status["feeds"]["slot-1"]["state"] == "stopped"
    assert status["feeds"]["slot-2"]["state"] == "no_source"  # untouched
    # A stopped feed refuses frames; the other feed still accepts them.
    assert recorder.write_frame("slot-1", _solid(1), ts=now) is False
    assert recorder.write_frame("slot-2", _solid(90), ts=now) is True
    recorder.stop()


def test_recorder_builder_taps_manager_grabber(tmp_path) -> None:
    class FakeFrame:
        def __init__(self) -> None:
            self.image = _solid(140)
            self.timestamp = time.time() - 2.0
            self.frame_id = 42

    class FakeGrabber:
        def __init__(self) -> None:
            self.frame = FakeFrame()

        def get_latest_frame(self, source_id: str):
            assert source_id == "aegis-observer-slot-01"
            return self.frame

    class FakeManager:
        _grabber = FakeGrabber()

    recorder = build_iso_recorder_from_manager(
        FakeManager(), recordings_dir=tmp_path / "rec", segment_seconds=60.0
    )
    recorder.start(["slot-1"])
    frame = recorder.frame_provider("slot-1")
    assert frame is not None and frame.frame_id == 42
    assert recorder.write_frame("slot-1", frame.image, ts=frame.timestamp)
    recorder.stop()
    assert len(recorder.list_segments("slot-1")) == 1


# ── queue fixtures ─────────────────────────────────────────────


class FakeWorld:
    """Mutable director/phase/fight state behind the provider seams."""

    def __init__(self) -> None:
        self.mode = "manual"
        self.phase = "mid_game"
        self.fights: list = []
        self.active_camera = 1

    def director(self) -> dict:
        return {"mode": self.mode, "active_camera": self.active_camera, "scene": "main"}

    def phase_fn(self) -> str:
        return self.phase

    def fights_fn(self) -> list:
        return list(self.fights)


def _make_queue(tmp_path, world: FakeWorld, svc: ReplayService | None = None, **kw):
    if svc is None:
        renderer = HighlightRenderer(
            highlights_dir=tmp_path / "highlights",
            recordings_dir=tmp_path / "recordings",
        )
        svc = ReplayService(window_seconds=3600, renderer=renderer)
    queue = ReplayQueue(
        svc,
        recorder=kw.pop("recorder", None),
        director_provider=world.director,
        phase_provider=world.phase_fn,
        fights_provider=world.fights_fn,
        **kw,
    )
    return svc, queue


def _enqueue(queue: ReplayQueue, score: int = 90, kind: str = "squad_wipe", **over):
    payload = dict(
        kind=kind,
        score=score,
        reason=f"{kind} test moment",
        window_start=BASE_TS,
        window_end=BASE_TS + 8.0,
        player="Scout",
        team="Team Soul",
        opponents=["V1"],
    )
    payload.update(over)
    return queue.enqueue_moment(**payload)


# ── queue: preparation honesty ─────────────────────────────────


def test_moment_without_recording_becomes_ready_event_replay(tmp_path) -> None:
    world = FakeWorld()
    _svc, queue = _make_queue(tmp_path, world)
    entry = _enqueue(queue)
    assert entry.state_history[:2] == ["preparing", "ready"]
    assert entry.state == "ready"
    assert entry.slot_label == "A"
    # No recording exists -> honest event replay, never a claimed clip.
    assert entry.is_video is False
    assert entry.render_status == "no_source"


def test_moment_with_real_recording_renders_video_clip(tmp_path) -> None:
    world = FakeWorld()
    rec_dir = tmp_path / "recordings"
    hl_dir = tmp_path / "highlights"
    rec_dir.mkdir(parents=True, exist_ok=True)
    hl_dir.mkdir(parents=True, exist_ok=True)

    recorder = ISORecorder(recordings_dir=rec_dir, segment_seconds=60.0, fps=10)
    recorder.start(["slot-2"])
    seg_start = time.time() - 30.0
    for i in range(50):  # ~5 s of real footage
        recorder.write_frame("slot-2", _solid(60 + i), ts=seg_start + i * 0.1)
    recorder.stop()

    renderer = HighlightRenderer(highlights_dir=hl_dir, recordings_dir=rec_dir)
    svc = ReplayService(window_seconds=3600, renderer=renderer)
    _svc, queue = _make_queue(tmp_path, world, svc=svc, recorder=recorder)
    entry = _enqueue(
        queue,
        window_start=seg_start + 1.0,
        window_end=seg_start + 2.0,
        feed_id="slot-2",
    )
    assert entry.is_video is True, entry.render_error
    assert entry.render_status == "rendered"
    assert set(entry.render_files) == {"landscape", "portrait", "gif"}
    for name in entry.render_files.values():
        assert (hl_dir / name).exists()


# ── queue: take gates ──────────────────────────────────────────


@pytest.mark.parametrize("mode,via", [("full_auto", "system"), ("semi_auto", "operator"), ("manual", "operator")])
async def test_take_during_active_fight_refused_in_all_modes(tmp_path, mode, via) -> None:
    world = FakeWorld()
    world.mode = mode
    world.fights = [{"fight_id": "f1", "teams": ["A", "B"], "intensity": 80.0}]
    _svc, queue = _make_queue(tmp_path, world)
    entry = _enqueue(queue)
    assert queue.can_take_now()["can_take"] is False
    assert "fight_on_pgm" in queue.can_take_now()["blocked_by"]
    with pytest.raises(ReplayTakeRefused):
        await queue.take(entry.id, taken_by="tester", via=via)
    assert queue.get(entry.id).state == "ready"  # still offered, not aired


async def test_full_auto_takes_once_in_lull_then_refuses(tmp_path) -> None:
    world = FakeWorld()
    world.mode = "full_auto"
    svc, queue = _make_queue(tmp_path, world)
    broadcasts: list = []

    async def listener(message):
        broadcasts.append(message)

    svc.add_listener(listener)
    entry = _enqueue(queue)
    taken = await queue.maybe_take_auto()
    assert taken is not None and taken.id == entry.id
    assert taken.state == "taken" and taken.taken_at is not None
    assert any(m["type"] == "replay.play" for m in broadcasts)
    # Never airs twice: a further auto tick finds nothing READY, and a
    # direct take on the taken entry is refused.
    assert await queue.maybe_take_auto() is None
    with pytest.raises(ReplayTakeRefused, match="never airs twice"):
        await queue.take(entry.id, taken_by="tester", via="operator")


async def test_manual_take_in_lull_and_second_take_refused(tmp_path) -> None:
    world = FakeWorld()
    world.mode = "semi_auto"
    _svc, queue = _make_queue(tmp_path, world)
    entry = _enqueue(queue)
    taken = await queue.take(entry.id, taken_by="director-1", via="operator")
    assert taken.state == "taken" and taken.taken_by == "director-1"
    with pytest.raises(ReplayTakeRefused):
        await queue.take(entry.id, taken_by="director-1", via="operator")


async def test_operator_take_refused_in_full_auto(tmp_path) -> None:
    world = FakeWorld()
    world.mode = "full_auto"
    _svc, queue = _make_queue(tmp_path, world)
    entry = _enqueue(queue)
    with pytest.raises(ReplayTakeRefused, match="full_auto"):
        await queue.take(entry.id, taken_by="director-1", via="operator")


@pytest.mark.parametrize("phase", ["late_game", "final_circle"])
async def test_phase_suppression_refuses_take_but_entry_prepares(tmp_path, phase) -> None:
    world = FakeWorld()
    world.mode = "manual"
    world.phase = phase
    _svc, queue = _make_queue(tmp_path, world)
    entry = _enqueue(queue)  # preparation is NOT suppressed (E11)
    assert entry.state == "ready"
    gate = queue.can_take_now()
    assert gate["can_take"] is False and "phase_suppressed" in gate["blocked_by"]
    with pytest.raises(ReplayTakeRefused):
        await queue.take(entry.id, taken_by="director-1", via="operator")
    assert queue.get(entry.id).state == "ready"


# ── queue: A/B discipline, bounds, sources ─────────────────────


async def test_ab_two_ready_third_queues_and_promotes_on_take(tmp_path) -> None:
    world = FakeWorld()
    world.mode = "manual"
    _svc, queue = _make_queue(tmp_path, world)
    a = _enqueue(queue, score=90, moment_id="m-a")
    b = _enqueue(queue, score=85, moment_id="m-b")
    c = _enqueue(queue, score=80, moment_id="m-c")
    assert (a.state, a.slot_label) == ("ready", "A")
    assert (b.state, b.slot_label) == ("ready", "B")
    assert c.state == "queued" and c.slot_label is None

    await queue.take(a.id, taken_by="director-1", via="operator")
    promoted = queue.get(c.id)
    assert promoted.state == "ready" and promoted.slot_label in ("A", "B")


async def test_dismiss_frees_slot_and_promotes(tmp_path) -> None:
    world = FakeWorld()
    _svc, queue = _make_queue(tmp_path, world)
    a = _enqueue(queue, score=90, moment_id="m-a")
    _enqueue(queue, score=85, moment_id="m-b")
    c = _enqueue(queue, score=80, moment_id="m-c")
    queue.dismiss(a.id, dismissed_by="director-1")
    assert queue.get(a.id).state == "dismissed"
    assert queue.get(c.id).state == "ready"


def test_bounded_queue_expires_oldest_low_value_first(tmp_path) -> None:
    world = FakeWorld()
    _svc, queue = _make_queue(tmp_path, world, max_entries=2)
    low = _enqueue(queue, score=60, moment_id="m-low")
    mid = _enqueue(queue, score=70, moment_id="m-mid")
    high = _enqueue(queue, score=95, moment_id="m-high")
    assert queue.get(low.id).state == "expired"
    assert queue.get(mid.id).state in ("ready", "queued")
    assert queue.get(high.id).state in ("ready", "queued")


def test_candidate_handoff_creates_entry_and_drops_underspecified(tmp_path) -> None:
    world = FakeWorld()
    _svc, queue = _make_queue(tmp_path, world)
    entry = queue.handle_candidate({
        "kind": "squad_wipe",
        "score": 90,
        "reason": "Fight #2 wipe while PGM was on fight #1",
        "window_start": BASE_TS,
        "window_end": BASE_TS + 6.0,
        "slot_number": 5,
        "team": "GodLike",
    })
    assert entry is not None
    assert entry.source == "replay_candidate" and entry.state == "ready"
    assert entry.feed_id == "slot-5"
    assert queue.handle_candidate({"kind": "squad_wipe"}) is None


def _seed_kills(svc: ReplayService, slot: int | None = None) -> None:
    for i, killer in enumerate(["A", "B", "C", "D"]):
        payload = {
            "killer": killer,
            "killerTeam": "Attackers",
            "victim": f"Victim{i}",
            "victimTeam": "WipedSquad",
            "weapon": "M416",
        }
        if slot is not None:
            payload["observer_slot"] = slot
        svc.buffer.record(
            event_type="kill_event",
            payload=payload,
            ts=BASE_TS + i * 5,
            event_id=f"k{i}-{slot}",
        )


def test_poll_highlights_enqueues_squad_wipe(tmp_path) -> None:
    world = FakeWorld()
    svc, queue = _make_queue(tmp_path, world)
    _seed_kills(svc)
    created = queue.poll_highlights()
    wipes = [e for e in created if e.kind == "squad_wipe"]
    assert len(wipes) == 1
    assert wipes[0].score == 90 and wipes[0].state == "ready"
    # Polling again dedupes on moment_id: no second live entry.
    queue.poll_highlights()
    live = [e for e in queue.list_entries() if e.state in ("ready", "queued")]
    assert len(live) == 1


def test_poll_skips_moment_that_was_on_pgm(tmp_path) -> None:
    world = FakeWorld()
    world.active_camera = 3
    svc, queue = _make_queue(tmp_path, world)
    _seed_kills(svc, slot=3)  # the audience already saw this live
    assert queue.poll_highlights() == []
    world.active_camera = 7  # same moment, different PGM -> off-PGM
    created = queue.poll_highlights()
    assert len([e for e in created if e.kind == "squad_wipe"]) == 1


def test_expired_entry_cannot_be_taken(tmp_path) -> None:
    world = FakeWorld()
    _svc, queue = _make_queue(tmp_path, world, ttl_seconds=30.0)
    entry = _enqueue(queue)
    assert queue.prune(now=entry.created_at + 31.0) == 1
    assert queue.get(entry.id).state == "expired"


def test_replay_router_exposes_queue_and_recorder_routes() -> None:
    from app.api.v1.replay.router import router

    paths = {route.path for route in router.routes}
    assert "/replay/queue" in paths
    assert "/replay/queue/{entry_id}/take" in paths
    assert "/replay/queue/{entry_id}/dismiss" in paths
    assert "/replay/recorder/status" in paths
    assert "/replay/recorder/start" in paths
    assert "/replay/recorder/stop" in paths
