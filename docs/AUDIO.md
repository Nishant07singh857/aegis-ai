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

## Files

- `backend/app/audio/` — `mixer.py`, `commentary_feed.py`, `game_audio.py`
- `backend/app/api/v1/audio/` — `router.py`, `schemas.py`
- `backend/app/streaming/service.py` — audio-aware `build_ffmpeg_command`
  + `_resolve_audio_inputs`
- `frontend/lib/audio.ts`, `frontend/app/broadcast/director/components/AudioMixer.tsx`
