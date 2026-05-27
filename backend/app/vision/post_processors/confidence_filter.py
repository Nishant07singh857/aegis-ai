# backend/app/vision/post_processors/confidence_filter.py
"""
AEGIS AI - Confidence Filter
Adaptive confidence filtering for vision detections.
Filters low-confidence results and adapts thresholds dynamically.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.vision.confidence")


class FilterMode(str, Enum):
    """Confidence filter modes"""
    STRICT = "strict"       # High precision, lower recall
    BALANCED = "balanced"   # Equal weight
    RELAXED = "relaxed"     # High recall, lower precision
    ADAPTIVE = "adaptive"   # Auto-adjusts based on conditions
    PER_FEED = "per_feed"   # Different thresholds per feed


@dataclass
class FilterConfig:
    """Confidence filter configuration"""
    mode: FilterMode = FilterMode.ADAPTIVE
    
    # Base thresholds
    min_confidence: float = 0.5
    min_detection_confidence: float = 0.4
    min_ocr_confidence: float = 0.6
    min_tracking_confidence: float = 0.3
    
    # Adaptive parameters
    adaptive_window: int = 50  # Recent detections to analyze
    target_precision: float = 0.85  # Target precision rate
    min_accept_rate: float = 0.3  # Minimum acceptance rate
    
    # Feed-specific overrides
    feed_overrides: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    # Temporal filtering
    temporal_window: float = 0.5  # Seconds for temporal smoothing
    require_consecutive: int = 2  # Consecutive detections required
    
    # Category-specific thresholds
    category_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "kill_feed": 0.7,
        "hp_bar": 0.5,
        "minimap": 0.6,
        "weapon": 0.5,
        "utility": 0.55,
        "vehicle": 0.5,
        "player": 0.4,
        "zone": 0.6,
        "loot": 0.45,
    })


@dataclass
class FilterResult:
    """Result of confidence filtering"""
    accepted: bool
    confidence: float
    adjusted_threshold: float
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class ConfidenceFilter:
    """
    Adaptive confidence filtering for vision detections.
    
    Features:
    - Dynamic threshold adjustment
    - Per-category thresholds
    - Feed-specific overrides
    - Temporal consistency filtering
    - Precision/recall optimization
    - Performance tracking
    """
    
    def __init__(self, config: Optional[FilterConfig] = None):
        self.config = config or FilterConfig()
        
        # Detection history for adaptive mode
        self.detection_history: deque = deque(maxlen=self.config.adaptive_window)
        
        # Per-category statistics
        self.category_stats: Dict[str, Dict[str, Any]] = {}
        
        # Per-feed statistics
        self.feed_stats: Dict[str, Dict[str, Any]] = {}
        
        # Temporal buffer for consecutive detection
        self.temporal_buffer: Dict[str, deque] = {}
        
        # Current adaptive thresholds
        self.adaptive_thresholds: Dict[str, float] = {}
        
        # Performance tracking
        self._total_evaluated = 0
        self._total_accepted = 0
        self._total_rejected = 0
        
        self._log = logger.bind(component="confidence_filter")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FILTERING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def filter(
        self,
        confidence: float,
        category: str = "default",
        feed_id: str = "",
        detection_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FilterResult:
        """
        Filter a detection by confidence.
        
        Args:
            confidence: Detection confidence (0-1)
            category: Detection category
            feed_id: Source feed identifier
            detection_id: Unique detection ID for temporal tracking
            metadata: Additional detection metadata
            
        Returns:
            FilterResult with acceptance decision
        """
        
        self._total_evaluated += 1
        
        # Get threshold
        threshold = self._get_threshold(category, feed_id)
        
        # Check temporal consistency
        temporal_boost = self._check_temporal(detection_id, confidence, category)
        
        # Adjust confidence with temporal boost
        adjusted_confidence = min(1.0, confidence + temporal_boost)
        
        # Make decision
        if adjusted_confidence >= threshold:
            self._total_accepted += 1
            result = FilterResult(
                accepted=True,
                confidence=adjusted_confidence,
                adjusted_threshold=threshold,
                reason=f"Confidence {adjusted_confidence:.3f} >= threshold {threshold:.3f}",
                metadata=metadata or {},
            )
        else:
            self._total_rejected += 1
            result = FilterResult(
                accepted=False,
                confidence=adjusted_confidence,
                adjusted_threshold=threshold,
                reason=f"Confidence {adjusted_confidence:.3f} < threshold {threshold:.3f}",
                metadata=metadata or {},
            )
        
        # Update history for adaptive mode
        self.detection_history.append({
            "confidence": confidence,
            "category": category,
            "feed_id": feed_id,
            "accepted": result.accepted,
            "timestamp": time.time(),
        })
        
        # Update adaptive thresholds
        if self.config.mode == FilterMode.ADAPTIVE:
            self._update_adaptive_threshold(category)
        
        return result
    
    def filter_batch(
        self,
        detections: List[Dict[str, Any]],
        category: str = "default",
        feed_id: str = "",
    ) -> Tuple[List[Dict[str, Any]], List[FilterResult]]:
        """Filter a batch of detections"""
        
        accepted = []
        results = []
        
        for det in detections:
            confidence = det.get("confidence", det.get("conf", 0.5))
            det_id = det.get("id", det.get("detection_id", ""))
            
            result = self.filter(
                confidence=confidence,
                category=category,
                feed_id=feed_id,
                detection_id=det_id,
                metadata=det.get("metadata", {}),
            )
            
            results.append(result)
            
            if result.accepted:
                accepted.append(det)
        
        return accepted, results
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # THRESHOLD MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _get_threshold(self, category: str, feed_id: str) -> float:
        """Get the appropriate threshold for a detection"""
        
        # Check feed-specific override
        if feed_id and feed_id in self.config.feed_overrides:
            override = self.config.feed_overrides[feed_id]
            if category in override:
                return override[category]
            if "default" in override:
                return override["default"]
        
        # Check adaptive threshold
        if self.config.mode == FilterMode.ADAPTIVE and category in self.adaptive_thresholds:
            return self.adaptive_thresholds[category]
        
        # Check category-specific threshold
        if category in self.config.category_thresholds:
            return self.config.category_thresholds[category]
        
        # Mode-based defaults
        mode_defaults = {
            FilterMode.STRICT: 0.7,
            FilterMode.BALANCED: 0.5,
            FilterMode.RELAXED: 0.3,
            FilterMode.ADAPTIVE: 0.5,
            FilterMode.PER_FEED: 0.5,
        }
        
        return mode_defaults.get(self.config.mode, self.config.min_confidence)
    
    def set_threshold(self, category: str, threshold: float) -> None:
        """Set threshold for a category"""
        self.config.category_thresholds[category] = threshold
    
    def set_feed_override(self, feed_id: str, category: str, threshold: float) -> None:
        """Set feed-specific override"""
        if feed_id not in self.config.feed_overrides:
            self.config.feed_overrides[feed_id] = {}
        self.config.feed_overrides[feed_id][category] = threshold
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TEMPORAL FILTERING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _check_temporal(self, detection_id: str, confidence: float, category: str) -> float:
        """
        Check temporal consistency.
        Boosts confidence for consistently detected objects.
        """
        
        if not detection_id:
            return 0.0
        
        if detection_id not in self.temporal_buffer:
            self.temporal_buffer[detection_id] = deque(maxlen=self.config.require_consecutive + 2)
        
        buffer = self.temporal_buffer[detection_id]
        buffer.append({
            "confidence": confidence,
            "timestamp": time.time(),
        })
        
        # Clean old entries
        cutoff = time.time() - self.config.temporal_window
        while buffer and buffer[0]["timestamp"] < cutoff:
            buffer.popleft()
        
        # Calculate temporal boost
        if len(buffer) >= self.config.require_consecutive:
            # Consistent detections get a boost
            avg_conf = sum(b["confidence"] for b in buffer) / len(buffer)
            
            if avg_conf > 0.5:
                return 0.1  # Boost for consistent detections
            elif avg_conf > 0.3:
                return 0.05
        
        return 0.0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ADAPTIVE MODE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _update_adaptive_threshold(self, category: str) -> None:
        """Update adaptive threshold based on recent performance"""
        
        # Get recent detections for this category
        recent = [
            d for d in self.detection_history
            if d["category"] == category
        ][-self.config.adaptive_window:]
        
        if len(recent) < 10:
            return
        
        # Calculate current acceptance rate
        accepted = sum(1 for d in recent if d["accepted"])
        acceptance_rate = accepted / len(recent)
        
        current_threshold = self.adaptive_thresholds.get(
            category,
            self.config.category_thresholds.get(category, 0.5),
        )
        
        # Adjust threshold to target acceptance rate
        target_rate = 1.0 - self.config.target_precision
        
        if acceptance_rate < self.config.min_accept_rate:
            # Too few accepted - lower threshold
            new_threshold = current_threshold - 0.05
        elif acceptance_rate > target_rate * 1.5:
            # Too many accepted - raise threshold
            new_threshold = current_threshold + 0.05
        else:
            # Within acceptable range
            if acceptance_rate > target_rate:
                new_threshold = current_threshold + 0.02
            else:
                new_threshold = current_threshold - 0.02
        
        # Clamp
        new_threshold = max(0.2, min(0.9, new_threshold))
        
        if abs(new_threshold - current_threshold) > 0.01:
            self.adaptive_thresholds[category] = new_threshold
            self._log.debug(
                f"Adaptive threshold for {category}: {current_threshold:.3f} → {new_threshold:.3f} "
                f"(acceptance: {acceptance_rate:.2f})"
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_acceptance_rate(self) -> float:
        """Get overall acceptance rate"""
        if self._total_evaluated == 0:
            return 0.0
        return self._total_accepted / self._total_evaluated
    
    def get_thresholds(self) -> Dict[str, float]:
        """Get all current thresholds"""
        thresholds = dict(self.config.category_thresholds)
        thresholds.update(self.adaptive_thresholds)
        return thresholds
    
    def get_stats(self) -> Dict[str, Any]:
        """Get confidence filter statistics"""
        return {
            "total_evaluated": self._total_evaluated,
            "total_accepted": self._total_accepted,
            "total_rejected": self._total_rejected,
            "acceptance_rate": round(self.get_acceptance_rate(), 3),
            "mode": self.config.mode.value,
            "thresholds": self.get_thresholds(),
            "adaptive_thresholds": self.adaptive_thresholds,
            "feed_overrides": {
                feed: overrides
                for feed, overrides in self.config.feed_overrides.items()
            },
        }
    
    def reset(self) -> None:
        """Reset confidence filter"""
        self.detection_history.clear()
        self.temporal_buffer.clear()
        self.adaptive_thresholds.clear()
        self._total_evaluated = 0
        self._total_accepted = 0
        self._total_rejected = 0
        self._log.info("Confidence filter reset")