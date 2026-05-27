# backend/app/core/monitoring.py
"""
AEGIS AI - Monitoring & Metrics
Prometheus metrics, health checks, performance monitoring
"""

import time
import psutil
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import structlog


logger = structlog.get_logger("aegis.monitoring")

@dataclass
class MetricPoint:
    """Single metric data point"""
    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    tags: Dict[str, str] = field(default_factory=dict)

class MetricsManager:
    """Central metrics collection and monitoring"""
    
    def __init__(self):
        self._metrics: Dict[str, list] = defaultdict(list)
        self._gauges: Dict[str, float] = {}
        self._counters: Dict[str, int] = defaultdict(int)
        self._histograms: Dict[str, list] = defaultdict(list)
        self._start_time = time.time()
        
        # Performance tracking
        self._request_times: Dict[str, list] = defaultdict(list)
        self._error_counts: Dict[str, int] = defaultdict(int)
        
        logger.info("Metrics manager initialized")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # METRIC COLLECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Record a metric value"""
        self._metrics[name].append(
            MetricPoint(name=name, value=value, tags=tags or {})
        )
        
        # Keep last 1000 points
        if len(self._metrics[name]) > 1000:
            self._metrics[name] = self._metrics[name][-1000:]
    
    def set_gauge(self, name: str, value: float):
        """Set a gauge metric"""
        self._gauges[name] = value
    
    def increment_counter(self, name: str, amount: int = 1):
        """Increment a counter"""
        self._counters[name] += amount
    
    def record_timing(self, name: str, duration_ms: float):
        """Record a timing metric"""
        self._histograms[name].append(duration_ms)
        
        # Keep last 500 timings
        if len(self._histograms[name]) > 500:
            self._histograms[name] = self._histograms[name][-500:]
    
    def record_request(self, endpoint: str, duration_ms: float, status_code: int):
        """Record an API request"""
        self._request_times[endpoint].append({
            "duration_ms": duration_ms,
            "status_code": status_code,
            "timestamp": time.time(),
        })
        
        # Keep last 100 requests per endpoint
        if len(self._request_times[endpoint]) > 100:
            self._request_times[endpoint] = self._request_times[endpoint][-100:]
        
        if status_code >= 400:
            self._error_counts[endpoint] += 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SYSTEM METRICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_system_metrics(self) -> Dict[str, Any]:
        """Get current system metrics"""
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        process = psutil.Process()
        
        return {
            "cpu_percent": cpu_percent,
            "cpu_count": psutil.cpu_count(),
            "memory_total_mb": memory.total / 1024 / 1024,
            "memory_used_mb": memory.used / 1024 / 1024,
            "memory_percent": memory.percent,
            "disk_total_gb": disk.total / 1024 / 1024 / 1024,
            "disk_used_gb": disk.used / 1024 / 1024 / 1024,
            "disk_percent": disk.percent,
            "process_memory_mb": process.memory_info().rss / 1024 / 1024,
            "process_cpu_percent": process.cpu_percent(),
            "process_threads": process.num_threads(),
            "open_files": len(process.open_files()),
            "uptime_seconds": time.time() - self._start_time,
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # METRIC QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_metric_summary(self, name: str) -> Optional[Dict[str, Any]]:
        """Get summary statistics for a metric"""
        points = self._metrics.get(name, [])
        if not points:
            return None
        
        values = [p.value for p in points]
        
        return {
            "name": name,
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "latest": values[-1],
        }
    
    def get_counter(self, name: str) -> int:
        """Get counter value"""
        return self._counters.get(name, 0)
    
    def get_gauge(self, name: str) -> Optional[float]:
        """Get gauge value"""
        return self._gauges.get(name)
    
    def get_histogram_stats(self, name: str) -> Optional[Dict[str, Any]]:
        """Get histogram statistics"""
        values = self._histograms.get(name, [])
        if not values:
            return None
        
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        return {
            "count": n,
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "avg": sum(values) / n,
            "p50": sorted_values[int(n * 0.5)],
            "p90": sorted_values[int(n * 0.9)],
            "p95": sorted_values[int(n * 0.95)],
            "p99": sorted_values[int(n * 0.99)],
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEALTH CHECK
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get overall health status"""
        system = self.get_system_metrics()
        
        # Determine health
        is_healthy = True
        warnings = []
        
        if system["memory_percent"] > 90:
            is_healthy = False
            warnings.append("High memory usage")
        elif system["memory_percent"] > 80:
            warnings.append("Elevated memory usage")
        
        if system["cpu_percent"] > 90:
            is_healthy = False
            warnings.append("High CPU usage")
        
        if system["disk_percent"] > 90:
            is_healthy = False
            warnings.append("Low disk space")
        
        return {
            "status": "healthy" if is_healthy else "degraded",
            "warnings": warnings,
            "system": system,
            "uptime_seconds": system["uptime_seconds"],
            "uptime_formatted": self._format_uptime(system["uptime_seconds"]),
            "timestamp": time.time(),
        }
    
    def _format_uptime(self, seconds: float) -> str:
        """Format uptime seconds to human readable"""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        
        return " ".join(parts)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PROMETHEUS FORMAT (for scraping)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_prometheus_metrics(self) -> str:
        """Export metrics in Prometheus format"""
        lines = []
        
        # System metrics
        system = self.get_system_metrics()
        lines.append("# HELP aegis_cpu_percent CPU usage percentage")
        lines.append("# TYPE aegis_cpu_percent gauge")
        lines.append(f"aegis_cpu_percent {system['cpu_percent']}")
        
        lines.append("# HELP aegis_memory_percent Memory usage percentage")
        lines.append("# TYPE aegis_memory_percent gauge")
        lines.append(f"aegis_memory_percent {system['memory_percent']}")
        
        lines.append("# HELP aegis_uptime_seconds Process uptime in seconds")
        lines.append("# TYPE aegis_uptime_seconds counter")
        lines.append(f"aegis_uptime_seconds {system['uptime_seconds']}")
        
        # Counters
        for name, value in self._counters.items():
            safe_name = name.replace("-", "_").replace(" ", "_")
            lines.append(f"# HELP aegis_{safe_name} Counter for {name}")
            lines.append(f"# TYPE aegis_{safe_name} counter")
            lines.append(f"aegis_{safe_name} {value}")
        
        # Gauges
        for name, value in self._gauges.items():
            safe_name = name.replace("-", "_").replace(" ", "_")
            lines.append(f"# HELP aegis_{safe_name} Gauge for {name}")
            lines.append(f"# TYPE aegis_{safe_name} gauge")
            lines.append(f"aegis_{safe_name} {value}")
        
        return "\n".join(lines) + "\n"
    
    def reset(self):
        """Reset all metrics"""
        self._metrics.clear()
        self._gauges.clear()
        self._counters.clear()
        self._histograms.clear()
        self._request_times.clear()
        self._error_counts.clear()
        logger.info("All metrics reset")


# Global metrics instance
metrics_manager = MetricsManager()