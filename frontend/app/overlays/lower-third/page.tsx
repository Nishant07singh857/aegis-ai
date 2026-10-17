'use client';

/**
 * AEGIS AI — lower-third overlay (OBS browser source).
 *
 * Route: /overlays/lower-third
 *
 * Broadcast lower-third graphic anchored bottom-left: an accent bar, the
 * event label (commentary type, e.g. WIPE / KNOCK / ZONE) and the latest
 * commentary line in large type. Slides up when a new line arrives,
 * auto-hides after ~6 seconds, reappears on the next line.
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
 */

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useLiveSocket } from '@/lib/useLiveSocket';
import { useOverlayParams } from '@/lib/overlay-params';
import { OverlayRoot, ConnBadge } from '@/components/overlay-kit';

interface CommentaryLine {
  timestamp: number;
  text: string;
  type: string;
}

const POLL_MS = 3_000;

function clampHold(raw: string | null): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 6;
  return Math.min(30, Math.max(2, Math.round(n)));
}

function LowerThirdInner() {
  const params = useOverlayParams();
  const sp = useSearchParams();
  const hold = useMemo(() => clampHold(sp.get('hold')), [sp]);
  const { status, retryCount } = useLiveSocket(params.api);

  const [line, setLine] = useState<CommentaryLine | null>(null);
  const [visible, setVisible] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seenTs = useRef<number>(-1);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${params.api}/api/v1/commentary/latest`);
        if (!res.ok) return;
        const body = (await res.json()) as { data?: CommentaryLine[] };
        const log = Array.isArray(body.data) ? body.data : [];
        const latest = log[log.length - 1];
        if (
          !cancelled &&
          latest &&
          typeof latest.text === 'string' &&
          latest.timestamp > seenTs.current
        ) {
          seenTs.current = latest.timestamp;
          setLine(latest);
          setVisible(true);
          if (hideTimer.current) clearTimeout(hideTimer.current);
          hideTimer.current = setTimeout(() => {
            if (!cancelled) setVisible(false);
          }, hold * 1000);
        }
      } catch {
        /* commentary endpoint unreachable — keep last line, badge shows state */
      }
    };
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, [params.api, hold]);

  const label = (line?.type ?? 'AEGIS AI').toUpperCase();

  return (
    <OverlayRoot accent={params.accent}>
      {line && visible ? (
        <div
          key={`${line.timestamp}-${line.text}`}
          className="ov-anim-rise-in absolute bottom-12 left-8 max-w-[46rem]"
        >
          <div className="overflow-hidden rounded-r-xl rounded-l-sm bg-black/75 backdrop-blur-sm">
            <div className="flex items-stretch">
              <div
                className="w-2 shrink-0"
                style={{ backgroundColor: 'var(--ov-accent)' }}
              />
              <div className="px-6 py-4">
                <div
                  className="mb-1 font-mono text-xs font-bold tracking-[0.35em]"
                  style={{ color: 'var(--ov-accent)' }}
                >
                  {label}
                </div>
                <div className="text-3xl font-extrabold leading-snug">
                  {line.text}
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="absolute bottom-12 left-8">
          <div className="rounded bg-black/50 px-4 py-2 font-mono text-[11px] tracking-[0.3em] text-white/50">
            {status === 'live'
              ? 'STANDBY — AWAITING COMMENTARY'
              : 'WAITING FOR COMMENTARY FEED'}
          </div>
        </div>
      )}
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
