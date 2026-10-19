'use client';

/**
 * AEGIS AI — broadcast PiP output page (Phase 10).
 *
 * Route: /broadcast/output-pip
 *
 * A standalone, capture-safe broadcast frame (designed to be
 * screen-captured by a headless Chromium, never clicked): the PiP layout
 * (components/graphics-staging/pip-layout.tsx) with the schematic map cam
 * in the inset and the main director output slot.
 *
 * Visual Broadcast Bible §4: Krafton never uses PiP for simultaneous
 * fights, so this page is map+gameplay only — the backend PiP service
 * source keyspace contains no fight feed, which keeps fight+fight PiP
 * unreachable by construction. No camera transitions here either (§3.2):
 * the PiP slots swap instantly when the backend state changes.
 *
 * Layout state comes from the backend PiP service:
 *   GET  /api/v1/pip/state        (initial snapshot; needs a token)
 *   WS   /ws/live  "pip.state"    (live updates — passive WS subscription
 *                                  works without a token)
 * Operator control lives in the PiP service API (operator+); this page is
 * read-only output.
 *
 * Honest empty states: the main feed slot reads "connect output source"
 * until a real main view is wired here — no fake video feed is rendered.
 * The map cam shows its own "waiting for position data" standby because
 * no live observer position feed exists yet.
 *
 * Query params:
 *   ?match=<id>   Display label only (default "LIVE").
 *   ?accent=<hex> Accent colour, e.g. ?accent=%2300f0ff (default #00f0ff).
 *   ?api=<url>    Backend API base override (also re-points the WS).
 */

import { Suspense, useEffect, useState } from 'react';
import { useOverlayParams } from '@/lib/overlay-params';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useLiveSocket, type PiPStateFrame } from '@/lib/useLiveSocket';
import { PiPLayout, type InsetPosition } from '@/components/graphics-staging/pip-layout';
import { MapCam } from '@/components/graphics-staging/map-cam';

const DEFAULT_PIP: PiPStateFrame = {
  enabled: false,
  main_source: 'output',
  inset_source: 'map-cam',
  inset_position: 'bottom_right',
  updated_at: '',
};

function SourceView({ source, label }: { source: string; label: string }) {
  if (source === 'map-cam') {
    // No live position feed exists yet — MapCam renders its honest standby.
    return <MapCam map="erangel" positions={[]} className="h-full w-full rounded-lg" />;
  }
  if (source === 'output') {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-[#0a0a0f] text-center">
        <p className="text-lg font-semibold text-white/70">
          Connect output source
        </p>
        <p className="max-w-md px-6 text-xs text-white/40">
          The main feed slot is not wired to a source yet. Set the PiP
          state&apos;s main_source to &quot;map-cam&quot; to show the map cam
          full-screen, or wire the director output view here.
        </p>
      </div>
    );
  }
  return (
    <div className="flex h-full w-full items-center justify-center bg-[#0a0a0f]">
      <p className="px-6 text-center text-xs text-white/40">
        Source &quot;{source}&quot; has no view on this page yet ({label}).
      </p>
    </div>
  );
}

function PipOutputInner() {
  const { match, api } = useOverlayParams();
  const { token } = useAuth();
  const { pipState: wsPipState, status } = useLiveSocket(api);

  const [pip, setPip] = useState<PiPStateFrame>(DEFAULT_PIP);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Initial snapshot — only when we have a token (GET /pip/state is
  // authenticated). WS frames keep it live either way.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    apiFetch<PiPStateFrame>('/api/v1/pip/state', { token })
      .then((state) => {
        if (!cancelled && state) setPip(state);
      })
      .catch((e) => {
        if (!cancelled) {
          setLoadError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Live updates from the socket win over the snapshot.
  useEffect(() => {
    if (wsPipState) setPip(wsPipState);
  }, [wsPipState]);

  const insetPosition = (
    ['top_left', 'top_right', 'bottom_left', 'bottom_right'] as const
  ).includes(pip.inset_position as InsetPosition)
    ? (pip.inset_position as InsetPosition)
    : 'bottom_right';

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-black">
      <PiPLayout
        enabled={pip.enabled}
        insetPosition={insetPosition}
        insetLabel={pip.inset_source}
        mainSlot={<SourceView source={pip.main_source} label="main" />}
        insetSlot={<SourceView source={pip.inset_source} label="inset" />}
      />
      {/* capture-safe status strip: state only, no controls */}
      <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded bg-black/60 px-2 py-1 text-[10px] text-white/60">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            status === 'live' ? 'bg-emerald-400' : 'bg-amber-400'
          }`}
        />
        <span>
          PiP {pip.enabled ? 'ON' : 'OFF'} · {match}
        </span>
        {loadError && <span className="text-amber-300/80">· snapshot unavailable</span>}
      </div>
    </div>
  );
}

export default function OutputPipPage() {
  return (
    <Suspense>
      <PipOutputInner />
    </Suspense>
  );
}
