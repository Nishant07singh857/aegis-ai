/**
 * AEGIS AI — lower-third shared bits (Phase 8, W2 excellence pass).
 *
 * NOT a registered template (underscore prefix, like _player-board.tsx).
 *
 * The honest standby pill every lower-third template shows until the first
 * real commentary line arrives (or while the graphic is hidden between
 * lines). No mock text anywhere.
 */

import type { ConnStatus } from '@/lib/useLiveSocket';

export function LowerThirdStandby({ status }: { status: ConnStatus }) {
  return (
    <div className="absolute bottom-12 left-8">
      <div className="rounded bg-black/50 px-4 py-2 font-mono text-[11px] tracking-[0.3em] text-white/50">
        {status === 'live'
          ? 'STANDBY — AWAITING COMMENTARY'
          : 'WAITING FOR COMMENTARY FEED'}
      </div>
    </div>
  );
}
