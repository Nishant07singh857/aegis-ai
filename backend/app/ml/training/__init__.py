# backend/app/ml/training/__init__.py
"""
AEGIS AI - ML Training CLI entry points.

Run from the repo root:

    python backend/app/ml/training/train.py    --data <dataset_dir> --epochs 50 --out runs/
    python backend/app/ml/training/evaluate.py --weights <best.pt> --data <dataset_dir>
    python backend/app/ml/training/export.py   --weights <best.pt> --out <export_dir>

See docs/TRAINING.md for the full Colab walkthrough.
"""
