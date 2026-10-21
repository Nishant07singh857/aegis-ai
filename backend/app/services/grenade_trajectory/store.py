# backend/app/services/grenade_trajectory/store.py
"""In-memory store for estimated grenade trajectory records (Phase 23).

Deliberately in-memory: no DB table fits this derived, per-match
analysis artifact, and adding a migration for it was NOT approved.
Records are bounded per match so a long broadcast cannot grow memory
without limit; the oldest records for a match are evicted first.

Every record in this store carries ``estimated: True`` — the store
refuses records that do not, so an unlabelled arc can never be served
by the API by accident.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional

#: Max trajectory records retained per match.
MAX_PER_MATCH = 500


class TrajectoryStore:
    """Bounded, label-enforcing in-memory record store."""

    def __init__(self, max_per_match: int = MAX_PER_MATCH):
        self._max_per_match = max_per_match
        self._lock = threading.Lock()
        self._by_match: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()

    def add(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Store one record. Raises ValueError if the estimate label or
        either endpoint is missing — those records must never exist."""
        if record.get("estimated") is not True:
            raise ValueError(
                "grenade trajectory records must carry estimated=True"
            )
        thrower = (record.get("thrower") or {}).get("position")
        explosion = (record.get("explosion") or {}).get("position")
        if not thrower or not explosion:
            raise ValueError(
                "grenade trajectory records require both endpoints"
            )
        match_id = str(record.get("match_id") or "unknown")
        with self._lock:
            bucket = self._by_match.setdefault(match_id, [])
            bucket.append(record)
            if len(bucket) > self._max_per_match:
                del bucket[: len(bucket) - self._max_per_match]
            self._by_match.move_to_end(match_id)
        return record

    def list(
        self, match_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Records for one match (insertion order), or every record."""
        with self._lock:
            if match_id is not None:
                return list(self._by_match.get(str(match_id), []))
            out: List[Dict[str, Any]] = []
            for bucket in self._by_match.values():
                out.extend(bucket)
            return out

    def count(self, match_id: Optional[str] = None) -> int:
        with self._lock:
            if match_id is not None:
                return len(self._by_match.get(str(match_id), []))
            return sum(len(b) for b in self._by_match.values())

    def clear(self) -> None:
        with self._lock:
            self._by_match.clear()


#: Process-wide store. The bus consumer writes here; the analysis API
#: reads here. One instance, shared by construction.
trajectory_store = TrajectoryStore()
