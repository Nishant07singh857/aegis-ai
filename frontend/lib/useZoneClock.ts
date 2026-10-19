'use client';

/**
 * AEGIS AI — live zone countdown hook (Phase 18, W3).
 *
 * The backend zone service owns the clock: it broadcasts a "zone.tick"
 * snapshot (status, current phase, time_remaining_seconds) on WS connect
 * and on every zone change (phases set / advance / reset). This hook
 * derives the ticking countdown client-side from the most recent frame —
 * the arithmetic is honest (remaining = reported remaining − elapsed
 * since the frame arrived), clamped at 0, and it re-renders every second
 * while a phase is active.
 *
 * Returns null fields (never invented numbers) when there is no zone
 * data: the zone service starts in "no_data" until an operator configures
 * phases, and the map cam renders that honestly.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ZoneStateFrame } from './useLiveSocket';

export interface ZoneClock {
  /** "no_data" | "active" | "complete" — "" when no frame has arrived. */
  status: string;
  /** Current phase name, e.g. "Zone 3" — null when unknown. */
  phaseName: string | null;
  phaseNumber: number | null;
  totalPhases: number | null;
  /** Seconds left in the phase, ticking live; null when not active. */
  remainingSeconds: number | null;
  /** "MM:SS" for the timer pill; null when not active. */
  remainingLabel: string | null;
}

function formatMMSS(totalSeconds: number): string {
  const s = Math.max(0, Math.ceil(totalSeconds));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

export function useZoneClock(zone: ZoneStateFrame | null): ZoneClock {
  const [tick, setTick] = useState(0);
  // Monotonic anchor: when the latest frame arrived (local clock) and the
  // remaining seconds it reported. Reset whenever a new frame lands.
  const anchorRef = useRef<{ at: number; remaining: number } | null>(null);
  const lastFrameRef = useRef<ZoneStateFrame | null>(null);

  if (zone !== lastFrameRef.current) {
    lastFrameRef.current = zone;
    anchorRef.current =
      zone &&
      zone.status === 'active' &&
      typeof zone.time_remaining_seconds === 'number'
        ? { at: Date.now(), remaining: zone.time_remaining_seconds }
        : null;
  }

  useEffect(() => {
    // Tick only while a phase is plausibly active; the interval is cheap
    // and self-cleans.
    if (zone?.status !== 'active') return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [zone?.status]);

  return useMemo(() => {
    const status = zone?.status ?? '';
    const phase = zone?.current_phase ?? null;
    let remainingSeconds: number | null = null;
    if (status === 'active' && anchorRef.current) {
      const elapsed = (Date.now() - anchorRef.current.at) / 1000;
      remainingSeconds = Math.max(0, anchorRef.current.remaining - elapsed);
    }
    return {
      status,
      phaseName: phase?.name ?? null,
      phaseNumber:
        typeof phase?.phase_number === 'number' ? phase.phase_number : null,
      totalPhases:
        typeof phase?.total_phases === 'number' ? phase.total_phases : null,
      remainingSeconds,
      remainingLabel:
        remainingSeconds === null ? null : formatMMSS(remainingSeconds),
    };
    // `tick` forces the 1s re-render; `zone` supplies the data.
  }, [zone, tick]);
}
