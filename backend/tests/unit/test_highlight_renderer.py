# backend/tests/unit/test_highlight_renderer.py
"""Unit tests for the Phase 21/T4 highlight video renderer.

Uses REAL FFmpeg output: a lavfi-generated test pattern video is the
source, and every render assertion is verified with ffprobe. Nothing is
mocked except the filesystem locations (tmp dirs).

  * full render: 16:9 MP4 (1280x720), 9:16 MP4 (720x1280), 3s GIF —
    files exist with the right dimensions/durations
  * registry persistence: a fresh renderer on the same dir sees
    the rendered record
  * no_source: empty recordings dir -> "no_source", NO files fabricated
  * failed mapping: epoch window with no known recording start ->
    "failed", no files
  * source discovery: match_id by filename, by sidecar match_id
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from app.services.replay.renderer import (
    HighlightRenderer,
    RenderRecord,
    find_source_video,
    probe,
)


def _gen_source(path: Path, seconds: int = 14) -> None:
    """Generate a real 1280x720 test-pattern video with ffmpeg (lavfi)."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[:500]
    assert path.exists()


@pytest.fixture()
def dirs(tmp_path):
    rec = tmp_path / "recordings"
    hl = tmp_path / "highlights"
    rec.mkdir()
    hl.mkdir()
    return rec, hl


# ── full real render ─────────────────────────────────────────────


def test_render_produces_three_real_files(dirs) -> None:
    rec_dir, hl_dir = dirs
    src = rec_dir / "match_abc123.mp4"
    _gen_source(src)

    renderer = HighlightRenderer(highlights_dir=hl_dir, recordings_dir=rec_dir)
    record = renderer.render(
        moment_id="moment-1",
        window_start=2.0,          # source-relative window
        window_end=7.0,
        match_id="abc123",
        pre_roll=1.0,
        post_roll=1.0,             # cut 1.0..8.0 = 7.0 s
    )

    assert record.status == "rendered", record.error
    assert set(record.files) == {"landscape", "portrait", "gif"}
    for kind, name in record.files.items():
        assert (hl_dir / name).exists(), kind

    land = probe(hl_dir / record.files["landscape"])
    port = probe(hl_dir / record.files["portrait"])
    gif = probe(hl_dir / record.files["gif"])

    assert land is not None and land["width"] == 1280 and land["height"] == 720
    assert port is not None and port["width"] == 720 and port["height"] == 1280
    assert gif is not None
    assert land["duration"] == pytest.approx(7.0, abs=0.6)
    assert port["duration"] == pytest.approx(7.0, abs=0.6)
    assert gif["duration"] == pytest.approx(3.0, abs=0.7)


def test_render_uses_sidecar_start_for_epoch_windows(dirs) -> None:
    rec_dir, hl_dir = dirs
    src = rec_dir / "match_xyz.mp4"
    _gen_source(src)
    started_at = time.time() - 120.0
    (rec_dir / "match_xyz.mp4.json").write_text(
        json.dumps({"match_id": "xyz", "started_at_epoch": started_at})
    )

    renderer = HighlightRenderer(highlights_dir=hl_dir, recordings_dir=rec_dir)
    record = renderer.render(
        moment_id="moment-2",
        window_start=started_at + 3.0,
        window_end=started_at + 8.0,
        match_id="xyz",
        pre_roll=0.0,
        post_roll=0.0,  # cut 3.0..8.0 = 5.0 s
    )
    assert record.status == "rendered", record.error
    land = probe(hl_dir / record.files["landscape"])
    assert land is not None and land["duration"] == pytest.approx(5.0, abs=0.6)


def test_registry_persists_across_instances(dirs) -> None:
    rec_dir, hl_dir = dirs
    _gen_source(rec_dir / "m.mp4")

    r1 = HighlightRenderer(highlights_dir=hl_dir, recordings_dir=rec_dir)
    r1.render("moment-3", 1.0, 4.0, pre_roll=0.0, post_roll=0.0)

    r2 = HighlightRenderer(highlights_dir=hl_dir, recordings_dir=rec_dir)
    loaded = r2.get_record("moment-3")
    assert loaded.status == "rendered"
    assert loaded.files, "rendered file names must survive a reload"
    for name in loaded.files.values():
        assert (hl_dir / name).exists()
    assert r2.file_path("moment-3", "landscape") is not None
    assert r2.get_record("never-seen").status == "not_requested"


# ── honest failure modes ─────────────────────────────────────────


def test_no_source_renders_nothing(dirs) -> None:
    rec_dir, hl_dir = dirs
    renderer = HighlightRenderer(highlights_dir=hl_dir, recordings_dir=rec_dir)
    record = renderer.render("moment-4", 10.0, 20.0, match_id="nope")
    assert record.status == "no_source"
    assert record.files == {}
    assert list(hl_dir.glob("*.mp4")) == [] and list(hl_dir.glob("*.gif")) == []
    # ... except the registry sidecar
    rendered_media = [p for p in hl_dir.iterdir() if p.name != "render_status.json"]
    assert rendered_media == []


def test_unknown_epoch_window_fails_honestly(dirs) -> None:
    rec_dir, hl_dir = dirs
    _gen_source(rec_dir / "m.mp4")

    renderer = HighlightRenderer(highlights_dir=hl_dir, recordings_dir=rec_dir)
    record = renderer.render(
        "moment-5",
        time.time() - 3600.0,  # epoch window, recording start unknowable
        time.time() - 3590.0,
        pre_roll=0.0,
        post_roll=0.0,
    )
    # creation_time tag of the lavfi file resolves the start, and the
    # window maps outside the 14 s source -> failed; if the tag were
    # absent, the unknown offset also fails. Either way: no guessing.
    assert record.status == "failed"
    assert record.error
    assert record.files == {}


# ── source discovery ─────────────────────────────────────────────


def test_find_source_by_filename(dirs) -> None:
    rec_dir, _ = dirs
    a = rec_dir / "match_alpha.mp4"
    b = rec_dir / "match_beta.mp4"
    a.touch()
    b.touch()
    assert find_source_video("beta", rec_dir) == b
    assert find_source_video("gamma", rec_dir) is None


def test_find_source_by_sidecar_match_id(dirs) -> None:
    rec_dir, _ = dirs
    a = rec_dir / "random_name.mp4"
    a.touch()
    (rec_dir / "random_name.mp4.json").write_text(
        json.dumps({"match_id": "m-99"})
    )
    assert find_source_video("m-99", rec_dir) == a
    assert find_source_video("other", rec_dir) is None


def test_find_source_ambiguous_without_match_id(dirs) -> None:
    rec_dir, _ = dirs
    (rec_dir / "a.mp4").touch()
    (rec_dir / "b.mp4").touch()
    # two recordings, no match_id: refuse to guess
    assert find_source_video(None, rec_dir) is None
    single = dirs[0]
    for p in single.iterdir():
        p.unlink()
    only = single / "only.mp4"
    only.touch()
    assert find_source_video(None, single) == only


def test_explicit_source_path_wins(dirs, tmp_path) -> None:
    rec_dir, _ = dirs
    (rec_dir / "match_m.mp4").touch()
    explicit = tmp_path / "elsewhere.mp4"
    explicit.touch()
    assert find_source_video("m", rec_dir, source_path=str(explicit)) == explicit
    assert find_source_video("m", rec_dir, source_path="/does/not/exist.mp4") is None
