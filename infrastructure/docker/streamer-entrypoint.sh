#!/usr/bin/env bash
# =============================================================================
# AEGIS AI — streamer entrypoint
#
# Renders the broadcast director page in headless Chromium on a virtual
# display (Xvfb), captures it with FFmpeg (x11grab), encodes with software
# x264, and pushes to every configured RTMP destination via the tee muxer.
#
# Required: at least ONE of YOUTUBE_STREAM_KEY / TWITCH_STREAM_KEY /
#           FACEBOOK_STREAM_KEY (stream keys are env-only, never in code).
# Optional tuning: STREAM_WIDTH, STREAM_HEIGHT, STREAM_FPS, VIDEO_BITRATE,
#                  VIDEO_PRESET, DIRECTOR_URL.
# Feed credential (Phase 32, K-C1): when DIRECTOR_URL renders
# /broadcast/output, the page embeds the game feed in a bare <img> that
# cannot send auth headers, so capture needs a short-lived signed feed
# token in the page URL. Provide FEED_TOKEN directly (minted beforehand
# by an operator via POST /api/v1/video/feed-token), OR set
# AEGIS_BACKEND_URL + AEGIS_API_KEY (operator key) and the entrypoint
# mints one (600s TTL). With neither, the entrypoint FAILS FAST rather
# than silently streaming a NO FEED slate. Overlay-only DIRECTOR_URLs
# (no /broadcast/output) skip this — they embed no game feed.
# =============================================================================
set -euo pipefail

WIDTH="${STREAM_WIDTH:-1280}"
HEIGHT="${STREAM_HEIGHT:-720}"
FPS="${STREAM_FPS:-30}"
BITRATE="${VIDEO_BITRATE:-4500k}"
PRESET="${VIDEO_PRESET:-veryfast}"
DIRECTOR_URL="${DIRECTOR_URL:-http://frontend:3000/broadcast/output?bg=brand}"
DISPLAY_NUM=":99"

fail() { echo "[streamer] ERROR: $*" >&2; exit 1; }

# --- at least one destination must be configured -----------------------------
OUTPUTS=()
if [ -n "${YOUTUBE_STREAM_KEY:-}" ]; then
  OUTPUTS+=("[f=flv]rtmp://a.rtmp.youtube.com/live2/${YOUTUBE_STREAM_KEY}")
fi
if [ -n "${TWITCH_STREAM_KEY:-}" ]; then
  OUTPUTS+=("[f=flv]rtmp://live.twitch.tv/app/${TWITCH_STREAM_KEY}")
fi
if [ -n "${FACEBOOK_STREAM_KEY:-}" ]; then
  OUTPUTS+=("[f=flv]rtmps://live-api-s.facebook.com:443/rtmp/${FACEBOOK_STREAM_KEY}")
fi
[ "${#OUTPUTS[@]}" -gt 0 ] || fail \
  "no stream destination configured — set at least one of YOUTUBE_STREAM_KEY, TWITCH_STREAM_KEY, FACEBOOK_STREAM_KEY"

TEE_URLS="$(IFS='|'; echo "${OUTPUTS[*]}")"
echo "[streamer] destinations: ${#OUTPUTS[@]} configured"

# --- feed credential (Phase 32, K-C1) -------------------------------------
# The output page embeds the game feed in a bare <img>, which cannot send
# auth headers, so headless capture carries a short-lived signed feed
# token in the page URL. Precedence: FEED_TOKEN env > mint via backend
# operator API key. Fail fast with instructions when neither is set:
# without a token the capture would silently show NO FEED (feed auth H3).
# Tokens are base64url (URL-safe alphabet), so no encoding is needed.
append_query() { # $1=url $2=param → url with the param appended
  case "$1" in
    *\?*) printf '%s&%s' "$1" "$2" ;;
    *) printf '%s?%s' "$1" "$2" ;;
  esac
}
case "${DIRECTOR_URL}" in
  *broadcast/output*)
    if [ -n "${FEED_TOKEN:-}" ]; then
      DIRECTOR_URL="$(append_query "${DIRECTOR_URL}" "feed_token=${FEED_TOKEN}")"
      echo "[streamer] using FEED_TOKEN from environment"
    elif [ -n "${AEGIS_BACKEND_URL:-}" ] && [ -n "${AEGIS_API_KEY:-}" ]; then
      BACKEND_BASE="$(printf '%s' "${AEGIS_BACKEND_URL}" | sed -E 's#/*$##')"
      MINTED="$(curl -fsS --max-time 10 -X POST "${BACKEND_BASE}/api/v1/video/feed-token" \
        -H "X-API-Key: ${AEGIS_API_KEY}" -H 'Content-Type: application/json' \
        -d '{"ttl_seconds":600}' 2>/dev/null \
        | sed -n 's/.*"feed_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' || true)"
      [ -n "${MINTED}" ] || fail \
        "could not mint a feed token from ${BACKEND_BASE} — check AEGIS_BACKEND_URL and AEGIS_API_KEY (operator key)"
      DIRECTOR_URL="$(append_query "${DIRECTOR_URL}" "feed_token=${MINTED}")"
      echo "[streamer] minted a short-lived feed token from the backend"
    else
      fail "DIRECTOR_URL renders /broadcast/output but no feed credential is configured — set FEED_TOKEN (minted by an operator via POST /api/v1/video/feed-token) or AEGIS_BACKEND_URL + AEGIS_API_KEY so one can be minted; without it the capture shows NO FEED (feed auth, H3)"
    fi
    ;;
  *)
    echo "[streamer] note: DIRECTOR_URL does not render /broadcast/output; no feed token attached"
    ;;
esac
echo "[streamer] director: ${DIRECTOR_URL%%\?*} @ ${WIDTH}x${HEIGHT} ${FPS}fps"

# --- virtual display ----------------------------------------------------------
Xvfb "${DISPLAY_NUM}" -screen 0 "${WIDTH}x${HEIGHT}x24" -nolisten tcp >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!
export DISPLAY="${DISPLAY_NUM}"

cleanup() {
  echo "[streamer] shutting down"
  kill "${FFMPEG_PID:-}" "${CHROME_PID:-}" "${XVFB_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- wait for the frontend to answer before launching the browser ------------
# Probe the frontend origin (scheme://host:port), whatever page DIRECTOR_URL points at.
FRONTEND_BASE="$(printf '%s' "${DIRECTOR_URL}" | sed -E 's#(https?://[^/?#]+).*#\1/#')"
for i in $(seq 1 60); do
  if curl -fsS -o /dev/null --max-time 3 "${FRONTEND_BASE}" 2>/dev/null; then
    echo "[streamer] frontend is up"
    break
  fi
  [ "$i" -eq 60 ] && fail "frontend did not answer at ${DIRECTOR_URL} after 60s"
  sleep 2
done

# --- headless-ish Chromium rendering into Xvfb --------------------------------
# --no-sandbox is required inside unprivileged containers; this is the
# standard container pattern, not a shortcut.
chromium \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --window-size="${WIDTH},${HEIGHT}" \
  --window-position=0,0 \
  --kiosk \
  --autoplay-policy=no-user-gesture-required \
  "${DIRECTOR_URL}" >/tmp/chromium.log 2>&1 &
CHROME_PID=$!

# Give the page time to boot its websocket feed and render the first frame.
sleep 12

# Normalize the bitrate to kilobits for -bufsize (accepts "4500k", "4500K", "6M").
RATE_NUM="${BITRATE%[kK]}"
if [[ "${BITRATE}" == *[mM] ]]; then
  RATE_NUM="$(( ${BITRATE%[mM]} * 1000 ))"
fi
BUFSIZE="$(( RATE_NUM * 2 ))k"

# --- capture + encode + multi-RTMP --------------------------------------------
# Video comes from the screen. There is no audio source yet (no real TTS
# audio exists), so we encode video-only (-an) — no faked silent track.
exec ffmpeg -hide_banner -loglevel warning \
  -f x11grab -video_size "${WIDTH}x${HEIGHT}" -framerate "${FPS}" -i "${DISPLAY_NUM}" \
  -an \
  -c:v libx264 -preset "${PRESET}" -tune zerolatency \
  -b:v "${BITRATE}" -maxrate "${BITRATE}" -bufsize "${BUFSIZE}" \
  -pix_fmt yuv420p -g "$(( FPS * 2 ))" -keyint_min "$(( FPS * 2 ))" \
  -f tee "${TEE_URLS}" &
FFMPEG_PID=$!

wait "${FFMPEG_PID}"
