# AEGIS AI — ML Training Guide (Phase 15)

Train the kill-feed detector (YOLOv8-nano) on free GPU, evaluate it honestly,
export it to ONNX, and drop it into AEGIS. No paid services anywhere in this
pipeline: Colab's free T4 GPU, open-source ultralytics, onnxruntime on CPU.

**What you're training and why.** The kill feed is the structured UI region
(top-right overlay) that lists every kill: *killer → victim*. The model learns
two classes:

| class | what it is | used for |
|---|---|---|
| `killfeed_panel` | the whole kill-feed region | crop target for OCR |
| `killfeed_entry` | one kill row inside the panel | per-row OCR crop |

The Phase 14 vision `KillFeedDetector` consumes these crops for OCR; this
guide produces the `.onnx` weights it runs.

**Honesty box (read first).** Nothing in this pipeline prints a single
headline "accuracy %". Detection quality is reported as **per-class
precision / recall / mAP@0.5 / mAP@0.5:0.95** plus TP/FP/FN counts. Every
data→quality number below is an **ESTIMATE** from experience with small
UI-detection tasks — not a promise, not a measurement. Your numbers will
come from `evaluate.py`, and they are the only ones that count.

---

## 0. Prerequisites

- The W1 dataset: a folder with `manifest.csv` + frame images
  (see `backend/app/ml/killfeed_detection/dataset.py` docstring for the
  exact manifest contract — columns, box units, background frames, COCO
  alternative).
- A Google account (Colab free tier) and ~2 GB free in Google Drive.
- This repo cloned wherever you run the commands.

Install the training requirements (Colab cell 2 does this; locally):

```bash
pip install -r backend/app/ml/training/requirements-train.txt
```

On a CPU-only machine, install PyTorch CPU first so pip doesn't pull the
~2.5 GB CUDA bundle:

```bash
pip install --extra-index-url https://download.pytorch.org/whl/cpu torch torchvision
pip install -r backend/app/ml/training/requirements-train.txt
```

---

## 1. Validate the dataset (local, CPU, 1 minute)

```bash
python backend/app/ml/training/train.py \
  --data /path/to/w1_dataset \
  --dry-run \
  --out runs/killfeed
```

This checks every manifest row (image exists, box inside frame, class names
valid), auto-splits by *image* into train/val/test (all boxes of one frame
stay together — splitting by box would leak near-duplicate frames into val
and inflate your metrics), and writes a YOLO dataset + `data.yaml` +
`dataset_card.json` under `runs/killfeed/_yolo_dataset/`.

Read `dataset_card.json`: it lists per-class/per-split counts and every
warning (tiny boxes dropped, boxes clipped, missing images). **Fix warnings
before paying for GPU time** — bad labels train a bad model, no matter how
long you train.

---

## 2. Train on Colab (free T4 GPU)

Upload the dataset folder to Google Drive (e.g. `MyDrive/aegis/killfeed_dataset/`),
then in a Colab notebook (Runtime → T4 GPU):

```python
# Cell 1 — mount Drive
from google.colab import drive
drive.mount('/content/drive')

# Cell 2 — install (takes ~3-5 min first time)
!pip -q install -r /content/drive/MyDrive/aegis/repo/backend/app/ml/training/requirements-train.txt

# Cell 3 — train (dataset already validated in step 1; --dry-run rebuild is cheap)
!cd /content/drive/MyDrive/aegis/repo && python backend/app/ml/training/train.py \
  --data /content/drive/MyDrive/aegis/killfeed_dataset \
  --epochs 50 \
  --batch 16 \
  --imgsz 640 \
  --out /content/drive/MyDrive/aegis/runs \
  --name killfeed_v1
```

Useful flags:

| flag | when to use |
|---|---|
| `--lr 0.001` | fine-tuning on <2k frames (default 0.01 is for larger sets) |
| `--freeze-backbone 10` | tiny dataset: train the head only, keep COCO backbone features |
| `--batch -1` | let ultralytics pick batch size from free VRAM (GPU only) |
| `--patience 20` | early stopping; raise to 30+ if mAP is still climbing |
| `--resume …/weights/last.pt` | continue after a Colab disconnect (checkpoints save every epoch) |
| `--no-augment` | ablation only — the UI-tuned policy is on by default |

**Colab survival tips.** Free Colab disconnects (~12 h max, often sooner when
idle). Checkpoints (`last.pt`) save every epoch, so `--resume` loses almost
nothing. Keep the browser tab focused; a cell that only trains won't keep the
session alive by itself — `--resume` is your safety net, not tab-focus tricks.

### How long does training take? (ESTIMATES)

YOLOv8-nano, 640px, Colab free T4 — all numbers below are **estimates**,
measured once on your data with `evaluate.py` being the only truth:

| labeled frames | epochs | est. T4 time | what to expect (ESTIMATE) |
|---|---|---|---|
| ~500 | 50 | ~15–30 min | prototype: finds the panel, misses entries, false boxes on other UI |
| ~1k | 50–100 | ~0.5–1.5 h | **usable prototype**: panel reliable, entries mostly found, some misses in fast fights |
| ~5k | 100 | ~3–6 h | solid: entries reliable, occasional miss on overlapping rows |
| ~10k diverse | 100–150 | ~6–12 h | production-ish for one tournament style |
| 50k+ diverse + hard negatives | 150+ | days (multi-run) | approaching the 99% *target* — see below |

**About "99%".** 99% is the TARGET, not a milestone you schedule. In
detection there is no single accuracy number; the target means roughly
"mAP@0.5 ≥ 0.99 on a held-out test set drawn from *different matches* than
training, with hard negatives mined from false positives". Realistically that
needs 50k+ diverse labeled frames **and** hard-negative mining (train →
evaluate → collect the false positives → label them as background → retrain).
Plan for it as an iterative loop, not a single training run. Anyone telling
you a frame count that *guarantees* 99% is selling something.

**Do NOT train full runs on CPU.** The `--smoke` test (step 1b) runs on CPU
in ~1–3 minutes and proves the pipeline works; a real 50-epoch run on CPU
takes days. CPU is for smoke tests and ONNX inference, not training.

### 1b. Smoke test (no GPU, no data — proves the machinery)

```bash
python backend/app/ml/training/train.py --smoke --out runs/killfeed_smoke
```

Builds 8 synthetic frames, trains 1 epoch on CPU, writes `best.pt`.
The weights are meaningless for detection (synthetic data) — the test only
proves arg parsing → manifest → YOLO dataset → model build → training step →
checkpoint all work.

---

## 3. Evaluate — read the per-class table, not a headline

```bash
python backend/app/ml/training/evaluate.py \
  --weights runs/killfeed_v1/weights/best.pt \
  --data /path/to/w1_dataset \
  --split val \
  --out runs/killfeed_eval_v1
```

You get `report.txt` / `report.md` / `report.json`:

```
  class             P       R   mAP50  mAP50-95     TP     FP     FN
  killfeed_panel  0.97    0.99   0.985    0.812    412      9      5
  killfeed_entry  0.88    0.81   0.864    0.601   1930    240    410
```

**How to read it (and what to do):**

- **mAP@0.5** is the headline for detection quality *per class*.
- **Recall** matters most for broadcast: a missed kill entry never reaches
  the overlay. Low entry recall → label more entries, especially overlapping
  rows and low-contrast (night map) frames.
- **Precision** matters for screen cleanliness: false boxes draw phantom
  entries. Low precision → add those frames as **background / hard-negative**
  images and retrain.
- **TP/FP/FN** are raw counts from the confusion matrix — the confusion
  *summary* the pipeline promises. If FP/FN show as 0 with a warning, the
  confusion matrix wasn't available; P/R/mAP are still valid.
- **Speed** (ms/image, FPS) tells you whether the model keeps up with the
  frame grabber on your CPU. YOLOv8-nano at 640px is typically 30–80 ms on a
  modern laptop CPU — fine for 1–2 FPS sampling; drop `--imgsz` to 416 for
  the export if you need faster.

Evaluate on `--split test` (frames from *different matches* than training)
before calling any model "done". Val guides tuning; test judges.

---

## 4. Export to verified ONNX

```bash
python backend/app/ml/training/export.py \
  --weights runs/killfeed_v1/weights/best.pt \
  --out exports/killfeed_v1
```

This writes `exports/killfeed_v1/killfeed.onnx` (opset pinned to 17) and then
**verifies** it: loads the graph in a fresh onnxruntime session and runs one
dummy forward pass. Output looks like:

```
Kill-feed export [onnx] -> VERIFIED
  path        : exports/killfeed_v1/killfeed.onnx
  size        : 12.3 MB
  opset       : 17
  ...
```

An export that fails verification exits non-zero and says `DO NOT SHIP`.
Never deploy an unverified file.

---

## 5. Drop the .onnx into AEGIS

1. Copy the verified file somewhere the backend can read it, e.g.
   `backend/models/killfeed_v1.onnx` (create the dir; keep versioned
   names like `killfeed_v1.onnx` when you iterate — model binaries are
   intentionally not committed).
2. Point the backend at it — no config file, just the env var
   (see `backend/.env.example`):

   ```bash
   export VISION_MODEL_PATH=/opt/aegis/models/killfeed_v1.onnx
   # or relative to AEGIS_MODEL_DIR (default: backend/models):
   export VISION_MODEL_PATH=killfeed_v1.onnx
   ```

   With the var set, the vision pipeline's kill-feed detector consults
   the trained model FIRST for entry localisation, then OCR-reads each
   entry box (hybrid); with the var unset it runs the pure Phase-14 OCR
   heuristic — identical behaviour to before. Details and the exact
   fallback rules: `docs/VISION_MODEL.md`, `backend/app/ml/vision_wiring.py`.
3. Feed it a recorded scrim and watch: panel box stable across frames,
   entries appearing/disappearing with kills, no phantom boxes on the
   minimap or player list. If phantoms appear, they're hard negatives for
   the next training round (step 3 → step 2).

---

## 6. Iterate: the loop that actually reaches the target

```
label more (esp. misses) → train → evaluate → export → test on scrim footage
        ↑                                                        |
        └──────── hard negatives from false positives ───────────┘
```

Each loop should move *one* number you chose in advance ("entry recall on
night-map frames from 0.81 → 0.90"). If a loop doesn't move it, the bottleneck
is data diversity, not epochs — label different matches, not more of the same.

---

## Troubleshooting

| symptom | likely cause | fix |
|---|---|---|
| `manifest.csv: missing required columns` | wrong header | see dataset.py docstring contract |
| `image not found` errors | relative paths vs dataset root | manifest `image_path` is relative to `--data` |
| `unknown class_name '…'` | typo or new class | add to `class_names` (config + data.yaml stay in sync) |
| train loss NaN in first epochs | lr too high for fine-tune | `--lr 0.001`, or `--freeze-backbone 10` |
| mAP plateaus early, train loss keeps falling | overfitting small data | more diverse frames; `--freeze-backbone`; stronger `--patience` won't help |
| Colab "CUDA out of memory" | batch too big | `--batch 8` (or `--batch -1` for auto) |
| `onnxsim not installed` warning | simplify pass skipped | now bundled in `requirements-train.txt`; the simplify pass runs by default |
| eval shows warnings, TP/FP/FN = 0 | confusion matrix unavailable for this export | P/R/mAP still valid; counts need a `.pt` eval |

## Files in this pipeline

```
backend/app/ml/killfeed_detection/
  __init__.py        package exports (mirrors fight_detection structure)
  dataset.py         manifest loading, validation, YOLO dataset builder
  model.py           KillFeedModel (YOLOv8n) + DetectionModelBase for future tasks
  augmentations.py   UI-tuned augmentation policy -> ultralytics hyperparams
  trainer.py         training orchestration + TrainingReport (real metrics only)
  evaluator.py       honest per-class P/R/mAP + confusion summary (no "accuracy %")
  export.py          ONNX export (opset pinned) + onnxruntime verification
backend/app/ml/training/
  train.py           CLI: --data --epochs --out [--smoke] [--dry-run]
  evaluate.py        CLI: honest metrics on held-out split
  export.py          CLI: verified ONNX export
  requirements-train.txt   pinned deps for Colab free tier
```

**Cost of this pipeline: ₹0 / $0.** Colab free T4, open-source everything,
CPU inference via onnxruntime. If Colab free tier ever blocks you, the same
commands run on any NVIDIA GPU box or Kaggle's free GPU — nothing here is
Colab-specific except the Drive mount in step 2.
