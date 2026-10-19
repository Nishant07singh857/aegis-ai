'use client';

/**
 * AEGIS AI — broadcast DIRECTOR output page.
 *
 * Route: /broadcast/output
 *
 * A full 1920×1080 layout designed to be screen-captured by a headless
 * Chromium (never clicked): scorebug, kill feed, standings/commentary
 * scenes and lower thirds, all rendered in one frame. Rendering lives in
 * components/DirectorOutputView (shared with /watch) and follows the
 * director state service: AUTO mode keeps the AI-driven behaviour, MANUAL
 * mode renders the operator's scene + overlays + lower-third exactly as
 * directed. Zero mock data: WaitingFeed covers the frame until the socket
 * goes live, and each section shows an honest standby line when its feed
 * is quiet.
 *
 * Query params:
 *   ?match=<id>    Display label only (default "LIVE"); the WS carries the
 *                  single live match feed, see scorebug header for details.
 *   ?accent=<hex>  Accent colour, e.g. ?accent=%2300f0ff (default #00f0ff).
 *   ?api=<url>     Backend API base override (also re-points the WS).
 *   ?bg=<mode>     "transparent" (default — OBS/chroma friendly) or "brand"
 *                  (opaque branded backdrop when the capture needs a plate).
 *   ?video=<mode>  "real" (default — live game feed) or "sim" (watermarked
 *                  simulator test pattern; honest, never real footage).
 *   ?ttl=<secs>    Kill ticker visibility in seconds (default 5, 3–60;
 *                  Bible-measured: Krafton killfeed entries fade after ~5s).
 *   ?hold=<secs>   Lower-third visibility in seconds (default 6, 2–30).
 */

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { useOverlayParams } from '@/lib/overlay-params';
import { DirectorOutputView } from '@/components/DirectorOutputView';

const KILL_TTL_DEFAULT = 5;
const HOLD_DEFAULT = 6;

function clampNum(
  raw: string | null,
  fallback: number,
  min: number,
  max: number
): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.round(n)));
}

function BroadcastOutputInner() {
  const params = useOverlayParams();
  const sp = useSearchParams();
  const brand = sp.get('bg') === 'brand';
  const ttl = clampNum(sp.get('ttl'), KILL_TTL_DEFAULT, 3, 60);
  const hold = clampNum(sp.get('hold'), HOLD_DEFAULT, 2, 30);
  const matchLabel =
    params.match === 'live' ? 'LIVE' : params.match.toUpperCase();
  // Phase 17 W2: ?video=sim streams the watermarked simulator test
  // pattern instead of the real game feed.
  const video = sp.get('video') === 'sim' ? 'simulator' : 'real';

  return (
    <DirectorOutputView
      api={params.api}
      accent={params.accent}
      match={matchLabel}
      ttl={ttl}
      hold={hold}
      brand={brand}
      video={video}
    />
  );
}

export default function BroadcastOutputPage() {
  return (
    <Suspense fallback={null}>
      <BroadcastOutputInner />
    </Suspense>
  );
}
