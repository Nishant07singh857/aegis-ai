#!/usr/bin/env python3
# backend/app/ml/training/train.py
"""
AEGIS AI - training CLI.

    python backend/app/ml/training/train.py --data <dataset_dir> --epochs 50 --out runs/
    python backend/app/ml/training/train.py --smoke          # CPU smoke test, no data needed
    python backend/app/ml/training/train.py --data <dir> --dry-run   # validate dataset only

<dataset_dir> is the W1 dataset root: it must contain manifest.csv (or a
COCO annotations.json). This script validates the manifest, builds the YOLO
dataset under --out, then trains YOLOv8-nano.

CPU smoke test (--smoke): builds a tiny synthetic dataset, trains 1 epoch
on CPU. Proves arg parsing, model build, dataset prep and the training step
work. The smoke weights are meaningless for detection — they only prove the
machinery runs.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# allow running as a script from the repo root without installing the package
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aegis.train_cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Train the AEGIS kill-feed detector (YOLOv8-nano).")
    p.add_argument("--data", default="",
                   help="W1 dataset root dir (contains manifest.csv).")
    p.add_argument("--task", default="killfeed", choices=["killfeed"],
                   help="detection task (more tasks plug in here later).")
    p.add_argument("--manifest", default="",
                   help="manifest file name inside --data (auto-detected if empty).")
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--batch", type=int, default=16,
                   help="batch size (-1 = ultralytics auto ~80%% VRAM, GPU only).")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--out", default="runs/killfeed",
                   help="run output root (YOLO dataset + checkpoints land here).")
    p.add_argument("--name", default="train", help="run name under --out.")
    p.add_argument("--device", default="auto",
                   help="auto | cpu | cuda | cuda:0 | mps | 0,1 (multi-GPU).")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--patience", type=int, default=20,
                   help="early-stopping patience in epochs.")
    p.add_argument("--lr", type=float, default=0.01,
                   help="initial LR (try 0.001 when fine-tuning on <2k frames).")
    p.add_argument("--model", default="yolov8n.pt",
                   help="base weights: yolov8n.pt | yolov8s.pt | path/to/custom.pt")
    p.add_argument("--freeze-backbone", type=int, default=0,
                   help="freeze first N layers (head-only fine-tune for small data).")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--resume", default="",
                   help="path to last.pt to resume an interrupted run.")
    p.add_argument("--no-augment", action="store_true",
                   help="disable the kill-feed augmentation policy.")
    p.add_argument("--val-fraction", type=float, default=0.15)
    p.add_argument("--dry-run", action="store_true",
                   help="validate the manifest and build the YOLO dataset, then exit "
                        "without training.")
    p.add_argument("--smoke", action="store_true",
                   help="CPU smoke test: synthetic data, 1 epoch. No --data needed.")
    return p


def resolve_data_yaml(data_dir: str, out_dir: str, args) -> tuple[str, dict]:
    """Return (data_yaml_path, prep_info): reuse an existing data.yaml, or
    build one from manifest.csv via KillFeedDataset."""
    from app.ml.killfeed_detection.dataset import DatasetConfig, KillFeedDataset

    direct = os.path.join(data_dir, "data.yaml")
    if os.path.isfile(direct):
        logger.info("using existing data.yaml: %s", direct)
        return direct, {"data_yaml": direct, "reused": True}
    ds = KillFeedDataset(DatasetConfig(val_fraction=args.val_fraction))
    prep = ds.prepare_yolo_dataset(
        data_dir, os.path.join(out_dir, "_yolo_dataset"),
        manifest=os.path.join(data_dir, args.manifest) if args.manifest else None,
    )
    return prep["data_yaml"], prep


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    from app.ml.killfeed_detection.augmentations import AugmentationConfig
    from app.ml.killfeed_detection.model import KillFeedModel, ModelConfig
    from app.ml.killfeed_detection.trainer import KillFeedTrainer, TrainingConfig

    if args.task != "killfeed":
        logger.error("unknown task %r", args.task)
        return 2

    # --smoke never touches real data -------------------------------------
    if args.smoke:
        logger.info("SMOKE: synthetic dataset, 1 epoch on CPU")
        report = KillFeedTrainer.smoke_train(out_dir=args.out, imgsz=320)
        print("\nSMOKE PASSED")
        print(f"  weights : {report.best_weights}")
        print(f"  run dir : {report.run_dir}")
        print("  (weights are from synthetic data — detection quality is meaningless;")
        print("   the smoke test only proves the pipeline runs end to end.)")
        return 0

    if not args.data:
        logger.error("--data <dataset_dir> is required (or use --smoke).")
        return 2
    data_dir = os.path.abspath(args.data)
    if not os.path.isdir(data_dir):
        logger.error("dataset dir not found: %s", data_dir)
        return 2
    out_dir = os.path.abspath(args.out)

    data_yaml, prep = resolve_data_yaml(data_dir, out_dir, args)
    if prep.get("reused"):
        print(f"dataset: using existing data.yaml (skipped manifest validation)")
    else:
        stats = prep.get("stats", {})
        print(f"dataset: {stats.get('num_boxes', '?')} boxes / "
              f"{stats.get('num_images', '?')} images "
              f"(+{stats.get('num_background_images', 0)} background), "
              f"splits={stats.get('splits', {})}")
        if prep.get("warnings"):
            print(f"dataset warnings ({len(prep['warnings'])}):")
            for w in prep["warnings"][:10]:
                print(f"  - {w}")

    if args.dry_run:
        print(f"\nDRY RUN OK — YOLO dataset ready at {prep.get('yolo_root')}")
        print(f"  data.yaml: {data_yaml}")
        print("  re-run without --dry-run to train.")
        return 0

    model = KillFeedModel(ModelConfig(
        model_name=args.model,
        imgsz=args.imgsz,
        freeze_backbone_layers=args.freeze_backbone,
    ))
    aug = AugmentationConfig(enabled=not args.no_augment)
    trainer = KillFeedTrainer(model, TrainingConfig(
        data_yaml=data_yaml,
        epochs=args.epochs,
        batch_size=args.batch,
        imgsz=args.imgsz,
        device=args.device,
        workers=args.workers,
        project=out_dir,
        run_name=args.name,
        patience=args.patience,
        lr0=args.lr,
        freeze_backbone_layers=args.freeze_backbone,
        augmentation=aug,
        resume_weights=args.resume,
        seed=args.seed,
    ))
    report = trainer.train()
    print()
    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
