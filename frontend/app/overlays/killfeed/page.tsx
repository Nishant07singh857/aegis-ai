'use client';

/**
 * AEGIS AI — killfeed overlay (OBS browser source).
 *
 * Route: /overlays/killfeed
 *
 * Scrolling kill ticker, top-right: newest kill slides in at the top,
 * entries auto-expire after ~12 seconds. Shows killer → victim, weapon,
 * and a KNOCK badge for knocks (vs finishes).
 *
 * Data: live WS (/ws/live) `kill` / `kill_feed_init` frames via
 * useLiveSocket. Zero mock data: the ticker is empty until the feed speaks.
 *
 * Query params:
 *   ?match=<id>    Accepted for consistency; the WS carries the live feed
 *                  (display-only, see scorebug header for details).
 *   ?accent=<hex>  Accent colour, e.g. ?accent=%23ff3366 (default #00f0ff).
 *   ?api=<url>     Backend API base override (also re-points the WS).
 *   ?ttl=<secs>    Seconds a kill stays visible (default 12, min 3, max 60).
 */

import { Suspense, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useLiveSocket, type KillEvent } from '@/lib/useLiveSocket';
import { useOverlayParams } from '@/lib/overlay-params';
import { OverlayRoot, ConnBadge } from '@/components/overlay-kit';

const MAX_VISIBLE = 8;

function clampTtl(raw: string | null): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 12;
  return Math.min(60, Math.max(3, Math.round(n)));
}

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
        <span className="shrink-0 font-mono text-sm font-bold text-red-400">✕</span>
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

function KillfeedInner() {
  const params = useOverlayParams();
  const sp = useSearchParams();
  const ttl = useMemo(() => clampTtl(sp.get('ttl')), [sp]);
  const { status, retryCount, killFeed } = useLiveSocket(params.api);
  const [, setTick] = useState(0);

  // Re-render every second so expirations drop off on time.
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const visible = useMemo(() => {
    const nowSec = Date.now() / 1000;
    return killFeed
      .filter((k) => nowSec - k.timestamp <= ttl)
      .slice(0, MAX_VISIBLE);
  }, [killFeed, ttl]);

  return (
    <OverlayRoot accent={params.accent}>
      <div className="absolute right-5 top-5 flex w-[380px] flex-col gap-2">
        {visible.map((kill) => (
          <KillRow key={kill.id} kill={kill} />
        ))}
      </div>
      <ConnBadge status={status} retryCount={retryCount} />
    </OverlayRoot>
  );
}

export default function KillfeedPage() {
  return (
    <Suspense fallback={null}>
      <KillfeedInner />
    </Suspense>
  );
}
