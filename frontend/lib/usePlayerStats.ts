'use client';

/**
 * AEGIS AI — per-player stats hook (Phase 8, W1).
 *
 * Feeds the player-leaderboard standings templates (MVP, Best IGL,
 * Finals MVP) with real data from
 * GET /api/v1/matches/{match_id}/player-stats.
 *
 * Honest by design: the backend returns only what a game-stats feed has
 * genuinely recorded; the table is empty today, so this hook returns []
 * and the boards render their honest empty states. Ratings are never
 * derived client-side from team totals — the raw fields map 1:1 onto the
 * PlayerStat contract in components/graphics/registry.ts.
 */

import { useEffect, useState } from 'react';
import { API_BASE } from '@/lib/api';
import type { PlayerStat } from '@/components/graphics/registry';

const POLL_MS = 30_000;

interface PlayerStatsRow {
  player_id?: string;
  player_name?: string;
  team_id?: string;
  team_tag?: string;
  team_color?: string | null;
  finishes?: number | null;
  damage?: number | null;
  survival_seconds?: number | null;
  knocks?: number | null;
  rating?: number | null;
  igl_rating?: number | null;
  finals_mvp_rating?: number | null;
  is_igl?: boolean;
  avg_points?: number | null;
  wwcd?: number | null;
  top_5s?: number | null;
  avg_finishes?: number | null;
}

function toPlayerStat(row: PlayerStatsRow): PlayerStat | null {
  const id = row.player_id ?? '';
  if (!id) return null;
  return {
    playerId: id,
    playerName: row.player_name ?? id,
    teamId: row.team_id ?? '',
    teamTag: row.team_tag ?? '',
    teamColor: row.team_color ?? undefined,
    finishes: row.finishes ?? null,
    damage: row.damage ?? null,
    survivalSeconds: row.survival_seconds ?? null,
    knocks: row.knocks ?? null,
    rating: row.rating ?? null,
    iglRating: row.igl_rating ?? null,
    finalsMvpRating: row.finals_mvp_rating ?? null,
    isIgl: row.is_igl ?? false,
    avgPoints: row.avg_points ?? null,
    wwcd: row.wwcd ?? null,
    top5s: row.top_5s ?? null,
    avgFinishes: row.avg_finishes ?? null,
  };
}

export function usePlayerStats(apiBase: string = API_BASE): PlayerStat[] {
  const [players, setPlayers] = useState<PlayerStat[]>([]);

  useEffect(() => {
    let cancelled = false;

    const fetchStats = async () => {
      try {
        const liveRes = await fetch(`${apiBase}/api/v1/matches/live`);
        if (!liveRes.ok) return;
        const liveBody = (await liveRes.json()) as { data?: { id?: string } };
        const matchId = liveBody.data?.id;
        if (!matchId) return;

        const res = await fetch(
          `${apiBase}/api/v1/matches/${encodeURIComponent(matchId)}/player-stats`
        );
        if (!res.ok) return;
        const body = (await res.json()) as {
          data?: { players?: PlayerStatsRow[] };
        };
        const rows = body.data?.players ?? [];
        if (!cancelled) {
          setPlayers(rows.flatMap((r) => {
            const p = toPlayerStat(r);
            return p ? [p] : [];
          }));
        }
      } catch {
        /* feed unavailable — boards stay in their honest empty state */
      }
    };

    fetchStats();
    const t = setInterval(fetchStats, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [apiBase]);

  return players;
}
