"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Badge, Card, EmptyState, ErrorState, Loading } from "../components/ui";

interface SystemInfo {
  status?: string;
  health?: Record<string, number | string>;
  modes?: Record<string, boolean>;
  event_bus?: string;
}

interface Agent {
  id: string;
  name: string;
  status?: string;
  cpu?: string;
  mem?: string;
  latency?: string;
  tasks?: number;
}

interface Device {
  id: string;
  name: string;
  type?: string;
  load?: number;
  status?: string;
  task?: string;
  assigned_agent?: string;
}

interface AgentLog {
  id?: string;
  timestamp?: string;
  from?: string;
  to?: string;
  message?: string;
  level?: string;
}

interface ApiErrorLike {
  status?: number;
  body?: unknown;
}

function apiErrorInfo(e: unknown): { status?: number; message: string } {
  const err = e as ApiErrorLike;
  const body = err?.body;
  let message = "Request failed";
  if (body && typeof body === "object") {
    const b = body as { message?: unknown; detail?: unknown };
    if (typeof b.message === "string" && b.message) message = b.message;
    else if (typeof b.detail === "string" && b.detail) message = b.detail;
  }
  if (typeof err?.status === "number") return { status: err.status, message: `(${err.status}) ${message}` };
  return { message };
}

function prettyKey(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (!Number.isNaN(d.getTime())) return d.toLocaleTimeString();
  return ts;
}

function statusTone(status: string | undefined): "cyan" | "red" | "amber" | "green" | "slate" {
  const s = (status ?? "").toLowerCase();
  if (s === "active" || s === "online" || s === "connected") return "green";
  if (s === "degraded" || s === "warning") return "amber";
  if (s === "error" || s === "offline" || s === "disconnected") return "red";
  return "slate";
}

function levelTone(level: string | undefined): "cyan" | "red" | "amber" | "green" | "slate" {
  const l = (level ?? "").toLowerCase();
  if (l === "error" || l === "critical") return "red";
  if (l === "warn" || l === "warning") return "amber";
  if (l === "info") return "cyan";
  return "slate";
}

function useAdminFetch<T>(path: string, token: string | null, sessionExpired: () => void) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<{ status?: number; message: string } | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await apiFetch<T>(path, { token: token ?? undefined });
      const payload = (res as { data?: unknown }).data;
      setData((payload ?? res) as T);
    } catch (e) {
      const info = apiErrorInfo(e);
      if (info.status === 401) {
        sessionExpired();
        setError({ status: 401, message: "Session expired — log in again." });
      } else {
        setError(info);
      }
      setData(null);
    }
  }, [path, token, sessionExpired]);

  return { data, error, load };
}

export default function AdminPage() {
  const router = useRouter();
  const { token, isAuthed, logout } = useAuth();
  const [expiredNote, setExpiredNote] = useState(false);

  const handleExpired = useCallback(() => {
    setExpiredNote(true);
    logout();
  }, [logout]);

  useEffect(() => {
    if (!isAuthed) router.replace("/login");
  }, [isAuthed, router]);

  const system = useAdminFetch<SystemInfo>("/api/v1/admin/system", token, handleExpired);
  const agents = useAdminFetch<{ data: Agent[] } | Agent[]>("/api/v1/admin/agents", token, handleExpired);
  const devices = useAdminFetch<{ data: Device[] } | Device[]>("/api/v1/devices", token, handleExpired);
  const logs = useAdminFetch<{ data: AgentLog[] } | AgentLog[]>("/api/v1/agent-logs", token, handleExpired);

  useEffect(() => {
    if (!isAuthed) return;
    void system.load();
    void agents.load();
    void devices.load();
    void logs.load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthed, token]);

  if (!isAuthed) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-2 bg-[#0a0a0f] text-white">
        <p className="text-sm text-white/50">Sending you to the login page…</p>
        {expiredNote && (
          <p className="mt-2 text-sm text-amber-400">Your session expired — log in again.</p>
        )}
      </div>
    );
  }

  const agentRows: Agent[] = Array.isArray(agents.data)
    ? agents.data
    : (agents.data?.data ?? []);
  const deviceRows: Device[] = Array.isArray(devices.data)
    ? devices.data
    : (devices.data?.data ?? []);
  const logRows: AgentLog[] = Array.isArray(logs.data) ? logs.data : (logs.data?.data ?? []);

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">Admin</h1>
            <p className="text-sm text-white/50">System, agents, devices and inter-agent logs</p>
          </div>
          <Link href="/" className="text-sm text-white/60 hover:text-[#00f0ff]">
            ← Dashboard
          </Link>
        </div>

        <Card title="System">
          {!system.data && !system.error && <Loading label="Loading system…" />}
          {system.error && (
            <ErrorState message={system.error.message} onRetry={() => void system.load()} />
          )}
          {system.data && (
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="green">{system.data.status ?? "unknown"}</Badge>
                <span className="text-sm text-white/50">event bus:</span>
                <Badge tone={statusTone(system.data.event_bus)}>
                  {system.data.event_bus ?? "unknown"}
                </Badge>
              </div>
              {system.data.health && (
                <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                  {Object.entries(system.data.health).map(([k, v]) => (
                    <div key={k} className="rounded-lg bg-white/5 p-3">
                      <div className="text-xs text-white/50">{prettyKey(k)}</div>
                      <div className="text-lg font-semibold text-[#00f0ff]">{String(v)}</div>
                    </div>
                  ))}
                </div>
              )}
              {system.data.modes && (
                <div className="mt-4">
                  <div className="mb-2 text-xs text-white/50">Modes</div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(system.data.modes).map(([k, v]) => (
                      <Badge key={k} tone={v ? "green" : "slate"}>
                        {prettyKey(k)}: {v ? "ON" : "OFF"}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>

        <Card title="Agents">
          {!agents.data && !agents.error && <Loading label="Loading agents…" />}
          {agents.error && <ErrorState message={agents.error.message} onRetry={() => void agents.load()} />}
          {agents.data && agentRows.length === 0 && <EmptyState message="No agents reported." />}
          {agents.data && agentRows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-xs text-white/50">
                    <th className="pb-2 pr-4 font-medium">Name</th>
                    <th className="pb-2 pr-4 font-medium">Status</th>
                    <th className="pb-2 pr-4 font-medium">CPU</th>
                    <th className="pb-2 pr-4 font-medium">Mem</th>
                    <th className="pb-2 pr-4 font-medium">Latency</th>
                    <th className="pb-2 font-medium">Tasks</th>
                  </tr>
                </thead>
                <tbody>
                  {agentRows.map((a) => (
                    <tr key={a.id} className="border-b border-white/5">
                      <td className="py-2 pr-4 font-medium">{a.name}</td>
                      <td className="py-2 pr-4">
                        <Badge tone={statusTone(a.status)}>{a.status ?? "—"}</Badge>
                      </td>
                      <td className="py-2 pr-4 text-white/70">{a.cpu ?? "—"}</td>
                      <td className="py-2 pr-4 text-white/70">{a.mem ?? "—"}</td>
                      <td className="py-2 pr-4 text-white/70">{a.latency ?? "—"}</td>
                      <td className="py-2 text-[#00f0ff]">{a.tasks ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Devices">
          {!devices.data && !devices.error && <Loading label="Loading devices…" />}
          {devices.error && (
            <ErrorState message={devices.error.message} onRetry={() => void devices.load()} />
          )}
          {devices.data && deviceRows.length === 0 && <EmptyState message="No devices reported." />}
          {devices.data && deviceRows.length > 0 && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {deviceRows.map((d) => (
                <div key={d.id} className="rounded-lg bg-white/5 p-4">
                  <div className="flex items-center justify-between">
                    <div className="font-semibold">{d.name}</div>
                    <Badge tone={statusTone(d.status)}>{d.status ?? "—"}</Badge>
                  </div>
                  <div className="mt-1 text-xs text-white/50">{d.type ?? "—"}</div>
                  <div className="mt-3">
                    <div className="mb-1 flex justify-between text-xs text-white/50">
                      <span>Load</span>
                      <span>{typeof d.load === "number" ? `${d.load}%` : "—"}</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-white/10">
                      <div
                        className="h-full rounded-full bg-[#00f0ff]"
                        style={{ width: `${typeof d.load === "number" ? Math.min(100, Math.max(0, d.load)) : 0}%` }}
                      />
                    </div>
                  </div>
                  <div className="mt-3 text-sm text-white/70">{d.task ?? "—"}</div>
                  <div className="mt-1 text-xs text-white/40">
                    Agent: {d.assigned_agent ?? "—"}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Agent logs">
          {!logs.data && !logs.error && <Loading label="Loading agent logs…" />}
          {logs.error && <ErrorState message={logs.error.message} onRetry={() => void logs.load()} />}
          {logs.data && logRows.length === 0 && <EmptyState message="No agent logs yet." />}
          {logs.data && logRows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-xs text-white/50">
                    <th className="pb-2 pr-4 font-medium">Time</th>
                    <th className="pb-2 pr-4 font-medium">Route</th>
                    <th className="pb-2 pr-4 font-medium">Message</th>
                    <th className="pb-2 font-medium">Level</th>
                  </tr>
                </thead>
                <tbody>
                  {logRows.map((l, i) => (
                    <tr key={l.id ?? i} className="border-b border-white/5 align-top">
                      <td className="whitespace-nowrap py-2 pr-4 text-xs text-white/40">
                        {formatTime(l.timestamp)}
                      </td>
                      <td className="whitespace-nowrap py-2 pr-4 text-white/70">
                        {l.from ?? "—"} → {l.to ?? "—"}
                      </td>
                      <td className="py-2 pr-4 text-white/85">{l.message ?? "—"}</td>
                      <td className="py-2">
                        <Badge tone={levelTone(l.level)}>{l.level ?? "—"}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
