'use client';

/**
 * AEGIS AI — killfeed overlay (OBS browser source).
 *
 * Route: /overlays/killfeed
 *
 * Renders the active killfeed graphics template (components/graphics):
 * the kill ticker fed by live WS `kill` / `kill_feed_init` frames.
 * Zero mock data: the ticker is empty until the feed speaks.
 *
 * Query params:
 *   ?match=<id>    Accepted for consistency; the WS carries the live feed
 *                  (display-only, see scorebug header for details).
 *   ?accent=<hex>  Accent colour, e.g. ?accent=%23ff3366 (default #00f0ff).
 *   ?api=<url>     Backend API base override (also re-points the WS).
 *   ?ttl=<secs>    Seconds a kill stays visible (default 12, min 3, max 60).
 *   ?template=<id> Pin one killfeed template for this source, e.g.
 *                  ?template=killfeed-stack. Unknown ids fall back to the
 *                  type default. Precedence: ?template= > the director
 *                  state's active template > the default (killfeed-ticker).
 */

import { Suspense, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useLiveSocket } from '@/lib/useLiveSocket';
import { useOverlayParams } from '@/lib/overlay-params';
import { resolveTemplate } from '@/lib/graphics';
import { OverlayRoot, ConnBadge } from '@/components/overlay-kit';

const MAX_VISIBLE = 8;

function clampTtl(raw: string | null): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 12;
  return Math.min(60, Math.max(3, Math.round(n)));
}

function KillfeedInner() {
  const params = useOverlayParams();
  const sp = useSearchParams();
  const ttl = useMemo(() => clampTtl(sp.get('ttl')), [sp]);
  const { status, retryCount, killFeed, directorState } = useLiveSocket(
    params.api
  );
  const [tick, setTick] = useState(0);

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
    // `tick` forces recomputation every second; the memo would otherwise
    // stay stale until the next kill arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [killFeed, ttl, tick]);

  const def = resolveTemplate('killfeed', sp.get('template'), directorState);
  const Template = def.component;

  return (
    <OverlayRoot accent={params.accent}>
      <Template events={visible} accent={params.accent} status={status} />
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
