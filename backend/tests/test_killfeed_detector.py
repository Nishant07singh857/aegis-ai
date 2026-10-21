"""Phase 14 W2 — KillFeedDetector verification.

SYNTHETIC test: renders fake BGMI-style kill-feed text (white text on a
dark translucent strip in the top-left kill-feed band — the real
location measured by Phase 23 W1; the strip is a TEXT feed, so the
legacy action-word path is what must read it) onto test frames with
PIL, runs the Tesseract-based detector, and measures parse accuracy.

Also covers:
  * dedup: same kill across 10 frames -> exactly 1 emitted event
  * blank frame (solid colour) -> no detection, no crash
  * no-guess rule: garbage text -> None, never invented names

Run:  python3 backend/tests/test_killfeed_detector.py
Requires: tesseract binary + pytesseract + numpy + PIL (all free/offline).
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.vision.detectors.killfeed import KillFeedDetector

W, H = 1280, 720
REGION = (0.0, 0.24, 0.34, 0.52)  # matches DEFAULT_KILL_FEED_REGION (Phase 23: top-left)

_CASES = [
    # (rendered line, expected killer, expected victim, expected type)
    ("SoulMortal knocked GodLJonathan", "SoulMortal", "GodLJonathan", "knock"),
    ("Sc0utOP eliminated TeamMate01", "Sc0utOP", "TeamMate01", "kill"),
    ("JonathanOP finished CarryMinati", "JonathanOP", "CarryMinati", "kill"),
    ("MortaL_yt knocked Scout_OP", "MortaL_yt", "Scout_OP", "knock"),
    ("Ghatak99 eliminated DynamoGaming", "Ghatak99", "DynamoGaming", "kill"),
    ("SnaxOP knocked Owais_OP", "SnaxOP", "Owais_OP", "knock"),
    ("ClutchGod eliminated TeamSoulFan", "ClutchGod", "TeamSoulFan", "kill"),
    ("ViperOP knocked DarkLord99", "ViperOP", "DarkLord99", "knock"),
    ("Regaltos eliminated ScoutOP", "Regaltos", "ScoutOP", "kill"),
    ("JellyOP knocked NinjaJOD", "JellyOP", "NinjaJOD", "knock"),
]


def _font(size: int):
    # LiberationSans-Regular renders most cleanly for Tesseract in tests.
    for path in (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_frame(lines, seed=0):
    """Render a BGMI-style frame: dark bg + top-left translucent strip + white text."""
    rng = np.random.RandomState(seed)
    bg = np.full((H, W, 3), 26, dtype=np.uint8)  # dark map background
    img = Image.fromarray(bg[:, :, ::-1])  # BGR->RGB for PIL
    draw = ImageDraw.Draw(img, "RGBA")
    x1, y1, x2, y2 = int(W * REGION[0]), int(H * REGION[1]), int(W * REGION[2]), int(H * REGION[3])
    draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 0, 140))  # translucent strip
    font = _font(24)
    y = y1 + 12
    for line in lines:
        draw.text((x1 + 12, y), line, font=font, fill=(255, 255, 255, 255))
        y += 36
    arr = np.asarray(img.convert("RGB"))
    return arr[:, :, ::-1].copy()  # back to BGR


def main():
    det = KillFeedDetector()
    print(f"tesseract available: {det.ocr_available()}")
    if not det.ocr_available():
        print("RESULT: SKIPPED - tesseract binary missing (no OCR to measure).")
        print("The detector degrades gracefully: parse_frame returns [].")
        sys.exit(3)

    # ---- 1. accuracy ---------------------------------------------------
    correct, total, details = 0, 0, []
    for i, (line, killer, victim, etype) in enumerate(_CASES):
        det.clear_dedup()
        frame = render_frame([line], seed=i)
        events = det.parse_frame(frame, match_id="synthetic")
        total += 1
        ok = False
        if len(events) == 1:
            ev = events[0]
            ok = (
                ev.player == killer
                and ev.victim == victim
                and ev.event_type == etype
                and ev.weapon is None  # honest: icons are never guessed
            )
            details.append((line, ev.player, ev.victim, ev.event_type, ok))
        else:
            details.append((line, f"<{len(events)} events>", "", "", False))
        correct += ok
    acc = 100.0 * correct / total
    print(f"\naccuracy: {correct}/{total} = {acc:.1f}% (target >= 80%)")
    for line, k, v, t, ok in details:
        print(f"  [{'PASS' if ok else 'FAIL'}] {line!r:45} -> killer={k!r} victim={v!r} type={t}")

    # ---- 2. dedup: same kill across 10 frames -> 1 event ---------------
    det.clear_dedup()
    emitted = 0
    for i in range(10):
        emitted += len(det.parse_frame(render_frame([_CASES[0][0]], seed=100 + i)))
    dedup_ok = emitted == 1
    print(f"\ndedup (same kill, 10 frames): emitted={emitted} -> {'PASS' if dedup_ok else 'FAIL'}")

    # ---- 3. blank frame -> no detection, no crash ----------------------
    det.clear_dedup()
    blank = np.full((H, W, 3), 26, dtype=np.uint8)
    blank_events = det.parse_frame(blank)
    blank_ok = blank_events == []
    print(f"blank frame: events={len(blank_events)} -> {'PASS' if blank_ok else 'FAIL'}")

    # ---- 4. no-guess rule: garbage OCR text never invents names -------
    junk = ["", "...", "###", "knocked", "a b", "123 456 789 " * 3]
    no_guess_ok = all(det.parse_text(j) is None for j in junk)
    print(f"no-guess on garbage text: {'PASS' if no_guess_ok else 'FAIL'}")

    all_ok = acc >= 80.0 and dedup_ok and blank_ok and no_guess_ok
    print(f"\nOVERALL: {'PASS' if all_ok else 'FAIL'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
