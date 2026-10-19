#!/usr/bin/env python3
# backend/app/ml/training/export.py
"""
AEGIS AI - export CLI.

    python backend/app/ml/training/export.py --weights runs/killfeed/train/weights/best.pt --out exports/

Exports trained weights to ONNX (opset pinned, default 17) and VERIFIES the
graph with onnxruntime before reporting success. An unverified export is a
failed export — the script says so and exits non-zero.
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
logger = logging.getLogger("aegis.export_cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Export kill-feed weights to verified ONNX for CPU inference.")
    p.add_argument("--weights", required=True, help="path to best.pt.")
    p.add_argument("--out", default="exports/killfeed",
                   help="output directory for the .onnx file.")
    p.add_argument("--name", default="killfeed.onnx", help="output file name.")
    p.add_argument("--opset", type=int, default=17,
                   help="pinned ONNX opset (keep 17 unless you have a reason).")
    p.add_argument("--imgsz", type=int, default=640,
                   help="must match the imgsz the model trained/evaluates at.")
    p.add_argument("--dynamic", action="store_true",
                   help="dynamic batch axis (batch size may vary at runtime).")
    p.add_argument("--no-simplify", action="store_true",
                   help="skip the onnxsim simplification pass.")
    p.add_argument("--no-verify", action="store_true",
                   help="skip onnxruntime verification (NOT recommended).")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    from app.ml.killfeed_detection.export import ExportConfig, KillFeedExporter

    exporter = KillFeedExporter(ExportConfig(
        weights=args.weights,
        out_dir=args.out,
        file_name=args.name,
        onnx_opset=args.opset,
        imgsz=args.imgsz,
        dynamic_batch=args.dynamic,
        simplify=not args.no_simplify,
        verify=not args.no_verify,
    ))
    report = exporter.export()
    print()
    print(report.summary())
    if args.no_verify:
        print("\nNOTE: verification was skipped (--no-verify). "
              "Do not ship this file without verifying it.")
    return 0 if report.verified or args.no_verify else 1


if __name__ == "__main__":
    sys.exit(main())
