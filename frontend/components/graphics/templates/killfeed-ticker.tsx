/**
 * AEGIS AI — killfeed template: Ticker.
 *
 * Port of the original /overlays/killfeed design: top-right ticker where
 * the newest kill slides in at the top — killer → victim, weapon, and a
 * KNOCK badge for knocks (vs finishes).
 */

import type { KillEvent } from '@/lib/useLiveSocket';
import { WaitingFeed } from '@/components/overlay-kit';
import type { KillfeedProps } from '../registry';

function KillRow({ kill }: { kill: KillEvent }) {
  return (
    <div className="ov-anim-slide-in flex items-center gap-2.5 rounded-lg bg-black/70 px-4 py-2 backdrop-blur-sm">
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
  if (events.length === 0) return <WaitingFeed what="KILL FEED" />;

  return (
    <div className="absolute right-5 top-5 flex w-[380px] flex-col gap-2">
      {events.map((kill) => (
        <KillRow key={kill.id} kill={kill} />
      ))}
    </div>
  );
}
