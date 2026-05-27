# backend/app/vision/post_processors/bbox_utils.py
"""
AEGIS AI - Bounding Box Utilities
Comprehensive bounding box operations for vision processing.
IOU calculation, NMS, box merging, clipping, and transformations.
"""

from typing import Dict, List, Any, Tuple
from dataclasses import dataclass
import structlog
import numpy as np

logger = structlog.get_logger("aegis.vision.bbox")


@dataclass
class BBoxOperations:
    """Static bounding box utility operations"""
    
    @staticmethod
    def iou(box1: Tuple[float, float, float, float], box2: Tuple[float, float, float, float]) -> float:
        """
        Calculate Intersection over Union between two boxes.
        Boxes in format: (x, y, width, height)
        """
        x1, y1, w1, h1 = box1
        x2, y2, w2, h2 = box2
        
        # Convert to (x1, y1, x2, y2)
        box1_x2 = x1 + w1
        box1_y2 = y1 + h1
        box2_x2 = x2 + w2
        box2_y2 = y2 + h2
        
        # Intersection
        inter_x1 = max(x1, x2)
        inter_y1 = max(y1, y2)
        inter_x2 = min(box1_x2, box2_x2)
        inter_y2 = min(box1_y2, box2_y2)
        
        if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
            return 0.0
        
        inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        
        # Union
        area1 = w1 * h1
        area2 = w2 * h2
        union_area = area1 + area2 - inter_area
        
        return inter_area / union_area if union_area > 0 else 0.0
    
    @staticmethod
    def iou_xyxy(box1: Tuple[float, float, float, float], box2: Tuple[float, float, float, float]) -> float:
        """
        Calculate IOU with boxes in (x1, y1, x2, y2) format.
        """
        x1, y1, x2, y2 = box1
        x1_b, y1_b, x2_b, y2_b = box2
        
        inter_x1 = max(x1, x1_b)
        inter_y1 = max(y1, y1_b)
        inter_x2 = min(x2, x2_b)
        inter_y2 = min(y2, y2_b)
        
        if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
            return 0.0
        
        inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        
        area1 = (x2 - x1) * (y2 - y1)
        area2 = (x2_b - x1_b) * (y2_b - y1_b)
        union_area = area1 + area2 - inter_area
        
        return inter_area / union_area if union_area > 0 else 0.0
    
    @staticmethod
    def giou(box1: Tuple[float, float, float, float], box2: Tuple[float, float, float, float]) -> float:
        """
        Calculate Generalized IOU.
        """
        x1, y1, w1, h1 = box1
        x2, y2, w2, h2 = box2
        
        # IOU
        iou = BBoxOperations.iou(box1, box2)
        
        # Smallest enclosing box
        enc_x1 = min(x1, x2)
        enc_y1 = min(y1, y2)
        enc_x2 = max(x1 + w1, x2 + w2)
        enc_y2 = max(y1 + h1, y2 + h2)
        
        enc_area = (enc_x2 - enc_x1) * (enc_y2 - enc_y1)
        
        # Union area
        area1 = w1 * h1
        area2 = w2 * h2
        union_area = area1 + area2 - (iou * (area1 + area2) / (1 + iou) if iou > 0 else 0)
        
        if enc_area > 0:
            return iou - (enc_area - union_area) / enc_area
        
        return iou
    
    @staticmethod
    def nms(
        boxes: List[Tuple[float, float, float, float]],
        scores: List[float],
        iou_threshold: float = 0.5,
    ) -> List[int]:
        """
        Non-Maximum Suppression.
        Returns indices of kept boxes.
        """
        if not boxes:
            return []
        
        # Convert to numpy
        boxes_array = np.array(boxes)
        scores_array = np.array(scores)
        
        # Get coordinates
        x1 = boxes_array[:, 0]
        y1 = boxes_array[:, 1]
        x2 = boxes_array[:, 0] + boxes_array[:, 2]
        y2 = boxes_array[:, 1] + boxes_array[:, 3]
        
        areas = (x2 - x1) * (y2 - y1)
        
        # Sort by score
        order = scores_array.argsort()[::-1]
        
        keep = []
        
        while order.size > 0:
            i = order[0]
            keep.append(i)
            
            if order.size == 1:
                break
            
            # Compute IOU with remaining boxes
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            
            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter)
            
            # Keep boxes with IOU < threshold
            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]
        
        return keep
    
    @staticmethod
    def soft_nms(
        boxes: List[Tuple[float, float, float, float]],
        scores: List[float],
        iou_threshold: float = 0.5,
        sigma: float = 0.5,
    ) -> Tuple[List[int], List[float]]:
        """
        Soft-NMS: decays scores instead of removing boxes.
        """
        boxes_array = np.array(boxes)
        scores_array = np.array(scores, dtype=np.float64)
        
        x1 = boxes_array[:, 0]
        y1 = boxes_array[:, 1]
        x2 = boxes_array[:, 0] + boxes_array[:, 2]
        y2 = boxes_array[:, 1] + boxes_array[:, 3]
        
        areas = (x2 - x1) * (y2 - y1)
        
        order = scores_array.argsort()[::-1]
        keep = []
        
        while order.size > 0:
            i = order[0]
            keep.append(i)
            
            if order.size == 1:
                break
            
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            
            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter)
            
            # Decay scores
            decay = np.exp(-(iou * iou) / sigma)
            scores_array[order[1:]] *= decay
            
            # Keep boxes with score > threshold
            inds = np.where(scores_array[order[1:]] > 0.001)[0]
            order = order[inds + 1]
            
            # Re-sort
            order = order[np.argsort(scores_array[order])[::-1]]
        
        return keep, [scores_array[i] for i in keep]
    
    @staticmethod
    def merge_boxes(
        boxes: List[Tuple[float, float, float, float]],
        iou_threshold: float = 0.3,
    ) -> List[Tuple[float, float, float, float]]:
        """Merge overlapping boxes"""
        if not boxes:
            return []
        
        merged = []
        used = set()
        
        for i, box1 in enumerate(boxes):
            if i in used:
                continue
            
            current_box = list(box1)
            
            for j, box2 in enumerate(boxes):
                if j <= i or j in used:
                    continue
                
                if BBoxOperations.iou(box1, box2) > iou_threshold:
                    # Merge: take union of boxes
                    x1, y1, w1, h1 = current_box
                    x2, y2, w2, h2 = box2
                    
                    new_x = min(x1, x2)
                    new_y = min(y1, y2)
                    new_w = max(x1 + w1, x2 + w2) - new_x
                    new_h = max(y1 + h1, y2 + h2) - new_y
                    
                    current_box = [new_x, new_y, new_w, new_h]
                    used.add(j)
            
            merged.append(tuple(current_box))
            used.add(i)
        
        return merged
    
    @staticmethod
    def clip_box(
        box: Tuple[float, float, float, float],
        width: int,
        height: int,
    ) -> Tuple[float, float, float, float]:
        """Clip box to image boundaries"""
        x, y, w, h = box
        
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = max(1, min(w, width - x))
        h = max(1, min(h, height - y))
        
        return (x, y, w, h)
    
    @staticmethod
    def scale_box(
        box: Tuple[float, float, float, float],
        scale_x: float,
        scale_y: float,
    ) -> Tuple[float, float, float, float]:
        """Scale box by factors"""
        x, y, w, h = box
        return (x * scale_x, y * scale_y, w * scale_x, h * scale_y)
    
    @staticmethod
    def box_area(box: Tuple[float, float, float, float]) -> float:
        """Calculate box area"""
        return box[2] * box[3]
    
    @staticmethod
    def box_center(box: Tuple[float, float, float, float]) -> Tuple[float, float]:
        """Get box center"""
        return (box[0] + box[2] / 2, box[1] + box[3] / 2)
    
    @staticmethod
    def box_aspect_ratio(box: Tuple[float, float, float, float]) -> float:
        """Get box aspect ratio (width/height)"""
        return box[2] / max(1, box[3])
    
    @staticmethod
    def distance_between_boxes(
        box1: Tuple[float, float, float, float],
        box2: Tuple[float, float, float, float],
    ) -> float:
        """Calculate center-to-center distance between boxes"""
        c1 = BBoxOperations.box_center(box1)
        c2 = BBoxOperations.box_center(box2)
        return np.sqrt((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2)
    
    @staticmethod
    def xywh_to_xyxy(box: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        """Convert (x, y, w, h) to (x1, y1, x2, y2)"""
        return (box[0], box[1], box[0] + box[2], box[1] + box[3])
    
    @staticmethod
    def xyxy_to_xywh(box: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        """Convert (x1, y1, x2, y2) to (x, y, w, h)"""
        return (box[0], box[1], box[2] - box[0], box[3] - box[1])


class BBoxUtils:
    """
    High-level bounding box utilities for vision processing.
    Wraps BBoxOperations with tracking and statistics.
    """
    
    def __init__(self):
        self._total_operations = 0
        self._log = logger.bind(component="bbox_utils")
    
    def filter_by_size(
        self,
        boxes: List[Tuple[float, float, float, float]],
        min_area: float = 0,
        max_area: float = float('inf'),
        min_width: float = 0,
        min_height: float = 0,
    ) -> List[int]:
        """Filter boxes by size constraints. Returns indices."""
        
        keep = []
        
        for i, box in enumerate(boxes):
            x, y, w, h = box
            area = w * h
            
            if area < min_area or area > max_area:
                continue
            if w < min_width or h < min_height:
                continue
            
            keep.append(i)
        
        return keep
    
    def filter_by_aspect_ratio(
        self,
        boxes: List[Tuple[float, float, float, float]],
        min_ratio: float = 0.0,
        max_ratio: float = float('inf'),
    ) -> List[int]:
        """Filter boxes by aspect ratio. Returns indices."""
        
        keep = []
        
        for i, box in enumerate(boxes):
            ratio = BBoxOperations.box_aspect_ratio(box)
            
            if ratio < min_ratio or ratio > max_ratio:
                continue
            
            keep.append(i)
        
        return keep
    
    def filter_by_confidence(
        self,
        boxes: List[Tuple[float, float, float, float]],
        confidences: List[float],
        threshold: float = 0.5,
    ) -> Tuple[List[Tuple[float, float, float, float]], List[float]]:
        """Filter boxes by confidence threshold"""
        
        filtered_boxes = []
        filtered_confs = []
        
        for box, conf in zip(boxes, confidences):
            if conf >= threshold:
                filtered_boxes.append(box)
                filtered_confs.append(conf)
        
        return filtered_boxes, filtered_confs
    
    def cluster_boxes(
        self,
        boxes: List[Tuple[float, float, float, float]],
        distance_threshold: float = 50.0,
    ) -> List[List[int]]:
        """Cluster boxes by proximity. Returns list of index clusters."""
        
        if not boxes:
            return []
        
        n = len(boxes)
        visited = [False] * n
        clusters = []
        
        for i in range(n):
            if visited[i]:
                continue
            
            cluster = [i]
            visited[i] = True
            
            # BFS to find all connected boxes
            queue = [i]
            while queue:
                current = queue.pop(0)
                
                for j in range(n):
                    if visited[j]:
                        continue
                    
                    dist = BBoxOperations.distance_between_boxes(
                        boxes[current], boxes[j]
                    )
                    
                    if dist < distance_threshold:
                        cluster.append(j)
                        visited[j] = True
                        queue.append(j)
            
            clusters.append(cluster)
        
        return clusters
    
    def get_stats(self) -> Dict[str, Any]:
        """Get bounding box utility statistics"""
        return {
            "total_operations": self._total_operations,
        }
    
    def reset(self) -> None:
        """Reset statistics"""
        self._total_operations = 0