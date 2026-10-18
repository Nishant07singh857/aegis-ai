/**
 * AEGIS AI — killfeed template: Ticker (Phase 8, W2 excellence pass).
 *
 * Port of the original /overlays/killfeed design: top-right ticker where
 * the newest kill slides in from the right — killer → victim, weapon, and
 * a KNOCK badge for knocks (vs finishes).
 *
 * Exit animation: the page TTL-filters `events`, so an expired kill used
 * to unmount instantly. Rows removed from the prop now stay mounted for
 * one exit beat (gfx-anim-slide-out-right) via useExitingList before they
 * unmount — no more pop-out.
 */

import type { KillEvent } from '@/lib/useLiveSocket';
import { WaitingFeed } from '@/components/overlay-kit';
import type { KillfeedProps } from '../registry';
import { useExitingList } from './_anim';

const killKey = (kill: KillEvent) => kill.id;

function KillRow({ kill, exiting }: { kill: KillEvent; exiting: boolean }) {
  return (
    <div
      className={`${
        exiting ? 'gfx-anim-slide-out-right' : 'gfx-anim-slide-in-right'
      } flex items-center gap-2.5 rounded-lg bg-black/70 px-4 py-2 backdrop-blur-sm`}
    >
      <div className="min-w-0 flex-1 truncate text-right text-lg font-bold">
        <span className="text-white">{kill.killer}</span>
        <span className="ml-2 font-mono text-[11px] tracking-[0.15em] text-white/55">
          {kill.killerTeam}
        </span>
      </div>
      {kill.isKnock ? (
        <span
          className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-bold tracking-[0.15em] text-black"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        >
          KNOCK
        </span>
      ) : (
        <span className="shrink-0 font-mono text-sm font-bold text-red-400">
          ✕
        </span>
      )}
      <div className="min-w-0 flex-1 truncate text-lg font-bold">
        <span className="text-white/90">{kill.victim}</span>
        <span className="ml-2 font-mono text-[11px] tracking-[0.15em] text-white/55">
          {kill.victimTeam}
        </span>
      </div>
      {kill.weapon ? (
        <span className="shrink-0 font-mono text-[11px] uppercase tracking-[0.1em] text-white/50">
          {kill.weapon}
        </span>
      ) : null}
    </div>
  );
}

export default function KillfeedTicker({ events }: KillfeedProps) {
  const rows = useExitingList(events, killKey);

  // `rows` (not `events`): when the last kill's TTL expires it still gets
  // its exit animation instead of cutting straight to the empty state.
  if (rows.length === 0) return <WaitingFeed what="KILL FEED" />;

  return (
    <div className="absolute right-5 top-5 flex w-[380px] flex-col gap-2">
      {rows.map(({ key, item, exiting }) => (
        <KillRow key={key} kill={item} exiting={exiting} />
      ))}
    </div>
  );
}
