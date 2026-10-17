'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from './api';
import { useAuth } from './auth';

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
    health,
    agents,
    modes,
    agentLogs,
    ack,
    send,
  };
}
