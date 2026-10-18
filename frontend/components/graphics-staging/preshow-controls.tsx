'use client';

/**
 * AEGIS AI — pre-show operator controls (Phase 10, staging).
 *
 * Operator-only panel for the /broadcast/preshow page: step buttons,
 * countdown-target picker, and reset. All writes hit the real API
 * (POST /api/v1/preshow/*) with the operator's token; errors surface inline.
 * This panel is hidden from viewers by the page (role-gated).
 */

import { useState, type CSSProperties } from 'react';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import type { PreshowSnapshot } from './preshow-view';

const STEPS = ['countdown', 'desk_intro', 'match_intro', 'starting'] as const;

const panelStyle: CSSProperties = {
  position: 'fixed',
  right: 16,
  bottom: 16,
  zIndex: 50,
  width: 320,
  background: 'rgba(10,14,26,0.92)',
  border: '1px solid #22314f',
  borderRadius: 12,
  padding: '1rem',
  color: '#f2f5fb',
  fontFamily: "'Rajdhani','Segoe UI',system-ui,sans-serif",
  backdropFilter: 'blur(6px)',
};

const btnStyle = (active: boolean): CSSProperties => ({
  flex: '1 1 45%',
  padding: '0.5rem 0.4rem',
  borderRadius: 8,
  border: active ? '1px solid #ffd166' : '1px solid #22314f',
  background: active ? 'rgba(255,209,102,0.15)' : '#14213d',
  color: active ? '#ffd166' : '#dfe7f5',
  cursor: 'pointer',
  fontWeight: 600,
  fontSize: '0.8rem',
  letterSpacing: '0.04em',
});

export default function PreshowControls({ onChanged }: { onChanged: () => void }) {
  const { token } = useAuth();
  const [target, setTarget] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const call = async (path: string, body?: unknown) => {
    setBusy(true);
    setError(null);
    try {
      await apiFetch<PreshowSnapshot>(`/api/v1${path}`, {
        method: 'POST',
        body: body ?? {},
        token,
      });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setBusy(false);
    }
  };

  const setCountdown = () => {
    if (!target) {
      setError('Pick a date and time first.');
      return;
    }
    const iso = new Date(target).toISOString();
    call('/preshow/countdown', { target_iso: iso });
  };

  return (
    <div style={panelStyle}>
      <div style={{ fontWeight: 700, letterSpacing: '0.1em', marginBottom: '0.6rem', fontSize: '0.85rem', color: '#8fa3c8' }}>
        PRE-SHOW CONTROL · OPERATOR
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.8rem' }}>
        {STEPS.map((s) => (
          <button key={s} style={btnStyle(false)} disabled={busy} onClick={() => call('/preshow/step', { step: s })}>
            {s.replace('_', ' ').toUpperCase()}
          </button>
        ))}
      </div>
      <label style={{ display: 'block', fontSize: '0.8rem', color: '#8fa3c8', marginBottom: '0.3rem' }}>
        Countdown target
      </label>
      <div style={{ display: 'flex', gap: '0.4rem' }}>
        <input
          type="datetime-local"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          style={{
            flex: 1,
            background: '#0d1526',
            border: '1px solid #22314f',
            borderRadius: 8,
            color: '#f2f5fb',
            padding: '0.5rem',
            fontSize: '0.85rem',
          }}
        />
        <button style={btnStyle(false)} disabled={busy} onClick={setCountdown}>
          SET
        </button>
      </div>
      <button
        style={{ ...btnStyle(false), width: '100%', marginTop: '0.8rem', borderColor: '#5a2b2b', color: '#ff9d9d' }}
        disabled={busy}
        onClick={() => call('/preshow/reset')}
      >
        RESET PRE-SHOW
      </button>
      {error && (
        <div style={{ marginTop: '0.6rem', color: '#ff9d9d', fontSize: '0.8rem' }}>{error}</div>
      )}
    </div>
  );
}
