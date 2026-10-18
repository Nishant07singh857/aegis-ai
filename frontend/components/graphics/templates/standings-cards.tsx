/**
 * AEGIS AI — standings template: Cards.
 *
 * Team cards in a grid: rank, colour chip, tag, total points and kills
 * per card, with a staggered fade-in as the cards appear.
 */

import { WaitingFeed } from '@/components/overlay-kit';
import type { StandingsProps } from '../registry';

export default function StandingsCards({ standings }: StandingsProps) {
  if (standings.length === 0) return <WaitingFeed what="STANDINGS FEED" />;

  return (
    <div className="absolute left-5 top-5 w-[520px]">
      <div className="mb-2 font-mono text-xs font-bold tracking-[0.3em] text-white/80">
        STANDINGS
      </div>
      <div className="grid grid-cols-2 gap-2">
        {standings.map((team, i) => {
          const eliminated = team.alive === false;
          return (
            <div
              key={team.id}
              className={`gfx-anim-fade-in overflow-hidden rounded-lg bg-black/70 backdrop-blur-sm ${
                eliminated ? 'opacity-45' : ''
              }`}
              style={{ animationDelay: `${Math.min(i, 9) * 45}ms` }}
            >
              <div className="flex items-center gap-2.5 px-3.5 py-2.5">
                <span className="font-mono text-lg font-black tabular-nums text-white/60">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span
                  className="inline-block h-6 w-1.5 shrink-0 rounded-full"
                  style={{
                    backgroundColor: team.color ?? 'var(--ov-accent)',
                  }}
                />
                <span className="min-w-0 flex-1 truncate text-lg font-extrabold tracking-wide">
                  {team.tag ?? team.name}
                </span>
                <span
                  className="font-mono text-xl font-black tabular-nums"
                  style={
                    i === 0 ? { color: 'var(--ov-accent)' } : undefined
                  }
                >
                  {team.total_pts ?? 0}
                </span>
              </div>
              <div className="flex items-center justify-between border-t border-white/10 px-3.5 py-1.5 font-mono text-[10px] tracking-[0.2em] text-white/55">
                <span>{team.kills ?? 0} KILLS</span>
                {eliminated ? (
                  <span className="rounded bg-red-500/80 px-1.5 py-0.5 font-bold tracking-[0.15em] text-white">
                    ELIM
                  </span>
                ) : (
                  <span>{team.placement_pts ?? 0} PLC PTS</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
