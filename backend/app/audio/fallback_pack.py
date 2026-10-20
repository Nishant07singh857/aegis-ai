"""AEGIS AI — fallback cached audio pack + predictive pre-synthesis
(Phase 22 / M13).

Two related caches of REAL Piper-rendered audio:

1. **Fallback pack** — ~20 generic lines per language, pre-rendered with
   Piper into ``<audio_dir>/fallback/<lang>/``. When live synthesis fails
   (engine error, voice missing mid-match), the synthesizer/feed falls
   back to the closest generic line and reports it honestly with
   ``source="cached_fallback"`` (and ``synthesized=False`` semantics:
   the line's own text was NOT synthesized — a cached generic line was
   substituted). Nothing is faked: if the pack is empty, there is no
   fallback.

2. **Predictive pre-synthesis cache** — at match start (and on mode
   change) the high-probability lines for the active mode are rendered
   ahead of time so a live event can play a cached clip with ~0 ms
   synthesis latency. Warm-up also loads each Piper voice once at boot
   so the first live line does not pay the ~seconds model-load cost.

Generic lines are deliberately content-free (no team/player names) so a
fallback can never state a false fact about the match.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import structlog

from .mixer import default_audio_dir

logger = structlog.get_logger("aegis.audio.fallback_pack")

#: 20 generic lines per language. No names, no scores — always safe.
GENERIC_LINES: Dict[str, List[str]] = {
    "hi": [
        "Kya kamaal ka khel chal raha hai yahan!",
        "Dekhte hain aage kya hota hai, match bahut interesting hai!",
        "Teams apni positions mazboot kar rahi hain!",
        "Zone ka pressure badhta ja raha hai!",
        "Har team chicken dinner ke liye poori koshish kar rahi hai!",
        "Yeh pal match ka rukh badal sakta hai!",
        "Khiladiyon ka focus dekhne layak hai!",
        "Ab har decision bahut important hai!",
        "Crowd ke liye yeh match yaadgaar ban raha hai!",
        "Kya timing, kya positioning, zabardast khel!",
        "Agle kuch pal bahut crucial hone wale hain!",
        "Dono taraf se shandaar khel dekhne ko mil raha hai!",
        "Survival hi ab sabse badi priority hai!",
        "Ek galti aur poora match palat sakta hai!",
        "Teams zone ke andar jagah banane ki koshish mein hain!",
        "Yeh fight match ki kahani likh sakti hai!",
        "Bahut hi soojh-boojh bhara khel chal raha hai!",
        "Har knock yahan sona hai, har finish keemat rakhta hai!",
        "End circle tak ka safar aasan nahi hoga!",
        "Khel ab apne sabse romanchak mod par hai!",
    ],
    "en": [
        "What a fantastic game we are witnessing here!",
        "Let's see what happens next, this match is wide open!",
        "Teams are locking down their positions now!",
        "The zone pressure keeps building with every phase!",
        "Every team is fighting hard for that chicken dinner!",
        "This moment could turn the entire match around!",
        "The focus from these players is incredible to watch!",
        "Every decision matters from this point onward!",
        "This is turning into a match to remember!",
        "Superb timing and positioning on display!",
        "The next few moments are going to be crucial!",
        "Brilliant gameplay from both sides right now!",
        "Survival is the top priority at this stage!",
        "One mistake and this match could flip completely!",
        "Teams are fighting for space inside the zone!",
        "This fight could write the story of the match!",
        "Really smart, calculated gameplay on show here!",
        "Every knock is gold, every finish counts!",
        "The road to the final circle will not be easy!",
        "The game is at its most exciting phase right now!",
    ],
}

#: High-probability lines pre-rendered per mode at match start. These are
#: the generic pack plus a few mode-flavoured staples — all name-free.
PRESYNTH_EXTRA: Dict[str, List[str]] = {
    "hi": [
        "Fight shuru ho gayi hai, sab ki nazar yahin hai!",
        "Ek aur shandaar finish dekhne ko mila!",
    ],
    "en": [
        "A fight has broken out, all eyes on this one!",
        "Another brilliant finish for the highlight reel!",
    ],
}


def fallback_dir(audio_dir: Optional[Path] = None) -> Path:
    return (audio_dir or default_audio_dir()) / "fallback"


def _slug(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def pack_index(audio_dir: Optional[Path] = None) -> Dict[str, List[dict]]:
    """What is actually on disk in the fallback pack (honest inventory)."""
    base = fallback_dir(audio_dir)
    out: Dict[str, List[dict]] = {}
    for lang, lines in GENERIC_LINES.items():
        entries = []
        for text in lines:
            path = base / lang / f"{_slug(text)}.wav"
            entries.append(
                {"text": text, "path": str(path), "rendered": path.exists()}
            )
        out[lang] = entries
    return out


def find_fallback_wav(
    language: str, audio_dir: Optional[Path] = None
) -> Optional[Path]:
    """Any rendered generic line for a language (rotates by time so the
    same clip does not repeat back-to-back), or None if pack is empty."""
    base = fallback_dir(audio_dir) / (language or "en")
    if not base.exists():
        return None
    wavs = sorted(base.glob("*.wav"))
    if not wavs:
        return None
    return wavs[int(time.time()) % len(wavs)]


def fallback_text_for(path: Path, audio_dir: Optional[Path] = None) -> Optional[str]:
    """Original generic text a fallback WAV was rendered from."""
    manifest = fallback_dir(audio_dir) / "manifest.json"
    try:
        if manifest.exists():
            data = json.loads(manifest.read_text())
            return data.get(path.name)
    except Exception:
        pass
    return None


async def build_fallback_pack(
    audio_dir: Optional[Path] = None, languages: Optional[List[str]] = None
) -> Dict[str, object]:
    """Render the generic lines with the real Piper synthesizer.

    Returns an honest report: lines rendered vs skipped (voice/engine
    missing). Safe to call repeatedly — existing files are kept.
    """
    from app.agents.base_agent import AgentConfig
    from app.agents.commentary_agents.voice_synthesizer import VoiceSynthesizer
    from app.core.event_bus import EventBus

    base = fallback_dir(audio_dir)
    base.mkdir(parents=True, exist_ok=True)
    synth = VoiceSynthesizer(AgentConfig(agent_id="fallback-pack-builder"), EventBus())
    langs = languages or list(GENERIC_LINES.keys())
    rendered, skipped = 0, 0
    manifest: Dict[str, str] = {}
    existing_manifest = base / "manifest.json"
    if existing_manifest.exists():
        try:
            manifest = json.loads(existing_manifest.read_text())
        except Exception:
            manifest = {}
    for lang in langs:
        out_dir = base / lang
        out_dir.mkdir(parents=True, exist_ok=True)
        profile_id = "hindi_caster_1" if lang == "hi" else "english_analyst"
        for text in GENERIC_LINES.get(lang, []):
            path = out_dir / f"{_slug(text)}.wav"
            manifest[path.name] = text
            if path.exists() and path.stat().st_size > 1000:
                rendered += 1
                continue
            audio = await synth.synthesize(text, profile_id=profile_id, cache=False)
            if audio is not None and audio.synthesized and audio.audio_data:
                path.write_bytes(audio.audio_data)
                rendered += 1
            else:
                skipped += 1
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2))
    logger.info("fallback pack build done", rendered=rendered, skipped=skipped)
    return {"rendered": rendered, "skipped": skipped, "dir": str(base)}


# ------------------------------------------------------------------ #
# Predictive pre-synthesis cache (latency mitigation)
# ------------------------------------------------------------------ #

_presynth_lock = threading.Lock()
_presynth_done: Dict[str, float] = {}  # language -> epoch when warmed


async def warm_presynthesis(
    languages: List[str], audio_dir: Optional[Path] = None
) -> Dict[str, object]:
    """Pre-render high-probability lines for the given languages into the
    synthesizer's LRU cache + ensure the fallback pack exists on disk.

    Called at feed start / mode change / match start. Rendering into the
    synthesizer cache means the first live use of a generic line costs a
    dict lookup instead of a Piper run; building the pack covers the
    failure path. Voice model load (the cold-start cost) is paid here,
    off the live path.
    """
    from app.agents.base_agent import AgentConfig
    from app.agents.commentary_agents.voice_synthesizer import VoiceSynthesizer
    from app.core.event_bus import EventBus

    warmed: List[str] = []
    synth = VoiceSynthesizer(AgentConfig(agent_id="presynth-warmer"), EventBus())
    for lang in languages:
        profile_id = "hindi_caster_1" if lang == "hi" else "english_analyst"
        lines = GENERIC_LINES.get(lang, [])[:6] + PRESYNTH_EXTRA.get(lang, [])
        ok = 0
        for text in lines:
            audio = await synth.synthesize(text, profile_id=profile_id, cache=True)
            if audio is not None and audio.synthesized:
                ok += 1
        with _presynth_lock:
            _presynth_done[lang] = time.time()
        if ok:
            warmed.append(lang)
        logger.info("presynthesis warm done", language=lang, lines_rendered=ok)
    # Ensure the on-disk fallback pack exists too (idempotent, cheap when
    # already rendered).
    pack = await build_fallback_pack(audio_dir=audio_dir, languages=languages)
    return {"warmed_languages": warmed, "pack": pack, "warmed_at": _presynth_done}
