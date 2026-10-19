#!/usr/bin/env python3
# backend/app/ml/training/evaluate.py
"""
AEGIS AI - evaluation CLI (honest metrics only).

    python backend/app/ml/training/evaluate.py --weights runs/killfeed/train/weights/best.pt --data <dataset_dir>

Reports per-class precision / recall / mAP@0.5 / mAP@0.5:0.95 plus a
confusion summary (TP/FP/FN) on the held-out split. It NEVER prints a
single headline "accuracy %" — for detection that number does not exist,
and printing one would hide which class is failing.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aegis.eval_cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Honestly evaluate a trained kill-feed detector.")
    p.add_argument("--weights", required=True, help="path to best.pt (or last.pt).")
    p.add_argument("--data", default="",
                   help="dataset root dir OR direct path to data.yaml. "
                        "Required unless --data-yaml is given.")
    p.add_argument("--data-yaml", default="",
                   help="direct path to data.yaml (overrides --data).")
    p.add_argument("--split", default="val", choices=["val", "test"],
                   help="held-out split to evaluate on (never train).")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--device", default="auto")
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--iou", type=float, default=0.6)
    p.add_argument("--out", default="runs/killfeed_eval",
                   help="directory for report.txt / report.md / report.json.")
    p.add_argument("--format", default="txt,md,json",
                   help="comma-separated subset of txt,md,json.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    from app.ml.killfeed_detection.evaluator import EvalConfig, KillFeedEvaluator

    data_yaml = args.data_yaml
    if not data_yaml:
        if not args.data:
            logger.error("pass --data <dataset_dir> or --data-yaml <data.yaml>.")
            return 2
        cand = args.data if args.data.endswith(".yaml") else os.path.join(args.data, "data.yaml")
        # also accept the YOLO dataset materialised by train.py under --out
        if not os.path.isfile(cand):
            cand2 = os.path.join(os.path.abspath(args.data), "_yolo_dataset", "data.yaml")
            cand = cand2 if os.path.isfile(cand2) else cand
        data_yaml = cand

    evaluator = KillFeedEvaluator(EvalConfig(
        weights=args.weights,
        data_yaml=data_yaml,
        split=args.split,
        imgsz=args.imgsz,
        batch_size=args.batch,
        device=args.device,
        conf=args.conf,
        iou=args.iou,
        out_dir=args.out,
    ))
    report = evaluator.evaluate()
    print()
    print(report.to_text())
    print()
    formats = tuple(f.strip() for f in args.format.split(",") if f.strip())
    paths = evaluator.save(report, formats=formats)
    for fmt, path in paths.items():
        print(f"  {fmt}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
