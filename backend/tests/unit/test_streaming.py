# backend/tests/unit/test_streaming.py
"""Unit tests for the Phase 5 automated streaming service.

Covers:
  * RTMP destination URL builders (correct URLs, missing-key rejection,
    unknown destinations, custom URL validation, no key leakage)
  * FFmpeg capture command builder (single vs tee output, video-only flag)
  * StreamService state machine (idle→starting→running→stopping→stopped,
    crash→bounded restart→failed) with FFmpeg/subprocess fully mocked.

No network, no real binaries, no real stream keys.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from app.streaming.destinations import (
    CUSTOM_KEY_ENV,
    FACEBOOK_KEY_ENV,
    TWITCH_KEY_ENV,
    YOUTUBE_KEY_ENV,
    DestinationError,
    build_destination_urls,
    custom_url,
    facebook_url,
    key_present,
    twitch_url,
    youtube_url,
)
from app.streaming.service import StreamConfig, StreamService, StreamState

KEY_ENVS = (YOUTUBE_KEY_ENV, TWITCH_KEY_ENV, FACEBOOK_KEY_ENV, CUSTOM_KEY_ENV)


@pytest.fixture(autouse=True)
def _clean_key_env(monkeypatch):
    """Stream keys must never exist outside the test that sets them."""
    for env in KEY_ENVS:
        monkeypatch.delenv(env, raising=False)


# --------------------------------------------------------------------- #
# RTMP destination builders
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestDestinationBuilders:
    def test_youtube_url(self, monkeypatch):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "  yt-key-123  ")
        assert youtube_url() == "rtmp://a.rtmp.youtube.com/live2/yt-key-123"

    def test_twitch_url(self, monkeypatch):
        monkeypatch.setenv(TWITCH_KEY_ENV, "tw-key")
        assert twitch_url() == "rtmp://live.twitch.tv/app/tw-key"

    def test_facebook_url(self, monkeypatch):
        monkeypatch.setenv(FACEBOOK_KEY_ENV, "fb-key")
        assert facebook_url() == "rtmps://live-api-s.facebook.com:443/rtmp/fb-key"

    def test_custom_url(self, monkeypatch):
        monkeypatch.setenv(CUSTOM_KEY_ENV, "custom-key")
        assert (
            custom_url("rtmp://ingest.example.com/live/")
            == "rtmp://ingest.example.com/live/custom-key"
        )

    @pytest.mark.parametrize(
        "builder,env",
        [
            (youtube_url, YOUTUBE_KEY_ENV),
            (twitch_url, TWITCH_KEY_ENV),
            (facebook_url, FACEBOOK_KEY_ENV),
        ],
    )
    def test_missing_key_raises_naming_variable(self, builder, env):
        """Missing key → ValueError naming the env var, never a key value."""
        with pytest.raises(DestinationError) as excinfo:
            builder()
        assert env in str(excinfo.value)
        assert isinstance(excinfo.value, ValueError)

    def test_blank_key_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "   ")
        with pytest.raises(DestinationError):
            youtube_url()
        assert not key_present(YOUTUBE_KEY_ENV)

    def test_custom_rejects_non_rtmp_scheme(self, monkeypatch):
        monkeypatch.setenv(CUSTOM_KEY_ENV, "k")
        with pytest.raises(DestinationError):
            custom_url("https://example.com/live")

    def test_custom_rejects_missing_key(self):
        with pytest.raises(DestinationError) as excinfo:
            custom_url("rtmp://ingest.example.com/live")
        assert CUSTOM_KEY_ENV in str(excinfo.value)

    def test_build_multiple_destinations(self, monkeypatch):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt")
        monkeypatch.setenv(TWITCH_KEY_ENV, "tw")
        built = build_destination_urls(["youtube", "twitch"])
        assert [name for name, _ in built] == ["youtube", "twitch"]
        assert built[0][1] == "rtmp://a.rtmp.youtube.com/live2/yt"

    def test_build_unknown_destination_rejected(self, monkeypatch):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt")
        with pytest.raises(DestinationError):
            build_destination_urls(["youtube", "kick"])

    def test_build_requires_at_least_one(self):
        with pytest.raises(DestinationError):
            build_destination_urls([])

    def test_build_custom_requires_url(self, monkeypatch):
        monkeypatch.setenv(CUSTOM_KEY_ENV, "k")
        with pytest.raises(DestinationError):
            build_destination_urls(["custom"])

    def test_key_present_reads_env_at_call_time(self, monkeypatch):
        assert not key_present(YOUTUBE_KEY_ENV)
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "late-key")
        assert key_present(YOUTUBE_KEY_ENV)


# --------------------------------------------------------------------- #
# FFmpeg command builder
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestFfmpegCommandBuilder:
    def _cmd(self, urls):
        return StreamService.build_ffmpeg_command(
            display=":99",
            width=1280,
            height=720,
            fps=30,
            video_bitrate="4500k",
            x264_preset="veryfast",
            urls=urls,
        )

    def test_video_only_no_fake_audio(self):
        cmd = self._cmd(["rtmp://a.rtmp.youtube.com/live2/k"])
        assert "-an" in cmd  # honest: no TTS audio exists, no silent track faked
        assert "libx264" in cmd
        assert "-i" in cmd and ":99" in cmd
        assert "1280x720" in cmd

    def test_single_destination_uses_flv_directly(self):
        url = "rtmp://a.rtmp.youtube.com/live2/k"
        cmd = self._cmd([url])
        assert cmd[-3:] == ["-f", "flv", url]
        assert "tee" not in cmd

    def test_multiple_destinations_use_tee_muxer(self):
        urls = [
            "rtmp://a.rtmp.youtube.com/live2/k1",
            "rtmp://live.twitch.tv/app/k2",
        ]
        cmd = self._cmd(urls)
        tee_index = cmd.index("tee")
        assert cmd[tee_index - 1] == "-f"
        tee_arg = cmd[tee_index + 1]
        # tee syntax: [f=flv]<escaped-url>|[f=flv]<escaped-url>; ':' escaped
        assert tee_arg.count("|") == 1
        assert "\\:" in tee_arg

    def test_no_urls_rejected(self):
        with pytest.raises(ValueError):
            self._cmd([])


# --------------------------------------------------------------------- #
# StreamService state machine (subprocess fully mocked)
# --------------------------------------------------------------------- #


class FakePopen:
    """Minimal subprocess.Popen stand-in. No real processes are spawned."""

    instances: list = []
    auto_crash: bool = False  # when True, poll() reports exit code 1
    _next_pid = 5000

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.pid = FakePopen._next_pid
        FakePopen._next_pid += 1
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.waited = False
        FakePopen.instances.append(self)

    def poll(self):
        if self.returncode is None and FakePopen.auto_crash:
            self.returncode = 1
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


@pytest.fixture
def _mock_binaries(monkeypatch):
    """Pretend ffmpeg / Xvfb / chromium all exist; capture every Popen."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    FakePopen.instances = []
    FakePopen.auto_crash = False
    FakePopen._next_pid = 5000
    monkeypatch.setattr(subprocess, "Popen", FakePopen)


def _test_service(**kwargs) -> StreamService:
    cfg = StreamConfig(
        monitor_interval=0.01,
        restart_backoff_base=0.01,
        stop_timeout=0.1,
        **kwargs,
    )
    return StreamService(config=cfg)


@pytest.mark.unit
class TestStreamServiceLifecycle:
    async def test_start_runs_full_pipeline(self, _mock_binaries, monkeypatch):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt-key")
        svc = _test_service()
        assert svc.state == StreamState.IDLE

        result = await svc.start(["youtube"])

        assert svc.state == StreamState.RUNNING
        assert result["state"] == "running"
        assert result["destinations_requested"] == ["youtube"]
        # Xvfb + Chromium + FFmpeg all spawned
        assert len(FakePopen.instances) == 3
        assert FakePopen.instances[0].cmd[0] == "Xvfb"
        assert "ffmpeg" in FakePopen.instances[2].cmd[0]
        await svc.stop()

    async def test_start_refuses_missing_key_without_spawning(
        self, _mock_binaries
    ):
        svc = _test_service()
        with pytest.raises(DestinationError):
            await svc.start(["youtube"])  # YOUTUBE_STREAM_KEY unset
        assert svc.state == StreamState.IDLE  # validation happens before STARTING
        assert FakePopen.instances == []

    async def test_start_refuses_when_already_running(
        self, _mock_binaries, monkeypatch
    ):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt-key")
        svc = _test_service()
        await svc.start(["youtube"])
        with pytest.raises(RuntimeError, match="already running"):
            await svc.start(["youtube"])
        await svc.stop()

    async def test_start_names_missing_binary(self, _mock_binaries, monkeypatch):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt-key")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        svc = _test_service()
        with pytest.raises(RuntimeError) as excinfo:
            await svc.start(["youtube"])
        assert "chromium" in str(excinfo.value).lower()
        assert svc.state == StreamState.IDLE
        assert FakePopen.instances == []

    async def test_stop_reaps_everything_no_zombies(
        self, _mock_binaries, monkeypatch
    ):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt-key")
        svc = _test_service()
        await svc.start(["youtube"])
        procs = list(FakePopen.instances)

        result = await svc.stop()

        assert result["state"] == "stopped"
        assert svc.state == StreamState.STOPPED
        for proc in procs:
            assert proc.terminated, "terminate() was never called"
            assert proc.waited, "wait() was never called — zombie risk"
        assert svc._monitor_task is None

    async def test_stop_is_idempotent(self, _mock_binaries):
        svc = _test_service()
        assert (await svc.stop())["state"] == "idle"

    async def test_restart_after_stop(self, _mock_binaries, monkeypatch):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt-key")
        svc = _test_service()
        await svc.start(["youtube"])
        await svc.stop()
        await svc.start(["youtube"])
        assert svc.state == StreamState.RUNNING
        await svc.stop()

    async def test_status_never_contains_keys(
        self, _mock_binaries, monkeypatch
    ):
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "super-secret-yt")
        monkeypatch.setenv(TWITCH_KEY_ENV, "super-secret-tw")
        svc = _test_service()
        await svc.start(["youtube", "twitch"])
        status = svc.status()
        blob = json.dumps(status)
        assert "super-secret-yt" not in blob
        assert "super-secret-tw" not in blob
        assert status["destinations_configured"]["youtube"] is True
        assert status["destinations_configured"]["twitch"] is True
        assert status["destinations_configured"]["facebook"] is False
        assert status["ffmpeg_alive"] is True
        assert status["ffmpeg_pid"] is not None
        await svc.stop()


@pytest.mark.unit
class TestStreamServiceCrashRecovery:
    async def _wait_for_state(self, svc, want, timeout=5.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while svc.state != want:
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError(
                    f"timed out waiting for state {want}; now {svc.state}"
                )
            await asyncio.sleep(0.01)

    async def test_crash_restarts_then_recovers(
        self, _mock_binaries, monkeypatch
    ):
        """One crash → one bounded restart; a healthy process keeps running."""
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt-key")
        svc = _test_service(max_restarts=3)
        await svc.start(["youtube"])
        first_ffmpeg = FakePopen.instances[2]

        first_ffmpeg.returncode = 1  # crash exactly once
        await asyncio.sleep(0.15)  # monitor poll + backoff + relaunch

        assert svc.state == StreamState.RUNNING
        assert svc._restart_count == 1
        assert len(FakePopen.instances) > 3  # pipeline was relaunched
        await svc.stop()

    async def test_repeated_crashes_exhaust_budget_then_failed(
        self, _mock_binaries, monkeypatch
    ):
        """Persistent crashes → FAILED after max_restarts, with last_error set."""
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt-key")
        svc = _test_service(max_restarts=2)
        await svc.start(["youtube"])

        FakePopen.auto_crash = True  # every spawned ffmpeg dies immediately
        await self._wait_for_state(svc, StreamState.FAILED)

        assert svc._restart_count == 2  # bounded: never more than max_restarts
        assert svc._last_error is not None
        assert "restart budget" in svc._last_error
        assert svc.status()["state"] == "failed"

    async def test_launch_failure_becomes_failed_state(
        self, _mock_binaries, monkeypatch
    ):
        """If spawning itself throws, the service lands in FAILED (not STARTING)."""
        monkeypatch.setenv(YOUTUBE_KEY_ENV, "yt-key")

        def boom(cmd, **kwargs):
            raise OSError("spawn exploded")

        monkeypatch.setattr(subprocess, "Popen", boom)
        svc = _test_service()
        with pytest.raises(OSError):
            await svc.start(["youtube"])
        assert svc.state == StreamState.FAILED
        assert "spawn exploded" in svc._last_error


# --------------------------------------------------------------------- #
# API schemas
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestStreamSchemas:
    def test_valid_start_request(self):
        from app.api.v1.stream.schemas import StreamStartRequest

        req = StreamStartRequest(
            destinations=["youtube", "custom"],
            custom_rtmp_url="rtmp://ingest.example.com/live",
            resolution="1080p",
        )
        assert req.resolution == "1080p"

    def test_unknown_destination_rejected(self):
        from app.api.v1.stream.schemas import StreamStartRequest

        with pytest.raises(ValidationError):
            StreamStartRequest(destinations=["kick"])

    def test_duplicate_destinations_rejected(self):
        from app.api.v1.stream.schemas import StreamStartRequest

        with pytest.raises(ValidationError):
            StreamStartRequest(destinations=["youtube", "youtube"])

    def test_unknown_resolution_rejected(self):
        from app.api.v1.stream.schemas import StreamStartRequest

        with pytest.raises(ValidationError):
            StreamStartRequest(destinations=["youtube"], resolution="4k")

    def test_status_response_has_no_key_fields(self):
        from app.api.v1.stream.schemas import StreamStatusResponse

        fields = set(StreamStatusResponse.model_fields)
        assert not any("key" in f for f in fields), f"key-like field leaked: {fields}"
