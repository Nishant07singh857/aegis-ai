/**
 * AEGIS AI — scorebug template: Classic.
 *
 * Port of the original /overlays/scorebug design: compact top-centre bar
 * with the two leading teams (tag, total points, kills), LIVE badge, zone
 * phase, match clock and teams-alive count in the middle.
 */

import type { Team } from '@/lib/useLiveSocket';
import { WaitingFeed } from '@/components/overlay-kit';
import { fmtClock, type ScorebugProps } from '../registry';

function TeamBlock({
  team,
  align,
}: {
  team: Team | undefined;
  align: 'left' | 'right';
}) {
  if (!team) return <div className="w-44" />;
  return (
    <div
      className={`flex w-44 items-center gap-3 px-4 py-2 ${
        align === 'right' ? 'flex-row-reverse text-right' : ''
      }`}
    >
      <span
        className="inline-block h-9 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: team.color ?? 'var(--ov-accent)' }}
      />
      <div className="min-w-0">
        <div className="truncate text-xl font-extrabold tracking-wide">
          {team.tag ?? team.name}
        </div>
        <div className="font-mono text-[11px] tracking-[0.2em] text-white/60">
          {team.kills ?? 0} KILLS
        </div>
      </div>
      <div
        className={`text-4xl font-black tabular-nums ${
          align === 'right' ? 'mr-auto' : 'ml-auto'
        }`}
      >
        {team.total_pts ?? 0}
      </div>
    </div>
  );
}

export default function ScorebugClassic({
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

  return (
    <div className="absolute left-1/2 top-4 -translate-x-1/2">
      <div className="overflow-hidden rounded-xl bg-black/70 backdrop-blur-sm">
        <div
          className="h-1 w-full"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="flex items-stretch divide-x divide-white/10">
          <TeamBlock team={first} align="left" />
          <div className="flex min-w-40 flex-col items-center justify-center gap-0.5 px-5 py-2">
            <div className="flex items-center gap-2 font-mono text-[11px] tracking-[0.25em] text-white/70">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  status === 'live'
                    ? 'bg-emerald-400'
                    : 'bg-amber-400 ov-anim-pulse'
                }`}
              />
              {matchLabel}
            </div>
            <div className="font-mono text-sm font-bold tracking-[0.2em]">
              {summary?.zone_phase != null
                ? `ZONE ${summary.zone_phase}`
                : 'ZONE —'}
              {summary?.match_time_seconds != null && (
                <span className="text-white/60">
                  {' '}
                  · {fmtClock(summary.match_time_seconds)}
                </span>
              )}
            </div>
            <div className="font-mono text-[11px] tracking-[0.2em] text-white/60">
              {teamsTotal > 0 ? `${teamsAlive}/${teamsTotal} ALIVE` : '—'}
            </div>
          </div>
          <TeamBlock team={second} align="right" />
        </div>
      </div>
    </div>
  );
}
