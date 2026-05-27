# backend/app/ml/fight_detection/dataset.py
"""
AEGIS AI - Fight Detection Dataset
Custom dataset for BGMI fight detection training.
Handles video sequences, annotations, and data loading.
"""

import os
import json
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import structlog
import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader

logger = structlog.get_logger("aegis.ml.dataset")


@dataclass
class DatasetConfig:
    """Dataset configuration"""
    # Paths
    data_root: str = "data/bgmi_fights"
    train_annotations: str = "annotations/train.json"
    val_annotations: str = "annotations/val.json"
    test_annotations: str = "annotations/test.json"
    
    # Image settings
    image_size: Tuple[int, int] = (640, 640)
    normalize: bool = True
    mean: Tuple[float, float, float] = (0.485, 0.456, 0.406)
    std: Tuple[float, float, float] = (0.229, 0.224, 0.225)
    
    # Sequence settings
    sequence_length: int = 5  # Frames per sequence
    sequence_stride: int = 2  # Frame stride
    min_sequence_length: int = 3
    
    # Augmentation
    use_augmentation: bool = True
    
    # Classes
    num_classes: int = 7
    class_names: List[str] = field(default_factory=lambda: [
        "player_shooting", "player_knocked", "explosion",
        "muzzle_flash", "blood_effect", "damage_indicator", "knock_animation",
    ])
    
    # Loading
    num_workers: int = 4
    prefetch_factor: int = 2
    pin_memory: bool = True
    
    # Caching
    cache_images: bool = True
    cache_size_gb: float = 4.0
    
    # Filtering
    min_box_area: float = 16.0
    max_box_area: float = 0.8  # Fraction of image
    min_visibility: float = 0.3
    
    # Class balancing
    use_class_weights: bool = True
    class_weights: Optional[List[float]] = None


@dataclass
class Annotation:
    """Single image annotation"""
    image_id: str
    image_path: str
    width: int
    height: int
    boxes: List[Tuple[float, float, float, float]]  # (x, y, w, h) normalized
    labels: List[int]
    sequence_id: Optional[str] = None
    frame_index: Optional[int] = None


class FightDataset(Dataset):
    """
    BGMI fight detection dataset.
    
    Features:
    - Video sequence loading with temporal context
    - Multi-scale training support
    - Class balancing
    - Image caching for faster training
    - Automatic validation split
    """
    
    def __init__(self, config: Optional[DatasetConfig] = None, split: str = "train"):
        self.config = config or DatasetConfig()
        self.split = split
        
        # Load annotations
        self.annotations: List[Annotation] = []
        self._load_annotations()
        
        # Image cache
        self._image_cache: Dict[str, np.ndarray] = {}
        self._cache_size = 0
        
        # Class statistics
        self.class_counts = defaultdict(int)
        self._compute_class_stats()
        
        # Sequence grouping
        self.sequences: Dict[str, List[int]] = defaultdict(list)
        self._group_sequences()
        
        logger.info(
            f"FightDataset loaded: {len(self.annotations)} images, "
            f"{len(self.sequences)} sequences ({split})"
        )
    
    def _load_annotations(self) -> None:
        """Load annotations from JSON file"""
        
        if self.split == "train":
            ann_path = os.path.join(self.config.data_root, self.config.train_annotations)
        elif self.split == "val":
            ann_path = os.path.join(self.config.data_root, self.config.val_annotations)
        else:
            ann_path = os.path.join(self.config.data_root, self.config.test_annotations)
        
        if not os.path.exists(ann_path):
            logger.warning(f"Annotations not found: {ann_path}")
            return
        
        try:
            with open(ann_path, 'r') as f:
                data = json.load(f)
            
            for img_ann in data.get("images", []):
                ann = Annotation(
                    image_id=img_ann["id"],
                    image_path=os.path.join(self.config.data_root, img_ann["file_name"]),
                    width=img_ann["width"],
                    height=img_ann["height"],
                    boxes=[],
                    labels=[],
                    sequence_id=img_ann.get("sequence_id"),
                    frame_index=img_ann.get("frame_index"),
                )
                
                # Load corresponding boxes
                for obj_ann in data.get("annotations", []):
                    if obj_ann["image_id"] == img_ann["id"]:
                        bbox = obj_ann["bbox"]  # COCO format: [x, y, w, h]
                        
                        # Normalize coordinates
                        norm_x = bbox[0] / img_ann["width"]
                        norm_y = bbox[1] / img_ann["height"]
                        norm_w = bbox[2] / img_ann["width"]
                        norm_h = bbox[3] / img_ann["height"]
                        
                        # Filter by size
                        area = norm_w * norm_h
                        if area < self.config.min_box_area / (img_ann["width"] * img_ann["height"]):
                            continue
                        if area > self.config.max_box_area:
                            continue
                        
                        ann.boxes.append((norm_x, norm_y, norm_w, norm_h))
                        ann.labels.append(obj_ann["category_id"])
                
                self.annotations.append(ann)
                
        except Exception as e:
            logger.error(f"Failed to load annotations: {e}")
    
    def _compute_class_stats(self) -> None:
        """Compute class distribution statistics"""
        
        for ann in self.annotations:
            for label in ann.labels:
                self.class_counts[label] += 1
        
        total = sum(self.class_counts.values())
        
        if total > 0 and self.config.use_class_weights:
            # Inverse frequency weighting
            self.config.class_weights = [
                total / (len(self.class_counts) * max(1, self.class_counts.get(i, 1)))
                for i in range(self.config.num_classes)
            ]
            
            logger.info(f"Class weights: {[round(w, 3) for w in self.config.class_weights]}")
    
    def _group_sequences(self) -> None:
        """Group annotations by sequence for temporal loading"""
        
        for idx, ann in enumerate(self.annotations):
            if ann.sequence_id:
                self.sequences[ann.sequence_id].append(idx)
        
        # Sort frames within each sequence
        for seq_id in self.sequences:
            self.sequences[seq_id].sort(
                key=lambda idx: self.annotations[idx].frame_index or 0
            )
    
    def __len__(self) -> int:
        return len(self.annotations)
    
    def __getitem__(self, idx: int) -> Dict[str, Any]:
        """
        Get a training sample.
        
        Returns:
            Dict with 'image', 'boxes', 'labels', and optional 'sequence'
        """
        
        ann = self.annotations[idx]
        
        # Load image
        image = self._load_image(ann.image_path)
        
        # Load temporal context if available
        sequence_frames = None
        if self.config.sequence_length > 1 and ann.sequence_id:
            sequence_frames = self._load_sequence(ann.sequence_id, ann.frame_index or 0)
        
        # Apply augmentations if training
        if self.split == "train" and self.config.use_augmentation:
            from .augmentations import FightAugmentations
            image, ann.boxes, ann.labels = FightAugmentations.apply(
                image, ann.boxes, ann.labels
            )
        
        # Normalize
        if self.config.normalize:
            image = self._normalize(image)
        
        # Convert to tensor
        image_tensor = torch.from_numpy(image).permute(2, 0, 1).float()
        
        # Prepare targets
        boxes_tensor = torch.tensor(ann.boxes, dtype=torch.float32) if ann.boxes else torch.zeros((0, 4))
        labels_tensor = torch.tensor(ann.labels, dtype=torch.long) if ann.labels else torch.zeros(0, dtype=torch.long)
        
        sample = {
            "image": image_tensor,
            "boxes": boxes_tensor,
            "labels": labels_tensor,
            "image_id": ann.image_id,
            "orig_size": (ann.height, ann.width),
        }
        
        if sequence_frames is not None:
            sample["sequence"] = sequence_frames
        
        return sample
    
    def _load_image(self, path: str) -> np.ndarray:
        """Load image with caching"""
        
        if path in self._image_cache:
            return self._image_cache[path].copy()
        
        try:
            import cv2
            image = cv2.imread(path)
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            
            # Resize to target size
            image = cv2.resize(image, self.config.image_size)
            
            # Cache if enabled
            if self.config.cache_images:
                self._image_cache[path] = image.copy()
                self._cache_size += image.nbytes
                
                # Limit cache size
                if self._cache_size > self.config.cache_size_gb * 1024**3:
                    # Remove oldest entries
                    remove_count = len(self._image_cache) // 4
                    for old_path in list(self._image_cache.keys())[:remove_count]:
                        del self._image_cache[old_path]
                    self._cache_size = sum(img.nbytes for img in self._image_cache.values())
            
            return image
            
        except Exception as e:
            logger.error(f"Failed to load image {path}: {e}")
            return np.zeros((*self.config.image_size, 3), dtype=np.uint8)
    
    def _load_sequence(
        self, sequence_id: str, current_frame: int
    ) -> Optional[torch.Tensor]:
        """Load temporal sequence of frames"""
        
        indices = self.sequences.get(sequence_id, [])
        
        if len(indices) < self.config.min_sequence_length:
            return None
        
        # Get frames around current frame
        half_window = self.config.sequence_length // 2
        start_idx = max(0, current_frame - half_window)
        
        frames = []
        for i in range(self.config.sequence_length):
            seq_idx = start_idx + i * self.config.sequence_stride
            
            if seq_idx < len(indices):
                ann_idx = indices[seq_idx]
                ann = self.annotations[ann_idx]
                img = self._load_image(ann.image_path)
                
                if self.config.normalize:
                    img = self._normalize(img)
                
                frames.append(torch.from_numpy(img).permute(2, 0, 1))
        
        if len(frames) >= self.config.min_sequence_length:
            # Pad if needed
            while len(frames) < self.config.sequence_length:
                frames.append(frames[-1].clone())
            
            return torch.stack(frames)  # (T, C, H, W)
        
        return None
    
    def _normalize(self, image: np.ndarray) -> np.ndarray:
        """Normalize image"""
        image = image.astype(np.float32) / 255.0
        mean = np.array(self.config.mean, dtype=np.float32)
        std = np.array(self.config.std, dtype=np.float32)
        image = (image - mean) / std
        return image
    
    def get_data_loader(
        self,
        batch_size: int = 16,
        shuffle: bool = True,
        num_workers: Optional[int] = None,
    ) -> DataLoader:
        """Create DataLoader for this dataset"""
        
        return DataLoader(
            self,
            batch_size=batch_size,
            shuffle=shuffle and self.split == "train",
            num_workers=num_workers or self.config.num_workers,
            pin_memory=self.config.pin_memory,
            prefetch_factor=self.config.prefetch_factor,
            collate_fn=self._collate_fn,
        )
    
    def _collate_fn(self, batch: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Custom collate function for batching"""
        
        images = torch.stack([item["image"] for item in batch])
        
        # Collect boxes and labels (variable size)
        boxes_list = [item["boxes"] for item in batch]
        labels_list = [item["labels"] for item in batch]
        
        # Build batch index for each box
        batch_indices = []
        for i, boxes in enumerate(boxes_list):
            batch_indices.extend([i] * len(boxes))
        
        return {
            "image": images,
            "boxes": torch.cat(boxes_list) if boxes_list else torch.zeros((0, 4)),
            "labels": torch.cat(labels_list) if labels_list else torch.zeros(0, dtype=torch.long),
            "batch_indices": torch.tensor(batch_indices, dtype=torch.long) if batch_indices else torch.zeros(0, dtype=torch.long),
            "image_ids": [item["image_id"] for item in batch],
            "orig_sizes": [item["orig_size"] for item in batch],
        }
    
    def get_class_distribution(self) -> Dict[int, int]:
        """Get class distribution"""
        return dict(self.class_counts)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get dataset statistics"""
        return {
            "split": self.split,
            "total_images": len(self.annotations),
            "total_sequences": len(self.sequences),
            "class_distribution": self.get_class_distribution(),
            "class_weights": [round(w, 3) for w in (self.config.class_weights or [])],
            "cache_size_mb": round(self._cache_size / (1024 * 1024), 1),
        }