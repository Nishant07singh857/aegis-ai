/**
 * AEGIS AI — killfeed template: Stack.
 *
 * Stacked kill cards that fade in as kills land: each card carries an
 * accent edge, the killer → victim line and the weapon underneath.
 * Expired kills drop off the stack (expiry is driven by the page's ?ttl=).
 */

import type { KillEvent } from '@/lib/useLiveSocket';
import { WaitingFeed } from '@/components/overlay-kit';
import type { KillfeedProps } from '../registry';

function KillCard({ kill }: { kill: KillEvent }) {
  return (
    <div className="gfx-anim-fade-in overflow-hidden rounded-lg bg-black/70 backdrop-blur-sm">
      <div className="flex items-stretch">
        <div
          className="w-1.5 shrink-0"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="min-w-0 flex-1 px-4 py-2">
          <div className="flex min-w-0 items-center gap-2 text-lg font-bold">
            <span className="truncate text-white">{kill.killer}</span>
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
            <span className="truncate text-white/90">{kill.victim}</span>
          </div>
          <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-white/50">
            <span className="truncate">
              {kill.killerTeam} → {kill.victimTeam}
            </span>
            {kill.weapon ? <span className="shrink-0">· {kill.weapon}</span> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function KillfeedStack({ events }: KillfeedProps) {
  if (events.length === 0) return <WaitingFeed what="KILL FEED" />;

  return (
    <div className="absolute right-5 top-5 flex w-[380px] flex-col gap-2">
      {events.map((kill) => (
        <KillCard key={kill.id} kill={kill} />
      ))}
    </div>
  );
}
