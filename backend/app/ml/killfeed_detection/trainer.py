# backend/app/ml/killfeed_detection/trainer.py
"""
AEGIS AI - Kill-Feed Detection Trainer
Honest, thin orchestration over ultralytics YOLO.train().

Deliberately NOT a from-scratch training loop: ultralytics owns the loss,
the dataloader, AMP and DDP. This class owns what AEGIS needs around it:

- reproducible run directories (run_config.json captures every knob)
- the kill-feed augmentation policy (via KillFeedAugmentations)
- checkpoint discipline (best.pt / last.pt, resume)
- a TrainingReport with the *real* metrics ultralytics measured —
  per-class precision/recall/mAP — never a single invented "accuracy %".

For a from-scratch reference loop see fight_detection/trainer.py; for real
GPU training, this wrapper is what train.py drives.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .augmentations import AugmentationConfig, KillFeedAugmentations
from .model import KillFeedModel, ModelConfig

logger = logging.getLogger("aegis.ml.killfeed.trainer")


@dataclass
class TrainingConfig:
    """Training run configuration. All paths come from the caller."""

    # data
    data_yaml: str = ""            # produced by KillFeedDataset.prepare_yolo_dataset
    # run
    epochs: int = 50
    batch_size: int = 16           # -1 lets ultralytics pick ~80% VRAM (GPU only)
    imgsz: int = 640
    device: str = "auto"           # auto | cpu | cuda | cuda:0 | mps | 0,1 (DDP)
    workers: int = 8
    project: str = "runs/killfeed" # run directory root
    run_name: str = "train"        # run dir = <project>/<run_name>[n]
    exist_ok: bool = False
    # optimisation
    optimizer: str = "auto"        # auto | SGD | Adam | AdamW ...
    lr0: float = 0.01              # ultralytics default; lower (0.001) for fine-tune
    weight_decay: float = 0.0005
    patience: int = 20             # early stopping (epochs without improvement)
    seed: int = 42
    # fine-tune
    freeze_backbone_layers: int = 0
    # augmentation policy (applied via to_ultralytics_kwargs)
    augmentation: AugmentationConfig = field(default_factory=AugmentationConfig)
    # resume
    resume_weights: str = ""       # path to last.pt to resume from
    # misc
    verbose: bool = True
    plots: bool = True             # save training curves into the run dir
    save_period: int = -1          # -1 = only best.pt + last.pt


@dataclass
class TrainingReport:
    """Outcome of a training run. metrics come straight from ultralytics'
    validation pass — keys like 'metrics/precision(B)', 'metrics/mAP50(B)':
    per-class breakdowns are added by the evaluator, not here."""
    run_dir: str
    best_weights: str
    last_weights: str
    epochs_run: int
    stopped_early: bool
    train_seconds: float
    metrics: Dict[str, float]
    model_info: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        m = self.metrics
        lines = [
            "Kill-feed training complete",
            f"  run dir     : {self.run_dir}",
            f"  best weights: {self.best_weights}",
            f"  epochs run  : {self.epochs_run}"
            + (" (early stopping)" if self.stopped_early else ""),
            f"  train time  : {self.train_seconds / 60:.1f} min",
            "  val metrics (from ultralytics; see evaluate.py for per-class breakdown):",
        ]
        for key in ("metrics/precision(B)", "metrics/recall(B)",
                    "metrics/mAP50(B)", "metrics/mAP50-95(B)"):
            if key in m:
                short = key.split("/")[-1]
                lines.append(f"    {short:16s} {m[key]:.4f}")
        if self.warnings:
            lines.append("  warnings:")
            lines += [f"    - {w}" for w in self.warnings]
        lines += [
            "Next: python backend/app/ml/training/evaluate.py "
            f"--weights {self.best_weights} --data <dataset_dir>",
            "Then: python backend/app/ml/training/export.py "
            f"--weights {self.best_weights} --out <export_dir>",
        ]
        return "\n".join(lines)


class KillFeedTrainer:
    """Orchestrates kill-feed YOLO training runs."""

    def __init__(self, model: KillFeedModel,
                 config: Optional[TrainingConfig] = None):
        self.model = model
        self.config = config or TrainingConfig()

    # ------------------------------------------------------------------
    def _resolve_device(self) -> str:
        if self.config.device != "auto":
            return self.config.device
        try:
            import torch
            if torch.cuda.is_available():
                return "0"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _train_kwargs(self) -> Dict[str, Any]:
        c = self.config
        aug = KillFeedAugmentations(c.augmentation).to_ultralytics_kwargs()
        kwargs: Dict[str, Any] = dict(
            data=c.data_yaml,
            epochs=c.epochs,
            batch=c.batch_size,
            imgsz=c.imgsz,
            device=self._resolve_device(),
            workers=c.workers,
            project=c.project,
            name=c.run_name,
            exist_ok=c.exist_ok,
            optimizer=c.optimizer,
            lr0=c.lr0,
            weight_decay=c.weight_decay,
            patience=c.patience,
            seed=c.seed,
            verbose=c.verbose,
            plots=c.plots,
            save_period=c.save_period,
            **aug,
        )
        if c.resume_weights:
            kwargs["resume"] = c.resume_weights
        return kwargs

    # ------------------------------------------------------------------
    def train(self) -> TrainingReport:
        c = self.config
        if not c.data_yaml or not os.path.isfile(c.data_yaml):
            raise FileNotFoundError(
                f"data_yaml not found: {c.data_yaml!r}. Build it first with "
                "KillFeedDataset.prepare_yolo_dataset() or train.py --dry-run."
            )
        device = self._resolve_device()
        t0 = time.time()

        if c.resume_weights:
            logger.info("resuming training from %s", c.resume_weights)
            self.model.config.model_name = c.resume_weights
        self.model.build(device=device if device != "0" else "cuda:0")

        kwargs = self._train_kwargs()
        run_config_path = os.path.join(
            os.path.abspath(c.project), "run_config_pending.json")
        os.makedirs(os.path.dirname(run_config_path), exist_ok=True)
        with open(run_config_path, "w") as f:
            json.dump({
                "model": asdict(self.model.config),
                "training": {k: (asdict(v) if isinstance(v, AugmentationConfig) else v)
                             for k, v in asdict(c).items()},
            }, f, indent=2, default=str)

        logger.info("starting YOLO training: epochs=%d batch=%s imgsz=%d device=%s",
                    c.epochs, c.batch_size, c.imgsz, kwargs["device"])
        logger.info("augmentation policy:\n%s",
                    KillFeedAugmentations(c.augmentation).describe())
        self.model.yolo.train(**kwargs)

        # run dir: ultralytics picks <project>/<name>[n]; read it from the
        # trainer rather than guessing the increment suffix ourselves.
        run_dir = os.path.abspath(str(self.model.yolo.trainer.save_dir))
        # persist the run config next to the results (reproducibility)
        os.replace(run_config_path,
                   os.path.join(run_dir, "run_config.json"))

        best = os.path.join(run_dir, "weights", "best.pt")
        last = os.path.join(run_dir, "weights", "last.pt")
        warnings: List[str] = []
        if not os.path.isfile(best):
            warnings.append(f"best.pt not found in {run_dir} — check the training log")
        if not os.path.isfile(last):
            warnings.append(f"last.pt not found in {run_dir}")

        metrics, epochs_run = self._read_results_csv(run_dir, c.epochs)
        report = TrainingReport(
            run_dir=run_dir,
            best_weights=best,
            last_weights=last,
            epochs_run=epochs_run,
            stopped_early=epochs_run < c.epochs,
            train_seconds=time.time() - t0,
            metrics=metrics,
            model_info=self.model.info(),
            warnings=warnings,
        )
        with open(os.path.join(run_dir, "training_report.json"), "w") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        logger.info("\n%s", report.summary())
        return report

    # ------------------------------------------------------------------
    @staticmethod
    def _read_results_csv(run_dir: str, requested_epochs: int
                          ) -> tuple[Dict[str, float], int]:
        """Read the real per-epoch metrics ultralytics wrote to results.csv.

        Returns (final_epoch_metrics, epochs_run). No invention: if the file
        is missing we return empty metrics and say so via epochs_run=0.
        """
        metrics: Dict[str, float] = {}
        csv_path = os.path.join(run_dir, "results.csv")
        try:
            with open(csv_path, newline="") as f:
                rows = list(csv.DictReader(f))
        except FileNotFoundError:
            logger.warning("results.csv not found in %s", run_dir)
            return {}, 0
        if not rows:
            return {}, 0
        epochs_run = min(len(rows), requested_epochs)
        last = rows[-1]
        for k, v in last.items():
            key = k.strip()
            try:
                metrics[key] = float(v)
            except (TypeError, ValueError):
                continue
        # normalise to the keys TrainingReport.summary() looks for
        aliases = {
            "metrics/precision(B)": ["metrics/precision(B)", "precision(B)", " P"],
            "metrics/recall(B)": ["metrics/recall(B)", "recall(B)", " R"],
            "metrics/mAP50(B)": ["metrics/mAP50(B)", "mAP50(B)", "mAP50"],
            "metrics/mAP50-95(B)": ["metrics/mAP50-95(B)", "mAP50-95(B)", "mAP50-95"],
        }
        normalised: Dict[str, float] = {}
        for canon, cands in aliases.items():
            for cand in cands:
                if cand in metrics:
                    normalised[canon] = metrics[cand]
                    break
                for mk in metrics:
                    if mk.strip().endswith(cand.strip()):
                        normalised[canon] = metrics[mk]
                        break
                if canon in normalised:
                    break
        normalised.update({k: v for k, v in metrics.items() if k not in normalised})
        return normalised, epochs_run

    # ------------------------------------------------------------------
    @classmethod
    def smoke_train(cls, out_dir: str = "runs/killfeed_smoke",
                    imgsz: int = 320) -> TrainingReport:
        """CPU smoke test: builds a tiny synthetic dataset (no real frames),
        trains YOLOv8n for 1 epoch, and returns the report.

        This proves the whole pipeline parses args, builds the model, runs a
        training step and writes checkpoints — without a GPU or real data.
        The resulting weights are meaningless for detection (synthetic data);
        they only prove the machinery works.
        """
        from .dataset import DatasetConfig, KillFeedDataset
        synth_root = os.path.join(os.path.abspath(out_dir), "_synthetic_dataset")
        data_dir = cls._make_synthetic_dataset(synth_root, n_images=8, img_size=imgsz)

        ds = KillFeedDataset(DatasetConfig(
            class_names=["killfeed_panel", "killfeed_entry"],
            val_fraction=0.25, test_fraction=0.0,
        ))
        prep = ds.prepare_yolo_dataset(data_dir, os.path.join(out_dir, "_yolo"))

        model = KillFeedModel(ModelConfig(model_name="yolov8n.pt", imgsz=imgsz))
        trainer = cls(model, TrainingConfig(
            data_yaml=prep["data_yaml"],
            epochs=1,
            batch_size=8,
            imgsz=imgsz,
            device="cpu",
            workers=0,
            project=os.path.join(out_dir, "runs"),
            run_name="smoke",
            exist_ok=True,
            patience=1,
            plots=False,
            verbose=False,
        ))
        report = trainer.train()
        if not os.path.isfile(report.best_weights):
            raise RuntimeError("smoke train finished but best.pt is missing")
        logger.info("SMOKE OK: 1 epoch on CPU, weights at %s", report.best_weights)
        return report

    @staticmethod
    def _make_synthetic_dataset(root: str, n_images: int = 8,
                                 img_size: int = 320) -> str:
        """Write n_images of fake 'UI on dark background' frames + manifest.csv.

        Not training data — just a shape-correct stand-in so --smoke exercises
        the real code path (manifest -> YOLO dataset -> 1 training epoch)."""
        import numpy as np
        try:
            import cv2
        except ImportError as e:
            raise ImportError("opencv-python-headless is required for --smoke") from e

        rng = np.random.default_rng(7)
        frames_dir = os.path.join(root, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        rows = ["image_path,x_min,y_min,x_max,y_max,class_name,split,width,height"]
        for i in range(n_images):
            img = (rng.integers(0, 30, (img_size, img_size, 3))).astype(np.uint8)
            # fake "feed panel": bright rectangle top-right
            px, py = int(img_size * 0.62), int(img_size * 0.04)
            pw, ph = int(img_size * 0.34), int(img_size * 0.30)
            cv2.rectangle(img, (px, py), (px + pw, py + ph), (200, 200, 200), -1)
            rows.append(
                f"frames/img_{i:03d}.png,{px},{py},{px + pw},{py + ph},"
                f"killfeed_panel,auto,{img_size},{img_size}")
            # 1-3 fake "entries": thin bright rows inside the panel
            for e in range(int(rng.integers(1, 4))):
                ey = py + 8 + e * int(ph / 4)
                eh = int(ph / 5)
                cv2.rectangle(img, (px + 6, ey), (px + pw - 6, ey + eh),
                              (240, 240, 240), -1)
                rows.append(
                    f"frames/img_{i:03d}.png,{px + 6},{ey},{px + pw - 6},{ey + eh},"
                    f"killfeed_entry,auto,{img_size},{img_size}")
            cv2.imwrite(os.path.join(frames_dir, f"img_{i:03d}.png"), img)
        with open(os.path.join(root, "manifest.csv"), "w") as f:
            f.write("\n".join(rows) + "\n")
        return root
