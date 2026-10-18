'use client';

/**
 * AEGIS AI — lower-third overlay (OBS browser source).
 *
 * Route: /overlays/lower-third
 *
 * Renders the active lower-third graphics template (components/graphics):
 * the event label (commentary type, e.g. WIPE / KNOCK / ZONE) and the
 * latest commentary line. Shows when a new line arrives, auto-hides after
 * ~6 seconds, reappears on the next line.
 *
 * Data: GET /api/v1/commentary/latest (polled every 3s) — the commentary
 * stream is not exposed by useLiveSocket, so HTTP polling is the honest
 * source. The WS is still opened (via useLiveSocket) so the reconnect badge
 * reflects feed health. Zero mock data: nothing renders until the first
 * real commentary line arrives.
 *
 * Query params:
 *   ?match=<id>    Accepted for consistency; the feed carries live
 *                  commentary (display-only, see scorebug header).
 *   ?accent=<hex>  Accent colour, e.g. ?accent=%2300f0ff (default #00f0ff).
 *   ?api=<url>     Backend API base override (poll + WS both follow it).
 *   ?hold=<secs>   Seconds the graphic stays visible (default 6, min 2,
 *                  max 30).
 *   ?template=<id> Pin one lower-third template for this source, e.g.
 *                  ?template=lowerthird-banner. Unknown ids fall back to
 *                  the type default. Precedence: ?template= > the director
 *                  state's active template > the default (lowerthird-slide).
 */

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useLiveSocket } from '@/lib/useLiveSocket';
import { useLatestCommentary } from '@/lib/useCommentary';
import { useOverlayParams } from '@/lib/overlay-params';
import { resolveTemplate } from '@/lib/graphics';
import { OverlayRoot, ConnBadge } from '@/components/overlay-kit';

function clampHold(raw: string | null): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 6;
  return Math.min(30, Math.max(2, Math.round(n)));
}

function LowerThirdInner() {
  const params = useOverlayParams();
  const sp = useSearchParams();
  const hold = useMemo(() => clampHold(sp.get('hold')), [sp]);
  const { status, retryCount, directorState } = useLiveSocket(params.api);

  const line = useLatestCommentary(params.api);
  const [visible, setVisible] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shownTs = useRef<number>(-1);

  useEffect(() => {
    if (line && line.timestamp !== shownTs.current) {
      shownTs.current = line.timestamp;
      setVisible(true);
      if (hideTimer.current) clearTimeout(hideTimer.current);
      hideTimer.current = setTimeout(() => {
        setVisible(false);
      }, hold * 1000);
    }
    return () => {
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, [line, hold]);

  const def = resolveTemplate('lower-third', sp.get('template'), directorState);
  const Template = def.component;
  const label = (line?.type ?? 'AEGIS AI').toUpperCase();

  return (
    <OverlayRoot accent={params.accent}>
      <Template
        visible={visible}
        title={label}
        subtitle={line?.text ?? ''}
        accent={params.accent}
        status={status}
        animKey={line ? `${line.timestamp}` : 'standby'}
      />
      <ConnBadge status={status} retryCount={retryCount} />
    </OverlayRoot>
  );
}

export default function LowerThirdPage() {
  return (
    <Suspense fallback={null}>
      <LowerThirdInner />
    </Suspense>
  );
}
