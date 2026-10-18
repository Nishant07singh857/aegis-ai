'use client';

/**
 * AEGIS AI — Team panels (Phase 10, graphics-staging).
 *
 * Team info panels: name, logo initial, total points, finishes,
 * alive count, placement.
 *
 * Real data shape (GET /api/v1/teams -> { data: [...] }, TeamResponse):
 *   { id, name, tag, logo, color, rank, kills, placement_pts,
 *     total_pts, wwcd, alive, players, source }
 *
 * Fields the backend does NOT track are rendered as "—", never invented:
 * per-team alive *count* (only a team-level `alive` boolean exists) and
 * placement when `rank` is absent (DB-sourced teams report None for stats
 * columns). `rank` doubles as placement.
 *
 * Panels accept data via props AND expose a usePanelsData() hook that
 * reads the real endpoint. Props win when provided.
 */

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/lib/auth';

/** One team row, mirroring the real TeamResponse payload. */
export interface TeamRow {
  id: string;
  name: string;
  tag?: string | null;
  color?: string | null;
  rank?: number | null;
  /** Finishes. */
  kills?: number | null;
  placement_pts?: number | null;
  total_pts?: number | null;
  wwcd?: number | null;
  alive?: boolean | null;
  players?: string[] | null;
}

interface TeamsResponse {
  data: TeamRow[];
}

/**
 * Read team panels data from the real /api/v1/teams endpoint.
 * Returns null while loading (caller renders its own standby).
 */
export function usePanelsData(): TeamRow[] | null {
  const { token } = useAuth();
  const [teams, setTeams] = useState<TeamRow[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<TeamsResponse>('/api/v1/teams', { token })
      .then((res) => {
        if (!cancelled) {
          setTeams(Array.isArray(res?.data) ? res.data : []);
        }
      })
      .catch(() => {
        if (!cancelled) setTeams([]);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return teams;
}

function TeamPanel({ team }: { team: TeamRow }) {
  const initial = (team.tag ?? team.name ?? '?').trim().charAt(0).toUpperCase() || '?';
  const accent = team.color ?? '#00f0ff';
  return (
    <div
      className="rounded-lg border bg-white/[0.03] px-4 py-3"
      style={{ borderColor: `${accent}55` }}
    >
      <div className="flex items-center gap-3">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-xl font-black text-white"
          style={{ backgroundColor: `${accent}33` }}
          aria-hidden
        >
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-bold text-white">
            {team.name}
          </div>
          <div className="text-xs text-white/50">
            {typeof team.rank === 'number' ? `#${team.rank}` : '—'}
            {' · '}
            {team.alive === true ? (
              <span className="text-emerald-400">Alive</span>
            ) : team.alive === false ? (
              <span className="text-red-400">Eliminated</span>
            ) : (
              <span className="text-white/40">—</span>
            )}
          </div>
        </div>
        <div
          className="shrink-0 text-2xl font-black"
          style={{ color: accent }}
          title="Total points"
        >
          {typeof team.total_pts === 'number' ? team.total_pts : '—'}
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-4 gap-2 border-t border-white/10 pt-2.5 text-center">
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-white/40">
            Points
          </dt>
          <dd className="text-sm font-bold text-white">
            {typeof team.total_pts === 'number' ? team.total_pts : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-white/40">
            Finishes
          </dt>
          <dd className="text-sm font-bold text-white">
            {typeof team.kills === 'number' ? team.kills : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-white/40">
            Alive
          </dt>
          {/* Per-team alive COUNT is not tracked — only the team-level
              alive flag. Render "—" honestly. */}
          <dd className="text-sm font-bold text-white/40" title="Alive count is not tracked by the backend">
            —
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-white/40">
            Place
          </dt>
          <dd className="text-sm font-bold text-white">
            {typeof team.rank === 'number' ? `#${team.rank}` : '—'}
          </dd>
        </div>
      </dl>
    </div>
  );
}

export interface TeamPanelsProps {
  /** Explicit data (wins over the hook). */
  teams?: TeamRow[] | null;
  title?: string;
  className?: string;
}

/** Grid of team panels. Honest empty state when there is no data. */
export function TeamPanels({
  teams: teamsProp,
  title = 'Teams',
  className = '',
}: TeamPanelsProps) {
  const hookTeams = usePanelsData();
  const teams = teamsProp !== undefined ? teamsProp : hookTeams;

  return (
    <section className={className} aria-label={title}>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-widest text-white/40">
        {title}
      </h3>
      {teams === null && (
        <p className="rounded-lg border border-white/10 bg-white/[0.02] px-4 py-6 text-center text-sm text-white/50">
          Loading teams…
        </p>
      )}
      {teams !== null && teams.length === 0 && (
        <p className="rounded-lg border border-white/10 bg-white/[0.02] px-4 py-6 text-center text-sm text-white/50">
          No team data available — the teams endpoint returned nothing.
        </p>
      )}
      {teams !== null && teams.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {teams.map((t) => (
            <TeamPanel key={t.id} team={t} />
          ))}
        </div>
      )}
    </section>
  );
}
