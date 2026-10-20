# backend/app/analytics/aggression.py
"""
Canonical aggression score + loot tier score (Phase 22 / W3, M5).

ONE aggression computation for the whole product. Before Phase 22 two
disconnected implementations existed: a live 1–10 heuristic in
``team_memory.py`` and the DB field ``team_intelligence.aggression_score``
(0–1) that NOTHING wrote — dossiers therefore rendered
``insufficient_data`` even when fight data existed. Now:

* :func:`compute_aggression_score` is the single formula (below).
* :func:`refresh_aggression_scores` persists it to
  ``team_intelligence.aggression_score`` from real fight/kill rows.
* ``DossierService.compute_dossier`` refreshes before reading, and
  ``team_memory``'s 1–10 value is documented there as a display scale
  of learned behaviour, not a competing source of truth.

FORMULA (all inputs are real recorded data; output 0..1):

    aggression = 0.45 * fight_rate + 0.35 * kill_rate + 0.20 * early_rate

where each component is normalised against the tournament scope
(the other teams in the same matches), min-max style with a floor:

    fight_rate = fights_per_match / max_fights_per_match_in_scope
    kill_rate  = kills_per_match  / max_kills_per_match_in_scope
    early_rate = early_fights / total_fights   (fights started in the
                 first 5 minutes of a match — hot-drop / early-push
                 tendency; 0 when fight timing is unknown)

A team with no fights scores ``None`` — never 0.0, never invented:
"no data" and "maximally passive" are different statements.

Loot tier score (:func:`loot_tier_score`) scores an EXISTING inventory
(item list from the loot detector / team tracker equipment records):

    per item: weapon base by class (AR 30 / DMR 35 / SR 40 / SMG 20 /
    shotgun 15 / pistol 5 / other 10) + attachment bonus (5 each,
    max 20) ; armour: level x 10; helmet: level x 8;
    consumables: 2 each (max 20).

Team score = mean of player scores, normalised to 0..1 by /100 and
clamped. ``None`` when the inventory is empty/absent.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

EARLY_FIGHT_WINDOW_MS = 5 * 60 * 1000  # first 5 minutes of a match


def _norm(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return max(0.0, min(1.0, value / maximum))


def compute_aggression_score(
    *,
    fights: int,
    matches: int,
    kills: int,
    early_fights: int = 0,
    scope_max_fights_per_match: float = 0.0,
    scope_max_kills_per_match: float = 0.0,
) -> Optional[float]:
    """The canonical aggression formula (see module docstring).

    Returns ``None`` when there is nothing to measure (no matches or
    no fights) — callers must surface that as unknown, not 0.
    """
    if matches <= 0 or fights <= 0:
        return None
    fpm = fights / matches
    kpm = kills / matches
    fight_rate = _norm(fpm, scope_max_fights_per_match or fpm)
    kill_rate = _norm(kpm, scope_max_kills_per_match or kpm)
    early_rate = max(0.0, min(1.0, early_fights / fights))
    score = 0.45 * fight_rate + 0.35 * kill_rate + 0.20 * early_rate
    return round(max(0.0, min(1.0, score)), 4)


async def refresh_aggression_scores(db: Any, team_ids: Optional[Sequence[str]] = None) -> Dict[str, Optional[float]]:
    """Recompute + persist ``team_intelligence.aggression_score``.

    Reads real ``fight_events`` / ``kill_events`` rows (the same rows
    dossiers use), computes the canonical score per team against the
    scope of all teams appearing in those fights, and upserts the
    ``TeamIntelligence`` row. Returns ``{team_id: score|None}``.
    Teams with no fights keep ``aggression_score = NULL``.
    """
    from sqlalchemy import select

    from app.db.models import FightEvent, KillEvent, Player, TeamIntelligence

    fights = (await db.execute(select(FightEvent))).scalars().all()
    if not fights:
        return {}

    # Per-team raw inputs.
    team_fights: Dict[str, int] = {}
    team_matches: Dict[str, set] = {}
    team_early: Dict[str, int] = {}
    match_starts: Dict[str, int] = {}
    for f in fights:
        match_starts[f.match_id] = min(
            match_starts.get(f.match_id, f.start_timestamp_ms), f.start_timestamp_ms
        )
    for f in fights:
        early = (f.start_timestamp_ms - match_starts[f.match_id]) <= EARLY_FIGHT_WINDOW_MS
        for tid in f.team_ids or []:
            # Scope is ALWAYS all teams (normalisation needs the full
            # field); ``team_ids`` only restricts which rows are WRITTEN.
            team_fights[tid] = team_fights.get(tid, 0) + 1
            team_matches.setdefault(tid, set()).add(f.match_id)
            if early:
                team_early[tid] = team_early.get(tid, 0) + 1

    # Kills per team via the players table (killer -> team).
    kills_by_team: Dict[str, int] = {}
    match_ids = list(match_starts)
    if match_ids:
        krows = (
            await db.execute(select(KillEvent).where(KillEvent.match_id.in_(match_ids)))
        ).scalars().all()
        if krows:
            pids = {k.killer_player_id for k in krows}
            players = (
                await db.execute(select(Player).where(Player.id.in_(list(pids))))
            ).scalars().all()
            team_of = {p.id: p.team_id for p in players}
            for k in krows:
                tid = team_of.get(k.killer_player_id)
                if tid:
                    kills_by_team[tid] = kills_by_team.get(tid, 0) + 1

    scope = list(team_fights)
    fpms = {t: team_fights[t] / max(1, len(team_matches.get(t, set()) or {1})) for t in scope}
    kpms = {
        t: kills_by_team.get(t, 0) / max(1, len(team_matches.get(t, set()) or {1}))
        for t in scope
    }
    max_fpm = max(fpms.values(), default=0.0)
    max_kpm = max(kpms.values(), default=0.0)

    out: Dict[str, Optional[float]] = {}
    written = False
    for tid in scope:
        score = compute_aggression_score(
            fights=team_fights[tid],
            matches=len(team_matches.get(tid, set())),
            kills=kills_by_team.get(tid, 0),
            early_fights=team_early.get(tid, 0),
            scope_max_fights_per_match=max_fpm,
            scope_max_kills_per_match=max_kpm,
        )
        out[tid] = score
        if team_ids is not None and tid not in team_ids:
            continue
        intel = (
            await db.execute(
                select(TeamIntelligence).where(TeamIntelligence.team_id == tid)
            )
        ).scalar_one_or_none()
        if intel is None:
            intel = TeamIntelligence(team_id=tid, profile={})
            db.add(intel)
        intel.aggression_score = score
        written = True
    if written:
        await db.commit()
    return out


# ── Loot tier score ──────────────────────────────────────────────

_WEAPON_BASE = {
    "sniper": 40, "sr": 40, "dmr": 35, "ar": 30, "assault": 30,
    "smg": 20, "shotgun": 15, "pistol": 5,
}


def _item_score(item: Dict[str, Any]) -> float:
    kind = str(item.get("type") or item.get("kind") or "").lower()
    name = str(item.get("name") or "").lower()
    score = 10.0  # generic item
    for key, base in _WEAPON_BASE.items():
        if key in kind or key in name:
            score = float(base)
            break
    attachments = item.get("attachments") or []
    score += min(20.0, 5.0 * len(list(attachments)))
    if "armor" in kind or "vest" in kind:
        score = 10.0 * float(item.get("level") or 0)
    if "helmet" in kind:
        score = 8.0 * float(item.get("level") or 0)
    if kind in ("consumable", "heal", "boost", "ammo"):
        score = 2.0
    return score


def loot_tier_score(inventories: Optional[Iterable[Iterable[Dict[str, Any]]]]) -> Optional[float]:
    """Tier score 0..1 over existing per-player inventories.

    ``inventories`` is an iterable of per-player item lists (dicts with
    ``type``/``name``/``level``/``attachments`` as recorded by the loot
    detector / equipment tracking). Returns ``None`` when there is no
    inventory data at all — an unscored team is not a 0-tier team.
    """
    if inventories is None:
        return None
    player_scores: List[float] = []
    for items in inventories:
        items = list(items or [])
        if not items:
            continue
        consumables = sum(1 for i in items if str(i.get("type") or "").lower() in ("consumable", "heal", "boost", "ammo"))
        gear = sum(_item_score(i) for i in items if str(i.get("type") or "").lower() not in ("consumable", "heal", "boost", "ammo"))
        player_scores.append(gear + min(20.0, 2.0 * consumables))
    if not player_scores:
        return None
    mean = sum(player_scores) / len(player_scores)
    return round(max(0.0, min(1.0, mean / 100.0)), 4)
