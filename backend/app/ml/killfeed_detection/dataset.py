# backend/app/ml/killfeed_detection/dataset.py
"""
AEGIS AI - Kill-Feed Detection Dataset
Loads the W1 dataset (manifest.csv + frames, or COCO JSON), validates it,
and builds a YOLO-format training dataset that ultralytics consumes natively.

W1 MANIFEST CONTRACT (manifest.csv, one row per bounding box)
------------------------------------------------------------
Required columns:
    image_path   path to the frame, relative to the dataset root
                 (absolute paths are also accepted)
    x_min, y_min, x_max, y_max
                 box corners in ABSOLUTE PIXELS (Pascal-VOC style)

Optional columns:
    class_name   label string; must be in DatasetConfig.class_names.
                 Defaults to the single class in config when the column is
                 absent or empty.
    split        train | val | test. When absent/empty, images are assigned
                 to splits automatically (stratified-ish by image, seeded).
    width, height
                 image size in pixels. When absent, it is read from the
                 image file (requires Pillow or OpenCV).

Background / hard-negative images (frames with NO kill feed visible):
    add a row with the image_path and empty box fields (or simply omit the
    image from the manifest and drop the file under ``backgrounds/`` —
    see prepare_yolo_dataset).

COCO JSON is also accepted (auto-detected by file extension, or pass
format="coco"): standard {"images","annotations","categories"} format with
bboxes as [x, y, w, h] in absolute pixels.

Example manifest.csv:
    image_path,x_min,y_min,x_max,y_max,class_name,split,width,height
    frames/m1/000123.jpg,1520,40,1870,220,killfeed_panel,train,1920,1080
    frames/m1/000123.jpg,1540,60,1860,110,killfeed_entry,train,1920,1080
    frames/m1/000124.jpg,,,,,,train,1920,1080        # background frame

The dataset itself does NOT run the training loop — ultralytics owns data
loading. KillFeedDataset is the *preparation and validation* layer: it
turns the human-labelled manifest into a clean, audited YOLO dataset plus
a dataset card (counts, splits, warnings) so bad labels are caught BEFORE
an expensive GPU run.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("aegis.ml.killfeed.dataset")

MANIFEST_COLUMNS = (
    "image_path", "x_min", "y_min", "x_max", "y_max",
    "class_name", "split", "width", "height",
)

REQUIRED_MANIFEST_COLUMNS = ("image_path", "x_min", "y_min", "x_max", "y_max")


@dataclass
class DatasetConfig:
    """Kill-feed dataset configuration. No hardcoded paths — the dataset
    root always comes from the caller (CLI --data / W1 output dir)."""

    # Classes (order defines class ids 0..N-1 in data.yaml)
    class_names: List[str] = field(default_factory=lambda: [
        "killfeed_panel",   # whole kill-feed region -> crop target for OCR
        "killfeed_entry",   # one kill row inside the panel -> per-row OCR crop
    ])

    # Splits
    val_fraction: float = 0.15
    test_fraction: float = 0.05   # 0.0 disables the test split
    split_seed: int = 42

    # Quality gates
    min_box_pixels: int = 8        # boxes smaller than this (either side) are dropped
    max_box_fraction: float = 0.95  # boxes covering more than this of the image are dropped
    require_all_images_exist: bool = True

    # YOLO export
    yolo_dirname: str = "yolo"     # <out>/<yolo_dirname>/{images,labels}/{train,val,test}
    copy_images: bool = False      # False -> symlink (fast, saves disk); True -> copy
    include_backgrounds_dir: bool = True  # also sweep <root>/backgrounds/ as background images


@dataclass
class BoxRecord:
    """One validated annotation."""
    image_path: str          # absolute path
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    class_id: int
    class_name: str
    width: int
    height: int
    split: str               # train | val | test


class ManifestError(ValueError):
    """Raised when the manifest is malformed."""


def _read_image_size(path: str) -> Tuple[int, int]:
    """Read (width, height) without heavy deps at import time."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except ImportError:
        pass
    try:
        import cv2
        img = cv2.imread(path)
        if img is None:
            raise ManifestError(f"cannot read image: {path}")
        h, w = img.shape[:2]
        return w, h
    except ImportError:
        pass
    raise ManifestError(
        f"cannot determine size of {path}: install Pillow or opencv-python-headless"
    )


def _parse_float(value: Any, field_name: str, row_no: int) -> Optional[float]:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ManifestError(f"row {row_no}: column '{field_name}' is not a number: {value!r}")


class KillFeedDataset:
    """
    Preparation + validation layer for the kill-feed detection dataset.

    Usage:
        ds = KillFeedDataset(DatasetConfig())
        records, stats = ds.load("path/to/dataset_root")   # finds manifest.csv
        out = ds.prepare_yolo_dataset("path/to/dataset_root", "runs/datasets/killfeed_v1")
        # out["data_yaml"] -> feed to KillFeedTrainer / train.py
    """

    def __init__(self, config: Optional[DatasetConfig] = None):
        self.config = config or DatasetConfig()
        self.records: List[BoxRecord] = []
        self.background_images: List[str] = []   # absolute paths, no boxes
        self.warnings: List[str] = []
        self._image_sizes: Dict[str, Tuple[int, int]] = {}

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    def load(self, dataset_root: str, manifest: Optional[str] = None,
             fmt: str = "auto") -> Tuple[List[BoxRecord], Dict[str, Any]]:
        """Load + validate the manifest. Returns (records, stats)."""
        dataset_root = os.path.abspath(dataset_root)
        manifest = manifest or self._find_manifest(dataset_root)
        if fmt == "auto":
            fmt = "coco" if manifest.lower().endswith(".json") else "csv"

        self.records, self.background_images, self.warnings = [], [], []
        if fmt == "csv":
            self._load_csv(dataset_root, manifest)
        elif fmt == "coco":
            self._load_coco(dataset_root, manifest)
        else:
            raise ManifestError(f"unknown manifest format: {fmt!r} (use 'csv' or 'coco')")

        if self.config.include_backgrounds_dir:
            self._sweep_backgrounds_dir(dataset_root)

        self._assign_splits()
        stats = self.get_stats()
        logger.info(
            "KillFeedDataset loaded: %d boxes over %d images (%d background), "
            "splits=%s", len(self.records), stats["num_images"],
            len(self.background_images), stats["splits"],
        )
        for w in self.warnings:
            logger.warning("dataset warning: %s", w)
        return self.records, stats

    def _find_manifest(self, dataset_root: str) -> str:
        for name in ("manifest.csv", "annotations.csv", "annotations.json",
                     "instances_train.json", "coco.json"):
            cand = os.path.join(dataset_root, name)
            if os.path.isfile(cand):
                return cand
        raise ManifestError(
            f"no manifest found in {dataset_root}: expected manifest.csv "
            "(or annotations.json for COCO)"
        )

    def _resolve_image(self, dataset_root: str, rel: str, row_no: int) -> str:
        rel = (rel or "").strip()
        if not rel:
            raise ManifestError(f"row {row_no}: empty image_path")
        abs_path = rel if os.path.isabs(rel) else os.path.join(dataset_root, rel)
        if not os.path.isfile(abs_path):
            msg = f"row {row_no}: image not found: {rel}"
            if self.config.require_all_images_exist:
                raise ManifestError(msg)
            self.warnings.append(msg + " (skipped)")
            return ""
        return os.path.abspath(abs_path)

    def _class_id(self, name: Any, row_no: int) -> Tuple[int, str]:
        name = (str(name).strip() if name not in (None, "") else "")
        if not name:
            if len(self.config.class_names) == 1:
                return 0, self.config.class_names[0]
            raise ManifestError(
                f"row {row_no}: empty class_name but config has "
                f"{len(self.config.class_names)} classes {self.config.class_names}"
            )
        if name not in self.config.class_names:
            raise ManifestError(
                f"row {row_no}: unknown class_name {name!r}; "
                f"expected one of {self.config.class_names}"
            )
        return self.config.class_names.index(name), name

    def _validate_box(self, x_min: float, y_min: float, x_max: float, y_max: float,
                      w: int, h: int, row_no: int) -> Optional[Tuple[float, float, float, float]]:
        bw, bh = x_max - x_min, y_max - y_min
        if bw <= 0 or bh <= 0:
            self.warnings.append(f"row {row_no}: degenerate box dropped (w={bw:.1f}, h={bh:.1f})")
            return None
        if bw < self.config.min_box_pixels or bh < self.config.min_box_pixels:
            self.warnings.append(
                f"row {row_no}: tiny box dropped ({bw:.0f}x{bh:.0f}px "
                f"< {self.config.min_box_pixels}px)"
            )
            return None
        if (bw * bh) / (w * h) > self.config.max_box_fraction:
            self.warnings.append(f"row {row_no}: oversized box dropped (covers whole frame)")
            return None
        # clip to frame
        x_min_c = max(0.0, min(x_min, w - 1))
        y_min_c = max(0.0, min(y_min, h - 1))
        x_max_c = max(1.0, min(x_max, w))
        y_max_c = max(1.0, min(y_max, h))
        if (x_min_c, y_min_c, x_max_c, y_max_c) != (x_min, y_min, x_max, y_max):
            self.warnings.append(f"row {row_no}: box clipped to frame bounds")
        return x_min_c, y_min_c, x_max_c, y_max_c

    def _load_csv(self, dataset_root: str, manifest: str) -> None:
        with open(manifest, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = [c.strip() for c in (reader.fieldnames or [])]
            missing = [c for c in REQUIRED_MANIFEST_COLUMNS if c not in header]
            if missing:
                raise ManifestError(
                    f"{manifest}: missing required columns {missing}; "
                    f"got {header}. See dataset.py docstring for the contract."
                )
            seen_images: Dict[str, bool] = {}
            for row_no, row in enumerate(reader, start=2):
                img = self._resolve_image(dataset_root, row.get("image_path"), row_no)
                if not img:
                    continue
                seen_images[img] = True

                w_given = _parse_float(row.get("width"), "width", row_no)
                h_given = _parse_float(row.get("height"), "height", row_no)
                if w_given and h_given:
                    w, h = int(w_given), int(h_given)
                else:
                    w, h = _read_image_size(img)
                    self._image_sizes[img] = (w, h)

                coords = [_parse_float(row.get(c), c, row_no) for c in
                          ("x_min", "y_min", "x_max", "y_max")]
                if all(c is None for c in coords):
                    # background image row (no boxes on this frame)
                    if img not in self.background_images:
                        self.background_images.append(img)
                    continue
                if any(c is None for c in coords):
                    raise ManifestError(f"row {row_no}: partial box coordinates {coords}")
                x_min, y_min, x_max, y_max = coords  # type: ignore[misc]

                valid = self._validate_box(x_min, y_min, x_max, y_max, w, h, row_no)
                if valid is None:
                    continue
                class_id, class_name = self._class_id(row.get("class_name"), row_no)
                split = (row.get("split") or "").strip().lower() or "auto"
                if split not in ("train", "val", "test", "auto"):
                    raise ManifestError(
                        f"row {row_no}: bad split {split!r} (use train|val|test)"
                    )
                self.records.append(BoxRecord(
                    image_path=img, x_min=valid[0], y_min=valid[1],
                    x_max=valid[2], y_max=valid[3], class_id=class_id,
                    class_name=class_name, width=w, height=h, split=split,
                ))

    def _load_coco(self, dataset_root: str, manifest: str) -> None:
        with open(manifest, encoding="utf-8") as f:
            coco = json.load(f)
        images = {im["id"]: im for im in coco.get("images", [])}
        cat_to_name = {c["id"]: c["name"] for c in coco.get("categories", [])}
        anns_by_image: Dict[Any, list] = {}
        for ann in coco.get("annotations", []):
            anns_by_image.setdefault(ann["image_id"], []).append(ann)
        for im_id, im in images.items():
            img = self._resolve_image(dataset_root, im["file_name"], row_no=0)
            if not img:
                continue
            w, h = int(im.get("width") or 0), int(im.get("height") or 0)
            if not (w and h):
                w, h = _read_image_size(img)
            anns = anns_by_image.get(im_id, [])
            if not anns:
                self.background_images.append(img)
                continue
            for ann in anns:
                x, y, bw, bh = ann["bbox"]
                valid = self._validate_box(x, y, x + bw, y + bh, w, h, row_no=0)
                if valid is None:
                    continue
                class_id, class_name = self._class_id(
                    cat_to_name.get(ann["category_id"], ""), row_no=0)
                self.records.append(BoxRecord(
                    image_path=img, x_min=valid[0], y_min=valid[1],
                    x_max=valid[2], y_max=valid[3], class_id=class_id,
                    class_name=class_name, width=w, height=h, split="auto",
                ))

    def _sweep_backgrounds_dir(self, dataset_root: str) -> None:
        bg_dir = os.path.join(dataset_root, "backgrounds")
        if not os.path.isdir(bg_dir):
            return
        exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        for name in sorted(os.listdir(bg_dir)):
            if name.lower().endswith(exts):
                p = os.path.abspath(os.path.join(bg_dir, name))
                if p not in self.background_images:
                    self.background_images.append(p)

    def _assign_splits(self) -> None:
        """Images marked 'auto' are split by image (all boxes of one frame stay
        together — splitting by box would leak near-duplicate frames across
        train/val and inflate metrics)."""
        rng = random.Random(self.config.split_seed)
        images = sorted({r.image_path for r in self.records})
        auto_images = [im for im in images
                       if all(r.split == "auto" for r in self.records if r.image_path == im)]
        rng.shuffle(auto_images)
        n = len(auto_images)
        n_test = int(round(n * self.config.test_fraction))
        n_val = int(round(n * self.config.val_fraction))
        # honour the requested splits: if the user asked for a val/test
        # fraction, guarantee at least one image when the pool allows it
        # (otherwise ultralytics gets a data.yaml with no val split and
        # training fails with an obscure error).
        if self.config.test_fraction > 0 and n >= 3:
            n_test = max(1, n_test)
        if self.config.val_fraction > 0 and n >= 2:
            n_val = max(1, n_val)
        # never starve the train split (matters for tiny datasets)
        while n > 0 and n_test + n_val >= n:
            if n_test > 0:
                n_test -= 1
            elif n_val > 0:
                n_val -= 1
            else:
                break
        test_set = set(auto_images[:n_test])
        val_set = set(auto_images[n_test:n_test + n_val])
        for r in self.records:
            if r.split == "auto":
                if r.image_path in test_set:
                    r.split = "test"
                elif r.image_path in val_set:
                    r.split = "val"
                else:
                    r.split = "train"

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        per_split: Dict[str, int] = {}
        per_class: Dict[str, int] = {c: 0 for c in self.config.class_names}
        images = set()
        for r in self.records:
            per_split[r.split] = per_split.get(r.split, 0) + 1
            per_class[r.class_name] += 1
            images.add(r.image_path)
        return {
            "num_boxes": len(self.records),
            "num_images": len(images),
            "num_background_images": len(self.background_images),
            "splits": per_split,
            "per_class_boxes": per_class,
            "class_names": self.config.class_names,
            "num_warnings": len(self.warnings),
        }

    # ------------------------------------------------------------------
    # YOLO export
    # ------------------------------------------------------------------
    def prepare_yolo_dataset(self, dataset_root: str, out_dir: str,
                             manifest: Optional[str] = None,
                             fmt: str = "auto") -> Dict[str, Any]:
        """
        Validate the manifest and materialise a YOLO dataset:
            <out_dir>/yolo/images/{train,val,test}/  (symlinks or copies)
            <out_dir>/yolo/labels/{train,val,test}/  (*.txt, YOLO format)
            <out_dir>/data.yaml
            <out_dir>/dataset_card.json
        Returns dict with paths + stats. Idempotent: wipes <out_dir>/yolo first.
        """
        if not self.records:
            self.load(dataset_root, manifest=manifest, fmt=fmt)
        stats = self.get_stats()
        if stats["num_boxes"] == 0:
            raise ManifestError("manifest contains zero valid boxes — nothing to train on")

        yolo_root = os.path.join(os.path.abspath(out_dir), self.config.yolo_dirname)
        if os.path.isdir(yolo_root):
            shutil.rmtree(yolo_root)

        # group boxes per image per split
        by_image: Dict[str, List[BoxRecord]] = {}
        for r in self.records:
            by_image.setdefault(r.image_path, []).append(r)
        # background images default to the train split (hard negatives belong
        # in training; a few also help val — keep it simple and honest)
        bg_by_split: Dict[str, List[str]] = {"train": [], "val": [], "test": []}
        for i, bg in enumerate(sorted(set(self.background_images))):
            target = "train" if i % 10 < 8 else "val"
            bg_by_split[target].append(bg)

        written = {"train": 0, "val": 0, "test": 0}
        for split in ("train", "val", "test"):
            img_dir = os.path.join(yolo_root, "images", split)
            lbl_dir = os.path.join(yolo_root, "labels", split)
            os.makedirs(img_dir, exist_ok=True)
            os.makedirs(lbl_dir, exist_ok=True)

            split_records = {im: rs for im, rs in by_image.items()
                             if rs[0].split == split}
            for bg in bg_by_split.get(split, []):
                split_records.setdefault(bg, [])

            for src, recs in sorted(split_records.items()):
                base = os.path.splitext(os.path.basename(src))[0]
                # de-duplicate basenames across source folders
                suffix, n = "", 0
                while os.path.lexists(os.path.join(img_dir, base + suffix + os.path.splitext(src)[1])):
                    n += 1
                    suffix = f"_{n}"
                stem = base + suffix
                ext = os.path.splitext(src)[1].lower() or ".jpg"
                dst_img = os.path.join(img_dir, stem + ext)
                if self.config.copy_images:
                    shutil.copy2(src, dst_img)
                else:
                    os.symlink(src, dst_img)

                lines = []
                w0 = recs[0].width if recs else _read_image_size(src)[0]
                h0 = recs[0].height if recs else _read_image_size(src)[1]
                for r in recs:
                    cx = ((r.x_min + r.x_max) / 2) / w0
                    cy = ((r.y_min + r.y_max) / 2) / h0
                    bw = (r.x_max - r.x_min) / w0
                    bh = (r.y_max - r.y_min) / h0
                    lines.append(f"{r.class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
                with open(os.path.join(lbl_dir, stem + ".txt"), "w") as f:
                    f.write("\n".join(lines))
                written[split] += 1

        data_yaml = os.path.join(os.path.abspath(out_dir), "data.yaml")
        with open(data_yaml, "w") as f:
            f.write(f"# generated by KillFeedDataset.prepare_yolo_dataset\n")
            f.write(f"path: {yolo_root}\n")
            for split in ("train", "val", "test"):
                if written[split]:
                    f.write(f"{split}: images/{split}\n")
            f.write(f"nc: {len(self.config.class_names)}\n")
            f.write("names:\n")
            for i, name in enumerate(self.config.class_names):
                f.write(f"  {i}: {name}\n")

        card = {
            "source_root": os.path.abspath(dataset_root),
            "manifest_stats": stats,
            "yolo_images_written": written,
            "warnings": self.warnings,
            "class_names": self.config.class_names,
        }
        with open(os.path.join(os.path.abspath(out_dir), "dataset_card.json"), "w") as f:
            json.dump(card, f, indent=2)

        logger.info("YOLO dataset written: %s (images=%s)", yolo_root, written)
        return {
            "yolo_root": yolo_root,
            "data_yaml": data_yaml,
            "dataset_card": os.path.join(os.path.abspath(out_dir), "dataset_card.json"),
            "images_written": written,
            "stats": stats,
            "warnings": self.warnings,
        }
