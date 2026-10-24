"""AEGIS AI - AICommentaryStudio (DORMANT / NOT IMPLEMENTED).

Honest status (inspection H17): this module does NOT synthesise any
voice. There is no XTTS v2 integration and no ElevenLabs integration
in this file — the synthesis branches were commented-out / ``pass``
stubs, yet the old code logged "Voice synthesis complete" and returned
a ``/tmp/live_cast_*.wav`` path for a file that was never created.
That fabrication is removed.

Every synthesis entry point here now returns an explicit honest
failure: ``synthesized=False`` with a reason, and no audio path.

The real, working TTS in this project is local Piper via the agents'
``VoiceSynthesizer`` — use that path; do not wire this dormant studio
into a live broadcast path.
"""

import structlog

logger = structlog.get_logger("aegis.intelligence.voice")

VOICE_STUDIO_UNAVAILABLE_REASON = (
    "AICommentaryStudio is dormant: no voice-synthesis backend is "
    "implemented in this module (no XTTS v2, no ElevenLabs). "
    "Real TTS is Piper via the agents' VoiceSynthesizer."
)


class AICommentaryStudio:
    """Dormant voice-studio stub — synthesises nothing.

    Kept only so existing imports do not silently change shape. All
    methods report failure honestly (``synthesized=False`` + reason)
    instead of returning a path to a file that does not exist.
    """

    def __init__(self, mode: str = "xtts_local"):
        self.mode = mode
        self.target_voice_sample = "dataset/audio/actual_match_01.mp3"
        logger.warning(
            "AICommentaryStudio initialised but dormant: no synthesis "
            "backend is implemented (requested mode=%s). %s",
            mode,
            VOICE_STUDIO_UNAVAILABLE_REASON,
        )

    def generate_casting_audio(self, text: str, emotion: str = "hype") -> dict:
        """Attempt casting-audio generation — always honestly fails.

        Returns:
            dict with ``synthesized=False``, ``audio_path=None`` and a
            ``reason``. No file is created and no success is logged.
        """
        logger.warning(
            "Voice synthesis unavailable (dormant studio); no audio produced",
            emotion=emotion,
            text_length=len(text),
            reason=VOICE_STUDIO_UNAVAILABLE_REASON,
        )
        return {
            "synthesized": False,
            "audio_path": None,
            "path": None,
            "mode": self.mode,
            "emotion": emotion,
            "reason": VOICE_STUDIO_UNAVAILABLE_REASON,
        }

    async def cast_live_event(self, event_type: str, caster_text: str):
        """Handle a live casting request — always honestly fails.

        Returns the same failure dict as ``generate_casting_audio``;
        nothing is played into any broadcast stream.
        """
        emotion = "hype" if event_type in ["squad_wipe", "clutch"] else "analytical"
        result = self.generate_casting_audio(caster_text, emotion)
        logger.warning(
            "cast_live_event not performed: voice studio dormant",
            event_type=event_type,
            reason=result["reason"],
        )
        return result


# ── Usage Example ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    studio = AICommentaryStudio(mode="xtts_local")

    text_to_speak = "Jonathan is pushing from the left flank! He spots two players, can he secure the wipe? Yes he does! What a phenomenal play!"

    result = studio.generate_casting_audio(text=text_to_speak, emotion="hype")
    print(result)
