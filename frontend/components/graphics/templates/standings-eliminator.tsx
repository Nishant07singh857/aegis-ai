/**
 * AEGIS AI — standings template: Eliminator Race.
 *
 * The top-5 finish leaders tallied LIVE from the kill feed — real data:
 * each kill event carries the killer's name and team, so finishes are
 * counted per (player, team) and re-ranked as the feed streams in. The
 * board emphasises the gap between #1 and #2 with a "N-FINISH GAP"
 * callout (Krafton-style). No kill events yet → the WaitingFeed state.
 *
 * Honest scope: the kill feed is per-match, so this board is labelled
 * THIS MATCH. A tournament-wide eliminator race needs a multi-match
 * feed (see docs/GRAPHICS.md) — the template never extrapolates one.
 */

import { useMemo } from 'react';
import { WaitingFeed } from '@/components/overlay-kit';
import type { KillEvent } from '@/lib/useLiveSocket';
import type { StandingsProps } from '../registry';
import {
  BoardHeader,
  PlayerBoardShell,
  PlayerRow,
  StatCell,
} from './_player-board';

const GRID = 'grid-cols-[2rem_1fr_3.2rem]';

interface Contender {
  playerName: string;
  teamTag: string;
  teamId: string;
  finishes: number;
}

function tallyEliminators(events: KillEvent[]): Contender[] {
  const byPlayer = new Map<string, Contender>();
  for (const e of events) {
    const key = `${e.killerTeamId}::${e.killer}`;
    const cur = byPlayer.get(key);
    if (cur) {
      cur.finishes += 1;
    } else {
      byPlayer.set(key, {
        playerName: e.killer,
        teamTag: e.killerTeam,
        teamId: e.killerTeamId,
        finishes: 1,
      });
    }
  }
  return [...byPlayer.values()].sort(
    (a, b) => b.finishes - a.finishes || a.playerName.localeCompare(b.playerName)
  );
}

export default function StandingsEliminator({
  killFeed = [],
  standings,
  status,
}: StandingsProps) {
  const contenders = useMemo(() => tallyEliminators(killFeed).slice(0, 5), [killFeed]);

  if (contenders.length === 0) return <WaitingFeed what="KILL FEED" />;

  const gap =
    contenders.length > 1
      ? contenders[0].finishes - contenders[1].finishes
      : 0;

  const colorOf = (teamId: string) =>
    standings.find((t) => t.id === teamId)?.color;

  return (
    <PlayerBoardShell
      title="ELIMINATOR RACE"
      subtitle="TOP FINISHERS — THIS MATCH"
      status={status}
      header={
        <>
          <div
            className={`gfx-anim-pop mb-2 inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 font-mono text-xs font-bold tracking-[0.2em] ${
              gap > 0
                ? 'border-amber-400/40 bg-amber-400/10 text-amber-300'
                : 'border-white/15 bg-white/5 text-white/60'
            }`}
          >
            {gap > 0 ? (
              <>
                <span>⚡</span>
                <span>
                  {contenders[0].playerName.toUpperCase()} LEADS BY {gap}{' '}
                  {gap === 1 ? 'FINISH' : 'FINISHES'}
                </span>
              </>
            ) : (
              <span>DEAD HEAT AT THE TOP</span>
            )}
          </div>
          <BoardHeader
            gridCols={GRID}
            labels={[{ text: 'FIN', right: true }]}
          />
        </>
      }
    >
      {contenders.map((c, i) => (
        <PlayerRow
          key={`${c.teamId}::${c.playerName}`}
          index={i}
          gridCols={GRID}
          playerName={c.playerName}
          teamTag={c.teamTag}
          teamColor={colorOf(c.teamId)}
          highlight={i === 0}
          cells={<StatCell value={c.finishes} strong={i === 0} />}
        />
      ))}
    </PlayerBoardShell>
  );
}
