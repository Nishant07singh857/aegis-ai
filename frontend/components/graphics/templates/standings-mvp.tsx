/**
 * AEGIS AI — standings template: MVP Board.
 *
 * Per-player rows: rank, player + team, MVP rating (~1.0x scale),
 * finishes, damage, survival time and knocks. Rows reveal with a
 * staggered entrance.
 *
 * Honest-data contract: the `players` prop is populated by the page from
 * GET /api/v1/matches/{id}/player-stats (recorded by a game-stats feed).
 * Fields the feed has not delivered render "—" — ratings are NEVER
 * derived from team totals. No player data yet → the WaitingFeed state.
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

export default function StandingsMvp({
  players = [],
  status,
}: StandingsProps) {
  if (players.length === 0) return <WaitingFeed what="PLAYER STATS FEED" />;

  const rows = [...players]
    .sort((a, b) => (b.finishes ?? -1) - (a.finishes ?? -1))
    .slice(0, 8);

  return (
    <PlayerBoardShell
      title="MVP BOARD"
      subtitle="PLAYER LEADERBOARD"
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
                value={p.rating}
                format={(n) => n.toFixed(1)}
                strong={i === 0}
              />
              <StatCell value={p.finishes} strong={i === 0} />
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
