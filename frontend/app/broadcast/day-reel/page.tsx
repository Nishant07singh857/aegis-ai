"use client";

/**
 * AEGIS AI — whole-day highlight reel page (Phase 20 / G5).
 *
 * Route: /broadcast/day-reel
 *
 * Plays/lists the whole-day reel from GET /api/v1/replay/day-reel:
 * the day's recorded highlight moments ordered by excitement score,
 * duration-capped by the backend (default 5 min / 24 segments).
 *
 * "Play" re-emits a segment's event window over the WS fan-out as
 * replay.play so the output page / PiP can re-render it (event replay —
 * not video), exactly like /broadcast/highlights.
 *
 * Honest empty state: when nothing was recorded today the reel is
 * empty and says so — "No highlights recorded yet". Moments are never
 * synthesised.
 *
 * Auth: viewing is read-only (any signed-in role). "Play" requires
 * operator or admin; the backend enforces it too.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";

interface DayReelSegment {
  rank: number;
  moment_id: string;
  match_id: string | null;
  match_number: number | null;
  title: string;
  kind: string;
  score: number;
  duration_ms: number;
  window_start_iso: string | null;
  window_end_iso: string | null;
  player: string | null;
  team: string | null;
}

interface DayReel {
  day: string;
  segments: DayReelSegment[];
  total_duration_ms: number;
  moment_count: number;
  trimmed_count: number;
  match_ids: string[];
  max_duration_ms: number;
  max_segments: number;
  is_empty: boolean;
}

const KIND_TONE: Record<string, "amber" | "red" | "cyan"> = {
  multi_kill: "amber",
  squad_wipe: "red",
  clutch: "cyan",
};

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

function DayReelPageInner() {
  const { token, role } = useAuth();
  const canOperate = role === "operator" || role === "admin";

  const [reel, setReel] = useState<DayReel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [playMsg, setPlayMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await apiFetch<DayReel>("/api/v1/replay/day-reel?limit=50", {
        token: token ?? undefined,
      });
      setReel(data);
    } catch (e) {
      setReel(null);
      setError(e instanceof Error ? e.message : "Request failed");
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handlePlay(seg: DayReelSegment) {
    if (!seg.window_start_iso || !seg.window_end_iso) return;
    setPlayingId(seg.moment_id);
    setPlayMsg(null);
    try {
      await apiFetch("/api/v1/replay/play", {
        method: "POST",
        token: token ?? undefined,
        body: {
          from_ts: seg.window_start_iso,
          to_ts: seg.window_end_iso,
        },
      });
      setPlayMsg(`Playing: ${seg.title} — watch the output page / PiP.`);
    } catch (e) {
      setPlayMsg(e instanceof Error ? `Play failed: ${e.message}` : "Play failed");
    } finally {
      setPlayingId(null);
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0f] p-6 text-white">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              Day <span className="text-cyan-400">Reel</span>
            </h1>
            <p className="mt-1 text-sm text-zinc-400">
              The day&apos;s recorded highlights, ordered by excitement score —
              event replay, not video. Scores are 0–100.
            </p>
          </div>
          <button
            onClick={() => void load()}
            className="rounded border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:border-cyan-500 hover:text-cyan-300"
          >
            Refresh
          </button>
        </div>

        {playMsg && (
          <p className="mb-4 rounded border border-cyan-800 bg-cyan-950/40 px-3 py-2 text-sm text-cyan-200">
            {playMsg}
          </p>
        )}

        {error && <ErrorState message={error} />}
        {reel === null && !error && <Loading label="Loading day reel" />}

        {reel !== null && reel.is_empty && !error && (
          <EmptyState message="No highlights recorded yet — moments appear here once real fights are recorded during today's matches." />
        )}

        {reel !== null && !reel.is_empty && !error && (
          <>
            <Card
              title={`Day reel — ${reel.day}`}
              action={
                <Badge tone="cyan">
                  {fmtDuration(reel.total_duration_ms)}
                </Badge>
              }
              className="mb-6"
            >
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-zinc-400">
                <span>{reel.segments.length} moments in the reel</span>
                <span>
                  {reel.moment_count} moment{reel.moment_count === 1 ? "" : "s"}{" "}
                  recorded today
                </span>
                {reel.match_ids.length > 0 && (
                  <span>
                    {reel.match_ids.length} match
                    {reel.match_ids.length === 1 ? "" : "es"} covered
                  </span>
                )}
                {reel.trimmed_count > 0 && (
                  <span>
                    {reel.trimmed_count} lower-ranked moment
                    {reel.trimmed_count === 1 ? "" : "s"} trimmed by the{" "}
                    {fmtDuration(reel.max_duration_ms)} / {reel.max_segments}
                    -segment cap
                  </span>
                )}
              </div>
            </Card>

            <ol className="space-y-3">
              {reel.segments.map((seg) => (
                <li key={seg.moment_id}>
                  <Card
                    title={`#${seg.rank} — ${seg.title || seg.kind}`}
                    action={
                      <Badge tone={KIND_TONE[seg.kind] ?? "slate"}>
                        {seg.score}/100
                      </Badge>
                    }
                  >
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
                      <span>
                        {fmtTime(seg.window_start_iso)} →{" "}
                        {fmtTime(seg.window_end_iso)}
                      </span>
                      {seg.match_number !== null && (
                        <span>Match {seg.match_number}</span>
                      )}
                      {seg.player && <span>Player: {seg.player}</span>}
                      {seg.team && <span>Team: {seg.team}</span>}
                      <span>{fmtDuration(seg.duration_ms)} in reel</span>
                    </div>
                    {canOperate &&
                      seg.window_start_iso &&
                      seg.window_end_iso && (
                        <button
                          onClick={() => void handlePlay(seg)}
                          disabled={playingId === seg.moment_id}
                          className="mt-3 rounded bg-amber-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
                        >
                          {playingId === seg.moment_id
                            ? "Playing…"
                            : "▶ Play replay"}
                        </button>
                      )}
                  </Card>
                </li>
              ))}
            </ol>
          </>
        )}

        <div className="mt-8 text-sm text-zinc-500">
          Looking for the current match&apos;s moments?{" "}
          <Link
            href="/broadcast/highlights"
            className="text-cyan-300 underline hover:text-cyan-200"
          >
            Open the highlights reel →
          </Link>
        </div>
      </div>
    </div>
  );
}

/** Route guard: any signed-in role may view the reel; the backend
 *  operator-gates Play. Viewers are NOT redirected away. */
export default function DayReelPage() {
  return (
    <RequireRole roles={["viewer", "operator", "admin", "director"]}>
      <DayReelPageInner />
    </RequireRole>
  );
}
