/**
 * AEGIS AI — standings template: Table.
 *
 * Port of the original /overlays/standings design: left-side table with
 * rank, team (colour chip + tag), kills, placement points and total
 * points. Eliminated teams are dimmed with an ELIM tag.
 *
 * Movement arrows: when the page passes a `movement` prop
 * (Record<teamId, 'up'|'down'|'same'> vs the previous match), each row
 * shows ▲ (green), ▼ (red) or — (neutral) plus a small legend. When the
 * prop is absent — the feed carries no previous-match snapshot today —
 * no arrows render (never faked).
 */

import { WaitingFeed } from '@/components/overlay-kit';
import type { StandingsProps } from '../registry';
import { staggerDelay } from './_anim';
import { StatusBadge } from './_player-board';

const GRID = 'grid-cols-[1.4rem_2rem_1fr_2.6rem_2.6rem_2.6rem]';

function MovementArrow({ dir }: { dir: 'up' | 'down' | 'same' | undefined }) {
  if (dir === undefined) return null;
  if (dir === 'up')
    return (
      <span className="text-emerald-400" title="Moved up vs previous match">
        ▲
      </span>
    );
  if (dir === 'down')
    return (
      <span className="text-red-400" title="Moved down vs previous match">
        ▼
      </span>
    );
  return (
    <span className="text-white/35" title="Same rank vs previous match">
      —
    </span>
  );
}

export default function StandingsTable({
  standings,
  status,
  movement,
}: StandingsProps) {
  if (standings.length === 0) return <WaitingFeed what="STANDINGS FEED" />;
  const showMovement = movement !== undefined && movement !== null;

  return (
    <div className="absolute left-5 top-1/2 w-[350px] -translate-y-1/2">
      <div className="gfx-anim-slide-in-left overflow-hidden rounded-xl bg-black/70 backdrop-blur-sm">
        <div
          className="h-1 w-full"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="px-4 pb-2 pt-2.5">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-mono text-xs font-bold tracking-[0.3em] text-white/80">
              STANDINGS
            </span>
            <StatusBadge status={status} />
          </div>
          <div
            className={`mb-1 grid ${GRID} items-center gap-1 font-mono text-[10px] tracking-[0.2em] text-white/45`}
          >
            {showMovement && <span />}
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
                  className={`gfx-anim-slide-in-left grid ${GRID} items-center gap-1 rounded px-1 py-1 ${
                    eliminated ? 'opacity-40' : ''
                  } ${i === 0 ? 'bg-white/10' : ''}`}
                  style={{ animationDelay: staggerDelay(i) }}
                >
                  {showMovement && (
                    <span className="font-mono text-xs">
                      <MovementArrow dir={movement?.[team.id]} />
                    </span>
                  )}
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
          {showMovement && (
            <div className="mt-2 flex items-center gap-4 border-t border-white/10 pt-1.5 font-mono text-[10px] tracking-[0.2em] text-white/45">
              <span className="text-emerald-400">▲ UP</span>
              <span className="text-red-400">▼ DOWN</span>
              <span className="text-white/35">— SAME</span>
              <span className="ml-auto">VS PREV MATCH</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
