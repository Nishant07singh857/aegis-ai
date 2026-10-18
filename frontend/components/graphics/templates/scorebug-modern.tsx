/**
 * AEGIS AI — scorebug template: Modern.
 *
 * Rounded pill bar with a gradient accent underline: the two leading
 * teams as chips (colour dot, tag, points), a centred LIVE / zone / clock
 * block, and the alive count.
 */

import { WaitingFeed } from '@/components/overlay-kit';
import { fmtClock, type ScorebugProps } from '../registry';

export default function ScorebugModern({
  standings,
  summary,
  status,
  matchLabel,
}: ScorebugProps) {
  const sorted = [...standings].sort(
    (a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0)
  );
  const [first, second] = sorted;
  const teamsAlive =
    summary?.teams_alive ?? standings.filter((t) => t.alive).length;
  const teamsTotal = standings.length;

  if (sorted.length === 0) return <WaitingFeed />;

  const chip = (tag: string | undefined, pts: number, color?: string) => (
    <div className="flex items-center gap-2.5">
      <span
        className="inline-block h-3 w-3 rounded-full"
        style={{ backgroundColor: color ?? 'var(--ov-accent)' }}
      />
      <span className="text-xl font-extrabold tracking-wide">{tag}</span>
      <span className="text-2xl font-black tabular-nums">{pts}</span>
    </div>
  );

  return (
    <div className="absolute left-1/2 top-4 -translate-x-1/2">
      <div className="gfx-anim-rise-up">
        <div
          className="flex items-center gap-6 rounded-full bg-black/75 px-7 py-2.5 backdrop-blur-sm"
          style={{
            boxShadow:
              '0 0 0 1px rgba(255,255,255,0.09), 0 10px 36px rgba(0,0,0,0.55)',
          }}
        >
          {chip(first?.tag ?? first?.name, first?.total_pts ?? 0, first?.color)}
          <div className="flex flex-col items-center gap-0.5 px-2">
            <div className="flex items-center gap-2 font-mono text-[10px] tracking-[0.25em] text-white/70">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  status === 'live'
                    ? 'bg-emerald-400'
                    : 'bg-amber-400 ov-anim-pulse'
                }`}
              />
              {matchLabel}
            </div>
            <div className="font-mono text-xs font-bold tracking-[0.18em]">
              {summary?.zone_phase != null
                ? `ZONE ${summary.zone_phase}`
                : 'ZONE —'}
              {summary?.match_time_seconds != null && (
                <span className="text-white/60">
                  {' '}
                  · {fmtClock(summary.match_time_seconds)}
                </span>
              )}
              <span className="text-white/60">
                {' '}
                · {teamsTotal > 0 ? `${teamsAlive}/${teamsTotal}` : '—'}
              </span>
            </div>
          </div>
          {chip(
            second?.tag ?? second?.name,
            second?.total_pts ?? 0,
            second?.color
          )}
        </div>
        <div
          className="mx-auto mt-1.5 h-1 w-3/4 rounded-full"
          style={{
            background:
              'linear-gradient(90deg, transparent, var(--ov-accent), transparent)',
          }}
        />
      </div>
    </div>
  );
}
