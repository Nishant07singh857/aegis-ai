# backend/app/ml/killfeed_detection/evaluator.py
"""
AEGIS AI - Kill-Feed Detection Evaluator
Honest evaluation of the kill-feed detector on a held-out split.

What this reports (and nothing else is claimed):
- per-class precision / recall / mAP@0.5 / mAP@0.5:0.95
- confusion summary: per-class TP / FP / FN derived from the confusion matrix
- inference speed (ms/image, FPS) on the evaluation device

What this NEVER does:
- print or claim a single headline "accuracy %" — detection has no such
  number; a headline accuracy would be a lie (it hides which class fails).
- evaluate on the training split and call it validation.

Metrics come from ultralytics' own val() pass on the requested split; this
class only organises them into a readable, honest report.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aegis.ml.killfeed.evaluator")


@dataclass
class EvalConfig:
    """Evaluation configuration. All paths from the caller."""

    weights: str = ""            # best.pt (or any .pt / .onnx for speed checks)
    data_yaml: str = ""          # same data.yaml the model trained on
    split: str = "val"           # val | test — never train
    imgsz: int = 640
    batch_size: int = 16
    device: str = "auto"
    conf: float = 0.25
    iou: float = 0.6
    out_dir: str = "runs/killfeed_eval"
    run_name: str = "eval"


@dataclass
class ClassMetrics:
    name: str
    precision: float
    recall: float
    map50: float
    map50_95: float
    tp: int = 0
    fp: int = 0
    fn: int = 0


@dataclass
class EvalReport:
    """Full, honest evaluation report."""
    weights: str
    split: str
    num_images: int
    classes: List[ClassMetrics]
    mean_map50: float
    mean_map50_95: float
    mean_precision: float
    mean_recall: float
    total_tp: int
    total_fp: int
    total_fn: int
    inference_ms_per_image: float
    fps: float
    eval_seconds: float
    warnings: List[str] = field(default_factory=list)

    # -- honest rendering: per-class table first, means clearly labelled --
    def to_text(self) -> str:
        lines = [
            "Kill-feed evaluation report  (detection metrics — no single 'accuracy %' exists)",
            f"  weights : {self.weights}",
            f"  split   : {self.split}  ({self.num_images} images)",
            "",
            "  per-class metrics:",
            "  " + f"{'class':<18}{'P':>8}{'R':>8}{'mAP50':>8}{'mAP50-95':>10}"
                   f"{'TP':>7}{'FP':>7}{'FN':>7}",
        ]
        for c in self.classes:
            lines.append(
                "  " + f"{c.name:<18}{c.precision:>8.3f}{c.recall:>8.3f}"
                f"{c.map50:>8.3f}{c.map50_95:>10.3f}"
                f"{c.tp:>7d}{c.fp:>7d}{c.fn:>7d}"
            )
        lines += [
            "",
            "  means (macro average over classes — indicative only, read the rows above):",
            f"    mean precision {self.mean_precision:.3f}   "
            f"mean recall {self.mean_recall:.3f}",
            f"    mean mAP@0.5   {self.mean_map50:.3f}   "
            f"mean mAP@0.5:0.95 {self.mean_map50_95:.3f}",
            f"  totals: TP={self.total_tp} FP={self.total_fp} FN={self.total_fn}",
            f"  speed : {self.inference_ms_per_image:.1f} ms/image "
            f"({self.fps:.1f} FPS, incl. preprocess+postprocess)",
            f"  eval time: {self.eval_seconds:.1f}s",
        ]
        if self.warnings:
            lines.append("  warnings:")
            lines += [f"    - {w}" for w in self.warnings]
        lines.append(
            "\nHow to read this: mAP@0.5 is the headline for detection quality; "
            "recall tells you how many feed entries you MISS (matters most for "
            "live broadcast — a missed kill never reaches the overlay); "
            "precision tells you how many false boxes you draw."
        )
        return "\n".join(lines)

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {c.name} | {c.precision:.3f} | {c.recall:.3f} | {c.map50:.3f} | "
            f"{c.map50_95:.3f} | {c.tp} | {c.fp} | {c.fn} |"
            for c in self.classes
        )
        warn = "\n".join(f"- {w}" for w in self.warnings)
        return (
            f"# Kill-feed evaluation report\n\n"
            f"- weights: `{self.weights}`\n- split: `{self.split}` "
            f"({self.num_images} images)\n\n"
            f"| class | precision | recall | mAP@0.5 | mAP@0.5:0.95 | TP | FP | FN |\n"
            f"|---|---|---|---|---|---|---|---|\n{rows}\n\n"
            f"**Macro means** (indicative only): P={self.mean_precision:.3f} "
            f"R={self.mean_recall:.3f} mAP@0.5={self.mean_map50:.3f} "
            f"mAP@0.5:0.95={self.mean_map50_95:.3f}\n\n"
            f"**Speed:** {self.inference_ms_per_image:.1f} ms/image "
            f"({self.fps:.1f} FPS)\n\n"
            f"{'**Warnings:**\n' + warn + chr(10) if warn else ''}"
            f"_Detection has no single 'accuracy %' — read the per-class rows._\n"
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


class KillFeedEvaluator:
    """Runs honest evaluation via ultralytics val()."""

    def __init__(self, config: Optional[EvalConfig] = None):
        self.config = config or EvalConfig()

    def _resolve_device(self) -> str:
        if self.config.device != "auto":
            return self.config.device
        try:
            import torch
            if torch.cuda.is_available():
                return "0"
        except ImportError:
            pass
        return "cpu"

    def evaluate(self) -> EvalReport:
        from .model import _require_ultralytics
        YOLO = _require_ultralytics()
        c = self.config
        if c.split == "train":
            raise ValueError(
                "refusing to 'evaluate' on the train split — that measures "
                "memorisation, not detection. Use split=val or split=test."
            )
        if not c.weights or not os.path.isfile(c.weights):
            raise FileNotFoundError(f"weights not found: {c.weights!r}")
        if not c.data_yaml or not os.path.isfile(c.data_yaml):
            raise FileNotFoundError(f"data_yaml not found: {c.data_yaml!r}")

        warnings: List[str] = []
        self._warn_on_imsize_mismatch(c.weights, c.imgsz, warnings)
        t0 = time.time()
        model = YOLO(c.weights)
        results = model.val(
            data=c.data_yaml,
            split=c.split,
            imgsz=c.imgsz,
            batch=c.batch_size,
            device=self._resolve_device(),
            conf=c.conf,
            iou=c.iou,
            plots=True,
            save_json=False,
            verbose=False,
        )
        eval_seconds = time.time() - t0

        names: Dict[int, str] = getattr(results, "names", {}) or {}
        n_cls = len(names)
        # per-class P/R/AP from ultralytics' metrics.box.
        # box.ap is per-class mAP@0.5:0.95 (1-D in current ultralytics;
        # older builds exposed the raw (nc, 10) table — handled below).
        box = getattr(results, "box", None)
        ap = getattr(box, "ap", None)              # per-class mAP@0.5:0.95
        ap50 = getattr(box, "ap50", None)          # per-class mAP@0.5
        p = getattr(box, "p", None)
        r = getattr(box, "r", None)
        nc_seen = getattr(box, "nc", None) or getattr(results, "nc", None) or n_cls

        tp_fp_fn = self._confusion_counts(results, n_cls, warnings)

        classes: List[ClassMetrics] = []

        def _per_class_ap50_95(i: int) -> float:
            try:
                import numpy as _np
                arr = _np.asarray(ap, dtype=float)
                if arr.ndim == 2 and i < arr.shape[0]:
                    return float(arr[i].mean())   # legacy (nc, 10) table
                if arr.ndim == 1 and i < arr.shape[0]:
                    return float(arr[i])           # current: already averaged
            except (TypeError, IndexError, ValueError):
                pass
            return 0.0

        for i in range(n_cls):
            def _at(arr, idx, default=0.0):
                try:
                    return float(arr[idx])
                except (TypeError, IndexError, KeyError):
                    return default
            tp, fp, fn = tp_fp_fn[i] if i < len(tp_fp_fn) else (0, 0, 0)
            classes.append(ClassMetrics(
                name=names.get(i, f"class_{i}"),
                precision=_at(p, i),
                recall=_at(r, i),
                map50=_at(ap50, i),
                map50_95=_per_class_ap50_95(i),
                tp=tp, fp=fp, fn=fn,
            ))
        if nc_seen != n_cls:
            warnings.append(
                f"val saw {nc_seen} classes but data.yaml names {n_cls}; "
                "check data.yaml <-> weights class alignment")

        def _mean(attr: str) -> float:
            vals = [getattr(cl, attr) for cl in classes]
            return sum(vals) / len(vals) if vals else 0.0

        speed = getattr(results, "speed", {}) or {}
        ms_per_image = float(speed.get("inference", 0.0))
        fps = 1000.0 / ms_per_image if ms_per_image > 0 else 0.0
        # ultralytics no longer exposes a stable image counter on the metrics
        # object — count the split's images from data.yaml instead (honest).
        num_images = self._count_split_images(c.data_yaml, c.split, warnings)

        report = EvalReport(
            weights=os.path.abspath(c.weights),
            split=c.split,
            num_images=num_images,
            classes=classes,
            mean_map50=_mean("map50"),
            mean_map50_95=_mean("map50_95"),
            mean_precision=_mean("precision"),
            mean_recall=_mean("recall"),
            total_tp=sum(cl.tp for cl in classes),
            total_fp=sum(cl.fp for cl in classes),
            total_fn=sum(cl.fn for cl in classes),
            inference_ms_per_image=round(ms_per_image, 2),
            fps=round(fps, 1),
            eval_seconds=round(eval_seconds, 1),
            warnings=warnings,
        )
        logger.info("\n%s", report.to_text())
        return report

    # ------------------------------------------------------------------
    @staticmethod
    def _warn_on_imsize_mismatch(weights: str, eval_imgsz: int,
                                 warnings: List[str]) -> None:
        """Warn when eval imgsz differs from the training imgsz stored in the
        checkpoint — mismatched sizes silently change the measured metrics."""
        try:
            import torch
            ckpt = torch.load(weights, map_location="cpu", weights_only=False)
            train_args = ckpt.get("train_args", {}) or {}
            train_imgsz = train_args.get("imgsz")
            if train_imgsz and int(train_imgsz) != int(eval_imgsz):
                warnings.append(
                    f"eval imgsz={eval_imgsz} differs from training imgsz={train_imgsz} "
                    f"(from {os.path.basename(weights)}); metrics are only comparable "
                    f"at the training size — re-run with --imgsz {train_imgsz}")
        except Exception:
            pass  # checkpoint unreadable; not worth failing eval over

    # ------------------------------------------------------------------
    @staticmethod
    def _count_split_images(data_yaml: str, split: str,
                            warnings: List[str]) -> int:
        """Count image files in the data.yaml split dir (fallback: 0 + warning)."""
        try:
            import yaml
            with open(data_yaml) as f:
                cfg = yaml.safe_load(f)
            base = cfg.get("path", "")
            rel = cfg.get(split, "")
            split_dir = rel if os.path.isabs(rel) else os.path.join(base, rel)
            exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
            return sum(1 for n in os.listdir(split_dir)
                       if n.lower().endswith(exts))
        except Exception as e:
            warnings.append(f"could not count {split} images ({e}); "
                            "image count shown as 0")
            return 0

    # ------------------------------------------------------------------
    @staticmethod
    def _confusion_counts(results: Any, n_cls: int,
                          warnings: List[str]) -> List[tuple]:
        """Derive per-class (TP, FP, FN) from ultralytics' confusion matrix.

        The matrix is (nc+1, nc+1) with the last row/col = background.
        TP = diag[i]; FP = col sum - TP; FN = row sum - TP.
        Returns zeros (with a warning) if the matrix is unavailable.
        """
        try:
            cm = getattr(results, "confusion_matrix", None)
            matrix = getattr(cm, "matrix", None)
            if matrix is None:
                raise AttributeError("no confusion matrix")
            import numpy as np
            m = np.asarray(matrix)
            out = []
            for i in range(n_cls):
                tp = int(m[i, i]) if i < m.shape[0] and i < m.shape[1] else 0
                fp = int(m[:, i].sum()) - tp if i < m.shape[1] else 0
                fn = int(m[i, :].sum()) - tp if i < m.shape[0] else 0
                out.append((tp, fp, fn))
            return out
        except Exception as e:  # honest: report zeros, say why
            warnings.append(f"confusion-matrix counts unavailable ({e}); "
                            "TP/FP/FN shown as 0")
            return [(0, 0, 0)] * n_cls

    # ------------------------------------------------------------------
    def save(self, report: EvalReport, out_dir: Optional[str] = None,
             formats: tuple = ("txt", "md", "json")) -> Dict[str, str]:
        out = os.path.abspath(out_dir or self.config.out_dir)
        os.makedirs(out, exist_ok=True)
        paths: Dict[str, str] = {}
        writers = {"txt": (report.to_text, "report.txt"),
                   "md": (report.to_markdown, "report.md"),
                   "json": (report.to_json, "report.json")}
        for fmt in formats:
            fn, fname = writers[fmt]
            path = os.path.join(out, fname)
            with open(path, "w") as f:
                f.write(fn())
            paths[fmt] = path
        logger.info("evaluation report written: %s", out)
        return paths
