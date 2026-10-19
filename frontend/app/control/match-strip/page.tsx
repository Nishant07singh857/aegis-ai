"use client";

/**
 * AEGIS AI — MATCH STRIP (Phase 20 / G4).
 *
 * Route: /control/match-strip (operator + admin).
 *
 * Pre-match checklist + countdown to match start:
 *   - checklist: flight path set → teams ready → observers mapped →
 *     stream live, with per-item links to the page where that item is
 *     actually done. Progress bar + "READY FOR BROADCAST" when 4/4.
 *   - countdown: ticking timer to the match start; past start shows
 *     "MATCH LIVE".
 *
 * Persistence (honest choice, documented):
 *   - The backend models matches (backend/app/db/models/match.py →
 *     Match.scheduled_time) but exposes NO match endpoints yet, so
 *     there is no natural backend home for per-match checklist state.
 *     State therefore persists per match in localStorage
 *     (key: aegis:match-strip:v1:<matchNumber>).
 *   - API TODO: when GET /api/v1/tournament/{id}/matches lands, take
 *     the countdown target from Match.scheduled_time and persist the
 *     checklist per match via a PATCH /matches/{id} (meta.checklist)
 *     endpoint. Search this file for "API-TODO".
 *
 * INTERNAL — not on stream.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, EmptyState } from "@/components/ui";

const STORAGE_PREFIX = "aegis:match-strip:v1:";

interface ChecklistItem {
  id: string;
  label: string;
  hint: string;
  /** Where the operator actually does this step. */
  href: string;
  linkLabel: string;
}

const CHECKLIST: ChecklistItem[] = [
  {
    id: "flight-path",
    label: "Flight path set",
    hint: "Plane path planned for this match's map.",
    href: "/broadcast/director",
    linkLabel: "Director console",
  },
  {
    id: "teams-ready",
    label: "Teams ready",
    hint: "All 16 teams in the lobby and ready.",
    href: "/control/observers",
    linkLabel: "Observer mapping",
  },
  {
    id: "observers-mapped",
    label: "Observers mapped",
    hint: "Every observer slot mapped to its team.",
    href: "/control/observers",
    linkLabel: "Observer mapping",
  },
  {
    id: "stream-live",
    label: "Stream live",
    hint: "Output page up and the stream is live.",
    href: "/broadcast/output",
    linkLabel: "Output page",
  },
];

interface PersistedStrip {
  startAt: string | null;
  checks: Record<string, boolean>;
}

function storageKey(matchNumber: number): string {
  return `${STORAGE_PREFIX}${matchNumber}`;
}

function loadStrip(matchNumber: number): PersistedStrip {
  try {
    const raw = localStorage.getItem(storageKey(matchNumber));
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<PersistedStrip>;
      return {
        startAt: typeof parsed.startAt === "string" ? parsed.startAt : null,
        checks:
          parsed.checks && typeof parsed.checks === "object"
            ? parsed.checks
            : {},
      };
    }
  } catch {
    /* corrupt entry -> start fresh */
  }
  return { startAt: null, checks: {} };
}

function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function formatStartTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    day: "numeric",
    month: "short",
  });
}

function MatchStripInner() {
  const [matchNumber, setMatchNumber] = useState(1);
  const [startAt, setStartAt] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [startInput, setStartInput] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [hydrated, setHydrated] = useState(false);

  // Load persisted state when the match changes.
  useEffect(() => {
    const saved = loadStrip(matchNumber);
    setStartAt(saved.startAt);
    setChecks(saved.checks);
    setStartInput(
      saved.startAt ? toDatetimeLocalValue(saved.startAt) : ""
    );
    setHydrated(true);
  }, [matchNumber]);

  // Persist on every change.
  useEffect(() => {
    if (!hydrated) return;
    try {
      const payload: PersistedStrip = { startAt, checks };
      localStorage.setItem(storageKey(matchNumber), JSON.stringify(payload));
    } catch {
      /* storage full / private mode — the checklist still works in-memory */
    }
  }, [startAt, checks, matchNumber, hydrated]);

  // Tick the countdown once a second.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const startMs = useMemo(() => {
    if (!startAt) return null;
    const t = new Date(startAt).getTime();
    return Number.isNaN(t) ? null : t;
  }, [startAt]);

  const checkedCount = useMemo(
    () => CHECKLIST.filter((item) => checks[item.id]).length,
    [checks]
  );
  const allChecked = checkedCount === CHECKLIST.length;

  const toggle = useCallback((id: string) => {
    setChecks((prev) => ({ ...prev, [id]: !prev[id] }));
  }, []);

  const handleSetStart = useCallback(() => {
    if (!startInput) return;
    const t = new Date(startInput).getTime();
    if (Number.isNaN(t)) return;
    setStartAt(new Date(t).toISOString());
  }, [startInput]);

  const handleClearStart = useCallback(() => {
    setStartAt(null);
    setStartInput("");
  }, []);

  const handleReset = useCallback(() => {
    setChecks({});
    handleClearStart();
  }, [handleClearStart]);

  const countdown = startMs === null ? null : startMs - now;
  const isLive = countdown !== null && countdown <= 0;

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto max-w-4xl space-y-6 px-4 py-8">
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">Match Strip</h1>
            <p className="text-sm text-white/50">
              Pre-match checklist + countdown — get every match on air cleanly
            </p>
          </div>
          <Badge tone="amber">INTERNAL — NOT ON STREAM</Badge>
        </div>

        {/* Match selector */}
        <Card title="Match">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMatchNumber((n) => Math.max(1, n - 1))}
              disabled={matchNumber <= 1}
              className="rounded border border-zinc-700 px-4 py-2 text-lg text-zinc-300 hover:border-cyan-500 disabled:opacity-30"
              aria-label="Previous match"
            >
              −
            </button>
            <div className="text-center">
              <div className="text-3xl font-bold text-cyan-300">
                Match {matchNumber}
              </div>
              <div className="text-xs text-zinc-500">
                checklist is saved separately for each match
              </div>
            </div>
            <button
              onClick={() => setMatchNumber((n) => n + 1)}
              className="rounded border border-zinc-700 px-4 py-2 text-lg text-zinc-300 hover:border-cyan-500"
              aria-label="Next match"
            >
              +
            </button>
            <button
              onClick={handleReset}
              className="ml-auto rounded border border-red-800 px-3 py-2 text-sm text-red-300 hover:bg-red-900/30"
            >
              Reset this match
            </button>
          </div>
        </Card>

        {/* Countdown */}
        <Card
          title="Countdown to match start"
          action={
            allChecked ? (
              <Badge tone="green">READY FOR BROADCAST</Badge>
            ) : (
              <Badge tone="slate">
                {checkedCount}/{CHECKLIST.length} checks
              </Badge>
            )
          }
        >
          {startMs === null ? (
            <div>
              <EmptyState message="Match start time not set — set it below and the countdown starts ticking." />
              {/* API-TODO: take the countdown target from
                  Match.scheduled_time once GET
                  /api/v1/tournament/{id}/matches exists instead of this
                  operator-set value. */}
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <input
                  type="datetime-local"
                  value={startInput}
                  onChange={(e) => setStartInput(e.target.value)}
                  className="flex-1 rounded border border-zinc-700 bg-[#121218] px-3 py-2 text-sm text-white focus:border-cyan-500 focus:outline-none"
                />
                <button
                  onClick={handleSetStart}
                  disabled={!startInput}
                  className="rounded bg-cyan-600 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-500 disabled:opacity-40"
                >
                  Set start time
                </button>
              </div>
            </div>
          ) : (
            <div>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div
                  className={`font-mono text-5xl font-bold tabular-nums ${
                    isLive ? "text-red-400" : "text-cyan-300"
                  }`}
                >
                  {isLive ? "MATCH LIVE" : formatCountdown(countdown!)}
                </div>
                <div className="text-sm text-zinc-400">
                  starts {formatStartTime(startAt!)}
                </div>
              </div>
              {!isLive && (
                <div className="mt-2 h-2 overflow-hidden rounded bg-zinc-800">
                  <div
                    className="h-full rounded bg-cyan-500 transition-[width]"
                    style={{
                      width: `${Math.min(
                        100,
                        Math.max(0, (checkedCount / CHECKLIST.length) * 100)
                      )}%`,
                    }}
                  />
                </div>
              )}
              <button
                onClick={handleClearStart}
                className="mt-3 text-xs text-zinc-500 underline hover:text-zinc-300"
              >
                Clear start time
              </button>
            </div>
          )}
        </Card>

        {/* Checklist */}
        <Card title="Pre-match checklist">
          <ol className="space-y-2">
            {CHECKLIST.map((item, i) => {
              const done = !!checks[item.id];
              return (
                <li
                  key={item.id}
                  className={`flex items-center gap-3 rounded-lg border px-4 py-3 transition-colors ${
                    done
                      ? "border-emerald-700/60 bg-emerald-950/30"
                      : "border-white/10 bg-white/5"
                  }`}
                >
                  <button
                    onClick={() => toggle(item.id)}
                    aria-pressed={done}
                    aria-label={`${done ? "Uncheck" : "Check"}: ${item.label}`}
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md border text-lg font-bold ${
                      done
                        ? "border-emerald-500 bg-emerald-600 text-white"
                        : "border-zinc-600 text-transparent hover:border-cyan-500"
                    }`}
                  >
                    ✓
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-zinc-500">{i + 1}.</span>
                      <span
                        className={`font-medium ${
                          done ? "text-emerald-200" : "text-white"
                        }`}
                      >
                        {item.label}
                      </span>
                    </div>
                    <p className="text-xs text-zinc-500">{item.hint}</p>
                  </div>
                  <Link
                    href={item.href}
                    className="shrink-0 text-xs text-cyan-300 underline hover:text-cyan-200"
                  >
                    {item.linkLabel} →
                  </Link>
                </li>
              );
            })}
          </ol>
          {allChecked && (
            <p className="mt-4 rounded border border-emerald-800 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-200">
              All checks green — this match is ready for broadcast.
            </p>
          )}
        </Card>
      </div>
    </div>
  );
}

function toDatetimeLocalValue(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

export default function MatchStripPage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <MatchStripInner />
    </RequireRole>
  );
}
