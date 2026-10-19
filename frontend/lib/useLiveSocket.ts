'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from './api';
import { useAuth } from './auth';
import { isDirectorState, isDirectorSuggestion, normalizeDirectorState } from './director';
import type { DirectorState, DirectorSuggestion } from './director';

export type ConnStatus = 'connecting' | 'live' | 'reconnecting' | 'offline';

export interface Team {
  id: string;
  name: string;
  tag?: string;
  logo?: string;
  color?: string;
  rank?: number;
  kills?: number;
  placement_pts?: number;
  total_pts?: number;
  wwcd?: number;
  alive?: boolean;
  players?: string[];
  source?: 'db' | 'demo';
}

export interface KillEvent {
  id: string;
  killer: string;
  killerTeam: string;
  killerTeamId: string;
  victim: string;
  victimTeam: string;
  weapon: string;
  isKnock: boolean;
  timestamp: number;
}

export interface ActionAck {
  action: string;
  status: string;
  message?: string;
}

/** Phase 10 PiP service state ("pip.state" frames); null until received. */
export interface PiPStateFrame {
  enabled: boolean;
  main_source: string;
  inset_source: string;
  inset_position: 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right';
  updated_by?: string | null;
  updated_at: string;
}

/** Phase 10 rundown segment ("rundown.state" frames). */
export interface RundownSegmentFrame {
  id: string;
  title: string;
  planned_duration_s: number;
  notes: string;
  order: number;
}

/** Phase 10 zone clock state ("zone.tick" frames); null until received. */
export interface ZonePhaseFrame {
  name: string;
  duration_seconds: number;
  phase_number: number;
  total_phases: number;
}

export interface ZoneStateFrame {
  status: 'no_data' | 'active' | 'complete' | string;
  current_phase?: ZonePhaseFrame | null;
  time_remaining_seconds?: number | null;
  updated_at?: string;
}

/** Phase 10 rundown state ("rundown.state" frames); null until received. */
export interface RundownStateFrame {
  segments: RundownSegmentFrame[];
  /** -1 = not started yet; otherwise the index on air. */
  current_index: number;
  started_at?: string | null;
  segment_started_at?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

/** Type guard for pip.state frames — malformed frames are ignored. */
function isPiPStateFrame(data: any): data is PiPStateFrame {
  return (
    data !== null &&
    typeof data === 'object' &&
    typeof data.enabled === 'boolean' &&
    typeof data.main_source === 'string' &&
    typeof data.inset_source === 'string' &&
    ['top_left', 'top_right', 'bottom_left', 'bottom_right'].includes(
      data.inset_position
    )
  );
}

/** Type guard for zone.tick frames — malformed frames are ignored. */
function isZoneStateFrame(data: any): data is ZoneStateFrame {
  if (data === null || typeof data !== 'object') return false;
  if (typeof data.status !== 'string') return false;
  const cp = data.current_phase;
  if (cp !== undefined && cp !== null) {
    if (typeof cp !== 'object' || typeof cp.name !== 'string') return false;
  }
  const rem = data.time_remaining_seconds;
  if (
    rem !== undefined &&
    rem !== null &&
    (typeof rem !== 'number' || !Number.isFinite(rem))
  )
    return false;
  return true;
}

/** Type guard for rundown.state frames — malformed frames are ignored. */
function isRundownStateFrame(data: any): data is RundownStateFrame {
  return (
    data !== null &&
    typeof data === 'object' &&
    Array.isArray(data.segments) &&
    typeof data.current_index === 'number'
  );
}

const MAX_BACKOFF_MS = 30_000;
const MAX_ATTEMPTS = 10;
const PING_INTERVAL_MS = 25_000;
const KILL_FEED_CAP = 30;
const AGENT_LOG_CAP = 50;

/** Derive the WS URL from the HTTP API base: http->ws, https->wss, + /ws/live. */
function toWsUrl(base: string, token: string | null): string {
  const stripped = base.replace(/\/+$/, '');
  const wsBase = stripped.replace(/^http(s?):\/\//i, 'ws$1://');
  const url = `${wsBase}/ws/live`;
  return token ? `${url}?token=${encodeURIComponent(token)}` : url;
}

export function useLiveSocket(apiBase?: string): {
  status: ConnStatus;
  retryCount: number;
  reconnect: () => void;
  standings: Team[];
  killFeed: KillEvent[];
  director: any;
  /** Phase 6 director service state ("director.state" frames); null until received. */
  directorState: DirectorState | null;
  /** Phase 7 latest AI suggestion frame ("director.suggestion"); null until received. */
  directorSuggestion: DirectorSuggestion | null;
  /** Monotonic counter of received suggestion frames (drives queue reconciliation). */
  suggestionSeq: number;
  /** Phase 10 PiP layout state ("pip.state" frames); null until received. */
  pipState: PiPStateFrame | null;
  /** Phase 10 rundown state ("rundown.state" frames); null until received. */
  rundownState: RundownStateFrame | null;
  /**
   * Phase 18 (W3) zone clock state ("zone.tick" frames); null until
   * received. The live countdown is derived client-side from
   * time_remaining_seconds — see lib/useZoneClock.
   */
  zone: ZoneStateFrame | null;
  health: any;
  agents: any[];
  modes: Record<string, boolean>;
  agentLogs: any[];
  ack: ActionAck | null;
  send: (msg: object) => void;
} {
  const { token, isAuthed } = useAuth();

  const [status, setStatus] = useState<ConnStatus>('connecting');
  const [retryCount, setRetryCount] = useState(0);
  const [standings, setStandings] = useState<Team[]>([]);
  const [killFeed, setKillFeed] = useState<KillEvent[]>([]);
  const [director, setDirector] = useState<any>(null);
  const [directorState, setDirectorState] = useState<DirectorState | null>(null);
  const [directorSuggestion, setDirectorSuggestion] = useState<DirectorSuggestion | null>(null);
  const [suggestionSeq, setSuggestionSeq] = useState(0);
  const [pipState, setPipState] = useState<PiPStateFrame | null>(null);
  const [rundownState, setRundownState] = useState<RundownStateFrame | null>(null);
  const [zone, setZone] = useState<ZoneStateFrame | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [agents, setAgents] = useState<any[]>([]);
  const [modes, setModes] = useState<Record<string, boolean>>({});
  const [agentLogs, setAgentLogs] = useState<any[]>([]);
  const [ack, setAck] = useState<ActionAck | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const attemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const intentionalCloseRef = useRef(false);
  const connectRef = useRef<() => void>(() => {});
  const authRef = useRef<{ token: string | null; isAuthed: boolean }>({
    token: null,
    isAuthed: false,
  });
  authRef.current = { token, isAuthed };

  // Optional per-caller API base (overlay pages pass their ?api= override).
  // Kept in a ref so the connect callback always uses the latest value.
  const apiBaseRef = useRef<string | undefined>(apiBase);
  apiBaseRef.current = apiBase;

  const clearTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (pingTimerRef.current) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
  }, []);

  const closeSocket = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    }
  }, []);

  const scheduleReconnect = useCallback(() => {
    const attempts = attemptsRef.current + 1;
    attemptsRef.current = attempts;
    setRetryCount(attempts);
    if (attempts >= MAX_ATTEMPTS) {
      setStatus('offline');
      return;
    }
    setStatus('reconnecting');
    const delay = Math.min(2 ** (attempts - 1) * 1000, MAX_BACKOFF_MS);
    reconnectTimerRef.current = setTimeout(() => {
      connectRef.current();
    }, delay);
  }, []);

  const handleFrame = useCallback((type: string, data: any) => {
    switch (type) {
      case 'connected':
        setStatus('live');
        break;
      case 'standings':
        setStandings(Array.isArray(data) ? data : []);
        break;
      case 'kill_feed_init':
        if (Array.isArray(data)) {
          setKillFeed([...data].reverse().slice(0, KILL_FEED_CAP));
        }
        break;
      case 'kill':
        if (data && typeof data === 'object') {
          setKillFeed((prev) => [data as KillEvent, ...prev].slice(0, KILL_FEED_CAP));
        }
        break;
      case 'director_state':
      case 'scene_switch':
        setDirector(data ?? null);
        break;
      case 'director.state':
        // Phase 6 director service broadcast (every director change).
        // Malformed frames are ignored — the hook keeps the last known
        // good state and the page re-fetches on reconnect.
        if (isDirectorState(data)) {
          setDirectorState(normalizeDirectorState(data));
        }
        break;
      case 'director.suggestion':
        // Phase 7 AI suggestion event (created / approved / rejected).
        // Malformed frames are ignored; the console reconciles the queue
        // against GET /director/suggestions on reconnect.
        if (isDirectorSuggestion(data)) {
          setDirectorSuggestion(data);
          setSuggestionSeq((n) => n + 1);
        }
        break;
      case 'system_health':
        setHealth(data ?? null);
        break;
      case 'agents_state':
        setAgents(Array.isArray(data) ? data : []);
        break;
      case 'modes_state':
        setModes(data && typeof data === 'object' ? (data as Record<string, boolean>) : {});
        break;
      case 'agent_logs':
        if (Array.isArray(data)) {
          setAgentLogs([...data].reverse().slice(0, AGENT_LOG_CAP));
        }
        break;
      case 'agent_log':
        if (data && typeof data === 'object') {
          setAgentLogs((prev) => [data, ...prev].slice(0, AGENT_LOG_CAP));
        }
        break;
      case 'action_ack':
        if (data && typeof data === 'object') {
          setAck(data as ActionAck);
        }
        break;
      case 'pip.state':
        // Phase 10 PiP layout state (every PiP change). Malformed frames
        // are ignored — the page re-fetches GET /pip/state on reconnect.
        if (isPiPStateFrame(data)) {
          setPipState(data);
        }
        break;
      case 'rundown.state':
        // Phase 10 rundown state (every rundown change). Malformed frames
        // are ignored — the page re-fetches GET /rundown/state on reconnect.
        if (isRundownStateFrame(data)) {
          setRundownState(data);
        }
        break;
      case 'zone.tick':
        // Phase 18 (W3) zone clock: the backend sends a snapshot on WS
        // connect and on every zone change (phases set / advance / reset).
        // Malformed frames are ignored. The live countdown is derived
        // client-side — see lib/useZoneClock.
        if (isZoneStateFrame(data)) {
          setZone(data);
        }
        break;
      default:
        // commentary and other streams are not consumed by this dashboard
        break;
    }
  }, []);

  const connect = useCallback(() => {
    clearTimers();
    closeSocket();
    intentionalCloseRef.current = false;
    if (attemptsRef.current > 0) setStatus('reconnecting');
    const { token: t, isAuthed: authed } = authRef.current;
    let ws: WebSocket;
    try {
      ws = new WebSocket(toWsUrl(apiBaseRef.current ?? API_BASE, authed ? t : null));
    } catch {
      scheduleReconnect();
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      attemptsRef.current = 0;
      setRetryCount(0);
      // status flips to 'live' once the server sends the first `connected` frame
      pingTimerRef.current = setInterval(() => {
        if (wsRef.current === ws && ws.readyState === WebSocket.OPEN) {
          try {
            ws.send('ping');
          } catch {
            /* socket died between checks */
          }
        }
      }, PING_INTERVAL_MS);
    };

    ws.onmessage = (ev: MessageEvent) => {
      if (typeof ev.data !== 'string') return;
      if (ev.data === 'pong') return;
      try {
        const frame = JSON.parse(ev.data);
        if (frame && typeof frame.type === 'string') {
          handleFrame(frame.type, frame.data);
        }
      } catch {
        /* ignore malformed frames */
      }
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      wsRef.current = null;
      clearTimers();
      if (intentionalCloseRef.current) return;
      scheduleReconnect();
    };

    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* noop */
      }
    };
  }, [clearTimers, closeSocket, handleFrame, scheduleReconnect]);

  connectRef.current = connect;

  /** No-op unless the socket is live. */
  const send = useCallback((msg: object) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }, []);

  /** Manual reconnect: reset backoff counters and connect immediately. */
  const reconnect = useCallback(() => {
    clearTimers();
    attemptsRef.current = 0;
    setRetryCount(0);
    setStatus('connecting');
    connectRef.current();
  }, [clearTimers]);

  // Connect on mount, and reconnect whenever auth state or the API base
  // changes (token may have been added/removed, which changes the ?token=
  // query on the WS URL; ?api= changes the host outright).
  useEffect(() => {
    intentionalCloseRef.current = false;
    attemptsRef.current = 0;
    setRetryCount(0);
    setStatus('connecting');
    connectRef.current();
    return () => {
      intentionalCloseRef.current = true;
      clearTimers();
      closeSocket();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, isAuthed, apiBase]);

  return {
    status,
    retryCount,
    reconnect,
    standings,
    killFeed,
    director,
    directorState,
    directorSuggestion,
    suggestionSeq,
    pipState,
    rundownState,
    zone,
    health,
    agents,
    modes,
    agentLogs,
    ack,
    send,
  };
}
