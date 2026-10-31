# AEGIS AI — Live Streaming Guide

AEGIS can broadcast itself to YouTube, Twitch, and Facebook at the same time,
with nobody touching anything once it is running. This guide covers both ways
to do it:

1. **Automated streamer** (recommended) — a Docker service renders the
   broadcast director page in a headless browser, captures it, and pushes it
   to every platform you configured. Start it, walk away.
2. **OBS, manually** — add the AEGIS overlay pages as OBS browser sources and
   stream from OBS yourself. Zero code, full manual control.

No paid APIs are involved anywhere. Stream keys are the only credentials, and
they live in your environment — never in code, never in this repo.

## 0. Topology decision (Phase 32, K-C2) — read this first

There is ONE supported automated-streaming topology: the **shell
streamer** in this guide (Docker service `streamer`, profile
`streaming`). The older API-driven path (`POST /api/v1/stream/start`,
`app.streaming.service.StreamService` spawning Xvfb/Chromium/FFmpeg from
the API process) is **deprecated and disabled**: it could never run in
the production backend image, which ships no Xvfb/Chromium/FFmpeg, so
every start attempt failed with 409. The API now answers 410 Gone for
start/stop and reports state `"deprecated"` for status — it does not
pretend to know the shell streamer's state. Do not re-wire a route to
`StreamService` without also putting those binaries into that route's
image AND proving a real RTMP push on the production rig.

HONEST LIMIT: no real RTMP push has ever been demonstrated from the dev
VM (no keys, no ingest reachable here). The first real RTMP push must be
proven on the production rig before any "streaming works" claim is made.

## 0b. Feed credential for headless capture (Phase 32, K-C1)

The game video on `/broadcast/output` comes from
`GET /api/v1/video/feed/{slot}`, which is authenticated (H3): a bare
`<img>` tag cannot send auth headers. Headless capture therefore carries
a short-lived signed feed token:

- An operator mints one: `POST /api/v1/video/feed-token`
  (operator auth; default TTL 600s, range 60–3600s). The token is valid
  ONLY on the feed route — it is not a general credential.
- The streamer entrypoint takes it two ways: `FEED_TOKEN` directly, or
  `AEGIS_BACKEND_URL` + `AEGIS_API_KEY` (operator key) to mint one
  itself. With neither, and a `/broadcast/output` DIRECTOR_URL, the
  streamer fails fast instead of silently capturing a NO FEED slate.
- The output page also accepts `?feed_token=` in its own URL and
  propagates it into the `<img>` src, so a manual/OBS capture URL can
  carry the same token.
- An open browser request with no credential still gets 401. Token
  limits (bearer until expiry, no revocation list) are documented in
  `backend/app/api/v1/video/feed_token.py` — keep TTLs short.

---

## 1. Getting a stream key

A stream key is a password that tells YouTube/Twitch/Facebook "this incoming
video is mine". Treat it like a password: anyone with it can stream to your
channel.

### YouTube

1. Open [YouTube Studio](https://studio.youtube.com) and sign in.
2. Click **Create** (camera icon, top right) → **Go live**.
3. If this is your first stream, YouTube asks you to verify your channel
   (phone verification); brand-new accounts may have to wait 24 hours before
   live streaming is enabled.
4. In the live dashboard, under **Stream settings**, choose or create a
   stream, then copy the **Stream key** (click the eye icon to reveal it).

RTMP endpoint used: `rtmp://a.rtmp.youtube.com/live2/`

### Twitch

1. Open the [Twitch Creator Dashboard](https://dashboard.twitch.tv) and sign in.
2. Go to **Settings → Stream**.
3. Copy the **Primary Stream key** (click **Copy**).

RTMP endpoint used: `rtmp://live.twitch.tv/app/`

### Facebook

1. Open [Facebook Live Producer](https://www.facebook.com/live/producer) and
   sign in to the Page or profile you want to stream from.
2. Select **Use stream key** as the video source.
3. Copy the **Stream key** shown (the server URL is filled in automatically by
   the streamer).

RTMP endpoint used: `rtmps://live-api-s.facebook.com:443/rtmp/`

---

## 2. Where to put the keys

Keys go in **environment variables**, passed to the streamer container at
start time. They must never appear in code, Dockerfiles, compose files, or
commits.

```bash
export YOUTUBE_STREAM_KEY='paste-your-key-here'
export TWITCH_STREAM_KEY='paste-your-key-here'
export FACEBOOK_STREAM_KEY='paste-your-key-here'
```

You only need the ones you actually stream to — set one, two, or all three.
At least one must be set, or the streamer refuses to start and tells you why.

If you prefer a file over exports, create a `.env` file in the directory
where you run `docker compose`:

```bash
# .env — git-ignored, chmod 600, never committed
YOUTUBE_STREAM_KEY=paste-your-key-here
TWITCH_STREAM_KEY=paste-your-key-here
```

Docker Compose reads `.env` automatically. Keep the file readable only by
you (`chmod 600 .env`) and never commit it.

**If a key leaks** (pasted into chat, committed by accident, screenshared):
go back to the platform dashboard above and **reset/regenerate** it, then
update your environment. A leaked key lets anyone stream to your channel.

---

## 3. CPU requirements

The streamer encodes with **software x264** (libx264) on the CPU — no GPU
needed, but encoding is the most expensive thing the box does.

| Output | What it needs (approximate) |
|---|---|
| 720p30 (default: 1280×720, 30 fps, 4.5 Mbps, `veryfast`) | ~4 vCPUs comfortably. This is the shipped default and the compose file caps the container at 4 CPUs / 4 GB RAM. |
| 1080p30 | ~6–8 vCPUs with software x264. |
| 1080p60 | A strong CPU (8+ fast cores) **or** a hardware encoder. |

Hardware encoding (NVIDIA NVENC, Intel QuickSync) is the usual answer for
1080p60 on modest hardware. The NVENC half of that story already exists in
code — but it is **not plumbed into the shell streamer**: `backend/app/streaming/encoder.py`
(`detect_encoder`) really probes for NVENC (ffmpeg `-encoders` lists
`h264_nvenc` AND `nvidia-smi` reports a GPU) and selects it under
`VIDEO_ENCODER=auto`/`nvenc`, falling back to `libx264` with a warning when
either probe fails; its sole production caller is the highlight
renderer (`backend/app/services/replay/renderer.py:398`), and it maps the x264 preset to `p4` for NVENC
(NVENC rejects x264 preset names). The streamer entrypoint below does not
call it at all — it hardcodes `-c:v libx264` (`infrastructure/docker/streamer-entrypoint.sh`).
Plumbing the entrypoint into `detect_encoder` (with the preset mapping) is
the remaining work; QuickSync has no equivalent probing code. Until then the
shipped streamer is software x264 only, whatever GPU the host has.

Rules of thumb:

- One encode feeds all platforms (the tee muxer), so adding Twitch to a
  YouTube stream costs almost no extra CPU — only network upload.
- Upload bandwidth matters: budget ~6 Mbps up per 720p30 stream destination
  headroom (bitrate + overhead + spikes).
- If the picture stutters, you are CPU-bound: drop to a
  faster preset (`VIDEO_PRESET=ultrafast`, lower quality) or fewer pixels
  (`STREAM_WIDTH=960 STREAM_HEIGHT=540`). (There is no stream audio to judge
  by — the stream is video-only, see §6.)

---

## 4. Quick start — automated streamer

From the repo root, with the base stack's secrets already exported (see
`infrastructure/docker/docker-compose.prod.yml` header):

```bash
# 1. stream keys (at least one)
export YOUTUBE_STREAM_KEY='...'
export TWITCH_STREAM_KEY='...'

# 2. start the stack plus the streamer
docker compose -f infrastructure/docker/docker-compose.prod.yml \
               -f infrastructure/docker/docker-compose.streaming.yml \
               --profile streaming up -d --build

# 3. watch it work
docker compose -f infrastructure/docker/docker-compose.prod.yml \
               -f infrastructure/docker/docker-compose.streaming.yml \
               --profile streaming logs -f streamer
```

What happens: the `streamer` service waits for the frontend, opens the
broadcast director output page
(`http://frontend:3000/broadcast/output?bg=brand` by default — the full-frame
layout built for headless capture, with an opaque branded plate) in Chromium
on a virtual display, captures it at 1280×720 @ 30 fps, encodes x264, and
pushes to every platform whose key you set. You are live within about half a
minute of the page loading.

Stop the stream:

```bash
docker compose -f infrastructure/docker/docker-compose.prod.yml \
               -f infrastructure/docker/docker-compose.streaming.yml \
               --profile streaming stop streamer
```

The service uses `restart: "no"` on purpose — a broadcast is a deliberate
session, not a background daemon. It does not auto-restart after a crash or
a host reboot; start it when you mean to go live.

### Environment reference

| Variable | Default | Meaning |
|---|---|---|
| `YOUTUBE_STREAM_KEY` | *(empty)* | YouTube stream key. At least one key required. |
| `TWITCH_STREAM_KEY` | *(empty)* | Twitch stream key. |
| `FACEBOOK_STREAM_KEY` | *(empty)* | Facebook stream key. |
| `DIRECTOR_URL` | `http://frontend:3000/broadcast/output?bg=brand` | Page the streamer captures. `?bg=brand` = opaque plate (for direct capture); omit it for a transparent background (for downstream compositing). Any overlay URL works for a widget-only stream. The entrypoint appends `feed_token=` for `/broadcast/output` URLs (see §0b). |
| `FEED_TOKEN` | *(empty)* | Pre-minted short-lived feed token (Phase 32, K-C1). Either this or `AEGIS_BACKEND_URL` + `AEGIS_API_KEY` is required for `/broadcast/output` captures. |
| `AEGIS_BACKEND_URL` | *(empty)* | Backend origin (e.g. `http://backend:8000`) used to mint a feed token. |
| `AEGIS_API_KEY` | *(empty)* | Operator API key used to mint a feed token. |
| `STREAM_WIDTH` / `STREAM_HEIGHT` | `1280` / `720` | Capture resolution. |
| `STREAM_FPS` | `30` | Capture frame rate. |
| `VIDEO_BITRATE` | `4500k` | x264 target bitrate (`k` or `M` suffix). |
| `VIDEO_PRESET` | `veryfast` | x264 preset; `ultrafast` = less CPU, lower quality; `medium` = more CPU, better quality. |

### Troubleshooting

- **Container exits immediately with "no stream destination configured"** —
  you started the profile without exporting any `*_STREAM_KEY`. Set at least
  one and re-run.
- **"frontend did not answer after 60s"** — the base stack isn't up. Start
  the base compose file first, then add the overlay, or just run the combined
  command above (the streamer waits via `depends_on`).
- **Chromium shows a blank page** — the director page needs the backend API;
  check `docker compose logs backend`. The streamer also needs the frontend
  built with the right `NEXT_PUBLIC_API_URL` (see the prod compose header).
- **"Stream key invalid" on the platform** — keys are single-line secrets;
  re-copy from the dashboard and check for trailing spaces or newlines in
  your `.env`.
- **Dropped frames / choppy motion** — CPU-bound. See §3: faster preset,
  lower resolution, or a bigger host.

---

## 5. The OBS alternative (manual, zero code)

If you prefer to run the show yourself — or you want to mix AEGIS graphics
with your own webcam/commentary — skip the streamer and use OBS Studio.

Every AEGIS broadcast surface is a plain web page, so each one works as an
**OBS Browser Source** (add Source → Browser, paste the URL, set the size):

| Page | URL (frontend on localhost:3000) | Use |
|---|---|---|
| Director console | `http://localhost:3000/broadcast` | Full operator view: POV control, teams, commentary. |
| Director output (capture-ready) | `http://localhost:3000/broadcast/output?bg=brand` | Full-frame 1920×1080 layout (scorebug + kill ticker + lower third) built for headless capture; `?bg=brand` adds the opaque plate. This is what the automated streamer captures. |
| Scorebug | `http://localhost:3000/overlays/scorebug` | Top-of-screen score strip. |
| Kill feed | `http://localhost:3000/overlays/killfeed` | Kill event ticker. |
| Standings | `http://localhost:3000/overlays/standings` | Tournament standings panel. |
| Lower third | `http://localhost:3000/overlays/lower-third` | Player/caster name strap. |

All five update live over the backend websocket feed — no page refreshes, no
mock data. In OBS you then stream to YouTube/Twitch/Facebook with the same
stream keys from §1 (OBS Settings → Stream), and you can layer your own
camera, microphone, and scenes on top. This is the fastest way to get on air
today and the easiest way to combine AEGIS graphics with a human caster.

---

## 6. Honest limits — what the stream shows today

- **There is no real game video feed yet.** The vision pipeline
  (`backend/app/vision/`) is built to ingest a match feed, but nothing is
  feeding it real gameplay right now. What you see on the director page and
  overlays is the real layout, real components, and real websocket plumbing —
  driven by the **simulated match engine**, not a live match.
- **Audio: there is none on the stream — video-only, honestly.** The
  streamer entrypoint encodes with `-an` and no audio input at all ("no
  faked silent track", `infrastructure/docker/streamer-entrypoint.sh`):
  no game audio, no commentary audio, no TTS, and no silent AAC track is
  muxed onto the stream. (The commentary *text* feed in the app is real.)
  The deprecated API streaming path does the same (`backend/app/streaming/service.py`:
  with no audio inputs the encoder gets `-an` rather than a faked silent
  track). If a platform requires an audio track, that is future work —
  nothing is faked today.
- **The picture is the web UI**, captured at 720p. It looks like a broadcast
  graphics package, not a game client — because that is what it is.

The path to a real match broadcast is: get a real game feed into the vision
ingestion pipeline (screen capture, observer client, or tournament API) →
the overlays and director keep working unchanged, because they already
render from live events. Real game capture is your future step, not
something this guide pretends exists.

---

## 7. Files involved

- `infrastructure/docker/Dockerfile.streamer` — ffmpeg + Chromium + Xvfb
  image (Debian bookworm, release-pinned).
- `infrastructure/docker/streamer-entrypoint.sh` — virtual display →
  Chromium → x11grab → x264 → multi-RTMP (tee muxer). Video-only (`-an`);
  hardcodes `libx264` — it does not call `streaming/encoder.py`.
- `backend/app/streaming/encoder.py` — real NVENC probing
  (`detect_encoder`: ffmpeg `h264_nvenc` + `nvidia-smi`, `VIDEO_ENCODER`
  override, `libx264` fallback, NVENC preset mapping). Used by the highlight
  renderer only — NOT by the shell
  streamer entrypoint above.
- `infrastructure/docker/docker-compose.streaming.yml` — overlay adding the
  `streamer` service (profile `streaming`) on top of prod/staging. It does
  not modify any base compose file.
- `frontend/app/broadcast/page.tsx` — director console (operator view).
- `frontend/app/broadcast/output/page.tsx` — capture-ready director output
  (default streamer target; `?bg=brand` for the opaque plate).
- `frontend/app/overlays/{scorebug,killfeed,standings,lower-third}/page.tsx`
  — OBS-ready overlay routes.
