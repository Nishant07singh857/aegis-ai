#!/usr/bin/env bash
# W1-RESUME: resume interrupted AEGIS VOD downloads.
# Previous worker used 360p (verified: bmsd2026_gf_day2/3 are 640x360 h264).
# Run from ~/workspace/aegis-ai. Do NOT touch data/raw/validation/.
set -u
cd "$(dirname "$0")" || exit 1
YD="python3 -m yt_dlp"

# If YouTube bot-checks (HTTP 429 / "Sign in to confirm you're not a bot"), wait
# and retry later, or export EXTRA_ARGS='--extractor-args youtube:player_client=android'
EXTRA="${EXTRA_ARGS:-}"

download_one () { # id outfile
  local id="$1" out="$2"
  local part="data/raw/${out}.mp4.part"
  echo "=== $out ($id) ==="
  $YD --continue --no-playlist $EXTRA \
      -f "bv*[height<=360]+ba/b[height<=360]/b" --remux-video mp4 \
      -o "data/raw/${out}.mp4" \
      "https://www.youtube.com/watch?v=${id}" && return 0
  # Resume failed (format mismatch on .part): delete part, clean 480p redownload
  echo "!!! --continue failed for $out; deleting $part and redownloading at 480p"
  rm -f "$part"
  $YD --no-playlist $EXTRA \
      -f "bv*[height<=480]+ba/b[height<=480]/b" --remux-video mp4 \
      -o "data/raw/${out}.mp4" \
      "https://www.youtube.com/watch?v=${id}"
}

download_one 3hKvgc-nD74 bmps2026_gf_day1         # BMPS 2026 GF Day 1           6:55:05
download_one 2arETtsT7gA bmsd2026_gf_day1        # BMSD 2026 GF Day 1           7:02:55
download_one 0a6mkVQUuJ8 bmsd2026_lastchance_day2 # BMSD 2026 Last Chance Day 2  5:51:10
download_one Pqmg2dcht_M bmsd2026_semis_day1     # BMSD 2026 Semi-Finals Day 1  6:13:25
download_one ICfiTyAzdEA bmsd2026_semis_day2     # BMSD 2026 Semi-Finals Day 2  5:57:20
download_one 2IluEt2hfvo bmsd2026_semis_day3     # BMSD 2026 Semi-Finals Day 3  5:16:05

echo "=== verify ==="
for f in data/raw/*.mp4; do
  echo "-- $f"
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,codec_name \
    -show_entries format=duration,size \
    -of default=noprint_wrappers=1 "$f"
done
