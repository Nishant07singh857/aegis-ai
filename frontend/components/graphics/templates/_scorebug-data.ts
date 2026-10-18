/**
 * AEGIS AI — shared data prep for the scorebug templates
 * (Phase 8, W2 excellence pass).
 *
 * The three scorebug templates (classic / modern / minimal) all need the
 * same derived values: standings sorted by total points, the top two
 * teams, and the alive/total counts (falling back to the live team frames
 * when the analytics summary hasn't arrived yet). Extracted so the logic
 * lives in exactly one place — the computed values are identical to the
 * previous per-file copies; no data behaviour changed.
 */

import { useMemo } from 'react';
import type { Team } from '@/lib/useLiveSocket';
import type { ScorebugSummary } from '../registry';

export interface ScorebugData {
  /** Standings sorted by total points, descending. */
  sorted: Team[];
  first: Team | undefined;
  second: Team | undefined;
  /** summary.teams_alive when the poll delivered it, else live frames. */
  teamsAlive: number;
  teamsTotal: number;
}

export function useScorebugData(
  standings: Team[],
  summary: ScorebugSummary | null
): ScorebugData {
  return useMemo(() => {
    const sorted = [...standings].sort(
      (a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0)
    );
    const [first, second] = sorted;
    const teamsAlive =
      summary?.teams_alive ?? standings.filter((t) => t.alive).length;
    return {
      sorted,
      first,
      second,
      teamsAlive,
      teamsTotal: standings.length,
    };
  }, [standings, summary]);
}
