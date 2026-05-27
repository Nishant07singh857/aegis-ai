# backend/app/ml/fight_detection/augmentations.py
"""
AEGIS AI - Fight Detection Augmentations
Custom data augmentation pipeline for BGMI fight detection.
Includes combat-specific augmentations for robust training.
"""

import random
import math
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import structlog
import numpy as np
import cv2

logger = structlog.get_logger("aegis.ml.augmentations")


@dataclass
class AugmentationConfig:
    """Augmentation configuration"""
    # Basic augmentations
    hsv_h: float = 0.015       # Hue variation
    hsv_s: float = 0.7         # Saturation variation
    hsv_v: float = 0.4         # Value variation
    degrees: float = 5.0       # Rotation degrees
    translate: float = 0.1     # Translation fraction
    scale: float = 0.5         # Scaling factor
    shear: float = 2.0         # Shear degrees
    perspective: float = 0.0   # Perspective distortion
    flip_lr: float = 0.5       # Left-right flip probability
    flip_ud: float = 0.0       # Up-down flip probability
    mosaic: float = 0.5        # Mosaic augmentation probability
    mixup: float = 0.1         # MixUp probability
    
    # Combat-specific
    blur_simulation: float = 0.1    # Motion blur (fast movement)
    brightness_flash: float = 0.05  # Muzzle flash brightness
    smoke_overlay: float = 0.05     # Smoke grenade effect
    blood_splatter: float = 0.03    # Blood effect overlay
    screen_shake: float = 0.02      # Screen shake simulation
    
    # Intensity ranges
    min_brightness: float = 0.5
    max_brightness: float = 1.5
    min_contrast: float = 0.7
    max_contrast: float = 1.3
    
    # Probability
    apply_probability: float = 0.8  # Overall augmentation probability


class FightAugmentations:
    """
    Custom augmentation pipeline for BGMI fight detection.
    
    Combat-specific augmentations:
    - Motion blur (fast player movement)
    - Brightness flash (muzzle flash simulation)
    - Smoke overlay (smoke grenade effects)
    - Blood splatter (damage effects)
    - Screen shake (explosion effects)
    
    Standard augmentations:
    - HSV color jittering
    - Geometric transforms
    - Mosaic and MixUp
    """
    
    def __init__(self, config: Optional[AugmentationConfig] = None):
        self.config = config or AugmentationConfig()
    
    @staticmethod
    def apply(
        image: np.ndarray,
        boxes: List[Tuple[float, float, float, float]],
        labels: List[int],
        config: Optional[AugmentationConfig] = None,
    ) -> Tuple[np.ndarray, List[Tuple[float, float, float, float]], List[int]]:
        """
        Apply augmentations to image and boxes.
        
        Args:
            image: Input image (H, W, C) in RGB
            boxes: Normalized bounding boxes (x, y, w, h)
            labels: Class labels
            config: Augmentation configuration
            
        Returns:
            Augmented image, boxes, labels
        """
        
        cfg = config or AugmentationConfig()
        
        if random.random() > cfg.apply_probability:
            return image, boxes, labels
        
        h, w = image.shape[:2]
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # COLOR AUGMENTATIONS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        # HSV jittering
        if cfg.hsv_h > 0 or cfg.hsv_s > 0 or cfg.hsv_v > 0:
            image = FightAugmentations._hsv_jitter(image, cfg)
        
        # Brightness/Contrast
        image = FightAugmentations._brightness_contrast(image, cfg)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # COMBAT-SPECIFIC AUGMENTATIONS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        # Motion blur (fast movement)
        if random.random() < cfg.blur_simulation:
            image = FightAugmentations._motion_blur(image)
        
        # Muzzle flash brightness
        if random.random() < cfg.brightness_flash:
            image = FightAugmentations._muzzle_flash(image)
        
        # Smoke overlay
        if random.random() < cfg.smoke_overlay:
            image = FightAugmentations._smoke_overlay(image)
        
        # Blood splatter
        if random.random() < cfg.blood_splatter:
            image = FightAugmentations._blood_splatter(image)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # GEOMETRIC AUGMENTATIONS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        # Random affine transform
        if cfg.degrees > 0 or cfg.translate > 0 or cfg.scale > 0 or cfg.shear > 0:
            image, boxes = FightAugmentations._random_affine(
                image, boxes, cfg, (w, h)
            )
        
        # Horizontal flip
        if random.random() < cfg.flip_lr:
            image = cv2.flip(image, 1)
            boxes = FightAugmentations._flip_boxes_horizontal(boxes)
        
        return image, boxes, labels
    
    @staticmethod
    def _hsv_jitter(image: np.ndarray, cfg: AugmentationConfig) -> np.ndarray:
        """Apply HSV color jittering"""
        
        # RGB to HSV
        hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV).astype(np.float32)
        
        # Random gains
        h_gain = random.uniform(-cfg.hsv_h, cfg.hsv_h) * 255
        s_gain = random.uniform(1 - cfg.hsv_s, 1 + cfg.hsv_s)
        v_gain = random.uniform(1 - cfg.hsv_v, 1 + cfg.hsv_v)
        
        # Apply
        hsv[:, :, 0] = (hsv[:, :, 0] + h_gain) % 180
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * s_gain, 0, 255)
        hsv[:, :, 2] = np.clip(hsv[:, :, 2] * v_gain, 0, 255)
        
        return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
    
    @staticmethod
    def _brightness_contrast(image: np.ndarray, cfg: AugmentationConfig) -> np.ndarray:
        """Adjust brightness and contrast"""
        
        alpha = random.uniform(cfg.min_contrast, cfg.max_contrast)
        beta = random.uniform(
            (cfg.min_brightness - 1) * 255,
            (cfg.max_brightness - 1) * 255,
        )
        
        return cv2.convertScaleAbs(image, alpha=alpha, beta=beta)
    
    @staticmethod
    def _motion_blur(image: np.ndarray) -> np.ndarray:
        """Apply motion blur (simulates fast movement)"""
        
        kernel_size = random.choice([5, 7, 9, 11, 15])
        angle = random.uniform(0, 360)
        
        # Create motion blur kernel
        kernel = np.zeros((kernel_size, kernel_size))
        center = kernel_size // 2
        
        cos_a = math.cos(math.radians(angle))
        sin_a = math.sin(math.radians(angle))
        
        for i in range(kernel_size):
            x = int(center + (i - center) * cos_a)
            y = int(center + (i - center) * sin_a)
            if 0 <= x < kernel_size and 0 <= y < kernel_size:
                kernel[y, x] = 1
        
        kernel = kernel / kernel.sum()
        
        return cv2.filter2D(image, -1, kernel)
    
    @staticmethod
    def _muzzle_flash(image: np.ndarray) -> np.ndarray:
        """Simulate muzzle flash brightness"""
        
        # Create bright spot
        h, w = image.shape[:2]
        center_x = random.randint(w // 4, 3 * w // 4)
        center_y = random.randint(h // 4, 3 * h // 4)
        radius = random.randint(20, 80)
        
        # Create circular bright region
        y, x = np.ogrid[:h, :w]
        dist = np.sqrt((x - center_x)**2 + (y - center_y)**2)
        mask = np.exp(-dist**2 / (2 * radius**2))
        mask = mask[:, :, np.newaxis]
        
        # Apply brightness boost
        boost = random.uniform(1.2, 2.0)
        return np.clip(image * (1 + mask * (boost - 1)), 0, 255).astype(np.uint8)
    
    @staticmethod
    def _smoke_overlay(image: np.ndarray) -> np.ndarray:
        """Simulate smoke grenade effect"""
        
        h, w = image.shape[:2]
        
        # Generate Perlin-like noise for smoke
        noise = np.random.randn(h // 8, w // 8, 1).astype(np.float32)
        noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_LINEAR)
        noise = cv2.GaussianBlur(noise, (21, 21), 10)
        noise = (noise - noise.min()) / (noise.max() - noise.min() + 1e-8)
        
        # Apply as semi-transparent overlay
        opacity = random.uniform(0.1, 0.4)
        smoke_color = random.uniform(0.5, 1.0)
        
        return np.clip(
            image * (1 - noise * opacity) + noise * smoke_color * 255 * opacity,
            0, 255,
        ).astype(np.uint8)
    
    @staticmethod
    def _blood_splatter(image: np.ndarray) -> np.ndarray:
        """Simulate blood splatter effect"""
        
        h, w = image.shape[:2]
        
        # Random splatter locations
        num_splatters = random.randint(1, 3)
        
        for _ in range(num_splatters):
            cx = random.randint(w // 4, 3 * w // 4)
            cy = random.randint(h // 4, 3 * h // 4)
            radius = random.randint(5, 30)
            
            y, x = np.ogrid[:h, :w]
            dist = np.sqrt((x - cx)**2 + (y - cy)**2)
            mask = (dist < radius).astype(np.float32)
            mask = cv2.GaussianBlur(mask, (5, 5), 2)
            mask = mask[:, :, np.newaxis]
            
            # Red overlay
            red = np.zeros_like(image, dtype=np.float32)
            red[:, :, 0] = mask[:, :, 0] * random.uniform(150, 255)
            
            opacity = random.uniform(0.2, 0.6)
            image = np.clip(image * (1 - mask * opacity) + red * opacity, 0, 255)
        
        return image.astype(np.uint8)
    
    @staticmethod
    def _random_affine(
        image: np.ndarray,
        boxes: List[Tuple[float, float, float, float]],
        cfg: AugmentationConfig,
        img_size: Tuple[int, int],
    ) -> Tuple[np.ndarray, List[Tuple[float, float, float, float]]]:
        """Apply random affine transformation"""
        
        w, h = img_size
        
        # Random parameters
        angle = random.uniform(-cfg.degrees, cfg.degrees)
        scale = random.uniform(1 - cfg.scale, 1 + cfg.scale)
        dx = random.uniform(-cfg.translate, cfg.translate) * w
        dy = random.uniform(-cfg.translate, cfg.translate) * h
        shear = random.uniform(-cfg.shear, cfg.shear)
        
        # Build transformation matrix
        center = (w / 2, h / 2)
        rot_mat = cv2.getRotationMatrix2D(center, angle, scale)
        
        # Add translation
        rot_mat[0, 2] += dx
        rot_mat[1, 2] += dy
        
        # Add shear
        shear_mat = np.array([
            [1, math.tan(math.radians(shear)), 0],
            [0, 1, 0],
        ])
        rot_mat = shear_mat @ rot_mat
        
        # Apply to image
        image = cv2.warpAffine(
            image, rot_mat, (w, h),
            borderMode=cv2.BORDER_REFLECT_101,
        )
        
        # Transform boxes
        new_boxes = []
        for box in boxes:
            x, y, bw, bh = box
            
            # Convert to corners
            corners = np.array([
                [x * w, y * h],
                [(x + bw) * w, y * h],
                [x * w, (y + bh) * h],
                [(x + bw) * w, (y + bh) * h],
            ])
            
            # Transform corners
            transformed = cv2.transform(corners.reshape(1, -1, 2), rot_mat).reshape(-1, 2)
            
            # Get new bounding box
            new_x = transformed[:, 0].min() / w
            new_y = transformed[:, 1].min() / h
            new_w = (transformed[:, 0].max() - transformed[:, 0].min()) / w
            new_h = (transformed[:, 1].max() - transformed[:, 1].min()) / h
            
            # Clip to valid range
            new_x = max(0, min(1, new_x))
            new_y = max(0, min(1, new_y))
            new_w = max(0.001, min(1 - new_x, new_w))
            new_h = max(0.001, min(1 - new_y, new_h))
            
            new_boxes.append((new_x, new_y, new_w, new_h))
        
        return image, new_boxes
    
    @staticmethod
    def _flip_boxes_horizontal(
        boxes: List[Tuple[float, float, float, float]],
    ) -> List[Tuple[float, float, float, float]]:
        """Flip boxes horizontally"""
        
        return [
            (1 - x - w, y, w, h)
            for x, y, w, h in boxes
        ]
    
    @staticmethod
    def apply_mosaic(
        images: List[np.ndarray],
        boxes_list: List[List[Tuple[float, float, float, float]]],
        labels_list: List[List[int]],
        output_size: Tuple[int, int] = (640, 640),
    ) -> Tuple[np.ndarray, List[Tuple[float, float, float, float]], List[int]]:
        """
        Apply mosaic augmentation (combine 4 images).
        
        Args:
            images: 4 input images
            boxes_list: Boxes for each image
            labels_list: Labels for each image
            output_size: Output image size
            
        Returns:
            Combined image, merged boxes, merged labels
        """
        
        if len(images) < 4:
            return images[0], boxes_list[0], labels_list[0]
        
        h, w = output_size
        
        # Random center point for mosaic
        cx = random.randint(w // 4, 3 * w // 4)
        cy = random.randint(h // 4, 3 * h // 4)
        
        # Create canvas
        mosaic = np.zeros((h, w, 3), dtype=np.uint8)
        all_boxes = []
        all_labels = []
        
        # Place 4 images
        placements = [
            (0, 0, cx, cy),           # Top-left
            (cx, 0, w - cx, cy),      # Top-right
            (0, cy, cx, h - cy),      # Bottom-left
            (cx, cy, w - cx, h - cy), # Bottom-right
        ]
        
        for i, (img, boxes, labels) in enumerate(zip(images, boxes_list, labels_list)):
            if i >= len(placements):
                break
            
            px, py, pw, ph = placements[i]
            
            # Resize image to fit placement
            resized = cv2.resize(img, (pw, ph))
            mosaic[py:py+ph, px:px+pw] = resized
            
            # Adjust boxes
            for box in boxes:
                x, y, bw, bh = box
                
                # Scale to placement
                new_x = (x * pw + px) / w
                new_y = (y * ph + py) / h
                new_w = bw * pw / w
                new_h = bh * ph / h
                
                all_boxes.append((new_x, new_y, new_w, new_h))
            
            all_labels.extend(labels)
        
        return mosaic, all_boxes, all_labels
    
    @staticmethod
    def apply_mixup(
        image1: np.ndarray,
        boxes1: List[Tuple[float, float, float, float]],
        labels1: List[int],
        image2: np.ndarray,
        boxes2: List[Tuple[float, float, float, float]],
        labels2: List[int],
        alpha: float = 0.5,
    ) -> Tuple[np.ndarray, List[Tuple[float, float, float, float]], List[int]]:
        """
        Apply MixUp augmentation (blend two images).
        
        Args:
            image1, image2: Input images
            boxes1, boxes2: Bounding boxes
            labels1, labels2: Class labels
            alpha: Mixing ratio
            
        Returns:
            Blended image, merged boxes, merged labels
        """
        
        # Beta distribution for mixing ratio
        lam = np.random.beta(alpha, alpha)
        
        # Ensure same size
        if image1.shape != image2.shape:
            image2 = cv2.resize(image2, (image1.shape[1], image1.shape[0]))
        
        # Mix images
        mixed = (lam * image1 + (1 - lam) * image2).astype(np.uint8)
        
        # Merge boxes and labels
        all_boxes = list(boxes1) + list(boxes2)
        all_labels = list(labels1) + list(labels2)
        
        return mixed, all_boxes, all_labels
    
    def get_stats(self) -> Dict[str, Any]:
        """Get augmentation configuration"""
        return {
            "config": {
                k: v for k, v in self.config.__dict__.items()
                if not k.startswith('_')
            },
        }