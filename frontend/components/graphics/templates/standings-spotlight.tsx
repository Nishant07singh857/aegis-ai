/**
 * AEGIS AI — standings template: Team Spotlight.
 *
 * A big bold featured-team card for the current leader (the page sorts
 * standings by points, so standings[0] is the featured team): rank,
 * tag, name, logo, total points and kills are real feed data.
 *
 * Honest-data contract: damage, knocks and avg survival time are NOT
 * part of the live team frames today, so they render "—" with a tooltip
 * ("waiting for feed") instead of invented numbers. When a game-stats
 * feed starts delivering them (per-team or per-player aggregates), the
 * values flow in through the same props — see docs/GRAPHICS.md.
 */

import { WaitingFeed } from '@/components/overlay-kit';
import type { StandingsProps } from '../registry';
import { HonestStat, fmtSurvival } from './_player-board';

function StatBlock({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg bg-white/5 px-4 py-3">
      <div className="font-mono text-[10px] tracking-[0.25em] text-white/45">
        {label}
      </div>
      <div className="font-mono text-2xl font-black tabular-nums text-white">
        {children}
      </div>
    </div>
  );
}

export default function StandingsSpotlight({ standings }: StandingsProps) {
  if (standings.length === 0) return <WaitingFeed what="STANDINGS FEED" />;

  const team = standings[0];
  const rank = team.rank ?? 1;

  return (
    <div className="absolute left-5 top-1/2 w-[460px] -translate-y-1/2">
      <div className="gfx-anim-pop overflow-hidden rounded-2xl bg-black/70 backdrop-blur-sm">
        <div
          className="h-1.5 w-full"
          style={{ backgroundColor: team.color ?? 'var(--ov-accent)' }}
        />
        <div className="px-6 pb-5 pt-4">
          <div className="mb-4 flex items-center justify-between">
            <span className="font-mono text-xs font-bold tracking-[0.3em] text-white/80">
              TEAM SPOTLIGHT
            </span>
            <span className="rounded bg-white/10 px-2.5 py-1 font-mono text-xs font-black tracking-[0.2em] text-white">
              RANK {String(rank).padStart(2, '0')}
              <span className="font-normal text-white/50">
                {' '}
                / {standings.length}
              </span>
            </span>
          </div>

          <div className="mb-5 flex items-center gap-4">
            <span className="text-6xl leading-none" aria-hidden="true">
              {team.logo ?? '🏆'}
            </span>
            <div className="min-w-0">
              <div
                className="truncate text-4xl font-black tracking-wide"
                style={{ color: team.color ?? 'var(--ov-accent)' }}
              >
                {team.tag ?? team.name}
              </div>
              <div className="truncate font-mono text-sm tracking-[0.2em] text-white/55">
                {(team.name ?? '').toUpperCase()}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2.5">
            <StatBlock label="TOTAL POINTS">{team.total_pts ?? 0}</StatBlock>
            <StatBlock label="FINISHES">{team.kills ?? 0}</StatBlock>
            <StatBlock label="DAMAGE">
              <HonestStat value={undefined} />
            </StatBlock>
            <StatBlock label="AVG SURVIVAL">
              {fmtSurvival(undefined)}
            </StatBlock>
          </div>

          <div className="mt-3 font-mono text-[10px] tracking-[0.2em] text-white/35">
            DAMAGE / SURVIVAL AWAIT THE GAME-STATS FEED
          </div>
        </div>
      </div>
    </div>
  );
}
