"use client";

/**
 * AEGIS AI — OBSERVER CONSOLE (Phase 16, W3).
 *
 * Route: /observer (observer role only).
 *
 * One observer = one team. Everything on this page is scoped to the team
 * id carried in the observer's JWT (`team_id` claim — the JWT payload is
 * the source of truth; there is deliberately no team picker here).
 *
 *   - Header: "OBSERVER" + their team name + slot number
 *     (e.g. "OBSERVER · <team name> — Slot 3"), resolved as
 *     JWT team_id -> GET /api/v1/teams/{id} for the name, and the slot
 *     number from GET /api/v1/observers matched on team_id.
 *   - Live status: ONLY their own observer slot is rendered (the slots
 *     list is filtered client-side to their team_id; other slots are never
 *     shown).
 *   - Team-scoped logging: KILL / KNOCK / ZONE quick buttons, POSTed to
 *     /api/v1/feed/ingest exactly like the spotter console. The killer
 *     team is ALWAYS their own team (pre-filled and locked — no picker).
 *     The victim team is a dropdown of the OTHER teams (names only; no
 *     other team's details are ever fetched or rendered).
 *
 * No fake data: every value comes from the API or the observer's own
 * input. Mobile-first (runs on phones), big touch targets.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import { RequireRole } from "@/lib/guard";
import { decodeJwtClaims } from "@/lib/useRole";
import { apiFetch, ApiError } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";

/* ---------- types ---------- */

interface TeamDetail {
  id: string;
  name: string;
  tag?: string | null;
}

interface TeamDetailResponse {
  data: TeamDetail;
}

interface TeamSummary {
  id: string;
  name: string;
  tag?: string | null;
}

interface TeamsResponse {
  data: TeamSummary[];
}

type SlotStatus = "idle" | "connected" | "feeding";

interface ObserverSlot {
  slot_number: number;
  team_id: string | null;
  team_name: string | null;
  status: SlotStatus;
  last_heartbeat: string | null;
}

interface SlotsResponse {
  data: ObserverSlot[];
}

interface IngestResponse {
  ingested: number;
  event_ids: string[];
}

type FeedEventType = "kill" | "knock" | "zone_update";

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

function formatHeartbeat(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "never" : formatTime(d.getTime());
}

function statusTone(status: SlotStatus): "green" | "cyan" | "slate" {
  switch (status) {
    case "feeding":
      return "green";
    case "connected":
      return "cyan";
    default:
      return "slate";
  }
}

function ObserverInner() {
  const { token } = useAuth();

  /* JWT scope claims — the payload is the source of truth for team_id. */
  const claims = useMemo(
    () => (token ? decodeJwtClaims(token) : null),
    [token],
  );
  const teamId = claims?.teamId ?? null;

  /* own team (name only — no other team's details are ever requested) */
  const [team, setTeam] = useState<TeamDetail | null>(null);
  const [teamError, setTeamError] = useState<string | null>(null);
  const [teamLoading, setTeamLoading] = useState(true);

  /* own observer slot (filtered from the slot list by team_id) */
  const [slot, setSlot] = useState<ObserverSlot | null>(null);
  const [slotMissing, setSlotMissing] = useState(false);
  const [slotError, setSlotError] = useState<string | null>(null);
  const [slotLoading, setSlotLoading] = useState(true);

  /* other teams — names only, for the victim dropdown */
  const [otherTeams, setOtherTeams] = useState<TeamSummary[]>([]);
  const [otherTeamsError, setOtherTeamsError] = useState<string | null>(null);

  /* kill/knock form (killer team is locked to the observer's own team) */
  const [killer, setKiller] = useState("");
  const [victim, setVictim] = useState("");
  const [victimTeam, setVictimTeam] = useState("");
  const [weapon, setWeapon] = useState("M416");
  const [isKnock, setIsKnock] = useState(false);
  const [matchId, setMatchId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [zoneBusy, setZoneBusy] = useState(false);

  /* feedback */
  const [recent, setRecent] = useState<LoggedEvent[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);

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

  /* ---- loads ---- */

  const loadTeam = useCallback(async () => {
    if (!token || !teamId) return;
    setTeamLoading(true);
    setTeamError(null);
    try {
      const res = await apiFetch<TeamDetailResponse>(
        `/api/v1/teams/${encodeURIComponent(teamId)}`,
        { token },
      );
      setTeam(res.data ?? null);
    } catch (e) {
      setTeam(null);
      setTeamError(
        e instanceof ApiError
          ? `Could not load your team: ${e.body}`
          : "Could not load your team: network error.",
      );
    } finally {
      setTeamLoading(false);
    }
  }, [token, teamId]);

  const loadSlot = useCallback(async () => {
    if (!token || !teamId) return;
    setSlotLoading(true);
    setSlotError(null);
    setSlotMissing(false);
    try {
      const res = await apiFetch<SlotsResponse>("/api/v1/observers", {
        token,
      });
      // Render ONLY the slot assigned to this observer's team.
      const mine = (res.data ?? []).find((s) => s.team_id === teamId) ?? null;
      setSlot(mine);
      setSlotMissing(mine === null);
    } catch (e) {
      setSlot(null);
      setSlotError(
        e instanceof ApiError
          ? `Could not load your slot: ${e.body}`
          : "Could not load your slot: network error.",
      );
    } finally {
      setSlotLoading(false);
    }
  }, [token, teamId]);

  const loadOtherTeams = useCallback(async () => {
    if (!token || !teamId) return;
    setOtherTeamsError(null);
    try {
      const res = await apiFetch<TeamsResponse>("/api/v1/teams", { token });
      // Victim dropdown needs the other teams' NAMES only — never details.
      setOtherTeams(
        (res.data ?? [])
          .filter((t) => t.id !== teamId)
          .slice()
          .sort((a, b) => a.name.localeCompare(b.name)),
      );
    } catch (e) {
      setOtherTeams([]);
      setOtherTeamsError(
        e instanceof ApiError
          ? `Could not load teams: ${e.body}`
          : "Could not load teams: network error.",
      );
    }
  }, [token, teamId]);

  useEffect(() => {
    if (!teamId) {
      setTeamLoading(false);
      setSlotLoading(false);
      return;
    }
    void loadTeam();
    void loadSlot();
    void loadOtherTeams();
  }, [teamId, loadTeam, loadSlot, loadOtherTeams]);

  /* ---- ingest ---- */

  const ingest = useCallback(
    async (eventType: FeedEventType, fields: Record<string, unknown>) => {
      if (!token) throw new Error("Not signed in.");
      if (!team) throw new Error("Your team is not loaded yet.");
      const event = {
        event_type: eventType,
        ts: Date.now() / 1000,
        match_id: matchId.trim() || undefined,
        // Killer side is ALWAYS the observer's own team — locked, no picker.
        team: team.name,
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
    [token, team, matchId],
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
    if (!victimTeam) {
      pushToast("err", "Pick the victim's team.");
      return;
    }
    if (!team) {
      pushToast("err", "Your team is not loaded yet.");
      return;
    }
    setSubmitting(true);
    try {
      await ingest(isKnock ? "knock" : "kill", {
        player: k,
        victim: v,
        victim_team: victimTeam,
        weapon,
      });
      logEvent(isKnock ? "knock" : "kill", `${k} → ${v}`);
      pushToast(
        "ok",
        `${isKnock ? "Knock" : "Kill"} logged: ${k} (${team.name}) → ${v} (${victimTeam}) · ${weapon}`,
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
    victimTeam,
    weapon,
    isKnock,
    team,
    ingest,
    logEvent,
    pushToast,
  ]);

  const submitZone = useCallback(async () => {
    setZoneBusy(true);
    try {
      await ingest("zone_update", {});
      logEvent("zone_update", "Zone update");
      pushToast("ok", "Zone update sent.");
    } catch (e) {
      pushToast("err", `Ingest failed: ${actionErrorMessage(e)}`);
    } finally {
      setZoneBusy(false);
    }
  }, [ingest, logEvent, pushToast]);

  /* ---- render ---- */

  if (!teamId) {
    return (
      <div className="min-h-screen bg-[#0a0a0f] text-white">
        <div className="mx-auto w-full max-w-xl px-3 py-5 sm:px-6">
          <ErrorState message="This observer account is not linked to a team (no team_id in your login token). Ask the tournament admin to issue a team-scoped observer login." />
        </div>
      </div>
    );
  }

  const loading = teamLoading || slotLoading;

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto w-full max-w-xl px-3 py-5 sm:px-6">
        {/* header: OBSERVER + team name + slot number */}
        <div className="mb-5 flex items-start justify-between gap-3">
          <div>
            <h1 className="font-mono text-sm font-bold uppercase tracking-[0.25em] text-white">
              Observer
            </h1>
            {loading ? (
              <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-zinc-500">
                Loading your assignment…
              </p>
            ) : team ? (
              <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-zinc-400">
                {team.tag ? `${team.tag} · ` : ""}
                {team.name}
                {slot ? ` — Slot ${slot.slot_number}` : " — no slot assigned"}
              </p>
            ) : null}
          </div>
          {!loading && slot && (
            <Badge tone={statusTone(slot.status)}>
              {slot.status === "feeding"
                ? "Live"
                : slot.status === "connected"
                  ? "Connected"
                  : "Idle"}
            </Badge>
          )}
        </div>

        {loading ? (
          <Loading label="Loading observer console" />
        ) : teamError ? (
          <ErrorState
            message={teamError}
            onRetry={() => void loadTeam()}
          />
        ) : !team ? (
          <ErrorState message="Your team could not be loaded." />
        ) : (
          <>
            {/* slot status — only this observer's own slot */}
            <Card title="Your observer slot" className="mb-4">
              {slotError ? (
                <ErrorState
                  message={slotError}
                  onRetry={() => void loadSlot()}
                />
              ) : slotMissing || !slot ? (
                <EmptyState message="No observer slot is assigned to your team yet. The control room assigns slots at /control/observers." />
              ) : (
                <dl className="space-y-2 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <dt className="font-mono text-[11px] uppercase tracking-[0.18em] text-zinc-500">
                      Slot
                    </dt>
                    <dd className="font-mono text-base font-bold text-white">
                      {slot.slot_number} / 16
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="font-mono text-[11px] uppercase tracking-[0.18em] text-zinc-500">
                      Status
                    </dt>
                    <dd>
                      <Badge tone={statusTone(slot.status)}>
                        {slot.status}
                      </Badge>
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="font-mono text-[11px] uppercase tracking-[0.18em] text-zinc-500">
                      Last heartbeat
                    </dt>
                    <dd className="font-mono text-xs text-zinc-300">
                      {formatHeartbeat(slot.last_heartbeat)}
                    </dd>
                  </div>
                </dl>
              )}
            </Card>

            {/* zone quick button */}
            <Card title="Quick event" className="mb-4">
              <button
                type="button"
                disabled={zoneBusy}
                onClick={() => void submitZone()}
                className="w-full rounded-xl border border-white/15 bg-white/5 px-2 py-4 font-mono text-sm font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#00f0ff]/50 hover:text-[#00f0ff] disabled:cursor-not-allowed disabled:opacity-40 max-md:min-h-[60px]"
              >
                {zoneBusy ? "Sending…" : "Zone update"}
              </button>
            </Card>

            {/* kill / knock form — killer team locked to own team */}
            <Card title="Log kill / knock" className="mb-4">
              {otherTeamsError ? (
                <ErrorState
                  message={otherTeamsError}
                  onRetry={() => void loadOtherTeams()}
                />
              ) : (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void submitKill();
                  }}
                  className="space-y-4"
                >
                  {/* locked killer team */}
                  <div>
                    <span className={labelClass}>Your team (killer side)</span>
                    <div className="flex items-center gap-2 rounded-xl border border-[#00f0ff]/30 bg-[#00f0ff]/5 px-4 py-3 max-md:min-h-[56px]">
                      <Badge tone="cyan">Locked</Badge>
                      <span className="text-base font-semibold text-white">
                        {team.tag ? `${team.tag} · ` : ""}
                        {team.name}
                      </span>
                    </div>
                  </div>

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
                      <label htmlFor="observer-killer" className={labelClass}>
                        Killer (your team)
                      </label>
                      <input
                        id="observer-killer"
                        value={killer}
                        onChange={(e) => setKiller(e.target.value)}
                        placeholder="Player name"
                        autoComplete="off"
                        className={inputClass}
                      />
                    </div>
                    <div>
                      <label htmlFor="observer-victim" className={labelClass}>
                        Victim
                      </label>
                      <input
                        id="observer-victim"
                        value={victim}
                        onChange={(e) => setVictim(e.target.value)}
                        placeholder="Player name"
                        autoComplete="off"
                        className={inputClass}
                      />
                    </div>
                  </div>

                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <label
                        htmlFor="observer-victim-team"
                        className={labelClass}
                      >
                        Victim team
                      </label>
                      <select
                        id="observer-victim-team"
                        value={victimTeam}
                        onChange={(e) => setVictimTeam(e.target.value)}
                        className={selectClass}
                      >
                        <option value="">Select team</option>
                        {otherTeams.map((t) => (
                          <option key={t.id} value={t.name}>
                            {t.tag ? `${t.tag} · ${t.name}` : t.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label htmlFor="observer-weapon" className={labelClass}>
                        Weapon
                      </label>
                      <select
                        id="observer-weapon"
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
                  </div>

                  <div>
                    <label htmlFor="observer-match" className={labelClass}>
                      Match ID (optional)
                    </label>
                    <input
                      id="observer-match"
                      value={matchId}
                      onChange={(e) => setMatchId(e.target.value)}
                      placeholder="e.g. match-3"
                      autoComplete="off"
                      className={inputClass}
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={submitting}
                    className="w-full rounded-xl bg-[#00f0ff] py-4 font-mono text-base font-bold uppercase tracking-[0.2em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-40 max-md:min-h-[60px]"
                  >
                    {submitting
                      ? "Sending…"
                      : `Submit ${isKnock ? "knock" : "kill"}`}
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
                        <span className="text-sm text-zinc-300">
                          {ev.label}
                        </span>
                      </div>
                      <span className="font-mono text-xs text-zinc-500">
                        {formatTime(ev.at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </>
        )}
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

export default function ObserverPage() {
  return (
    <RequireRole roles={["observer"]}>
      <ObserverInner />
    </RequireRole>
  );
}
