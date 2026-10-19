# backend/app/ml/killfeed_detection/export.py
"""
AEGIS AI - Kill-Feed Detection Export
Trained .pt -> ONNX (opset pinned) for CPU inference inside AEGIS,
with mandatory onnxruntime verification.

Why ONNX: the live broadcast pipeline runs on CPU (the same box that runs
the backend). ONNX Runtime executes the YOLO graph without a torch install
and without GPU — small, fast, dependency-light. Opset is pinned (default
17) so the exported graph behaves identically wherever it is verified.

Every export is *verified*: the ONNX graph is loaded in a fresh
onnxruntime InferenceSession and run on a dummy input. An export that does
not verify is reported as failed — never silently shipped.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aegis.ml.killfeed.export")


@dataclass
class ExportConfig:
    """Export configuration."""

    weights: str = ""            # input best.pt
    out_dir: str = "runs/killfeed_export"
    file_name: str = "killfeed.onnx"
    format: str = "onnx"         # onnx today; openvino/tensorrt later

    # ONNX graph — pinned for reproducibility
    onnx_opset: int = 17
    imgsz: int = 640
    dynamic_batch: bool = False  # True lets batch size vary at runtime
    simplify: bool = True        # onnxsim pass (skipped if not installed)

    # verification
    verify: bool = True          # run onnxruntime check; never ship unverified
    verify_batch: int = 1


@dataclass
class ExportReport:
    format: str
    path: str
    size_mb: float
    opset: int
    verified: bool
    input_shape: List[int] = field(default_factory=list)
    output_shapes: List[List[int]] = field(default_factory=list)
    output_names: List[str] = field(default_factory=list)
    export_seconds: float = 0.0
    error: str = ""
    verification_attempted: bool = True

    def summary(self) -> str:
        if self.verified:
            status = "VERIFIED"
        elif not self.verification_attempted:
            status = "NOT VERIFIED (skipped)"
        else:
            status = "VERIFICATION FAILED"
        lines = [
            f"Kill-feed export [{self.format}] -> {status}",
            f"  path        : {self.path}",
            f"  size        : {self.size_mb:.1f} MB",
            f"  opset       : {self.opset}",
            f"  input       : {self.input_shape}",
            f"  outputs     : {self.output_names} {self.output_shapes}",
            f"  export time : {self.export_seconds:.1f}s",
        ]
        if self.error:
            lines.append(f"  error       : {self.error}")
        if self.verified:
            lines.append(
                "  Drop this .onnx into AEGIS and point the runtime detector "
                "at it (see docs/TRAINING.md step 5)."
            )
        elif not self.verification_attempted:
            lines.append("  Verification was SKIPPED (--no-verify): run with "
                         "verification before shipping this file.")
        else:
            lines.append("  DO NOT SHIP — verification failed.")
        return "\n".join(lines)


class KillFeedExporter:
    """Exports kill-feed weights to production formats with verification."""

    def __init__(self, config: Optional[ExportConfig] = None):
        self.config = config or ExportConfig()

    # ------------------------------------------------------------------
    def export(self) -> ExportReport:
        c = self.config
        if c.format != "onnx":
            raise ValueError(f"unsupported export format {c.format!r} (only 'onnx' today)")
        return self.export_onnx()

    def export_onnx(self) -> ExportReport:
        from .model import _require_ultralytics
        YOLO = _require_ultralytics()
        c = self.config
        t0 = time.time()

        if not c.weights or not os.path.isfile(c.weights):
            raise FileNotFoundError(f"weights not found: {c.weights!r}")
        out_dir = os.path.abspath(c.out_dir)
        os.makedirs(out_dir, exist_ok=True)

        logger.info("exporting %s -> ONNX (opset=%d, imgsz=%d)",
                    c.weights, c.onnx_opset, c.imgsz)
        model = YOLO(c.weights)
        exported = model.export(
            format="onnx",
            opset=c.onnx_opset,
            imgsz=c.imgsz,
            dynamic=c.dynamic_batch,
            simplify=c.simplify,
        )
        # ultralytics returns the path it wrote; move to our chosen name/dir
        src = str(exported)
        dst = os.path.join(out_dir, c.file_name)
        if os.path.abspath(src) != os.path.abspath(dst):
            os.replace(src, dst)

        size_mb = os.path.getsize(dst) / (1024 * 1024)
        report = ExportReport(
            format="onnx", path=dst, size_mb=round(size_mb, 2),
            opset=c.onnx_opset, verified=False,
            export_seconds=round(time.time() - t0, 1),
        )
        if c.verify:
            try:
                self.verify_onnx(dst, batch=c.verify_batch, imgsz=c.imgsz,
                                 report=report)
                report.verified = True
            except Exception as e:
                report.error = str(e)
                logger.error("ONNX verification FAILED: %s", e)
        else:
            report.verification_attempted = False
            logger.warning("ONNX verification skipped (--no-verify)")
        logger.info("\n%s", report.summary())
        return report

    # ------------------------------------------------------------------
    @staticmethod
    def verify_onnx(onnx_path: str, batch: int = 1, imgsz: int = 640,
                    report: Optional[ExportReport] = None) -> Dict[str, Any]:
        """Load the graph in onnxruntime and run one dummy forward pass.

        Raises on any failure. Returns input/output shapes on success.
        """
        try:
            import onnxruntime as ort
            import numpy as np
        except ImportError as e:
            raise ImportError(
                "onnxruntime and numpy are required for verification "
                "(in requirements-train.txt)"
            ) from e

        sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        if not sess.get_inputs():
            raise RuntimeError("ONNX graph has no inputs")
        inp = sess.get_inputs()[0]
        shape = [batch if (d == "batch" or not isinstance(d, int)) else d
                 for d in inp.shape]
        # concrete shape: (batch, 3, imgsz, imgsz)
        concrete = [batch, 3, imgsz, imgsz]
        dummy = np.zeros(concrete, dtype=np.float32)
        outputs = sess.run(None, {inp.name: dummy})
        if not outputs:
            raise RuntimeError("ONNX graph produced no outputs")

        info = {
            "input_name": inp.name,
            "input_shape": concrete,
            "output_names": [o.name for o in sess.get_outputs()],
            "output_shapes": [list(o.shape) for o in sess.get_outputs()],
            "num_outputs": len(outputs),
        }
        if report is not None:
            report.input_shape = concrete
            report.output_names = info["output_names"]
            report.output_shapes = info["output_shapes"]
        logger.info("ONNX verified: %s in=%s out=%s",
                    onnx_path, concrete, info["output_shapes"])
        return info
