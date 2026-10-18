/**
 * AEGIS AI — scorebug template: Minimal.
 *
 * Thin full-width top strip: the leading team tags with their points and
 * the alive count only. Designed to sit over gameplay without covering it.
 */

import { WaitingFeed } from '@/components/overlay-kit';
import type { ScorebugProps } from '../registry';

export default function ScorebugMinimal({ standings, summary }: ScorebugProps) {
  const sorted = [...standings].sort(
    (a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0)
  );
  const teamsAlive =
    summary?.teams_alive ?? standings.filter((t) => t.alive).length;
  const teamsTotal = standings.length;

  if (sorted.length === 0) return <WaitingFeed />;

  return (
    <div className="absolute left-0 right-0 top-0">
      <div className="gfx-anim-fade-in">
        <div className="flex items-center justify-between bg-black/60 px-6 py-1.5 backdrop-blur-sm">
          <div className="flex items-center gap-5">
            {sorted.slice(0, 4).map((team) => (
              <div key={team.id} className="flex items-center gap-2">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{
                    backgroundColor: team.color ?? 'var(--ov-accent)',
                  }}
                />
                <span className="text-sm font-extrabold tracking-wider">
                  {team.tag ?? team.name}
                </span>
                <span className="font-mono text-sm font-bold tabular-nums text-white/85">
                  {team.total_pts ?? 0}
                </span>
              </div>
            ))}
          </div>
          <div className="font-mono text-xs font-bold tracking-[0.25em] text-white/75">
            {teamsTotal > 0 ? `${teamsAlive}/${teamsTotal} ALIVE` : '—'}
          </div>
        </div>
        <div
          className="h-0.5 w-full"
          style={{
            background:
              'linear-gradient(90deg, transparent, var(--ov-accent), transparent)',
          }}
        />
      </div>
    </div>
  );
}
