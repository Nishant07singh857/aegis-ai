'use client';

/**
 * AEGIS AI — top-center live team stat card (Phase 20, W1 — Global Predictive / G2).
 *
 * PMGC-style: the currently observed team's live stats — flag/abbr +
 * finishes, damage, knocks, survival. Wired to the same event source as
 * the facecam/player panel: the director state's active_camera slot,
 * resolved to a team via GET /api/v1/observers (useObservedTeam in
 * game-overlays.tsx). The parent keys this component on the camera slot
 * so an observer switch hard-cuts the card, per Bible §3.
 *
 * Position: top-center (Bible V2 §1c — the PMGC slot India leaves empty;
 * shared with the WWCD% cards as a stacked mode). The parent places this
 * inside the top-center column; this component renders only the card.
 *
 * Honest-data rule (Phase 9): every stat renders "—" until a feed
 * supplies it. Team finishes come from the live standings; damage /
 * knocks / survival aggregate the player-stats feed (empty today, so
 * "—" — never guessed from team totals).
 */

import { useMemo } from 'react';
import type { Team } from '@/lib/useLiveSocket';
import type { PlayerStat } from '@/components/graphics/registry';

function tagFor(name: string | null): string {
  if (!name) return '—';
  const clean = name.replace(/[^A-Za-z0-9 ]/g, '').trim();
  if (!clean) return '—';
  const words = clean.split(/\s+/);
  if (words.length > 1)
    return words.map((w) => w[0]).join('').slice(0, 4).toUpperCase();
  return clean.slice(0, 4).toUpperCase();
}

function fmtSurvival(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export interface TopCenterStatsProps {
  slot: number;
  teamId: string | null;
  teamName: string | null;
  teamColor?: string;
  standings: Team[];
  players: PlayerStat[];
  className?: string;
}

export function TopCenterStats({
  slot,
  teamId,
  teamName,
  teamColor,
  standings,
  players,
  className = '',
}: TopCenterStatsProps) {
  const stats = useMemo(() => {
    // Resolve the observed team: prefer the observer-mapping id, fall
    // back to a case-insensitive name match, never a guessed team.
    const row =
      standings.find((t) => teamId != null && t.id === teamId) ??
      standings.find(
        (t) =>
          teamName != null &&
          (t.name ?? '').toLowerCase() === teamName.toLowerCase()
      );
    const roster = teamId != null ? players.filter((p) => p.teamId === teamId) : [];

    const sum = (f: (p: PlayerStat) => number | null | undefined) => {
      const vals = roster.map(f).filter((v): v is number => v != null && Number.isFinite(v));
      return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) : null;
    };
    const survivals = roster
      .map((p) => p.survivalSeconds)
      .filter((v): v is number => v != null && Number.isFinite(v));

    return {
      row,
      finishes: row?.kills ?? null,
      damage: sum((p) => p.damage),
      knocks: sum((p) => p.knocks),
      survival: survivals.length > 0 ? Math.max(...survivals) : null,
    };
  }, [standings, players, teamId, teamName]);

  const label = stats.row?.name ?? teamName ?? `CAM ${slot}`;
  const tag = tagFor(stats.row?.name ?? teamName);
  const accent = teamColor ?? stats.row?.color ?? 'var(--ov-accent)';

  const cells: Array<{ label: string; value: string }> = [
    {
      label: 'FINISHES',
      value: stats.finishes != null ? String(stats.finishes) : '—',
    },
    {
      label: 'DAMAGE',
      value: stats.damage != null ? String(Math.round(stats.damage)) : '—',
    },
    {
      label: 'KNOCKS',
      value: stats.knocks != null ? String(stats.knocks) : '—',
    },
    { label: 'SURVIVAL', value: fmtSurvival(stats.survival) },
  ];

  return (
    <div
      className={`ag-enter-fade rounded-lg border border-white/12 bg-black/70 backdrop-blur-sm ${className}`}
      aria-live="polite"
      aria-label={`Observed team stats: ${label}`}
    >
      <div className="flex items-center gap-2.5 px-3 pt-2">
        <span
          className="flex h-5 w-8 shrink-0 items-center justify-center rounded-sm font-mono text-[9px] font-black tracking-wider text-black"
          style={{ backgroundColor: accent }}
        >
          {tag}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] font-black tracking-wide text-white">
          {label}
        </span>
        <span className="shrink-0 font-mono text-[9px] tracking-[0.25em] text-white/45">
          CAM {slot}
        </span>
      </div>
      <div className="flex items-center gap-4 px-3 pb-2 pt-1.5 font-mono text-[11px] font-bold tracking-[0.15em] text-white/60">
        {cells.map((c) => (
          <span key={c.label}>
            {c.label}{' '}
            <span className="text-white tabular-nums">{c.value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

export default TopCenterStats;
