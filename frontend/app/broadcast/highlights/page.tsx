"use client";

/**
 * AEGIS AI — highlights reel page.
 *
 * Route: /broadcast/highlights
 *
 * Shows the top scored highlight moments from the replay buffer
 * (GET /api/v1/replay/highlights): multi-kills, squad wipes, clutches —
 * each with its score, reason, timestamp, and involved players/teams.
 * "Play" re-emits the moment's event window over the WS fan-out as
 * replay.play so the output page / PiP can re-render the segment
 * (event replay — not video).
 *
 * Honest empty state: when the simulator/feed has produced no
 * qualifying kill events, the reel is empty and says so. Nothing here
 * is mocked — every number comes from the real backend.
 *
 * Auth: viewing is read-only (any signed-in role). "Play" and "Mark"
 * require operator or admin; the backend enforces it too.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";

interface HighlightMoment {
  moment_id: string;
  kind: "multi_kill" | "squad_wipe" | "clutch";
  score: number;
  reason: string;
  player: string | null;
  team: string | null;
  opponents: string[];
  window_start: number;
  window_end: number;
  window_start_iso: string;
  window_end_iso: string;
  event_ids: string[];
}

interface ReplayMark {
  id: string;
  label: string;
  ts: number;
  ts_iso: string;
  event_count: number;
}

const KIND_TONE: Record<HighlightMoment["kind"], "amber" | "red" | "cyan"> = {
  multi_kill: "amber",
  squad_wipe: "red",
  clutch: "cyan",
};

const KIND_LABEL: Record<HighlightMoment["kind"], string> = {
  multi_kill: "Multi-kill",
  squad_wipe: "Squad wipe",
  clutch: "Clutch",
};

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function HighlightsPageInner() {
  const { token, role } = useAuth();
  const canOperate = role === "operator" || role === "admin";

  const [reel, setReel] = useState<HighlightMoment[] | null>(null);
  const [marks, setMarks] = useState<ReplayMark[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [playMsg, setPlayMsg] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [markMsg, setMarkMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [reelRes, marksRes] = await Promise.all([
        apiFetch<HighlightMoment[]>("/api/v1/replay/highlights?limit=10", {
          token: token ?? undefined,
        }),
        apiFetch<ReplayMark[]>("/api/v1/replay/marks", {
          token: token ?? undefined,
        }),
      ]);
      setReel(reelRes ?? []);
      setMarks(marksRes ?? []);
    } catch (e) {
      setReel(null);
      setError(e instanceof Error ? e.message : "Request failed");
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handlePlay(m: HighlightMoment) {
    setPlayingId(m.moment_id);
    setPlayMsg(null);
    try {
      await apiFetch("/api/v1/replay/play", {
        method: "POST",
        token: token ?? undefined,
        body: {
          from_ts: m.window_start_iso,
          to_ts: m.window_end_iso,
        },
      });
      setPlayMsg(`Playing: ${m.reason} — watch the output page / PiP.`);
    } catch (e) {
      setPlayMsg(e instanceof Error ? `Play failed: ${e.message}` : "Play failed");
    } finally {
      setPlayingId(null);
    }
  }

  async function handleCapture() {
    setMarkMsg(null);
    try {
      const mark = await apiFetch<ReplayMark>("/api/v1/replay/capture", {
        method: "POST",
        token: token ?? undefined,
        body: { label: label.trim() || undefined },
      });
      setMarks((prev) => [mark, ...prev]);
      setLabel("");
      setMarkMsg(`Marked "${mark.label}" — replayable from the marks list.`);
    } catch (e) {
      setMarkMsg(e instanceof Error ? `Mark failed: ${e.message}` : "Mark failed");
    }
  }

  async function handlePlayMark(mark: ReplayMark) {
    setPlayMsg(null);
    try {
      await apiFetch("/api/v1/replay/play", {
        method: "POST",
        token: token ?? undefined,
        body: { mark_id: mark.id },
      });
      setPlayMsg(`Playing mark "${mark.label}" — watch the output page / PiP.`);
    } catch (e) {
      setPlayMsg(e instanceof Error ? `Play failed: ${e.message}` : "Play failed");
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0f] p-6 text-white">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              Highlights <span className="text-cyan-400">Reel</span>
            </h1>
            <p className="mt-1 text-sm text-zinc-400">
              Top scored moments from the replay buffer — event replay, not
              video. Scores are 0–100 with the detection reason shown.
            </p>
            <p className="mt-1 text-sm text-zinc-500">
              Whole day instead?{" "}
              <Link
                href="/broadcast/day-reel"
                className="text-cyan-300 underline hover:text-cyan-200"
              >
                Open the day reel →
              </Link>
            </p>
          </div>
          <button
            onClick={() => void load()}
            className="rounded border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:border-cyan-500 hover:text-cyan-300"
          >
            Refresh
          </button>
        </div>

        {canOperate && (
          <Card title="Capture a mark" className="mb-6">
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Mark label (optional, e.g. 'Final circle fight')"
                maxLength={120}
                className="flex-1 rounded border border-zinc-700 bg-[#121218] px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-cyan-500 focus:outline-none"
              />
              <button
                onClick={() => void handleCapture()}
                className="rounded bg-cyan-600 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-500"
              >
                Mark now
              </button>
            </div>
            {markMsg && <p className="mt-2 text-xs text-zinc-400">{markMsg}</p>}
          </Card>
        )}

        {playMsg && (
          <p className="mb-4 rounded border border-cyan-800 bg-cyan-950/40 px-3 py-2 text-sm text-cyan-200">
            {playMsg}
          </p>
        )}

        {error && <ErrorState message={error} />}
        {reel === null && !error && <Loading label="Loading highlights" />}

        {reel !== null && reel.length === 0 && !error && (
          <EmptyState message="No highlights yet — the replay buffer has no qualifying kill events. Moments appear here once real fights happen (simulator or a live feed)." />
        )}

        {reel !== null && reel.length > 0 && (
          <ol className="space-y-3">
            {reel.map((m, i) => (
              <li key={m.moment_id}>
                <Card
                  title={`#${i + 1} — ${KIND_LABEL[m.kind]}`}
                  action={
                    <Badge tone={KIND_TONE[m.kind]}>
                      {m.score}/100
                    </Badge>
                  }
                >
                  <p className="text-sm text-zinc-200">{m.reason}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
                    <span>
                      {fmtTime(m.window_start_iso)} → {fmtTime(m.window_end_iso)}
                    </span>
                    {m.player && <span>Player: {m.player}</span>}
                    {m.team && <span>Team: {m.team}</span>}
                    <span>{m.event_ids.length} events</span>
                  </div>
                  {canOperate && (
                    <button
                      onClick={() => void handlePlay(m)}
                      disabled={playingId === m.moment_id}
                      className="mt-3 rounded bg-amber-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
                    >
                      {playingId === m.moment_id ? "Playing…" : "▶ Play replay"}
                    </button>
                  )}
                </Card>
              </li>
            ))}
          </ol>
        )}

        <div className="mt-8">
          <h2 className="mb-3 text-lg font-semibold">Saved marks</h2>
          {marks.length === 0 ? (
            <EmptyState message="No marks captured yet. Operators can bookmark a moment with the capture box above." />
          ) : (
            <ul className="space-y-2">
              {marks.map((mark) => (
                <li
                  key={mark.id}
                  className="flex items-center justify-between gap-3 rounded border border-zinc-800 bg-[#121218] px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-white">
                      {mark.label}
                    </p>
                    <p className="text-xs text-zinc-500">
                      {fmtTime(mark.ts_iso)} · {mark.event_count} events in window
                    </p>
                  </div>
                  {canOperate && (
                    <button
                      onClick={() => void handlePlayMark(mark)}
                      className="shrink-0 rounded border border-amber-600 px-3 py-1 text-xs font-semibold text-amber-300 hover:bg-amber-600 hover:text-white"
                    >
                      Play
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

/** Route guard: any signed-in role may view the reel; the backend
 *  operator-gates Play and Mark. Viewers are NOT redirected away. */
export default function HighlightsPage() {
  return (
    <RequireRole roles={["viewer", "operator", "admin"]}>
      <HighlightsPageInner />
    </RequireRole>
  );
}
