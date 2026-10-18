'use client';

/**
 * AEGIS AI — lightweight /ws/live socket for the Phase 10 broadcast-flow
 * pages (pre-show, WWCD ceremony, zone timer).
 *
 * Why this exists instead of reusing lib/useLiveSocket: Phase 9 is in flight
 * and may be reshaping that hook; these pages only need raw frames for a
 * handful of message types, so they take a small self-contained hook with
 * its own backoff/reconnect. The message contract is the same:
 *   { "type": "<domain>.<event>", "data": { ... } }
 * over /ws/live (auth via ?token=, mirroring lib/useLiveSocket).
 *
 * Usage:
 *   const { status, latest, seq } = useBroadcastSocket(['preshow.state']);
 *   const state = latest['preshow.state']; // data payload or undefined
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '@/lib/api';
import { useAuth } from '@/lib/auth';

export type SocketStatus = 'connecting' | 'live' | 'reconnecting' | 'offline';

const MAX_BACKOFF_MS = 30_000;
const MAX_ATTEMPTS = 10;
const PING_INTERVAL_MS = 25_000;

function toWsUrl(base: string, token: string | null): string {
  const stripped = base.replace(/\/+$/, '');
  const wsBase = stripped.replace(/^http(s?):\/\//i, 'ws$1://');
  const url = `${wsBase}/ws/live`;
  return token ? url + '?token=' + encodeURIComponent(token) : url;
}

export function useBroadcastSocket(types: string[]): {
  status: SocketStatus;
  latest: Record<string, any>;
  seq: number;
  reconnect: () => void;
} {
  const { token, isAuthed } = useAuth();
  const [status, setStatus] = useState<SocketStatus>('connecting');
  const [latest, setLatest] = useState<Record<string, any>>({});
  const [seq, setSeq] = useState(0);
  const wanted = useRef<Set<string>>(new Set(types));
  const attempt = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const socket = useRef<WebSocket | null>(null);

  const reconnect = useCallback(() => {
    attempt.current = 0;
    if (retryTimer.current) clearTimeout(retryTimer.current);
    socket.current?.close();
  }, []);

  useEffect(() => {
    wanted.current = new Set(types);
  }, [types.join('|')]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let disposed = false;

    const cleanup = () => {
      if (retryTimer.current) clearTimeout(retryTimer.current);
      if (pingTimer.current) clearInterval(pingTimer.current);
      retryTimer.current = null;
      pingTimer.current = null;
      if (socket.current) {
        socket.current.onclose = null;
        socket.current.onerror = null;
        socket.current.onmessage = null;
        try {
          socket.current.close();
        } catch {
          /* already closed */
        }
        socket.current = null;
      }
    };

    const connect = () => {
      if (disposed) return;
      cleanup();
      setStatus(attempt.current === 0 ? 'connecting' : 'reconnecting');
      let ws: WebSocket;
      try {
        ws = new WebSocket(toWsUrl(API_BASE, token));
      } catch {
        scheduleRetry();
        return;
      }
      socket.current = ws;

      ws.onopen = () => {
        if (disposed) return;
        attempt.current = 0;
        setStatus('live');
        pingTimer.current = setInterval(() => {
          try {
            ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
          } catch {
            /* socket died; onclose will handle */
          }
        }, PING_INTERVAL_MS);
      };

      ws.onmessage = (event) => {
        if (disposed) return;
        try {
          const msg = JSON.parse(event.data);
          if (msg && typeof msg.type === 'string' && wanted.current.has(msg.type)) {
            setLatest((prev) => ({ ...prev, [msg.type]: msg.data ?? null }));
            setSeq((n) => n + 1);
          }
        } catch {
          /* ignore malformed frames */
        }
      };

      const onDead = () => {
        if (disposed) return;
        scheduleRetry();
      };
      ws.onclose = onDead;
      ws.onerror = onDead;
    };

    const scheduleRetry = () => {
      if (disposed) return;
      attempt.current += 1;
      if (attempt.current > MAX_ATTEMPTS) {
        setStatus('offline');
        return;
      }
      setStatus('reconnecting');
      const backoff = Math.min(1000 * 2 ** (attempt.current - 1), MAX_BACKOFF_MS);
      retryTimer.current = setTimeout(connect, backoff);
    };

    connect();
    return () => {
      disposed = true;
      cleanup();
    };
    // Reconnect when auth state flips (token available after login).
  }, [token, isAuthed, reconnect]);

  return { status, latest, seq, reconnect };
}
