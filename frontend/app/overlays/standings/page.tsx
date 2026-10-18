'use client';

/**
 * AEGIS AI — standings overlay (OBS browser source).
 *
 * Route: /overlays/standings
 *
 * Renders the active standings graphics template (components/graphics)
 * with the live standings from the WS.
 * Zero mock data: renders "waiting for feed" until the socket delivers.
 *
 * Query params:
 *   ?match=<id>    Accepted for consistency; the WS carries the live feed
 *                  (display-only, see scorebug header for details).
 *   ?accent=<hex>  Accent colour, e.g. ?accent=%2300f0ff (default #00f0ff).
 *   ?api=<url>     Backend API base override (also re-points the WS).
 *   ?rows=<n>      Rows to show (default 10, min 3, max 20).
 *   ?template=<id> Pin one standings template for this source, e.g.
 *                  ?template=standings-cards. Unknown ids fall back to the
 *                  type default. Precedence: ?template= > the director
 *                  state's active template > the default (standings-table).
 */

import { Suspense, useMemo } from 'react';
import { useSearchParams } from 'next/navigation';
import { useLiveSocket } from '@/lib/useLiveSocket';
import { useOverlayParams } from '@/lib/overlay-params';
import { usePlayerStats } from '@/lib/usePlayerStats';
import { resolveTemplate } from '@/lib/graphics';
import { OverlayRoot, ConnBadge } from '@/components/overlay-kit';

function clampRows(raw: string | null): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 10;
  return Math.min(20, Math.max(3, Math.round(n)));
}

function StandingsInner() {
  const params = useOverlayParams();
  const sp = useSearchParams();
  const rowCount = useMemo(() => clampRows(sp.get('rows')), [sp]);
  const { status, retryCount, standings, killFeed, directorState } =
    useLiveSocket(params.api);
  // Per-player stats for the MVP/IGL/Finals-MVP boards; [] until a
  // game-stats feed records them (those templates render honestly then).
  const players = usePlayerStats(params.api);

  const rows = useMemo(
    () =>
      [...standings]
        .sort((a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0))
        .slice(0, rowCount),
    [standings, rowCount]
  );

  const def = resolveTemplate('standings', sp.get('template'), directorState);
  const Template = def.component;

  return (
    <OverlayRoot accent={params.accent}>
      <Template
        standings={rows}
        accent={params.accent}
        status={status}
        killFeed={killFeed}
        players={players}
      />
      <ConnBadge status={status} retryCount={retryCount} />
    </OverlayRoot>
  );
}

export default function StandingsPage() {
  return (
    <Suspense fallback={null}>
      <StandingsInner />
    </Suspense>
  );
}
