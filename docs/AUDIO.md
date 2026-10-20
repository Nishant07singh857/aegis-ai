# AEGIS AI — Audio Pipeline (W3)

The broadcast is no longer silent. This document says exactly what works,
what doesn't, and what hardware you still need. Last verified 2026-10-19
on this VM (Ubuntu, Python 3.12, FFmpeg present, no ADB device, no audio
hardware).

## What works today

**1. TTS commentary — Piper (local, free, offline).**
Commentary text → real spoken audio, zero API spend, zero network calls.

- Engine: [Piper](https://github.com/OHF-voice/piper1-gpl) via `piper-tts`
  (`pip install piper-tts`). Chosen over the alternatives because it
  actually works here:
  - *espeak-ng*: installs anywhere and is tiny, but sounds like a 1990s
    robot — wrong quality bar for a broadcast voice. Rejected.
  - *Coqui XTTS*: better voice cloning, but far heavier (GPU-hungry,
    ~GB-sized models, slow on CPU) and more fragile to install. Rejected
    for the default path; the `TTSEngine.COQUI` enum value remains as a
    documented extension point.
  - *Piper*: 63 MB Hindi voice, CPU inference, one `pip install`, real
    ONNX model. Verified end-to-end in this VM.
- Voice tested: `hi_IN-pratham-medium` (male Hindi voice — fits the
  Hinglish caster profiles). Download once:
  `python -m piper.download_voices hi_IN-pratham-medium`
- Enable:
  ```bash
  export AEGIS_TTS_ENGINE=piper
  export AEGIS_TTS_MODEL_PATH=/path/to/hi_IN-pratham-medium.onnx
  ```
  Without both vars, synthesis is **honestly disabled** — the API returns
  `synthesized: false` and the UI shows "TTS off — no engine". Nothing
  ever fakes audio.
- **English voice + language routing (Phase 21/T5a).** A second free
  Piper voice, `en_US-amy-medium` (~63 MB, from the official
  rhasspy/piper-voices repo), now sits next to the Hindi voice in
  `.local-voices/` (`AEGIS_VOICES_DIR` overrides the directory). Routing
  is automatic: lines are language-detected (Devanagari or >= 2 Hinglish
  marker words → Hindi voice; otherwise English voice), an explicit
  `language: "en" | "hi"` on `POST /api/v1/audio/commentary` or an
  explicit voice profile overrides detection, and the per-language
  voice file wins over the single-model `AEGIS_TTS_MODEL_PATH` legacy
  path. Verified end-to-end in this VM: an English line rendered with
  the English voice (3.3 s WAV) and a Hinglish line with the Hindi
  voice (2.8 s WAV). `GET /api/v1/audio/status` reports per-language
  voice availability honestly.
- Flow: `POST /api/v1/audio/commentary {"text": "..."}` → the
  `VoiceSynthesizer` agent synthesizes → WAV saved to
  `<audio_out>/commentary/clip_NNNN_<ts>.wav` → PCM queued to the live
  feed. Download any clip: `GET /api/v1/audio/clip/{clip_id}`.

**2. Continuous commentary PCM feed (the stream input).**
`POST /api/v1/audio/feed/start` launches a writer thread emitting
uninterrupted `s16le / 48000 Hz / stereo` PCM to a FIFO at
`<audio_out>/pipes/commentary.pcm` — silence between lines, clip audio
when a line is queued. Verified: FFmpeg read 8.0 s from the FIFO and the
capture measured **max −3.0 dB / mean −22.3 dB** — real voice, not
silence. If no reader (FFmpeg) is attached, audio is dropped with a
warn-once log, never buffered forever.

**3. Software mixer (director page → "Audio mixer" card).**
Commentary volume slider (0–150 %, applied as gain to the PCM samples),
game-audio volume slider (gain hint applied in the FFmpeg filtergraph),
master mute. State persists to `<audio_out>/mixer_state.json` across
restarts. API: `GET/POST /api/v1/audio/mixer`.

**4. Stream integration (FFmpeg last mile) — real, not just docs.**
`StreamService.build_ffmpeg_command()` now accepts optional audio
inputs. On stream start it resolves real sources:
- commentary FIFO — included **only while the feed is running**
  (opening a reader on a writer-less FIFO would hang the start);
- game audio — from `AEGIS_GAME_AUDIO_INPUT`, format
  `<ffmpeg_format>:<device>`, e.g. `alsa:hw:1,0` or
  `file:/path/to/game_audio.mkv`;
- inputs are mixed with `amix` and encoded to AAC 128k alongside the
  x264 video. Master mute drops all audio inputs.
- With no inputs the encoder gets `-an` — video-only, honestly, exactly
  as before. Unit-tested for 0/1/2 inputs.

So the full chain works today:
**commentary text → Piper → WAV + PCM FIFO → FFmpeg → RTMP with AAC audio.**

## What does NOT work yet (needs hardware / install)

**Game audio.** There is no game sound without a physical route from the
observer phone to the server. `GET /api/v1/audio/game/status` probes and
reports honestly; on this VM it says: no adb, no ALSA capture device —
"the broadcast game-audio channel will be silent until hardware is
connected." Two real routes, in preference order:

- **Route A — scrcpy audio forwarding (all digital, no cables).**
  Needs: `adb` + `scrcpy` v2+ on the server, observer phone on
  **Android 11+ (API 30+)**, USB/Wi-Fi ADB authorized.
  ```bash
  apt install adb scrcpy        # server
  adb devices                   # authorize the RSA prompt on the phone
  ```
  Then `POST /api/v1/audio/game/start` runs
  `scrcpy --no-video --no-window --audio-codec=opus --audio-bit-rate=128K
  --record=<audio_out>/game/game_audio_<ts>.mkv`.
  It fails loudly (409 + scrcpy's stderr) if the device/API level can't
  do it — never silently. Caveat: whether `--no-video` audio-only
  recording works depends on the scrcpy build; **not verified here** (no
  phone in this VM). If your scrcpy build refuses, use Route B.
  Feed the recording into the stream mix:
  ```bash
  export AEGIS_GAME_AUDIO_INPUT="file:<audio_out>/game/game_audio_<ts>.mkv"
  ```
  (then restart the stream).

- **Route B — manual 3.5mm cable (always works).**
  3.5mm TRRS cable from the phone's headphone jack (or a USB-C audio
  dongle) → server line-in or a USB audio interface (e.g. any cheap
  USB sound card). Verify the capture device appears:
  ```bash
  arecord -l     # note card/device numbers, e.g. card 1 device 0
  ```
  Then:
  ```bash
  export AEGIS_GAME_AUDIO_INPUT="alsa:hw:1,0"
  ```
  (then restart the stream). Set levels with `arecord`/`alsamixer`
  first — digital gain can't fix a clipped analog input.

**Not built / deliberately left out:**
- No automatic commentary *triggering* from match events yet — lines are
  queued via the API / director "Speak" button. Wiring the
  `script_generator` output straight into the feed is the obvious next
  step (the pieces exist: script text → `POST /audio/commentary`).
- No voice-activity ducking (game audio doesn't dip under commentary).
  The amix is a straight sum; keep game volume modest.
- No lip-sync/delay compensation — Piper lines queue in order, paced in
  real time; expect a few seconds of TTS latency behind the event.

## Honest quality caveats

- **Piper will not match human casters.** It is a single neutral voice
  reading text: flat prosody, no real excitement, no crowd energy, no
  Hinglish code-switching finesse (the Hindi voice reads Roman-script
  Hindi acceptably; English words inside get a Hindi accent). Emotion
  styles (excited/shout/…) currently only nudge pitch/speed parameters —
  the voice itself doesn't emote. Treat it as a functional placeholder
  voice, not a product voice. A better free path later: a fine-tuned
  Piper voice or a heavier local model — both need real BGMI caster
  recordings to train on.
- **Latency:** cold synthesis loads the ONNX model (~10 s on a quiet
  box); warm lines measured 1.5–3.4 s of audio for short sentences, but
  synthesis *compute* took ~50–100 s on this heavily contended VM (load
  avg 12+). On a dedicated broadcast box expect seconds, not minutes —
  still not instant. Queue lines ahead of the moment, or pre-synthesize
  recurring phrases (the synthesizer caches by text).
- **Game audio over scrcpy** adds its own ~0.5–1 s device-side latency;
  the manual cable route is near-zero latency.

## Setup checklist (broadcast day)

1. `pip install piper-tts` and download the voice (one-time).
2. Export `AEGIS_TTS_ENGINE=piper` and `AEGIS_TTS_MODEL_PATH=…` for the
   backend process (docker-compose: add to the backend environment).
3. Optional: `AEGIS_AUDIO_DIR` (default `<repo>/audio_out`).
4. Game audio: Route A (`adb`/`scrcpy` + Android 11+ phone) or Route B
   (3.5mm cable → `arecord -l` → `AEGIS_GAME_AUDIO_INPUT`).
5. Start backend → director page → **Audio mixer** card → "Start
   commentary feed" (and "Capture game audio" if Route A).
6. `POST /stream/start` — FFmpeg now picks up the FIFO (+ game input)
   automatically; verify audio in the stream monitor before going live.

## Phase 22 — Commentary completion (M13)

**5 commentary modes** (director-controlled, real behavioural
differences — `backend/app/audio/commentary_modes.py`, persisted to
`<audio_out>/commentary_mode.json`, per-match overrides supported;
director UI: "Commentary mode" card on the director console):

| Mode | Speaks? | Language / voice | Pacing | Content mix (PBP/analysis/hype) |
|---|---|---|---|---|
| Hindi Main | yes | Hindi — `hi_IN-pratham` (`hindi_caster_1`) | 3.0 s gap, cooldown ×1.0 | 45/20/35 |
| English Main | yes | English — `en_US-amy` (`english_analyst`) | 4.0 s gap, cooldown ×1.25 | 40/40/20 |
| Bilingual Blend | yes | Alternates Hindi/English per line; **dual-track render on** | 3.5 s gap, ×1.1 | 40/25/35 |
| Silent | **no** | — (text only) | — | text flows to lower-thirds + captions |
| Human Assist | **no** | — (text only) | 2.0 s, ×0.8 | AI queues text suggestions (`GET /audio/suggestions`); the human caster speaks |

Caster profiles are wired to the real installed Piper voices
(`hindi_caster_1`/`hinglish_caster` → `hi_IN-pratham-medium`,
`english_analyst` → `en_US-amy-medium`, engine `piper`); the old
placeholder `eleven_*` voice ids and `engine=NONE` defaults are gone.
An explicit `AEGIS_TTS_ENGINE=none` still disables synthesis honestly.

**Dual-track audio (Hindi + English simultaneously).** In Bilingual
Blend, every line is also translated (LanguageManager → local Ollama)
and synthesized with the other language's voice. Both languages get
their own PCM FIFO (`pipes/commentary_hi.pcm`, `pipes/commentary_en.pcm`)
and each bilingual line is muxed into a program `.mkv` under
`<audio_out>/dual_track/` with **two AAC streams tagged `hin`/`eng`**
(verified with ffprobe). When the stream runs in bilingual mode,
FFmpeg writes the same dual-track program recording alongside the live
push (`<audio_out>/program/program_<ts>.mkv`).
Per-platform truth:
- **Local program recording (.mkv): both tracks, always** (when the
  translation was available — see below).
- **YouTube / Twitch / Facebook live (RTMP/FLV): ONE audio track only.**
  FLV carries a single audio stream; the live push carries the primary
  (Hindi) track and **track 2 is dropped by the protocol**, not by us.
  The dual-track .mkv can be uploaded afterwards as a VOD where players
  offer language selection.
- **Translation dependency:** the second track needs a real translation.
  With no Ollama reachable, translation is honestly unavailable, the
  line reports `dual_track.rendered: false` with the reason, and no
  second-language audio is faked. The primary track is unaffected.

**Subtitles.** Every spoken (and Silent-mode text) line becomes a
caption cue: `GET /api/v1/audio/subtitles/{hi|en}.vtt` (WebVTT sidecar),
`GET /api/v1/audio/captions` (JSON), `GET /api/v1/audio/captions/current`
(cue active now). The watch page has a **CC toggle**; the output page
takes `?captions=1` to burn cues into the capture.

**Profanity filter.** A curated regex/wordlist pass (English + Hindi
Devanagari + Roman Hinglish) runs on every line **before** TTS/display;
hits are masked to `***` and logged (`backend/app/audio/profanity.py`).
It never raises — a filter error passes the text through and logs.

**Fallback cached audio pack.** 20 generic (name-free, fact-free) lines
per language, pre-rendered with Piper into `<audio_out>/fallback/{hi,en}/`
(40/40 rendered on this VM, 2026-10-20). When live Piper synthesis
fails, the synthesizer substitutes a generic line and reports it
honestly: `source: "cached_fallback"`, `text` = the generic line
actually spoken, `requested_text` = the line that failed. Rebuild any
time via `POST /api/v1/audio/fallback-pack/build`.

**Measured latency** (`backend/scripts/benchmark_commentary_latency.py`,
this VM, 2026-10-20 — re-run it; numbers move with machine load):

| Stage | Before voice-cache fix | After |
|---|---|---|
| Profanity filter | 0.005 ms median | 0.004 ms median |
| Cold synthesis (model load + first line) | 4,193 ms | 3,427 ms |
| Warm synthesis (per line, en+hi) | 3,800 ms median (2,758–4,435) | **519 ms median (392–3,501)** |
| End-to-end `enqueue_line` (filter→synth→WAV→PCM→queue) | 8,526 ms median | 3,984 ms median (1,282–4,958) |
| Pre-synthesized / LRU cache hit | ~0 ms | ~0 ms |

The big win was real: the synthesizer reloaded the ONNX voice on
**every** line; voices are now cached per model path, and
`warm_models()` + predictive pre-synthesis
(`POST /api/v1/audio/presynth/warm`, also fired at backend boot) pay the
load cost off the live path. **Honest bottom line: the PRD's <600 ms
end-to-end target is NOT met.** Warm *synthesis alone* now medians
~0.52 s on this VM, but event→audio-out end-to-end is still ~1.3–5 s
(FFmpeg conversion, dual-track attempt, VM contention). Only
pre-rendered lines (fallback pack / pre-synthesis cache) play with ~0 ms
synthesis cost. Human casters still carry live emotion in the hybrid
model — see the caveats above.

## API reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/audio/status` | user | TTS + mixer + feed + game-audio status |
| POST | `/api/v1/audio/commentary` | operator | Synthesize + queue one line (202) |
| GET | `/api/v1/audio/clips` | user | Recent clips (id, text, duration) |
| GET | `/api/v1/audio/clip/{id}` | user | Download WAV |
| GET/POST | `/api/v1/audio/mixer` | user / operator | Volume/mute state |
| POST | `/api/v1/audio/feed/start` `…/stop` | operator | PCM FIFO feed control |
| GET | `/api/v1/audio/game/status` | user | Honest hardware probe |
| POST | `/api/v1/audio/game/start` `…/stop` | operator | scrcpy capture control |
| GET/POST | `/api/v1/audio/commentary-mode` | user / operator | Phase 22: get/set the 5-mode commentary mode |
| GET | `/api/v1/audio/subtitles/{hi\|en}.vtt` | user | Phase 22: WebVTT caption sidecar |
| GET | `/api/v1/audio/captions` `…/current` | user | Phase 22: caption cues JSON / active cue |
| GET | `/api/v1/audio/suggestions` | operator | Phase 22: Human Assist caster suggestions |
| GET | `/api/v1/audio/latency` | user | Phase 22: measured pipeline latency stats |
| POST | `/api/v1/audio/fallback-pack/build` | operator | Phase 22: (re)render the generic-line fallback pack |
| POST | `/api/v1/audio/presynth/warm` | operator | Phase 22: warm voices + predictive pre-synthesis |

## Files

- `backend/app/audio/` — `mixer.py`, `commentary_feed.py`, `game_audio.py`;
  Phase 22: `commentary_modes.py`, `profanity.py`, `subtitles.py`,
  `fallback_pack.py`
- `backend/app/api/v1/audio/` — `router.py`, `schemas.py`
- `backend/app/streaming/service.py` — audio-aware `build_ffmpeg_command`
  + `_resolve_audio_inputs`; Phase 22: `build_ffmpeg_command_dual_track`
  + `_resolve_dual_track_inputs`
- `backend/scripts/benchmark_commentary_latency.py` — latency benchmark
- `frontend/lib/audio.ts`, `frontend/app/broadcast/director/components/AudioMixer.tsx`;
  Phase 22: `components/CommentaryModePanel.tsx`, captions in
  `components/DirectorOutputView.tsx` + watch-page CC toggle
