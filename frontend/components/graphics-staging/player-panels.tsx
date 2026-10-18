'use client';

/**
 * AEGIS AI — Player panels (Phase 10, graphics-staging).
 *
 * Player cards: name, team, finishes, damage, alive status.
 *
 * Real data shape (GET /api/v1/players -> { data: [...] }):
 *   { id, name, team, role, kills, damage, mvp_score }
 *
 * The endpoint does NOT track per-player alive status — the cards show
 * "—" for that field rather than inventing it. (Team-level alive flags
 * exist on /api/v1/teams, not per player.)
 *
 * Panels accept data via props AND expose a usePanelsData() hook that
 * reads the real endpoint. Props win when provided — that lets broadcast
 * pages feed players from a live WS frame later without changing this
 * component.
 */

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/lib/auth';

/** One player row, mirroring the real /api/v1/players payload. */
export interface PlayerRow {
  id: string;
  name: string;
  team: string;
  role?: string;
  /** Finishes. */
  kills?: number;
  damage?: number;
  mvp_score?: number;
  /** Per-player alive status is not tracked by the backend — unknown
   *  renders as "—". */
  alive?: boolean | null;
}

interface PlayersResponse {
  data: PlayerRow[];
}

/**
 * Read player panels data from the real /api/v1/players endpoint.
 * Returns null while loading (caller renders its own standby).
 */
export function usePanelsData(): PlayerRow[] | null {
  const { token } = useAuth();
  const [players, setPlayers] = useState<PlayerRow[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<PlayersResponse>('/api/v1/players', { token })
      .then((res) => {
        if (!cancelled) {
          setPlayers(Array.isArray(res?.data) ? res.data : []);
        }
      })
      .catch(() => {
        if (!cancelled) setPlayers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return players;
}

function PlayerCard({ player }: { player: PlayerRow }) {
  const initial = (player.name ?? '?').trim().charAt(0).toUpperCase() || '?';
  return (
    <div className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-cyan-400/15 text-lg font-bold text-cyan-300">
        {initial}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-white">
          {player.name}
        </div>
        <div className="truncate text-xs text-white/50">
          {player.team}
          {player.role ? ` · ${player.role}` : ''}
        </div>
      </div>
      <dl className="flex shrink-0 items-center gap-4 text-center">
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-white/40">
            Finishes
          </dt>
          <dd className="text-sm font-bold text-white">
            {typeof player.kills === 'number' ? player.kills : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-white/40">
            Damage
          </dt>
          <dd className="text-sm font-bold text-white">
            {typeof player.damage === 'number'
              ? player.damage.toLocaleString()
              : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-white/40">
            Status
          </dt>
          <dd
            className={`text-sm font-bold ${
              player.alive === true
                ? 'text-emerald-400'
                : player.alive === false
                  ? 'text-red-400'
                  : 'text-white/40'
            }`}
          >
            {player.alive === true
              ? 'ALIVE'
              : player.alive === false
                ? 'OUT'
                : '—'}
          </dd>
        </div>
      </dl>
    </div>
  );
}

export interface PlayerPanelsProps {
  /** Explicit data (wins over the hook). */
  players?: PlayerRow[] | null;
  title?: string;
  className?: string;
}

/** Grid of player cards. Honest empty state when there is no data. */
export function PlayerPanels({
  players: playersProp,
  title = 'Players',
  className = '',
}: PlayerPanelsProps) {
  const hookPlayers = usePanelsData();
  const players = playersProp !== undefined ? playersProp : hookPlayers;

  return (
    <section className={className} aria-label={title}>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-widest text-white/40">
        {title}
      </h3>
      {players === null && (
        <p className="rounded-lg border border-white/10 bg-white/[0.02] px-4 py-6 text-center text-sm text-white/50">
          Loading players…
        </p>
      )}
      {players !== null && players.length === 0 && (
        <p className="rounded-lg border border-white/10 bg-white/[0.02] px-4 py-6 text-center text-sm text-white/50">
          No player data available — the players endpoint returned nothing.
        </p>
      )}
      {players !== null && players.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {players.map((p) => (
            <PlayerCard key={p.id} player={p} />
          ))}
        </div>
      )}
    </section>
  );
}
