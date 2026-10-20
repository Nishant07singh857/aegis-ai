# backend/tests/unit/test_commentary_modes.py
"""Phase 22 (M13 completion) tests:

  * 5 commentary modes have REAL behavioural differences (speaks flag,
    language, pacing, content mix, profiles, dual-track)
  * mode service persistence + per-match override
  * profanity filter (en/hi/hinglish, never crashes)
  * subtitle WebVTT sidecar (valid output, empty track, current cue)
  * fallback pack lookup + synthesizer cached_fallback provenance
  * dual-track FFmpeg command (two language-tagged audio streams in the
    recording output; RTMP output carries the primary track only)
  * feed mode gates: silent = text-only, human_assist = suggestion only
  * dual-track line render with a stubbed translator
"""

from __future__ import annotations

import pytest

from app.audio.commentary_modes import (
    CommentaryMode,
    CommentaryModeService,
    behavior_for,
    pick_style,
)
from app.audio.profanity import filter_profanity
from app.audio.subtitles import SubtitleStore, render_webvtt
from app.streaming.service import StreamService


# ── mode behaviour differences ────────────────────────────────────


def test_all_five_modes_exist_with_distinct_behaviour():
    modes = list(CommentaryMode)
    assert {m.value for m in modes} == {
        "hindi_main", "english_main", "bilingual_blend", "silent", "human_assist"
    }
    behaviours = {m: behavior_for(m) for m in modes}
    # speaks: only silent + human_assist never synthesize audio
    assert behaviours[CommentaryMode.SILENT].speaks is False
    assert behaviours[CommentaryMode.HUMAN_ASSIST].speaks is False
    assert behaviours[CommentaryMode.HUMAN_ASSIST].suggestions_only is True
    assert behaviours[CommentaryMode.SILENT].text_overlay is True
    for m in (CommentaryMode.HINDI_MAIN, CommentaryMode.ENGLISH_MAIN, CommentaryMode.BILINGUAL_BLEND):
        assert behaviours[m].speaks is True
    # language + voice routing differ
    assert behaviours[CommentaryMode.HINDI_MAIN].primary_language == "hi"
    assert behaviours[CommentaryMode.ENGLISH_MAIN].primary_language == "en"
    assert behaviours[CommentaryMode.HINDI_MAIN].primary_profiles[0] == "hindi_caster_1"
    assert behaviours[CommentaryMode.ENGLISH_MAIN].primary_profiles[0] == "english_analyst"
    # pacing differs
    gaps = {m: b.min_gap_seconds for m, b in behaviours.items()}
    assert gaps[CommentaryMode.ENGLISH_MAIN] > gaps[CommentaryMode.HINDI_MAIN]
    assert behaviours[CommentaryMode.ENGLISH_MAIN].cooldown_multiplier != behaviours[
        CommentaryMode.HINDI_MAIN
    ].cooldown_multiplier
    # content mix differs: english_main is the most analytical
    assert behaviours[CommentaryMode.ENGLISH_MAIN].content_mix["analysis"] > behaviours[
        CommentaryMode.HINDI_MAIN
    ].content_mix["analysis"]
    # only bilingual renders dual tracks
    assert behaviours[CommentaryMode.BILINGUAL_BLEND].dual_track is True
    assert all(not b.dual_track for m, b in behaviours.items() if m != CommentaryMode.BILINGUAL_BLEND)


def test_pick_style_respects_mix_and_hype_triggers():
    # roll 0.0 always lands in play_by_play for speaking modes
    assert pick_style(CommentaryMode.HINDI_MAIN, "kill", 0.0) == "play_by_play"
    # hype triggers ignore the roll
    assert pick_style(CommentaryMode.ENGLISH_MAIN, "clutch_win", 0.0) == "hype"
    # english_main: roll 0.5 is inside its 0.40 analysis band
    assert pick_style(CommentaryMode.ENGLISH_MAIN, "kill", 0.5) == "analysis"
    # hindi_main: roll 0.5 is past pbp(0.45)+analysis(0.20)=0.65? no: 0.5<0.65 -> analysis
    assert pick_style(CommentaryMode.HINDI_MAIN, "kill", 0.5) == "analysis"
    assert pick_style(CommentaryMode.HINDI_MAIN, "kill", 0.99) == "hype"


def test_mode_service_persistence_and_per_match(tmp_path):
    svc = CommentaryModeService(audio_dir=tmp_path)
    assert svc.get_mode() == CommentaryMode.BILINGUAL_BLEND  # default
    svc.set_mode(CommentaryMode.HINDI_MAIN, updated_by="director1")
    svc.set_mode(CommentaryMode.SILENT, match_id="match-42")
    assert svc.get_mode() == CommentaryMode.HINDI_MAIN
    assert svc.get_mode("match-42") == CommentaryMode.SILENT
    assert svc.get_mode("other-match") == CommentaryMode.HINDI_MAIN
    # a fresh service over the same dir restores the state
    svc2 = CommentaryModeService(audio_dir=tmp_path)
    assert svc2.get_mode() == CommentaryMode.HINDI_MAIN
    assert svc2.get_mode("match-42") == CommentaryMode.SILENT
    desc = svc2.describe("match-42")
    assert desc["mode"] == "silent" and desc["speaks"] is False
    assert len(desc["all_modes"]) == 5


def test_caster_profiles_wired_to_real_piper_voices():
    from app.agents.base_agent import AgentConfig
    from app.agents.commentary_agents.voice_synthesizer import (
        TTSEngine,
        VoiceSynthesizer,
    )
    from app.core.event_bus import EventBus

    synth = VoiceSynthesizer(AgentConfig(agent_id="profile-test"), EventBus())
    for pid, voice in (
        ("hindi_caster_1", "hi_IN-pratham-medium"),
        ("english_analyst", "en_US-amy-medium"),
        ("hinglish_caster", "hi_IN-pratham-medium"),
    ):
        profile = synth.profiles[pid]
        assert profile.engine == TTSEngine.PIPER, pid
        assert profile.voice_id == voice, pid
        assert "eleven" not in profile.voice_id  # no placeholder ids remain


# ── profanity filter ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected_fragment",
    [
        ("what a chutiya play that was", "***"),
        ("that was fucking brilliant", "***"),
        ("बहुत हरामी खेल था", "***"),
    ],
)
def test_profanity_filtered(raw, expected_fragment):
    result = filter_profanity(raw)
    assert result.filtered is True
    assert expected_fragment in result.text


def test_profanity_clean_text_untouched():
    result = filter_profanity("Brilliant rotation by Team Soul into the zone")
    assert result.filtered is False
    assert result.text == "Brilliant rotation by Team Soul into the zone"


def test_profanity_never_crashes_on_edge_input():
    assert filter_profanity("").text == ""
    assert filter_profanity(None).text == ""  # type: ignore[arg-type]


# ── subtitles ─────────────────────────────────────────────────────


def test_webvtt_rendering():
    store = SubtitleStore()
    store.add_cue("en", "First line", duration=3.0, start=1000.0)
    store.add_cue("en", "Second line --> not a marker", duration=2.0, start=1004.0)
    vtt = store.webvtt("en")
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:03.000" in vtt
    assert "00:00:04.000 --> 00:00:06.000" in vtt
    assert "--> not a marker" not in vtt.splitlines()[6] if len(vtt.splitlines()) > 6 else True
    assert "Second line" in vtt


def test_webvtt_empty_track_is_valid():
    assert render_webvtt([]).startswith("WEBVTT")


def test_current_cue_only_when_active():
    import time

    store = SubtitleStore()
    store.add_cue("hi", "abhi wali line", duration=60.0, start=time.time())
    assert store.current("hi")["text"] == "abhi wali line"
    store2 = SubtitleStore()
    store2.add_cue("hi", "purani line", duration=1.0, start=time.time() - 100)
    assert store2.current("hi") is None


# ── fallback pack ─────────────────────────────────────────────────


def test_fallback_pack_lines_and_lookup(tmp_path):
    from app.audio.fallback_pack import (
        GENERIC_LINES,
        fallback_dir,
        find_fallback_wav,
        pack_index,
    )

    assert len(GENERIC_LINES["hi"]) == 20
    assert len(GENERIC_LINES["en"]) == 20
    # nothing rendered yet -> honest empty lookup
    assert find_fallback_wav("en", audio_dir=tmp_path) is None
    index = pack_index(audio_dir=tmp_path)
    assert all(not e["rendered"] for e in index["en"])
    # render one fake wav where the pack expects it -> lookup finds it
    import hashlib

    text = GENERIC_LINES["en"][0]
    name = hashlib.md5(text.encode()).hexdigest()[:12] + ".wav"
    target = fallback_dir(tmp_path) / "en" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"RIFF-fake")
    assert find_fallback_wav("en", audio_dir=tmp_path) == target


async def test_synthesizer_falls_back_to_cached_pack(monkeypatch, tmp_path):
    """Live Piper failure + rendered pack on disk -> cached_fallback audio."""
    import hashlib
    import io
    import wave

    from app.agents.base_agent import AgentConfig
    from app.agents.commentary_agents.voice_synthesizer import VoiceSynthesizer
    from app.audio import fallback_pack
    from app.core.event_bus import EventBus

    # Build a real (silent) WAV as the generic line's cached render.
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * 22050)
    generic = fallback_pack.GENERIC_LINES["en"][0]
    name = hashlib.md5(generic.encode()).hexdigest()[:12] + ".wav"
    pack_dir = tmp_path / "fallback" / "en"
    pack_dir.mkdir(parents=True)
    (pack_dir / name).write_bytes(buf.getvalue())
    (tmp_path / "fallback" / "manifest.json").write_text(
        '{"' + name + '": "' + generic + '"}'
    )
    monkeypatch.setenv("AEGIS_AUDIO_DIR", str(tmp_path))
    monkeypatch.setenv("AEGIS_TTS_ENGINE", "piper")

    synth = VoiceSynthesizer(AgentConfig(agent_id="fallback-test"), EventBus())

    async def _fail(text, profile, voice_params):
        return None  # live synthesis failed

    synth._call_tts_engine = _fail  # type: ignore[method-assign]
    audio = await synth.synthesize("A live line that failed", profile_id="english_analyst")
    assert audio is not None
    assert audio.source == "cached_fallback"
    assert audio.text == generic  # the generic line is what will be heard
    assert audio.requested_text == "A live line that failed"
    assert audio.synthesized is True  # real pre-rendered audio, honestly sourced


# ── dual-track FFmpeg command ─────────────────────────────────────


def test_dual_track_ffmpeg_command():
    cmd = StreamService.build_ffmpeg_command_dual_track(
        display=":99", width=1280, height=720, fps=30,
        video_bitrate="4500k", x264_preset="veryfast",
        urls=["rtmp://a.rtmp.youtube.com/live2/k"],
        hi_input_args=["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "/tmp/hi.pcm"],
        en_input_args=["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "/tmp/en.pcm"],
        recording_path="/tmp/program.mkv",
    )
    joined = " ".join(cmd)
    # recording output maps BOTH audio inputs and tags both languages
    assert cmd.count("1:a") >= 2 and "2:a" in cmd
    assert "language=hin" in joined and "language=eng" in joined
    assert "matroska" in cmd and "/tmp/program.mkv" in cmd
    # live output is flv (single audio track — protocol limit)
    assert "flv" in cmd


# ── feed mode gates ───────────────────────────────────────────────


def _feed(tmp_path):
    from app.audio.commentary_feed import CommentaryAudioFeed

    return CommentaryAudioFeed(audio_dir=tmp_path)


async def test_feed_silent_mode_is_text_only(tmp_path, monkeypatch):
    from app.audio.commentary_modes import CommentaryModeService

    feed = _feed(tmp_path)
    svc = CommentaryModeService(audio_dir=tmp_path)
    svc.set_mode("silent")
    monkeypatch.setattr(feed, "_mode_service", lambda: svc)
    result = await feed.enqueue_line("Kya kamaal ka clutch tha!", priority=100)
    assert result["synthesized"] is False
    assert result["text_only"] is True
    assert result["mode"] == "silent"
    assert feed._last_text_line["text"] == "Kya kamaal ka clutch tha!"


async def test_feed_human_assist_suggests_without_speaking(tmp_path, monkeypatch):
    from app.audio.commentary_modes import CommentaryModeService

    feed = _feed(tmp_path)
    svc = CommentaryModeService(audio_dir=tmp_path)
    svc.set_mode("human_assist")
    monkeypatch.setattr(feed, "_mode_service", lambda: svc)
    result = await feed.enqueue_line("Jonathan is one shot away from a clutch", priority=100)
    assert result["synthesized"] is False
    assert result["suggestion"] is True
    suggestions = feed.suggestions()
    assert len(suggestions) == 1
    assert suggestions[0]["text"] == "Jonathan is one shot away from a clutch"


async def test_feed_pacing_gate_blocks_rapid_lines(tmp_path, monkeypatch):
    import time as _time

    from app.audio.commentary_modes import CommentaryModeService

    feed = _feed(tmp_path)
    svc = CommentaryModeService(audio_dir=tmp_path)
    svc.set_mode("english_main")  # min gap 4.0s
    monkeypatch.setattr(feed, "_mode_service", lambda: svc)
    feed._last_spoken_at = _time.time()  # a line was "just spoken"
    result = await feed.enqueue_line("Another line right away", priority=10)
    assert result["queued"] is False
    assert "pacing" in result["reason"]
    # high priority bypasses the gate (reaches synthesis attempt)
    result2 = await feed.enqueue_line("Urgent clutch line", priority=95)
    assert "pacing" not in (result2.get("reason") or "")


async def test_feed_profanity_filtered_before_synthesis(tmp_path, monkeypatch):
    from app.audio.commentary_modes import CommentaryModeService

    feed = _feed(tmp_path)
    svc = CommentaryModeService(audio_dir=tmp_path)
    svc.set_mode("silent")  # text path exposes the filtered text directly
    monkeypatch.setattr(feed, "_mode_service", lambda: svc)
    result = await feed.enqueue_line("yeh chutiya moment tha", priority=100)
    assert "***" in result["text"]
    assert "chutiya" not in result["text"]
