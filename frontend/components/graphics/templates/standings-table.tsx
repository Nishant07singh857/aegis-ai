/**
 * AEGIS AI — standings template: Table.
 *
 * Port of the original /overlays/standings design: left-side table with
 * rank, team (colour chip + tag), kills, placement points and total
 * points. Eliminated teams are dimmed with an ELIM tag.
 */

import { WaitingFeed } from '@/components/overlay-kit';
import type { StandingsProps } from '../registry';

export default function StandingsTable({
  standings,
  status,
}: StandingsProps) {
  if (standings.length === 0) return <WaitingFeed what="STANDINGS FEED" />;

  return (
    <div className="absolute left-5 top-1/2 w-[330px] -translate-y-1/2">
      <div className="overflow-hidden rounded-xl bg-black/70 backdrop-blur-sm">
        <div
          className="h-1 w-full"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="px-4 pb-2 pt-2.5">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-mono text-xs font-bold tracking-[0.3em] text-white/80">
              STANDINGS
            </span>
            <span
              className={`font-mono text-[10px] tracking-[0.25em] ${
                status === 'live' ? 'text-emerald-400' : 'text-amber-400'
              }`}
            >
              {status === 'live' ? '● LIVE' : '○ SYNCING'}
            </span>
          </div>
          <div className="mb-1 grid grid-cols-[2rem_1fr_2.6rem_2.6rem_2.6rem] items-center gap-1 font-mono text-[10px] tracking-[0.2em] text-white/45">
            <span>#</span>
            <span>TEAM</span>
            <span className="text-right">K</span>
            <span className="text-right">PLC</span>
            <span className="text-right">PTS</span>
          </div>
          <div className="flex flex-col gap-0.5">
            {standings.map((team, i) => {
              const eliminated = team.alive === false;
              return (
                <div
                  key={team.id}
                  className={`grid grid-cols-[2rem_1fr_2.6rem_2.6rem_2.6rem] items-center gap-1 rounded px-1 py-1 ${
                    eliminated ? 'opacity-40' : ''
                  } ${i === 0 ? 'bg-white/10' : ''}`}
                >
                  <span className="font-mono text-sm font-bold text-white/70 tabular-nums">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      className="inline-block h-4 w-1.5 shrink-0 rounded-full"
                      style={{
                        backgroundColor: team.color ?? 'var(--ov-accent)',
                      }}
                    />
                    <span className="truncate text-base font-extrabold tracking-wide">
                      {team.tag ?? team.name}
                    </span>
                    {eliminated && (
                      <span className="shrink-0 rounded bg-red-500/80 px-1 font-mono text-[9px] font-bold tracking-[0.15em]">
                        ELIM
                      </span>
                    )}
                  </span>
                  <span className="text-right font-mono text-sm tabular-nums text-white/85">
                    {team.kills ?? 0}
                  </span>
                  <span className="text-right font-mono text-sm tabular-nums text-white/85">
                    {team.placement_pts ?? 0}
                  </span>
                  <span
                    className="text-right font-mono text-base font-black tabular-nums"
                    style={
                      i === 0 ? { color: 'var(--ov-accent)' } : undefined
                    }
                  >
                    {team.total_pts ?? 0}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
