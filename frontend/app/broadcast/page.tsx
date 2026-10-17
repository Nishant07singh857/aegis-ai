"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Badge, Card, EmptyState, ErrorState, Loading } from "../components/ui";

interface Team {
  id: string;
  name: string;
  tag?: string | null;
}

interface DirectorState {
  current_pov?: string | null;
  target_team?: string | null;
  action?: string | null;
  reason?: unknown;
  [k: string]: unknown;
}

interface PostResult {
  status?: string;
  message?: string;
}

interface ApiErrorLike {
  status?: number;
  body?: unknown;
}

function apiErrorMessage(e: unknown): string {
  const err = e as ApiErrorLike;
  const body = err?.body;
  if (body && typeof body === "object") {
    const msg = (body as { message?: unknown; detail?: unknown }).message;
    if (typeof msg === "string" && msg) return `(${err.status}) ${msg}`;
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return `(${err.status}) ${detail}`;
  }
  if (typeof err?.status === "number") return `Request failed with status ${err.status}`;
  return "Request failed";
}

function prettyKey(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function prettyReason(reason: unknown): string {
  if (reason == null) return "—";
  if (typeof reason === "string") return reason;
  try {
    return JSON.stringify(reason);
  } catch {
    return String(reason);
  }
}

export default function BroadcastPage() {
  const { token, isAuthed } = useAuth();
  const [director, setDirector] = useState<DirectorState | null>(null);
  const [directorError, setDirectorError] = useState<string | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [selectedTeam, setSelectedTeam] = useState("");
  const [switchResult, setSwitchResult] = useState<PostResult | null>(null);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [switchBusy, setSwitchBusy] = useState(false);
  const [replayResult, setReplayResult] = useState<PostResult | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [modes, setModes] = useState<Record<string, boolean> | null>(null);
  const [modesError, setModesError] = useState<string | null>(null);
  const [modeBusy, setModeBusy] = useState<string | null>(null);

  const loadDirector = useCallback(async () => {
    setDirectorError(null);
    try {
      const res = await apiFetch<{ data: DirectorState }>("/api/v1/broadcast/status", {
        token: token ?? undefined,
      });
      setDirector(res.data ?? null);
    } catch (e) {
      setDirector(null);
      setDirectorError(apiErrorMessage(e));
    }
  }, [token]);

  const loadModes = useCallback(async () => {
    setModesError(null);
    try {
      const res = await apiFetch<{ modes?: Record<string, boolean> }>("/api/v1/system/status", {
        token: token ?? undefined,
      });
      setModes(res.modes ?? null);
    } catch (e) {
      setModes(null);
      setModesError(apiErrorMessage(e));
    }
  }, [token]);

  const loadTeams = useCallback(async () => {
    try {
      const res = await apiFetch<{ data: Team[] }>("/api/v1/teams", { token: token ?? undefined });
      setTeams(res.data ?? []);
    } catch {
      setTeams([]);
    }
  }, [token]);

  useEffect(() => {
    void loadDirector();
    void loadModes();
    void loadTeams();
  }, [loadDirector, loadModes, loadTeams]);

  async function handleSwitch() {
    if (!selectedTeam) {
      setSwitchError("Pick a team first.");
      setSwitchResult(null);
      return;
    }
    setSwitchBusy(true);
    setSwitchError(null);
    setSwitchResult(null);
    try {
      const res = await apiFetch<PostResult>("/api/v1/broadcast/switch", {
        method: "POST",
        token: token ?? undefined,
        body: { team_id: selectedTeam },
      });
      setSwitchResult(res);
      await loadDirector();
    } catch (e) {
      setSwitchError(apiErrorMessage(e));
    } finally {
      setSwitchBusy(false);
    }
  }

  async function handleReplay() {
    setReplayBusy(true);
    setReplayError(null);
    setReplayResult(null);
    try {
      const res = await apiFetch<PostResult>("/api/v1/broadcast/replay", {
        method: "POST",
        token: token ?? undefined,
      });
      setReplayResult(res);
    } catch (e) {
      setReplayError(apiErrorMessage(e));
    } finally {
      setReplayBusy(false);
    }
  }

  async function handleToggleMode(key: string, value: boolean) {
    setModeBusy(key);
    setModesError(null);
    try {
      const res = await apiFetch<{ status?: string; modes?: Record<string, boolean> }>(
        "/api/v1/broadcast/production-mode",
        { method: "POST", token: token ?? undefined, body: { [key]: value } }
      );
      if (res.modes) setModes(res.modes);
    } catch (e) {
      setModesError(apiErrorMessage(e));
    } finally {
      setModeBusy(null);
    }
  }

  const pov = director?.current_pov ?? director?.target_team ?? "—";

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto max-w-5xl px-4 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">Broadcast Control</h1>
            <p className="text-sm text-white/50">Director POV, replays and production modes</p>
          </div>
          <Link href="/" className="text-sm text-white/60 hover:text-[#00f0ff]">
            ← Dashboard
          </Link>
        </div>

        {!isAuthed && (
          <Card title="Operator login required">
            <p className="text-sm text-white/70">
              Switching cameras, triggering replays and changing production modes are operator
              actions. Log in to control the broadcast.
            </p>
            <Link
              href="/login"
              className="mt-4 inline-block rounded-lg bg-[#00f0ff] px-4 py-2 text-sm font-semibold text-black"
            >
              Go to login
            </Link>
          </Card>
        )}

        <Card title="Director state">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Current director state</h2>
            <button
              onClick={() => void loadDirector()}
              className="rounded-lg border border-white/10 bg-white/5 px-3 py-1 text-sm text-white/70 hover:text-white"
            >
              Refresh
            </button>
          </div>
          <div className="mt-3">
            {!director && !directorError && <Loading label="Loading director state…" />}
            {directorError && <ErrorState message={directorError} onRetry={() => void loadDirector()} />}
            {director && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div className="rounded-lg bg-white/5 p-4">
                  <div className="text-xs text-white/50">Point of view</div>
                  <div className="mt-1 text-xl font-bold text-[#00f0ff]">{pov}</div>
                </div>
                <div className="rounded-lg bg-white/5 p-4">
                  <div className="text-xs text-white/50">Action</div>
                  <div className="mt-1 text-xl font-bold">{director.action ?? "—"}</div>
                </div>
                <div className="rounded-lg bg-white/5 p-4">
                  <div className="text-xs text-white/50">Reason</div>
                  <div className="mt-1 break-words text-sm text-white/80">{prettyReason(director.reason)}</div>
                </div>
              </div>
            )}
          </div>
        </Card>

        {isAuthed && (
          <>
            <Card title="Switch POV">
              <div className="flex flex-col gap-3 md:flex-row md:items-end">
                <div className="flex-1">
                  <label htmlFor="pov-team" className="mb-1 block text-xs text-white/50">
                    Team
                  </label>
                  <select
                    id="pov-team"
                    value={selectedTeam}
                    onChange={(e) => setSelectedTeam(e.target.value)}
                    className="w-full rounded-lg border border-white/10 bg-[#14141c] px-3 py-2 text-sm text-white focus:border-[#00f0ff] focus:outline-none"
                  >
                    <option value="">Select a team…</option>
                    {teams.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                        {t.tag ? ` (${t.tag})` : ""}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  onClick={handleSwitch}
                  disabled={switchBusy || !selectedTeam}
                  className="rounded-lg bg-[#00f0ff] px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
                >
                  {switchBusy ? "Switching…" : "Switch POV"}
                </button>
              </div>
              {switchResult && (
                <p className="mt-3 text-sm text-emerald-400">
                  {switchResult.status}: {switchResult.message ?? ""}
                </p>
              )}
              {switchError && <p className="mt-3 text-sm text-[#ff3366]">{switchError}</p>}
            </Card>

            <Card title="Replay">
              <button
                onClick={handleReplay}
                disabled={replayBusy}
                className="rounded-lg bg-[#00f0ff] px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
              >
                {replayBusy ? "Triggering…" : "Trigger replay"}
              </button>
              {replayResult && (
                <p className="mt-3 text-sm text-emerald-400">
                  {replayResult.status}: {replayResult.message ?? ""}
                </p>
              )}
              {replayError && <p className="mt-3 text-sm text-[#ff3366]">{replayError}</p>}
            </Card>

            <Card title="Production modes">
              {!modes && !modesError && <Loading label="Loading modes…" />}
              {modesError && <ErrorState message={modesError} onRetry={() => void loadModes()} />}
              {modes && Object.keys(modes).length === 0 && <EmptyState message="No production modes reported by the server." />}
              {modes && Object.keys(modes).length > 0 && (
                <div className="flex flex-col gap-3">
                  {Object.entries(modes).map(([key, value]) => (
                    <div
                      key={key}
                      className="flex items-center justify-between rounded-lg bg-white/5 px-4 py-3"
                    >
                      <span className="text-sm font-medium">{prettyKey(key)}</span>
                      <button
                        onClick={() => void handleToggleMode(key, !value)}
                        disabled={modeBusy === key}
                        aria-pressed={value}
                        className={`relative h-6 w-11 rounded-full transition ${
                          value ? "bg-[#00f0ff]" : "bg-white/15"
                        } disabled:opacity-50`}
                      >
                        <span
                          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition ${
                            value ? "left-[22px]" : "left-0.5"
                          }`}
                        />
                      </button>
                    </div>
                  ))}
                  {modeBusy && <p className="text-xs text-white/40">Updating mode…</p>}
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(modes).map(([key, value]) => (
                      <Badge key={key} tone={value ? "green" : "slate"}>
                        {prettyKey(key)}: {value ? "ON" : "OFF"}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
