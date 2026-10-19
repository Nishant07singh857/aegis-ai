/**
 * AEGIS AI — live WWCD% prediction hook (Phase 20, W1 — Global Predictive / G1).
 *
 * Polls GET /api/v1/predictions/win-probability every 30s and returns the
 * per-team win probabilities for the live match. The backend recomputes
 * from real match state on every call (alive players, zone position,
 * form, head-to-head); when it cannot compute (404) or the caller is
 * unauthorised (401/403), the hook returns null and the cards render
 * "—" — never invented percentages.
 */

'use client';

import { useEffect, useState } from 'react';
import { API_BASE } from '@/lib/api';

export const WIN_PROB_POLL_MS = 30_000;

export interface WinProbPrediction {
  team_id: string;
  team_name: string;
  win_probability: number | null;
  /** Change vs the previous 30s snapshot; null on first sighting. */
  delta: number | null;
  alive_count: number;
  kills: number;
  in_zone: boolean;
}

interface WinProbabilityResponse {
  match_id: string;
  updated_at_utc: string;
  teams_alive: number;
  zone_phase: number | null;
  teams: WinProbPrediction[];
}

interface LiveMatchBody {
  data?: { id?: string };
}

function sanitiseTeams(rows: WinProbPrediction[]): WinProbPrediction[] | null {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const clean = rows
    .filter((r) => r && typeof r.team_id === 'string')
    .map((r) => ({
      ...r,
      win_probability:
        typeof r.win_probability === 'number' && Number.isFinite(r.win_probability)
          ? Math.min(100, Math.max(0, r.win_probability))
          : null,
    }));
  return clean.length > 0 ? clean : null;
}

export function useWinProbability(
  apiBase: string = API_BASE,
  token?: string | null
): {
  predictions: WinProbPrediction[] | null;
  updatedAt: string | null;
  teamsAlive: number | null;
  zonePhase: number | null;
} {
  const [predictions, setPredictions] = useState<WinProbPrediction[] | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [teamsAlive, setTeamsAlive] = useState<number | null>(null);
  const [zonePhase, setZonePhase] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchPredictions = async () => {
      try {
        const base = apiBase.replace(/\/+$/, '');
        const headers: Record<string, string> = token
          ? { Authorization: `Bearer ${token}` }
          : {};
        const liveRes = await fetch(`${base}/api/v1/matches/live`, { headers });
        if (!liveRes.ok) {
          if (!cancelled) setPredictions(null);
          return;
        }
        const liveBody = (await liveRes.json()) as LiveMatchBody;
        const matchId = liveBody.data?.id;
        if (!matchId) {
          if (!cancelled) {
            setPredictions(null);
            setTeamsAlive(null);
            setZonePhase(null);
          }
          return;
        }
        const res = await fetch(
          `${base}/api/v1/predictions/win-probability?match_id=${encodeURIComponent(matchId)}`,
          { headers }
        );
        if (!res.ok) {
          // 401/403/404 or feed down — cards stay in the honest "—" state.
          if (!cancelled) setPredictions(null);
          return;
        }
        const body = (await res.json()) as WinProbabilityResponse;
        if (!cancelled) {
          setPredictions(sanitiseTeams(body.teams));
          setUpdatedAt(body.updated_at_utc ?? null);
          setTeamsAlive(body.teams_alive ?? null);
          setZonePhase(body.zone_phase ?? null);
        }
      } catch {
        /* feed unreachable — cards stay in their honest "—" state */
        if (!cancelled) setPredictions(null);
      }
    };

    fetchPredictions();
    const t = setInterval(fetchPredictions, WIN_PROB_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [apiBase, token]);

  return { predictions, updatedAt, teamsAlive, zonePhase };
}
