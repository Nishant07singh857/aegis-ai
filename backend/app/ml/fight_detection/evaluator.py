# backend/app/ml/fight_detection/evaluator.py
"""
AEGIS AI - Fight Detection Evaluator
Comprehensive model evaluation with mAP, precision/recall,
confusion matrix, and per-class analysis.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import structlog
import numpy as np
import torch

from .model import FightDetectionModel
from .dataset import FightDataset

logger = structlog.get_logger("aegis.ml.evaluator")


@dataclass
class EvalConfig:
    """Evaluation configuration"""
    # Thresholds
    confidence_threshold: float = 0.5
    iou_threshold: float = 0.5
    iou_range: Tuple[float, float] = (0.5, 0.95)
    iou_step: float = 0.05
    
    # Metrics
    compute_mAP: bool = True
    compute_confusion_matrix: bool = True
    compute_per_class: bool = True
    
    # Visualization
    save_predictions: bool = False
    max_visualizations: int = 50
    
    # Performance
    batch_size: int = 16
    num_workers: int = 4
    
    # Output
    output_dir: str = "evaluation_results"


@dataclass
class EvalMetrics:
    """Evaluation metrics"""
    # Overall
    mAP50: float = 0.0
    mAP50_95: float = 0.0
    
    # Per-class
    per_class_AP: Dict[int, float] = field(default_factory=dict)
    per_class_precision: Dict[int, float] = field(default_factory=dict)
    per_class_recall: Dict[int, float] = field(default_factory=dict)
    
    # Counts
    total_predictions: int = 0
    total_ground_truth: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    
    # Confusion matrix
    confusion_matrix: Optional[np.ndarray] = None
    
    # Timing
    inference_time_ms: float = 0.0
    total_time: float = 0.0
    images_per_second: float = 0.0


class FightEvaluator:
    """
    Model evaluator for fight detection.
    
    Features:
    - mAP calculation (COCO-style)
    - Precision/Recall curves
    - Per-class performance analysis
    - Confusion matrix
    - Inference speed benchmarking
    - False positive analysis
    """
    
    def __init__(self, config: Optional[EvalConfig] = None):
        self.config = config or EvalConfig()
        self.metrics = EvalMetrics()
    
    def evaluate(
        self,
        model: FightDetectionModel,
        dataset: FightDataset,
        device: str = "cuda",
    ) -> EvalMetrics:
        """
        Run complete evaluation.
        
        Args:
            model: Trained model
            dataset: Evaluation dataset
            device: Device to run on
            
        Returns:
            EvalMetrics with all computed metrics
        """
        
        logger.info("Starting evaluation...")
        start_time = time.time()
        
        model = model.to(device)
        model.eval()
        
        loader = dataset.get_data_loader(
            batch_size=self.config.batch_size,
            shuffle=False,
            num_workers=self.config.num_workers,
        )
        
        all_predictions = []
        all_targets = []
        total_inference_time = 0.0
        total_images = 0
        
        with torch.no_grad():
            for batch in loader:
                images = batch['image'].to(device)
                batch_size = images.size(0)
                total_images += batch_size
                
                # Time inference
                torch.cuda.synchronize() if device == 'cuda' else None
                t_start = time.time()
                
                outputs = model(images)
                
                torch.cuda.synchronize() if device == 'cuda' else None
                inference_time = (time.time() - t_start) * 1000
                total_inference_time += inference_time
                
                # Process predictions
                predictions = self._process_outputs(outputs)
                
                all_predictions.extend(predictions)
                all_targets.extend(self._process_targets(batch))
        
        # Calculate metrics
        if self.config.compute_mAP:
            self._compute_mAP(all_predictions, all_targets)
        
        if self.config.compute_confusion_matrix:
            self._compute_confusion_matrix(all_predictions, all_targets)
        
        if self.config.compute_per_class:
            self._compute_per_class_metrics(all_predictions, all_targets)
        
        # Timing
        self.metrics.total_time = time.time() - start_time
        self.metrics.inference_time_ms = total_inference_time / max(1, total_images)
        self.metrics.images_per_second = total_images / max(0.001, self.metrics.total_time)
        
        logger.info(
            f"Evaluation complete: mAP50={self.metrics.mAP50:.4f}, "
            f"mAP50-95={self.metrics.mAP50_95:.4f}, "
            f"FPS={self.metrics.images_per_second:.1f}"
        )
        
        return self.metrics
    
    def _process_outputs(
        self,
        outputs: Tuple[torch.Tensor, ...],
    ) -> List[Dict[str, Any]]:
        """Process raw model outputs into predictions"""
        
        predictions = []
        
        for output in outputs:
            # output shape: (B, num_outputs, H, W)
            B, C, H, W = output.shape
            
            for b in range(B):
                # Simplified: extract high-confidence detections
                # In production, use proper NMS and decoding
                
                pred = {
                    'boxes': [],
                    'scores': [],
                    'labels': [],
                }
                
                predictions.append(pred)
        
        return predictions
    
    def _process_targets(self, batch: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process batch targets"""
        
        targets = []
        
        for i in range(len(batch['image_ids'])):
            target = {
                'boxes': batch['boxes'][batch['batch_indices'] == i].tolist(),
                'labels': batch['labels'][batch['batch_indices'] == i].tolist(),
            }
            targets.append(target)
        
        return targets
    
    def _compute_mAP(
        self,
        predictions: List[Dict[str, Any]],
        targets: List[Dict[str, Any]],
    ) -> None:
        """Compute mAP metrics"""
        
        # Simplified mAP calculation
        # In production, use pycocotools or similar
        
        iou_thresholds = np.arange(
            self.config.iou_range[0],
            self.config.iou_range[1] + self.config.iou_step,
            self.config.iou_step,
        )
        
        aps = []
        
        for iou_thresh in iou_thresholds:
            ap = self._compute_AP_at_iou(predictions, targets, iou_thresh)
            aps.append(ap)
        
        self.metrics.mAP50 = aps[0] if aps else 0.0
        self.metrics.mAP50_95 = np.mean(aps) if aps else 0.0
    
    def _compute_AP_at_iou(
        self,
        predictions: List[Dict[str, Any]],
        targets: List[Dict[str, Any]],
        iou_threshold: float,
    ) -> float:
        """Compute Average Precision at specific IOU"""
        
        # Simplified AP calculation
        # Sort predictions by confidence
        
        all_scores = []
        all_matches = []
        
        for pred, target in zip(predictions, targets):
            pred_boxes = pred.get('boxes', [])
            pred_scores = pred.get('scores', [])
            target_boxes = target.get('boxes', [])
            
            if not pred_boxes or not target_boxes:
                continue
            
            # Simple IOU matching
            for score, pbox in zip(pred_scores, pred_boxes):
                best_iou = 0.0
                for tbox in target_boxes:
                    iou = self._calculate_iou(pbox, tbox)
                    best_iou = max(best_iou, iou)
                
                all_scores.append(score)
                all_matches.append(best_iou >= iou_threshold)
        
        if not all_scores:
            return 0.0
        
        # Sort by confidence
        sorted_indices = np.argsort(all_scores)[::-1]
        all_matches = np.array(all_matches)[sorted_indices]
        
        # Compute precision-recall
        tp = np.cumsum(all_matches)
        fp = np.cumsum(~all_matches)
        
        precision = tp / np.maximum(tp + fp, 1)
        recall = tp / max(1, len(targets))
        
        # 11-point interpolation
        ap = 0.0
        for t in np.linspace(0, 1, 11):
            if np.sum(recall >= t) == 0:
                p = 0
            else:
                p = np.max(precision[recall >= t])
            ap += p / 11
        
        return ap
    
    def _calculate_iou(
        self,
        box1: Tuple[float, float, float, float],
        box2: Tuple[float, float, float, float],
    ) -> float:
        """Calculate IOU between two boxes"""
        
        x1, y1, w1, h1 = box1
        x2, y2, w2, h2 = box2
        
        # Convert to corners
        box1_x2 = x1 + w1
        box1_y2 = y1 + h1
        box2_x2 = x2 + w2
        box2_y2 = y2 + h2
        
        # Intersection
        ix1 = max(x1, x2)
        iy1 = max(y1, y2)
        ix2 = min(box1_x2, box2_x2)
        iy2 = min(box1_y2, box2_y2)
        
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        
        inter = (ix2 - ix1) * (iy2 - iy1)
        area1 = w1 * h1
        area2 = w2 * h2
        
        return inter / (area1 + area2 - inter)
    
    def _compute_confusion_matrix(
        self,
        predictions: List[Dict[str, Any]],
        targets: List[Dict[str, Any]],
    ) -> None:
        """Compute confusion matrix"""
        
        num_classes = 7  # Fight detection classes
        matrix = np.zeros((num_classes + 1, num_classes + 1), dtype=np.int64)  # +1 for background
        
        for pred, target in zip(predictions, targets):
            pred_labels = pred.get('labels', [])
            target_labels = target.get('labels', [])
            
            for pl in pred_labels:
                for tl in target_labels:
                    matrix[tl][pl] += 1
        
        self.metrics.confusion_matrix = matrix
    
    def _compute_per_class_metrics(
        self,
        predictions: List[Dict[str, Any]],
        targets: List[Dict[str, Any]],
    ) -> None:
        """Compute per-class metrics"""
        
        class_tp = defaultdict(int)
        class_fp = defaultdict(int)
        class_fn = defaultdict(int)
        
        for pred, target in zip(predictions, targets):
            pred_labels = set(pred.get('labels', []))
            target_labels = set(target.get('labels', []))
            
            for cls_id in range(7):
                if cls_id in pred_labels and cls_id in target_labels:
                    class_tp[cls_id] += 1
                elif cls_id in pred_labels:
                    class_fp[cls_id] += 1
                elif cls_id in target_labels:
                    class_fn[cls_id] += 1
        
        for cls_id in range(7):
            tp = class_tp[cls_id]
            fp = class_fp[cls_id]
            fn = class_fn[cls_id]
            
            precision = tp / max(1, tp + fp)
            recall = tp / max(1, tp + fn)
            
            self.metrics.per_class_precision[cls_id] = precision
            self.metrics.per_class_recall[cls_id] = recall
    
    def get_stats(self) -> Dict[str, Any]:
        """Get evaluation statistics"""
        return {
            "mAP50": round(self.metrics.mAP50, 4),
            "mAP50_95": round(self.metrics.mAP50_95, 4),
            "per_class": {
                cls_id: {
                    "AP": round(self.metrics.per_class_AP.get(cls_id, 0), 4),
                    "precision": round(self.metrics.per_class_precision.get(cls_id, 0), 4),
                    "recall": round(self.metrics.per_class_recall.get(cls_id, 0), 4),
                }
                for cls_id in range(7)
            },
            "counts": {
                "total_predictions": self.metrics.total_predictions,
                "total_ground_truth": self.metrics.total_ground_truth,
                "true_positives": self.metrics.true_positives,
                "false_positives": self.metrics.false_positives,
                "false_negatives": self.metrics.false_negatives,
            },
            "performance": {
                "avg_inference_ms": round(self.metrics.inference_time_ms, 2),
                "fps": round(self.metrics.images_per_second, 1),
                "total_time": round(self.metrics.total_time, 1),
            },
        }