/**
 * AEGIS AI — shared internals for the standings templates
 * (Phase 8, W2 excellence pass).
 *
 * NOT a registered template — imported by the templates above. Contains
 * the player-leaderboard board shell, the staggered row wrapper,
 * survival-time formatting, the HonestStat renderer, and the shared
 * LIVE/SYNCING status badge (also used by standings-table):
 * a value the feed has not delivered (null/undefined/NaN) always renders
 * "—", never a fabricated number.
 */

import type { ReactNode } from 'react';
import type { ConnStatus } from '@/lib/useLiveSocket';
import { staggerDelay } from './_anim';

const MISSING = '—';

/** Render a possibly-missing numeric stat honestly. */
export function HonestStat({
  value,
  format,
}: {
  value: number | null | undefined;
  format?: (n: number) => string;
}) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return (
      <span className="text-white/30" title="Not delivered by the feed">
        {MISSING}
      </span>
    );
  }
  return <>{format ? format(value) : value}</>;
}

/** seconds → "m:ss" survival clock, or the missing dash. */
export function fmtSurvival(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return MISSING;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/** The shared ● LIVE / ○ SYNCING pill used by the board shells. */
export function StatusBadge({ status }: { status: ConnStatus }) {
  const live = status === 'live';
  return (
    <span
      className={`font-mono text-[10px] tracking-[0.25em] ${
        live ? 'text-emerald-400' : 'text-amber-400'
      }`}
    >
      {live ? '● LIVE' : '○ SYNCING'}
    </span>
  );
}

export function PlayerBoardShell({
  title,
  subtitle,
  status,
  header,
  children,
  widthPx,
}: {
  title: string;
  subtitle: string;
  status: ConnStatus;
  /** The column-header row (grid), supplied by the template. */
  header: ReactNode;
  children: ReactNode;
  /** Board width in px; default 560 (the IGL board goes wider). */
  widthPx?: number;
}) {
  return (
    <div
      className="absolute left-5 top-1/2 -translate-y-1/2"
      style={{ width: `${widthPx ?? 560}px` }}
    >
      <div className="overflow-hidden rounded-xl bg-black/70 backdrop-blur-sm">
        <div
          className="h-1 w-full"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="px-4 pb-2.5 pt-2.5">
          <div className="mb-1 flex items-center justify-between">
            <div>
              <div className="font-mono text-xs font-bold tracking-[0.3em] text-white/80">
                {title}
              </div>
              <div className="font-mono text-[10px] tracking-[0.2em] text-white/40">
                {subtitle}
              </div>
            </div>
            <StatusBadge status={status} />
          </div>
          {header}
          <div className="flex flex-col gap-0.5">{children}</div>
        </div>
      </div>
    </div>
  );
}

const HEADER_CELL =
  'font-mono text-[10px] tracking-[0.2em] text-white/45';

/** Header label cell; `right` right-aligns numeric columns. */
function Col({ right, children }: { right?: boolean; children: ReactNode }) {
  return (
    <span className={`${HEADER_CELL}${right ? ' text-right' : ''}`}>
      {children}
    </span>
  );
}

/**
 * Column-header grid that matches the rows: `#`, `PLAYER`, then the
 * template's labels. `gridCols` must equal the rows' `gridCols`.
 */
export function BoardHeader({
  gridCols,
  labels,
}: {
  gridCols: string;
  labels: { text: string; right?: boolean }[];
}) {
  return (
    <div className={`mb-1 grid ${gridCols} items-center gap-1`}>
      <span className={HEADER_CELL}>#</span>
      <span className={HEADER_CELL}>PLAYER</span>
      {labels.map((l) => (
        <Col key={l.text} right={l.right}>
          {l.text}
        </Col>
      ))}
    </div>
  );
}

/**
 * One leaderboard row: rank, player (tag + team chip), then the template's
 * stat cells. GPU-friendly staggered entrance (transform/opacity only).
 */
export function PlayerRow({
  index,
  playerName,
  teamTag,
  teamColor,
  gridCols,
  cells,
  highlight,
}: {
  index: number;
  playerName: string;
  teamTag: string;
  teamColor?: string;
  /** Tailwind grid-cols-[...] class matching the board's header. */
  gridCols: string;
  cells: ReactNode;
  highlight?: boolean;
}) {
  return (
    <div
      className={`gfx-anim-slide-in-left grid ${gridCols} items-center gap-1 rounded px-1 py-1 ${
        highlight ? 'bg-white/10' : ''
      }`}
      style={{ animationDelay: staggerDelay(index) }}
    >
      <span className="font-mono text-sm font-bold text-white/70 tabular-nums">
        {String(index + 1).padStart(2, '0')}
      </span>
      <span className="flex min-w-0 items-center gap-2">
        <span
          className="inline-block h-4 w-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: teamColor ?? 'var(--ov-accent)' }}
        />
        <span className="min-w-0">
          <span className="block truncate text-base font-extrabold tracking-wide">
            {playerName}
          </span>
          <span className="block font-mono text-[10px] tracking-[0.25em] text-white/45">
            {teamTag}
          </span>
        </span>
      </span>
      {cells}
    </div>
  );
}

/** Right-aligned numeric stat cell using the honesty renderer. */
export function StatCell({
  value,
  format,
  strong,
}: {
  value: number | null | undefined;
  format?: (n: number) => string;
  strong?: boolean;
}) {
  return (
    <span
      className={`text-right font-mono text-sm tabular-nums ${
        strong ? 'font-black text-white' : 'text-white/85'
      }`}
    >
      <HonestStat value={value} format={format} />
    </span>
  );
}
