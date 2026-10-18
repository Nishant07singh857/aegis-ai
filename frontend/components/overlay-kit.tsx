'use client';

/**
 * AEGIS AI — shared building blocks for the /overlays/* broadcast pages.
 *
 * Broadcast rules enforced here:
 *  - transparent page background (OBS captures the alpha channel),
 *  - no scrollbars (overflow: hidden everywhere),
 *  - large, heavy broadcast typography with a soft text shadow so the
 *    graphics stay readable over gameplay footage,
 *  - accent colour driven by ?accent= via the --ov-accent CSS variable.
 */

import type { CSSProperties, ReactNode } from 'react';
import type { ConnStatus } from '@/lib/useLiveSocket';

/**
 * Root wrapper every overlay page renders. Injects the transparent-body
 * style (the operator console's globals.css paints an opaque body, which
 * would kill the OBS alpha channel) and exposes --ov-accent.
 */
export function OverlayRoot({
  accent,
  children,
}: {
  accent: string;
  children: ReactNode;
}) {
  return (
    <>
      <style>{`
        html, body { background: transparent !important; }
        body { overflow: hidden; }
        .ov-root, .ov-root * { scrollbar-width: none !important; }
        .ov-root *::-webkit-scrollbar { display: none !important; }
        @keyframes ov-slide-in {
          from { transform: translateX(48px); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
        @keyframes ov-rise-in {
          from { transform: translate(-50%, 24px); opacity: 0; }
          to { transform: translate(-50%, 0); opacity: 1; }
        }
        @keyframes ov-fade-pulse {
          0%, 100% { opacity: 0.45; }
          50% { opacity: 1; }
        }
        .ov-anim-slide-in { animation: ov-slide-in 0.28s ease-out both; }
        .ov-anim-rise-in { animation: ov-rise-in 0.32s ease-out both; }
        .ov-anim-pulse { animation: ov-fade-pulse 1.6s ease-in-out infinite; }
      `}</style>
      <div
        className="ov-root fixed inset-0 overflow-hidden text-white select-none"
        style={
          {
            '--ov-accent': accent,
            fontFamily:
              'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif',
            textShadow: '0 2px 12px rgba(0,0,0,0.65)',
          } as CSSProperties
        }
      >
        {children}
      </div>
    </>
  );
}

const CONN_LABEL: Record<Exclude<ConnStatus, 'live'>, string> = {
  connecting: 'CONNECTING',
  reconnecting: 'RECONNECTING',
  offline: 'FEED OFFLINE',
};

/**
 * Subtle connection badge, bottom-right. Invisible while live so the
 * broadcast stays clean; shows only while the socket is down/retrying.
 *
 * When `onRetry` is provided and the socket is offline, the badge becomes a
 * clickable retry affordance (24/7 capture pages otherwise sit offline
 * forever after backoff exhaustion).
 */
export function ConnBadge({
  status,
  retryCount,
  onRetry,
}: {
  status: ConnStatus;
  retryCount: number;
  onRetry?: () => void;
}) {
  if (status === 'live') return null;
  const label = CONN_LABEL[status];
  const clickable = status === 'offline' && onRetry;
  return (
    <div
      className={`absolute bottom-3 right-4 z-50 flex items-center gap-2 rounded bg-black/60 px-3 py-1.5 font-mono text-[11px] tracking-[0.18em] ${
        clickable ? 'cursor-pointer' : ''
      }`}
      onClick={clickable ? onRetry : undefined}
      role={clickable ? 'button' : undefined}
      title={clickable ? 'Retry connection now' : undefined}
    >
      <span
        className={`inline-block h-2 w-2 rounded-full ${
          status === 'offline' ? 'bg-red-500' : 'bg-amber-400 ov-anim-pulse'
        }`}
      />
      <span className="text-white/80">
        {label}
        {status === 'reconnecting' && retryCount > 0 ? ` · TRY ${retryCount}` : ''}
        {clickable ? ' · RETRY' : ''}
      </span>
    </div>
  );
}

/**
 * Honest empty state: rendered until the WS has delivered real data.
 * No mock numbers anywhere — the overlay stays quiet until the feed speaks.
 */
export function WaitingFeed({ what = 'LIVE FEED' }: { what?: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center">
      <div className="flex items-center gap-3 rounded-lg bg-black/55 px-5 py-3 font-mono text-sm tracking-[0.25em] text-white/70">
        <span
          className="inline-block h-2.5 w-2.5 rounded-full bg-amber-400 ov-anim-pulse"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        WAITING FOR {what}
      </div>
    </div>
  );
}
