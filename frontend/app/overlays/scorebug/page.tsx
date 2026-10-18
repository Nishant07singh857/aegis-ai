'use client';

/**
 * AEGIS AI — scorebug overlay (OBS browser source).
 *
 * Route: /overlays/scorebug
 *
 * Renders the active scorebug graphics template (components/graphics):
 * the two leading teams (tag, total points, kills), teams-alive count,
 * zone phase and match clock in the middle.
 *
 * Data: live WS (/ws/live) `standings` frames via useLiveSocket; zone phase /
 * match clock from GET /api/v1/analytics/summary (polled every 15s).
 * No mock data: templates render "waiting for feed" until the socket delivers.
 *
 * Query params:
 *   ?match=<id>    Display label (default "LIVE"). The WS carries the live
 *                  match feed; the value is display-only / reserved for a
 *                  future per-match subscription.
 *   ?accent=<hex>  Accent colour, e.g. ?accent=%2300f0ff (default #00f0ff).
 *   ?api=<url>     Backend API base override (also re-points the WS).
 *   ?template=<id> Pin one scorebug template for this source, e.g.
 *                  ?template=scorebug-minimal. Unknown ids fall back to the
 *                  type default. Precedence: ?template= > the director
 *                  state's active template > the default (scorebug-classic).
 */

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useLiveSocket } from '@/lib/useLiveSocket';
import { useOverlayParams } from '@/lib/overlay-params';
import { resolveTemplate } from '@/lib/graphics';
import type { ScorebugSummary } from '@/components/graphics/registry';
import { OverlayRoot, ConnBadge } from '@/components/overlay-kit';

function ScorebugInner() {
  const params = useOverlayParams();
  const sp = useSearchParams();
  const { status, retryCount, standings, directorState } = useLiveSocket(
    params.api
  );
  const [summary, setSummary] = useState<ScorebugSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${params.api}/api/v1/analytics/summary`);
        if (!res.ok) return;
        const body = (await res.json()) as { data?: ScorebugSummary };
        if (!cancelled && body.data) setSummary(body.data);
      } catch {
        /* feed may be down — keep last known values */
      }
    };
    load();
    const t = setInterval(load, 15_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [params.api]);

  const def = resolveTemplate('scorebug', sp.get('template'), directorState);
  const Template = def.component;
  const matchLabel =
    params.match === 'live' ? 'LIVE' : params.match.toUpperCase();

  return (
    <OverlayRoot accent={params.accent}>
      <Template
        standings={standings}
        summary={summary}
        accent={params.accent}
        status={status}
        matchLabel={matchLabel}
      />
      <ConnBadge status={status} retryCount={retryCount} />
    </OverlayRoot>
  );
}

export default function ScorebugPage() {
  return (
    <Suspense fallback={null}>
      <ScorebugInner />
    </Suspense>
  );
}
