# backend/app/services/director_learning/knobs.py
"""Learnable-knob registry + effective-value provider (Phase 27, W3).

This is transparent, bounded rule-tuning — NOT machine learning. A
small, fixed set of LiveDirector numbers (the *knobs*) may be nudged
by the learning engine (``engine.py``) inside hard registry bounds,
from labelled outcomes and human feedback. Every change is a row in
``director_learning_adjustments`` with plain-language evidence, and
every change can be rolled back exactly.

Two profiles (Phase 27 clone-mode addition)
-------------------------------------------
The provider carries a *profile dimension*:

* ``learned=False`` — the FACTORY profile: registry defaults, always.
  ``full_auto``, ``semi_auto`` and the manual shadow resolve with
  ``learned=False`` forever, so the factory director never changes.
* ``learned=True`` — the LEARNED profile: the ``after_value`` of the
  latest ``applied`` adjustment per knob. It drives ONLY ``clone``
  mode, and only while the learning mode is not ``off``. With no
  applied adjustment row, the learned profile IS the defaults by
  construction ("profile exists" means >= 1 applied row).

Grammar / honesty floors are NOT knobs. Min fight holds, the switch
cooldown, the per-minute switch cap, the storyline multiplier cap
(1.35), final-zone locks, staleness windows, score caps and the
context-beat settings are deliberately absent from the registry, and
:func:`register_knob` / :func:`ensure_adjustable` RAISE on any attempt
to register or adjust a forbidden name — the guard is code, not
convention (W5 attacks exactly this).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import structlog

logger = structlog.get_logger("aegis.director_learning.knobs")


@dataclass(frozen=True)
class KnobSpec:
    """One learnable knob: default, hard bounds, step, unit, why."""

    name: str
    default: float
    min_value: float
    max_value: float
    step: float
    unit: str
    why: str


# ── the registry — EXACTLY these knobs, no more ───────────────────

_KNOBS = (
    KnobSpec(
        "margin_default", 15.0, 8.0, 30.0, 1.0, "points",
        "Absolute score margin a challenger needs over the incumbent "
        "in the ordinary case (LiveDirector MARGIN_DEFAULT).",
    ),
    KnobSpec(
        "margin_fight_hold", 40.0, 25.0, 60.0, 1.0, "points",
        "Margin required to break the phase fight-hold window "
        "(MARGIN_FIGHT_HOLD). The hold DURATION is grammar — not a knob.",
    ),
    KnobSpec(
        "margin_stale_incumbent", 10.0, 5.0, 25.0, 1.0, "points",
        "Margin required when the incumbent is stale "
        "(MARGIN_STALE_INCUMBENT). The staleness windows are not knobs.",
    ),
    KnobSpec(
        "margin_clutch", 5.0, 2.0, 15.0, 1.0, "points",
        "Margin required when the challenger's fight is a verified "
        "clutch (MARGIN_CLUTCH_CHALLENGER).",
    ),
    KnobSpec(
        "storyline_rank1_uplift", 0.10, 0.0, 0.20, 0.02, "multiplier uplift",
        "Storyline W uplift for the live-standings rank-1 team.",
    ),
    KnobSpec(
        "storyline_rank2_uplift", 0.06, 0.0, 0.15, 0.02, "multiplier uplift",
        "Storyline W uplift for the live-standings rank-2 team.",
    ),
    KnobSpec(
        "storyline_match_point_uplift", 0.10, 0.0, 0.20, 0.02,
        "multiplier uplift",
        "Storyline W uplift for a team on match point (Smash status).",
    ),
    KnobSpec(
        "storyline_hot_streak_uplift", 0.08, 0.0, 0.16, 0.02,
        "multiplier uplift",
        "Storyline W uplift for a team on a live-kill-stream hot streak.",
    ),
    KnobSpec(
        "engagement_precombat_points", 15.0, 8.0, 25.0, 1.0, "points",
        "Precombat score term for a team in PRE_COMBAT engagement state.",
    ),
    KnobSpec(
        "precombat_confidence_enter", 0.50, 0.35, 0.70, 0.05, "confidence",
        "PotentialFight confidence at/above which PRE_COMBAT is entered.",
    ),
    KnobSpec(
        "drop_clash_watch_points", 10.0, 5.0, 20.0, 1.0, "points",
        "Watch/precombat weight a predicted drop-clash team carries.",
    ),
)

KNOB_REGISTRY: Dict[str, KnobSpec] = {spec.name: spec for spec in _KNOBS}

#: Registry defaults — identical to today's LiveDirector constants /
#: LiveDirectorConfig field defaults (byte-identical factory profile).
KNOB_DEFAULTS: Dict[str, float] = {
    name: spec.default for name, spec in KNOB_REGISTRY.items()
}


# ── forbidden names (grammar / honesty floors — NEVER learnable) ──

#: Exact forbidden names (normalised lower-case).
FORBIDDEN_KNOBS = frozenset(
    {
        "min_fight_hold", "min_fight_hold_s", "min_fight_holds",
        "switch_cooldown_s", "switch_cooldown", "max_switches_per_minute",
        "storyline_max_multiplier", "max_multiplier",
        "final_zone_top3_max_teams", "final_zone_top5_max_teams",
        "final_zone_stability_s", "final_circle_lock",
        "stale_after_fight_s", "stale_after_other_s", "stale_decay_per_s",
        "precombat_cap", "heat_cap", "continuity_cap", "zone_pressure_cap",
        "context_beat_enabled", "context_beat_duration_s",
        "context_beat_scene", "clone_grab_inactivity_s",
        "on_air_base", "fight_resolve_idle_s", "intel_ttl_s",
    }
)

#: Substring patterns that make ANY name forbidden (caps generally,
#: cooldowns, hold timers, final-zone machinery, context-beat, grab).
_FORBIDDEN_PATTERNS = (
    "min_fight_hold", "fight_hold_s", "cooldown", "max_switches",
    "max_multiplier", "final_zone", "final_circle_lock", "stale_after",
    "stale_decay", "_cap", "cap_", "context_beat", "clone_grab",
    "stability_s", "hard_lock",
)


class ForbiddenKnobError(ValueError):
    """Raised when code tries to register/adjust a grammar floor."""


def is_forbidden(name: str) -> bool:
    normalised = (name or "").strip().lower()
    if not normalised:
        return True
    if normalised in FORBIDDEN_KNOBS:
        return True
    return any(pat in normalised for pat in _FORBIDDEN_PATTERNS)


def ensure_adjustable(name: str) -> KnobSpec:
    """Return the spec for ``name`` or raise.

    Raises :class:`ForbiddenKnobError` for grammar floors and
    :class:`KeyError` for names that simply are not knobs.
    """
    if is_forbidden(name):
        raise ForbiddenKnobError(
            f"{name!r} is a broadcast-grammar / honesty floor and is "
            "NOT a learnable knob; it can never be registered or adjusted."
        )
    spec = KNOB_REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"Unknown knob {name!r}: not in the registry.")
    return spec


def register_knob(spec: KnobSpec) -> KnobSpec:
    """Register a knob at runtime — forbidden names RAISE.

    The shipped registry is fixed; this exists so the guard is
    exercised by any future registration path (and by tests).
    """
    ensure_not_forbidden = is_forbidden(spec.name)
    if ensure_not_forbidden:
        raise ForbiddenKnobError(
            f"Refusing to register {spec.name!r}: grammar / honesty "
            "floors are not learnable knobs."
        )
    if spec.name in KNOB_REGISTRY:
        raise ValueError(f"Knob {spec.name!r} is already registered.")
    if spec.min_value > spec.default or spec.default > spec.max_value:
        raise ValueError(f"Knob {spec.name!r}: default outside bounds.")
    KNOB_REGISTRY[spec.name] = spec
    KNOB_DEFAULTS[spec.name] = spec.default
    return spec


def clamp_value(knob: str, value: float) -> float:
    """Clamp ``value`` to the knob's registry bounds (step-grid clean)."""
    spec = ensure_adjustable(knob)
    clamped = max(spec.min_value, min(spec.max_value, float(value)))
    # Kill float dust from uplift arithmetic (0.1 - 0.02 etc.).
    return round(clamped, 4)


def default_value(knob: str) -> float:
    return ensure_adjustable(knob).default


# ── the provider ──────────────────────────────────────────────────


def _default_session_factory():
    from app.db.base import AsyncSessionLocal

    return AsyncSessionLocal


class KnobProvider:
    """Effective-value provider with an explicit cache + refresh().

    ``refresh()`` (async) reloads the learning mode and the latest
    ``applied`` adjustment per knob from the DB. ``effective()`` is
    synchronous and reads only the cache, so the LiveDirector's
    per-evaluation reads never touch the DB. A failed refresh keeps
    the previous cache (failure-isolated; before the first successful
    refresh the cache is empty and every read is the default).
    """

    def __init__(
        self, session_factory: Optional[Callable[[], Any]] = None
    ) -> None:
        self._session_factory = session_factory
        self._learned: Dict[str, float] = {}
        self._learning_mode: str = "suggest"
        self._applied_count: int = 0
        self.refresh_count: int = 0

    # ── cache lifecycle ──────────────────────────────────────────

    def _factory(self):
        if self._session_factory is not None:
            return self._session_factory
        return _default_session_factory()

    async def refresh(self) -> None:
        """Reload learning mode + applied adjustments into the cache."""
        from sqlalchemy import select

        from .models import (
            LEARNING_STATE_ID,
            DirectorLearningAdjustment,
            DirectorLearningState,
        )

        try:
            async with self._factory()() as session:
                state = await session.get(
                    DirectorLearningState, LEARNING_STATE_ID
                )
                mode = state.learning_mode if state is not None else "suggest"
                rows = (
                    await session.execute(
                        select(DirectorLearningAdjustment)
                        .where(
                            DirectorLearningAdjustment.status == "applied"
                        )
                        .order_by(DirectorLearningAdjustment.ts.asc())
                    )
                ).scalars().all()
            learned: Dict[str, float] = {}
            for row in rows:
                if row.knob in KNOB_REGISTRY:
                    # Clamp on read (W5 gate D2): a value written
                    # outside the engine/API paths must never be
                    # served beyond the registry bounds.
                    learned[row.knob] = clamp_value(
                        row.knob, float(row.after_value)
                    )
            self._learned = learned
            self._learning_mode = (mode or "suggest").strip().lower()
            self._applied_count = len(rows)
            self.refresh_count += 1
        except Exception as exc:  # failure-isolated: keep prior cache
            logger.warning("knob_provider_refresh_failed", error=str(exc)[:200])

    def prime_cache(
        self,
        learned: Optional[Dict[str, float]] = None,
        learning_mode: str = "suggest",
    ) -> None:
        """Set the cache directly (tests / builder warm-up)."""
        self._learned = {
            k: clamp_value(k, v)
            for k, v in (learned or {}).items()
            if k in KNOB_REGISTRY
        }
        self._learning_mode = (learning_mode or "suggest").strip().lower()
        self._applied_count = len(self._learned)

    # ── reads ────────────────────────────────────────────────────

    @property
    def learning_mode(self) -> str:
        return self._learning_mode

    def has_profile(self) -> bool:
        """'Profile exists' == >= 1 applied adjustment row."""
        return self._applied_count > 0

    def effective(self, knob: str, learned: bool = True) -> float:
        """Effective value of ``knob``.

        ``learned=False`` (factory profile) => registry default,
        ALWAYS. ``learned=True`` => latest applied value, except when
        the learning mode is ``off`` (adjustments dormant) => default.
        """
        spec = ensure_adjustable(knob)
        if not learned:
            return spec.default
        if self._learning_mode == "off":
            return spec.default
        return float(self._learned.get(knob, spec.default))

    def effective_all(self, learned: bool = True) -> Dict[str, float]:
        return {name: self.effective(name, learned=learned) for name in KNOB_REGISTRY}

    def defaults_all(self) -> Dict[str, float]:
        return dict(KNOB_DEFAULTS)

    def learned_snapshot(self) -> Dict[str, Any]:
        return {
            "learning_mode": self._learning_mode,
            "has_profile": self.has_profile(),
            "applied_count": self._applied_count,
            "learned": dict(self._learned),
            "effective_learned": self.effective_all(learned=True),
            "effective_factory": self.effective_all(learned=False),
        }


# ── module-level "current provider" (mirrors W1's sink pattern) ───

_current_provider: Optional[KnobProvider] = None


def register_provider(provider: Optional[KnobProvider]) -> None:
    global _current_provider
    _current_provider = provider


# Alias matching the journal's set_* naming.
set_provider = register_provider


def get_provider() -> Optional[KnobProvider]:
    """The registered provider, or None (=> static defaults)."""
    return _current_provider


def clear_provider() -> None:
    global _current_provider
    _current_provider = None


def effective(knob: str, learned: bool = True) -> float:
    """Module-level read: provider value, else the registry default."""
    ensure_adjustable(knob)
    provider = _current_provider
    if provider is None:
        return KNOB_DEFAULTS[knob]
    return provider.effective(knob, learned=learned)


def effective_all(learned: bool = True) -> Dict[str, float]:
    provider = _current_provider
    if provider is None:
        return dict(KNOB_DEFAULTS)
    return provider.effective_all(learned=learned)
