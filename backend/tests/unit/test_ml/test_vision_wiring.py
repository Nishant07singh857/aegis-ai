# backend/tests/unit/test_ml/test_vision_wiring.py
"""
Phase 15 W1 — vision wiring tests.

Covers the two things W1 added/changed in the model→vision path:

1. ``TrainedDetector`` YOLOv8 decode (``decode_mode="yolov8"``):
   ultralytics-exported single-output ONNX ``(B, 4+C, N)`` with decoded
   xyxy boxes + sigmoided class scores. Pure-numpy decode — no
   onnxruntime, no GPU, no model file needed.
2. ``VisionModelRegistry`` killfeed registration + the
   ``build_killfeed_detector()`` hybrid (trained-first localisation,
   OCR reading, heuristic fallback).

All tests are honest about what they prove: the decode math, the
routing (trained-first / fallback), and the registry contract. None of
them claim detection quality — that comes from ``evaluate.py`` on real
footage, which this VM cannot run.
"""

import os
from types import SimpleNamespace

import numpy as np
import pytest

from app.ml.detection_result import DetectionBox
from app.ml.registry import (
    DETECTOR_MODEL_CONFIG,
    MODEL_DETECTOR_MAP,
    VisionModelRegistry,
)
from app.ml.trained_detector import (
    KILLFEED_DEFAULT_CLASSES,
    TrainedDetector,
    TrainedDetectorConfig,
)
from app.ml.vision_wiring import (
    KILLFEED_ENTRY_CLASS,
    build_chained_detector,
    build_killfeed_detector,
)
from app.vision.detectors.killfeed import KillFeedDetector


def _make_yolov8_detector(**overrides):
    cfg = TrainedDetectorConfig(
        classes=list(KILLFEED_DEFAULT_CLASSES),
        decode_mode="yolov8",
        conf_threshold=0.0,  # keep everything; tests filter explicitly
        nms_iou=1.0,         # no NMS suppression in decode tests
        **overrides,
    )
    return TrainedDetector(name="test_killfeed", config=cfg)


def _yolov8_output(boxes_xyxy, scores, n_total=8400):
    """
    Build a fake (1, 6, N) ultralytics-style output.

    boxes_xyxy: (K, 4) decoded xyxy in model-input pixels.
    scores:     (K, 2) sigmoided class scores.
    Remaining N-K anchors are low-confidence background.
    """
    out = np.zeros((1, 6, n_total), dtype=np.float32)
    out[0, 4:, :] = 0.01  # background: tiny scores everywhere
    k = len(boxes_xyxy)
    out[0, 0:4, :k] = np.asarray(boxes_xyxy, dtype=np.float32).T
    out[0, 4:6, :k] = np.asarray(scores, dtype=np.float32).T
    return [out]


@pytest.mark.unit
@pytest.mark.ml
class TestYolov8Decode:
    """decode_mode='yolov8' turns the ultralytics export layout into boxes."""

    def test_single_output_layout(self):
        det = _make_yolov8_detector()
        outputs = _yolov8_output(
            boxes_xyxy=[[100.0, 50.0, 300.0, 90.0],   # entry row
                        [90.0, 40.0, 320.0, 200.0]],  # panel
            scores=[[0.05, 0.92],   # -> killfeed_entry (id 1)
                    [0.88, 0.10]],  # -> killfeed_panel (id 0)
            n_total=64,
        )
        boxes, labels, confs = det._decode_outputs(
            outputs, scale=1.0, pad=(0.0, 0.0),
            frame_width=640, frame_height=640,
            conf_threshold=0.5, nms_iou=1.0, class_filter=None,
        )
        assert len(boxes) == 2
        by_label = {lb: (b, c) for b, lb, c in zip(boxes, labels, confs)}
        entry_box, entry_conf = by_label["killfeed_entry"]
        assert (entry_box.x, entry_box.y,
                entry_box.x + entry_box.width,
                entry_box.y + entry_box.height) == pytest.approx(
            (100.0, 50.0, 300.0, 90.0))
        assert entry_conf == pytest.approx(0.92)
        panel_box, panel_conf = by_label["killfeed_panel"]
        assert panel_conf == pytest.approx(0.88)
        assert (panel_box.x, panel_box.y) == pytest.approx((90.0, 40.0))

    def test_transposed_layout_accepted(self):
        det = _make_yolov8_detector()
        out = _yolov8_output(
            boxes_xyxy=[[10.0, 20.0, 60.0, 40.0]],
            scores=[[0.02, 0.95]],
            n_total=32,
        )[0].transpose(0, 2, 1)  # (1, N, 6)
        boxes, labels, confs = det._decode_outputs(
            [out], scale=1.0, pad=(0.0, 0.0),
            frame_width=640, frame_height=640,
            conf_threshold=0.5, nms_iou=1.0, class_filter=None,
        )
        assert len(boxes) == 1
        assert labels[0] == "killfeed_entry"
        assert confs[0] == pytest.approx(0.95)

    def test_letterbox_is_undone(self):
        """Model-input coords map back to frame coords through pad+scale."""
        det = _make_yolov8_detector()
        outputs = _yolov8_output(
            boxes_xyxy=[[320.0, 320.0, 640.0, 400.0]],
            scores=[[0.01, 0.9]],
            n_total=16,
        )
        # frame 1280x720 letterboxed into 640x640: scale=0.5, pad_y=(640-360)/2
        boxes, _, _ = det._decode_outputs(
            outputs, scale=0.5, pad=(0.0, 140.0),
            frame_width=1280, frame_height=720,
            conf_threshold=0.5, nms_iou=1.0, class_filter=None,
        )
        assert len(boxes) == 1
        b = boxes[0]
        assert (b.x, b.y, b.x + b.width, b.y + b.height) == pytest.approx(
            (640.0, 360.0, 1280.0, 520.0))

    def test_conf_threshold_filters(self):
        det = _make_yolov8_detector()
        outputs = _yolov8_output(
            boxes_xyxy=[[0.0, 0.0, 10.0, 10.0]],
            scores=[[0.01, 0.4]],
            n_total=16,
        )
        boxes, _, _ = det._decode_outputs(
            outputs, scale=1.0, pad=(0.0, 0.0),
            frame_width=640, frame_height=640,
            conf_threshold=0.5, nms_iou=1.0, class_filter=None,
        )
        assert boxes == []

    def test_bad_shape_raises_clear_error(self):
        det = _make_yolov8_detector()
        with pytest.raises(ValueError, match="yolov8 decode"):
            det._decode_yolov8(np.zeros((1, 7, 100), dtype=np.float32))

    def test_entry_class_constant_matches_dataset_order(self):
        # KILLFEED_ENTRY_CLASS must be the 2nd class — the dataset's
        # class_names order defines class id 1.
        assert KILLFEED_ENTRY_CLASS == "killfeed_entry"
        assert KILLFEED_DEFAULT_CLASSES[1] == KILLFEED_ENTRY_CLASS


@pytest.mark.unit
@pytest.mark.ml
class TestRegistryKillfeed:
    """The registry knows the killfeed model and its decode config."""

    def test_killfeed_registered(self):
        assert MODEL_DETECTOR_MAP["killfeed"] == ["killfeed_panel", "killfeed_entry"]
        cfg = DETECTOR_MODEL_CONFIG["killfeed"]
        assert cfg["decode_mode"] == "yolov8"
        assert cfg["classes"] == ["killfeed_panel", "killfeed_entry"]

    def test_no_model_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("VISION_MODEL_PATH", raising=False)
        reg = VisionModelRegistry()
        assert reg.has_model() is False
        assert reg.get_detector("killfeed") is None
        assert reg.get_detector("no_such_detector") is None

    def test_missing_file_is_honest_no_model(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VISION_MODEL_PATH", str(tmp_path / "nope.onnx"))
        reg = VisionModelRegistry()
        assert reg.has_model() is False
        assert reg.get_detector("killfeed") is None

    def test_get_detector_applies_yolov8_config(self, monkeypatch, tmp_path):
        # The registry must hand the killfeed detector a yolov8 decode
        # config. A fake file is enough for has_model(); the session is
        # stubbed so no real model bytes are ever loaded.
        fake_onnx = tmp_path / "killfeed.onnx"
        fake_onnx.write_bytes(b"not-a-real-model")
        monkeypatch.setenv("VISION_MODEL_PATH", str(fake_onnx))
        reg = VisionModelRegistry()
        assert reg.has_model() is True
        monkeypatch.setattr(reg, "_session", object())  # skip real load
        det = reg.get_detector("killfeed")
        assert det is not None
        assert det.config.decode_mode == "yolov8"
        assert det.config.classes == ["killfeed_panel", "killfeed_entry"]
        assert det.config.class_filter == ["killfeed_panel", "killfeed_entry"]
        assert det.name == "trained_killfeed"


@pytest.mark.unit
@pytest.mark.ml
class TestKillfeedDetectorBuilder:
    """build_killfeed_detector(): hybrid when a model exists, heuristic when not."""

    def test_no_model_gives_plain_heuristic(self):
        det = build_killfeed_detector(trained=None)
        assert isinstance(det, KillFeedDetector)
        assert type(det) is KillFeedDetector  # not the hybrid subclass
        assert det.name == "killfeed"

    def test_chained_builder_returns_heuristic_unchanged(self, monkeypatch):
        monkeypatch.delenv("VISION_MODEL_PATH", raising=False)
        heur = object()
        assert build_chained_detector("knock_detector", heur) is heur

    async def test_hybrid_consults_trained_first(self, monkeypatch):
        entry = DetectionBox(x=100.0, y=50.0, width=200.0, height=40.0,
                             confidence=0.9, label="killfeed_entry")

        async def fake_detect(frame, **kwargs):
            return SimpleNamespace(
                boxes=[entry], detector_name="fake_trained",
                labels=["killfeed_entry"], confidences=[0.9],
            )

        fake_trained = SimpleNamespace(detect=fake_detect)
        det = build_killfeed_detector(trained=fake_trained)
        assert type(det) is not KillFeedDetector  # hybrid subclass

        fake_event = SimpleNamespace(
            event_type="kill",
            payload={"ocr_confidence": 92.0},
            model_dump=lambda: {"event_type": "kill", "player": "A",
                                "victim": "B"},
        )
        monkeypatch.setattr(
            det, "_ocr_region_to_events", lambda crop, mid, now: [fake_event])

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = await det.detect(frame, match_id="m1")

        assert result.is_successful
        assert result.extra_data["chain_source"] == "trained_model+ocr"
        assert len(result.extra_data["events"]) == 1
        assert result.extra_data["events"][0]["player"] == "A"
        assert result.labels == ["kill"]
        # trained entry box is what the result reports
        assert result.boxes[0].x == pytest.approx(100.0)

    async def test_hybrid_falls_back_when_trained_raises(self, monkeypatch):
        async def boom(frame, **kwargs):
            raise RuntimeError("bad model bytes")

        det = build_killfeed_detector(trained=SimpleNamespace(detect=boom))
        # heuristic path: no tesseract in this env → parse_frame yields []
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = await det.detect(frame, match_id="m1")

        assert det._trained_failures == 1
        # Fell back to the heuristic: no trained_model+ocr provenance, and
        # no guessed events — an honest empty result.
        assert result.extra_data.get("chain_source") != "trained_model+ocr"
        assert result.extra_data.get("events", []) == []

    async def test_hybrid_falls_back_when_trained_finds_nothing(self, monkeypatch):
        async def empty(frame, **kwargs):
            return SimpleNamespace(boxes=[], detector_name="fake",
                                   labels=[], confidences=[])

        det = build_killfeed_detector(trained=SimpleNamespace(detect=empty))
        seen = []
        monkeypatch.setattr(
            det, "_ocr_region_to_events",
            lambda crop, mid, now: seen.append(crop) or [])
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = await det.detect(frame, match_id="m1")
        # Trained found nothing → per-entry OCR never ran; the heuristic
        # whole-region parse ran instead (and found nothing → honest empty).
        assert seen == []
        assert result.extra_data.get("events", []) == []
