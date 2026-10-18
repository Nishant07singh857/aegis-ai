/**
 * AEGIS AI — standings template: Finals MVP Board.
 *
 * Same columns as the MVP board (rank, player, team, rating, finishes,
 * damage, survival, knocks) but labelled FINALS MVP and driven by the
 * separate finals rating scale (the ~3.2x scale feeds record per finals
 * event) — the tournament MVP and the finals MVP are different boards.
 *
 * Same honest-data contract as standings-mvp: the `players` prop comes
 * from GET /api/v1/matches/{id}/player-stats; undelivered fields render
 * "—"; empty → the WaitingFeed state.
 */

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
  'grid-cols-[2rem_1fr_3.2rem_2.6rem_3.4rem_3.6rem_2.8rem]';

export default function StandingsFinalsMvp({
  players = [],
  status,
}: StandingsProps) {
  if (players.length === 0) return <WaitingFeed what="FINALS STATS FEED" />;

  const rows = [...players]
    .sort((a, b) => (b.finalsMvpRating ?? -1) - (a.finalsMvpRating ?? -1))
    .slice(0, 8);

  return (
    <PlayerBoardShell
      title="FINALS MVP"
      subtitle="GRAND FINALS RATING"
      status={status}
      header={
        <BoardHeader
          gridCols={GRID}
          labels={[
            { text: 'RTG', right: true },
            { text: 'FIN', right: true },
            { text: 'DMG', right: true },
            { text: 'SURV', right: true },
            { text: 'KNK', right: true },
          ]}
        />
      }
    >
      {rows.map((p, i) => (
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
                value={p.finalsMvpRating}
                format={(n) => n.toFixed(1)}
                strong={i === 0}
              />
              <StatCell value={p.finishes} />
              <StatCell value={p.damage} />
              <StatCell value={p.survivalSeconds} format={fmtSurvival} />
              <StatCell value={p.knocks} />
            </>
          }
        />
      ))}
    </PlayerBoardShell>
  );
}
