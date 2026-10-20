"""Services for dossiers: fight log, team dossiers, match reports (Phase 11, W2).

All statistics are computed from real rows in fight_events / kill_events /
rotation_events / team_intelligence. Nothing is invented: thin data yields
honest "insufficient data" states, and every derived value names its source.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    FightEvent,
    KillEvent,
    Match,
    MatchEvent,
    Player,
    RotationEvent,
    Team,
    TeamIntelligence,
    Tournament,
)

from .schemas import (
    DossierOut,
    FightDetailOut,
    FightIngest,
    FightTeamOut,
    FightPlayerOut,
    FightWinnerOut,
    HeadToHeadOut,
    MatchReportData,
    MatchTeamSummary,
    PlaystyleOut,
    StatEdgeOut,
    TendenciesOut,
    ThirdPartyOut,
)

DOSSIER_CACHE_KEY = "dossier_v1"
DOSSIER_TTL_SECONDS = 3600
THIRD_PARTY_DELAY_MS = 30_000  # a team whose first kill action lands >30s after
# fight start is reported as a likely third party (evidence stated explicitly)

# Rotation-event meta keys this service understands (all optional; producers
# may omit them — the dossier then says so honestly instead of guessing).
META_LOCATION_LABEL = "location_label"  # e.g. "Pochinki"
META_ZONE_PHASE = "zone_phase"


# ══════════════════════════════════════════════════════════════════════
# Pure helpers (no DB — unit-testable)
# ══════════════════════════════════════════════════════════════════════


def classify_playstyle(
    aggression_score: Optional[float],
    fights_per_match: Optional[float],
    tournament_median_fpm: Optional[float],
) -> tuple[str, str]:
    """Return (label, evidence-string). Never invents a label without data."""
    if fights_per_match is None and aggression_score is None:
        return "insufficient_data", "no fights and no aggression_score recorded"
    parts: list[str] = []
    if fights_per_match is not None:
        parts.append(f"{fights_per_match:.1f} fights/match")
    if aggression_score is not None:
        parts.append(f"aggression_score {aggression_score:.2f}")
    evidence = ", ".join(parts)

    votes: list[str] = []
    if aggression_score is not None:
        if aggression_score >= 0.66:
            votes.append("aggressive")
        elif aggression_score <= 0.33:
            votes.append("passive")
    if fights_per_match is not None and tournament_median_fpm:
        if fights_per_match >= tournament_median_fpm * 1.4:
            votes.append("aggressive")
        elif fights_per_match <= tournament_median_fpm * 0.6:
            votes.append("passive")
    if not votes:
        return "balanced", evidence
    if all(v == "aggressive" for v in votes):
        return "aggressive", evidence
    if all(v == "passive" for v in votes):
        return "passive", evidence
    return "balanced", evidence


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]


def _safe_div(num: float, den: float) -> Optional[float]:
    return num / den if den else None


# ══════════════════════════════════════════════════════════════════════
# FightService
# ══════════════════════════════════════════════════════════════════════


class FightService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── queries ──────────────────────────────────────────────────

    def _base_query(self, team_id: Optional[str], match_id: Optional[str]):
        q = select(FightEvent).order_by(FightEvent.start_timestamp_ms.desc())
        if match_id:
            q = q.where(FightEvent.match_id == match_id)
        if team_id:
            q = q.where(FightEvent.team_ids.contains(team_id))
        return q

    async def count_fights(
        self, team_id: Optional[str] = None, match_id: Optional[str] = None
    ) -> int:
        q = select(func.count()).select_from(FightEvent)
        if match_id:
            q = q.where(FightEvent.match_id == match_id)
        if team_id:
            q = q.where(FightEvent.team_ids.contains(team_id))
        return (await self.db.execute(q)).scalar_one()

    async def list_fights(
        self,
        team_id: Optional[str] = None,
        match_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[FightDetailOut], int]:
        total = await self.count_fights(team_id=team_id, match_id=match_id)
        rows = (
            await self.db.execute(
                self._base_query(team_id, match_id).limit(limit).offset(offset)
            )
        ).scalars().all()
        if not rows:
            return [], total
        details = await self._enrich(list(rows))
        return details, total

    async def get_fight(self, fight_id: str) -> Optional[FightDetailOut]:
        row = (
            await self.db.execute(
                select(FightEvent).where(FightEvent.id == fight_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return (await self._enrich([row]))[0]

    # ── enrichment ───────────────────────────────────────────────

    async def _enrich(self, fights: list[FightEvent]) -> list[FightDetailOut]:
        team_ids: set[str] = set()
        player_ids: set[str] = set()
        match_ids: set[str] = set()
        for f in fights:
            team_ids.update(t for t in (f.team_ids or []) if isinstance(t, str))
            player_ids.update(p for p in (f.player_ids or []) if isinstance(p, str))
            match_ids.add(f.match_id)
            if f.winner_team_id:
                team_ids.add(f.winner_team_id)

        teams: dict[str, Team] = {}
        if team_ids:
            rows = (
                await self.db.execute(select(Team).where(Team.id.in_(team_ids)))
            ).scalars().all()
            teams = {t.id: t for t in rows}

        players: dict[str, Player] = {}
        if player_ids:
            rows = (
                await self.db.execute(select(Player).where(Player.id.in_(player_ids)))
            ).scalars().all()
            players = {p.id: p for p in rows}

        # Kill events inside each fight's window, for third-party derivation.
        kills_by_match: dict[str, list[KillEvent]] = defaultdict(list)
        if match_ids:
            min_start = min(f.start_timestamp_ms for f in fights)
            max_end = max(
                f.end_timestamp_ms or f.start_timestamp_ms + 120_000 for f in fights
            )
            krows = (
                await self.db.execute(
                    select(KillEvent).where(
                        KillEvent.match_id.in_(match_ids),
                        KillEvent.timestamp_ms >= min_start - 5_000,
                        KillEvent.timestamp_ms <= max_end + 5_000,
                    )
                )
            ).scalars().all()
            for k in krows:
                kills_by_match[k.match_id].append(k)
            # Map killer/victim players to teams for the kills we just loaded.
            extra_pids = {k.killer_player_id for k in krows} | {
                k.victim_player_id for k in krows
            } - set(players)
            if extra_pids:
                prows = (
                    await self.db.execute(
                        select(Player).where(Player.id.in_(extra_pids))
                    )
                ).scalars().all()
                for p in prows:
                    players[p.id] = p

        out: list[FightDetailOut] = []
        for f in fights:
            out.append(
                self._detail(f, teams, players, kills_by_match.get(f.match_id, []))
            )
        return out

    def _detail(
        self,
        f: FightEvent,
        teams: dict[str, Team],
        players: dict[str, Player],
        match_kills: list[KillEvent],
    ) -> FightDetailOut:
        team_names = {tid: (teams[tid].name if tid in teams else tid) for tid in (f.team_ids or [])}

        team_outs: list[FightTeamOut] = []
        for tid in f.team_ids or []:
            plist = [
                FightPlayerOut(
                    player_id=pid,
                    ign=players[pid].ign if pid in players else pid,
                    team_id=players[pid].team_id if pid in players else None,
                    team_name=teams[players[pid].team_id].name
                    if pid in players and players[pid].team_id in teams
                    else None,
                )
                for pid in (f.player_ids or [])
                if pid in players and players[pid].team_id == tid
            ]
            # players listed on the fight but unknown to the players table
            # still appear (honest: ign falls back to the raw id).
            known = {p.player_id for p in plist}
            for pid in (f.player_ids or []):
                if pid not in known and pid not in players:
                    plist.append(FightPlayerOut(player_id=pid, ign=pid))
            team_outs.append(
                FightTeamOut(
                    team_id=tid,
                    team_name=team_names.get(tid, tid),
                    players=plist,
                )
            )

        winner = None
        if f.winner_team_id:
            winner = FightWinnerOut(
                team_id=f.winner_team_id,
                team_name=teams[f.winner_team_id].name
                if f.winner_team_id in teams
                else f.winner_team_id,
            )
            outcome = f"{winner.team_name} won"
        elif f.end_timestamp_ms is None:
            outcome = "ongoing"
        else:
            outcome = "disengaged"

        third_parties = self._derive_third_parties(f, match_kills, team_names, players)

        duration_ms = None
        if f.end_timestamp_ms is not None and f.end_timestamp_ms >= f.start_timestamp_ms:
            duration_ms = f.end_timestamp_ms - f.start_timestamp_ms

        return FightDetailOut(
            id=f.id,
            match_id=f.match_id,
            map_name=f.map_name,
            start_timestamp_ms=f.start_timestamp_ms,
            end_timestamp_ms=f.end_timestamp_ms,
            duration_ms=duration_ms,
            location_x=f.location_x,
            location_y=f.location_y,
            intensity_score=f.intensity_score or 0.0,
            teams=team_outs,
            winner=winner,
            outcome=outcome,
            third_parties=third_parties,
            total_kills=f.total_kills or 0,
            total_knocks=f.total_knocks or 0,
            utility_used=f.utility_used or {},
        )

    def _derive_third_parties(
        self,
        f: FightEvent,
        match_kills: list[KillEvent],
        team_names: dict[str, str],
        players: dict[str, Player],
    ) -> list[ThirdPartyOut]:
        """Honest third-party detection from kill timing.

        For each team in the fight, find its first kill-event timestamp inside
        the fight window (killer player resolved to a team via the players
        table). A team whose first action lands >30s after the fight started
        is reported as a likely third party. Teams with no kill events in the
        window are NOT guessed at — they are reported as unknown.
        """
        end = f.end_timestamp_ms or (f.start_timestamp_ms + 120_000)
        in_window = [
            k
            for k in match_kills
            if f.start_timestamp_ms - 5_000 <= k.timestamp_ms <= end + 5_000
        ]
        if not in_window:
            return [
                ThirdPartyOut(
                    team_id=tid,
                    team_name=team_names.get(tid, tid),
                    evidence=(
                        "no kill events recorded in the fight window — "
                        "join order unknown"
                    ),
                )
                for tid in (f.team_ids or [])
            ]

        first_action: dict[str, int] = {}
        for k in sorted(in_window, key=lambda k: k.timestamp_ms):
            killer = players.get(k.killer_player_id)
            if killer is None or killer.team_id not in (f.team_ids or []):
                continue
            first_action.setdefault(killer.team_id, k.timestamp_ms)

        out: list[ThirdPartyOut] = []
        for tid in f.team_ids or []:
            name = team_names.get(tid, tid)
            if tid not in first_action:
                out.append(
                    ThirdPartyOut(
                        team_id=tid,
                        team_name=name,
                        evidence=(
                            "no kill events attributed to this team in the "
                            "fight window — join order unknown"
                        ),
                    )
                )
            else:
                delay = first_action[tid] - f.start_timestamp_ms
                if delay > THIRD_PARTY_DELAY_MS:
                    out.append(
                        ThirdPartyOut(
                            team_id=tid,
                            team_name=name,
                            first_action_at_ms=first_action[tid],
                            delay_after_start_ms=delay,
                            evidence=(
                                f"first attributed kill {delay // 1000}s after "
                                f"fight start (threshold {THIRD_PARTY_DELAY_MS // 1000}s)"
                            ),
                        )
                    )
        return out

    # ── ingest ───────────────────────────────────────────────────

    async def ingest(self, payload: FightIngest) -> FightDetailOut:
        match = (
            await self.db.execute(select(Match).where(Match.id == payload.match_id))
        ).scalar_one_or_none()
        if match is None:
            raise KeyError(f"match not found: {payload.match_id}")
        event = FightEvent(
            match_id=payload.match_id,
            start_timestamp_ms=payload.start_timestamp_ms,
            end_timestamp_ms=payload.end_timestamp_ms,
            team_ids=list(payload.team_ids),
            player_ids=list(payload.player_ids),
            location_x=payload.location_x,
            location_y=payload.location_y,
            map_name=payload.map_name,
            intensity_score=payload.intensity_score,
            winner_team_id=payload.winner_team_id,
            total_kills=payload.total_kills,
            total_knocks=payload.total_knocks,
            utility_used=dict(payload.utility_used),
        )
        self.db.add(event)
        await self.db.flush()
        detail = (await self._enrich([event]))[0]
        await self.db.commit()
        return detail


# ══════════════════════════════════════════════════════════════════════
# DossierService
# ══════════════════════════════════════════════════════════════════════


class DossierService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.fights = FightService(db)

    async def get_dossier(
        self, team_id: str, force_recompute: bool = False
    ) -> DossierOut:
        team = (
            await self.db.execute(select(Team).where(Team.id == team_id))
        ).scalar_one_or_none()
        if team is None:
            raise KeyError(f"team not found: {team_id}")

        intel = await self._get_intel(team_id)
        cached = (intel.profile or {}).get(DOSSIER_CACHE_KEY) if intel else None
        if cached and not force_recompute and self._fresh(cached):
            data = dict(cached.get("dossier") or {})
            data["data_freshness"] = "fresh"
            return DossierOut(**data)

        dossier = await self.compute_dossier(team_id)
        await self._persist(team_id, dossier)
        dossier.data_freshness = "recomputed"
        return dossier

    @staticmethod
    def _fresh(cached: dict) -> bool:
        try:
            ts = datetime.fromisoformat(cached.get("computed_at", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() < DOSSIER_TTL_SECONDS
        except Exception:
            return False

    async def _get_intel(self, team_id: str) -> Optional[TeamIntelligence]:
        return (
            await self.db.execute(
                select(TeamIntelligence).where(TeamIntelligence.team_id == team_id)
            )
        ).scalar_one_or_none()

    async def _persist(self, team_id: str, dossier: DossierOut) -> None:
        intel = await self._get_intel(team_id)
        payload = {
            "computed_at": dossier.computed_at,
            "dossier": dossier.model_dump(),
        }
        if intel is None:
            intel = TeamIntelligence(team_id=team_id, profile={DOSSIER_CACHE_KEY: payload})
            self.db.add(intel)
        else:
            profile = dict(intel.profile or {})
            profile[DOSSIER_CACHE_KEY] = payload
            intel.profile = profile
            intel.updated_at_utc = datetime.now(timezone.utc)
        await self.db.commit()

    # ── computation ──────────────────────────────────────────

    async def compute_dossier(self, team_id: str) -> DossierOut:
        team = (
            await self.db.execute(select(Team).where(Team.id == team_id))
        ).scalar_one_or_none()
        if team is None:
            raise KeyError(f"team not found: {team_id}")

        # Phase 22 (W3, M5): refresh the ONE canonical aggression
        # score from real fight/kill rows before reading it — the DB
        # field used to have no writer, so dossiers rendered
        # insufficient_data even when fight data existed. Best-effort:
        # a refresh failure leaves the last persisted value in place.
        try:
            from app.analytics.aggression import refresh_aggression_scores

            await refresh_aggression_scores(self.db, team_ids=[team_id])
        except Exception:
            pass

        intel = await self._get_intel(team_id)
        aggression = intel.aggression_score if intel else None

        team_fights, _ = await self.fights.list_fights(team_id=team_id, limit=10_000)
        match_ids = {f.match_id for f in team_fights}

        rotations = (
            await self.db.execute(
                select(RotationEvent).where(RotationEvent.team_id == team_id)
            )
        ).scalars().all()
        match_ids.update(r.match_id for r in rotations)

        # player -> team map for kill attribution (team's own players)
        team_players = (
            await self.db.execute(select(Player).where(Player.team_id == team_id))
        ).scalars().all()
        team_pids = {p.id for p in team_players}

        kills: list[KillEvent] = []
        if match_ids:
            kills = (
                await self.db.execute(
                    select(KillEvent).where(KillEvent.match_id.in_(match_ids))
                )
            ).scalars().all()

        matches_sampled = len(match_ids)
        fights_sampled = len(team_fights)
        notes: list[str] = []

        # Tournament scope for "vs average": all teams with fights in the
        # same matches this team played.
        scope_team_ids = await self._scope_teams(match_ids)
        scope_metrics = await self._scope_metrics(scope_team_ids, match_ids)

        # ── playstyle ──
        fpm = _safe_div(fights_sampled, matches_sampled)
        all_fpm = [
            m["fights_per_match"]
            for m in scope_metrics.values()
            if m["fights_per_match"] is not None
        ]
        label, evidence = classify_playstyle(aggression, fpm, _median(all_fpm))
        if matches_sampled < 3:
            notes.append(
                f"Only {matches_sampled} match(es) sampled — playstyle label "
                "is low-confidence."
            )
            if label == "insufficient_data":
                pass
        playstyle = PlaystyleOut(
            label=label,
            evidence=evidence,
            fights_per_match=round(fpm, 2) if fpm is not None else None,
            aggression_score=aggression,
            matches_sampled=matches_sampled,
        )

        # ── strengths / weaknesses ──
        strengths, weaknesses, comp_note = self._edges(
            team_id, scope_metrics, fights_sampled
        )

        # ── tendencies ──
        tendencies = await self._tendencies(team_id, rotations, team_fights, match_ids)

        # ── head-to-head ──
        h2h = await self._head_to_head(team_id, team_fights, kills)

        freshness = (
            "insufficient_data"
            if (matches_sampled == 0 or fights_sampled == 0)
            else "recomputed"
        )
        if freshness == "insufficient_data":
            notes.append(
                "Insufficient data: no fights recorded for this team yet. "
                "Record fights via POST /api/v1/dossiers/ingest/fight."
            )

        return DossierOut(
            team_id=team_id,
            team_name=team.name,
            computed_at=datetime.now(timezone.utc).isoformat(),
            data_freshness=freshness,
            matches_sampled=matches_sampled,
            fights_sampled=fights_sampled,
            playstyle=playstyle,
            strengths=strengths,
            weaknesses=weaknesses,
            comparison_note=comp_note,
            tendencies=tendencies,
            head_to_head=h2h,
            notes=notes,
        )

    async def _scope_teams(self, match_ids: set[str]) -> set[str]:
        """All teams appearing in the sampled matches (fight participants)."""
        if not match_ids:
            return set()
        rows = (
            await self.db.execute(
                select(FightEvent.team_ids).where(FightEvent.match_id.in_(match_ids))
            )
        ).all()
        out: set[str] = set()
        for (tids,) in rows:
            out.update(t for t in (tids or []) if isinstance(t, str))
        return out

    async def _scope_metrics(
        self, team_ids: set[str], match_ids: set[str]
    ) -> dict[str, dict[str, Optional[float]]]:
        """Per-team metric table over the sampled matches (real data only)."""
        metrics: dict[str, dict[str, Optional[float]]] = {}
        if not team_ids or not match_ids:
            return metrics

        fights = (
            await self.db.execute(
                select(FightEvent).where(FightEvent.match_id.in_(match_ids))
            )
        ).scalars().all()

        kills = (
            await self.db.execute(
                select(KillEvent).where(KillEvent.match_id.in_(match_ids))
            )
        ).scalars().all()
        killer_teams: dict[str, str] = {}
        if kills:
            pids = {k.killer_player_id for k in kills}
            prows = (
                await self.db.execute(select(Player).where(Player.id.in_(pids)))
            ).scalars().all()
            killer_teams = {p.id: p.team_id for p in prows}

        rots = (
            await self.db.execute(
                select(RotationEvent.team_id, func.count(RotationEvent.id))
                .where(RotationEvent.match_id.in_(match_ids))
                .group_by(RotationEvent.team_id)
            )
        ).all()
        rot_count = {tid: cnt for tid, cnt in rots}

        per_team_fights: dict[str, list[FightEvent]] = defaultdict(list)
        for f in fights:
            for tid in f.team_ids or []:
                if tid in team_ids:
                    per_team_fights[tid].append(f)

        for tid in team_ids:
            tf = per_team_fights.get(tid, [])
            n = len(tf)
            won = sum(1 for f in tf if f.winner_team_id == tid)
            tm_matches = {f.match_id for f in tf}
            tk = sum(1 for k in kills if killer_teams.get(k.killer_player_id) == tid)
            metrics[tid] = {
                "fight_win_rate": _safe_div(won, n),
                "kills_per_match": _safe_div(tk, len(tm_matches)) if tm_matches else None,
                "knocks_per_fight": _safe_div(
                    sum(f.total_knocks or 0 for f in tf), n
                ),
                "avg_intensity": (sum(f.intensity_score or 0.0 for f in tf) / n)
                if n
                else None,
                "rotations_per_match": _safe_div(
                    rot_count.get(tid, 0), len(tm_matches)
                ) if tm_matches else None,
                "fights_per_match": _safe_div(n, len(tm_matches))
                if tm_matches
                else None,
            }
        return metrics

    METRIC_LABELS: dict[str, str] = {
        "fight_win_rate": "fight win rate",
        "kills_per_match": "kills per match",
        "knocks_per_fight": "knocks per fight",
        "avg_intensity": "avg fight intensity",
        "rotations_per_match": "rotations per match",
        "fights_per_match": "fights per match",
    }

    def _edges(
        self,
        team_id: str,
        scope_metrics: dict[str, dict[str, Optional[float]]],
        fights_sampled: int,
    ) -> tuple[list[StatEdgeOut], list[StatEdgeOut], str]:
        comp_note = (
            f"Compared against the tournament average across "
            f"{len(scope_metrics)} team(s) in the sampled matches."
        )
        if fights_sampled < 5 or len(scope_metrics) < 2:
            return [], [], (
                comp_note
                + " Insufficient data for strengths/weaknesses "
                f"({fights_sampled} fights sampled; need >= 5 and >= 2 teams)."
            )
        mine = scope_metrics.get(team_id, {})
        avgs: dict[str, float] = {}
        for metric in self.METRIC_LABELS:
            vals = [
                m[metric]
                for tid, m in scope_metrics.items()
                if m[metric] is not None
            ]
            if vals:
                avgs[metric] = sum(vals) / len(vals)

        scored: list[tuple[str, float, float, float]] = []  # metric, mine, avg, rel
        for metric, avg in avgs.items():
            v = mine.get(metric)
            if v is None or avg == 0:
                continue
            rel = (v - avg) / abs(avg)
            scored.append((metric, v, avg, rel))

        strengths = [
            StatEdgeOut(
                metric=m,
                label=f"{self.METRIC_LABELS[m]} {v:.2f} vs {a:.2f} tournament avg",
                team_value=round(v, 3),
                tournament_avg=round(a, 3),
                delta_pct=round(r * 100, 1),
            )
            for m, v, a, r in sorted(scored, key=lambda x: x[3], reverse=True)
            if r > 0
        ][:3]
        weaknesses = [
            StatEdgeOut(
                metric=m,
                label=f"{self.METRIC_LABELS[m]} {v:.2f} vs {a:.2f} tournament avg",
                team_value=round(v, 3),
                tournament_avg=round(a, 3),
                delta_pct=round(r * 100, 1),
            )
            for m, v, a, r in sorted(scored, key=lambda x: x[3])
            if r < 0
        ][:3]
        return strengths, weaknesses, comp_note

    async def _tendencies(
        self,
        team_id: str,
        rotations: list[RotationEvent],
        team_fights: list[FightDetailOut],
        match_ids: set[str],
    ) -> TendenciesOut:
        # — drop locations: first rotation of each match as the drop proxy —
        first_rot: dict[str, RotationEvent] = {}
        for r in sorted(rotations, key=lambda r: r.timestamp_ms):
            first_rot.setdefault(r.match_id, r)
        loc_counter: Counter[str] = Counter()
        for r in first_rot.values():
            label = (r.meta or {}).get(META_LOCATION_LABEL)
            if isinstance(label, str) and label.strip():
                loc_counter[label.strip()] += 1
        drop_locations = [
            {"location": loc, "matches": cnt}
            for loc, cnt in loc_counter.most_common(3)
        ]
        if drop_locations:
            drop_note = (
                "Drop proxy: location_label of each match's first recorded "
                "rotation event."
            )
        elif first_rot:
            drop_note = (
                "Rotation events exist but carry no location_label — "
                "drop locations unknown."
            )
        else:
            drop_note = "No rotation events recorded — drop locations unknown."

        # — rotation timing: early / mid / late thirds of the match —
        timing = {"early": 0, "mid": 0, "late": 0, "unknown": 0, "label": "unknown", "note": ""}
        durations = await self._match_durations(match_ids)
        for r in rotations:
            dur = durations.get(r.match_id)
            if not dur:
                timing["unknown"] += 1
                continue
            frac = r.timestamp_ms / dur
            if frac < 1 / 3:
                timing["early"] += 1
            elif frac < 2 / 3:
                timing["mid"] += 1
            else:
                timing["late"] += 1
        known = timing["early"] + timing["mid"] + timing["late"]
        if known:
            dom = max(("early", "mid", "late"), key=lambda k: timing[k])
            timing["label"] = f"{dom} rotator"
            timing["note"] = (
                f"{timing[dom]}/{known} rotations in the {dom} third of the match "
                "(by match duration)."
            )
        else:
            timing["note"] = (
                "Rotation timing unknown: match durations not recorded."
                if rotations
                else "No rotation events recorded."
            )

        # — centrality: team fight-centroid vs tournament fight-centroid —
        # Classified relative to the median team distance (center-leaning /
        # edge-leaning), with the raw numbers shown. Coordinate units depend
        # on the producer; the comparison is relative, never metres.
        centrality: dict[str, Any] = {"label": "unknown", "note": ""}
        located = [
            f
            for f in team_fights
            if f.location_x is not None and f.location_y is not None
        ]
        if len(located) >= 3:
            all_fights, _ = await self.fights.list_fights(limit=10_000)
            per_team_pts: dict[str, list[tuple[float, float]]] = defaultdict(list)
            for f in all_fights:
                if f.location_x is None or f.location_y is None:
                    continue
                for t in f.teams:
                    per_team_pts[t.team_id].append((f.location_x, f.location_y))  # type: ignore[arg-type]
            all_pts = [p for pts in per_team_pts.values() for p in pts]
            if all_pts and len(per_team_pts.get(team_id, [])) >= 3:
                tx = sum(p[0] for p in all_pts) / len(all_pts)
                ty = sum(p[1] for p in all_pts) / len(all_pts)
                dists: dict[str, float] = {}
                centroids: dict[str, tuple[float, float]] = {}
                for tid, pts in per_team_pts.items():
                    if len(pts) < 3:
                        continue
                    cx_t = sum(p[0] for p in pts) / len(pts)
                    cy_t = sum(p[1] for p in pts) / len(pts)
                    centroids[tid] = (cx_t, cy_t)
                    dists[tid] = math.hypot(cx_t - tx, cy_t - ty)
                med = _median(list(dists.values()))
                my_dist = dists[team_id]
                cx, cy = centroids[team_id]
                label = (
                    "center-leaning"
                    if med is not None and my_dist < med
                    else "edge-leaning" if med is not None and my_dist > med
                    else "average"
                )
                centrality = {
                    "label": label,
                    "team_centroid": [round(cx, 2), round(cy, 2)],
                    "tournament_centroid": [round(tx, 2), round(ty, 2)],
                    "distance_from_tournament_centroid": round(my_dist, 2),
                    "median_team_distance": round(med, 2) if med is not None else None,
                    "located_fights": len(located),
                    "note": (
                        "Team fight-centroid distance vs the tournament "
                        "fight-centroid, classified against the median team "
                        f"distance over {len(dists)} team(s) with >= 3 located "
                        "fights."
                    ),
                }
            else:
                centrality["note"] = "No located fights tournament-wide."
        else:
            centrality["note"] = (
                f"Only {len(located)} fight(s) with coordinates — "
                "centrality unknown."
            )

        return TendenciesOut(
            drop_locations=drop_locations,
            drop_location_note=drop_note,
            rotation_timing=timing,
            centrality=centrality,
        )

    async def _match_durations(self, match_ids: set[str]) -> dict[str, float]:
        if not match_ids:
            return {}
        rows = (
            await self.db.execute(
                select(
                    Match.id,
                    Match.duration_seconds,
                    Match.actual_start_time,
                    Match.actual_end_time,
                ).where(Match.id.in_(match_ids))
            )
        ).all()
        out: dict[str, float] = {}
        for mid, dur_s, start, end in rows:
            if dur_s:
                out[mid] = float(dur_s) * 1000
            elif start and end:
                out[mid] = (end - start).total_seconds() * 1000
        return out

    async def _head_to_head(
        self,
        team_id: str,
        team_fights: list[FightDetailOut],
        kills: list[KillEvent],
    ) -> list[HeadToHeadOut]:
        opponents: dict[str, dict[str, int]] = defaultdict(
            lambda: {"fights": 0, "won": 0, "lost": 0, "disengaged": 0}
        )
        for f in team_fights:
            for t in f.teams:
                if t.team_id == team_id:
                    continue
                o = opponents[t.team_id]
                o["fights"] += 1
                if f.winner and f.winner.team_id == team_id:
                    o["won"] += 1
                elif f.winner and f.winner.team_id == t.team_id:
                    o["lost"] += 1
                elif f.outcome == "disengaged":
                    o["disengaged"] += 1
        if not opponents:
            return []

        # kills for/against per opponent (killer/victim -> team via players)
        pids: set[str] = set()
        for k in kills:
            pids.add(k.killer_player_id)
            pids.add(k.victim_player_id)
        pmap: dict[str, str] = {}
        if pids:
            prows = (
                await self.db.execute(select(Player).where(Player.id.in_(pids)))
            ).scalars().all()
            pmap = {p.id: p.team_id for p in prows}
        kf: Counter[str] = Counter()
        ka: Counter[str] = Counter()
        for k in kills:
            kt, vt = pmap.get(k.killer_player_id), pmap.get(k.victim_player_id)
            if kt == team_id and vt in opponents:
                kf[vt] += 1
            elif vt == team_id and kt in opponents:
                ka[kt] += 1

        names: dict[str, str] = {}
        trows = (
            await self.db.execute(select(Team).where(Team.id.in_(opponents.keys())))
        ).scalars().all()
        names = {t.id: t.name for t in trows}

        return [
            HeadToHeadOut(
                opponent_team_id=oid,
                opponent_name=names.get(oid, oid),
                fights=v["fights"],
                won=v["won"],
                lost=v["lost"],
                disengaged=v["disengaged"],
                kills_for=kf.get(oid, 0),
                kills_against=ka.get(oid, 0),
            )
            for oid, v in sorted(opponents.items(), key=lambda kv: -kv[1]["fights"])
        ]


# ══════════════════════════════════════════════════════════════════════
# ReportService — assembles report payloads for pdf.py
# ══════════════════════════════════════════════════════════════════════


class ReportService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.fights = FightService(db)
        self.dossiers = DossierService(db)

    async def get_match_report_data(self, match_id: str) -> MatchReportData:
        match = (
            await self.db.execute(select(Match).where(Match.id == match_id))
        ).scalar_one_or_none()
        if match is None:
            raise KeyError(f"match not found: {match_id}")

        tournament_name = "Unknown tournament"
        if match.tournament_id:
            t = (
                await self.db.execute(
                    select(Tournament).where(Tournament.id == match.tournament_id)
                )
            ).scalar_one_or_none()
            if t:
                tournament_name = t.name

        played_at = ""
        if match.actual_start_time:
            played_at = match.actual_start_time.isoformat()
        elif match.scheduled_time:
            played_at = match.scheduled_time.isoformat()

        fights, _ = await self.fights.list_fights(match_id=match_id, limit=10_000)

        # team set for this match: fight participants + kill participants + rotations
        team_ids: set[str] = set()
        for f in fights:
            team_ids.update(t.team_id for t in f.teams)
        kills = (
            await self.db.execute(
                select(KillEvent).where(KillEvent.match_id == match_id)
            )
        ).scalars().all()
        pids = {k.killer_player_id for k in kills} | {k.victim_player_id for k in kills}
        pmap: dict[str, str] = {}
        if pids:
            prows = (
                await self.db.execute(select(Player).where(Player.id.in_(pids)))
            ).scalars().all()
            pmap = {p.id: p.team_id for p in prows}
            team_ids.update(t for t in pmap.values() if t)
        rots = (
            await self.db.execute(
                select(RotationEvent).where(RotationEvent.match_id == match_id)
            )
        ).scalars().all()
        team_ids.update(r.team_id for r in rots)

        names: dict[str, str] = {}
        if team_ids:
            trows = (
                await self.db.execute(select(Team).where(Team.id.in_(team_ids)))
            ).scalars().all()
            names = {t.id: t.name for t in trows}

        kills_by_team: Counter[str] = Counter()
        for k in kills:
            kt = pmap.get(k.killer_player_id)
            if kt:
                kills_by_team[kt] += 1

        placements = await self._placements(match)

        summaries = [
            MatchTeamSummary(
                team_id=tid,
                team_name=names.get(tid, tid),
                placement=placements.get(tid, (None, ""))[0],
                placement_note=placements.get(tid, (None, ""))[1],
                finishes=kills_by_team.get(tid, 0),
                kills=kills_by_team.get(tid, 0),
            )
            for tid in sorted(team_ids, key=lambda t: names.get(t, t))
        ]
        # order by placement when known
        summaries.sort(
            key=lambda s: (s.placement is None, s.placement or 0, -s.kills)
        )

        rotation_highlights: list[dict[str, Any]] = []
        by_team: dict[str, list[RotationEvent]] = defaultdict(list)
        for r in rots:
            by_team[r.team_id].append(r)
        for tid, rs in sorted(by_team.items(), key=lambda kv: names.get(kv[0], kv[0])):
            rs_sorted = sorted(rs, key=lambda r: r.timestamp_ms)
            rotation_highlights.append(
                {
                    "team": names.get(tid, tid),
                    "rotations": len(rs_sorted),
                    "first_rotation_at_ms": rs_sorted[0].timestamp_ms,
                    "last_rotation_at_ms": rs_sorted[-1].timestamp_ms,
                }
            )

        notes: list[str] = []
        if not fights:
            notes.append("No fight events recorded for this match.")
        if not kills:
            notes.append("No kill events recorded for this match.")
        if not placements:
            notes.append(
                "Placements not recorded for this match — placement column "
                "is marked accordingly, not estimated."
            )

        return MatchReportData(
            match_id=match_id,
            tournament_name=tournament_name,
            match_number=match.match_number,
            map_name=match.map_name or "Unknown map",
            played_at=played_at,
            team_summaries=summaries,
            fights=fights,
            rotation_highlights=rotation_highlights,
            notes=notes,
        )

    async def _placements(self, match: Match) -> dict[str, tuple[Optional[int], str]]:
        """Placement per team_id, from real sources only (never estimated)."""
        out: dict[str, tuple[Optional[int], str]] = {}
        meta = match.meta or {}
        for key in ("placements", "results", "standings"):
            val = meta.get(key)
            if isinstance(val, dict) and val:
                for tid, place in val.items():
                    try:
                        out[str(tid)] = (int(place), f"from match.meta['{key}']")
                    except (TypeError, ValueError):
                        continue
                if out:
                    return out
        # fall back to match events
        events = (
            await self.db.execute(
                select(MatchEvent).where(
                    MatchEvent.match_id == match.id,
                    MatchEvent.event_type.in_(
                        ["placement", "match_placement", "final_standings", "team_placement"]
                    ),
                )
            )
        ).scalars().all()
        for e in events:
            data = e.event_data or {}
            tid = e.team_id or data.get("team_id")
            place = data.get("placement", data.get("place"))
            if tid and place is not None:
                try:
                    out[str(tid)] = (int(place), f"from match_events ({e.event_type})")
                except (TypeError, ValueError):
                    continue
        return out
