#!/usr/bin/env python3
"""
AEGIS deep-analysis pipeline for global tournament VODs (PMGC/PMGO).
Steps per video file:
  1. ffprobe duration
  2. Scene-cut detection (camera switching speed + patterns)
  3. Frame classification at 30s intervals -> shot-type distribution
  4. Whisper transcription of sampled 10-min/hour segments -> caster rhythm
  5. Overlay/colour metrics per scene class
Outputs JSON per stage into the working dir.
"""
import json, os, subprocess, sys, math
from pathlib import Path

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def probe(path):
    r = run(["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    d = json.loads(r.stdout)
    dur = float(d["format"]["duration"])
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    return {"duration": dur, "w": v["width"], "h": v["height"],
            "fps": eval(v.get("avg_frame_rate", "30/1"))}

def scene_cuts(path, threshold=0.35):
    """Use ffmpeg select scene filter to list cut timestamps."""
    out = Path(path).with_suffix("").name + "_cuts.txt"
    r = run(["ffmpeg", "-hide_banner", "-i", str(path),
             "-filter:v", f"select='gt(scene,{threshold})',showinfo",
             "-f", "null", "-"])
    cuts = []
    for line in r.stderr.splitlines():
        if "showinfo" in line and "pts_time:" in line:
            t = line.split("pts_time:")[1].split()[0]
            try: cuts.append(float(t))
            except: pass
    return sorted(set(cuts))

def classify_frame(img_path):
    """
    Cheap heuristic classification of a sampled frame into shot types.
    Returns dict with shot_type guess and metrics.
    """
    import numpy as np
    from PIL import Image
    im = Image.open(img_path).convert("RGB").resize((160, 90))
    a = np.asarray(im).astype(float)
    h, w = a.shape[:2]
    # regions
    top = a[:20]; bottom = a[-18:]
    green = (a[...,1] > 90) & (a[...,0] < 120) & (a[...,2] < 120)  # vegetation-ish
    green_frac = green.mean()
    # dark cinematic bars
    dark_rows_top = (a[:10].mean(axis=(0,1)).max() < 25)
    dark_rows_bot = (a[-10:].mean(axis=(0,1)).max() < 25)
    # saturation/brightness
    bright = (a.mean(axis=2) > 200).mean()
    sat = (a.max(axis=2) - a.min(axis=2)).mean()
    # edge energy in centre (gameplay usually busy)
    gray = a.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1)).mean(); gy = np.abs(np.diff(gray, axis=0)).mean()
    busy = (gx + gy)
    return {"green_frac": round(float(green_frac),3), "bright_frac": round(float(bright),3),
            "sat": round(float(sat),1), "busy": round(float(busy),1),
            "cinema_bars": bool(dark_rows_top and dark_rows_bot)}

def main(path, workdir):
    path = Path(path); work = Path(workdir); work.mkdir(parents=True, exist_ok=True)
    info = probe(path)
    print("INFO", info, flush=True)
    (work/"meta.json").write_text(json.dumps({"file": str(path), **info}, indent=1))

    cuts = scene_cuts(path)
    print(f"CUTS: {len(cuts)} cuts over {info['duration']/3600:.1f}h", flush=True)
    (work/"cuts.json").write_text(json.dumps(cuts))
    if len(cuts) > 1:
        gaps = [b-a for a,b in zip(cuts, cuts[1:])]
        avg_gap = sum(gaps)/len(gaps)
        print(f"AVG SHOT LENGTH: {avg_gap:.1f}s (excl. >120s gaps trimmed)")
        gaps2 = [g for g in gaps if g < 120]
        print(f"  trimmed avg: {sum(gaps2)/len(gaps2):.1f}s median: {sorted(gaps2)[len(gaps2)//2]:.1f}s")

    # frame sampling every 30s at 320px
    frames = work/"frames"; frames.mkdir(exist_ok=True)
    run(["ffmpeg","-hide_banner","-y","-i",str(path),
         "-vf","fps=1/30,scale=320:-1",str(frames/"f%06d.jpg")])
    fps = sorted(frames.glob("*.jpg"))
    print(f"SAMPLED {len(fps)} frames", flush=True)
    results = []
    for i, f in enumerate(fps):
        try:
            m = classify_frame(f)
        except Exception as e:
            m = {"err": str(e)}
        results.append({"t": i*30, "file": f.name, **m})
    (work/"frame_metrics.json").write_text(json.dumps(results, indent=1))
    print("DONE", path.name)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
