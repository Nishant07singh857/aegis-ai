'use client';

/**
 * AEGIS AI — live stream control card (admin).
 *
 * Phase 32 (K-C2): the API stream path is DEPRECATED — POST
 * /api/v1/stream/start|stop return 410 and GET /api/v1/stream/status
 * reports state "deprecated". The official topology is the shell
 * streamer (docker-compose.streaming.yml, profile streaming); this card
 * renders that deprecation notice instead of start/stop controls.
 * (Historical shape, kept for the notice path:
 *   GET  /api/v1/stream/status  -> { state, uptime_seconds,
 *                                    destinations_requested, destinations_configured,
 *                                    resolution, fps, restart_count, last_error }
 *
 * Stream keys never leave the server: the API never returns them and this
 * UI never asks for or displays them — keys live in server env config only.
 * The optional custom-RTMP field below is the *server* URL (e.g.
 * rtmp://ingest.example.com/live); the stream key still comes from the
 * CUSTOM_RTMP_KEY env var on the server.
 * No mock data: every value on screen comes from the status endpoint.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { Badge, Card, ErrorState, Loading } from '@/components/ui';

const DESTINATIONS = [
  { id: 'youtube', label: 'YouTube' },
  { id: 'twitch', label: 'Twitch' },
  { id: 'facebook', label: 'Facebook' },
  { id: 'custom', label: 'Custom RTMP' },
] as const;

type DestinationId = (typeof DESTINATIONS)[number]['id'];

const RESOLUTIONS = ['720p', '1080p'] as const;

const STATUS_POLL_MS = 10_000;

/**
 * Raw server shape (backend/app/api/v1/stream/schemas.py). Every field is
 * read defensively; missing fields degrade to neutral placeholders rather
 * than crashing the card.
 */
interface RawStreamStatus {
  data?: RawStreamStatus;
  state?: unknown;
  uptime_seconds?: unknown;
  destinations_requested?: unknown;
  destinations_configured?: unknown;
  resolution?: unknown;
  fps?: unknown;
  restart_count?: unknown;
  last_error?: unknown;
}

interface NormalizedStatus {
  state: string;
  uptimeSeconds: number | null;
  destinationsRequested: string[];
  configured: Record<string, boolean>;
  resolution: string | null;
  fps: number | null;
  restartCount: number | null;
  lastError: string | null;
}

function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === 'string' && x.length > 0);
}

function asBoolMap(v: unknown): Record<string, boolean> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {};
  const out: Record<string, boolean> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (typeof val === 'boolean') out[k] = val;
  }
  return out;
}

function normalizeStatus(raw: RawStreamStatus | null): NormalizedStatus {
  const r: RawStreamStatus | null =
    raw && typeof raw === 'object' && raw.data && typeof raw.data === 'object'
      ? raw.data
      : raw;

  const state =
    typeof r?.state === 'string' && r.state ? r.state.toLowerCase() : 'unknown';
  const uptimeRaw = r?.uptime_seconds;
  const uptimeSeconds =
    typeof uptimeRaw === 'number' && Number.isFinite(uptimeRaw)
      ? Math.max(0, Math.floor(uptimeRaw))
      : null;

  const destinationsRequested = asStringArray(r?.destinations_requested);
  const configured = asBoolMap(r?.destinations_configured);
  const resolution =
    typeof r?.resolution === 'string' && r.resolution ? r.resolution : null;
  const fps =
    typeof r?.fps === 'number' && Number.isFinite(r.fps) ? r.fps : null;
  const rc = r?.restart_count;
  const restartCount =
    typeof rc === 'number' && Number.isFinite(rc) ? rc : null;
  const le = r?.last_error;
  const lastError = typeof le === 'string' && le ? le : null;

  return {
    state,
    uptimeSeconds,
    destinationsRequested,
    configured,
    resolution,
    fps,
    restartCount,
    lastError,
  };
}

function fmtUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = m.toString().padStart(2, '0');
  const ss = s.toString().padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function stateTone(state: string): 'green' | 'amber' | 'red' | 'slate' {
  if (state === 'running') return 'green';
  if (state === 'starting' || state === 'stopping') return 'amber';
  if (state === 'failed') return 'red';
  return 'slate';
}

export function StreamControl({
  token,
  onSessionExpired,
}: {
  token: string | null;
  onSessionExpired: () => void;
}) {
  const [status, setStatus] = useState<NormalizedStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<'start' | 'stop' | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<DestinationId>>(new Set(['youtube']));
  const [resolution, setResolution] = useState<(typeof RESOLUTIONS)[number]>('720p');
  const [customUrl, setCustomUrl] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const raw = await apiFetch<RawStreamStatus>('/api/v1/stream/status', {
        token: token ?? undefined,
      });
      setStatus(normalizeStatus(raw));
      setError(null);
    } catch (e: unknown) {
      const err = e as { status?: number; message?: string };
      if (err?.status === 401) {
        onSessionExpired();
        setError('Session expired — log in again.');
      } else {
        setError(err?.message ?? 'Failed to load stream status.');
      }
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [token, onSessionExpired]);

  useEffect(() => {
    if (!token) return;
    void loadStatus();
    pollRef.current = setInterval(() => void loadStatus(), STATUS_POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [token, loadStatus]);

  const toggleDestination = (id: DestinationId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const runAction = async (kind: 'start' | 'stop') => {
    setBusy(kind);
    setActionError(null);
    try {
      const path = kind === 'start' ? '/api/v1/stream/start' : '/api/v1/stream/stop';
      const body =
        kind === 'start'
          ? {
              destinations: [...selected],
              resolution,
              ...(selected.has('custom') && customUrl.trim()
                ? { custom_rtmp_url: customUrl.trim() }
                : {}),
            }
          : {};
      await apiFetch<unknown>(path, {
        method: 'POST',
        token: token ?? undefined,
        body,
      });
      await loadStatus();
    } catch (e: unknown) {
      const err = e as { status?: number; message?: string };
      if (err?.status === 401) {
        onSessionExpired();
        setActionError('Session expired — log in again.');
      } else {
        setActionError(err?.message ?? `${kind} request failed.`);
      }
    } finally {
      setBusy(null);
    }
  };

  const isLive = status?.state === 'running' || status?.state === 'starting';

  const destLabel = (id: string): string =>
    DESTINATIONS.find((d) => d.id === id)?.label ?? id;

  return (
    <Card title="Live stream">
      {loading && <Loading label="Loading stream status…" />}
      {!loading && error && (
        <ErrorState message={error} onRetry={() => void loadStatus()} />
      )}
      {/* Phase 32 (K-C2): the API stream path is deprecated — the shell
          streamer is the official topology. The start/stop controls are
          hidden here so the card cannot present a dead path as live;
          the backend detail (last_error) carries the streamer command. */}
      {!loading && !error && status?.state === 'deprecated' && (
        <div className="rounded-lg border border-amber-300/30 bg-amber-300/5 px-3 py-2">
          <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-amber-300">
            API stream deprecated
          </p>
          <p className="mt-1 text-sm text-zinc-300">
            {status.lastError ??
              'Use the shell streamer (docker-compose.streaming.yml, profile streaming) — see docs/STREAMING.md.'}
          </p>
        </div>
      )}
      {!loading && !error && status && status.state !== 'deprecated' && (
        <div className="space-y-5">
          {/* status row */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <Badge tone={stateTone(status.state)}>{status.state}</Badge>
            <span className="font-mono text-xs text-white/50">
              uptime:{' '}
              <span className="text-[#00f0ff]">
                {status.uptimeSeconds != null
                  ? fmtUptime(status.uptimeSeconds)
                  : '—'}
              </span>
            </span>
            {status.resolution && (
              <span className="font-mono text-xs text-white/50">
                {status.resolution}
                {status.fps != null && ` @ ${status.fps}fps`}
              </span>
            )}
            {status.restartCount != null && status.restartCount > 0 && (
              <span className="font-mono text-xs text-amber-300/80">
                restarts: {status.restartCount}
              </span>
            )}
            <span className="font-mono text-xs text-white/50">
              active:{' '}
              {status.destinationsRequested.length > 0 ? (
                <span className="text-white/85">
                  {status.destinationsRequested.map(destLabel).join(', ')}
                </span>
              ) : (
                <span className="text-white/35">none</span>
              )}
            </span>
            <button
              type="button"
              onClick={() => void loadStatus()}
              className="ml-auto rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-zinc-300 hover:border-[#00f0ff]/50 hover:text-[#00f0ff] transition-colors"
            >
              Refresh
            </button>
          </div>

          {/* last error, when present */}
          {status.lastError && (
            <div className="rounded-lg border border-[#ff3366]/30 bg-[#ff3366]/5 px-3 py-2">
              <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-[#ff3366]">
                Last error
              </p>
              <p className="mt-1 text-sm text-zinc-300">{status.lastError}</p>
            </div>
          )}

          {/* destination status + toggles */}
          <div>
            <div className="mb-2 font-mono text-[11px] uppercase tracking-[0.2em] text-white/40">
              Destinations
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {DESTINATIONS.map((d) => {
                const cfg = status.configured[d.id];
                const checked = selected.has(d.id);
                const disabled = isLive || busy !== null;
                return (
                  <label
                    key={d.id}
                    className={`flex items-center gap-2.5 rounded-lg border bg-white/5 px-3 py-2.5 text-sm transition-colors ${
                      disabled
                        ? 'cursor-not-allowed opacity-50'
                        : 'cursor-pointer hover:border-[#00f0ff]/40'
                    } ${checked ? 'border-[#00f0ff]/50' : 'border-white/10'}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => toggleDestination(d.id)}
                      className="h-4 w-4 accent-[#00f0ff]"
                    />
                    <span className="text-white/85">{d.label}</span>
                    {typeof cfg === 'boolean' && (
                      <Badge tone={cfg ? 'green' : 'slate'}>
                        {cfg ? 'ready' : 'no key'}
                      </Badge>
                    )}
                  </label>
                );
              })}
            </div>
            {isLive && (
              <p className="mt-2 font-mono text-[11px] text-white/40">
                Stop the stream to change destinations.
              </p>
            )}

            {/* custom RTMP server URL (server address only — key stays in env) */}
            {selected.has('custom') && !isLive && (
              <div className="mt-3">
                <label
                  htmlFor="stream-custom-url"
                  className="mb-1 block font-mono text-[11px] uppercase tracking-[0.2em] text-white/40"
                >
                  Custom RTMP server URL
                </label>
                <input
                  id="stream-custom-url"
                  type="text"
                  value={customUrl}
                  onChange={(e) => setCustomUrl(e.target.value)}
                  placeholder="rtmp://ingest.example.com/live"
                  spellCheck={false}
                  autoComplete="off"
                  className="w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-sm text-white/90 placeholder:text-white/25 focus:border-[#00f0ff]/60 focus:outline-none"
                />
                <p className="mt-1 font-mono text-[11px] text-white/35">
                  Server URL only — the stream key comes from the server
                  environment.
                </p>
              </div>
            )}
          </div>

          {/* resolution + start / stop */}
          <div className="flex flex-wrap items-center gap-3">
            <label
              htmlFor="stream-resolution"
              className="font-mono text-[11px] uppercase tracking-[0.2em] text-white/40"
            >
              Resolution
            </label>
            <select
              id="stream-resolution"
              value={resolution}
              disabled={isLive || busy !== null}
              onChange={(e) =>
                setResolution(e.target.value as (typeof RESOLUTIONS)[number])
              }
              className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-sm text-white/90 focus:border-[#00f0ff]/60 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 max-md:min-h-[48px]"
            >
              {RESOLUTIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <div className="flex w-full items-center gap-3 sm:w-auto">
              <button
                type="button"
                disabled={busy !== null || isLive || selected.size === 0}
                onClick={() => void runAction('start')}
                className="rounded-lg bg-[#00f0ff] px-6 py-2.5 font-mono text-xs font-bold uppercase tracking-[0.15em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-30 max-sm:flex-1 max-md:min-h-[48px]"
              >
                {busy === 'start' ? 'Starting…' : 'Start stream'}
              </button>
              <button
                type="button"
                disabled={busy !== null || !isLive}
                onClick={() => void runAction('stop')}
                className="rounded-lg border border-[#ff3366]/50 bg-[#ff3366]/10 px-6 py-2.5 font-mono text-xs font-bold uppercase tracking-[0.15em] text-[#ff3366] transition-opacity disabled:cursor-not-allowed disabled:opacity-30 hover:bg-[#ff3366]/20 max-sm:flex-1 max-md:min-h-[48px]"
              >
                {busy === 'stop' ? 'Stopping…' : 'Stop'}
              </button>
            </div>
          </div>

          {actionError && (
            <ErrorState message={actionError} onRetry={() => setActionError(null)} />
          )}

          <p className="font-mono text-[11px] text-white/35">
            Stream keys are read from the server environment — they are never
            shown or editable here.
          </p>
        </div>
      )}
    </Card>
  );
}
