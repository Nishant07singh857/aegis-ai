'use client';

/**
 * AEGIS AI — /broadcast/preshow (Phase 10).
 *
 * Full-screen pre-show broadcast view. Reads GET /api/v1/preshow/state on
 * mount (auth when available) and subscribes to `preshow.state` WS frames;
 * the WS frame is the freshest source and wins. The team list for the
 * match-intro flight-path card comes from the REAL teams API
 * (GET /api/v1/teams) — never invented.
 *
 * Operators (admin/operator roles) also get the staging control panel
 * (PreshowControls); viewers see the show only.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useRole } from '@/lib/useRole';
import PreshowView, {
  type PreshowFlightPath,
  type PreshowSnapshot,
  type PreshowTeam,
} from '@/components/graphics-staging/preshow-view';
import PreshowControls from '@/components/graphics-staging/preshow-controls';
import { useBroadcastSocket } from '@/components/graphics-staging/use-broadcast-socket';

interface TeamListResponse {
  data: Array<{ id: string; name: string; tag?: string | null }>;
}

interface FlightPathResponse {
  flight_path: PreshowFlightPath;
}

export default function PreshowPage() {
  const { token } = useAuth();
  const { isOperator } = useRole();
  const [state, setState] = useState<PreshowSnapshot | null>(null);
  const [teams, setTeams] = useState<PreshowTeam[]>([]);
  const [flightPath, setFlightPath] = useState<PreshowFlightPath | null>(null);
  const { latest } = useBroadcastSocket(['preshow.state']);
  const frame = latest['preshow.state'] as PreshowSnapshot | undefined;

  const fetchState = useCallback(async () => {
    try {
      const s = await apiFetch<PreshowSnapshot>('/api/v1/preshow/state', { token });
      setState(s);
    } catch {
      /* backend unreachable — WS may still deliver */
    }
  }, [token]);

  const fetchTeams = useCallback(async () => {
    try {
      const res = await apiFetch<TeamListResponse>('/api/v1/teams', { token });
      setTeams(
        (res.data ?? []).map((t) => ({
          id: t.id,
          name: t.name,
          tag: t.tag ?? undefined,
        })),
      );
    } catch {
      /* honest empty state: the view shows "loading" instead of fake teams */
    }
  }, [token]);

  const fetchFlightPath = useCallback(async () => {
    try {
      const res = await apiFetch<FlightPathResponse>('/api/v1/flight/path', { token });
      // Null when nothing came back — PreshowView then renders its
      // labelled schematic fallback; a path is never invented here.
      // An unset snapshot (is_set: false) also falls back in the view.
      setFlightPath(res.flight_path ?? null);
    } catch {
      /* backend unreachable — keep null so the schematic fallback renders */
    }
  }, [token]);

  useEffect(() => {
    fetchState();
    fetchTeams();
    fetchFlightPath();
  }, [fetchState, fetchTeams, fetchFlightPath]);

  useEffect(() => {
    if (frame) setState(frame);
  }, [frame]);

  return (
    <div style={{ background: '#05070d', minHeight: '100vh' }}>
      <PreshowView state={state} teams={teams} flightPath={flightPath} />
      {isOperator && <PreshowControls onChanged={fetchState} />}
    </div>
  );
}
