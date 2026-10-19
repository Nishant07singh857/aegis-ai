'use client';

/**
 * AEGIS AI — /broadcast/ceremony (Phase 10).
 *
 * Standalone preview page for the WWCD ceremony graphic. Reads
 * GET /api/v1/ceremony/state on mount and follows `ceremony.step` WS
 * frames; the WS frame is the freshest source and wins.
 *
 * Operators get a control panel: pick the winning team from the REAL
 * teams roster (GET /api/v1/teams), optionally attach a stats payload and
 * an interview payload (verbatim, never invented — omitting them shows
 * honest "pending" cards), then advance through the ceremony steps.
 * Viewers see the ceremony only.
 */

import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useRole } from '@/lib/useRole';
import WwcdCeremony, {
  type CeremonySnapshot,
} from '@/components/graphics-staging/wwcd-ceremony';
import { useBroadcastSocket } from '@/components/graphics-staging/use-broadcast-socket';

interface TeamListResponse {
  data: Array<{ id: string; name: string; tag?: string | null }>;
}

const panelStyle: CSSProperties = {
  position: 'fixed',
  right: 16,
  bottom: 16,
  zIndex: 50,
  width: 340,
  maxHeight: '86vh',
  overflowY: 'auto',
  background: 'rgba(10,14,26,0.94)',
  border: '1px solid #6b5320',
  borderRadius: 12,
  padding: '1rem',
  color: '#f7f3e8',
  fontFamily: "'Rajdhani','Segoe UI',system-ui,sans-serif",
  backdropFilter: 'blur(6px)',
};

const btnStyle = (danger = false): CSSProperties => ({
  padding: '0.55rem 0.6rem',
  borderRadius: 8,
  border: danger ? '1px solid #5a2b2b' : '1px solid #6b5320',
  background: danger ? 'rgba(90,43,43,0.4)' : '#2b2113',
  color: danger ? '#ff9d9d' : '#ffd166',
  cursor: 'pointer',
  fontWeight: 700,
  fontSize: '0.8rem',
  letterSpacing: '0.06em',
});

const inputStyle: CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  background: '#0d1526',
  border: '1px solid #22314f',
  borderRadius: 8,
  color: '#f2f5fb',
  padding: '0.5rem',
  fontSize: '0.85rem',
  marginBottom: '0.6rem',
};

function OperatorPanel({ onChanged }: { onChanged: () => void }) {
  const { token } = useAuth();
  const [teams, setTeams] = useState<TeamListResponse['data']>([]);
  const [winnerTeamId, setWinnerTeamId] = useState('');
  const [statsJson, setStatsJson] = useState('');
  const [interviewJson, setInterviewJson] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiFetch<TeamListResponse>('/api/v1/teams', { token })
      .then((res) => setTeams(res.data ?? []))
      .catch(() => setTeams([]));
  }, [token]);

  const call = async (path: string, body?: unknown) => {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/v1${path}`, { method: 'POST', body: body ?? {}, token });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setBusy(false);
    }
  };

  const parseOptional = (raw: string, label: string): Record<string, unknown> | undefined => {
    const trimmed = raw.trim();
    if (!trimmed) return undefined;
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('must be a JSON object');
      }
      return parsed as Record<string, unknown>;
    } catch (e) {
      throw new Error(`${label}: ${e instanceof Error ? e.message : 'invalid JSON'}`);
    }
  };

  const start = () => {
    if (!winnerTeamId) {
      setError('Pick the winning team first.');
      return;
    }
    try {
      const stats = parseOptional(statsJson, 'Stats');
      const interview = parseOptional(interviewJson, 'Interview');
      call('/ceremony/start', {
        winner_team_id: winnerTeamId,
        ...(stats ? { stats } : {}),
        ...(interview ? { interview } : {}),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Invalid payload');
    }
  };

  return (
    <div style={panelStyle}>
      <div style={{ fontWeight: 700, letterSpacing: '0.1em', marginBottom: '0.8rem', fontSize: '0.85rem', color: '#c9a94e' }}>
        WWCD CEREMONY CONTROL · OPERATOR
      </div>
      <label style={{ display: 'block', fontSize: '0.8rem', color: '#a89568', marginBottom: '0.3rem' }}>
        Winning team (from the real roster)
      </label>
      <select value={winnerTeamId} onChange={(e) => setWinnerTeamId(e.target.value)} style={inputStyle}>
        <option value="">— select team —</option>
        {teams.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}{t.tag ? ` [${t.tag}]` : ''}
          </option>
        ))}
      </select>
      <label style={{ display: 'block', fontSize: '0.8rem', color: '#a89568', marginBottom: '0.3rem' }}>
        Stats JSON (optional — omit for honest “pending”)
      </label>
      <textarea
        value={statsJson}
        onChange={(e) => setStatsJson(e.target.value)}
        placeholder='{"placement_points": 10, "finishes": 14, "damage": 2310}'
        rows={3}
        style={{ ...inputStyle, fontFamily: 'monospace' }}
      />
      <label style={{ display: 'block', fontSize: '0.8rem', color: '#a89568', marginBottom: '0.3rem' }}>
        Interview JSON (optional)
      </label>
      <textarea
        value={interviewJson}
        onChange={(e) => setInterviewJson(e.target.value)}
        placeholder='{"player": "PLAYER_NAME", "quote": "We just played our game."}'
        rows={3}
        style={{ ...inputStyle, fontFamily: 'monospace' }}
      />
      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
        <button style={{ ...btnStyle(), flex: 1 }} disabled={busy} onClick={start}>
          START
        </button>
        <button style={{ ...btnStyle(), flex: 1 }} disabled={busy} onClick={() => call('/ceremony/advance')}>
          ADVANCE ▸
        </button>
        <button style={{ ...btnStyle(true), flex: 1 }} disabled={busy} onClick={() => call('/ceremony/reset')}>
          RESET
        </button>
      </div>
      {error && <div style={{ marginTop: '0.6rem', color: '#ff9d9d', fontSize: '0.8rem' }}>{error}</div>}
      <div style={{ marginTop: '0.6rem', color: '#a89568', fontSize: '0.75rem' }}>
        Steps: winner_announce → stats_rundown → interview → standings → done.
      </div>
    </div>
  );
}

export default function CeremonyPage() {
  const { token } = useAuth();
  const { isOperator } = useRole();
  const [state, setState] = useState<CeremonySnapshot | null>(null);
  const { latest } = useBroadcastSocket(['ceremony.step']);
  const frame = latest['ceremony.step'] as CeremonySnapshot | undefined;

  const fetchState = useCallback(async () => {
    try {
      const s = await apiFetch<CeremonySnapshot>('/api/v1/ceremony/state', { token });
      setState(s);
    } catch {
      /* backend unreachable — WS may still deliver */
    }
  }, [token]);

  useEffect(() => {
    fetchState();
  }, [fetchState]);

  useEffect(() => {
    if (frame) setState(frame);
  }, [frame]);

  return (
    <div style={{ background: '#070503', minHeight: '100vh' }}>
      <WwcdCeremony state={state} />
      {isOperator && <OperatorPanel onChanged={fetchState} />}
    </div>
  );
}
