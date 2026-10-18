/**
 * AEGIS AI — standings template: Best IGL Board.
 *
 * Columns: rank, IGL name, team, IGL rating, avg points, WWCD count,
 * top-5s, avg survival and IGL avg finishes. Only players flagged IGL
 * (roster role = "IGL") are shown.
 *
 * Honest-data contract: IGL rating, avg points, top-5s, avg survival and
 * avg finishes come from GET /api/v1/matches/{id}/player-stats when a
 * game-stats feed records them; missing fields render "—". WWCD is
 * joined from the live team standings frames (team-level data — real).
 * No IGL-flagged players in the feed yet → the WaitingFeed state.
 */

import { useMemo } from 'react';
import { WaitingFeed } from '@/components/overlay-kit';
import type { StandingsProps } from '../registry';
import {
  BoardHeader,
  PlayerBoardShell,
  PlayerRow,
  StatCell,
  fmtSurvival,
} from './_player-board';

const GRID =
  'grid-cols-[2rem_1fr_3.2rem_3.2rem_3rem_2.8rem_3.6rem_3.4rem]';

export default function StandingsIgl({
  players = [],
  standings,
  status,
}: StandingsProps) {
  const igls = useMemo(() => {
    const wwcdByTeam = new Map(
      standings.map((t) => [t.id, t.wwcd ?? null])
    );
    return players
      .filter((p) => p.isIgl)
      .map((p) => ({
        ...p,
        wwcd: p.wwcd ?? wwcdByTeam.get(p.teamId) ?? null,
      }))
      .sort((a, b) => (b.iglRating ?? -1) - (a.iglRating ?? -1))
      .slice(0, 8);
  }, [players, standings]);

  if (igls.length === 0) return <WaitingFeed what="IGL FEED" />;

  return (
    <PlayerBoardShell
      title="BEST IGL"
      subtitle="IN-GAME LEADERS"
      status={status}
      widthPx={720}
      header={
        <BoardHeader
          gridCols={GRID}
          labels={[
            { text: 'RTG', right: true },
            { text: 'AVG', right: true },
            { text: 'WWCD', right: true },
            { text: 'T5', right: true },
            { text: 'SURV', right: true },
            { text: 'FIN', right: true },
          ]}
        />
      }
    >
      {igls.map((p, i) => (
        <PlayerRow
          key={p.playerId}
          index={i}
          gridCols={GRID}
          playerName={p.playerName}
          teamTag={p.teamTag}
          teamColor={p.teamColor}
          highlight={i === 0}
          cells={
            <>
              <StatCell
                value={p.iglRating}
                format={(n) => n.toFixed(2)}
                strong={i === 0}
              />
              <StatCell
                value={p.avgPoints}
                format={(n) => n.toFixed(1)}
              />
              <StatCell value={p.wwcd} />
              <StatCell value={p.top5s} />
              <StatCell value={p.survivalSeconds} format={fmtSurvival} />
              <StatCell value={p.avgFinishes} format={(n) => n.toFixed(1)} />
            </>
          }
        />
      ))}
    </PlayerBoardShell>
  );
}
