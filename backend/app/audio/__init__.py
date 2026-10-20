"""AEGIS AI — audio pipeline (Phase: audio; Phase 22: commentary completion).

commentary_feed: commentary text -> Piper TTS WAV clips + a continuous
    48 kHz stereo PCM stream on a FIFO for FFmpeg to pick up; Phase 22
    adds per-language track FIFOs, mode gating, subtitle cues, latency
    samples and dual-track program muxing.
mixer:          software volume/mute state for commentary + game audio.
game_audio:     honest probe + launcher for observer-phone game audio
    (scrcpy audio forwarding on Android 11+, else manual 3.5mm cable).
commentary_modes: the 5 PRD modes (Hindi Main / English Main / Bilingual
    Blend / Silent / Human Assist) with real behavioural differences.
profanity:      curated en/hi/hinglish profanity filter (pre-TTS/display).
subtitles:      WebVTT sidecar + caption cue store from commentary lines.
fallback_pack:  pre-rendered generic-line failover pack + predictive
    pre-synthesis warm-up (latency mitigation).
"""
