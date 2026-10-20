# backend/tests/unit/test_voice_routing.py
"""Unit tests for the Phase 21/T5a Piper voice routing.

Routing rule: English lines -> English voice/profile; Hindi and
Hinglish lines -> Hindi voice/profile. Voices that are not on disk are
reported unavailable and synthesis stays honestly disabled for them.

  * language detection: Devanagari, Hinglish markers, plain English
  * per-language voice file resolution in a tmp voices dir
  * legacy AEGIS_TTS_MODEL_PATH fallback when no per-language file
  * VoiceSynthesizer.synthesize() picks the right profile per language
  * the shipped voices dir: availability must match what is on disk
    (the Hindi voice; the English voice when it has been downloaded)
"""

from __future__ import annotations

import os

import pytest

from app.agents.base_agent import AgentConfig
from app.agents.commentary_agents.voice_synthesizer import (
    PIPER_VOICE_FILES,
    VoiceSynthesizer,
    available_piper_voices,
    default_voices_dir,
    detect_text_language,
    piper_voice_path,
    resolve_piper_model_path,
)
from app.core.event_bus import EventBus


# ── language detection ───────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("स्काउट ने क्या खेला है, कमाल कर दिया!", "hi"),
        ("SCOUT KYA KHEL RAHA HAI! 54 FINISHES!", "hi"),
        ("Yeh match toh bahut hi kamaal ka raha hai", "hi"),
        ("What a brilliant rotation by Team Soul into the final circle", "en"),
        ("Jonathan picks up the M416 and holds the bridge beautifully", "en"),
        ("What a play, hai na?", "en"),  # single marker: not enough evidence
    ],
)
def test_detect_text_language(text: str, expected: str) -> None:
    assert detect_text_language(text) == expected


# ── voice file resolution ────────────────────────────────────────


@pytest.fixture()
def voices_dir(tmp_path):
    # Only the Hindi voice installed in this fixture dir.
    (tmp_path / PIPER_VOICE_FILES["hi"]).write_bytes(b"fake-onnx-for-path-tests")
    (tmp_path / (PIPER_VOICE_FILES["hi"] + ".json")).write_text("{}")
    return tmp_path


def test_voice_path_resolution(voices_dir) -> None:
    assert piper_voice_path("hi", voices_dir) is not None
    assert piper_voice_path("en", voices_dir) is None  # not installed here
    assert piper_voice_path("xx", voices_dir) is None
    assert available_piper_voices(voices_dir) == {"hi": True, "en": False}


def test_resolve_falls_back_to_legacy_env_model(voices_dir, tmp_path, monkeypatch) -> None:
    legacy = tmp_path / "legacy-voice.onnx"
    legacy.write_bytes(b"x")
    monkeypatch.setenv("AEGIS_TTS_MODEL_PATH", str(legacy))
    # English voice is not installed in voices_dir -> legacy single model
    assert resolve_piper_model_path("en", voices_dir) == str(legacy)
    # Installed per-language voice wins over the legacy path
    assert resolve_piper_model_path("hi", voices_dir) == str(
        voices_dir / PIPER_VOICE_FILES["hi"]
    )


def test_resolve_none_without_voice_or_env(voices_dir, monkeypatch) -> None:
    monkeypatch.delenv("AEGIS_TTS_MODEL_PATH", raising=False)
    assert resolve_piper_model_path("en", voices_dir) is None


def test_shipped_voices_dir_matches_disk() -> None:
    """Availability reporting must mirror reality, never claim a file."""
    voices = available_piper_voices()
    assert set(voices) == {"hi", "en"}
    for language, present in voices.items():
        path = default_voices_dir() / PIPER_VOICE_FILES[language]
        assert present == path.exists()
    # The Hindi voice ships with the repo (docs/AUDIO.md) and must exist.
    assert voices["hi"] is True


# ── synthesize() profile routing ─────────────────────────────────


async def test_synthesize_routes_profile_by_language(monkeypatch) -> None:
    monkeypatch.delenv("AEGIS_TTS_ENGINE", raising=False)
    synth = VoiceSynthesizer(
        AgentConfig(agent_id="voice-routing-test"), EventBus()
    )
    seen_profiles = []

    async def _capture(text, profile, voice_params):
        seen_profiles.append(profile.profile_id)
        return None  # routing is what is under test, not synthesis

    monkeypatch.setenv("AEGIS_TTS_ENGINE", "capture")
    # _call_tts_engine returns None for unknown engines; intercept after
    # the profile decision point instead by patching the dispatcher.
    orig = synth._call_tts_engine

    async def _dispatch(text, profile, voice_params):
        seen_profiles.append(profile.profile_id)
        return None

    synth._call_tts_engine = _dispatch  # type: ignore[method-assign]

    await synth.synthesize("What a brilliant clutch from Jonathan!")
    await synth.synthesize("KYA KHEL RAHA HAI SCOUT! KAMAAL KAR DIYA!")

    assert seen_profiles == ["english_analyst", "hindi_caster_1"]


async def test_synthesize_explicit_profile_wins(monkeypatch) -> None:
    synth = VoiceSynthesizer(
        AgentConfig(agent_id="voice-routing-test-2"), EventBus()
    )
    seen_profiles = []

    async def _dispatch(text, profile, voice_params):
        seen_profiles.append(profile.profile_id)
        return None

    synth._call_tts_engine = _dispatch  # type: ignore[method-assign]
    # English text but an explicit Hindi profile: explicit always wins.
    await synth.synthesize(
        "A calm English line", profile_id="hinglish_caster"
    )
    assert seen_profiles == ["hinglish_caster"]
