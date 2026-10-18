'use client';

/**
 * AEGIS AI — PRODUCER RUN-OF-SHOW (Phase 10).
 *
 * Route: /control/rundown (operator + admin). The show plan for the
 * broadcast: ordered segments with planned durations, the current
 * segment highlighted, elapsed/remaining time, next-up, and producer
 * controls (advance, reset, add/remove, reorder).
 *
 * INTERNAL — not on stream.
 *
 * Data: GET/POST /api/v1/rundown/* (authenticated; writes are operator+)
 * plus live "rundown.state" WS frames via useLiveSocket. Every mutation
 * re-reads the state from the backend — the UI never invents rundown
 * data. Elapsed time is derived client-side from the backend's
 * segment_started_at timestamp.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '@/lib/auth';
import { apiFetch, ApiError } from '@/lib/api';
import { useLiveSocket, type RundownStateFrame } from '@/lib/useLiveSocket';
import { RequireRole } from '@/lib/guard';
import { Badge, Card, EmptyState } from '@/components/ui';

function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return `${m}:${String(rest).padStart(2, '0')}`;
}

function parseTs(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const t = new Date(ts).getTime();
  return Number.isNaN(t) ? null : t;
}

function RundownInner() {
  const { token, logout } = useAuth();
  const { rundownState: wsState } = useLiveSocket();

  const [state, setState] = useState<RundownStateFrame | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // form state for adding a segment
  const [title, setTitle] = useState('');
  const [durationMin, setDurationMin] = useState('10');
  const [notes, setNotes] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await apiFetch<RundownStateFrame>('/api/v1/rundown/state', {
        token,
      });
      setState(data);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) logout();
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [token, logout]);

  useEffect(() => {
    load();
  }, [load]);

  // Live WS frames keep the rundown fresh between polls.
  useEffect(() => {
    if (wsState) setState(wsState);
  }, [wsState]);

  // Tick the elapsed/remaining clock once a second.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const mutate = useCallback(
    async (path: string, opts?: { method?: string; body?: unknown }) => {
      setBusy(true);
      try {
        const data = await apiFetch<RundownStateFrame>(path, {
          token,
          method: opts?.method ?? 'POST',
          body: opts?.body,
        });
        setState(data);
        setError(null);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) logout();
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [token, logout]
  );

  const handleAdvance = useCallback(() => mutate('/api/v1/rundown/advance'), [mutate]);
  const handleReset = useCallback(() => mutate('/api/v1/rundown/reset'), [mutate]);

  const handleAdd = useCallback(async () => {
    setFormError(null);
    const mins = Number(durationMin);
    if (!title.trim()) {
      setFormError('Give the segment a title.');
      return;
    }
    if (!Number.isFinite(mins) || mins <= 0) {
      setFormError('Duration must be more than 0 minutes.');
      return;
    }
    setBusy(true);
    try {
      await apiFetch('/api/v1/rundown/segments', {
        token,
        method: 'POST',
        body: {
          title: title.trim(),
          planned_duration_s: Math.round(mins * 60),
          notes: notes.trim() || null,
        },
      });
      setTitle('');
      setDurationMin('10');
      setNotes('');
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) logout();
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [title, durationMin, notes, token, logout, load]);

  const handleRemove = useCallback(
    (id: string) =>
      mutate(`/api/v1/rundown/segments/${id}`, { method: 'DELETE' }),
    [mutate]
  );

  const handleMove = useCallback(
    (id: string, newOrder: number) =>
      mutate(`/api/v1/rundown/segments/${id}/reorder`, {
        body: { new_order: newOrder },
      }),
    [mutate]
  );

  const segments = useMemo(
    () =>
      (Array.isArray(state?.segments) ? state!.segments : [])
        .slice()
        .sort((a, b) => a.order - b.order),
    [state]
  );

  const currentIndex = state?.current_index ?? -1;
  const current = currentIndex >= 0 ? segments[currentIndex] : null;
  const nextUp = currentIndex + 1 < segments.length ? segments[currentIndex + 1] : null;

  const segmentStartMs = parseTs(state?.segment_started_at);
  const elapsedS =
    segmentStartMs !== null && current ? Math.floor((now - segmentStartMs) / 1000) : null;
  const remainingS =
    elapsedS !== null && current ? current.planned_duration_s - elapsedS : null;

  const totalPlannedS = segments.reduce((acc, s) => acc + (s.planned_duration_s || 0), 0);

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto max-w-5xl space-y-6 px-4 py-8">
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">Run of Show</h1>
            <p className="text-sm text-white/50">
              Producer rundown — segments, timings and show control
            </p>
          </div>
          <Badge tone="amber">INTERNAL — NOT ON STREAM</Badge>
        </div>

        {error && (
          <div className="rounded-lg border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}

        <Card
          title="Show control"
          action={
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleReset}
                disabled={busy}
                className="rounded-md border border-white/15 px-3 py-1.5 text-sm text-white/70 hover:bg-white/5 disabled:opacity-40"
              >
                Reset
              </button>
              <button
                type="button"
                onClick={handleAdvance}
                disabled={busy}
                className="rounded-md bg-[#00f0ff] px-4 py-1.5 text-sm font-bold text-black hover:bg-cyan-300 disabled:opacity-40"
              >
                {currentIndex < 0 ? 'Start show' : 'Advance →'}
              </button>
            </div>
          }
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg bg-white/5 p-3">
              <div className="text-xs text-white/50">On air now</div>
              <div className="mt-1 text-lg font-semibold text-[#00f0ff]">
                {current ? current.title : 'Not started'}
              </div>
              {current && elapsedS !== null && (
                <div className="mt-1 text-sm text-white/60">
                  Elapsed {formatDuration(elapsedS)}
                  {remainingS !== null && remainingS >= 0 && (
                    <> · {formatDuration(remainingS)} left</>
                  )}
                  {remainingS !== null && remainingS < 0 && (
                    <span className="text-amber-300">
                      {' '}· over by {formatDuration(-remainingS)}
                    </span>
                  )}
                </div>
              )}
            </div>
            <div className="rounded-lg bg-white/5 p-3">
              <div className="text-xs text-white/50">Next up</div>
              <div className="mt-1 text-lg font-semibold text-white">
                {nextUp ? nextUp.title : '—'}
              </div>
              {nextUp && (
                <div className="mt-1 text-sm text-white/60">
                  Planned {formatDuration(nextUp.planned_duration_s)}
                </div>
              )}
            </div>
            <div className="rounded-lg bg-white/5 p-3">
              <div className="text-xs text-white/50">Show totals</div>
              <div className="mt-1 text-lg font-semibold text-white">
                {segments.length} segments
              </div>
              <div className="mt-1 text-sm text-white/60">
                Planned {formatDuration(totalPlannedS)}
              </div>
            </div>
          </div>
        </Card>

        <Card title="Timeline">
          {state === null && (
            <EmptyState message="Loading rundown…" />
          )}
          {state !== null && segments.length === 0 && (
            <EmptyState message="The rundown is empty — add the first segment below." />
          )}
          {segments.length > 0 && (
            <ol className="space-y-2">
              {segments.map((s, i) => {
                const isCurrent = i === currentIndex;
                const isPast = currentIndex >= 0 && i < currentIndex;
                return (
                  <li
                    key={s.id}
                    className={`flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2.5 ${
                      isCurrent
                        ? 'border-[#00f0ff]/60 bg-[#00f0ff]/10'
                        : isPast
                          ? 'border-white/5 bg-white/[0.02] opacity-60'
                          : 'border-white/10 bg-white/[0.03]'
                    }`}
                  >
                    <span
                      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                        isCurrent
                          ? 'bg-[#00f0ff] text-black'
                          : 'bg-white/10 text-white/60'
                      }`}
                    >
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-white">{s.title}</span>
                        {isCurrent && <Badge tone="cyan">ON AIR</Badge>}
                        {isPast && <Badge tone="slate">done</Badge>}
                      </div>
                      {s.notes && (
                        <p className="mt-0.5 truncate text-xs text-white/45">
                          {s.notes}
                        </p>
                      )}
                    </div>
                    <span className="shrink-0 font-mono text-sm text-white/70">
                      {formatDuration(s.planned_duration_s)}
                    </span>
                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        onClick={() => handleMove(s.id, i - 1)}
                        disabled={busy || i === 0}
                        title="Move earlier"
                        className="rounded border border-white/15 px-2 py-1 text-xs text-white/70 hover:bg-white/5 disabled:opacity-30"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        onClick={() => handleMove(s.id, i + 1)}
                        disabled={busy || i === segments.length - 1}
                        title="Move later"
                        className="rounded border border-white/15 px-2 py-1 text-xs text-white/70 hover:bg-white/5 disabled:opacity-30"
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        onClick={() => handleRemove(s.id)}
                        disabled={busy}
                        title="Remove segment"
                        className="rounded border border-red-400/30 px-2 py-1 text-xs text-red-300 hover:bg-red-400/10 disabled:opacity-30"
                      >
                        ✕
                      </button>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </Card>

        <Card title="Add segment">
          {formError && (
            <p className="mb-3 rounded-lg border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-200">
              {formError}
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-[1fr_140px_auto]">
            <label className="block">
              <span className="mb-1 block text-xs text-white/50">Title</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Map 2 — Erangel"
                maxLength={140}
                className="w-full rounded-md border border-white/15 bg-black/40 px-3 py-2 text-sm text-white placeholder:text-white/30"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-white/50">
                Duration (minutes)
              </span>
              <input
                value={durationMin}
                onChange={(e) => setDurationMin(e.target.value)}
                inputMode="decimal"
                className="w-full rounded-md border border-white/15 bg-black/40 px-3 py-2 text-sm text-white"
              />
            </label>
            <div className="flex items-end">
              <button
                type="button"
                onClick={handleAdd}
                disabled={busy}
                className="rounded-md bg-[#00f0ff] px-4 py-2 text-sm font-bold text-black hover:bg-cyan-300 disabled:opacity-40"
              >
                Add
              </button>
            </div>
          </div>
          <label className="mt-3 block">
            <span className="mb-1 block text-xs text-white/50">
              Producer notes (optional)
            </span>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Cues, talking points, graphics to trigger…"
              maxLength={2000}
              className="w-full rounded-md border border-white/15 bg-black/40 px-3 py-2 text-sm text-white placeholder:text-white/30"
            />
          </label>
          <p className="mt-2 text-xs text-white/35">
            Rundown state is held in memory by the backend (documented
            follow-up: durable DB persistence).
          </p>
        </Card>
      </div>
    </div>
  );
}

export default function RundownPage() {
  return (
    <RequireRole roles={['operator', 'admin']}>
      <RundownInner />
    </RequireRole>
  );
}
