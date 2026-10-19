"""AEGIS AI — audio pipeline (Phase: audio).

commentary_feed: commentary text -> Piper TTS WAV clips + a continuous
    48 kHz stereo PCM stream on a FIFO for FFmpeg to pick up.
mixer:          software volume/mute state for commentary + game audio.
game_audio:     honest probe + launcher for observer-phone game audio
    (scrcpy audio forwarding on Android 11+, else manual 3.5mm cable).
"""
