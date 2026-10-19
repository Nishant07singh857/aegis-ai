# VISION.md — Vision capture pipeline (Phase 14)

How video frames get from an Android observer phone (or a PC screen)
into AEGIS detectors — and, honestly, what works today versus what
needs hardware you plug in.

## Architecture

```
Android phone (BGMI spectator)                    PC (this server)
┌──────────────────────┐        ┌─────────────────────────────────┐
│  adb exec-out        │ PNG    │ FrameGrabber                    │
│  screencap -p  ────────────► │  _capture_loop (async, paced)   │
│  (5–10 FPS, default) │        │   └─ blocking I/O in threads    │
├──────────────────────┤        │        │                        │
│  scrcpy → H.264      │ MKV    │        ▼                        │
│  over FIFO (opt-in,  ────────────► CapturedFrame → frame buffer  │
│  higher FPS)         │ stream │        │ (drop-oldest, depth 2)  │
└──────────────────────┘        │        ▼                        │
                                │ FramePipeline                   │
┌──────────────────────┐        │  preprocess: resize → grayscale │
│  PC screen via mss   │ BGR    │   (asyncio.to_thread, never     │
│  (free, MIT)         ────────────►  blocks the event loop)      │
└──────────────────────┘        │        │                        │
                                │        ▼                        │
                                │  per-detector ROI crop →        │
                                │  detect() in thread pool        │
                                │  (one bad detector can't kill   │
                                │   the run)                      │
                                └─────────────────────────────────┘
```

Modules (`backend/app/vision/capture/`):

| Module | Role |
|---|---|
| `frame_grabber.py` | `FrameGrabber`: per-source capture loops, persistent handles, disconnect state machine |
| `pipeline.py` | `FramePipeline`: preprocess + parallel detector dispatch, never blocks the event loop |
| `adb_controller.py` | `ADBController`: async ADB wrapper + blocking `screencap_png()` for the loop |
| `scrcpy_capture.py` | `ScrcpyCapture`: persistent scrcpy H.264 → FIFO → OpenCV decode |
| `device_manager.py` | `DeviceManager`: registry + health scoring for up to 16 spectator devices |
| `feed_health_monitor.py` | `FeedHealthMonitor`: FPS/latency/drop scoring per feed |
| `resolution_scaler.py` | `ResolutionScaler`: priority-based resolution/FPS/bandwidth allocation |
| `config.py` | `CaptureConfig`: every knob, env-based (see reference below) |

No fake frames anywhere: when a source has no feed, `get_latest_frame()`
returns `None`, detectors are marked `skipped`, and the source reports
`NO_FEED` / `RECONNECTING`.

> **Color note (W4):** the generic pipeline defaults to grayscale
> preprocessing, but `VisionCaptureService` (`app/vision/runtime.py`)
> forces `grayscale=False` — the Phase 14 HUD readers need BGR color
> (HSV sampling, BGR OCR). `AEGIS_CAPTURE_GRAYSCALE` is ignored by the
> vision feed service.

## Source states (graceful degradation)

`SourceState`: `STOPPED → CONNECTING → LIVE ⇄ NO_FEED / RECONNECTING`

- A source that never yields frames moves `CONNECTING → RECONNECTING`
  after `reconnect_attempts` misses, with exponential backoff (capped at
  60 s). Retries never stop; the server never crashes.
- A `LIVE` source that goes quiet for longer than `feed_timeout_s`
  flips to `NO_FEED`.
- `get_stats()` exposes `state`, `last_frame_age_s`, `last_error`,
  `frames_captured`, and `errors` per source — this is what your
  operator dashboard should render as the honest feed status.

## Setup

### 1. Android via ADB (default, 5–10 FPS, no extra software)

On the **phone** (each observer device):

1. Settings → About phone → tap **Build number** 7 times → Developer
   options appear.
2. Developer options → enable **USB debugging**.
3. For Wi-Fi (recommended for 16 phones; USB hubs get messy):
   Developer options → enable **Wireless debugging** (Android 11+),
   or once over USB run `adb tcpip 5555`.
4. Connect the phone and PC to the **same network** (a dedicated
   router/hotspot, not venue Wi-Fi — see Troubleshooting).
5. Note the phone's IP: Settings → About phone → Status → IP address.

On the **server**:

```bash
# Install ADB (Debian/Ubuntu)
sudo apt install android-tools-adb

# Verify the binary
adb version

# Connect over Wi-Fi (repeat per phone)
adb connect 192.168.1.50:5555

# Confirm: device must show as "device" (not "unauthorized"/"offline")
adb devices
```

If a device shows `unauthorized`, accept the RSA prompt on the phone
screen and re-run `adb devices`.

Then point AEGIS at it:

```bash
export AEGIS_CAPTURE_BACKEND=adb_poll   # default
export AEGIS_ADB_SERIAL=192.168.1.50:5555
export AEGIS_CAPTURE_FPS=8
```

Quick smoke test (one PNG to stdout, no server needed):

```bash
adb -s 192.168.1.50:5555 exec-out screencap -p > /tmp/test.png && file /tmp/test.png
# → /tmp/test.png: PNG image data, ...
```

Throughput note: `adb exec-out screencap` is ~5–10 FPS at phone
resolution — plenty for kill-feed OCR, zone/minimap readers, and
alive counters. It is NOT 30 FPS video; use scrcpy below for that.

### 2. Android via scrcpy (opt-in, higher FPS)

```bash
# Debian/Ubuntu
sudo apt install scrcpy
# macOS
brew install scrcpy
# Verify
scrcpy --version
```

Requirements: the device must already be visible in `adb devices`
(see step 1 — scrcpy uses ADB under the hood).

```bash
export AEGIS_CAPTURE_BACKEND=scrcpy
export AEGIS_ADB_SERIAL=192.168.1.50:5555
export AEGIS_SCRCPY_MAX_SIZE=960   # encode width cap (px)
export AEGIS_SCRCPY_MAX_FPS=15
```

How it works: `ScrcpyCapture` spawns
`scrcpy -s <serial> --no-display --no-audio --max-size 960 --max-fps 15
--video-codec=h264 --record-format=mkv --record <fifo>`, and a reader
thread decodes the MKV stream from the FIFO with OpenCV. If the binary
is missing or the device is unreachable, `start()` raises `RuntimeError`
with the exact fix — never a fake frame. OpenCV needs ffmpeg support
(`opencv-python-headless` qualifies).

### 3. PC screen capture (free, no phone needed)

For capturing the server's own display (e.g. an observer emulator
window, or testing without hardware):

```bash
pip install mss          # free, MIT-licensed; already in pyproject.toml
```

```bash
export AEGIS_CAPTURE_BACKEND=mss
```

`mss` grabs the primary monitor. Headless servers have no monitor —
run under `Xvfb` or use the `adb_poll`/`none` backends instead.

### 4. Disable capture

```bash
export AEGIS_CAPTURE_BACKEND=none
```

The pipeline stays up, every source honestly reports `NO_FEED`, and
detectors are skipped. Useful for API-only / dry-run operation.

## Configuration reference

All env vars (defaults work out of the box):

| Variable | Default | Meaning |
|---|---|---|
| `AEGIS_CAPTURE_BACKEND` | `adb_poll` | `adb_poll` \| `scrcpy` \| `mss` \| `none` |
| `AEGIS_ADB_SERIAL` | `""` | Device serial or `ip:port`, e.g. `192.168.1.50:5555` |
| `AEGIS_ADB_PATH` | `adb` | ADB binary |
| `AEGIS_SCRCPY_PATH` | `scrcpy` | scrcpy binary |
| `AEGIS_SCRCPY_MAX_SIZE` | `960` | scrcpy `--max-size` (px) |
| `AEGIS_SCRCPY_MAX_FPS` | `15` | scrcpy `--max-fps` |
| `AEGIS_CAPTURE_FPS` | `8` | Target FPS for the poll loop |
| `AEGIS_CAPTURE_WIDTH` / `AEGIS_CAPTURE_HEIGHT` | `960` / `540` | Preprocess size fed to detectors |
| `AEGIS_CAPTURE_GRAYSCALE` | `1` | `1` = grayscale before detectors |
| `AEGIS_FRAME_QUEUE_SIZE` | `2` | Latest-frame buffer depth (drop-oldest) |
| `AEGIS_RECONNECT_DELAY_S` | `2.0` | Initial reconnect backoff |
| `AEGIS_MAX_RECONNECT_ATTEMPTS` | `5` | Misses before `RECONNECTING` |
| `AEGIS_FEED_TIMEOUT_S` | `5.0` | Quiet seconds before `LIVE → NO_FEED` |
| `AEGIS_ROI_<DETECTOR>` | — | Per-detector ROI override, `"x,y,w,h"` as 0–1 relative floats of the preprocessed frame. Example: `AEGIS_ROI_kill_feed_ocr="0.0,0.0,1.0,0.25"` crops the top quarter for the kill-feed OCR detector. Unset = detector's own regions. |
| `AEGIS_VISION_MATCH_ID` | `""` | Match id attached to every event emitted by the vision feed service (overridable per `POST /feed/vision/start` call). |

Programmatic use:

```python
from app.vision.capture import (
    FrameGrabber, FramePipeline, StreamConfig, FrameSource,
    load_capture_config,
)

config = load_capture_config()          # reads AEGIS_* env vars
grabber = FrameGrabber()
grabber.add_source(StreamConfig(
    source_id="phone1",
    source_type=FrameSource.ADB_DEVICE,
    device_id=config.adb_serial,        # e.g. "192.168.1.50:5555"
    target_fps=int(config.target_fps),
    capture_backend=config.backend,     # "adb_poll" | "scrcpy"
    adb_path=config.adb_path,
))
await grabber.start_capture("phone1")

pipeline = FramePipeline(config=config)
pipeline.register_detector(kill_feed_ocr)   # any BaseDetector

frame = grabber.get_latest_frame("phone1")  # None → honest no-feed
result = await pipeline.process_frame(frame)
print(result.feed_ok, result.outcomes["kill_feed_ocr"].status)
```

## Troubleshooting

| Symptom | Cause → Fix |
|---|---|
| `adb devices` shows `unauthorized` | Accept the RSA fingerprint dialog on the phone, then `adb disconnect` + `adb connect` again. |
| `adb connect` times out | Phone and server on different networks, or client isolation on venue Wi-Fi → use a dedicated hotspot/router; `ping` the phone IP first. |
| Source stuck in `RECONNECTING`, `last_error: no frames received` | Device asleep (set screen timeout to 30 min: `adb shell settings put system screen_off_timeout 1800000`), BGMI killed, or Wi-Fi dropped. `ADBController.set_screen_timeout()` does this programmatically. |
| `scrcpy exited immediately` | Device not in `adb devices`, or another scrcpy/adb session holds it. `adb disconnect <serial>` then reconnect. |
| Low FPS on `adb_poll` | Expected: 5–10 FPS is the design target. Lower `AEGIS_CAPTURE_WIDTH/HEIGHT` or switch to `scrcpy`. |
| `mss` finds no monitors | Headless server — run under Xvfb or use another backend. |
| Spotter taps delayed vs stream | BGMI spectator mode itself lags ~30 s–1 min behind live; delay the broadcast output to match (see MEMORY 2026-10-18 testing-risks note). This is a game-client constraint, not a capture bug. |

## Feed integration (Phase 14 W4): detectors → event bus

The vision pipeline is wired into the existing game-feed adapter as a
first-class feed source (`adapter_id="vision"`):

```
FrameGrabber → FramePipeline → detectors → NormalizedEvent
     → VisionFeedAdapter.ingest() → event bus (kill_event, knock_event,
        zone_event, alive_count_update, team_status_update)
```

- `backend/app/vision/runtime.py` — `VisionCaptureService`: the background
  loop (capture → pipeline → event extraction → ingest). Forces
  **color** frames (`grayscale=False` regardless of
  `AEGIS_CAPTURE_GRAYSCALE`) because the HUD readers need BGR/HSV.
- `backend/app/services/feed_adapter/vision_adapter.py` —
  `VisionFeedAdapter(BaseFeedAdapter)`: registered at startup so
  `GET /api/v1/feed/status` lists `vision` in `registered_adapters`.
- New `FeedEventType` members: `ALIVE_COUNT → alive_count_update`,
  `TEAM_STATUS → team_status_update` (additive; existing consumers ignore
  unknown bus types).

**Operator-gated, never automatic.** Capture starts only via API:

| Endpoint | Auth | Effect |
|---|---|---|
| `POST /api/v1/feed/vision/start` | operator+ | starts capture + detection loop; `vision` becomes the active adapter |
| `POST /api/v1/feed/vision/stop` | operator+ | stops everything; active adapter falls back to `simulator` |
| `GET /api/v1/feed/vision/status` | any user | capture state, measured FPS, frames/events counters, per-detector OCR availability |

`match_id` for emitted events: request body → else `AEGIS_VISION_MATCH_ID`.

Honest-degradation preserved end to end: no feed → the loop idles and
emits nothing; OCR unclear → reader reports UNKNOWN, no event; one
detector raising is isolated per frame and never kills the loop; an
ingest failure is logged loudly (never silently dropped) without
stopping the loop.

## Honesty: what was NOT verified, and what breaks

- ❌ **No real BGMI footage was tested.** Every accuracy number below is
  measured on *synthetic* frames (OpenCV/PIL-rendered HUD text on black
  backgrounds). Real spectator-mode frames differ in font, size, color,
  compression artifacts, translucency, and UI skin per season.
- **Synthetic accuracy (measured 2026-10-19, this VM, CPU-only):**
  - Kill feed OCR (Tesseract 5.3.4): 9/10 lines correct (90%); the one
    miss was `Sc0utOP` → `ScOutOP`. Dedup: 10 identical frames → 1 event.
    Blank frame → 0 events.
  - Alive counter: 100/100 two-digit numbers correct via the OpenCV
    template fallback; garbage (`abc`, `--`, noise) → UNKNOWN, never a
    wrong number (reader-level `min_confidence` + `confirm_frames` gates).
  - Zone timer: `0:45`, `1:23`, `12:05` read correctly; reset-edge emits
    one `zone_update` with `phase_source: inferred_from_timer_reset`.
  - Team bar: `alive/alive/knocked/dead` classified correctly on
    synthetic cards; blank/noise → all-UNKNOWN, no invented states.
- **Measured throughput (this VM, CPU-only, synthetic 1080p frames):**
  ~**2.3 FPS end-to-end** (435 ms/frame). Cost breakdown: kill-feed OCR
  on text ~860 ms, alive OCR ~400 ms, zone OCR ~400 ms (the two run in
  parallel threads), team color ~10 ms. With the template-digit fallback
  (no tesseract) the OCR readers drop to ~10–30 ms and the pipeline is
  killfeed-bound. HUD counters don't need 8 FPS — running OCR readers at
  1–2 Hz (or every Nth frame) is the obvious optimization before any
  hardware spend. No GPU path exists for Tesseract (CPU-bound); the
  template fallback is the current no-tesseract story.
- **What breaks on game UI updates:** every HUD region is a relative
  coordinate (`AliveCountReader.DEFAULT_REGION` etc.) tuned by eye, and
  the team reader's HSV ranges are starting guesses — a BGMI season
  reskin that moves the alive counter or recolors the HP bar silently
  degrades reads to UNKNOWN (loud, not wrong). **Recalibrate regions +
  colors against real captured frames before production use.**
- **Environment caveat:** during QA the VM lost `opencv`/`numpy`/`pillow`
  (pip) and the `tesseract` binary (apt) to an external process
  mid-session; all were reinstalled and every number above was
  re-measured after restoration. Pin these in production images.
- ❌ No Android device in this VM: the ADB/scrcpy paths are tested with
  fake binaries and FIFOs only — field-test with a real phone before a
  live show.

## What was verified vs what needs hardware

- ✅ `adb_poll` loop: verified end-to-end with a fake `adb` binary
  emitting real PNGs — 15 frames at 5 FPS, PNG decode, `LIVE` state,
  disconnect → `RECONNECTING` → auto-`LIVE` on restore (21 unit tests).
- ✅ Frame pipeline: synthetic-image tests for preprocess, parallel
  detector dispatch, detector-failure isolation, no-feed honesty,
  ROI overrides, event-loop non-blocking.
- ✅ scrcpy decode path: validated with ffmpeg writing H.264/MKV into
  the same FIFO arrangement `ScrcpyCapture` uses.
- ✅ Feed integration (W4): 20 integration tests — adapter registration,
  ingest→bus mapping for all five vision event types, synthetic
  frames → pipeline → `NormalizedEvent`s (alive change, team change,
  zone reset edge), full no-fake audit (blank/solid/noise → zero events;
  the team reader's one-time all-UNKNOWN baseline is the only allowed
  emission and invents no game facts), no-feed loop honesty,
  start/stop lifecycle, import-deadlock regression (fresh interpreter).
- ✅ QA fixes found by re-running (not trusting) worker claims:
  `TemplateDigitEngine` margin veto rejected clean `8`/`5` glyphs
  (fixed with a topology confirmation that can only confirm the
  template winner, never promote a runner-up); `TesseractEngine`
  blurred *after* the tight crop, collapsing confidence 62→2 on clean
  text (blur removed, measured); lazy schema imports raced across
  detector threads and deadlocked the import lock (serialized +
  warmed at service startup).
- ❌ Real Android device: **not available in this VM** — the ADB and
  scrcpy paths against physical hardware still need a field test with
  `adb connect <ip>:5555` and the smoke test above.
- ❌ `mss` screen capture: not testable on this headless VM (no
  display); code path is straightforward `mss` API usage.
- ❌ NDI: intentionally unsupported — returns honest no-feed.
- ❌ Real BGMI footage: **nothing above was measured on it** — see
  Honesty section.

## Zero-cost note

Every dependency in this pipeline is free/offline:
`opencv-python-headless`, `numpy`, `mss` (MIT), `adb`/`scrcpy` (Apache
2.0), `ffmpeg` (for the scrcpy test path). No paid APIs, no cloud
calls — consistent with the project's zero-API-spend policy.
