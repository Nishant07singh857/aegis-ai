/**
 * AEGIS AI — live WWCD% prediction cards (Phase 20, W1 — Global Predictive / G1).
 *
 * PMGO-style top-center cards for the top 5 remaining teams: flag/logo +
 * abbr + alive bars + "WWCD – NN%" with animated % bars, per-team delta
 * vs the previous 30s snapshot, and the leader glowing. Data comes from
 * the useWinProbability hook (GET /api/v1/predictions/win-probability,
 * 30s cadence); when the backend cannot compute, the cards render "—" —
 * percentages are never invented.
 *
 * Animation (docs/ANIMATED_GRAPHICS.md): entrances 0.5–1.0s ease-out,
 * bar fills via scaleX (GPU) with a 1s transition so 30s updates glide,
 * numbers count up via useCountUp (shared with Phase 19's WinProbBar).
 * The % bar mechanics reuse animated.css's ag-bar-track/ag-bar-fill and
 * ag-live-dot — the PMGO card layout itself (flag + alive bars +
 * "WWCD – NN%") is new, per docs/DEEP_ANALYSIS/VISUAL_BIBLE_V2.md §1.
 */

'use client';

import type { CSSProperties } from 'react';
import { useAnimationStyle } from './animation-style';
import { fmtCounted, useCountUp } from './_hooks';
import { useWinProbability, type WinProbPrediction } from '@/lib/useWinProbability';

const BAR_FALLBACK_COLORS = ['#00f0ff', '#f5b301', '#7c5cff', '#3ddc84', '#ff5c7a'];

function clampProb(p: number | null | undefined): number | null {
  if (p == null || !Number.isFinite(p)) return null;
  return Math.min(100, Math.max(0, p));
}

function abbrFor(name: string): string {
  const clean = name.replace(/[^A-Za-z0-9 ]/g, '').trim();
  if (!clean) return '—';
  const words = clean.split(/\s+/);
  if (words.length > 1) return words.map((w) => w[0]).join('').slice(0, 4).toUpperCase();
  return clean.slice(0, 4).toUpperCase();
}

/** Alive bars: 4 slots, filled for each alive player (PMGO style). */
function AliveBars({ alive, accent }: { alive: number; accent: string }) {
  const filled = Math.max(0, Math.min(4, alive));
  return (
    <span className="flex items-center gap-0.5" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <span
          key={i}
          className="h-3 w-1.5 rounded-[2px]"
          style={{
            backgroundColor: i < filled ? accent : 'rgba(255,255,255,0.14)',
            boxShadow: i < filled ? `0 0 6px ${accent}` : 'none',
          }}
        />
      ))}
    </span>
  );
}

function DeltaBadge({ delta }: { delta: number | null | undefined }) {
  if (delta == null || !Number.isFinite(delta)) return null;
  const up = delta > 0;
  const flat = delta === 0;
  return (
    <span
      className="font-mono text-[10px] font-bold tabular-nums"
      style={{ color: up ? '#3ddc84' : flat ? 'rgba(255,255,255,0.4)' : '#ff5c7a' }}
      title={`pichhle snapshot se ${delta >= 0 ? '+' : ''}${delta.toFixed(1)}%`}
    >
      {up ? '▲' : flat ? '•' : '▼'} {Math.abs(delta).toFixed(0)}
    </span>
  );
}

function TeamCard({
  team,
  rank,
  colorByTeam,
  tagByTeam,
  logoByTeam,
}: {
  team: WinProbPrediction;
  rank: number;
  colorByTeam?: Record<string, string>;
  tagByTeam?: Record<string, string>;
  logoByTeam?: Record<string, string>;
}) {
  const { enterClass } = useAnimationStyle();
  const prob = clampProb(team.win_probability);
  const counted = useCountUp(prob, 1000);
  const accent = colorByTeam?.[team.team_id] ?? BAR_FALLBACK_COLORS[rank % BAR_FALLBACK_COLORS.length];
  const tag = tagByTeam?.[team.team_id] ?? abbrFor(team.team_name);
  const logo = logoByTeam?.[team.team_id];
  const leader = rank === 0;

  return (
    <div
      className={`${enterClass} relative overflow-hidden rounded-lg bg-black/70 backdrop-blur-sm`}
      style={{ animationDelay: `${rank * 0.08}s`, border: leader ? `1px solid ${accent}` : '1px solid rgba(255,255,255,0.12)' }}
    >
      {leader && <span className="ag-glow" aria-hidden />}
      <div className="relative px-3 py-2">
        <div className="flex items-center gap-2.5">
          {/* flag / logo or abbr */}
          {logo ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={logo} alt="" className="h-6 w-6 shrink-0 rounded-sm object-contain" />
          ) : (
            <span
              className="flex h-6 w-9 shrink-0 items-center justify-center rounded-sm font-mono text-[10px] font-black tracking-wider text-black"
              style={{ backgroundColor: accent }}
            >
              {tag}
            </span>
          )}
          <span className="min-w-0 flex-1 truncate text-[13px] font-black tracking-wide text-white">
            {team.team_name}
          </span>
          <AliveBars alive={team.alive_count} accent={accent} />
          <span className="shrink-0 font-mono text-[13px] font-black tabular-nums text-white">
            {prob == null ? '—' : `WWCD – ${fmtCounted(counted)}%`}
          </span>
          <DeltaBadge delta={team.delta} />
        </div>
        {/* % bar: scaleX fill (GPU), 1s glide on 30s updates */}
        <div className="ag-bar-track mt-1.5 h-1.5" aria-hidden>
          <span
            className="ag-bar-fill"
            style={{ '--ag-fill': prob == null ? 0 : prob / 100, backgroundColor: accent } as CSSProperties}
          />
        </div>
      </div>
    </div>
  );
}

function Standby() {
  return (
    <div className="ag-enter-fade rounded-lg border border-white/10 bg-black/70 px-4 py-3 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[11px] font-bold tracking-[0.3em] text-white/60">
          WWCD% PREDICTION
        </span>
        <span className="font-mono text-[10px] tracking-[0.2em] text-white/40">
          AWAITING FEED
        </span>
      </div>
      <div className="mt-2 font-mono text-xs tracking-[0.25em] text-white/30">
        — — —
      </div>
    </div>
  );
}

export interface WwcdCardsProps {
  /** Override for the hook's own fetch (tests / storybook). */
  predictions?: WinProbPrediction[] | null;
  api?: string;
  token?: string | null;
  colorByTeam?: Record<string, string>;
  tagByTeam?: Record<string, string>;
  logoByTeam?: Record<string, string>;
  className?: string;
}

/**
 * Live WWCD% prediction cards. By default the hook polls the backend
 * every 30s; pass `predictions` to render a fixed snapshot instead.
 */
export default function WwcdCards({
  predictions: override,
  api,
  token,
  colorByTeam,
  tagByTeam,
  logoByTeam,
  className = '',
}: WwcdCardsProps) {
  const { predictions: live } = useWinProbability(
    api && api.length > 0 ? api : undefined,
    token ?? null
  );
  const predictions = override !== undefined ? override : live;
  const top5 = predictions?.slice(0, 5) ?? [];

  return (
    <div className={className} aria-live="polite">
      <div className="mb-1.5 flex items-center justify-between px-1">
        <span className="font-mono text-[11px] font-bold tracking-[0.3em] text-white/60">
          WWCD% PREDICTION
        </span>
        {top5.length > 0 && (
          <span className="flex items-center gap-1.5 rounded bg-red-500/15 px-2 py-0.5 font-mono text-[10px] font-bold tracking-[0.2em] text-red-400">
            <span className="ag-live-dot" aria-hidden />
            LIVE
          </span>
        )}
      </div>
      {top5.length === 0 ? (
        <Standby />
      ) : (
        <div className="flex flex-col gap-1.5">
          {top5.map((team, i) => (
            <TeamCard
              key={team.team_id}
              team={team}
              rank={i}
              colorByTeam={colorByTeam}
              tagByTeam={tagByTeam}
              logoByTeam={logoByTeam}
            />
          ))}
        </div>
      )}
    </div>
  );
}
