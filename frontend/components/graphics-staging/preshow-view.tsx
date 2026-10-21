'use client';

/**
 * AEGIS AI — pre-show broadcast view (Phase 10, staging).
 *
 * Full-screen renderer for the pre-show state machine
 * (countdown -> desk_intro -> match_intro -> starting). Pure display: it
 * takes a `state` snapshot ({ step, countdown_target }) as a prop and never
 * fetches or mutates anything itself, so it can be embedded anywhere the
 * broadcast output needs it.
 *
 * Steps:
 *  - countdown:    big live timer to `countdown_target`; when no target is
 *                  set it shows an honest "LIVE SOON" card (never a fake
 *                  countdown).
 *  - desk_intro:   pre-show desk card.
 *  - match_intro:  flight-path style card: schematic map with a drawn flight
 *                  path + the REAL team list passed in via `teams` (from
 *                  GET /api/v1/teams — never invented here).
 *  - starting:     "MATCH STARTING" transition sting.
 *
 * INTEGRATION POINT (Phase 9): embed in the output page after Phase 9 lands
 * by rendering <PreshowView state={...} teams={...} /> inside the pre-show
 * scene of /broadcast/output (or its Phase 9 successor), driven by the
 * `preshow.state` WS frames.
 */

import { useEffect, useMemo, useState, type CSSProperties } from 'react';

export interface PreshowSnapshot {
  step: 'countdown' | 'desk_intro' | 'match_intro' | 'starting';
  steps: string[];
  countdown_target: string | null;
  updated_at: string;
}

export interface PreshowTeam {
  id: string;
  name: string;
  tag?: string;
}

/** Director-set flight path from GET /api/v1/flight/path (Phase 24). */
export interface PreshowFlightPath {
  is_set: boolean;
  source?: string | null;
  map_id?: string | null;
  start?: { name: string; x: number | null; y: number | null } | null;
  end?: { name: string; x: number | null; y: number | null } | null;
  start_poi?: string | null;
  end_poi?: string | null;
}

function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function formatCountdown(target: string, now: number): string {
  const diff = Math.max(0, new Date(target).getTime() - now);
  const h = Math.floor(diff / 3_600_000);
  const m = Math.floor((diff % 3_600_000) / 60_000);
  const s = Math.floor((diff % 60_000) / 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

const SHELL: CSSProperties = {
  width: '100%',
  minHeight: '100vh',
  background: 'radial-gradient(1200px 700px at 50% 20%, #14213d 0%, #0a0e1a 60%, #05070d 100%)',
  color: '#f2f5fb',
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
  color: '#8fa3c8',
  textTransform: 'uppercase',
  marginBottom: '1rem',
};

function CountdownView({ target }: { target: string | null }) {
  const now = useNow(500);
  if (!target) {
    return (
      <div style={SHELL}>
        <div style={KICKER}>AEGIS Broadcast</div>
        <h1 style={{ fontSize: 'clamp(2.5rem, 7vw, 5rem)', margin: 0, letterSpacing: '0.12em' }}>
          LIVE SOON
        </h1>
        <p style={{ color: '#8fa3c8', marginTop: '1rem', maxWidth: 560 }}>
          The broadcast is about to begin. The operator has not set a start
          time yet — stay tuned.
        </p>
      </div>
    );
  }
  const live = new Date(target).getTime() <= now;
  return (
    <div style={SHELL}>
      <div style={KICKER}>AEGIS Broadcast · Starting in</div>
      <div
        style={{
          fontSize: 'clamp(3.5rem, 11vw, 8rem)',
          fontWeight: 700,
          letterSpacing: '0.08em',
          fontVariantNumeric: 'tabular-nums',
          color: live ? '#ffd166' : '#f2f5fb',
          textShadow: '0 0 40px rgba(255,209,102,0.25)',
        }}
      >
        {live ? 'LIVE NOW' : formatCountdown(target, now)}
      </div>
      <p style={{ color: '#8fa3c8', marginTop: '1rem' }}>
        {live
          ? 'The show is live — the feed is switching over.'
          : `Broadcast begins ${new Date(target).toLocaleString()}`}
      </p>
    </div>
  );
}

function DeskIntroView() {
  return (
    <div style={SHELL}>
      <div style={KICKER}>Pre-show</div>
      <h1 style={{ fontSize: 'clamp(2rem, 5.5vw, 4rem)', margin: 0, letterSpacing: '0.1em' }}>
        THE DESK
      </h1>
      <p style={{ color: '#8fa3c8', marginTop: '1rem', maxWidth: 620 }}>
        Hosts and analysts break down the lobby, the storylines, and what to
        watch for in the matches ahead.
      </p>
    </div>
  );
}

/**
 * Flight-path map. When a REAL director-set path with coordinates is
 * supplied (Phase 24, GET /api/v1/flight/path) the line is drawn between
 * the actual POI coordinates and labelled "DIRECTOR-SET" with the POI
 * names. Otherwise this is the pre-existing decorative schematic —
 * clearly abstract, never presented as data.
 */
function FlightPathMap({ flightPath }: { flightPath?: PreshowFlightPath | null }) {
  const real =
    flightPath?.is_set &&
    flightPath.start &&
    flightPath.end &&
    flightPath.start.x != null &&
    flightPath.end.x != null
      ? flightPath
      : null;
  const sx = real ? (real.start!.x as number) * 400 : 40;
  const sy = real ? (real.start!.y as number) * 400 : 330;
  const ex = real ? (real.end!.x as number) * 400 : 360;
  const ey = real ? (real.end!.y as number) * 400 : 70;
  return (
    <svg
      viewBox="0 0 400 400"
      role="img"
      aria-label={real ? `Flight path (director-set): ${real.start!.name} to ${real.end!.name}` : 'Schematic map with flight path'}
      style={{ width: 'min(46vw, 420px)', height: 'auto', opacity: 0.95 }}
    >
      <rect x="0" y="0" width="400" height="400" rx="18" fill="#0d1526" stroke="#22314f" />
      {Array.from({ length: 9 }, (_, i) => (
        <g key={i} stroke="#1a2742" strokeWidth="1">
          <line x1={(i + 1) * 40} y1="0" x2={(i + 1) * 40} y2="400" />
          <line x1="0" y1={(i + 1) * 40} x2="400" y2={(i + 1) * 40} />
        </g>
      ))}
      <circle cx="120" cy="110" r="34" fill="#16233d" stroke="#2c3f66" />
      <circle cx="270" cy="290" r="48" fill="#16233d" stroke="#2c3f66" />
      <circle cx="300" cy="120" r="22" fill="#16233d" stroke="#2c3f66" />
      <line x1={sx} y1={sy} x2={ex} y2={ey} stroke="#ffd166" strokeWidth="3" strokeDasharray="10 8" />
      <polygon points={`${ex},${ey} ${ex - 16},${ey + 2} ${ex - 8},${ey + 14}`} fill="#ffd166" />
      <circle cx={sx} cy={sy} r="7" fill="#ffd166" />
      <circle cx={ex} cy={ey} r="5" fill="#ffd166" />
      <text x={sx} y={sy + 26} fill="#8fa3c8" fontSize="13" textAnchor="middle">
        {real ? real.start!.name : 'DROP START'}
      </text>
      <text x={ex} y={ey - 14} fill="#8fa3c8" fontSize="13" textAnchor="middle">
        {real ? real.end!.name : 'PATH END'}
      </text>
      <text x="200" y="392" fill={real ? '#ffd166' : '#5b6b8c'} fontSize="12" textAnchor="middle" letterSpacing="2">
        {real
          ? `DIRECTOR-SET${real.map_id ? ` · ${real.map_id.toUpperCase()}` : ''}`
          : 'SCHEMATIC — FLIGHT PATH NOT SET'}
      </text>
    </svg>
  );
}

function MatchIntroView({
  teams,
  flightPath,
}: {
  teams: PreshowTeam[];
  flightPath?: PreshowFlightPath | null;
}) {
  return (
    <div style={{ ...SHELL, flexDirection: 'row', gap: '3rem', flexWrap: 'wrap' }}>
      <FlightPathMap flightPath={flightPath} />
      <div style={{ textAlign: 'left', minWidth: 260, maxWidth: 420 }}>
        <div style={{ ...KICKER, marginBottom: '0.5rem' }}>Match intro · Flight path</div>
        {flightPath?.is_set ? (
          <p style={{ color: '#ffd166', margin: '0 0 0.75rem', fontSize: '0.95rem' }}>
            Flight path (director-set): {flightPath.start_poi ?? flightPath.start?.name ?? '—'}
            {' → '}
            {flightPath.end_poi ?? flightPath.end?.name ?? '—'}
            {flightPath.map_id ? ` · ${flightPath.map_id}` : ''}
          </p>
        ) : (
          <p style={{ color: '#8fa3c8', margin: '0 0 0.75rem', fontSize: '0.95rem' }}>
            Flight path not set yet — schematic shown.
          </p>
        )}
        <h2 style={{ fontSize: 'clamp(1.6rem, 3.5vw, 2.6rem)', margin: '0 0 1rem', letterSpacing: '0.06em' }}>
          THE LOBBY
        </h2>
        {teams.length === 0 ? (
          <p style={{ color: '#8fa3c8' }}>
            Team list is loading from the tournament roster — the flight path
            will be announced once the lobby is confirmed.
          </p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: '0.45rem', maxHeight: '46vh', overflow: 'hidden' }}>
            {teams.map((t, i) => (
              <li
                key={t.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                  background: 'rgba(20,33,61,0.55)',
                  border: '1px solid #22314f',
                  borderRadius: 10,
                  padding: '0.5rem 0.9rem',
                }}
              >
                <span style={{ color: '#ffd166', fontWeight: 700, width: '2ch' }}>{i + 1}</span>
                <span style={{ fontWeight: 600, letterSpacing: '0.04em' }}>{t.name}</span>
                {t.tag && <span style={{ color: '#8fa3c8', fontSize: '0.85em' }}>[{t.tag}]</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function StartingView() {
  return (
    <div style={SHELL}>
      <div style={KICKER}>AEGIS Broadcast</div>
      <h1
        style={{
          fontSize: 'clamp(2.5rem, 8vw, 6rem)',
          margin: 0,
          letterSpacing: '0.14em',
          color: '#ffd166',
          animation: 'preshow-pulse 1.2s ease-in-out infinite',
        }}
      >
        MATCH STARTING
      </h1>
      <style>{`@keyframes preshow-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.55; } }`}</style>
      <p style={{ color: '#8fa3c8', marginTop: '1rem' }}>
        Switching to the live observer feed…
      </p>
    </div>
  );
}

export default function PreshowView({
  state,
  teams = [],
  flightPath = null,
}: {
  state: PreshowSnapshot | null;
  teams?: PreshowTeam[];
  /** Real director-set flight path (Phase 24); null -> schematic. */
  flightPath?: PreshowFlightPath | null;
}) {
  const step = state?.step ?? 'countdown';
  const target = state?.countdown_target ?? null;
  const content = useMemo(() => {
    switch (step) {
      case 'desk_intro':
        return <DeskIntroView />;
      case 'match_intro':
        return <MatchIntroView teams={teams} flightPath={flightPath} />;
      case 'starting':
        return <StartingView />;
      case 'countdown':
      default:
        return <CountdownView target={target} />;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, target, teams, flightPath]);
  return content;
}
