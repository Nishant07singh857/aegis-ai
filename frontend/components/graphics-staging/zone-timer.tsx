'use client';

/**
 * AEGIS AI — zone timer graphic (Phase 10, staging).
 *
 * Self-contained: fetches GET /api/v1/zone/state on mount (auth when
 * available) and subscribes to `zone.tick` WS frames via the staging
 * useBroadcastSocket hook. Counts down locally between operator advances —
 * the backend re-emits `zone.tick` on every advance/configure/reset, and the
 * component re-syncs from the fresh snapshot (fresh server data always wins
 * over the local tick).
 *
 * Honest states:
 *  - no_data:   "WAITING FOR ZONE DATA" — no countdown is invented.
 *  - complete:  "ALL ZONES COMPLETE".
 *  - active:    phase name + live countdown (clamped at 00:00 when the phase
 *               overruns; the operator advances it).
 *
 * INTEGRATION POINT (Phase 9): after Phase 9 lands, this component (or just
 * its countdown core) can be embedded in the output page's HUD; the WS
 * contract it consumes (`zone.tick` frames on /ws/live) is stable.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useBroadcastSocket } from './use-broadcast-socket';

interface ZoneCurrentPhase {
  name: string;
  duration_seconds: number;
  phase_number: number;
  total_phases: number;
}

interface ZoneSnapshot {
  status: 'no_data' | 'active' | 'complete';
  current_phase: ZoneCurrentPhase | null;
  time_remaining_seconds: number | null;
  updated_at: string;
}

const BOX: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.8rem',
  background: 'rgba(10,16,30,0.88)',
  border: '1px solid #22314f',
  borderRadius: 12,
  padding: '0.7rem 1.2rem',
  color: '#f2f5fb',
  fontFamily: "'Rajdhani','Segoe UI',system-ui,sans-serif",
  backdropFilter: 'blur(6px)',
};

function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

export default function ZoneTimer() {
  const { token } = useAuth();
  const [snapshot, setSnapshot] = useState<ZoneSnapshot | null>(null);
  const [tick, setTick] = useState(0);
  const anchor = useRef<{ remaining: number; at: number } | null>(null);
  const { latest, reconnect } = useBroadcastSocket(['zone.tick']);
  const frame = latest['zone.tick'] as ZoneSnapshot | undefined;

  const fetchState = useCallback(async () => {
    try {
      const state = await apiFetch<ZoneSnapshot>('/api/v1/zone/state', { token });
      setSnapshot(state);
    } catch {
      /* backend unreachable — the WS may still deliver; show loading */
    }
  }, [token]);

  useEffect(() => {
    fetchState();
  }, [fetchState]);

  // WS frame is the freshest source and wins over REST.
  useEffect(() => {
    if (frame) setSnapshot(frame);
  }, [frame]);

  // Local countdown: re-anchor whenever a fresh snapshot arrives.
  useEffect(() => {
    if (
      snapshot?.status === 'active' &&
      snapshot.time_remaining_seconds !== null &&
      snapshot.time_remaining_seconds !== undefined
    ) {
      anchor.current = { remaining: snapshot.time_remaining_seconds, at: Date.now() };
    } else {
      anchor.current = null;
    }
  }, [snapshot]);

  useEffect(() => {
    if (snapshot?.status !== 'active') return;
    const t = setInterval(() => setTick((n) => n + 1), 500);
    return () => clearInterval(t);
  }, [snapshot?.status]);

  void tick;
  void reconnect;

  if (!snapshot) {
    return (
      <div style={BOX}>
        <span style={{ color: '#8fa3c8', letterSpacing: '0.2em', fontSize: '0.8rem' }}>
          ZONE — LOADING…
        </span>
      </div>
    );
  }

  if (snapshot.status === 'no_data') {
    return (
      <div style={BOX}>
        <span style={{ color: '#8fa3c8', letterSpacing: '0.2em', fontSize: '0.8rem' }}>
          ZONE — WAITING FOR ZONE DATA
        </span>
      </div>
    );
  }

  if (snapshot.status === 'complete' || !snapshot.current_phase) {
    return (
      <div style={BOX}>
        <span style={{ color: '#7ee2a8', letterSpacing: '0.2em', fontSize: '0.8rem', fontWeight: 700 }}>
          ALL ZONES COMPLETE
        </span>
      </div>
    );
  }

  const remaining = anchor.current
    ? Math.max(0, anchor.current.remaining - (Date.now() - anchor.current.at) / 1000)
    : (snapshot.time_remaining_seconds ?? 0);
  const overrun = remaining <= 0;

  return (
    <div style={BOX} title={overrun ? 'Phase overran — waiting for the operator to advance' : undefined}>
      <span style={{ color: '#8fa3c8', letterSpacing: '0.2em', fontSize: '0.8rem' }}>ZONE</span>
      <span style={{ fontWeight: 700, fontSize: '1.05rem', letterSpacing: '0.04em' }}>
        {snapshot.current_phase.name}
      </span>
      <span
        style={{
          fontVariantNumeric: 'tabular-nums',
          fontWeight: 800,
          fontSize: '1.4rem',
          color: overrun ? '#ff9d9d' : remaining <= 15 ? '#ffd166' : '#f2f5fb',
        }}
      >
        {formatClock(remaining)}
      </span>
      <span style={{ color: '#5c6f92', fontSize: '0.75rem' }}>
        {snapshot.current_phase.phase_number}/{snapshot.current_phase.total_phases}
      </span>
    </div>
  );
}
