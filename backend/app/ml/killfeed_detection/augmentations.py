# backend/app/ml/killfeed_detection/augmentations.py
"""
AEGIS AI - Kill-Feed Detection Augmentations
Augmentation policy for the kill-feed detector, expressed as ultralytics
training hyperparameters.

Why this policy (and not the fight-detection one):
- The kill feed is *screen UI*, not world geometry: it never appears upside
  down (flipud=0), never under perspective warp, and barely rotates.
  Aggressive geometric augmentation would teach the model lies.
- What DOES vary between matches: brightness/contrast (stream encoder,
  day/night maps), slight scale/position shifts (different resolutions and
  HUD safe-areas), and motion blur (fast camera pans).
- Kill-feed entries are small thin rows: mosaic stays ON (it is the best
  augmentation for small objects) and random erasing stays low (erasing a
  whole thin row deletes the object).

to_ultralytics_kwargs() maps this config 1:1 onto YOLO.train() arguments,
so the policy is versioned here and applied there — one source of truth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Any

logger = logging.getLogger("aegis.ml.killfeed.augmentations")


@dataclass
class AugmentationConfig:
    """Kill-feed augmentation policy."""

    # colour / photometric (stream encoders vary these a lot)
    hsv_h: float = 0.015
    hsv_s: float = 0.5
    hsv_v: float = 0.4

    # geometry — conservative for UI
    degrees: float = 3.0       # tiny rotation only
    translate: float = 0.08    # HUD safe-area shifts
    scale: float = 0.3         # resolution differences
    shear: float = 1.0
    perspective: float = 0.0   # screens don't perspectively warp
    flipud: float = 0.0        # UI is never upside down
    fliplr: float = 0.0        # kill feed is always top-RIGHT; mirroring lies

    # composition
    mosaic: float = 0.8        # best augmentation for small entry rows
    mixup: float = 0.05        # low: mixed UI overlays look unrealistic
    copy_paste: float = 0.0    # pasted UI rows look fake; keep off

    # regularisation
    erasing: float = 0.2       # random erasing (0..1); low so thin rows survive

    # master switch
    enabled: bool = True


class KillFeedAugmentations:
    """Maps AugmentationConfig onto ultralytics YOLO.train() hyperparameters."""

    def __init__(self, config: AugmentationConfig | None = None):
        self.config = config or AugmentationConfig()

    def to_ultralytics_kwargs(self) -> Dict[str, Any]:
        c = self.config
        if not c.enabled:
            return dict(hsv_h=0.0, hsv_s=0.0, hsv_v=0.0, degrees=0.0,
                        translate=0.0, scale=0.0, shear=0.0, perspective=0.0,
                        flipud=0.0, fliplr=0.0, mosaic=0.0, mixup=0.0,
                        copy_paste=0.0, erasing=0.0)
        return dict(
            hsv_h=c.hsv_h, hsv_s=c.hsv_s, hsv_v=c.hsv_v,
            degrees=c.degrees, translate=c.translate, scale=c.scale,
            shear=c.shear, perspective=c.perspective,
            flipud=c.flipud, fliplr=c.fliplr,
            mosaic=c.mosaic, mixup=c.mixup, copy_paste=c.copy_paste,
            erasing=c.erasing,
        )

    def describe(self) -> str:
        c = self.config
        state = "ENABLED" if c.enabled else "DISABLED"
        return (
            f"Kill-feed augmentation policy [{state}]\n"
            f"  photometric : hsv=({c.hsv_h}, {c.hsv_s}, {c.hsv_v})\n"
            f"  geometric   : degrees={c.degrees} translate={c.translate} "
            f"scale={c.scale} shear={c.shear} perspective={c.perspective}\n"
            f"  flips       : flipud={c.flipud} fliplr={c.fliplr} "
            f"(UI stays top-right, never mirrored)\n"
            f"  composition : mosaic={c.mosaic} mixup={c.mixup} "
            f"copy_paste={c.copy_paste}\n"
            f"  erasing     : {c.erasing}\n"
            f"Rationale: UI overlays vary in brightness/position/scale across "
            f"streams, never in orientation — so photometric + mild geometric "
            f"augmentation, no flips, no perspective."
        )
