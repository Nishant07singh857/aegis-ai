# backend/app/vision/capture/latency_sync.py
"""
AEGIS AI - Latency Sync Tracker (Phase 22 / W3, M1+M2).

The PRD asks for multi-feed latency sync within ±500 ms. This module is
the measurement framework for that claim — it does NOT fake sync:

* Every captured frame is stamped at grab time on the server
  (``capture_ts``, ``time.time()``). When a source can supply a genuine
  on-device capture timestamp it may pass ``device_ts`` as well; the
  observer phones in this build cannot, so ``device_ts`` is normally
  ``None`` and offsets are *grab-time* offsets, labelled as such.
* For each feed we track the offset of its latest frame's grab time
  against the fleet median grab time. A feed whose frames consistently
  arrive later than the fleet is drifting/lagging by that offset.
* ``alignment_report()`` exposes, per feed: last capture timestamp,
  offset vs fleet median (ms), a rolling drift estimate (ms, from the
  slope of recent offsets), sample count, and ``within_tolerance``
  against the ±500 ms PRD window.

Nothing here delays or re-times frames — alignment *correction* (a
jitter buffer) only makes sense once on-device timestamps exist. What
exists today is honest measurement + reporting, which is the
prerequisite the gap audit asked for.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

#: PRD latency-sync tolerance window, milliseconds (±500 ms).
SYNC_TOLERANCE_MS: float = 500.0

_MAX_SAMPLES = 120


@dataclass
class FeedSyncState:
    """Per-feed sync bookkeeping."""

    source_id: str
    last_capture_ts: Optional[float] = None
    last_device_ts: Optional[float] = None
    samples: int = 0
    # Recent (capture_ts, offset_ms) pairs for drift estimation.
    offset_history: Deque = field(default_factory=lambda: deque(maxlen=_MAX_SAMPLES))


class LatencySyncTracker:
    """Tracks per-feed capture-time offsets across a fleet of feeds."""

    def __init__(self, tolerance_ms: float = SYNC_TOLERANCE_MS) -> None:
        self.tolerance_ms = float(tolerance_ms)
        self._feeds: Dict[str, FeedSyncState] = {}

    # ── recording ────────────────────────────────────────────────

    def register_feed(self, source_id: str) -> None:
        self._feeds.setdefault(source_id, FeedSyncState(source_id=source_id))

    def unregister_feed(self, source_id: str) -> None:
        self._feeds.pop(source_id, None)

    def record_frame(
        self,
        source_id: str,
        capture_ts: Optional[float] = None,
        device_ts: Optional[float] = None,
    ) -> None:
        """Stamp one captured frame for ``source_id``.

        ``capture_ts`` defaults to now (server grab time). The offset is
        computed against the current fleet median of last-capture times
        *including* this frame, so a lone feed reports offset 0.
        """
        ts = float(capture_ts) if capture_ts is not None else time.time()
        state = self._feeds.setdefault(source_id, FeedSyncState(source_id=source_id))
        state.last_capture_ts = ts
        state.last_device_ts = device_ts
        state.samples += 1
        median = self._fleet_median_ts()
        if median is not None:
            offset_ms = (ts - median) * 1000.0
            state.offset_history.append((ts, offset_ms))

    # ── reporting ────────────────────────────────────────────────

    def _fleet_median_ts(self) -> Optional[float]:
        stamps = sorted(
            s.last_capture_ts for s in self._feeds.values() if s.last_capture_ts
        )
        if not stamps:
            return None
        mid = len(stamps) // 2
        if len(stamps) % 2:
            return stamps[mid]
        return (stamps[mid - 1] + stamps[mid]) / 2.0

    @staticmethod
    def _drift_ms_per_min(state: FeedSyncState) -> Optional[float]:
        """Least-squares slope of recent offsets, ms per minute.

        ``None`` with fewer than 3 samples or a degenerate time span —
        drift is never invented from insufficient data.
        """
        pts: List = list(state.offset_history)
        if len(pts) < 3:
            return None
        t0 = pts[0][0]
        xs = [p[0] - t0 for p in pts]
        ys = [p[1] for p in pts]
        span = xs[-1] - xs[0]
        if span <= 0:
            return None
        n = len(pts)
        mx = sum(xs) / n
        my = sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        if denom <= 0:
            return None
        slope_per_s = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
        return round(slope_per_s * 60.0, 2)

    def feed_report(self, source_id: str) -> Optional[Dict[str, Any]]:
        state = self._feeds.get(source_id)
        if state is None:
            return None
        median = self._fleet_median_ts()
        offset_ms: Optional[float] = None
        if state.last_capture_ts is not None and median is not None:
            offset_ms = round((state.last_capture_ts - median) * 1000.0, 1)
        return {
            "source_id": source_id,
            "last_capture_ts": state.last_capture_ts,
            "device_ts": state.last_device_ts,
            # Offsets are grab-time offsets unless a device_ts was given.
            "offset_basis": "device_ts" if state.last_device_ts else "server_grab_time",
            "offset_ms": offset_ms,
            "drift_ms_per_min": self._drift_ms_per_min(state),
            "samples": state.samples,
            "within_tolerance": (
                abs(offset_ms) <= self.tolerance_ms if offset_ms is not None else None
            ),
            "tolerance_ms": self.tolerance_ms,
        }

    def alignment_report(self) -> Dict[str, Any]:
        """Fleet-wide alignment report (the ±500 ms framework surface)."""
        feeds = {
            sid: self.feed_report(sid) for sid in sorted(self._feeds)
        }
        measured = [f for f in feeds.values() if f and f["offset_ms"] is not None]
        max_abs = max((abs(f["offset_ms"]) for f in measured), default=None)
        return {
            "tolerance_ms": self.tolerance_ms,
            "feeds_measured": len(measured),
            "max_abs_offset_ms": max_abs,
            "all_within_tolerance": (
                all(f["within_tolerance"] for f in measured) if measured else None
            ),
            "feeds": feeds,
        }

    def reset(self) -> None:
        self._feeds.clear()
