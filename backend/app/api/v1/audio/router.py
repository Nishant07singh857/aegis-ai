"""Audio API router — mounted at /api/v1 by app/main.py.

The broadcast audio pipeline:
  * TTS commentary (Piper, local/free) -> WAV clips + PCM FIFO feed
  * game audio from the observer phone (scrcpy route or manual cable)
  * software mixer (commentary volume, game volume hint, master mute)

Auth mirrors the stream slice: reads need any authenticated user,
writes need operator+. Stream keys never appear here — there are none;
TTS is fully offline.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.api.deps import get_current_user, require_operator
from app.audio.commentary_feed import get_commentary_feed
from app.audio.game_audio import get_game_audio_capture
from app.audio.mixer import get_audio_mixer

from .schemas import (
    AudioStatusResponse,
    ClipInfo,
    CommentaryLineRequest,
    CommentaryLineResponse,
    CommentaryModeRequest,
    FeedActionResponse,
    GameAudioActionResponse,
    MixerStateResponse,
    MixerUpdateRequest,
)

router = APIRouter()


# ------------------------------------------------------------------ #
# status
# ------------------------------------------------------------------ #
@router.get("/audio/status", response_model=AudioStatusResponse)
async def audio_status(_user=Depends(get_current_user)):
    feed = get_commentary_feed()
    mixer = get_audio_mixer()
    game = get_game_audio_capture()
    return AudioStatusResponse(
        tts=feed.tts_status(),
        mixer=MixerStateResponse(**mixer.to_dict()),
        feed=feed.status(),
        game_audio=game.status(),
    )


# ------------------------------------------------------------------ #
# commentary TTS
# ------------------------------------------------------------------ #
@router.post(
    "/audio/commentary",
    response_model=CommentaryLineResponse,
    dependencies=[Depends(require_operator)],
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_commentary_line(payload: CommentaryLineRequest):
    """Synthesize one commentary line (Piper, offline) and queue it to the feed."""
    feed = get_commentary_feed()
    result = await feed.enqueue_line(
        text=payload.text,
        profile_id=payload.profile_id,
        style=payload.style,
        language=payload.language,
        match_id=payload.match_id,
        priority=payload.priority,
    )
    return CommentaryLineResponse(**result)


@router.get("/audio/clips", response_model=list[ClipInfo])
async def list_clips(limit: int = 20, _user=Depends(get_current_user)):
    feed = get_commentary_feed()
    return [ClipInfo(**c) for c in feed.recent_clips(limit=min(max(limit, 1), 50))]


@router.get("/audio/clip/{clip_id}")
async def download_clip(clip_id: str, _user=Depends(get_current_user)):
    feed = get_commentary_feed()
    path = feed.clip_wav_path(clip_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    return FileResponse(path, media_type="audio/wav", filename=f"{clip_id}.wav")


# ------------------------------------------------------------------ #
# mixer
# ------------------------------------------------------------------ #
@router.get("/audio/mixer", response_model=MixerStateResponse)
async def get_mixer(_user=Depends(get_current_user)):
    return MixerStateResponse(**get_audio_mixer().to_dict())


@router.post("/audio/mixer", response_model=MixerStateResponse)
async def set_mixer(payload: MixerUpdateRequest, _user=Depends(require_operator)):
    mixer = get_audio_mixer()
    state = mixer.set_levels(
        commentary_volume=payload.commentary_volume,
        game_volume=payload.game_volume,
        master_mute=payload.master_mute,
    )
    return MixerStateResponse(**state)


# ------------------------------------------------------------------ #
# commentary PCM feed (FIFO for FFmpeg)
# ------------------------------------------------------------------ #
@router.post("/audio/feed/start", response_model=FeedActionResponse)
async def start_feed(_user=Depends(require_operator)):
    return FeedActionResponse(**get_commentary_feed().start())


@router.post("/audio/feed/stop", response_model=FeedActionResponse)
async def stop_feed(_user=Depends(require_operator)):
    return FeedActionResponse(**get_commentary_feed().stop())


# ------------------------------------------------------------------ #
# Phase 22: commentary modes / subtitles / suggestions / latency
# ------------------------------------------------------------------ #
@router.get("/audio/commentary-mode")
async def get_commentary_mode(match_id: str | None = None, _user=Depends(get_current_user)):
    from app.audio.commentary_modes import get_commentary_mode_service

    return get_commentary_mode_service().describe(match_id)


@router.post("/audio/commentary-mode", dependencies=[Depends(require_operator)])
async def set_commentary_mode(payload: CommentaryModeRequest, user=Depends(require_operator)):
    from app.audio.commentary_modes import CommentaryMode, get_commentary_mode_service

    try:
        mode = CommentaryMode(payload.mode)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown commentary mode '{payload.mode}'. Valid: "
            + ", ".join(m.value for m in CommentaryMode),
        )
    service = get_commentary_mode_service()
    service.set_mode(mode, match_id=payload.match_id, updated_by=user.username)
    return service.describe(payload.match_id)


@router.get("/audio/captions")
async def list_captions(limit: int = 20, _user=Depends(get_current_user)):
    from app.audio.subtitles import get_subtitle_store

    return {"captions": get_subtitle_store().latest(limit=min(max(limit, 1), 100))}


@router.get("/audio/captions/current")
async def current_caption(language: str | None = None, _user=Depends(get_current_user)):
    from app.audio.subtitles import get_subtitle_store

    return {"caption": get_subtitle_store().current(language)}


@router.get("/audio/subtitles/{language}.vtt")
async def subtitle_webvtt(language: str, _user=Depends(get_current_user)):
    from fastapi.responses import PlainTextResponse

    from app.audio.subtitles import get_subtitle_store

    if language not in ("hi", "en"):
        raise HTTPException(status_code=404, detail="Subtitle track: hi or en only.")
    return PlainTextResponse(
        get_subtitle_store().webvtt(language), media_type="text/vtt"
    )


@router.get("/audio/suggestions")
async def list_suggestions(limit: int = 20, _user=Depends(require_operator)):
    """Human Assist text suggestions for the human caster (operator+)."""
    return {"suggestions": get_commentary_feed().suggestions(limit=min(max(limit, 1), 50))}


@router.get("/audio/latency")
async def latency_stats(_user=Depends(get_current_user)):
    return get_commentary_feed().latency_stats()


@router.post("/audio/fallback-pack/build", dependencies=[Depends(require_operator)])
async def build_fallback_pack():
    """Pre-render the generic-line fallback pack with Piper (idempotent)."""
    from app.audio.fallback_pack import build_fallback_pack as _build

    return await _build()


@router.post("/audio/presynth/warm", dependencies=[Depends(require_operator)])
async def warm_presynth():
    """Predictive pre-synthesis warm-up for the active mode's languages."""
    from app.audio.commentary_modes import get_commentary_mode_service
    from app.audio.fallback_pack import warm_presynthesis

    behavior = get_commentary_mode_service().behavior()
    langs = [behavior.primary_language]
    if behavior.secondary_language:
        langs.append(behavior.secondary_language)
    feed = get_commentary_feed()
    warmed = await feed._get_synth().warm_models()
    pack = await warm_presynthesis(langs)
    return {"voices_warmed": warmed, "presynthesis": pack}


# ------------------------------------------------------------------ #
# game audio capture
# ------------------------------------------------------------------ #
@router.get("/audio/game/status")
async def game_status(_user=Depends(get_current_user)):
    return get_game_audio_capture().status()


@router.post("/audio/game/start", response_model=GameAudioActionResponse)
async def start_game_audio(_user=Depends(require_operator)):
    result = get_game_audio_capture().start_scrcpy()
    if not result["recording"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=result.get("error", "Cannot start game audio capture.")
        )
    return GameAudioActionResponse(**result)


@router.post("/audio/game/stop", response_model=GameAudioActionResponse)
async def stop_game_audio(_user=Depends(require_operator)):
    return GameAudioActionResponse(**get_game_audio_capture().stop())
