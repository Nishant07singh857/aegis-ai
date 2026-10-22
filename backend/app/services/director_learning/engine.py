# backend/app/services/director_learning/engine.py
"""Learning engine — bounded rule-tuning proposals (Phase 27, W3).

Transparent statistical rule-tuning — NOT machine learning, NOT
neural training. The engine aggregates the decision journal, the
suggestion fates (W1) and the decision labels (W2; the table may be
empty — every rule then simply finds no labelled evidence) into
per-bucket rates, applies FIVE fixed, named rules, and writes
adjustment rows. Each row carries the bucket, the before/after
values (clamped to the knob registry bounds), a plain-language
reason with the numbers in it, and an evidence JSON with sample
ids and a sample watermark.

Modes (``director_learning_state``, single row):
  off     — run_analysis creates nothing; the provider serves defaults.
  suggest — proposals are written status='proposed' (default).
  auto    — proposals are written already 'applied'
            (applied_by='learning_engine') and the provider refreshes.

Guards: <= 1 pending ('proposed') row per knob; a knob at its bound
produces no proposal; sample-cooldown — after a knob is applied, it
is not re-proposed until >= min_samples NEW decided/labelled samples
exist in the justifying bucket beyond the applied row's watermark.

R4 window: ALL history (missed fights are rare; the cooldown guard
still bounds re-proposals). Documented in docs/DIRECTOR_LEARNING.md.

W4 API (service level): LearningEngine methods run_analysis,
apply_adjustment, reject_adjustment, rollback_adjustment,
list_adjustments, list_proposals, get_adjustment, get_learning_mode,
set_learning_mode, profile_exists — plus the module-level async
helpers of the same names taking ``session_factory=``.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog
from sqlalchemy import select

from .knobs import (
    KNOB_REGISTRY,
    KnobProvider,
    clamp_value,
    ensure_adjustable,
    get_provider,
)
from .models import (
    LEARNING_STATE_ID,
    DirectorDecision,
    DirectorDecisionLabel,
    DirectorLearningAdjustment,
    DirectorLearningState,
    DirectorSuggestionFate,
)

logger = structlog.get_logger("aegis.director_learning.engine")

#: Default minimum samples before any rule may fire.
DEFAULT_MIN_SAMPLES = 10
#: Named rule thresholds (fixed by the Phase 27 brief).
R1_APPROVAL_RATE_MAX = 0.40
R1_MARGIN_DEFAULT_DELTA = 3.0
R2_FIZZLE_RATE_MIN = 0.35
R2_PRECOMBAT_DELTA = -2.0
R2_MARGIN_DEFAULT_DELTA = 2.0
R3_PREMATURE_RATE_MIN = 0.30
R3_FIGHT_HOLD_DELTA = 4.0
R4_MISSED_FIGHT_MIN = 5
R4_STALE_DELTA = -2.0
R5_STORYLINE_MIN_SAMPLES = 8
R5_REJECT_MODIFIED_RATE_MIN = 0.60
R5_RANK1_DELTA = -0.02

LEARNING_MODES = ("off", "suggest", "auto")
DECIDED_FATES = ("approved", "rejected", "modified")

# Label vocabulary (W2's labeler; aliases normalised defensively).
_FIZZLE = {"fizzle", "fizzled", "fizzle_switch"}
_PREMATURE = {"premature_exit", "premature", "early_exit"}
_GOOD = {"good_call", "good_switch", "good"}
_MISSED = {"missed_fight", "missed"}


def _label_kind(label: Optional[str]) -> Optional[str]:
    text = (label or "").strip().lower()
    if text in _FIZZLE:
        return "fizzle"
    if text in _PREMATURE:
        return "premature_exit"
    if text in _GOOD:
        return "good_call"
    if text in _MISSED:
        return "missed_fight"
    return None


def _rule_family(rule: Optional[str]) -> str:
    """Rule family = rule with any trailing _<number> stripped."""
    text = (rule or "").strip()
    if not text:
        return "none"
    return re.sub(r"_\d+(?:\.\d+)?$", "", text)


def _bucket_of(decision: Dict[str, Any]) -> Dict[str, str]:
    challenger = decision.get("challenger") or {}
    return {
        "phase": decision.get("phase") or "unknown",
        "engagement_state": challenger.get("engagement_state") or "none",
        "rule_family": _rule_family(decision.get("rule")),
    }


def _bucket_key(bucket: Dict[str, str]) -> Tuple[str, str, str]:
    return (
        bucket.get("phase") or "unknown",
        bucket.get("engagement_state") or "none",
        bucket.get("rule_family") or "none",
    )


def _storyline_bonus(decision: Dict[str, Any]) -> float:
    challenger = decision.get("challenger") or {}
    team_id = challenger.get("team_id")
    for cand in decision.get("candidates") or []:
        if cand.get("team_id") == team_id:
            try:
                return float((cand.get("breakdown") or {}).get("storyline_bonus") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _precombat_term(decision: Dict[str, Any]) -> float:
    challenger = decision.get("challenger") or {}
    team_id = challenger.get("team_id")
    for cand in decision.get("candidates") or []:
        if cand.get("team_id") == team_id:
            try:
                return float((cand.get("breakdown") or {}).get("precombat") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _row_dict(row: DirectorLearningAdjustment) -> Dict[str, Any]:
    data = row.to_dict()
    data.pop("is_deleted", None)
    data.pop("deleted_at", None)
    return data


def _default_session_factory():
    from app.db.base import AsyncSessionLocal

    return AsyncSessionLocal


# ── learning-mode state (W4 get/set) ──────────────────────────────


async def get_learning_mode(
    session_factory: Optional[Callable[[], Any]] = None,
) -> str:
    factory = session_factory or _default_session_factory()
    async with factory() as session:
        state = await session.get(DirectorLearningState, LEARNING_STATE_ID)
        return (state.learning_mode if state is not None else "suggest")


async def set_learning_mode(
    mode: str,
    updated_by: Optional[str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
    provider: Optional[KnobProvider] = None,
) -> str:
    normalised = (mode or "").strip().lower()
    if normalised not in LEARNING_MODES:
        raise ValueError(
            f"Invalid learning mode {mode!r}: must be one of {LEARNING_MODES}."
        )
    factory = session_factory or _default_session_factory()
    async with factory() as session:
        state = await session.get(DirectorLearningState, LEARNING_STATE_ID)
        if state is None:
            state = DirectorLearningState(
                id=LEARNING_STATE_ID, learning_mode=normalised,
                updated_by=updated_by,
            )
            session.add(state)
        else:
            state.learning_mode = normalised
            state.updated_by = updated_by
        await session.commit()
    prov = provider or get_provider()
    if prov is not None:
        await prov.refresh()
    return normalised


# ── the engine ────────────────────────────────────────────────────


class LearningEngine:
    """Aggregates journal evidence into bounded knob proposals."""

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        provider: Optional[KnobProvider] = None,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self.min_samples = max(1, int(min_samples))

    def _factory(self):
        return self._session_factory or _default_session_factory()

    @property
    def provider(self) -> Optional[KnobProvider]:
        return self._provider or get_provider()

    # ── aggregation ──────────────────────────────────────────────

    async def _load(self):
        async with self._factory()() as session:
            decisions = [
                r.to_dict()
                for r in (await session.execute(select(DirectorDecision))).scalars().all()
            ]
            fate_rows = (
                await session.execute(
                    select(DirectorSuggestionFate).order_by(
                        DirectorSuggestionFate.ts.asc()
                    )
                )
            ).scalars().all()
            labels = [
                r.to_dict()
                for r in (await session.execute(select(DirectorDecisionLabel))).scalars().all()
            ]
            adjustments = (
                await session.execute(select(DirectorLearningAdjustment))
            ).scalars().all()
        for d in decisions:
            d.pop("is_deleted", None)
            d.pop("deleted_at", None)
        for lab in labels:
            lab.pop("is_deleted", None)
            lab.pop("deleted_at", None)
        # Latest fate per suggestion wins (a 'modified' row supersedes).
        latest_fate: Dict[str, Dict[str, Any]] = {}
        for row in fate_rows:
            data = row.to_dict()
            data.pop("is_deleted", None)
            data.pop("deleted_at", None)
            latest_fate[str(row.suggestion_id)] = data
        return decisions, latest_fate, labels, adjustments

    def _aggregate(self, decisions, latest_fate, labels):
        """Per-bucket metrics + the R4/R5 cross-bucket views."""
        labels_by_decision: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for lab in labels:
            labels_by_decision[str(lab.get("decision_id"))].append(lab)

        buckets: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

        def bucket_for(decision):
            bucket = _bucket_of(decision)
            key = _bucket_key(bucket)
            entry = buckets.setdefault(
                key,
                {
                    "bucket": bucket,
                    "suggestion_ids": [],
                    "decided": 0,
                    "approved": 0,
                    "switch_ids": [],
                    "fizzle": 0,
                    "premature_exit": 0,
                    "good_call": 0,
                    "fizzle_precombat_positive": 0,
                    "storyline_decided": 0,
                    "storyline_rejected_modified": 0,
                    "label_ids": [],
                },
            )
            return entry

        for decision in decisions:
            entry = bucket_for(decision)
            sid = decision.get("suggestion_id")
            if sid and decision.get("outcome") == "suggestion":
                fate = latest_fate.get(str(sid))
                if fate is not None and fate.get("fate") in DECIDED_FATES:
                    entry["suggestion_ids"].append(str(decision.get("id")))
                    entry["decided"] += 1
                    if fate["fate"] == "approved":
                        entry["approved"] += 1
                    if _storyline_bonus(decision) > 0:
                        entry["storyline_decided"] += 1
                        if fate["fate"] in ("rejected", "modified"):
                            entry["storyline_rejected_modified"] += 1
            if decision.get("outcome") == "switch":
                entry["switch_ids"].append(str(decision.get("id")))
                for lab in labels_by_decision.get(str(decision.get("id")), []):
                    kind = _label_kind(lab.get("label"))
                    if kind is None:
                        continue
                    entry["label_ids"].append(str(lab.get("id")))
                    if kind == "fizzle":
                        entry["fizzle"] += 1
                        if _precombat_term(decision) > 0:
                            entry["fizzle_precombat_positive"] += 1
                    elif kind == "premature_exit":
                        entry["premature_exit"] += 1
                    elif kind == "good_call":
                        entry["good_call"] += 1

        # R4 (global): missed_fight labels on stale-margin decisions
        # or decisions whose incumbent breakdown shows staleness.
        decisions_by_id = {str(d.get("id")): d for d in decisions}
        missed_qualifying: List[Dict[str, Any]] = []
        for lab in labels:
            if _label_kind(lab.get("label")) != "missed_fight":
                continue
            decision = decisions_by_id.get(str(lab.get("decision_id")))
            if decision is None:
                continue
            rule = decision.get("rule") or ""
            staleness = 0.0
            try:
                staleness = float(
                    ((decision.get("incumbent") or {}).get("breakdown") or {})
                    .get("staleness_penalty") or 0.0
                )
            except (TypeError, ValueError):
                staleness = 0.0
            if rule.startswith("margin_stale") or staleness < 0:
                missed_qualifying.append(
                    {"label": lab, "decision": decision}
                )
        return buckets, missed_qualifying

    # ── guards ───────────────────────────────────────────────────

    @staticmethod
    def _bucket_total(entry: Dict[str, Any]) -> int:
        labelled_switches = (
            entry["fizzle"] + entry["premature_exit"] + entry["good_call"]
        )
        return entry["decided"] + labelled_switches

    def _cooldown_ok(self, knob, bucket_key, bucket_total, adjustments) -> bool:
        """False when < min_samples NEW samples since the last apply."""
        applied = [
            a for a in adjustments
            if a.knob == knob and a.status == "applied"
            and (a.evidence or {}).get("watermark") is not None
        ]
        if not applied:
            return True
        latest = max(applied, key=lambda a: a.ts)
        watermark = (latest.evidence or {}).get("watermark") or {}
        # The watermark counts samples in ITS bucket; only compare
        # like-for-like (same justifying bucket).
        if tuple((latest.bucket or {}).get(k) for k in ("phase", "engagement_state", "rule_family")) != bucket_key:
            # Different bucket than the last application: the applied
            # row's watermark says nothing about this bucket — but the
            # knob was still just changed; require fresh samples here
            # is impossible to prove, so be conservative ONLY when the
            # buckets match. Different bucket => independent evidence.
            return True
        return (bucket_total - int(watermark.get("bucket_total", 0))) >= self.min_samples

    # ── analysis ─────────────────────────────────────────────────

    async def run_analysis(
        self, min_samples: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """One analysis pass. Returns the adjustment rows created."""
        n_min = max(1, int(min_samples)) if min_samples else self.min_samples
        mode = await get_learning_mode(session_factory=self._session_factory)
        provider = self.provider
        if provider is not None:
            await provider.refresh()
        if mode == "off":
            return []

        decisions, latest_fate, labels, adjustments = await self._load()
        buckets, missed_qualifying = self._aggregate(
            decisions, latest_fate, labels
        )
        pending_knobs = {
            a.knob for a in adjustments if a.status == "proposed"
        }

        # Candidate proposals: (knob, bucket, delta, reason, evidence)
        candidates: List[Dict[str, Any]] = []

        def add_candidate(knob, entry, delta, reason, evidence):
            if knob in pending_knobs:
                return
            bucket_key = _bucket_key(entry["bucket"])
            if not self._cooldown_ok(
                knob, bucket_key, self._bucket_total(entry), adjustments
            ):
                return
            before = (
                provider.effective(knob, learned=True)
                if provider is not None
                else KNOB_REGISTRY[knob].default
            )
            after = clamp_value(knob, before + delta)
            if after == before:
                return  # at bound => no proposal
            evidence = {
                **evidence,
                "watermark": {"bucket_total": self._bucket_total(entry)},
            }
            candidates.append(
                {
                    "knob": knob, "bucket": dict(entry["bucket"]),
                    "before": before, "after": after,
                    "reason": reason, "evidence": evidence,
                }
            )
            pending_knobs.add(knob)  # <= 1 new row per knob per pass

        for key in sorted(buckets):
            entry = buckets[key]
            bucket = entry["bucket"]
            decided = entry["decided"]
            switches = len(entry["switch_ids"])
            labelled = (
                entry["fizzle"] + entry["premature_exit"] + entry["good_call"]
            )
            # R1 — low suggestion approval rate -> margin_default +3
            if decided >= n_min:
                rate = entry["approved"] / decided
                if rate < R1_APPROVAL_RATE_MAX:
                    add_candidate(
                        "margin_default", entry, R1_MARGIN_DEFAULT_DELTA,
                        (
                            f"R1: in bucket phase={bucket['phase']}, "
                            f"engagement={bucket['engagement_state']}, "
                            f"rule_family={bucket['rule_family']}, only "
                            f"{entry['approved']}/{decided} suggestions were "
                            f"approved (approval rate {rate:.2f} < 0.40). "
                            "Humans keep rejecting these challengers, so the "
                            "default switch margin rises by 3 points."
                        ),
                        {
                            "rule": "R1", "n": decided,
                            "approval_rate": round(rate, 4),
                            "approved": entry["approved"],
                            "decision_ids": entry["suggestion_ids"][:20],
                        },
                    )
            # R2 — switch fizzle rate -> precombat -2 OR margin +2
            if switches >= n_min and labelled > 0:
                fizzle_rate = entry["fizzle"] / switches
                if fizzle_rate > R2_FIZZLE_RATE_MIN:
                    if (
                        entry["fizzle"] > 0
                        and entry["fizzle_precombat_positive"] * 2 >= entry["fizzle"]
                    ):
                        add_candidate(
                            "engagement_precombat_points", entry,
                            R2_PRECOMBAT_DELTA,
                            (
                                f"R2: {entry['fizzle']}/{switches} switches "
                                f"fizzled (rate {fizzle_rate:.2f} > 0.35) and "
                                f"at least half the fizzled challengers had a "
                                f"positive precombat term "
                                f"({entry['fizzle_precombat_positive']} of "
                                f"{entry['fizzle']}), so the PRE_COMBAT score "
                                "term drops by 2 points."
                            ),
                            {
                                "rule": "R2", "n": switches,
                                "fizzle_rate": round(fizzle_rate, 4),
                                "fizzle": entry["fizzle"],
                                "fizzle_precombat_positive": entry[
                                    "fizzle_precombat_positive"
                                ],
                                "decision_ids": entry["switch_ids"][:20],
                                "label_ids": entry["label_ids"][:20],
                            },
                        )
                    else:
                        add_candidate(
                            "margin_default", entry, R2_MARGIN_DEFAULT_DELTA,
                            (
                                f"R2: {entry['fizzle']}/{switches} switches "
                                f"fizzled (rate {fizzle_rate:.2f} > 0.35) "
                                "without a precombat majority, so the "
                                "default switch margin rises by 2 points."
                            ),
                            {
                                "rule": "R2", "n": switches,
                                "fizzle_rate": round(fizzle_rate, 4),
                                "fizzle": entry["fizzle"],
                                "decision_ids": entry["switch_ids"][:20],
                                "label_ids": entry["label_ids"][:20],
                            },
                        )
                # R3 — premature exits -> margin_fight_hold +4
                premature_rate = entry["premature_exit"] / switches
                if premature_rate > R3_PREMATURE_RATE_MIN:
                    add_candidate(
                        "margin_fight_hold", entry, R3_FIGHT_HOLD_DELTA,
                        (
                            f"R3: {entry['premature_exit']}/{switches} "
                            f"switches exited prematurely (rate "
                            f"{premature_rate:.2f} > 0.30), so the "
                            "fight-hold switch margin rises by 4 points."
                        ),
                        {
                            "rule": "R3", "n": switches,
                            "premature_exit_rate": round(premature_rate, 4),
                            "premature_exit": entry["premature_exit"],
                            "decision_ids": entry["switch_ids"][:20],
                            "label_ids": entry["label_ids"][:20],
                        },
                    )
            # R5 — storyline suggestions rejected/modified -> rank1 -0.02
            s_n = entry["storyline_decided"]
            if s_n >= R5_STORYLINE_MIN_SAMPLES:
                s_rate = entry["storyline_rejected_modified"] / s_n
                if s_rate > R5_REJECT_MODIFIED_RATE_MIN:
                    add_candidate(
                        "storyline_rank1_uplift", entry, R5_RANK1_DELTA,
                        (
                            f"R5: {entry['storyline_rejected_modified']}/{s_n} "
                            f"decided suggestions with a storyline bonus were "
                            f"rejected/modified (rate {s_rate:.2f} > 0.60), "
                            "so the rank-1 storyline uplift drops by 0.02."
                        ),
                        {
                            "rule": "R5", "n": s_n,
                            "rejected_modified_rate": round(s_rate, 4),
                            "rejected_modified": entry[
                                "storyline_rejected_modified"
                            ],
                            "decision_ids": entry["suggestion_ids"][:20],
                        },
                    )

        # R4 (global, all history) — missed fights on stale decisions
        if len(missed_qualifying) >= R4_MISSED_FIGHT_MIN:
            dominant_bucket = Counter(
                _bucket_key(_bucket_of(item["decision"]))
                for item in missed_qualifying
            ).most_common(1)[0][0]
            entry = buckets.get(dominant_bucket)
            if entry is not None:
                add_candidate(
                    "margin_stale_incumbent", entry, R4_STALE_DELTA,
                    (
                        f"R4: {len(missed_qualifying)} missed-fight labels "
                        "are tied to stale-margin decisions (rule "
                        "margin_stale_* or a stale incumbent), so the "
                        "stale-incumbent margin drops by 2 points to cut "
                        "away sooner."
                    ),
                    {
                        "rule": "R4", "n": len(missed_qualifying),
                        "missed_fight": len(missed_qualifying),
                        "window": "all",
                        "decision_ids": [
                            str(i["decision"].get("id"))
                            for i in missed_qualifying[:20]
                        ],
                        "label_ids": [
                            str(i["label"].get("id"))
                            for i in missed_qualifying[:20]
                        ],
                    },
                )

        created: List[Dict[str, Any]] = []
        async with self._factory()() as session:
            for cand in candidates:
                ensure_adjustable(cand["knob"])
                row = DirectorLearningAdjustment(
                    id=str(uuid.uuid4()),
                    ts=datetime.now(timezone.utc),
                    status="applied" if mode == "auto" else "proposed",
                    knob=cand["knob"],
                    bucket=cand["bucket"],
                    before_value=cand["before"],
                    after_value=cand["after"],
                    reason=cand["reason"],
                    evidence=cand["evidence"],
                    learning_mode=mode,
                    applied_by="learning_engine" if mode == "auto" else None,
                )
                session.add(row)
                created.append(row)
            await session.commit()
            for r in created:
                await session.refresh(r)
            result = [_row_dict(r) for r in created]
        if mode == "auto" and result and provider is not None:
            await provider.refresh()
        return result

    # ── application lifecycle (W4 consumes these) ────────────────

    async def list_adjustments(
        self, status: Optional[str] = None, knob: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Adjustments newest-first, optionally filtered."""
        async with self._factory()() as session:
            query = select(DirectorLearningAdjustment).order_by(
                DirectorLearningAdjustment.ts.desc()
            )
            if status is not None:
                query = query.where(DirectorLearningAdjustment.status == status)
            if knob is not None:
                query = query.where(DirectorLearningAdjustment.knob == knob)
            query = query.limit(max(1, int(limit)))
            rows = (await session.execute(query)).scalars().all()
            return [_row_dict(r) for r in rows]

    async def list_proposals(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Pending ('proposed') adjustments, newest first."""
        return await self.list_adjustments(status="proposed", limit=limit)

    async def get_adjustment(self, adjustment_id: str) -> Optional[Dict[str, Any]]:
        async with self._factory()() as session:
            row = await session.get(
                DirectorLearningAdjustment, str(adjustment_id)
            )
            return _row_dict(row) if row is not None else None

    async def profile_exists(self) -> bool:
        """True when >= 1 applied adjustment row exists."""
        async with self._factory()() as session:
            rows = (
                await session.execute(
                    select(DirectorLearningAdjustment).where(
                        DirectorLearningAdjustment.status == "applied"
                    ).limit(1)
                )
            ).scalars().all()
            return len(rows) > 0

    async def apply_adjustment(
        self, adjustment_id: str, applied_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """proposed -> applied; provider refreshes."""
        async with self._factory()() as session:
            row = await session.get(
                DirectorLearningAdjustment, str(adjustment_id)
            )
            if row is None:
                raise KeyError(f"No adjustment {adjustment_id!r}.")
            if row.status != "proposed":
                raise ValueError(
                    f"Adjustment {adjustment_id!r} is {row.status!r}; "
                    "only 'proposed' rows can be applied."
                )
            row.status = "applied"
            row.applied_by = applied_by
            await session.commit()
            await session.refresh(row)
            result = _row_dict(row)
        provider = self.provider
        if provider is not None:
            await provider.refresh()
        return result

    async def reject_adjustment(
        self, adjustment_id: str, decided_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """proposed -> rejected (actor recorded in applied_by)."""
        async with self._factory()() as session:
            row = await session.get(
                DirectorLearningAdjustment, str(adjustment_id)
            )
            if row is None:
                raise KeyError(f"No adjustment {adjustment_id!r}.")
            if row.status != "proposed":
                raise ValueError(
                    f"Adjustment {adjustment_id!r} is {row.status!r}; "
                    "only 'proposed' rows can be rejected."
                )
            row.status = "rejected"
            row.applied_by = decided_by
            await session.commit()
            await session.refresh(row)
            return _row_dict(row)

    async def rollback_adjustment(
        self,
        adjustment_id: str,
        by: Optional[str] = None,
        cascade: bool = False,
    ) -> Dict[str, Any]:
        """Roll back an applied adjustment EXACTLY.

        The target row becomes 'rolled_back' and a compensating row
        (status='applied', parent_id=target, after=target.before) is
        inserted, so the effective value returns to the exact
        pre-adjustment value. Append-only: nothing is deleted.
        Returns the compensating row.
        """
        async with self._factory()() as session:
            target = await session.get(
                DirectorLearningAdjustment, str(adjustment_id)
            )
            if target is None:
                raise KeyError(f"No adjustment {adjustment_id!r}.")
            if target.status != "applied":
                raise ValueError(
                    f"Adjustment {adjustment_id!r} is {target.status!r}; "
                    "only 'applied' rows can be rolled back."
                )
            # W5 gate D1: the provider resolves the effective value
            # as the NEWEST applied row for the knob (ts order), so
            # blindly compensating an older row would silently
            # clobber newer applied adjustments while their rows
            # still read 'applied'. Default: REFUSE (the API maps
            # this to 409). With cascade=True the caller explicitly
            # unwinds the target AND every newer applied row for the
            # knob — all marked 'rolled_back', all cited in the
            # compensating row — restoring the exact value from
            # before the target adjustment.
            newer_rows = (
                await session.execute(
                    select(DirectorLearningAdjustment)
                    .where(
                        DirectorLearningAdjustment.knob == target.knob,
                        DirectorLearningAdjustment.status == "applied",
                        DirectorLearningAdjustment.id != target.id,
                        DirectorLearningAdjustment.ts >= target.ts,
                    )
                    .order_by(DirectorLearningAdjustment.ts.asc())
                )
            ).scalars().all()
            if newer_rows and not cascade:
                raise ValueError(
                    f"Adjustment {adjustment_id!r} is not the latest "
                    f"applied adjustment for {target.knob!r} "
                    f"({len(newer_rows)} newer applied row(s) exist); "
                    "roll back the newer adjustment first, or pass "
                    "cascade=True to unwind them together."
                )
            current_effective = (
                float(newer_rows[-1].after_value)
                if newer_rows
                else float(target.after_value)
            )
            for _row in newer_rows:
                _row.status = "rolled_back"
            mode = await get_learning_mode(
                session_factory=self._session_factory
            )
            target.status = "rolled_back"
            compensating = DirectorLearningAdjustment(
                id=str(uuid.uuid4()),
                ts=datetime.now(timezone.utc),
                status="applied",
                knob=target.knob,
                bucket=dict(target.bucket or {}),
                before_value=current_effective,
                after_value=float(target.before_value),
                reason=(
                    f"Rollback of adjustment {target.id} by {by}: "
                    f"{target.knob} restored from {current_effective} "
                    f"to {target.before_value} (the exact pre-adjustment "
                    "value)."
                    + (
                        f" Cascaded over {len(newer_rows)} newer "
                        "applied adjustment(s), also marked rolled_back."
                        if newer_rows
                        else ""
                    )
                ),
                evidence={
                    "rollback_of": target.id,
                    "cascaded": [r.id for r in newer_rows],
                    "watermark": (target.evidence or {}).get("watermark"),
                },
                learning_mode=mode,
                applied_by=by,
                parent_id=target.id,
            )
            session.add(compensating)
            await session.commit()
            await session.refresh(compensating)
            result = _row_dict(compensating)
        provider = self.provider
        if provider is not None:
            await provider.refresh()
        return result

    async def get_learning_mode(self) -> str:
        return await get_learning_mode(session_factory=self._session_factory)

    async def set_learning_mode(
        self, mode: str, updated_by: Optional[str] = None
    ) -> str:
        return await set_learning_mode(
            mode, updated_by=updated_by,
            session_factory=self._session_factory, provider=self.provider,
        )


# ── module-level helpers (W4 convenience, session_factory=) ──────


async def run_analysis(
    session_factory: Optional[Callable[[], Any]] = None,
    provider: Optional[KnobProvider] = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> List[Dict[str, Any]]:
    return await LearningEngine(
        session_factory=session_factory, provider=provider,
        min_samples=min_samples,
    ).run_analysis()


async def apply_adjustment(
    adjustment_id: str, applied_by: Optional[str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
    provider: Optional[KnobProvider] = None,
) -> Dict[str, Any]:
    return await LearningEngine(
        session_factory=session_factory, provider=provider
    ).apply_adjustment(adjustment_id, applied_by=applied_by)


async def reject_adjustment(
    adjustment_id: str, decided_by: Optional[str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
    provider: Optional[KnobProvider] = None,
) -> Dict[str, Any]:
    return await LearningEngine(
        session_factory=session_factory, provider=provider
    ).reject_adjustment(adjustment_id, decided_by=decided_by)


async def rollback_adjustment(
    adjustment_id: str, by: Optional[str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
    provider: Optional[KnobProvider] = None,
) -> Dict[str, Any]:
    return await LearningEngine(
        session_factory=session_factory, provider=provider
    ).rollback_adjustment(adjustment_id, by=by)


# ── process-wide engine (builder-registered) ─────────────────────

_engine: Optional[LearningEngine] = None


def set_engine(engine: Optional[LearningEngine]) -> None:
    global _engine
    _engine = engine


def get_engine() -> Optional[LearningEngine]:
    return _engine


def periodic_analysis_callable(
    engine: Optional[LearningEngine] = None,
) -> Callable[[], Any]:
    """Callable the coordinator can schedule for periodic analysis.

    The engine does NOT start its own loop (no busy loop); the
    coordinator registers this with its lifespan scheduler if wanted.
    """
    target = engine or _engine

    async def _run() -> List[Dict[str, Any]]:
        if target is None:
            return []
        return await target.run_analysis()

    return _run
