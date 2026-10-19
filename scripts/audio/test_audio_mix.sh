#!/usr/bin/env bash
# AEGIS AI — test the broadcast audio mix without launching the stream.
#
# Captures N seconds from the commentary PCM FIFO (and optionally the
# game-audio input) into a WAV you can listen to. Verifies the exact
# FFmpeg input/filter chain the stream service uses.
#
# Usage:
#   scripts/audio/test_audio_mix.sh [seconds] [out.wav]
#
# Prereqs: backend running, commentary feed started
#   (POST /api/v1/audio/feed/start, or the director "Audio mixer" card),
#   at least one line queued (POST /api/v1/audio/commentary).
# Game audio (optional): export AEGIS_GAME_AUDIO_INPUT="alsa:hw:1,0"
#   or "file:/path/game_audio.mkv" — same format the stream service uses.
set -euo pipefail

SECS="${1:-10}"
OUT="${2:-/tmp/aegis_audio_mix_test.wav}"
AUDIO_DIR="${AEGIS_AUDIO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)/audio_out}"
FIFO="$AUDIO_DIR/pipes/commentary.pcm"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found on PATH"; exit 1; }
if [ ! -p "$FIFO" ]; then
  echo "No commentary FIFO at $FIFO."
  echo "Start the feed first: POST /api/v1/audio/feed/start (or the director 'Audio mixer' card)."
  exit 1
fi

INPUTS=(-f s16le -ar 48000 -ac 2 -i "$FIFO")
FILTER="[0:a]volume=1.0[aout]"
MAP=("[aout]")

if [ -n "${AEGIS_GAME_AUDIO_INPUT:-}" ] && [[ "$AEGIS_GAME_AUDIO_INPUT" == *:* ]]; then
  FMT="${AEGIS_GAME_AUDIO_INPUT%%:*}"
  DEV="${AEGIS_GAME_AUDIO_INPUT#*:}"
  INPUTS+=(-f "$FMT" -i "$DEV")
  FILTER="[0:a]volume=1.0[c0];[1:a]volume=1.0[c1];[c0][c1]amix=inputs=2:normalize=0[aout]"
  echo "Mixing game input: $FMT:$DEV"
else
  echo "Commentary only (set AEGIS_GAME_AUDIO_INPUT='<fmt>:<dev>' to add game audio)."
fi

echo "Capturing ${SECS}s -> $OUT ..."
ffmpeg -hide_banner -loglevel warning -y \
  "${INPUTS[@]}" \
  -filter_complex "$FILTER" \
  -map "${MAP[@]}" \
  -t "$SECS" \
  "$OUT"

echo "--- volumedetect ---"
ffmpeg -hide_banner -i "$OUT" -af volumedetect -f null - 2>&1 | grep -E "max_volume|mean_volume" || true
echo "Done. Play it: ffplay $OUT"
