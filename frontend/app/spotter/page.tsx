"use client";

/**
 * AEGIS AI — SPOTTER CONSOLE (Phase 12).
 *
 * Route: /spotter (operator + admin).
 *
 * A human spotter watches a REAL game and logs events fast. Speed is
 * everything: big touch targets, minimal typing, mobile-first (360px).
 *
 *   - Kill entry: killer/victim names (datalist of recent names), killer
 *     team / victim team dropdowns from the REAL teams API (honest error
 *     when the API is down — never hardcoded teams), weapon dropdown,
 *     Kill vs Knock toggle, one big SUBMIT -> POST /api/v1/feed/ingest
 *     with a NormalizedEvent (see docs/GAME_FEED.md).
 *   - Quick buttons row: Zone update / Match start / Match end — one tap
 *     each, straight to ingest.
 *   - Feedback: last 5 submitted events with timestamps (local state) and
 *     a success/error toast. Ingest failures surface the backend message
 *     honestly.
 *
 * No fake data: everything the page shows either comes from the API or
 * from the spotter's own input in this session.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import { RequireRole } from "@/lib/guard";
import { apiFetch, ApiError } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";

/* ---------- types ---------- */

interface TeamSummary {
  id: string;
  name: string;
  tag?: string | null;
}

interface TeamsResponse {
  data: TeamSummary[];
}

interface IngestResponse {
  ingested: number;
  event_ids: string[];
}

type FeedEventType =
  | "kill"
  | "knock"
  | "team_wipe"
  | "zone_update"
  | "match_start"
  | "match_end"
  | "standings_update"
  | "commentary";

interface LoggedEvent {
  key: string;
  eventType: FeedEventType;
  label: string;
  at: number;
}

interface Toast {
  id: number;
  kind: "ok" | "err";
  message: string;
}

const WEAPONS = [
  "M416",
  "AKM",
  "UMP45",
  "Kar98k",
  "AWM",
  "M24",
  "SCAR-L",
  "Beryl",
  "DP-28",
  "grenade",
  "vehicle",
  "zone",
];

const inputClass =
  "w-full rounded-xl border border-white/15 bg-black/40 px-4 text-base text-white placeholder:text-zinc-600 outline-none focus:border-[#00f0ff]/60 min-h-[52px]";

const selectClass =
  "w-full rounded-xl border border-white/15 bg-black/40 px-4 text-base text-white outline-none focus:border-[#00f0ff]/60 min-h-[52px]";

const labelClass =
  "mb-1.5 block font-mono text-[11px] uppercase tracking-[0.18em] text-zinc-400";

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function SpotterInner() {
  const { token, role } = useAuth();

  /* teams (real API) */
  const [teams, setTeams] = useState<TeamSummary[] | null>(null);
  const [teamsError, setTeamsError] = useState<string | null>(null);
  const [teamsLoading, setTeamsLoading] = useState(true);

  /* kill form */
  const [killer, setKiller] = useState("");
  const [victim, setVictim] = useState("");
  const [killerTeam, setKillerTeam] = useState("");
  const [victimTeam, setVictimTeam] = useState("");
  const [weapon, setWeapon] = useState("M416");
  const [isKnock, setIsKnock] = useState(false);
  const [matchId, setMatchId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [quickBusy, setQuickBusy] = useState<FeedEventType | null>(null);

  /* feedback */
  const [recent, setRecent] = useState<LoggedEvent[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const loadTeams = useCallback(async () => {
    if (!token) return;
    setTeamsLoading(true);
    setTeamsError(null);
    try {
      const res = await apiFetch<TeamsResponse>("/api/v1/teams", { token });
      setTeams(res.data ?? []);
    } catch (e) {
      setTeams(null);
      setTeamsError(
        e instanceof ApiError
          ? `Could not load teams: ${e.body}`
          : "Could not load teams: network error.",
      );
    } finally {
      setTeamsLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void loadTeams();
  }, [loadTeams]);

  const teamOptions = useMemo(
    () => (teams ?? []).slice().sort((a, b) => a.name.localeCompare(b.name)),
    [teams],
  );

  const recentNames = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const ev of recent) {
      for (const name of ev.label.split("|")) {
        const n = name.trim();
        if (n && !seen.has(n)) {
          seen.add(n);
          out.push(n);
        }
      }
      if (out.length >= 24) break;
    }
    return out;
  }, [recent]);

  const pushToast = useCallback((kind: Toast["kind"], message: string) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev.slice(-2), { id, kind, message }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4200);
  }, []);

  const logEvent = useCallback((eventType: FeedEventType, label: string) => {
    const entry: LoggedEvent = {
      key: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      eventType,
      label,
      at: Date.now(),
    };
    setRecent((prev) => [entry, ...prev].slice(0, 5));
  }, []);

  const ingest = useCallback(
    async (eventType: FeedEventType, fields: Record<string, unknown>) => {
      if (!token) throw new Error("Not signed in.");
      const event = {
        event_type: eventType,
        ts: Date.now() / 1000,
        match_id: matchId.trim() || undefined,
        ...fields,
      };
      const res = await apiFetch<IngestResponse>("/api/v1/feed/ingest", {
        method: "POST",
        token,
        body: { events: [event] },
      });
      if (res.ingested !== 1) {
        throw new Error(`Backend ingested ${res.ingested} of 1 events.`);
      }
      return res;
    },
    [token, matchId],
  );

  function actionErrorMessage(e: unknown): string {
    if (e instanceof ApiError) return e.body;
    if (e instanceof Error) return e.message;
    return "Request failed.";
  }

  const submitKill = useCallback(async () => {
    const k = killer.trim();
    const v = victim.trim();
    if (!k || !v) {
      pushToast("err", "Enter both killer and victim names.");
      return;
    }
    if (!killerTeam || !victimTeam) {
      pushToast("err", "Pick killer and victim teams.");
      return;
    }
    setSubmitting(true);
    try {
      await ingest(isKnock ? "knock" : "kill", {
        player: k,
        team: killerTeam,
        victim: v,
        victim_team: victimTeam,
        weapon,
      });
      const label = `${k}|${v}`;
      logEvent(isKnock ? "knock" : "kill", label);
      pushToast(
        "ok",
        `${isKnock ? "Knock" : "Kill"} logged: ${k} (${killerTeam}) → ${v} (${victimTeam}) · ${weapon}`,
      );
      setKiller("");
      setVictim("");
    } catch (e) {
      pushToast("err", `Ingest failed: ${actionErrorMessage(e)}`);
    } finally {
      setSubmitting(false);
    }
  }, [
    killer,
    victim,
    killerTeam,
    victimTeam,
    weapon,
    isKnock,
    ingest,
    logEvent,
    pushToast,
  ]);

  const quickIngest = useCallback(
    async (eventType: FeedEventType, label: string) => {
      setQuickBusy(eventType);
      try {
        await ingest(eventType, {});
        logEvent(eventType, label);
        pushToast("ok", `${label} sent.`);
      } catch (e) {
        pushToast("err", `Ingest failed: ${actionErrorMessage(e)}`);
      } finally {
        setQuickBusy(null);
      }
    },
    [ingest, logEvent, pushToast],
  );

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto w-full max-w-xl px-3 py-5 sm:px-6">
        {/* header */}
        <div className="mb-5 flex items-center justify-between gap-3">
          <div>
            <h1 className="font-mono text-sm font-bold uppercase tracking-[0.25em] text-white">
              Spotter Console
            </h1>
            <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-zinc-500">
              Live event logging · real feed
            </p>
          </div>
          <Badge tone="cyan">{role === "admin" ? "Admin" : "Operator"}</Badge>
        </div>

        {/* quick buttons */}
        <Card title="Quick events" className="mb-4">
          <div className="grid grid-cols-3 gap-2">
            {(
              [
                ["zone_update", "Zone update"],
                ["match_start", "Match start"],
                ["match_end", "Match end"],
              ] as Array<[FeedEventType, string]>
            ).map(([type, label]) => (
              <button
                key={type}
                type="button"
                disabled={quickBusy !== null}
                onClick={() => void quickIngest(type, label)}
                className="rounded-xl border border-white/15 bg-white/5 px-2 py-4 font-mono text-[11px] font-bold uppercase tracking-wider text-zinc-200 transition-colors hover:border-[#00f0ff]/50 hover:text-[#00f0ff] disabled:cursor-not-allowed disabled:opacity-40 max-md:min-h-[56px]"
              >
                {quickBusy === type ? "Sending…" : label}
              </button>
            ))}
          </div>
        </Card>

        {/* kill form */}
        <Card title="Log kill / knock" className="mb-4">
          {teamsLoading ? (
            <Loading label="Loading teams" />
          ) : teamsError ? (
            <ErrorState message={teamsError} onRetry={() => void loadTeams()} />
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submitKill();
              }}
              className="space-y-4"
            >
              {/* kill vs knock toggle */}
              <div>
                <span className={labelClass}>Event type</span>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setIsKnock(false)}
                    aria-pressed={!isKnock}
                    className={`rounded-xl border px-4 py-3.5 font-mono text-sm font-bold uppercase tracking-[0.15em] transition-colors max-md:min-h-[56px] ${
                      !isKnock
                        ? "border-[#ff3366]/60 bg-[#ff3366]/15 text-[#ff8899]"
                        : "border-white/15 bg-white/5 text-zinc-400 hover:border-white/30"
                    }`}
                  >
                    Kill
                  </button>
                  <button
                    type="button"
                    onClick={() => setIsKnock(true)}
                    aria-pressed={isKnock}
                    className={`rounded-xl border px-4 py-3.5 font-mono text-sm font-bold uppercase tracking-[0.15em] transition-colors max-md:min-h-[56px] ${
                      isKnock
                        ? "border-amber-300/60 bg-amber-300/15 text-amber-200"
                        : "border-white/15 bg-white/5 text-zinc-400 hover:border-white/30"
                    }`}
                  >
                    Knock
                  </button>
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor="spotter-killer" className={labelClass}>
                    Killer
                  </label>
                  <input
                    id="spotter-killer"
                    list="spotter-recent-names"
                    value={killer}
                    onChange={(e) => setKiller(e.target.value)}
                    placeholder="Player name"
                    autoComplete="off"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label htmlFor="spotter-victim" className={labelClass}>
                    Victim
                  </label>
                  <input
                    id="spotter-victim"
                    list="spotter-recent-names"
                    value={victim}
                    onChange={(e) => setVictim(e.target.value)}
                    placeholder="Player name"
                    autoComplete="off"
                    className={inputClass}
                  />
                </div>
              </div>
              <datalist id="spotter-recent-names">
                {recentNames.map((n) => (
                  <option key={n} value={n} />
                ))}
              </datalist>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor="spotter-killer-team" className={labelClass}>
                    Killer team
                  </label>
                  <select
                    id="spotter-killer-team"
                    value={killerTeam}
                    onChange={(e) => setKillerTeam(e.target.value)}
                    className={selectClass}
                  >
                    <option value="">Select team</option>
                    {teamOptions.map((t) => (
                      <option key={t.id} value={t.name}>
                        {t.tag ? `${t.tag} · ${t.name}` : t.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="spotter-victim-team" className={labelClass}>
                    Victim team
                  </label>
                  <select
                    id="spotter-victim-team"
                    value={victimTeam}
                    onChange={(e) => setVictimTeam(e.target.value)}
                    className={selectClass}
                  >
                    <option value="">Select team</option>
                    {teamOptions.map((t) => (
                      <option key={t.id} value={t.name}>
                        {t.tag ? `${t.tag} · ${t.name}` : t.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor="spotter-weapon" className={labelClass}>
                    Weapon
                  </label>
                  <select
                    id="spotter-weapon"
                    value={weapon}
                    onChange={(e) => setWeapon(e.target.value)}
                    className={selectClass}
                  >
                    {WEAPONS.map((w) => (
                      <option key={w} value={w}>
                        {w}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="spotter-match" className={labelClass}>
                    Match ID (optional)
                  </label>
                  <input
                    id="spotter-match"
                    value={matchId}
                    onChange={(e) => setMatchId(e.target.value)}
                    placeholder="e.g. match-3"
                    autoComplete="off"
                    className={inputClass}
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={submitting}
                className="w-full rounded-xl bg-[#00f0ff] py-4 font-mono text-base font-bold uppercase tracking-[0.2em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-40 max-md:min-h-[60px]"
              >
                {submitting ? "Sending…" : `Submit ${isKnock ? "knock" : "kill"}`}
              </button>
            </form>
          )}
        </Card>

        {/* recent events */}
        <Card title="Last logged (this session)">
          {recent.length === 0 ? (
            <EmptyState message="Nothing logged yet in this session." />
          ) : (
            <ul className="space-y-2">
              {recent.map((ev) => (
                <li
                  key={ev.key}
                  className="flex items-center justify-between gap-3 rounded-lg bg-black/30 px-4 py-2.5"
                >
                  <div className="flex items-center gap-3">
                    <Badge
                      tone={
                        ev.eventType === "kill"
                          ? "red"
                          : ev.eventType === "knock"
                            ? "amber"
                            : "cyan"
                      }
                    >
                      {ev.eventType.replace("_", " ")}
                    </Badge>
                    <span className="text-sm text-zinc-300">{ev.label}</span>
                  </div>
                  <span className="font-mono text-xs text-zinc-500">
                    {formatTime(ev.at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* toasts */}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`max-w-xl rounded-xl border px-4 py-3 text-sm shadow-lg ${
              t.kind === "ok"
                ? "border-emerald-400/40 bg-emerald-950/90 text-emerald-200"
                : "border-[#ff3366]/40 bg-[#2a0d16]/95 text-[#ffb3c2]"
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SpotterPage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <SpotterInner />
    </RequireRole>
  );
}
