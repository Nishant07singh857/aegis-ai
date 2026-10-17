'use client';

/**
 * AEGIS AI — scorebug overlay (OBS browser source).
 *
 * Route: /overlays/scorebug
 *
 * Compact top-center bar: the two leading teams (tag, total points, kills),
 * teams-alive count, zone phase and match clock in the middle.
 *
 * Data: live WS (/ws/live) `standings` frames via useLiveSocket; zone phase /
 * match clock from GET /api/v1/analytics/summary (polled every 15s).
 * No mock data: renders "waiting for feed" until the socket delivers.
 *
 * Query params:
 *   ?match=<id>    Display label (default "LIVE"). The WS carries the live
 *                  match feed; the value is display-only / reserved for a
 *                  future per-match subscription.
 *   ?accent=<hex>  Accent colour, e.g. ?accent=%2300f0ff (default #00f0ff).
 *   ?api=<url>     Backend API base override (also re-points the WS).
 */

import { Suspense, useEffect, useState } from 'react';
import { useLiveSocket } from '@/lib/useLiveSocket';
import { useOverlayParams } from '@/lib/overlay-params';
import { OverlayRoot, ConnBadge, WaitingFeed } from '@/components/overlay-kit';

interface SummaryData {
  zone_phase?: number;
  teams_alive?: number;
  match_time_seconds?: number;
}

function fmtClock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function ScorebugInner() {
  const params = useOverlayParams();
  const { status, retryCount, standings } = useLiveSocket(params.api);
  const [summary, setSummary] = useState<SummaryData | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${params.api}/api/v1/analytics/summary`);
        if (!res.ok) return;
        const body = (await res.json()) as { data?: SummaryData };
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

  const sorted = [...standings].sort(
    (a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0)
  );
  const [first, second] = sorted;
  const teamsAlive =
    summary?.teams_alive ?? standings.filter((t) => t.alive).length;
  const teamsTotal = standings.length;
  const matchLabel =
    params.match === 'live' ? 'LIVE' : params.match.toUpperCase();

  const teamBlock = (team: (typeof sorted)[number] | undefined, align: 'left' | 'right') => {
    if (!team) return <div className="w-44" />;
    return (
      <div
        className={`flex w-44 items-center gap-3 px-4 py-2 ${
          align === 'right' ? 'flex-row-reverse text-right' : ''
        }`}
      >
        <span
          className="inline-block h-9 w-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: team.color ?? 'var(--ov-accent)' }}
        />
        <div className="min-w-0">
          <div className="truncate text-xl font-extrabold tracking-wide">
            {team.tag ?? team.name}
          </div>
          <div className="font-mono text-[11px] tracking-[0.2em] text-white/60">
            {team.kills ?? 0} KILLS
          </div>
        </div>
        <div
          className={`text-4xl font-black tabular-nums ${
            align === 'right' ? 'mr-auto' : 'ml-auto'
          }`}
        >
          {team.total_pts ?? 0}
        </div>
      </div>
    );
  };

  return (
    <OverlayRoot accent={params.accent}>
      {sorted.length === 0 ? (
        <WaitingFeed />
      ) : (
        <div className="absolute left-1/2 top-4 -translate-x-1/2">
          <div className="overflow-hidden rounded-xl bg-black/70 backdrop-blur-sm">
            <div
              className="h-1 w-full"
              style={{ backgroundColor: 'var(--ov-accent)' }}
            />
            <div className="flex items-stretch divide-x divide-white/10">
              {teamBlock(first, 'left')}
              <div className="flex min-w-40 flex-col items-center justify-center gap-0.5 px-5 py-2">
                <div className="flex items-center gap-2 font-mono text-[11px] tracking-[0.25em] text-white/70">
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${
                      status === 'live' ? 'bg-emerald-400' : 'bg-amber-400 ov-anim-pulse'
                    }`}
                  />
                  {matchLabel}
                </div>
                <div className="font-mono text-sm font-bold tracking-[0.2em]">
                  {summary?.zone_phase != null
                    ? `ZONE ${summary.zone_phase}`
                    : 'ZONE —'}
                  {summary?.match_time_seconds != null && (
                    <span className="text-white/60">
                      {' '}
                      · {fmtClock(summary.match_time_seconds)}
                    </span>
                  )}
                </div>
                <div className="font-mono text-[11px] tracking-[0.2em] text-white/60">
                  {teamsTotal > 0 ? `${teamsAlive}/${teamsTotal} ALIVE` : '—'}
                </div>
              </div>
              {teamBlock(second, 'right')}
            </div>
          </div>
        </div>
      )}
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
