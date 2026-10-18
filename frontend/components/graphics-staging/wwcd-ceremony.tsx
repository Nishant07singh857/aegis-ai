'use client';

/**
 * AEGIS AI — WWCD ceremony graphic (Phase 10, staging).
 *
 * Renders the current ceremony step for the match winner:
 *  - winner_announce: "WINNER WINNER CHICKEN DINNER" gold graphic + team name
 *  - stats_rundown:   stat rows from the operator-supplied stats payload
 *                     (honest "stats pending" card when absent — never
 *                     invented)
 *  - interview:       winner interview lower-third (honest "pending" card
 *                     when absent)
 *  - standings:       prompt to cut to the standings overlay
 *  - idle / done:     honest standby cards
 *
 * Pure presentational component: it takes a `state` snapshot as a prop and
 * never fetches or mutates anything, so it can be embedded anywhere the
 * broadcast output needs it.
 *
 * INTEGRATION POINT (Phase 9): after Phase 9 lands, embed in the output page
 * (the /broadcast/output renderer) like this:
 *
 *   import WwcdCeremony from '@/components/graphics-staging/wwcd-ceremony';
 *   // ...inside the ceremony scene, fed by `ceremony.step` WS frames:
 *   <WwcdCeremony state={ceremonyState} />
 *
 * The `ceremony.step` frames are already emitted by the backend ceremony
 * service on /ws/live (see backend/app/services/ceremony/).
 */

import type { CSSProperties } from 'react';

export interface CeremonySnapshot {
  step: 'idle' | 'winner_announce' | 'stats_rundown' | 'interview' | 'standings' | 'done';
  winner: { team_id: string | null; team_name: string | null };
  stats: Record<string, unknown> | null;
  interview: Record<string, unknown> | null;
  updated_at: string;
}

const SHELL: CSSProperties = {
  width: '100%',
  minHeight: '100vh',
  background: 'radial-gradient(1200px 700px at 50% 15%, #2b2113 0%, #141007 55%, #070503 100%)',
  color: '#f7f3e8',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  fontFamily: "'Rajdhani','Segoe UI',system-ui,sans-serif",
  textAlign: 'center',
  padding: '2rem',
  boxSizing: 'border-box',
};

const KICKER: CSSProperties = {
  letterSpacing: '0.45em',
  fontSize: 'clamp(0.7rem, 1.4vw, 1rem)',
  color: '#c9a94e',
  textTransform: 'uppercase',
  marginBottom: '1.2rem',
};

const GOLD_TITLE: CSSProperties = {
  fontSize: 'clamp(2.2rem, 7vw, 5.2rem)',
  fontWeight: 800,
  margin: 0,
  letterSpacing: '0.06em',
  background: 'linear-gradient(180deg, #ffe9a8 0%, #ffd166 45%, #b8860b 100%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text',
  color: 'transparent',
  textShadow: '0 0 60px rgba(255,209,102,0.2)',
};

function PendingCard({ what }: { what: string }) {
  return (
    <div style={SHELL}>
      <div style={KICKER}>WWCD Ceremony</div>
      <h2 style={{ fontSize: 'clamp(1.4rem, 3.5vw, 2.4rem)', margin: 0 }}>{what}</h2>
      <p style={{ color: '#a89568', marginTop: '1rem', maxWidth: 520 }}>
        Awaiting data from the operator — nothing is shown until the real
        numbers arrive.
      </p>
    </div>
  );
}

/** Human-friendly labels for common stat keys; unknown keys are title-cased. */
function statLabel(key: string): string {
  const map: Record<string, string> = {
    placement_points: 'Placement points',
    placement_pts: 'Placement points',
    finishes: 'Finishes',
    kills: 'Finishes',
    damage: 'Damage',
    survival_time: 'Survival time',
    knocks: 'Knocks',
    chicken: 'Chicken dinners',
    wwcd: 'Chicken dinners',
    team_name: 'Team',
  };
  if (map[key]) return map[key];
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function WinnerAnnounce({ teamName }: { teamName: string | null }) {
  return (
    <div style={SHELL}>
      <div style={KICKER}>Match over</div>
      <h1 style={GOLD_TITLE}>WINNER WINNER</h1>
      <h1 style={{ ...GOLD_TITLE, marginTop: '-0.3em' }}>CHICKEN DINNER</h1>
      <div
        style={{
          marginTop: '1.6rem',
          fontSize: 'clamp(1.6rem, 4.5vw, 3rem)',
          fontWeight: 700,
          letterSpacing: '0.08em',
        }}
      >
        {teamName ?? 'WINNER TO BE ANNOUNCED'}
      </div>
      {!teamName && (
        <p style={{ color: '#a89568', marginTop: '0.8rem' }}>
          The operator has not confirmed the winning team yet.
        </p>
      )}
    </div>
  );
}

function StatsRundown({ teamName, stats }: { teamName: string | null; stats: Record<string, unknown> | null }) {
  if (!stats || Object.keys(stats).length === 0) {
    return <PendingCard what={`${teamName ?? 'Winner'} — match stats pending`} />;
  }
  const rows = Object.entries(stats).filter(([k]) => k !== 'team_name' && k !== 'team_id');
  return (
    <div style={SHELL}>
      <div style={KICKER}>Match stats</div>
      <h2 style={{ fontSize: 'clamp(1.8rem, 4.5vw, 3rem)', margin: '0 0 1.6rem', letterSpacing: '0.08em' }}>
        {teamName ?? 'WINNER'}
      </h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: '0.8rem', width: 'min(860px, 92vw)' }}>
        {rows.map(([key, value]) => (
          <div
            key={key}
            style={{
              background: 'rgba(43,33,19,0.6)',
              border: '1px solid #6b5320',
              borderRadius: 12,
              padding: '1rem 0.8rem',
            }}
          >
            <div style={{ fontSize: 'clamp(1.6rem, 3.5vw, 2.4rem)', fontWeight: 800, color: '#ffd166' }}>
              {String(value)}
            </div>
            <div style={{ color: '#c9a94e', letterSpacing: '0.12em', fontSize: '0.8rem', textTransform: 'uppercase', marginTop: '0.3rem' }}>
              {statLabel(key)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function InterviewView({ teamName, interview }: { teamName: string | null; interview: Record<string, unknown> | null }) {
  if (!interview || Object.keys(interview).length === 0) {
    return <PendingCard what={`${teamName ?? 'Winner'} — winner interview pending`} />;
  }
  const player = typeof interview.player === 'string' ? interview.player : null;
  const quote = typeof interview.quote === 'string' ? interview.quote : null;
  const extra = Object.entries(interview).filter(([k]) => k !== 'player' && k !== 'quote');
  return (
    <div style={{ ...SHELL, justifyContent: 'flex-end', paddingBottom: '12vh' }}>
      <div
        style={{
          background: 'linear-gradient(90deg, rgba(20,14,6,0.95), rgba(43,33,19,0.9))',
          borderLeft: '6px solid #ffd166',
          borderRadius: '0 14px 14px 0',
          padding: '1.4rem 2.2rem',
          textAlign: 'left',
          maxWidth: 'min(760px, 90vw)',
        }}
      >
        <div style={{ ...KICKER, marginBottom: '0.4rem' }}>Winner interview</div>
        <div style={{ fontSize: 'clamp(1.4rem, 3.5vw, 2.2rem)', fontWeight: 700 }}>
          {player ?? 'Winner'}{teamName ? ` · ${teamName}` : ''}
        </div>
        {quote && (
          <div style={{ fontSize: 'clamp(1.1rem, 2.5vw, 1.6rem)', fontStyle: 'italic', color: '#e8dcc0', marginTop: '0.6rem' }}>
            “{quote}”
          </div>
        )}
        {extra.map(([k, v]) => (
          <div key={k} style={{ color: '#a89568', marginTop: '0.4rem', fontSize: '0.95rem' }}>
            {statLabel(k)}: {String(v)}
          </div>
        ))}
      </div>
    </div>
  );
}

function StandingsPrompt({ teamName }: { teamName: string | null }) {
  return (
    <div style={SHELL}>
      <div style={KICKER}>Updated standings</div>
      <h2 style={{ fontSize: 'clamp(1.8rem, 4.5vw, 3rem)', margin: 0, letterSpacing: '0.08em' }}>
        {teamName ?? 'WINNER'} TAKES THE LEAD
      </h2>
      <p style={{ color: '#a89568', marginTop: '1rem', maxWidth: 560 }}>
        Cutting to the full standings — rank, chicken count, finishes and
        movement after this match.
      </p>
    </div>
  );
}

function IdleView() {
  return (
    <div style={SHELL}>
      <div style={KICKER}>WWCD Ceremony</div>
      <h2 style={{ fontSize: 'clamp(1.4rem, 3.5vw, 2.4rem)', margin: 0 }}>STANDBY</h2>
      <p style={{ color: '#a89568', marginTop: '1rem', maxWidth: 520 }}>
        No ceremony is running. The operator starts one when a match ends.
      </p>
    </div>
  );
}

function DoneView({ teamName }: { teamName: string | null }) {
  return (
    <div style={SHELL}>
      <div style={KICKER}>WWCD Ceremony</div>
      <h2 style={{ fontSize: 'clamp(1.4rem, 3.5vw, 2.4rem)', margin: 0 }}>
        {teamName ? `${teamName} — CEREMONY COMPLETE` : 'CEREMONY COMPLETE'}
      </h2>
      <p style={{ color: '#a89568', marginTop: '1rem' }}>
        Back to the broadcast.
      </p>
    </div>
  );
}

export default function WwcdCeremony({ state }: { state: CeremonySnapshot | null }) {
  const step = state?.step ?? 'idle';
  const teamName = state?.winner.team_name ?? null;
  switch (step) {
    case 'winner_announce':
      return <WinnerAnnounce teamName={teamName} />;
    case 'stats_rundown':
      return <StatsRundown teamName={teamName} stats={state?.stats ?? null} />;
    case 'interview':
      return <InterviewView teamName={teamName} interview={state?.interview ?? null} />;
    case 'standings':
      return <StandingsPrompt teamName={teamName} />;
    case 'done':
      return <DoneView teamName={teamName} />;
    case 'idle':
    default:
      return <IdleView />;
  }
}
