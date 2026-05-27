# backend/app/utils/math_utils.py
"""
AEGIS AI - Mathematical Utilities
Common math operations, statistics, interpolation, and signal processing.
"""

import math
from typing import List, Tuple
import structlog
import numpy as np

logger = structlog.get_logger("aegis.utils.math")


class MathUtils:
    """Mathematical utility functions"""
    
    @staticmethod
    def clamp(value: float, min_val: float, max_val: float) -> float:
        """Clamp value to range"""
        return max(min_val, min(max_val, value))
    
    @staticmethod
    def lerp(a: float, b: float, t: float) -> float:
        """Linear interpolation"""
        return a + (b - a) * t
    
    @staticmethod
    def smooth_step(edge0: float, edge1: float, x: float) -> float:
        """Smooth step interpolation"""
        t = MathUtils.clamp((x - edge0) / (edge1 - edge0), 0, 1)
        return t * t * (3 - 2 * t)
    
    @staticmethod
    def distance_2d(x1: float, y1: float, x2: float, y2: float) -> float:
        """2D Euclidean distance"""
        return math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
    
    @staticmethod
    def distance_3d(x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> float:
        """3D Euclidean distance"""
        return math.sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)
    
    @staticmethod
    def angle_between(x1: float, y1: float, x2: float, y2: float) -> float:
        """Angle between two points in degrees"""
        return math.degrees(math.atan2(y2 - y1, x2 - x1))
    
    @staticmethod
    def normalize_angle(angle: float) -> float:
        """Normalize angle to [0, 360)"""
        return angle % 360
    
    @staticmethod
    def angle_difference(a: float, b: float) -> float:
        """Smallest difference between two angles"""
        diff = (b - a) % 360
        if diff > 180:
            diff -= 360
        return diff
    
    @staticmethod
    def moving_average(data: List[float], window: int) -> List[float]:
        """Calculate moving average"""
        if len(data) < window:
            return [sum(data) / len(data)] * len(data)
        
        result = []
        for i in range(len(data)):
            start = max(0, i - window + 1)
            window_data = data[start:i+1]
            result.append(sum(window_data) / len(window_data))
        
        return result
    
    @staticmethod
    def exponential_moving_average(data: List[float], alpha: float = 0.3) -> List[float]:
        """Calculate exponential moving average"""
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(alpha * data[i] + (1 - alpha) * result[-1])
        return result
    
    @staticmethod
    def sigmoid(x: float) -> float:
        """Sigmoid function"""
        return 1 / (1 + math.exp(-x))
    
    @staticmethod
    def softmax(x: np.ndarray) -> np.ndarray:
        """Softmax function"""
        exp_x = np.exp(x - np.max(x))
        return exp_x / exp_x.sum()
    
    @staticmethod
    def iou_2d(box1: Tuple[float, float, float, float], box2: Tuple[float, float, float, float]) -> float:
        """Calculate 2D Intersection over Union"""
        x1, y1, w1, h1 = box1
        x2, y2, w2, h2 = box2
        
        xi1 = max(x1, x2)
        yi1 = max(y1, y2)
        xi2 = min(x1 + w1, x2 + w2)
        yi2 = min(y1 + h1, y2 + h2)
        
        if xi2 <= xi1 or yi2 <= yi1:
            return 0.0
        
        inter = (xi2 - xi1) * (yi2 - yi1)
        area1 = w1 * h1
        area2 = w2 * h2
        
        return inter / (area1 + area2 - inter)


class StatisticsUtils:
    """Statistical utility functions"""
    
    @staticmethod
    def mean(data: List[float]) -> float:
        """Calculate mean"""
        return sum(data) / len(data) if data else 0.0
    
    @staticmethod
    def median(data: List[float]) -> float:
        """Calculate median"""
        sorted_data = sorted(data)
        n = len(sorted_data)
        if n == 0:
            return 0.0
        if n % 2 == 0:
            return (sorted_data[n//2 - 1] + sorted_data[n//2]) / 2
        return sorted_data[n//2]
    
    @staticmethod
    def variance(data: List[float]) -> float:
        """Calculate variance"""
        if len(data) < 2:
            return 0.0
        m = StatisticsUtils.mean(data)
        return sum((x - m)**2 for x in data) / (len(data) - 1)
    
    @staticmethod
    def std_dev(data: List[float]) -> float:
        """Calculate standard deviation"""
        return math.sqrt(StatisticsUtils.variance(data))
    
    @staticmethod
    def percentile(data: List[float], p: float) -> float:
        """Calculate percentile"""
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * p / 100
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        return sorted_data[int(f)] * (c - k) + sorted_data[int(c)] * (k - f)
    
    @staticmethod
    def z_score(value: float, mean: float, std: float) -> float:
        """Calculate z-score"""
        if std == 0:
            return 0.0
        return (value - mean) / std
    
    @staticmethod
    def correlation(x: List[float], y: List[float]) -> float:
        """Calculate Pearson correlation"""
        n = len(x)
        if n < 2 or n != len(y):
            return 0.0
        
        mean_x = StatisticsUtils.mean(x)
        mean_y = StatisticsUtils.mean(y)
        
        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        std_x = math.sqrt(sum((xi - mean_x)**2 for xi in x))
        std_y = math.sqrt(sum((yi - mean_y)**2 for yi in y))
        
        if std_x == 0 or std_y == 0:
            return 0.0
        
        return cov / (std_x * std_y)