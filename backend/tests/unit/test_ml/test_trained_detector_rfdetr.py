# backend/tests/unit/test_ml/test_trained_detector_rfdetr.py
"""
Phase 33 W4 — RF-DETR decode, GATED (K-H7).

What this proves, and what it does NOT prove:

Proves (all with synthetic/mock tensors — NO real model file):
  1. decode_mode="rfdetr" decodes the rfdetr 1.11.2 export layout:
     dets (B,Q,4) normalized cxcywh + labels (B,Q,C) raw logits,
     background = last slot, dropped. Per-class sigmoid + GLOBAL
     ranking over Q*C' pairs: one query scoring high on BOTH
     classes yields TWO boxes (a per-query argmax would drop one).
  2. cxcywh -> xyxy in model-input pixels (384 default here), and
     the shared path's letterbox-undo + conf threshold still apply.
  3. normalize_imagenet=True applies ImageNet mean/std after /255;
     the default (False) path is byte-identical to before.
  4. A mocked ONNX session runs end-to-end through detect() with
     the rfdetr config — the placeholder the patch was tested
     against. The mock is labelled as such everywhere; it is NOT
     best.onnx and proves nothing about real-model accuracy.
  5. GATING: the live path is unchanged without best.onnx — the
     registry default for "killfeed" is still "yolov8", the
     TrainedDetector default is still "yolov5"/normalize=False,
     and no-model still yields no detector (heuristic fallback).
     rfdetr activates ONLY via an explicit decode_mode override.
  6. YOLO26 fallback: a YOLO26 ultralytics export shares the
     yolov8 single-output layout (B, 4+C, N); the existing yolov8
     decode turns it into panel/entry boxes. This test uses a
     synthetic tensor in that layout, NOT a real YOLO26 file.

Does NOT prove: any real-model accuracy, any behaviour of a real
best.onnx, or that RF-DETR is wired live. best.onnx is absent from
this repo (verified: git ls-files *.onnx is empty) and the smoke
test against the real file is still pending — see the W4 report.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from app.ml.registry import (
    DETECTOR_MODEL_CONFIG,
    VisionModelRegistry,
)
from app.ml.trained_detector import (
    KILLFEED_DEFAULT_CLASSES,
    TrainedDetector,
    TrainedDetectorConfig,
)


def _make_rfdetr_detector(**overrides):
    kwargs = dict(
        classes=list(KILLFEED_DEFAULT_CLASSES),
        decode_mode="rfdetr",
        normalize_imagenet=True,
        input_size=(384, 384),
        conf_threshold=0.5,
        nms_iou=1.0,  # no NMS suppression in decode tests
    )
    kwargs.update(overrides)
    cfg = TrainedDetectorConfig(**kwargs)
    return TrainedDetector(name="test_rfdetr", config=cfg)


def _rfdetr_outputs():
    """Synthetic rfdetr-style pair: dets (1,4,4), labels (1,4,3).

    Classes: 0=killfeed_panel, 1=killfeed_entry, 2=background (dropped).
    Query 2 scores high on BOTH classes — the global top-k case.
    Query 3 is all-low and must be thresholded out.
    """
    dets = np.array([[
        [0.25, 0.25, 0.50, 0.25],   # q0 -> model (0, 48, 192, 144)
        [0.75, 0.50, 0.25, 0.50],   # q1 -> model (240, 96, 336, 288)
        [0.50, 0.50, 0.20, 0.20],   # q2 -> model (153.6, 153.6, 230.4, 230.4)
        [0.10, 0.10, 0.10, 0.10],   # q3 -> low scores, filtered out
    ]], dtype=np.float32)
    labels = np.array([[
        [2.0, -1.0, -5.0],    # q0: panel 0.881, entry 0.269
        [-2.0, 3.0, 0.0],     # q1: panel 0.119, entry 0.953
        [4.0, 3.5, -10.0],    # q2: panel 0.982, entry 0.971 (BOTH kept)
        [-5.0, -5.0, 5.0],    # q3: real classes ~0.007, bg high (dropped)
    ]], dtype=np.float32)
    return [dets, labels]


def _decode(det, outputs):
    return det._decode_outputs(
        outputs, scale=1.0, pad=(0.0, 0.0),
        frame_width=384, frame_height=384,
        conf_threshold=0.5, nms_iou=1.0, class_filter=None,
    )


@pytest.mark.unit
@pytest.mark.ml
class TestRfdetrDecode:
    """decode_mode='rfdetr' turns dets+labels into boxes."""

    def test_sigmoid_bg_drop_and_xyxy(self):
        det = _make_rfdetr_detector()
        raw = det._decode_rfdetr(_rfdetr_outputs())
        # Raw rows are score-desc across ALL Q*C' pairs.
        assert raw.shape[1] == 6
        top = raw[0]
        assert top[5] == pytest.approx(0)            # q2, panel
        assert top[4] == pytest.approx(0.982014, abs=1e-4)
        assert top[0:4] == pytest.approx((153.6, 153.6, 230.4, 230.4))
        # Background slot never appears as a class id.
        assert set(raw[:, 5].astype(int)) <= {0, 1}
        # No score equals sigmoid(bg) of any query: bg is dropped,
        # not relabelled (q3's bg logit 5.0 -> 0.993 would stand out).
        assert not np.any(np.isclose(raw[:, 4], 0.993307, atol=1e-4))

    def test_one_query_two_classes_not_argmax_dropped(self):
        """The point of global top-k: q2 yields panel AND entry boxes."""
        det = _make_rfdetr_detector()
        boxes, labels, confs = _decode(det, _rfdetr_outputs())
        assert len(boxes) == 4  # q0-panel, q1-entry, q2-panel, q2-entry
        by_label = {}
        for b, lb, c in zip(boxes, labels, confs):
            by_label.setdefault(lb, []).append((b, c))
        assert len(by_label["killfeed_panel"]) == 2
        assert len(by_label["killfeed_entry"]) == 2
        q2_panel = [b for b, _ in by_label["killfeed_panel"]
                    if abs(b.x - 153.6) < 1e-6][0]
        q2_entry = [b for b, _ in by_label["killfeed_entry"]
                    if abs(b.x - 153.6) < 1e-6][0]
        assert (q2_panel.x, q2_panel.y,
                q2_panel.x + q2_panel.width,
                q2_panel.y + q2_panel.height) == pytest.approx(
            (153.6, 153.6, 230.4, 230.4))
        assert (q2_entry.x, q2_entry.y) == pytest.approx((153.6, 153.6))

    def test_named_outputs_matched_not_positional(self):
        """Outputs arriving as [labels, dets] still decode via names."""
        session = SimpleNamespace(get_outputs=lambda: [
            SimpleNamespace(name="labels"),
            SimpleNamespace(name="dets"),
        ])
        det = _make_rfdetr_detector()
        det._session = session  # injected mock session (not a real model)
        dets, labels = _rfdetr_outputs()
        boxes, lbls, _ = _decode(det, [labels, dets])
        assert len(boxes) == 4
        assert set(lbls) == {"killfeed_panel", "killfeed_entry"}

    def test_shape_fallback_without_session_names(self):
        """No session at all -> shape fallback ((*,*,4) = dets)."""
        det = _make_rfdetr_detector()  # session=None, no output names
        boxes, _, _ = _decode(det, _rfdetr_outputs())
        assert len(boxes) == 4

    def test_missing_output_raises_clear_error(self):
        det = _make_rfdetr_detector()
        dets, _ = _rfdetr_outputs()
        with pytest.raises(ValueError, match="rfdetr decode needs"):
            det._decode_rfdetr([dets])

    def test_letterbox_is_undone(self):
        """Model-input coords map back through pad+scale, as yolov8 does."""
        det = _make_rfdetr_detector()
        dets = np.array([[[0.5, 0.5, 0.5, 0.5]]], dtype=np.float32)
        labels = np.array([[[4.0, -5.0, -5.0]]], dtype=np.float32)
        boxes, lbls, _ = det._decode_outputs(
            [dets, labels], scale=0.3, pad=(0.0, 84.0),
            frame_width=1280, frame_height=720,
            conf_threshold=0.5, nms_iou=1.0, class_filter=None,
        )
        assert len(boxes) == 1
        b = boxes[0]
        assert (b.x, b.y, b.x + b.width, b.y + b.height) == pytest.approx(
            (320.0, 40.0, 960.0, 680.0))

    def test_conf_threshold_filters(self):
        det = _make_rfdetr_detector()
        boxes, _, _ = det._decode_outputs(
            _rfdetr_outputs(), scale=1.0, pad=(0.0, 0.0),
            frame_width=384, frame_height=384,
            conf_threshold=0.99, nms_iou=1.0, class_filter=None,
        )
        assert boxes == []


@pytest.mark.unit
@pytest.mark.ml
class TestRfdetrPreprocess:
    """ImageNet normalisation only when explicitly enabled."""

    def test_imagenet_branch(self):
        det = _make_rfdetr_detector()
        frame = np.zeros((384, 384, 3), dtype=np.uint8)
        frame[:, :] = (0, 0, 255)  # solid red in BGR -> RGB (1, 0, 0)
        tensor, scale, pad = det._preprocess(frame)
        assert tensor.shape == (1, 3, 384, 384)
        assert scale == pytest.approx(1.0)
        assert pad == pytest.approx((0.0, 0.0))
        assert tensor[0, 0, 0, 0] == pytest.approx((1 - 0.485) / 0.229)
        assert tensor[0, 1, 0, 0] == pytest.approx((0 - 0.456) / 0.224)
        assert tensor[0, 2, 0, 0] == pytest.approx((0 - 0.406) / 0.225)

    def test_default_path_unchanged_plain_div255(self):
        det = _make_rfdetr_detector(normalize_imagenet=False)
        frame = np.zeros((384, 384, 3), dtype=np.uint8)
        frame[:, :] = (0, 0, 255)
        tensor, _, _ = det._preprocess(frame)
        assert tensor[0, 0, 0, 0] == pytest.approx(1.0)
        assert tensor[0, 1, 0, 0] == pytest.approx(0.0)
        assert tensor[0, 2, 0, 0] == pytest.approx(0.0)


@pytest.mark.unit
@pytest.mark.ml
class TestRfdetrMockSessionEndToEnd:
    """detect() through a MOCKED ort session (placeholder, not best.onnx)."""

    async def test_detect_with_mock_session(self):
        dets, labels = _rfdetr_outputs()
        seen = {}

        class _MockSession:
            def get_inputs(self):
                return [SimpleNamespace(name="input")]
            def get_outputs(self):
                return [SimpleNamespace(name="dets"),
                        SimpleNamespace(name="labels")]
            def run(self, _names, inputs):
                seen["tensor"] = inputs["input"]
                return [dets, labels]

        det = _make_rfdetr_detector()
        det._session = _MockSession()
        frame = np.zeros((640, 640, 3), dtype=np.uint8)
        result = await det.detect(frame)
        assert result.is_successful
        assert len(result.boxes) == 4
        # Input was ImageNet-normalised: black frame -> (0-0.485)/0.229.
        assert seen["tensor"].shape == (1, 3, 384, 384)
        assert float(seen["tensor"][0, 0].mean()) == pytest.approx(
            (0 - 0.485) / 0.229)
        # Letterbox undo: 640 -> 384 scale is 0.6; q0 model box
        # (0, 48, 192, 144) lands at frame (0, 80, 320, 240).
        panel_boxes = [b for b, lb in zip(result.boxes, result.labels)
                       if lb == "killfeed_panel"]
        assert any((b.x, b.y, b.x + b.width, b.y + b.height)
                   == pytest.approx((0.0, 80.0, 320.0, 240.0))
                   for b in panel_boxes)


@pytest.mark.unit
@pytest.mark.ml
class TestRfdetrGating:
    """Without best.onnx / without an explicit override, nothing changes."""

    def test_defaults_are_not_rfdetr(self):
        cfg = TrainedDetectorConfig()
        assert cfg.decode_mode == "yolov5"
        assert cfg.normalize_imagenet is False

    def test_registry_killfeed_default_still_yolov8(self):
        cfg = DETECTOR_MODEL_CONFIG["killfeed"]
        assert cfg["decode_mode"] == "yolov8"
        assert cfg["classes"] == ["killfeed_panel", "killfeed_entry"]
        assert "normalize_imagenet" not in cfg
        assert "input_size" not in cfg

    def test_registry_get_detector_still_yolov8(self, monkeypatch, tmp_path):
        fake_onnx = tmp_path / "killfeed.onnx"
        fake_onnx.write_bytes(b"not-a-real-model")
        monkeypatch.setenv("VISION_MODEL_PATH", str(fake_onnx))
        reg = VisionModelRegistry()
        monkeypatch.setattr(reg, "_session", object())  # skip real load
        det = reg.get_detector("killfeed")
        assert det is not None
        assert det.config.decode_mode == "yolov8"
        assert det.config.normalize_imagenet is False
        assert det.config.input_size == (640, 640)

    def test_registry_explicit_override_reaches_rfdetr(self, monkeypatch, tmp_path):
        """The opt-in plumbing works — but only when explicitly asked."""
        fake_onnx = tmp_path / "killfeed.onnx"
        fake_onnx.write_bytes(b"not-a-real-model")
        monkeypatch.setenv("VISION_MODEL_PATH", str(fake_onnx))
        reg = VisionModelRegistry()
        monkeypatch.setattr(reg, "_session", object())
        det = reg.get_detector(
            "killfeed", decode_mode="rfdetr",
            normalize_imagenet=True, input_size=(384, 384),
        )
        assert det.config.decode_mode == "rfdetr"
        assert det.config.normalize_imagenet is True
        assert det.config.input_size == (384, 384)

    def test_no_model_still_no_detector(self, monkeypatch):
        monkeypatch.delenv("VISION_MODEL_PATH", raising=False)
        reg = VisionModelRegistry()
        assert reg.has_model() is False
        assert reg.get_detector("killfeed") is None


@pytest.mark.unit
@pytest.mark.ml
class TestYolo26FallbackStillDecodable:
    """YOLO26 (ultralytics export) plugs into the EXISTING yolov8 decode.

    YOLO26 exports the same single-output (B, 4+C, N) layout as
    YOLOv8 — decoded xyxy + sigmoided scores. This test feeds a
    synthetic tensor in exactly that layout; it is NOT a real
    YOLO26 file and proves decode compatibility, not YOLO26 quality.
    """

    def test_yolov8_layout_yields_panel_and_entry(self):
        cfg = TrainedDetectorConfig(
            classes=list(KILLFEED_DEFAULT_CLASSES),
            decode_mode="yolov8",
            conf_threshold=0.0,
            nms_iou=1.0,
        )
        det = TrainedDetector(name="test_yolo26_fallback", config=cfg)
        out = np.zeros((1, 6, 64), dtype=np.float32)
        out[0, 4:, :] = 0.01
        out[0, 0:4, 0] = [100.0, 50.0, 300.0, 90.0]    # entry row
        out[0, 4:6, 0] = [0.05, 0.92]                  # -> killfeed_entry
        out[0, 0:4, 1] = [90.0, 40.0, 320.0, 200.0]    # panel
        out[0, 4:6, 1] = [0.88, 0.10]                  # -> killfeed_panel
        boxes, labels, confs = det._decode_outputs(
            [out], scale=1.0, pad=(0.0, 0.0),
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
        assert by_label["killfeed_panel"][1] == pytest.approx(0.88)
