# VISION_MODEL.md — Trained Vision Model Integration (Phase 15, W3)

How the optional trained ONNX models plug into the Phase-14 heuristic
vision pipeline, and exactly what happens when they aren't there.

Two models are supported:

| model | pipeline | classes | backs |
|---|---|---|---|
| fight model | `app/ml/fight_detection/` | 7 fight classes | `fight_detector`, `knock_detector` (generic chain) |
| kill-feed model | `app/ml/killfeed_detection/` | `killfeed_panel`, `killfeed_entry` | `killfeed` (hybrid: model localises, OCR reads) |

## The honest summary

**Without a trained model the system is 100% heuristic.** That is a
supported, first-class configuration — not a degraded error state.
The registry logs it, health checks report `"mode": "heuristic"`,
and every detection still works through the Phase-14 heuristic
detectors. No invented accuracy numbers exist anywhere in this
integration; mAP/precision/recall are measured by the training
pipelines (`app/ml/*/evaluator.py`) and live in the training reports,
not here.

## Where the .onnx file goes

### Fight model

1. Train and export with the Phase-15 pipeline:
   `app/ml/fight_detection/trainer.py` → `ModelExporter.export()`
   produces `<model_name>.onnx` (input `input`, outputs
   `output_p3/p4/p5`, opset 17, see
   `app/ml/fight_detection/export.py`).
2. Copy the file somewhere the backend can read it, e.g.
   `backend/models/fight_detector.onnx` (create the directory; it is
   intentionally not committed — models are large binaries).
3. Point the backend at it (step 3 below).

### Kill-feed model (the Phase 15 W2 pipeline)

1. Train, evaluate and export per `docs/TRAINING.md`:
   `backend/app/ml/training/train.py` → `evaluate.py` → `export.py`
   produces `killfeed.onnx` (single output `(1, 4+2, 8400)`, opset 17 —
   the ultralytics YOLOv8 export layout, decoded with
   `decode_mode="yolov8"`).
2. Copy the verified file somewhere the backend can read it, e.g.
   `backend/models/killfeed_v1.onnx` (versioned names when you iterate).
3. Point the backend at it:

```bash
export VISION_MODEL_PATH=/opt/aegis/models/killfeed_v1.onnx
# or relative to AEGIS_MODEL_DIR (default: backend/models):
export VISION_MODEL_PATH=killfeed_v1.onnx
```

Also see the `VISION_MODEL_PATH` / `AEGIS_MODEL_DIR` entries in
`backend/.env.example`. No secrets are involved — it is a local file
path, not a credential. Never commit a model file; never commit a
`.env` with real paths if you consider them sensitive.

## Configuration reference

| Setting | Source | Default | Meaning |
|---|---|---|---|
| `VISION_MODEL_PATH` | env | *(unset)* | ONNX model file. Unset/empty/missing → heuristic-only mode. |
| `AEGIS_MODEL_DIR` | env | `backend/models` | Base dir for relative `VISION_MODEL_PATH`. |
| `conf_threshold` | detector config / `detect()` kwarg | `0.25` | Min class confidence kept after decode. |
| `nms_iou` | detector config / `detect()` kwarg | `0.45` | Per-class NMS IoU threshold. |
| `class_filter` | detector config / `detect()` kwarg | `None` (all) | e.g. `["player_knocked"]` to keep only some classes. |
| `max_detections` | detector config | `300` | Cap after NMS, highest-confidence first. |

Runtime deps (all free, CPU-only): `onnxruntime` + OpenCV, both in
the backend `ml` extra — `pip install '.[ml]'`. Both are imported
lazily; importing `app.ml` without them works fine.

## Fallback chain

```
                    ┌─────────────────────────────┐
  BGR frame ───────▶│      ChainedDetector        │
                    │        .detect()            │
                    └──────────────┬──────────────┘
                                   │ 1. trained model
                    ┌──────────────▼──────────────┐
                    │ TrainedDetector (ONNX)      │── boxes found ──▶ return (source=trained_model)
                    │ via VisionModelRegistry     │
                    └──────────────┬──────────────┘
                                   │ missing / raised / found nothing
                    ┌──────────────▼──────────────┐
                    │ Heuristic detector (Phase-14│── result ──▶ return (source=heuristic)
                    │ BaseDetector, injected)     │
                    └──────────────┬──────────────┘
                                   │ missing / raised
                    ┌──────────────▼──────────────┐
                    │ "unknown" empty result      │
                    │ status=NOT_FOUND, count=0,  │
                    │ extra_data.reasons explains │
                    │ which links failed and why  │
                    └─────────────────────────────┘
```

Rules:
- `VisionModelRegistry.has_model()` is a cheap env-var + file check.
  `get_detector(name)` returns a `TrainedDetector` or `None`
  (unknown name or no model) — the caller then uses heuristics.
- `ChainedDetector` never performs worse than heuristics alone: a
  model exception is logged and falls through; a model that finds
  nothing still lets the heuristic run (`require_model_boxes=True`
  by default).
- Every result's `extra_data` records its provenance
  (`chain_source`: `trained_model` | `heuristic` | `none`), so the
  broadcast layer can show which engine answered.

### Which detector names the model backs

`VisionModelRegistry.MODEL_DETECTOR_MAP`:

| name | model classes used | decode |
|---|---|---|
| `fight_detector` | all 7 (`player_shooting`, `player_knocked`, `explosion`, `muzzle_flash`, `blood_effect`, `damage_indicator`, `knock_animation`) | `yolov5` |
| `knock_detector` | `player_knocked`, `knock_animation`, `blood_effect`, `damage_indicator` | `yolov5` |
| `killfeed` | `killfeed_panel`, `killfeed_entry` | `yolov8` |

Any other name → `get_detector()` returns `None` (heuristic path).

### The kill-feed hybrid (why not a plain chain)

The kill-feed model is a REGION LOCALISER (boxes), while the
Phase-14 `KillFeedDetector` is a NAME READER (Tesseract OCR). A plain
`ChainedDetector` would be wrong here: a trained box carries no
killer/victim names, so "trained wins" would suppress the OCR events
the broadcast needs.

`app/ml/vision_wiring.py` builds the hybrid instead
(`build_killfeed_detector()`, used by `vision/runtime.py`):

```
  BGR frame ──▶ trained killfeed model (FIRST)
                  │ entry boxes found
                  ▼
              OCR each entry crop (same parse/confidence/dedup
              machinery as the heuristic) ──▶ named events
                  │ model absent / raised / found nothing /
                  │ crops yielded no named events
                  ▼
              plain heuristic whole-region OCR parse (unchanged)
```

The hybrid is a `KillFeedDetector` subclass: same `name`
(`"killfeed"`), same `async detect()` contract, same
`extra_data["events"]` NormalizedEvents — `runtime.py` needs no
contract changes. `extra_data["chain_source"]` records
`"trained_model+ocr"` vs the heuristic path, so operators can see
which engine answered. Until a real `killfeed.onnx` exists AND its
entry recall is measured high on real footage, the whole-region
heuristic fallback stays active — the hybrid never trusts the model
blindly.

## Result shape contract

`TrainedDetector.detect()` returns `app.ml.detection_result.DetectionResult`,
which mirrors the Phase-14 `vision` result type method-for-method
(`count`, `is_successful`, `get_best_detection()`,
`filter_by_confidence()`, `to_dict()`, plus `DetectionBox` with
`x1/y1/x2/y2/center/area`, `to_tuple()`, `to_xyxy()`, `iou()`,
`contains_point()`). Status follows Phase-14 convention: `SUCCESS`
when ≥1 box, `NOT_FOUND` when empty. The types are defined in
`app/ml` (not imported from `app.vision`) because the vision package
was under concurrent development — same shape, zero import coupling.

## Model-format contract (what the decoder expects)

### Fight model (`decode_mode="yolov5"`)

Produced by `ModelExporter` in this repo; other models must match or
reconfigure `TrainedDetectorConfig`:

- Input: `(1, 3, 640, 640)`, RGB, float32, `/255`, name `input`
  (any name works — the first session input is used).
- Outputs: `output_p3/p4/p5` = `(1, 3*(4+7), H, W)` raw logits,
  strides 8/16/32, anchors from `FightDetectionModel` config.
- Decode: YOLOv5-style — `xy = (sigmoid*2 − 0.5 + grid) * stride`,
  `wh = (sigmoid*2)² * anchor`; confidence = max sigmoid class score
  (no separate objectness head in this model).
- Preprocess letterboxes the BGR frame (keeps aspect ratio, gray
  padding) and maps boxes back to original frame pixels.

### Kill-feed model (`decode_mode="yolov8"`)

Produced by `KillFeedExporter` (`model.export(format="onnx")` on an
ultralytics YOLOv8 model); verified against ultralytics 8.4.182, the
version pinned in `training/requirements-train.txt`:

- Input: `(1, 3, 640, 640)`, RGB, float32, `/255` (first session
  input is used, any name).
- Output: ONE tensor `(1, 4+num_classes, N)` — rows 0..3 are the
  DECODED xyxy box corners in model-input pixels, rows 4.. are the
  sigmoided class scores (ultralytics bakes the DFL box decode and the
  sigmoid into the exported graph). A `(1, N, 4+num_classes)`
  transposed layout is accepted too.
- Decode: confidence = max class score; per-class NMS. No objectness
  head exists in YOLOv8. Classes default to
  `killfeed_panel` / `killfeed_entry` (order = `DatasetConfig.class_names`).

A wrongly-configured decode produces *wrong boxes*, not silence —
visually verify against real frames when swapping models
(`decode_mode="direct"` exists for pre-decoded `(N,6)` outputs).

## Wiring it up (for the integrator)

```python
from app.ml.registry import get_registry
from app.ml.fallback_chain import ChainedDetector
from app.vision.detectors.knock_detector import KnockDetector  # Phase-14

registry = get_registry()
if registry.load_model():          # False + logged when unconfigured
    logger.info("trained vision online", **registry.get_status())

detector = ChainedDetector(
    name="knock",
    trained=registry.get_detector("knock_detector"),  # None → heuristic only
    heuristic=KnockDetector(),
)
result = await detector.detect(frame)   # DetectionResult-shaped, always
```

Health: `GET` some endpoint → `get_registry().get_status()` →
`{"has_model": bool, "mode": "trained"|"heuristic", ...}`.

## What this integration does NOT do

- No training, no dataset handling, no metric computation — that is
  W1/W2's territory (`app/ml/killfeed_detection/`,
  `app/ml/fight_detection/`).
- No accuracy claims. If anyone asks "how accurate is the model",
  the answer lives in the evaluator report, never in this doc.
- No paid APIs, no network calls, no credentials. CPU-only local
  inference.
- No writes outside `app/ml/` + the two config lines above.
