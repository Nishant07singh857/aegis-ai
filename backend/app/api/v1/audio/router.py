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
        text=payload.text, profile_id=payload.profile_id, style=payload.style
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
