# DATA_COLLECTION.md — The training-data flywheel (Phase 15, W1)

How every real match you record becomes labeled training data for AEGIS.

## The honest state of things

- **This VM has no GPU and no BGMI footage — nothing here can be trained.**
  What W1 builds is the *flywheel*: record real matches → log real events →
  auto-label → versioned dataset grows. Training happens later, on a machine
  with a GPU and this dataset.
- **Labels come ONLY from your spotter's real taps.** The auto-labeler never
  invents labels. That means **label quality = spotter accuracy**: a
  mistapped killer name or a late tap becomes a wrong label in the dataset.
  Garbage in, garbage out — the tooling counts and reports skips, it does
  not fix bad taps.

## The loop

```
1. RECORD    observer PC:  python -m app.ml.datasets.recorder --match-id <id> --out ./recordings --fps 10
2. LOG       spotter taps kills/knocks/wipes/zones during the match (event bus)
3. EXPORT    match's events -> events.jsonl (NormalizedEvent shape, one JSON per line)
4. LABEL     python -m app.ml.datasets.auto_labeler ...  ->  datasets/v1/, v2/, ...
5. REPEAT    every match appends a new version; the dataset only ever grows
```

## 1. Hardware

| Role | What you need | Notes |
|---|---|---|
| Observer PC (recording) | The machine running the observer screen + this repo's `backend/` | Recording is CPU-cheap at 10 fps; mp4v encoding of a 1080p screen uses one modest core |
| Observer phone(s) | BGMI spectator mode, as in your normal setup | The *screen being recorded* is what the model will learn from |
| Spotter phone | `/spotter` page, as in your normal setup | Every tap is a future label — tap accurately |
| Clock sync | All devices on correct time (NTP) | The join is by epoch timestamp; a 5s clock skew = 5s mislabeled frames |

Disk: ~10 fps 1080p mp4 ≈ 1–2 GB/hour. A 6-match tournament day ≈ 10 GB of
video; labeled JPEG datasets add ~100–300 MB per version.

## 2. Record a match

On the observer PC, from `backend/`:

```bash
python -m app.ml.datasets.recorder --match-id BMSD_GF_M3 --out ./recordings/BMSD_GF_M3 --fps 10
```

- Writes `match_BMSD_GF_M3.mp4` + `timestamps.csv` (`frame_idx,epoch_ts`).
- Stop with **Ctrl+C** — partial recordings are finalized cleanly.
- `--duration 1800` stops automatically after N seconds.
- Screen capture defaults to the free `mss` package (`pip install mss`).
  Smoke-test without a screen: `--source test` (synthetic frames).
- The loop paces itself to `--fps` on a monotonic clock; each frame's *real*
  epoch time is what lands in the CSV, so a slow machine degrades gracefully
  instead of drifting.

Record at **10 fps**. That is the training convention: enough to catch kill
moments, cheap to store, and every frame within ±2s of an event becomes a
labeled sample (~40 frames per event).

## 3. Export the event log

The labeler needs `events.jsonl`: one `NormalizedEvent`-shaped JSON object
per line (`event_type, ts, player, victim, team, victim_team, weapon,
payload, event_id, match_id`). Your spotter's taps already flow through the
feed API onto the event bus as `NormalizedEvent`s — dump the match's events
to JSONL, e.g.:

```python
from app.services.feed_adapter.schemas import NormalizedEvent
with open("events.jsonl", "w") as fh:
    for bus_event in match_events:  # whatever your match log source is
        e = NormalizedEvent(event_type=bus_event["type"], ts=bus_event["ts"], **bus_event["fields"])
        fh.write(e.model_dump_json() + "\n")
```

Keep the recorder's `timestamps.csv` next to it. Name files per match:
`recordings/BMSD_GF_M3/{match_*.mp4,timestamps.csv,events.jsonl}`.

## 4. Build the labeled dataset

From `backend/`:

```bash
python -m app.ml.datasets.auto_labeler \
  --video recordings/BMSD_GF_M3/match_BMSD_GF_M3.mp4 \
  --timestamps recordings/BMSD_GF_M3/timestamps.csv \
  --events recordings/BMSD_GF_M3/events.jsonl \
  --out ./training-data \
  --collector "Your Name"
```

What happens:

1. **Join** — every `kill / knock / team_wipe / zone_update` event is matched
   to the video frames within **±2s** (`--window` to change). Only real logged
   events become labels. Nothing is synthesized.
2. **Skip, loudly** — an event with no video window (recording started late,
   clock skew) is skipped and counted in `stats.json` with its reason
   (`no_video_window`, `non_label_event`, malformed lines). Skips are never
   silent.
3. **Extract** — only labeled frames are decoded to `frames/*.jpg` (quality 90).
4. **Manifest** — `manifest.csv` with
   `frame_path, ts, label_type, killer, victim, team, weapon, alive_count,
   zone_phase` (+ `event_id, match_id, frame_idx` provenance). Paths are
   relative to the dataset dir, so the dataset is portable.
5. **Version** — output goes to `training-data/datasets/v1/`, then `v2/`,
   … automatically. Old versions are never overwritten.
6. **Card** — `dataset_card.md` is rendered from the run's *real* numbers:
   who collected, when, how many samples, label source, skip breakdown.

### Fixing timestamp misalignment

Spotter taps lag what the spotter saw, and the observer phone (spectator
mode, ~30–60s behind live) may not share the recording PC's clock. If labels
look systematically early/late, re-run with a correction instead of
accepting bad labels:

```bash
python -m app.ml.datasets.auto_labeler ... --ts-offset -1.5
```

(Shifts every event timestamp by −1.5s before joining.)

## 5. Grow it, then train it

- One match ≈ one version. After a tournament day you have `v1…v6` — six
  independently reproducible labeled slices.
- Before training, read a new version's `dataset_card.md` and spot-check a
  dozen frames in `frames/` against their `manifest.csv` rows. If the
  spotter had a bad match, exclude that version — it costs you nothing to
  leave a version out.
- Training itself (GPU machine, Phase 15 W2+) consumes `datasets/vN/`
  directly: `frames/` + `manifest.csv` is the whole contract.

## Caveats (read before trusting the data)

- **Garbage in, garbage out.** Wrong taps → wrong labels → a model that
  confidently learns the wrong thing. The labeler reports skips; it cannot
  detect a confidently-wrong tap.
- Frames are sampled *around logged events only*. Quiet rotations with no
  events are not represented — this dataset teaches "what just happened",
  not ambient scene understanding.
- `zone_phase` uses the logger's explicit value when present, otherwise the
  Nth `zone_update` logged that match — derived from real logged events.
- `alive_count` is filled only when the event's payload carried it.

## Module reference

- `backend/app/ml/datasets/recorder.py` — `record_match()`, CLI
- `backend/app/ml/datasets/auto_labeler.py` — `build_dataset()`, CLI
- `backend/app/ml/datasets/selftest.py` — `python -m app.ml.datasets.selftest`
  (synthetic data only; proves the join math without real footage)
- `backend/app/ml/datasets/dataset_card.template.md` — the card template
