"""AEGIS AI — POI (point-of-interest) database (Phase 24, Worker W4).

Turns a normalised map position ``(x, y)`` into a *name* — "Pochinki",
"Bootcamp", "Pecado" — so director derivations, drop tracking and
broadcast graphics can say where something happened instead of quoting
raw coordinates.

Contract (W2 + W3 code against this simultaneously — do not change
signatures without updating ``docs/SIGNAL_REGISTRY.md``):

* ``POIDatabase`` — loads every ``data/<map>.json`` file in this package.
* ``get_poi_database()`` — process-wide singleton accessor.
* ``nearest(map_id, x, y, max_radius=0.05) -> dict | None``
* ``by_name(map_id, name) -> dict | None``
* ``all_pois(map_id) -> list[dict]``
* ``maps() -> list[str]``

POI dict shape (exactly these keys)::

    {"name": str, "map": str, "x": float, "y": float,
     "type": str, "tier": int}

``x`` / ``y`` are normalised ``0..1`` in map-image space, origin
top-left (``x`` grows east, ``y`` grows south) — the same space the
vision minimap / map-cam work uses.

Honesty rules
-------------
* Coordinates are *approximate naming references* (see the
  ``coords_note`` in every data file): good enough for nearest-POI
  naming inside ``max_radius``, **not** survey-grade positions. No
  code or doc may present them as exact.
* An unknown map, an unknown name, or a position with no POI inside
  ``max_radius`` returns ``None`` / ``[]`` — never an exception and
  never a guessed name. Callers must handle "no name here" as a
  normal outcome (open field, water, un-named compound).
* Returned dicts are copies; mutating one never corrupts the database.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Directory holding the ``<map>.json`` data files (inside this package).
DATA_DIR = Path(__file__).resolve().parent / "data"

#: POI types used by the data files. Kept as data, not an enum, so a new
#: map file can introduce a type without a code change — but producers
#: should stick to this vocabulary.
POI_TYPES = (
    "city",
    "compound",
    "landmark",
    "bridge",
    "island",
    "hill",
    "warehouse-area",
)

#: Tier semantics: 1 = hot-drop (high loot / high contest),
#: 2 = medium, 3 = quiet.
VALID_TIERS = (1, 2, 3)

_POI_KEYS = ("name", "map", "x", "y", "type", "tier")


def _normalise_map_id(map_id: Any) -> str:
    """Best-effort map-id normalisation; never raises."""
    if not isinstance(map_id, str):
        return ""
    return map_id.strip().lower()


def _clean_poi(raw: Any, fallback_map: str) -> Optional[Dict[str, Any]]:
    """Validate one raw POI entry; return a clean dict or ``None``.

    Malformed entries are skipped, never raised — one bad row in a
    data file must not take down naming for the whole map.
    """
    if not isinstance(raw, dict):
        return None
    try:
        name = str(raw["name"]).strip()
        x = float(raw["x"])
        y = float(raw["y"])
        tier = int(raw["tier"])
    except (KeyError, TypeError, ValueError):
        return None
    if not name or tier not in VALID_TIERS:
        return None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    poi_type = str(raw.get("type") or "compound").strip() or "compound"
    map_id = _normalise_map_id(raw.get("map")) or fallback_map
    return {
        "name": name,
        "map": map_id,
        "x": x,
        "y": y,
        "type": poi_type,
        "tier": tier,
    }


class POIDatabase:
    """In-memory POI lookup over the JSON files in ``data/``.

    Parameters
    ----------
    data_dir:
        Directory of ``<map>.json`` files. Defaults to the package
        ``data/`` directory. Tests may pass a temporary directory;
        production code should use the default (via
        :func:`get_poi_database`).
    """

    def __init__(self, data_dir: Optional[Path | str] = None) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        # map_id -> list of POI dicts (file order preserved).
        self._by_map: Dict[str, List[Dict[str, Any]]] = {}
        # (map_id, casefolded name) -> POI dict, for by_name lookups.
        self._by_name: Dict[tuple[str, str], Dict[str, Any]] = {}
        self._load_all()

    # ── loading ──────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Load every data file. Bad files are skipped, never raised."""
        if not self._data_dir.is_dir():
            return
        for path in sorted(self._data_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            fallback_map = _normalise_map_id(payload.get("map")) or path.stem.lower()
            raw_pois = payload.get("pois")
            if not isinstance(raw_pois, list):
                continue
            cleaned: List[Dict[str, Any]] = []
            seen_names: set[str] = set()
            for raw in raw_pois:
                poi = _clean_poi(raw, fallback_map)
                if poi is None:
                    continue
                key = poi["name"].casefold()
                if key in seen_names:
                    # Duplicate name inside one map: first entry wins so
                    # by_name stays deterministic.
                    continue
                seen_names.add(key)
                cleaned.append(poi)
                self._by_name[(fallback_map, key)] = poi
            if cleaned:
                self._by_map[fallback_map] = cleaned

    # ── public contract ──────────────────────────────────────────

    def maps(self) -> List[str]:
        """Return the sorted list of map ids that have POI data."""
        return sorted(self._by_map)

    def all_pois(self, map_id: str) -> List[Dict[str, Any]]:
        """Return every POI for ``map_id`` (copies, file order).

        Unknown map → ``[]`` (never an exception).
        """
        pois = self._by_map.get(_normalise_map_id(map_id), [])
        return [dict(p) for p in pois]

    def by_name(self, map_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Look up a POI by name on one map (case-insensitive).

        Unknown map or unknown name → ``None``.
        """
        if not isinstance(name, str):
            return None
        poi = self._by_name.get(
            (_normalise_map_id(map_id), name.strip().casefold())
        )
        return dict(poi) if poi is not None else None

    def nearest(
        self,
        map_id: str,
        x: float,
        y: float,
        max_radius: float = 0.05,
    ) -> Optional[Dict[str, Any]]:
        """Return the closest POI to ``(x, y)`` within ``max_radius``.

        Distance is Euclidean in normalised map-image space. The
        radius is inclusive: a POI exactly ``max_radius`` away counts.
        No POI inside the radius (or an unknown map, or non-finite
        inputs) → ``None`` — the caller shows no name rather than a
        wrong one.
        """
        pois = self._by_map.get(_normalise_map_id(map_id))
        if not pois:
            return None
        try:
            fx = float(x)
            fy = float(y)
            radius = float(max_radius)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(fx) and math.isfinite(fy)):
            return None
        if not math.isfinite(radius) or radius < 0:
            return None
        best: Optional[Dict[str, Any]] = None
        best_dist = radius
        for poi in pois:
            dist = math.hypot(poi["x"] - fx, poi["y"] - fy)
            # Strict ``<`` after the first hit keeps ties deterministic
            # (earliest POI in file order wins an exact tie).
            if dist < best_dist or (best is None and dist <= best_dist):
                if dist <= radius:
                    best = poi
                    best_dist = dist
        return dict(best) if best is not None else None


_singleton: Optional[POIDatabase] = None
_singleton_lock = threading.Lock()


def get_poi_database() -> POIDatabase:
    """Return the process-wide :class:`POIDatabase` singleton.

    The database is built once (data files are small) and shared by
    every caller, so W2/W3 derivations never re-read disk per lookup.
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = POIDatabase()
    return _singleton


__all__ = [
    "DATA_DIR",
    "POI_TYPES",
    "VALID_TIERS",
    "POIDatabase",
    "get_poi_database",
]
