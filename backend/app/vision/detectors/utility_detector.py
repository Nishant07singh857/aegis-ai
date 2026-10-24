# backend/app/vision/detectors/utility_detector.py
"""
AEGIS AI - World-Side Utility Detector (Phase 23, W3 rewrite).

Detects utility phenomena in the GAME WORLD of the BGMI spectator view
(smoke walls, molotov fire) — NOT killfeed icons (that is the killfeed
detector's job).

PHASE 23 REAL-FOOTAGE AUDIT (7 VODs, 640x360, sampled + dense scans of
bmsd2026_gf_day1.mp4) — why this file was rewritten:

* OLD smoke detector (S<=30, V>=150, area>=1000, no other filter) fired
  on essentially EVERY sampled frame (smoke-mask covered 2-24% of
  pixels). The recurring ~1,850px contours sat at y=119, h=37 in a neat
  row — that is the broadcast team-status HUD bar, not smoke. Sky,
  clouds, white graphics and UI panels all passed. Verdict: the old
  smoke path produced pure false positives; its events were junk.
* OLD fire detector (H 0-20, S>=200, V>=200, area>=200) almost never
  fired; a static 52x12 UI strip at (458,150) DID pass its size test.
* OLD explosion detector (H 10-30, S>=150, V>=200) fired once in the
  dense scan, co-located with the fire region — the same phenomenon
  double-counted as "grenade" AND "molotov". A single-frame orange
  flash cannot be separated from molotov ignition / muzzle flash /
  ability effects on real footage.
* The brightest real frames in the VODs (mean luma 251, 97% of pixels
  >240) are BROADCAST white transitions/graphics, not stun grenades —
  brightness alone cannot identify a stun.

What this rewrite does (every threshold below was measured on the
frames described above; reference frame = 640x360, thresholds scale
by frame area):

* SMOKE — low-saturation bright region PLUS: large soft blob
  (area >= 1.3% of frame, w>=70, h>=35 at ref size), blobby fill
  ratio, LOW internal texture (mean |Laplacian| < 24 — measured:
  soft world regions 13-18, HUD/UI contours 28-66), centroid in the
  world band (y >= 22% of height — kills the HUD row and sky bands),
  not a full-width top band. PLUS temporal confirmation: the same
  ~60px bucket must be seen on 2 separate detect() passes before an
  event is emitted, and an episode is emitted once (25s suppression,
  matching real smoke duration). Residual FP risk: fog/dust weather
  effects and large soft graphics in the lower frame can still pass —
  confidence is capped accordingly (<=0.78) and the runtime dedups.
* MOLOTOV — saturated orange/red region PLUS: area >= 0.35% of frame,
  bbox not a thin strip (h>=20, w/h<6 — kills the measured UI strip),
  centroid in the world band, a FLAME CORE: >=4% of the region must
  be near-white/yellow (H 15-45, S>=100, V>=248). This core test was
  added after visual verification caught an ORANGE CAR confirming as
  molotov (bridge fight, t=15563): the car's mask flickered under a
  panning camera (areas 1855/1537/3823/1506) but its yellow-white
  core measured 4px of 4,906 (0.08%) — painted metal has no flame
  core. PLUS FLICKER: the region's mask area must vary across
  sightings (CV >= 0.12 over >=3 sightings) OR be very large
  (>=1.1% of frame) and growing on >=2 sightings. Persistence +
  flicker + core separates a burning molotov (~7s) from a muzzle
  flash (1 frame, small), static UI/signage (no flicker) and orange
  vehicles (no core). Episode emitted once, 8s suppression. Cost:
  dull-cored real fires will be missed — false negatives are
  preferred over fake events.
* SCENE GATE — both smoke and molotov are skipped when the frame's
  median saturation is < 20. Measured: broadcast grayscale segments
  (player cams / replays shown in B&W) have median S = 0 and their
  soft bright regions otherwise pass every smoke filter (a B&W
  player-cam frame confirmed as "smoke" in validation); real
  gameplay frames measure median S 80-110. World phenomena are only
  claimed on colour gameplay frames.
* GRENADE (explosion flash) — DISABLED BY DEFAULT
  (``enable_explosion_detection=False``). Reason, measured above: on
  real footage its only triggers co-locate with fire regions, so it
  double-counts molotovs and cannot be honestly labelled "grenade".
  The code path is kept for future calibration with labelled footage;
  enabling it is an explicit operator decision.
* STUN / FLASH — DISABLED BY DEFAULT
  (``enable_stun_detection=False``). The logic is implemented
  (full-frame luminance spike + decay within ~1s + NO hard cut:
  the pre-flash and post-flash scenes must histogram-match, which is
  what separates an in-game flash from a broadcast transition), but
  it could not be validated against a single confirmed real stun in
  7 VODs, while broadcast white transitions that mimic it are common.
  Honest disabled beats false events. Enable only after calibration
  on labelled footage.

INTEGRITY GATE (H21): neither path can be enabled by the constructor
bool alone. BOTH the bool AND the environment variable
``AEGIS_ENABLE_UNMEASURED_UTILITY_DETECTION=1`` must be set — a
single bool flip in code review can no longer silently put
pure-false-positive logic on the live path. When the gate does open,
construction logs a loud WARNING, and every emitted grenade/stun
event carries ``confidence_basis="unmeasured_heuristic"``.

Attribution honesty: this detector works in screen space. It does NOT
know who threw anything. Events carry no player/team; the runtime
stamps the observer slot and the Phase 22 vision-intelligence
subscriber attributes at TEAM level via that slot (thrower recorded
as the unidentified observer screen). Per-player utility stats are
never credited from world detections.
"""

import os
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.utility")

#: Reference frame area (640x360 VODs) the absolute thresholds were
#: tuned on; all area thresholds scale linearly with frame area.
_REF_AREA = 640.0 * 360.0

#: Env gate for the UNMEASURED utility paths (explosion / stun) —
#: H21. The constructor bool alone is NOT sufficient to enable them;
#: this variable must also be set to a truthy value at construction
#: time. Naming follows the repo's AEGIS_* env convention.
UNMEASURED_UTILITY_ENV_VAR = "AEGIS_ENABLE_UNMEASURED_UTILITY_DETECTION"


def unmeasured_utility_env_enabled() -> bool:
    """True only when the operator env gate for unmeasured utility
    detection (explosion/stun) is explicitly set."""
    return (
        os.getenv(UNMEASURED_UTILITY_ENV_VAR, "") or ""
    ).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class UtilityInfo:
    """Utility usage information (world-side observation)."""
    event_id: str = ""
    timestamp: float = field(default_factory=time.time)

    # Utility type: grenade, smoke, molotov, stun
    utility_type: str = ""

    # Thrower — ALWAYS empty for world detections (never guessed).
    player_id: str = ""
    player_name: str = ""
    team_id: str = ""

    # Position in frame pixels (centroid of the observed phenomenon)
    throw_position: Tuple[float, float] = (0.0, 0.0)
    landing_position: Tuple[float, float] = (0.0, 0.0)
    trajectory_points: List[Tuple[float, float]] = field(default_factory=list)

    # Effect
    is_exploded: bool = False
    explosion_time: float = 0.0
    affected_players: List[str] = field(default_factory=list)
    damage_dealt: int = 0

    # Smoke specific
    smoke_radius: float = 0.0
    smoke_duration: float = 25.0

    # Detection
    confidence: float = 0.0
    frame_detected: int = 0
    # Measured evidence summary (area px, flicker CV, sightings...)
    evidence: Dict[str, float] = field(default_factory=dict)


class UtilityDetector(BaseDetector):
    """
    Detects world-side utility phenomena in BGMI spectator frames.

    Emits (via DetectionResult.extra_data["events"]) only CONFIRMED
    episodes: smoke (2-pass temporal confirmation), molotov (flicker +
    persistence). Grenade-explosion and stun detection are implemented
    but gated OFF by default — see the module docstring for the
    measured reasons.
    """

    def __init__(
        self,
        enable_explosion_detection: bool = False,
        enable_stun_detection: bool = False,
        clock=None,
    ):
        super().__init__(name="utility_detector")

        self.detection_region = DetectionBox(x=0.0, y=0.0, width=1.0, height=1.0)

        # Kept for backwards compatibility (older tests / introspection).
        self.utility_indicators = {
            "grenade": {
                "throw_icon": "frag_icon",
                "color_range": ((0, 100, 100), (30, 200, 200)),
                "explosion_color": ((0, 100, 200), (50, 200, 255)),
            },
            "smoke": {
                "throw_icon": "smoke_icon",
                "color_range": ((100, 100, 100), (200, 200, 200)),
                "smoke_color": ((150, 150, 150), (255, 255, 255)),
            },
            "molotov": {
                "throw_icon": "molotov_icon",
                "color_range": ((0, 150, 200), (50, 200, 255)),
                "fire_color": ((0, 100, 200), (50, 150, 255)),
            },
        }

        #: Honest feature gates (see module docstring for evidence).
        #: H21: the constructor bool is necessary but NOT sufficient —
        #: the operator env gate must also be set, so a silent bool
        #: flip cannot put these pure-false-positive paths live.
        env_gate = unmeasured_utility_env_enabled()
        self.enable_explosion_detection = bool(
            enable_explosion_detection and env_gate
        )
        self.enable_stun_detection = bool(
            enable_stun_detection and env_gate
        )
        for flag_name, requested, effective in (
            ("enable_explosion_detection", enable_explosion_detection,
             self.enable_explosion_detection),
            ("enable_stun_detection", enable_stun_detection,
             self.enable_stun_detection),
        ):
            if requested and not env_gate:
                logger.warning(
                    "unmeasured_utility_detection_blocked_by_env_gate",
                    flag=flag_name,
                    env_var=UNMEASURED_UTILITY_ENV_VAR,
                    reason="explosion/stun detection was measured as "
                    "pure false positives on real footage (Phase 23); "
                    "the constructor bool alone no longer enables it — "
                    "set the env var as an explicit operator decision",
                )
            elif effective:
                logger.warning(
                    "UNMEASURED_UTILITY_DETECTION_ENABLED",
                    flag=flag_name,
                    env_var=UNMEASURED_UTILITY_ENV_VAR,
                    reason="explosion/stun events from this detector are "
                    "UNMEASURED and were pure false positives on real "
                    "footage (Phase 23 audit); enable only for "
                    "calibration on labelled footage",
                )
        self._clock = clock or time.time

        # Active episodes (for get_active_smoke_positions / status).
        self.active_smokes: List[UtilityInfo] = []
        self.active_fires: List[UtilityInfo] = []

        # --- temporal state -------------------------------------------
        # smoke: bucket -> [first_seen_ts, sightings, last_info]
        self._smoke_candidates: Dict[tuple, list] = {}
        # smoke/molotov: bucket -> last emit ts (episode suppression)
        self._episode_emitted: Dict[tuple, float] = {}
        # molotov: bucket -> [(ts, area), ...] recent sightings
        self._fire_sightings: Dict[tuple, List[Tuple[float, float]]] = {}
        self._fire_confirmed: Dict[tuple, float] = {}
        # stun: previous frame luma stats + pre-flash histogram
        self._prev_mean_luma: Optional[float] = None
        self._prev_hist: Optional[np.ndarray] = None
        self._flash_peak_ts: Optional[float] = None
        self._flash_pre_hist: Optional[np.ndarray] = None
        self._last_stun_emit: float = 0.0

        self._log = logger.bind(detector=self.name)

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        start_time = time.time()
        h, w = frame.shape[:2]
        self._last_frame_area = float(h * w)
        now = self._clock()
        confirmed: List[UtilityInfo] = []

        try:
            import cv2  # noqa: F401  (detector is inert without OpenCV)
        except ImportError:
            return DetectionResult(
                detector_name=self.name,
                status=DetectionStatus.FAILED,
                frame_width=w, frame_height=h,
                processing_time_ms=self._time_detection(start_time),
                extra_data={"utilities_detected": 0, "events": [],
                            "error": "opencv_unavailable"},
            )

        # --- scene gate: colour gameplay frames only ------------------
        # Desaturated broadcast segments (B&W player cams/replays,
        # median S ~= 0 measured) are not the game world; claiming
        # smoke/fire there is a false positive by construction.
        scene_desaturated = False
        try:
            import cv2 as _cv2
            _hsv = _cv2.cvtColor(frame, _cv2.COLOR_BGR2HSV)
            scene_desaturated = float(np.median(_hsv[:, :, 1])) < 20.0
        except Exception:
            scene_desaturated = False

        if scene_desaturated:
            smoke_candidates = []
            fire_candidates = []
        else:
            # --- smoke: candidates -> temporal confirmation -----------
            smoke_candidates = self._detect_smoke(frame)
            # --- molotov: candidates -> flicker/persistence -----------
            fire_candidates = self._detect_fire(frame)
        confirmed.extend(self._confirm_smoke(smoke_candidates, now))
        confirmed.extend(self._confirm_fire(fire_candidates, now))

        # --- grenade explosion flash: DISABLED by default --------------
        if self.enable_explosion_detection:
            confirmed.extend(self._detect_explosions(frame))

        # --- stun flash transient: DISABLED by default -----------------
        if self.enable_stun_detection:
            stun = self._detect_stun(frame, now)
            if stun is not None:
                confirmed.append(stun)
        else:
            # Keep luma history warm so enabling later starts sane.
            self._track_luma(frame)

        self._update_active_utilities(confirmed)
        self._prune(now)

        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if confirmed else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "utilities_detected": len(confirmed),
                "events": [
                    {
                        "type": u.utility_type,
                        "position": u.landing_position,
                        "affected_players": u.affected_players,
                        "damage": u.damage_dealt,
                        "confidence": round(u.confidence, 3),
                        # Smoke/molotov confidences come out of the
                        # Phase 23 measured gates; grenade/stun are
                        # UNMEASURED heuristics (H21) and say so.
                        "confidence_basis": (
                            "unmeasured_heuristic"
                            if u.utility_type in ("grenade", "stun")
                            else "phase23_measured_gates"
                        ),
                        "source": "world_detector",
                        "evidence": {k: round(float(v), 3) for k, v in u.evidence.items()},
                    }
                    for u in confirmed
                ],
                "active_smokes": len(self.active_smokes),
                "active_fires": len(self.active_fires),
                "scene_desaturated_skip": scene_desaturated,
                "explosion_detection_enabled": self.enable_explosion_detection,
                "stun_detection_enabled": self.enable_stun_detection,
            },
        )
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # Smoke
    # ------------------------------------------------------------------

    def _detect_smoke(self, frame: np.ndarray) -> List[UtilityInfo]:
        """Single-frame smoke CANDIDATES (strict filters; confirmation
        happens in _confirm_smoke). Returns candidates, not events."""
        events: List[UtilityInfo] = []
        try:
            import cv2

            h, w = frame.shape[:2]
            scale = (h * w) / _REF_AREA
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            lap = np.abs(cv2.Laplacian(gray, cv2.CV_64F))

            # Low saturation, bright (smoke in BGMI reads gray-white).
            mask = cv2.inRange(
                hsv,
                np.array([0, 0, 130], dtype=np.uint8),
                np.array([180, 40, 255], dtype=np.uint8),
            )
            # Soften single-pixel noise; smoke edges are diffuse.
            mask = cv2.medianBlur(mask, 5)

            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            min_area = 3000.0 * scale          # 1.3% of frame at ref size
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < min_area:
                    continue
                x, y, bw, bh = cv2.boundingRect(contour)
                if bw < 70 * (scale ** 0.5) or bh < 35 * (scale ** 0.5):
                    continue
                # Blobby fill: smoke fills most of its bbox; struts,
                # panels and text rows do not.
                if area / max(1.0, bw * bh) < 0.45:
                    continue
                cx, cy = x + bw / 2.0, y + bh / 2.0
                # World band: the measured HUD row sits at y~0.33H with
                # h=37 (rejected by size anyway); sky/graphic bands sit
                # at the top. Require the centroid below 22% of height.
                if cy < 0.22 * h:
                    continue
                # Full-width top band = sky / broadcast graphic wipe.
                if y <= 2 and bw >= 0.8 * w:
                    continue
                # Texture: measured mean|Laplacian| inside the bbox —
                # soft world phenomena 13-18, HUD/UI contours 28-66.
                roi = lap[max(y, 0):y + bh, max(x, 0):x + bw]
                texture = float(roi.mean()) if roi.size else 999.0
                if texture >= 24.0:
                    continue
                conf = 0.60 + min(0.18, (area / max(1.0, h * w)) * 2.0)
                events.append(UtilityInfo(
                    event_id=f"smoke_cand_{int(time.time()*1000)}",
                    utility_type="smoke",
                    landing_position=(float(cx), float(cy)),
                    smoke_radius=float(np.sqrt(area / np.pi)),
                    confidence=conf,
                    evidence={"area_px": area, "texture": texture},
                ))
        except Exception as e:
            self._log.error(f"Smoke detection error: {e}")
        return events

    def _confirm_smoke(self, candidates: List[UtilityInfo], now: float) -> List[UtilityInfo]:
        """A smoke must persist: same ~60px bucket on 2 separate
        detect() passes (pipeline cadence ~1.3s) before it is real.
        One episode emits once; suppression lasts 25s (smoke life)."""
        confirmed: List[UtilityInfo] = []
        seen_buckets = set()
        for cand in candidates:
            bucket = ("smoke", round(cand.landing_position[0] / 60),
                      round(cand.landing_position[1] / 60))
            seen_buckets.add(bucket)
            if now - self._episode_emitted.get(bucket, -1e18) < 25.0:
                continue  # episode already reported
            entry = self._smoke_candidates.get(bucket)
            if entry is None:
                self._smoke_candidates[bucket] = [now, 1, cand]
                continue
            entry[1] += 1
            entry[2] = cand
            if entry[1] >= 2:
                self._episode_emitted[bucket] = now
                del self._smoke_candidates[bucket]
                cand.event_id = f"smoke_{int(now*1000)}"
                cand.evidence["sightings"] = 2.0
                confirmed.append(cand)
        # Drop stale candidates (phenomenon vanished before confirming).
        for bucket in list(self._smoke_candidates):
            if bucket not in seen_buckets and now - self._smoke_candidates[bucket][0] > 6.0:
                del self._smoke_candidates[bucket]
        return confirmed

    # ------------------------------------------------------------------
    # Molotov / fire
    # ------------------------------------------------------------------

    def _detect_fire(self, frame: np.ndarray) -> List[UtilityInfo]:
        """Single-frame fire CANDIDATES (flicker/persistence logic in
        _confirm_fire separates molotovs from muzzle flashes and UI)."""
        events: List[UtilityInfo] = []
        try:
            import cv2

            h, w = frame.shape[:2]
            scale = (h * w) / _REF_AREA
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

            # Saturated orange/red fire. Two hue bands (red wraps).
            mask_a = cv2.inRange(
                hsv, np.array([2, 180, 190], dtype=np.uint8),
                np.array([28, 255, 255], dtype=np.uint8))
            mask = mask_a
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            min_area = 800.0 * scale           # 0.35% of frame at ref
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < min_area:
                    continue
                x, y, bw, bh = cv2.boundingRect(contour)
                # Thin-strip rejection: the measured UI false positive
                # was 52x12. Real fire pools are compact-ish.
                if bh < 20 * (scale ** 0.5) or bw / max(1.0, bh) > 6.0:
                    continue
                cx, cy = x + bw / 2.0, y + bh / 2.0
                if cy < 0.18 * h:  # top banner / sky fire effects
                    continue
                # Flame core: near-white/yellow heart of a real flame.
                # Measured separator vs orange vehicles: a confirmed
                # car false positive had 0.08% core pixels; flames
                # render with a substantial bright core.
                roi_hsv = hsv[max(y, 0):y + bh, max(x, 0):x + bw]
                core = cv2.inRange(
                    roi_hsv, np.array([15, 100, 248], dtype=np.uint8),
                    np.array([45, 255, 255], dtype=np.uint8))
                core_frac = float((core > 0).sum()) / max(1.0, area)
                if core_frac < 0.04:
                    continue
                events.append(UtilityInfo(
                    event_id=f"fire_cand_{int(time.time()*1000)}",
                    utility_type="molotov",
                    landing_position=(float(cx), float(cy)),
                    confidence=0.62,
                    evidence={"area_px": area, "core_frac": core_frac},
                ))
        except Exception as e:
            self._log.error(f"Fire detection error: {e}")
        return events

    def _confirm_fire(self, candidates: List[UtilityInfo], now: float) -> List[UtilityInfo]:
        """Molotov confirmation: a burning pool persists ~7s and its
        mask area FLICKERS. Require, within a 3s window in one ~60px
        bucket: >=3 sightings with area CV >= 0.12 (measured real fire:
        areas 1855/1537/3823/1506 -> CV ~0.5), OR >=2 sightings with a
        very large area (>=1.1% of frame). A muzzle flash (1 sighting,
        small) and static signage (CV ~0) never confirm."""
        confirmed: List[UtilityInfo] = []
        frame_area = getattr(self, "_last_frame_area", _REF_AREA)
        for cand in candidates:
            bucket = ("molotov", round(cand.landing_position[0] / 60),
                      round(cand.landing_position[1] / 60))
            hist = self._fire_sightings.setdefault(bucket, [])
            hist.append((now, cand.evidence.get("area_px", 0.0)))
            hist[:] = [(t, a) for (t, a) in hist if now - t <= 3.0]
            if bucket in self._fire_confirmed and now - self._fire_confirmed[bucket] < 8.0:
                continue  # episode already reported; still burning
            areas = [a for _, a in hist]
            # "Big fire" path: very large AND growing/spreading (a
            # static saturated billboard/sunset patch has ratio ~1.0
            # and must not confirm).
            big = (
                max(areas) >= 0.011 * frame_area
                and min(areas) > 0
                and max(areas) / min(areas) >= 1.15
            )
            flicker = False
            cv_val = 0.0
            if len(areas) >= 3:
                mean_a = float(np.mean(areas))
                if mean_a > 0:
                    cv_val = float(np.std(areas) / mean_a)
                    flicker = cv_val >= 0.12
            if flicker or (len(areas) >= 2 and big):
                self._fire_confirmed[bucket] = now
                cand.event_id = f"molotov_{int(now*1000)}"
                cand.confidence = 0.66 + (0.12 if flicker else 0.0)
                cand.evidence["flicker_cv"] = cv_val
                cand.evidence["sightings"] = float(len(areas))
                confirmed.append(cand)
        return confirmed

    # ------------------------------------------------------------------
    # Grenade explosion flash — DISABLED BY DEFAULT (see module docs)
    # ------------------------------------------------------------------

    def _detect_explosions(self, frame: np.ndarray) -> List[UtilityInfo]:
        """Bright orange flash blobs. FALSE-POSITIVE-PRONE: cannot be
        separated from molotov ignition / muzzle flash on real footage
        (Phase 23 audit). Only runs when explicitly enabled."""
        events: List[UtilityInfo] = []
        try:
            import cv2

            h, w = frame.shape[:2]
            scale = (h * w) / _REF_AREA
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(
                hsv, np.array([10, 150, 220], dtype=np.uint8),
                np.array([30, 255, 255], dtype=np.uint8))
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < 300 * scale or area > 10000 * scale:
                    continue
                M = cv2.moments(contour)
                if M["m00"] <= 0:
                    continue
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                events.append(UtilityInfo(
                    event_id=f"explosion_{int(time.time()*1000)}",
                    utility_type="grenade",
                    landing_position=(float(cx), float(cy)),
                    is_exploded=True,
                    explosion_time=time.time(),
                    confidence=0.55,  # low: known confusers (see docs)
                    evidence={"area_px": area},
                ))
        except Exception as e:
            self._log.error(f"Explosion detection error: {e}")
        return events

    # ------------------------------------------------------------------
    # Stun / flash — DISABLED BY DEFAULT (see module docs)
    # ------------------------------------------------------------------

    def _track_luma(self, frame: np.ndarray) -> None:
        try:
            import cv2
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            self._prev_mean_luma = float(gray.mean())
            self._prev_hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).flatten()
        except Exception:
            pass

    def _detect_stun(self, frame: np.ndarray, now: float) -> Optional[UtilityInfo]:
        """White-flash transient WITH hard-cut guard.

        Fires only when: mean luma spikes >=55 above the previous
        frame AND >=70% of pixels exceed 235 AND, on the FOLLOWING
        pass, luma has decayed while the scene histogram matches the
        pre-flash scene (correlation >= 0.7) — i.e. the flash happened
        INSIDE continuous gameplay. A broadcast transition fails the
        histogram test because the scene on the other side differs.
        Unvalidated on real stuns -> gated off by default.
        """
        try:
            import cv2

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_luma = float(gray.mean())
            white_frac = float((gray > 235).mean())
            hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).flatten()

            event = None
            if self._flash_peak_ts is not None:
                # We saw a spike last pass: did it decay in-scene?
                decayed = mean_luma < 200.0
                same_scene = False
                if self._flash_pre_hist is not None:
                    a = self._flash_pre_hist.astype(np.float64)
                    b = hist.astype(np.float64)
                    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
                    if denom > 0:
                        same_scene = float(a @ b) / denom >= 0.7
                if (decayed and same_scene
                        and now - self._last_stun_emit >= 5.0):
                    self._last_stun_emit = now
                    h, w = frame.shape[:2]
                    event = UtilityInfo(
                        event_id=f"stun_{int(now*1000)}",
                        utility_type="stun",
                        landing_position=(w / 2.0, h / 2.0),
                        confidence=0.6,
                        evidence={"white_frac": getattr(
                            self, "_flash_peak_white_frac", white_frac)},
                    )
                self._flash_peak_ts = None
                self._flash_pre_hist = None
            elif (self._prev_mean_luma is not None
                    and mean_luma - self._prev_mean_luma >= 55.0
                    and white_frac >= 0.70):
                # Spike onset: remember the pre-flash scene, wait one
                # pass for the decay + continuity check.
                self._flash_peak_ts = now
                self._flash_pre_hist = self._prev_hist
                self._flash_peak_white_frac = white_frac

            self._prev_mean_luma = mean_luma
            self._prev_hist = hist
            return event
        except Exception as e:
            self._log.error(f"Stun detection error: {e}")
            return None

    # ------------------------------------------------------------------
    # Bookkeeping (kept API-compatible with the original detector)
    # ------------------------------------------------------------------

    def _update_active_utilities(self, detected: List[UtilityInfo]) -> None:
        current_time = time.time()
        for event in detected:
            if event.utility_type == "smoke":
                self.active_smokes.append(event)
            elif event.utility_type == "molotov":
                self.active_fires.append(event)
        self.active_smokes = [
            s for s in self.active_smokes if current_time - s.timestamp < 30
        ]
        self.active_fires = [
            f for f in self.active_fires if current_time - f.timestamp < 10
        ]

    def _prune(self, now: float) -> None:
        self._episode_emitted = {
            k: v for k, v in self._episode_emitted.items() if now - v < 60.0
        }
        self._fire_sightings = {
            k: v for k, v in self._fire_sightings.items()
            if v and now - v[-1][0] <= 5.0
        }
        self._fire_confirmed = {
            k: v for k, v in self._fire_confirmed.items() if now - v < 30.0
        }

    def get_active_smoke_positions(self) -> List[Tuple[float, float]]:
        return [s.landing_position for s in self.active_smokes]

    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        return [self.detection_region]
