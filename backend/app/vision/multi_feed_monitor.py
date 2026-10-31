# backend/app/vision/multi_feed_monitor.py
"""
AEGIS AI - Multi-Feed Parallel Killfeed Monitoring (Phase 25, W2).

The structural missed-fight fix from docs/SPECTATOR_PRODUCTION_RESEARCH.md
§6 items 2 + 4: the #1 documented observer failure is simultaneity — a
single watched feed misses fights happening elsewhere. The killfeed,
however, is rendered on EVERY observer screen. This module runs the
cheap UI-layer readers on every connected observer feed in parallel:

    per feed: KillFeedDetector (raw, pre-dedup entries)
              AliveCountReader (its own 2-frame confirmation)
              ZoneTimerReader  (timer-reset edge)

The full 10-detector pipeline (HP / utility / minimap / zone geometry /
healing / loot / team status) keeps running on the primary / on-air feed
only, inside ``VisionCaptureService`` — unchanged. Monitors never run it.

DESIGN
------
Scheduling (CPU budget — see capture/config.py for the numbers):
  A single scheduler task wakes every ``monitor_tick_s`` (default
  0.5 s) and samples at most ``monitor_batch_size`` feeds (default 4),
  round-robin. Samples run in worker threads so a slow OCR pass never
  blocks the event loop or the primary pipeline. Effective per-feed
  interval = ceil(N / batch) * tick: 0.5 s for up to 4 feeds, 2.0 s at
  16 feeds. That is stated plainly in ``status()``
  (``budget.effective_interval_s_per_feed``) — 16 feeds do NOT get the
  primary feed's ~2.3 FPS full-pipeline rate, and this module never
  pretends otherwise. A killfeed entry lives ~5 s on screen, so a 2 s
  sampling interval still yields the 2 sightings the vote needs.

Phase 28 (W3) — adaptive parallel sampler + band-change pre-filter:
  Phase 26 first sampled the batch strictly one feed at a time
  (``await asyncio.to_thread(sample_once)`` in a loop — the to_thread
  offload existed but was never overlapped), then tried a parallel
  gather and REVERTED it: on this 2-vCPU VM it measured neutral-to-
  negative. The Phase 28 re-measurement (scripts/phase28_sampler_bench.py,
  32 samples over the REAL Phase 26 entry-frame set; full numbers +
  methodology in scripts/phase28_sampler_report.json) found why, and
  what actually helps here. First generation, when every sample paid
  a full parse (per-sample median 1,366 ms — Worker A's independent
  profile measured 1,357 ms):

    * sequential direct loop        50.08 s  (0.639 samples/s)
    * to_thread, awaited serially   60.65 s  (0.528 samples/s) — the old
      production dispatch was SLOWER than a plain loop (default-executor
      dispatch overhead, zero overlap)
    * ThreadPoolExecutor(2)         34.54 s  (0.927 samples/s, 1.45x)
    * ProcessPoolExecutor(2)        40.27 s  (0.795 samples/s, 1.24x)

  A later re-run, after Worker A's row-result micro-cache landed in
  the detector (re-seen strips return in ~5 ms), compressed every
  wall (sequential 14.25 s / thread 12.05 s / process 24.31 s) but
  kept the ordering: threads fastest, processes slowest by far.

  Threads win on this box because the parse's expensive parts already
  escape the GIL — Tesseract runs as a subprocess (pytesseract spawns
  it and the Python thread merely waits) and the cv2 stages
  (matchTemplate / morphology / connected components) release the GIL
  and carry their own native thread pool — so two Python threads DO
  overlap real work, until the 2 physical cores saturate (hence 1.45x,
  not 2x). Processes lose their GIL-freedom advantage to pickling the
  full 640x360x3 frame (691,200 B/task; the killfeed region alone is
  65,751 B but the production reader takes the full frame and crops
  internally), pool startup, and cv2 thread oversubscription
  (2 processes x 2 cv2 threads on 2 cores). A process pool also cannot
  host FeedMonitor state at all (frame-provider closures, the voter,
  bound loggers are unpicklable) — it was rejected on data AND fit.

  Design: ``run_tick`` selects the batch in the SAME round-robin order
  as before, runs the batch's ``sample_once`` calls concurrently on a
  bounded ThreadPoolExecutor (workers = min(cpu, batch, config cap),
  see ``status()["sampler"]``), and then applies the cross-feed gate +
  ingest SERIALLY on the event loop in round-robin order. Sampling is
  per-feed state only (each feed owns its detector / voter / readers),
  so concurrency cannot change WHAT is detected; serialising the emit
  phase in the original order keeps dedup first-winner, alive-count
  and zone-phase decisions bit-identical to the sequential scheduler.
  On a 1-core box (or with the sampler forced sequential) the old
  one-at-a-time to_thread path runs unchanged.

  Pre-filter (Worker B's ``band_change.BandChangeFilter``, imported
  guarded — absent/erroring means ALWAYS parse, fail-open): a feed's
  killfeed parse may be skipped only when (1) the feed has NO pending
  unconfirmed killfeed candidate, (2) it is not the feed's every-Nth
  forced-parse sample (``force_parse_every``, default 3), and (3) the
  filter's ``observe()`` says the band did not change. The skip gates
  ONLY the killfeed parse — the filter's signature covers the
  killfeed band alone, while the alive/zone readers watch different
  HUD regions and are stateful, so they still run on every sample and
  their semantics cannot change. Skips are counted per feed in
  ``status()`` (``samples_parsed`` vs ``samples_skipped``); a
  parse-skipped feed still consumes its round-robin turn (fairness
  unchanged) but ~none of the parse budget.

  Per-sample cost, MEASURED on this VM class (2 vCPU, Tesseract
  present, 960x540 frames): alive read ~4 ms, zone read ~4 ms
  (template engine), killfeed read on an UNCHANGED band ~1 ms — the
  raw path reuses the detector's band-hash cache, because the band is
  static for seconds at a time. The expensive case is a CHANGED band:
  one full layout parse (~34 ms for a clean band; a pathological
  pure-noise frame measured ~2.9 s, which real bands — mostly dark
  with a few bright rows — do not resemble, but the spike is real and
  is visible per feed as ``last_sample_ms`` in the health record). A
  slow parse delays later feeds in that tick only; it cannot block
  the primary pipeline (separate executor) or the event loop
  (worker thread).

Voting (generalised AliveCountReader pattern, research §6 item 4):
  Killfeed entries are read PRE-dedup (the detector's own 30 s dedup
  would hide the second sighting the vote needs). A per-feed
  ``KillfeedVoter`` keeps the last ``monitor_vote_window`` sampled
  frames (default 3) and emits an entry only when its dedup key appears
  in at least ``monitor_vote_threshold`` of them (default 2 of 3). A
  single-frame flicker therefore emits nothing; a real entry (visible
  ~5 s) confirms on its second sighting and is emitted exactly once —
  the voter then suppresses that key for the dedup TTL, mirroring the
  detector's own semantics. Alive/zone readers keep their built-in
  confirmation/edge semantics (they are state readers, not entries).

Per-feed identity (attribution contract, services/vision_intelligence):
  Every emitted event is stamped with ``payload["observer_slot"]`` =
  the feed's slot and ``payload["source_id"]`` = the capture source id,
  plus ``payload["vision_monitor"] = "multi_feed"`` so consumers can
  tell monitor events from primary-pipeline events. The slot is the
  ONLY attribution input; a team is never guessed in this layer.
  Events from unmapped slots still flow — the intelligence subscriber
  drops them downstream exactly as it does for the primary feed.

Cross-feed dedup (bus-spam control):
  The same kill renders on many feeds at once. Per-feed detector dedup
  cannot see across feeds, so this service keeps ONE shared gate:
  kill/knock keys are emitted by the first feed whose vote confirms
  them and suppressed for ``monitor_dedup_ttl_s`` (default 30 s) for
  every other feed; alive-count changes are match-global, so only the
  first feed to confirm a NEW value emits it; zone-phase events are
  match-global and monotonic, so only a phase advance emits. Every
  suppression is counted per feed in the health record
  (``duplicates_suppressed``) — visible, never silent. Downstream
  consumers therefore see at most one copy of each fact, the same
  volume the single-feed runtime produced.

Failure isolation + honest health (capture/feed_health_monitor.py):
  Each feed is sampled independently: a provider that raises, a reader
  that throws, or an ingest failure is recorded against THAT feed and
  never stalls the scheduler, other feeds, or the primary pipeline
  (mirroring FramePipeline's per-detector isolation). Per-feed state —
  ok / stale_frames / no_signal / error / stopped — is computed from
  recorded facts and exposed in ``status()["feeds"]``. No feed (no
  sources registered) -> no events and an honestly empty status.
  No fake frames, no fake events, ever.

Builder: ``build_multi_feed_monitor()`` below is the production entry
point (mirrors ``build_vision_intelligence_service``); the coordinator
wires it into the app lifespan or attaches it to a
``VisionCaptureService`` via ``build_vision_service()`` in runtime.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple

import structlog

from .capture.config import CaptureConfig, load_capture_config
from .capture.feed_health_monitor import MultiFeedHealthTracker

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids the cv2 import)
    from .capture.frame_grabber import CapturedFrame

log = structlog.get_logger("aegis.vision.multi_feed_monitor")

# NormalizedEvent schemas, loaded lazily exactly as runtime.py does, so
# importing this module never drags app.services (SQLAlchemy) behind it.
_schemas = None
_schemas_lock = threading.Lock()


def _feed_schemas():
    global _schemas
    if _schemas is not None:
        return _schemas
    with _schemas_lock:
        if _schemas is not None:
            return _schemas
        try:
            from app.services.feed_adapter import schemas as mod
        except ImportError:  # pragma: no cover - repo-layout guard
            import importlib.util
            import pathlib
            import sys

            path = (
                pathlib.Path(__file__).resolve().parent.parent
                / "services"
                / "feed_adapter"
                / "schemas.py"
            )
            name = "aegis_feed_adapter_schemas_mfm"
            spec = importlib.util.spec_from_file_location(name, str(path))
            if spec is None or spec.loader is None:  # pragma: no cover
                raise
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        _schemas = mod
    return _schemas


# ---------------------------------------------------------------------------
# Phase 28 (W3): sampler + pre-filter configuration resolution.
#
# CaptureConfig is owned by the capture package and predates Phase 28,
# so the new knobs are resolved WITHOUT requiring new dataclass fields:
# a same-named attribute on the config object wins when present (the
# coordinator can add real CaptureConfig fields later and they take
# effect unchanged), then the AEGIS_MONITOR_* env var, then the
# default. Same env-first pattern as capture/config.py's loaders.
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _cfg_int(config: Any, attr: str, env: str, default: int) -> int:
    value = getattr(config, attr, None)
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return _env_int(env, default)


def _cfg_str(config: Any, attr: str, env: str, default: str) -> str:
    value = getattr(config, attr, None)
    if value:
        return str(value).strip().lower()
    return (os.getenv(env, "") or default).strip().lower()


def resolve_sampler_mode(config: Any) -> str:
    """'thread' | 'sequential'. 'auto' (default) picks thread when the
    box has more than one CPU — the Phase 28 bench showed threads win
    1.45x at 2 cores and there is nothing to overlap on one core."""
    mode = _cfg_str(
        config, "monitor_sampler_mode", "AEGIS_MONITOR_SAMPLER_MODE", "auto"
    )
    if mode in ("thread", "threads", "parallel"):
        return "thread"
    if mode in ("sequential", "serial", "single", "off"):
        return "sequential"
    return "thread" if (os.cpu_count() or 1) > 1 else "sequential"


def resolve_sampler_workers(config: Any, for_count: int) -> int:
    """Effective concurrent sampler workers for ``for_count`` feeds.

    min(CPU cores, feeds, config cap) in auto mode; an explicit
    ``monitor_sampler_workers`` / AEGIS_MONITOR_SAMPLER_WORKERS > 0
    overrides the CPU term (operator's call on bigger/shared boxes)
    but never exceeds the feed count — extra workers would idle.
    Sequential mode is always 1.
    """
    if resolve_sampler_mode(config) == "sequential":
        return 1
    feeds = max(1, int(for_count))
    explicit = _cfg_int(
        config, "monitor_sampler_workers", "AEGIS_MONITOR_SAMPLER_WORKERS", 0
    )
    if explicit > 0:
        return max(1, min(explicit, feeds, 32))
    cap = _cfg_int(
        config,
        "monitor_sampler_max_workers",
        "AEGIS_MONITOR_SAMPLER_MAX_WORKERS",
        8,
    )
    return max(1, min(os.cpu_count() or 1, feeds, max(1, cap)))


def resolve_force_parse_every(config: Any) -> int:
    """Every Nth framed sample is force-parsed regardless of the
    pre-filter (Phase 28 contract; default 3). <= 1 means the filter
    can never skip — every sample parses."""
    return _cfg_int(
        config, "monitor_force_parse_every", "AEGIS_MONITOR_FORCE_PARSE_EVERY", 3
    )


def _load_band_filter() -> Optional[Any]:
    """Worker B's BandChangeFilter, or None. Import-guarded and
    fail-open by construction: a missing module, a constructor that
    needs arguments we don't know, or the AEGIS_MONITOR_BAND_FILTER=0
    kill-switch all yield None, and None means ALWAYS parse."""
    if (os.getenv("AEGIS_MONITOR_BAND_FILTER", "") or "").strip() in ("0", "off", "false"):
        return None
    try:
        from .band_change import BandChangeFilter
    except Exception:
        return None
    try:
        return BandChangeFilter()
    except Exception as exc:
        log.warning("band_change_filter_unavailable", error=str(exc)[:200])
        return None


#: Async callable publishing one NormalizedEvent (VisionFeedAdapter.ingest).
IngestFn = Callable[[Any], Any]

#: Zero-arg callable returning the latest CapturedFrame for a feed, or
#: None when the feed has no frame. May raise — the monitor isolates it.
FrameProvider = Callable[[], Optional["CapturedFrame"]]

_SLOT_SOURCE_RE = re.compile(r"aegis-observer-slot-(\d{1,2})")


def slot_from_source_id(source_id: str) -> Optional[int]:
    """Observer slot encoded in a MultiDeviceCaptureManager source id
    (``aegis-observer-slot-NN``), or None — never guessed from anything
    else."""
    m = _SLOT_SOURCE_RE.search(source_id or "")
    if not m:
        return None
    slot = int(m.group(1))
    return slot if 1 <= slot <= 16 else None


# ---------------------------------------------------------------------------
# Feed specification + reader bundle
# ---------------------------------------------------------------------------


@dataclass
class FeedSourceSpec:
    """One observer feed to monitor."""

    slot_number: int  # observer slot 1..16 (attribution identity)
    source_id: str  # capture source id, e.g. "aegis-observer-slot-03"
    frame_provider: FrameProvider
    label: str = ""

    def __post_init__(self) -> None:
        if not 1 <= int(self.slot_number) <= 16:
            raise ValueError(
                f"slot_number must be 1..16, got {self.slot_number}"
            )
        if not self.source_id:
            raise ValueError("source_id must be non-empty")


@dataclass
class FeedReaderBundle:
    """The cheap-reader set for one feed. Any reader may be None when
    it could not be constructed (e.g. no OCR engine): the feed is then
    monitored with the remaining readers and the gap is logged, never
    papered over with a substitute."""

    killfeed: Optional[Any] = None
    alive: Optional[Any] = None
    zone: Optional[Any] = None
    # Phase 29 (W1): POVReader (spectated identity + per-card state).
    # Additive: feeds without it behave exactly as before.
    pov: Optional[Any] = None


#: Builds a fresh reader bundle per feed (per-feed state is mandatory:
#: alive confirmation, zone phase and killfeed voting must never bleed
#: across feeds).
ReaderFactory = Callable[[int, str], FeedReaderBundle]


def default_reader_factory(slot_number: int, source_id: str) -> FeedReaderBundle:
    """Production readers: a NEW KillFeedDetector / AliveCountReader /
    ZoneTimerReader per feed, plus (Phase 29, additive) a POVReader."""
    killfeed = alive = zone = pov = None
    try:
        from .detectors.killfeed import KillFeedDetector

        killfeed = KillFeedDetector()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("monitor_killfeed_unavailable", source_id=source_id,
                    error=str(exc)[:200])
    try:
        from .detectors.hud import AliveCountReader, ZoneTimerReader

        alive = AliveCountReader()
        zone = ZoneTimerReader()
    except Exception as exc:
        log.warning("monitor_hud_readers_unavailable", source_id=source_id,
                    error=str(exc)[:200])
    try:
        from .detectors.pov_reader import POVReader

        pov = POVReader()
    except Exception as exc:
        log.warning("monitor_pov_reader_unavailable", source_id=source_id,
                    error=str(exc)[:200])
    return FeedReaderBundle(killfeed=killfeed, alive=alive, zone=zone, pov=pov)


# ---------------------------------------------------------------------------
# Killfeed candidates + voting
# ---------------------------------------------------------------------------


@dataclass
class _Candidate:
    """One raw (pre-vote) killfeed sighting."""

    key: str
    make_event: Callable[[float], Any]


def _entry_key(entry: Any) -> Optional[str]:
    """Stable dedup key for a raw killfeed sighting of any shape."""
    dedup_key = getattr(entry, "dedup_key", None)
    if callable(dedup_key):
        try:
            return str(dedup_key())
        except Exception:
            pass
    payload = getattr(entry, "payload", None)
    if isinstance(payload, dict) and payload.get("dedup_key"):
        return str(payload["dedup_key"])
    killer = getattr(entry, "killer", None) or getattr(entry, "player", None)
    victim = getattr(entry, "victim", None)
    etype = getattr(entry, "event_type", None)
    etype = getattr(etype, "value", etype)
    if killer and victim:
        return f"{str(killer).strip().lower()}|{str(victim).strip().lower()}|{etype or 'kill'}"
    if isinstance(entry, dict):
        k = entry.get("killer") or entry.get("player")
        v = entry.get("victim")
        if k and v:
            return f"{str(k).strip().lower()}|{str(v).strip().lower()}|{entry.get('event_type') or 'kill'}"
    return None


#: Score threshold a foreign weapon label must clear in the
#: fallback gate — the same 0.55 default ``gated_weapon`` and the
#: production ``IconMatcher`` use. (No margin evidence can rescue a
#: below-threshold score, and no score can rescue a missing margin.)
_FOREIGN_WEAPON_SCORE_THRESHOLD = 0.55


def _finite_float(value: Any) -> Optional[float]:
    """Finite float or None (non-numeric / NaN / +/-inf -> None).

    NaN and infinities are evidence of nothing: they must never
    satisfy a gate comparison, so they fail closed here instead of
    leaking through a ``<`` check that NaN always fails.
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _foreign_weapon_evidence(entry: Any) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Margin evidence carried by a foreign entry, if any.

    Returns ``(family, score, margin)`` where ``family`` is the
    ``weapon_family``/``icon_family`` attribute when it is a string,
    ``score`` is the weapon/icon score (never the OCR ``confidence``,
    which is on a different scale and says nothing about the icon),
    and ``margin`` is ``weapon_margin``/``margin`` when present, else
    ``score - second_score`` from a runner-up pair. Any component may
    be None: missing evidence is reported, never invented.
    """
    family = getattr(entry, "weapon_family", None) or getattr(
        entry, "icon_family", None
    )
    if not isinstance(family, str) or not family.strip():
        family = None
    score: Optional[float] = None
    for attr in ("weapon_confidence", "weapon_score", "icon_score", "score"):
        score = _finite_float(getattr(entry, attr, None))
        if score is not None:
            break
    margin: Optional[float] = None
    for attr in ("weapon_margin", "margin"):
        margin = _finite_float(getattr(entry, attr, None))
        if margin is not None:
            break
    if margin is None and score is not None:
        for attr in ("second_score", "runner_up_score", "second_best_score"):
            second = _finite_float(getattr(entry, attr, None))
            if second is not None:
                margin = score - second
                break
    return family, score, margin


def _gate_foreign_weapon(
    entry: Any,
) -> Tuple[Optional[str], bool, Optional[float], Optional[str], Optional[float]]:
    """Gate a foreign detector's weapon label (Phase 35 M-2).

    Returns ``(weapon, abstained, margin, family, score)`` under the
    SAME contract as Phase 32's ``gated_weapon``: a label ships only
    when margin evidence exists and clears the clamped 0.15 floor
    (and the score clears 0.55). Every other case abstains —
    ``weapon=None`` with ``abstained=True`` when a label was claimed
    — so the abstention flag and the label can never disagree.

    * A foreign ``weapon_abstained`` flag is respected, never
      overridden: mirroring it is necessary but NOT sufficient —
      the label itself is re-checked below.
    * No margin evidence -> abstain rather than trust, however
      confident the foreign detector sounds.
    * Family evidence goes through ``gated_weapon`` itself (score +
      margin, clamped threshold); the gated result must also agree
      with the claimed label, or inconsistent evidence abstains.
    * Without family evidence the claimed label must be exactly a
      class-level weapon (``ar``/``smg``/``sniper``/``dmr``/
      ``frag_grenade``/``pistol``) — a model name (``m416``), a
      family id (``ar_a``), or any other string never ships.
    * A non-string ``weapon`` (e.g. a dict) is a claimed-but-unusable
      label: abstain, never unwrap it into a name.
    * If the gate implementation itself cannot be imported, fail
      closed: abstain. A missing gate must never become a bypass.
    """
    raw = getattr(entry, "weapon", None)
    already_abstained = bool(getattr(entry, "weapon_abstained", False))
    family, score, margin = _foreign_weapon_evidence(entry)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        # No label claimed: nothing to gate. A foreign abstention
        # flag (weapon already None) is preserved as-is.
        return None, already_abstained, margin, None, score
    if not isinstance(raw, str):
        return None, True, margin, None, score
    claimed = raw.strip()
    if already_abstained:
        return None, True, margin, None, score
    try:
        from .detectors.icon_matcher import (
            FAMILY_TO_CLASS,
            MARGIN_THRESHOLD,
            gated_weapon,
        )
    except Exception:
        return None, True, margin, None, score
    if margin is None:
        return None, True, margin, None, score
    if family is not None:
        if score is None:
            return None, True, margin, None, score
        gated = gated_weapon(family, score, score - margin)
        if gated is None or gated != claimed:
            return None, True, margin, None, score
        return gated, False, margin, family, score
    if claimed not in frozenset(FAMILY_TO_CLASS.values()):
        return None, True, margin, None, score
    if score is None or score < _FOREIGN_WEAPON_SCORE_THRESHOLD:
        return None, True, margin, None, score
    if margin < MARGIN_THRESHOLD:
        return None, True, margin, None, score
    return claimed, False, margin, None, score


def _entry_to_event(detector: Any, entry: Any, match_id: Optional[str], now: float):
    """Build the NormalizedEvent for a CONFIRMED entry.

    Prefers the detector's own builder (identical payload contract to
    the primary pipeline); falls back to a manual build with the same
    fields when a foreign detector does not provide one. The fallback
    gates the foreign ``weapon`` through the Phase 32 margin /
    abstention contract (Phase 35 M-2): no margin evidence, or margin
    below the clamped 0.15 floor, yields ``weapon=None`` with
    ``weapon_abstained=True`` — never a verbatim foreign label.
    """
    builder = getattr(detector, "_entry_to_event", None)
    if callable(builder):
        return builder(entry, match_id, now)
    schemas = _feed_schemas()
    etype = getattr(entry, "event_type", "kill")
    feed_type = (
        schemas.FeedEventType.KILL if etype == "kill" else schemas.FeedEventType.KNOCK
    )
    payload = {
        "detector": getattr(detector, "name", "killfeed"),
        "raw_text": getattr(entry, "raw_text", ""),
        "ocr_confidence": round(float(getattr(entry, "confidence", 0.0)), 1),
        "headshot": bool(getattr(entry, "headshot", False)),
        # Integrity C2 (source marker ONLY): icon-derived headshot —
        # never a trusted/persistable headshot source.
        "headshot_source": "vision_icon",
        "dedup_key": _entry_key(entry),
    }
    weapon, abstained, margin, family, score = _gate_foreign_weapon(entry)
    # Phase 35 M-2: the fallback label passes the SAME gated_weapon /
    # abstention contract as Phase 32 — margin < 0.15 (clamped floor)
    # or no margin evidence -> UNKNOWN, never a confident verbatim
    # name. Mirroring a foreign abstention flag alone was the old
    # (ungated) behaviour and is not sufficient: the label itself is
    # re-checked by _gate_foreign_weapon above. Flag and label agree
    # on every path: a dropped label always carries
    # weapon_abstained=True, a shipped label always False. The
    # 0.0 margin below is the absent-evidence default (the
    # KillFeedEntry convention), not a measured margin.
    payload["weapon_abstained"] = abstained
    payload["weapon_margin"] = round(margin, 3) if margin is not None else 0.0
    if weapon is not None and family is not None:
        # Family-evidenced pass: production-shaped provenance, the
        # same keys KillFeedDetector's builder emits for a gated label.
        payload["weapon_family"] = family
        payload["weapon_source"] = "icon_template"
        payload["weapon_granularity"] = "class"
        if score is not None:
            payload["weapon_confidence"] = round(score, 3)
    elif weapon is not None and score is not None:
        payload["weapon_confidence"] = round(score, 3)
    elif abstained:
        # Diagnostic only: which glyph the foreign detector claimed,
        # without ever promoting it to a weapon label.
        claimed_family = getattr(entry, "weapon_family", None) or getattr(
            entry, "icon_family", None
        )
        if isinstance(claimed_family, str) and claimed_family.strip():
            payload["icon_family"] = claimed_family.strip()
    return schemas.NormalizedEvent(
        event_type=feed_type,
        ts=now,
        match_id=match_id,
        player=getattr(entry, "killer", None),
        victim=getattr(entry, "victim", None),
        weapon=weapon,
        # Integrity C2: vision provenance (see feed_adapter/schemas.py).
        source="vision_killfeed",
        payload=payload,
    )


def read_killfeed_candidates(
    detector: Any, image: Any, match_id: Optional[str], now: float
) -> List[_Candidate]:
    """Read RAW (pre-dedup, pre-vote) killfeed sightings from one frame.

    For the production KillFeedDetector this mirrors ``parse_frame``'s
    layout-then-legacy order but deliberately bypasses the detector's
    dedup (``_remember``): the voter needs repeat sightings. Test /
    foreign detectors may instead expose ``read_entries(image)``
    returning entries or ready NormalizedEvents, or plain
    ``parse_frame`` (whose own dedup then applies — documented in the
    module docstring; the voter still gates single-frame flicker from
    detectors without dedup).
    """
    if detector is None or image is None:
        return []

    read_entries = getattr(detector, "read_entries", None)
    if callable(read_entries):
        out: List[_Candidate] = []
        for entry in read_entries(image) or []:
            key = _entry_key(entry)
            if key is None:
                continue
            if hasattr(entry, "payload") and hasattr(entry, "event_type") \
                    and hasattr(entry, "event_id"):
                out.append(_Candidate(key=key, make_event=lambda ts, e=entry: e))
            else:
                out.append(
                    _Candidate(
                        key=key,
                        make_event=lambda ts, e=entry: _entry_to_event(
                            detector, e, match_id, ts
                        ),
                    )
                )
        return out

    if hasattr(detector, "parse_layout_region") and hasattr(detector, "_crop_region"):
        # Production KillFeedDetector raw path (pre-dedup).
        if hasattr(detector, "ocr_available") and not detector.ocr_available():
            return []
        region = detector._crop_region(image)
        if region is None or getattr(region, "size", 0) == 0:
            return []
        frame_h = int(image.shape[0])
        # Band-hash cache (the detector's own protocol, reused read/
        # write exactly as parse_frame does): the killfeed band is
        # static for seconds at a time, so repeat samples of an
        # unchanged band must NOT re-run segmentation + OCR — that
        # cache is what keeps a monitor sample cheap (see the CPU
        # budget note in this module's docstring). Only a CHANGED band
        # pays a full parse.
        cache_key = None
        try:
            cache_key = hashlib.sha1(
                region.tobytes() + str(region.shape).encode()
            ).hexdigest()
        except Exception:
            cache_key = None
        dedup_ttl = 30.0
        try:
            dedup_ttl = float(detector.config.get("dedup_ttl_sec", 30.0))
        except Exception:
            pass
        if (
            cache_key is not None
            and cache_key == getattr(detector, "_band_negative_key", None)
            and now - float(getattr(detector, "_band_negative_at", 0.0)) < dedup_ttl
        ):
            return []
        entries: List[Any] = []
        if (
            cache_key is not None
            and cache_key == getattr(detector, "_band_cache_key", None)
        ):
            entries = list(getattr(detector, "_band_cache_entries", []) or [])
        else:
            try:
                entries = list(detector.parse_layout_region(region, frame_h) or [])
            except Exception:
                entries = []
            if cache_key is not None:
                try:
                    detector._band_cache_key = cache_key
                    detector._band_cache_entries = entries
                except Exception:
                    pass
        if not entries:
            # Legacy action-word fallback, minus dedup (parse_frame's
            # second path): preprocess -> OCR lines -> parse_text with
            # the detector's own confidence gate.
            try:
                pre = detector._preprocess(region)
                if pre is not None:
                    min_conf = float(
                        detector.config.get("min_word_confidence", 30.0)
                    )
                    for text, conf in detector._ocr_lines(pre):
                        if conf < min_conf:
                            continue
                        entry = detector.parse_text(text, confidence=conf)
                        if entry is not None and getattr(entry, "is_valid", False):
                            entries.append(entry)
            except Exception:
                entries = []
            if not entries and cache_key is not None:
                # Both paths read nothing on these exact pixels; the
                # parse is deterministic, so negative-cache them like
                # parse_frame does instead of re-paying the parse on
                # every monitor sample of a static empty band.
                try:
                    detector._band_negative_key = cache_key
                    detector._band_negative_at = now
                except Exception:
                    pass
        out = []
        for entry in entries:
            if entry is None or not getattr(entry, "is_valid", True):
                continue
            key = _entry_key(entry)
            if key is None:
                continue
            out.append(
                _Candidate(
                    key=key,
                    make_event=lambda ts, e=entry: _entry_to_event(
                        detector, e, match_id, ts
                    ),
                )
            )
        return out

    parse_frame = getattr(detector, "parse_frame", None)
    if callable(parse_frame):
        events = parse_frame(image, match_id=match_id, now=now) or []
        out = []
        for ev in events:
            key = _entry_key(ev)
            if key is None:
                continue
            out.append(_Candidate(key=key, make_event=lambda ts, e=ev: e))
        return out
    return []


class KillfeedVoter:
    """2-of-3 multi-frame voting for one feed's killfeed entries.

    Generalises AliveCountReader's confirm-frames pattern from a scalar
    HUD value to a SET of feed entries: every sample (including an
    empty one — a vote against) pushes the set of keys seen; a key
    confirmed in >= ``threshold`` of the last ``window`` samples is
    emitted once and then suppressed for ``ttl_s`` (the entry's
    on-screen lifetime is ~5 s; the TTL mirrors KillFeedDetector's
    30 s dedup so a genuinely repeated identical kill re-arms the same
    way it does on the primary feed).
    """

    def __init__(
        self,
        window: int = 3,
        threshold: int = 2,
        ttl_s: float = 30.0,
        max_keys: int = 512,
    ) -> None:
        self.window = max(1, int(window))
        self.threshold = max(1, min(int(threshold), self.window))
        self.ttl_s = max(1.0, float(ttl_s))
        self.max_keys = max(16, int(max_keys))
        self._samples: deque = deque(maxlen=self.window)
        self._pending: Dict[str, _Candidate] = {}
        self._emitted: Dict[str, float] = {}

    def observe(
        self, candidates: List[_Candidate], now: float
    ) -> List[_Candidate]:
        """Fold one sampled frame's sightings in; return newly
        confirmed candidates (usually empty)."""
        # Expire old emissions FIRST: an expired key also loses its
        # window history, so re-confirmation after the TTL needs fresh
        # sightings — ancient samples must never confirm a "new" kill.
        expired = {
            k for k, t in self._emitted.items() if now - t > self.ttl_s
        }
        for key in expired:
            del self._emitted[key]
        if expired:
            self._samples = deque(
                (s - expired for s in self._samples), maxlen=self.window
            )

        keys_now: Set[str] = set()
        for cand in candidates:
            keys_now.add(cand.key)
            self._pending[cand.key] = cand  # freshest sighting wins
        self._samples.append(keys_now)

        # Purge stale pending entries.
        live_keys: Set[str] = set().union(*self._samples) if self._samples else set()
        for key in list(self._pending):
            if key not in live_keys and key not in self._emitted:
                del self._pending[key]
        while len(self._emitted) > self.max_keys:
            oldest = min(self._emitted, key=lambda k: self._emitted[k])
            del self._emitted[oldest]

        confirmed: List[_Candidate] = []
        for key in keys_now:
            if key in self._emitted:
                continue
            sightings = sum(1 for s in self._samples if key in s)
            if sightings >= self.threshold:
                self._emitted[key] = now
                cand = self._pending.get(key)
                if cand is not None:
                    confirmed.append(cand)
        return confirmed

    def has_pending_unconfirmed(self) -> bool:
        """True when a sighting is parked in the window awaiting its
        confirming second sighting. The Phase 28 pre-filter must never
        skip such a feed: skipping would stall the vote (the window
        only advances on observed samples) and could age the entry off
        screen (~5 s) before it ever confirms."""
        return any(key not in self._emitted for key in self._pending)

    def reset(self) -> None:
        self._samples.clear()
        self._pending.clear()
        self._emitted.clear()


# ---------------------------------------------------------------------------
# Per-feed monitor
# ---------------------------------------------------------------------------


def _image_signature(image: Any) -> Optional[str]:
    """Content signature of a bare ndarray (Phase 29 POV gate): the
    same strided-hash idea as ``_frame_signature``, applied to the
    image directly. None when the image cannot be hashed — the caller
    treats None as 'changed' and reads."""
    try:
        sample = image[::16, ::16]
        digest = hashlib.sha1(sample.tobytes()).hexdigest()[:12]
        return f"{image.shape}:{digest}"
    except Exception:
        return None


def _frame_signature(frame: Any) -> str:
    """Cheap CONTENT signature: a strided pixel hash.

    Content only, deliberately NOT the frame id: a frozen game screen
    (or a wedged capture re-delivering the same pixels) still arrives
    under fresh frame ids, and it is the unchanging content that makes
    the feed stale. Striding by 16 keeps the hash at ~1/256 of the
    frame's pixels — enough to tell frozen from live without paying
    for a full-frame hash on the CPU budget this module protects.
    """
    image = getattr(frame, "image", None)
    if image is None:
        return "no-image"
    try:
        sample = image[::16, ::16]
        digest = hashlib.sha1(sample.tobytes()).hexdigest()[:12]
        return f"{image.shape}:{digest}"
    except Exception:
        return "unhashable"


class FeedMonitor:
    """Cheap-reader monitor for ONE observer feed.

    ``sample_once()`` is synchronous and total: it never raises. All
    failures are recorded in the shared health tracker and surfaced via
    status; the returned list holds only real, voted, slot-stamped
    events (pre cross-feed gate — the service applies that).
    """

    def __init__(
        self,
        spec: FeedSourceSpec,
        readers: FeedReaderBundle,
        health: MultiFeedHealthTracker,
        config: CaptureConfig,
        match_id: Optional[str] = None,
        band_filter: Optional[Any] = None,
        force_parse_every: Optional[int] = None,
        roster_provider: Optional[Any] = None,
    ) -> None:
        self.spec = spec
        self.readers = readers
        self._health = health
        self._config = config
        self._match_id = match_id
        self.enabled = True
        self.voter = KillfeedVoter(
            window=config.monitor_vote_window,
            threshold=config.monitor_vote_threshold,
            ttl_s=config.monitor_dedup_ttl_s,
        )
        self._log = log.bind(source_id=spec.source_id, slot=spec.slot_number)
        # Phase 28 pre-filter state (see _should_parse). band_filter is
        # the service-shared BandChangeFilter (its per-feed state is
        # keyed by feed key); None = filter absent = always parse.
        self._band_filter = band_filter
        self._force_parse_every = (
            resolve_force_parse_every(config)
            if force_parse_every is None
            else int(force_parse_every)
        )
        self._framed_samples = 0  # samples that delivered a frame
        self._samples_parsed = 0
        self._samples_skipped = 0
        self._filter_bypass_pending = 0
        self._filter_bypass_forced = 0
        self._filter_errors = 0
        # Phase 29 (W1): POV state. The tracker exists only when the
        # bundle carries a POV reader; roster binding is injected
        # (slot -> team/roster) and may be None (identity then stays
        # honestly unknown while statuses still flow).
        self._pov_tracker: Optional[Any] = None
        if readers.pov is not None:
            try:
                from app.services.observer_pilot.state import POVStateTracker

                self._pov_tracker = POVStateTracker(
                    spec.slot_number, spec.source_id,
                    roster_provider=roster_provider,
                )
            except Exception as exc:  # pragma: no cover - defensive
                self._log.warning("pov_tracker_unavailable", error=str(exc)[:200])
        self._pov_state: Optional[Any] = None
        self._pov_signature: Optional[str] = None
        self._pov_stale = False
        self._pov_reads = 0
        self._pov_samples_skipped = 0

    # -- one sample ----------------------------------------------------

    def sample_once(self, now: float) -> List[Any]:
        health = self._health
        health.record_sample_start(self.spec.source_id, now)
        started = time.perf_counter()
        try:
            frame = self.spec.frame_provider()
        except Exception as exc:
            health.record_error(
                self.spec.source_id, f"frame_provider: {exc}", now
            )
            self._log.warning("monitor_frame_provider_failed", error=str(exc)[:200])
            return []
        image = getattr(frame, "image", None) if frame is not None else None
        if frame is None or image is None:
            health.record_no_frame(self.spec.source_id, now)
            return []
        health.record_frame(
            self.spec.source_id, _frame_signature(frame), now
        )

        # Phase 28 pre-filter: an unchanged killfeed band cannot
        # contain a NEW killfeed entry, so the expensive killfeed
        # PARSE may be skipped — under the strict contract in
        # _should_parse (pending-vote bypass, forced every-Nth parse,
        # fail-open on any filter problem). The skip gates ONLY the
        # killfeed parse: the filter's signature covers the killfeed
        # band alone, while the alive/zone readers watch DIFFERENT
        # HUD regions, are stateful (confirm/edge semantics), and are
        # cheap — they run on every sample exactly as before, so no
        # alive/zone behaviour can change. A parse-skipped sample does
        # not advance the voter (no killfeed observation was made).
        self._framed_samples += 1
        skip_parse = not self._should_parse(frame, image)
        if skip_parse:
            self._samples_skipped += 1
        else:
            self._samples_parsed += 1

        events: List[Any] = []
        # Each reader is isolated: one throwing never loses the others.
        if not skip_parse:
            try:
                candidates = read_killfeed_candidates(
                    self.readers.killfeed, image, self._match_id, now
                )
                for cand in self.voter.observe(candidates, now):
                    events.append(cand.make_event(now))
            except Exception as exc:
                health.record_reader_error(
                    self.spec.source_id, f"killfeed: {exc}", now
                )
                self._log.warning("monitor_killfeed_failed", error=str(exc)[:200])
        try:
            ev = self._read_alive(image, now)
            if ev is not None:
                events.append(ev)
        except Exception as exc:
            health.record_reader_error(
                self.spec.source_id, f"alive: {exc}", now
            )
            self._log.warning("monitor_alive_failed", error=str(exc)[:200])
        try:
            ev = self._read_zone(image, now)
            if ev is not None:
                events.append(ev)
        except Exception as exc:
            health.record_reader_error(
                self.spec.source_id, f"zone: {exc}", now
            )
            self._log.warning("monitor_zone_failed", error=str(exc)[:200])
        # Phase 29 (W1): POV read. Emits NO bus events -- it maintains
        # this feed's FeedPOVState for the pilot service (pov_state()).
        try:
            self._update_pov(image, now)
        except Exception as exc:
            health.record_reader_error(
                self.spec.source_id, f"pov: {exc}", now
            )
            self._log.warning("monitor_pov_failed", error=str(exc)[:200])

        for event in events:
            self._stamp(event)
        health.record_success(
            self.spec.source_id, now,
            sample_ms=(time.perf_counter() - started) * 1000.0,
        )
        return events

    # -- Phase 28 pre-filter --------------------------------------------

    def _should_parse(self, frame: Any, image: Any) -> bool:
        """BandChangeFilter gate. Returns True (parse) unless ALL of
        the contract's skip conditions hold — every doubt parses:

          1. pending unconfirmed candidates in the voter -> BYPASS
             (always parse; a skip could stall the 2-of-3 vote);
          2. every Nth framed sample (force_parse_every) -> parse
             regardless of what the filter says;
          3. filter missing or raising -> parse (fail-open);
          4. only filter False + rules 1-2 not triggered -> skip.
        """
        filt = self._band_filter
        if filt is None:
            return True
        if self.voter.has_pending_unconfirmed():
            self._filter_bypass_pending += 1
            return True
        every = self._force_parse_every
        if every <= 1 or (self._framed_samples % every == 0):
            self._filter_bypass_forced += 1
            return True
        try:
            return bool(filt.observe(self.spec.source_id, image))
        except Exception:
            # The filter may expect the CapturedFrame rather than the
            # bare ndarray — one retry with the other shape, then
            # fail open. A filter that errors twice never skips.
            try:
                return bool(filt.observe(self.spec.source_id, frame))
            except Exception as exc:
                self._filter_errors += 1
                self._log.warning(
                    "band_change_filter_failed_fail_open", error=str(exc)[:200]
                )
                return True

    def sampler_stats(self) -> Dict[str, Any]:
        """Per-feed Phase 28 counters, merged into status() by the
        service (the health tracker itself is pre-Phase-28 and does
        not know about parse skips)."""
        return {
            "samples_parsed": self._samples_parsed,
            "samples_skipped": self._samples_skipped,
            "prefilter_bypass_pending": self._filter_bypass_pending,
            "prefilter_bypass_forced": self._filter_bypass_forced,
            "prefilter_errors": self._filter_errors,
        }

    # -- Phase 29 POV state ------------------------------------------------

    def _update_pov(self, image: Any, now: float) -> None:
        """Run the POV reader + tracker for this sample.

        Cost gate (same spirit as the Phase 28 band pre-filter): when
        the frame's content signature is unchanged since the last POV
        read, the read is skipped and the cached state is served
        marked stale -- its ``observed_at`` is NOT refreshed, so the
        state's age stays truthful. An unhashable frame always reads.
        """
        reader = self.readers.pov
        if reader is None or self._pov_tracker is None:
            return
        signature = _image_signature(image)
        if (
            signature is not None
            and signature == self._pov_signature
            and self._pov_state is not None
        ):
            self._pov_stale = True
            self._pov_samples_skipped += 1
            return
        raw = reader.read(image, now=now)
        self._pov_state = self._pov_tracker.update(raw, observed_at=now)
        self._pov_signature = signature
        self._pov_stale = False
        self._pov_reads += 1

    def pov_state(self) -> Optional[Any]:
        """Latest FeedPOVState for this feed (None until the first
        successful POV read, or when the bundle has no POV reader).
        A state served from the unchanged-frame cache is a copy with
        ``stale=True`` and the ORIGINAL observed_at."""
        if self._pov_state is None:
            return None
        if self._pov_stale and not self._pov_state.stale:
            from dataclasses import replace

            return replace(self._pov_state, stale=True)
        return self._pov_state

    def pov_stats(self) -> Dict[str, Any]:
        state = self.pov_state()
        return {
            "pov_available": self.readers.pov is not None,
            "pov_reads": self._pov_reads,
            "pov_samples_skipped": self._pov_samples_skipped,
            "pov_stale": bool(state.stale) if state is not None else False,
            "pov_identity_known": (
                bool(state.identity_known) if state is not None else False
            ),
            "pov_spectated_index": (
                state.spectated_index if state is not None else None
            ),
        }

    # -- readers --------------------------------------------------------

    def _read_alive(self, image: Any, now: float) -> Optional[Any]:
        reader = self.readers.alive
        if reader is None:
            return None
        schemas = _feed_schemas()
        poll = getattr(reader, "poll", None)
        if callable(poll):
            update = poll(image)
            if update is None:
                return None
            if isinstance(update, dict):
                players = update.get("players")
                confidence = update.get("confidence")
                engine = update.get("engine")
            else:
                players = getattr(update, "players", None)
                confidence = getattr(update, "confidence", None)
                engine = getattr(update, "engine", "")
            if players is None:
                return None
            return schemas.NormalizedEvent(
                event_type=schemas.FeedEventType.ALIVE_COUNT,
                ts=now,
                match_id=self._match_id,
                payload={
                    "players_alive": players,
                    "confidence": confidence,
                    "ocr_engine": engine,
                    "detector": getattr(reader, "name", "alive_count_reader"),
                },
            )
        detect = getattr(reader, "detect", None)
        if callable(detect):
            result = _run_detector_sync(reader, image)
            change = _extra_change(result, reader, "alive", "change")
            if not change:
                return None
            return schemas.NormalizedEvent(
                event_type=schemas.FeedEventType.ALIVE_COUNT,
                ts=now,
                match_id=self._match_id,
                payload={
                    "players_alive": change.get("players"),
                    "confidence": change.get("confidence"),
                    "ocr_engine": change.get("engine"),
                    "detector": getattr(reader, "name", "alive_count_reader"),
                },
            )
        return None

    def _read_zone(self, image: Any, now: float) -> Optional[Any]:
        reader = self.readers.zone
        if reader is None:
            return None
        poll = getattr(reader, "poll", None)
        if callable(poll):
            try:
                event = poll(image, match_id=self._match_id)
            except TypeError:
                event = poll(image)
            return event  # already a NormalizedEvent (or None)
        detect = getattr(reader, "detect", None)
        if callable(detect):
            result = _run_detector_sync(reader, image)
            schemas = _feed_schemas()
            dumped = _extra_change(result, reader, "zone", "event")
            if dumped:
                return schemas.NormalizedEvent(**dumped)
        return None

    # -- stamping --------------------------------------------------------

    def _stamp(self, event: Any) -> None:
        """Attach this feed's identity. Slot is stamped ONLY from the
        feed spec (the device this monitor reads) — never derived from
        frame content."""
        try:
            payload = getattr(event, "payload", None)
            if isinstance(payload, dict):
                # Force, not setdefault: this sighting physically came
                # from THIS feed, so its slot is the truthful stamp.
                payload["observer_slot"] = self.spec.slot_number
                payload["source_id"] = self.spec.source_id
                payload["vision_monitor"] = "multi_feed"
            if self._match_id and not getattr(event, "match_id", None):
                event.match_id = self._match_id
        except Exception:  # pragma: no cover - foreign event shapes
            pass


def _run_detector_sync(detector: Any, image: Any) -> Any:
    """Run a BaseDetector-style async detect() from a worker thread
    (same approach as FramePipeline._run_detector_sync)."""
    import asyncio as _asyncio

    coro = detector.detect(image)
    if _asyncio.iscoroutine(coro):
        return _asyncio.run(coro)
    return coro


def _extra_change(result: Any, reader: Any, section: str, key: str) -> Any:
    """Pull ``extra[section][key]`` out of a DetectionResult-shaped
    value (object with extra_data or its to_dict() form)."""
    extra = getattr(result, "extra_data", None)
    if extra is None:
        to_dict = getattr(result, "to_dict", None)
        if callable(to_dict):
            try:
                extra = (to_dict() or {}).get("extra") or {}
            except Exception:
                extra = {}
    if not isinstance(extra, dict):
        return None
    section_data = extra.get(section) or {}
    if isinstance(section_data, dict):
        return section_data.get(key)
    return None


# ---------------------------------------------------------------------------
# Multi-feed service
# ---------------------------------------------------------------------------


class MultiFeedMonitorService:
    """Round-robin scheduler + cross-feed gate for all feed monitors.

    Lifecycle mirrors the other AEGIS services: construct (cheap, no
    I/O), ``await start()``, ``await stop()``. Feeds can be added /
    removed / enabled / disabled at any time; a feed added while the
    service runs joins the rotation on the next tick.
    """

    def __init__(
        self,
        ingest: IngestFn,
        config: Optional[CaptureConfig] = None,
        match_id: Optional[str] = None,
        detector_factory: Optional[ReaderFactory] = None,
        clock: Callable[[], float] = time.time,
        band_filter: Any = None,
        roster_provider: Optional[Any] = None,
    ) -> None:
        self._ingest = ingest
        self.config = config or load_capture_config()
        self._match_id = match_id
        self._factory = detector_factory or default_reader_factory
        self._clock = clock
        # Phase 29 (W1): slot -> (team_id, roster) binding for the
        # per-feed POV trackers; None = POV identity stays unknown.
        self._roster_provider = roster_provider
        # Phase 28: shared band-change pre-filter (guarded import;
        # None = module absent/disabled = every sample parses). An
        # explicitly passed instance (tests / coordinator) wins.
        self._band_filter = (
            band_filter if band_filter is not None else _load_band_filter()
        )
        # Phase 28: bounded sampler pool, created lazily on the first
        # parallel tick and shut down in stop(). Size is resolved per
        # tick from the CURRENT fleet (see resolve_sampler_workers).
        self._sampler_executor: Optional[ThreadPoolExecutor] = None
        self._sampler_executor_size = 0
        self.health = MultiFeedHealthTracker(
            stale_after_s=self.config.monitor_stale_after_s,
            no_signal_after_s=self.config.monitor_no_signal_after_s,
        )
        self._feeds: Dict[str, FeedMonitor] = {}
        self._order: List[str] = []
        self._rr_index = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._started_at: Optional[float] = None
        # Cross-feed gate state (see module docstring).
        self._kill_emitted: Dict[str, float] = {}
        self._alive_last: Optional[int] = None
        self._zone_last_phase = 0
        # Grabber auto-sync (builder-attached): source ids we registered
        # ourselves, so removals never touch explicitly-added feeds.
        self._grabber: Any = None
        self._grabber_slots: Dict[str, int] = {}
        self._auto_sources: Set[str] = set()
        self._excluded_slots: Set[int] = set()
        # Honest counters.
        self._events_emitted = 0
        self._duplicates_suppressed = 0
        self._ingest_errors = 0
        self._last_error = ""

    # -- feed management -------------------------------------------------

    def add_feed(self, spec: FeedSourceSpec) -> FeedMonitor:
        """Register (or replace) one feed's monitor. Fresh readers and
        a fresh voter are built per feed — state never carries over."""
        if spec.slot_number in self._excluded_slots:
            log.info("monitor_feed_excluded", source_id=spec.source_id,
                     slot=spec.slot_number, reason="excluded slot (primary feed)")
            raise ValueError(
                f"slot {spec.slot_number} is excluded from monitoring"
            )
        existing = self._feeds.get(spec.source_id)
        if existing is not None:
            self.remove_feed(spec.source_id)
        readers = self._factory(spec.slot_number, spec.source_id)
        monitor = FeedMonitor(
            spec=spec, readers=readers, health=self.health,
            config=self.config, match_id=self._match_id,
            band_filter=self._band_filter,
            roster_provider=self._roster_provider,
        )
        self._feeds[spec.source_id] = monitor
        self._order.append(spec.source_id)
        self.health.register_feed(spec.source_id, spec.slot_number)
        self.health.set_running(
            spec.source_id, self._running and monitor.enabled
        )
        log.info("monitor_feed_added", source_id=spec.source_id,
                 slot=spec.slot_number)
        return monitor

    def remove_feed(self, source_id: str) -> bool:
        monitor = self._feeds.pop(source_id, None)
        if monitor is None:
            return False
        self._order = [s for s in self._order if s != source_id]
        self._auto_sources.discard(source_id)
        self.health.unregister_feed(source_id)
        log.info("monitor_feed_removed", source_id=source_id)
        return True

    def set_feed_enabled(self, source_id: str, enabled: bool) -> bool:
        """Per-feed start/stop: a disabled feed is skipped by the
        scheduler and reports state "stopped"."""
        monitor = self._feeds.get(source_id)
        if monitor is None:
            return False
        monitor.enabled = bool(enabled)
        if not enabled:
            monitor.voter.reset()
        self.health.set_running(
            source_id, self._running and monitor.enabled
        )
        return True

    def get_feed(self, source_id: str) -> Optional[FeedMonitor]:
        return self._feeds.get(source_id)

    # -- Phase 29 POV access (W4 pilot service reads through here) -------

    def pov_state(self, slot_number: int) -> Optional[Any]:
        """Latest FeedPOVState for the feed on ``slot_number``, or
        None when the slot is not monitored or has not been read yet."""
        for monitor in self._feeds.values():
            if monitor.spec.slot_number == int(slot_number):
                return monitor.pov_state()
        return None

    def pov_states(self) -> Dict[int, Any]:
        """slot_number -> FeedPOVState for every feed that has one."""
        out: Dict[int, Any] = {}
        for monitor in self._feeds.values():
            state = monitor.pov_state()
            if state is not None:
                out[monitor.spec.slot_number] = state
        return out

    @property
    def feed_source_ids(self) -> List[str]:
        return list(self._order)

    def exclude_slots(self, slots: Set[int]) -> None:
        self._excluded_slots |= {int(s) for s in slots}

    # -- grabber sync ------------------------------------------------------

    def attach_grabber(
        self, grabber: Any, slot_by_source: Optional[Dict[str, int]] = None
    ) -> None:
        """Attach a FrameGrabber for automatic feed discovery.

        Sources are adopted when their slot is known — from
        ``slot_by_source`` or the ``aegis-observer-slot-NN`` source-id
        convention. A source with no derivable slot is skipped and
        logged: monitoring it would emit events with a guessed slot.
        """
        self._grabber = grabber
        if slot_by_source:
            self._grabber_slots.update(slot_by_source)

    def sync_from_grabber(self) -> Dict[str, Any]:
        """Adopt new grabber sources / drop vanished auto-adopted ones."""
        if self._grabber is None:
            return {"adopted": [], "dropped": []}
        captures = getattr(self._grabber, "captures", {}) or {}
        adopted: List[str] = []
        for source_id in list(captures.keys()):
            if source_id in self._feeds:
                continue
            slot = self._grabber_slots.get(source_id) or slot_from_source_id(source_id)
            if slot is None or slot in self._excluded_slots:
                continue
            grabber = self._grabber
            spec = FeedSourceSpec(
                slot_number=slot,
                source_id=source_id,
                frame_provider=lambda sid=source_id, g=grabber: g.get_latest_frame(sid),
            )
            try:
                self.add_feed(spec)
            except ValueError:
                continue
            self._auto_sources.add(source_id)
            adopted.append(source_id)
        dropped: List[str] = []
        for source_id in list(self._auto_sources):
            if source_id not in captures:
                self.remove_feed(source_id)
                dropped.append(source_id)
        return {"adopted": adopted, "dropped": dropped}

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        if not self.config.monitor_enabled:
            log.warning("multi_feed_monitor_disabled_by_config")
            return
        self.sync_from_grabber()
        self._running = True
        self._started_at = self._clock()
        for source_id, monitor in self._feeds.items():
            self.health.set_running(source_id, monitor.enabled)
        self._task = asyncio.create_task(self._loop(), name="aegis-multi-feed-monitor")
        log.info("multi_feed_monitor_started", feeds=len(self._feeds),
                 tick_s=self.config.monitor_tick_s,
                 batch=self.config.monitor_batch_size)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._sampler_executor is not None:
            # wait=False: in-flight samples finish on their threads;
            # their results are simply never emitted after stop.
            try:
                self._sampler_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._sampler_executor = None
            self._sampler_executor_size = 0
        for source_id in self._feeds:
            self.health.set_running(source_id, False)
        log.info("multi_feed_monitor_stopped")

    async def _loop(self) -> None:
        while self._running:
            tick_started = time.perf_counter()
            try:
                self.sync_from_grabber()
                await self.run_tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # the scheduler itself never dies
                self._last_error = str(exc)[:300]
                log.error("multi_feed_monitor_tick_failed", error=self._last_error)
            elapsed = time.perf_counter() - tick_started
            await asyncio.sleep(
                max(0.0, float(self.config.monitor_tick_s) - elapsed)
            )

    # -- sampling ------------------------------------------------------------

    def _sampler_workers(self, for_count: int) -> int:
        return resolve_sampler_workers(self.config, for_count)

    def _get_sampler_executor(self, workers: int) -> ThreadPoolExecutor:
        """The bounded sampler pool, (re)created when the resolved
        size changes. Threads are daemon-equivalent in practice:
        stop() shuts the pool down; idle workers never hold work."""
        if (
            self._sampler_executor is not None
            and self._sampler_executor_size == workers
        ):
            return self._sampler_executor
        if self._sampler_executor is not None:
            try:
                self._sampler_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        self._sampler_executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="aegis-monitor-sampler"
        )
        self._sampler_executor_size = workers
        return self._sampler_executor

    async def _sample_raw(
        self, source_id: str, workers: int
    ) -> Tuple[List[Any], float]:
        """Run ONE feed's ``sample_once`` off the event loop; return
        (raw events, sample timestamp). Never raises: a failing feed
        yields [] and records its failure in health.

        Thread-safety of running these concurrently (workers > 1):
        each feed's sample touches ONLY that feed's objects — its
        detector (incl. its band cache and IconMatcher), voter,
        alive/zone readers and its OWN record in the health tracker
        (per-feed MonitorFeedHealth objects; the tracker holds no
        cross-feed mutable counters). The shared service state
        (cross-feed gate, RR index, event counters) is mutated only
        on the event loop, in _emit_events / run_tick.
        """
        monitor = self._feeds.get(source_id)
        now = self._clock()
        if monitor is None or not monitor.enabled:
            return [], now
        # Sampling a feed means its monitor is running (start() also
        # sets this for the whole fleet; this covers direct drives).
        self.health.set_running(source_id, True)
        try:
            if workers <= 1:
                raw_events = await asyncio.to_thread(monitor.sample_once, now)
            else:
                loop = asyncio.get_running_loop()
                raw_events = await loop.run_in_executor(
                    self._get_sampler_executor(workers), monitor.sample_once, now
                )
        except Exception as exc:  # sample_once is total; this is a belt
            self.health.record_error(source_id, f"sample: {exc}", now)
            self._last_error = str(exc)[:300]
            return [], now
        return raw_events, now

    async def _emit_events(
        self, source_id: str, raw_events: List[Any], now: float
    ) -> List[Any]:
        """Cross-feed gate + ingest for one feed's raw events. Runs
        ONLY on the event loop, so gate state needs no locks."""
        emitted: List[Any] = []
        for event in raw_events:
            if not self._cross_feed_gate(event, now):
                self._duplicates_suppressed += 1
                self.health.record_events(source_id, 0, suppressed=1)
                continue
            try:
                await self._ingest(event)
                self._events_emitted += 1
                self.health.record_events(source_id, 1)
                emitted.append(event)
            except Exception as exc:
                self._ingest_errors += 1
                self._last_error = str(exc)[:300]
                log.error("monitor_ingest_failed", source_id=source_id,
                          error=self._last_error)
        return emitted

    async def run_tick(self) -> int:
        """Sample the next round-robin batch of enabled feeds.

        Returns how many feeds were sampled. Public so tests and the
        coordinator can drive the monitor deterministically.

        Phase 28: the batch is SELECTED in the exact pre-Phase-28
        round-robin order (a skipped-by-prefilter feed still consumes
        its turn — it just costs no parse). With > 1 resolved worker
        the batch's samples run concurrently on the sampler pool;
        the cross-feed gate + ingest then run serially in that same
        round-robin order, so which feed wins a cross-feed duplicate
        (and therefore the slot stamped downstream) is identical to
        the sequential scheduler — completion order never decides
        attribution.
        """
        enabled = [
            sid for sid in self._order
            if sid in self._feeds and self._feeds[sid].enabled
        ]
        if not enabled:
            return 0
        batch = max(1, int(self.config.monitor_batch_size))
        count = min(batch, len(enabled))
        batch_ids: List[str] = []
        for _ in range(count):
            source_id = enabled[self._rr_index % len(enabled)]
            self._rr_index = (self._rr_index + 1) % max(1, len(enabled))
            batch_ids.append(source_id)
        workers = self._sampler_workers(len(batch_ids))
        if workers <= 1 or len(batch_ids) == 1:
            for source_id in batch_ids:
                await self.sample_feed_once(source_id)
            return len(batch_ids)
        results = await asyncio.gather(
            *(self._sample_raw(sid, workers) for sid in batch_ids)
        )
        for source_id, (raw_events, now) in zip(batch_ids, results):
            await self._emit_events(source_id, raw_events, now)
        return len(batch_ids)

    async def sample_feed_once(self, source_id: str) -> List[Any]:
        """Sample one feed NOW (vote -> cross-feed gate -> ingest).

        Returns the events actually emitted to ingest. A failing feed
        returns [] and records its failure in health — it never raises
        into the scheduler or other feeds.
        """
        monitor = self._feeds.get(source_id)
        if monitor is None or not monitor.enabled:
            return []
        enabled_count = sum(1 for m in self._feeds.values() if m.enabled)
        workers = self._sampler_workers(max(1, enabled_count))
        raw_events, now = await self._sample_raw(source_id, workers)
        return await self._emit_events(source_id, raw_events, now)

    # -- cross-feed gate ------------------------------------------------------

    def _cross_feed_gate(self, event: Any, now: float) -> bool:
        """True when this event is the first copy of its fact.

        Kill/knock: shared dedup key + TTL (first confirming feed
        wins). Alive count: match-global value, emit only on change vs
        the last value ANY feed emitted. Zone timer: match-global and
        monotonic — emit only when the phase advances past the last
        phase any feed emitted. Everything else passes.
        """
        etype = getattr(event, "event_type", None)
        etype = getattr(etype, "value", etype)
        payload = getattr(event, "payload", None) or {}
        if etype in ("kill", "knock"):
            key = payload.get("dedup_key") or _entry_key(event)
            if key is None:
                return True
            ttl = float(self.config.monitor_dedup_ttl_s)
            self._kill_emitted = {
                k: t for k, t in self._kill_emitted.items() if now - t <= ttl
            }
            if key in self._kill_emitted:
                return False
            self._kill_emitted[key] = now
            return True
        if etype == "alive_count":
            value = payload.get("players_alive")
            if value is None:
                return True
            try:
                value = int(value)
            except (TypeError, ValueError):
                return True
            if value == self._alive_last:
                return False
            self._alive_last = value
            return True
        if etype == "zone_update":
            phase = payload.get("zone_phase")
            if phase is None:
                return True
            try:
                phase = int(phase)
            except (TypeError, ValueError):
                return True
            if phase <= self._zone_last_phase:
                return False
            self._zone_last_phase = phase
            return True
        return True

    # -- status ----------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        now = self._clock()
        enabled = sum(1 for m in self._feeds.values() if m.enabled)
        uptime = (now - self._started_at) if self._started_at else 0.0
        # Phase 28: per-feed parse/skip split lives on the FeedMonitors
        # (the health tracker predates the pre-filter); merge it into
        # the health view so samples vs skips are visible per feed.
        feeds_health = self.health.get_all_health(now)
        totals = {
            "samples_parsed": 0,
            "samples_skipped": 0,
            "prefilter_bypass_pending": 0,
            "prefilter_bypass_forced": 0,
            "prefilter_errors": 0,
        }
        for source_id, monitor in self._feeds.items():
            stats = monitor.sampler_stats()
            for key in totals:
                totals[key] += stats[key]
            rec = feeds_health.get(source_id)
            if rec is not None:
                rec.update(stats)
                rec.update(monitor.pov_stats())
        return {
            "running": self._running,
            "enabled_by_config": bool(self.config.monitor_enabled),
            "match_id": self._match_id,
            "uptime_s": round(uptime, 1),
            "feeds_total": len(self._feeds),
            "feeds_enabled": enabled,
            "budget": self.config.monitor_budget_summary(enabled),
            "sampler": {
                "mode": resolve_sampler_mode(self.config),
                "cpu_count": os.cpu_count() or 1,
                "workers_effective": self._sampler_workers(max(1, enabled)),
                "workers_cap": _cfg_int(
                    self.config,
                    "monitor_sampler_max_workers",
                    "AEGIS_MONITOR_SAMPLER_MAX_WORKERS",
                    8,
                ),
                "workers_explicit": _cfg_int(
                    self.config,
                    "monitor_sampler_workers",
                    "AEGIS_MONITOR_SAMPLER_WORKERS",
                    0,
                )
                or None,
                "pool_size": self._sampler_executor_size,
                "force_parse_every": resolve_force_parse_every(self.config),
                "band_filter_available": self._band_filter is not None,
                **totals,
            },
            "cross_feed": {
                "kill_keys_tracked": len(self._kill_emitted),
                "duplicates_suppressed": self._duplicates_suppressed,
                "alive_last_emitted": self._alive_last,
                "zone_last_phase_emitted": self._zone_last_phase,
            },
            "events_emitted": self._events_emitted,
            "ingest_errors": self._ingest_errors,
            "last_error": self._last_error,
            "health_summary": self.health.summary(now),
            "feeds": feeds_health,
        }


# ---------------------------------------------------------------------------
# Builder (production wiring — coordinator calls this from the lifespan)
# ---------------------------------------------------------------------------


def build_multi_feed_monitor(
    ingest: IngestFn,
    *,
    config: Optional[CaptureConfig] = None,
    match_id: Optional[str] = None,
    feeds: Optional[List[FeedSourceSpec]] = None,
    frame_grabber: Any = None,
    multi_device_manager: Any = None,
    exclude_slots: Optional[Set[int]] = None,
    slot_by_source: Optional[Dict[str, int]] = None,
    detector_factory: Optional[ReaderFactory] = None,
    clock: Callable[[], float] = time.time,
    roster_provider: Optional[Any] = None,
) -> MultiFeedMonitorService:
    """Build the multi-feed monitor service (does NOT start it).

    Sources, in order:
      * ``feeds`` — explicit FeedSourceSpec list (rigs/tests).
      * ``multi_device_manager`` — adopt every mapped observer slot's
        capture source from the manager's shared FrameGrabber.
      * ``frame_grabber`` — adopt sources whose slot is derivable
        (``slot_by_source`` or the aegis-observer-slot-NN convention).

    ``exclude_slots`` (typically the primary/on-air slot, whose full
    pipeline already emits the cheap readers' events) are never
    monitored — that is what prevents double emission when the primary
    feed is also one of the 16.
    """
    cfg = config or load_capture_config()
    service = MultiFeedMonitorService(
        ingest=ingest, config=cfg, match_id=match_id,
        detector_factory=detector_factory, clock=clock,
        roster_provider=roster_provider,
    )
    if exclude_slots:
        service.exclude_slots(set(exclude_slots))
    for spec in feeds or []:
        if spec.slot_number in service._excluded_slots:
            log.info("monitor_feed_skipped_excluded", source_id=spec.source_id,
                     slot=spec.slot_number)
            continue
        service.add_feed(spec)
    grabber = frame_grabber
    slots_map: Dict[str, int] = dict(slot_by_source or {})
    if multi_device_manager is not None:
        grabber = grabber or getattr(multi_device_manager, "_grabber", None)
        manager_slots = getattr(multi_device_manager, "_slots", {}) or {}
        for slot_number, slot in manager_slots.items():
            source_id = getattr(slot, "source_id", None)
            if source_id and getattr(slot, "serial", ""):
                slots_map[source_id] = int(slot_number)
    if grabber is not None:
        service.attach_grabber(grabber, slots_map)
        service.sync_from_grabber()
    return service


__all__ = [
    "FeedMonitor",
    "FeedReaderBundle",
    "FeedSourceSpec",
    "KillfeedVoter",
    "MultiFeedMonitorService",
    "build_multi_feed_monitor",
    "default_reader_factory",
    "read_killfeed_candidates",
    "resolve_force_parse_every",
    "resolve_sampler_mode",
    "resolve_sampler_workers",
    "slot_from_source_id",
]
