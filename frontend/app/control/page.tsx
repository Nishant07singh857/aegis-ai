"use client";

/**
 * AEGIS AI — CONTROL ROOM (Phase 6).
 *
 * Route: /control (operator + admin). The processing section: agent status,
 * pipeline / event-bus health and recent errors/logs — everything the
 * broadcast output never shows.
 *
 * INTERNAL — not on stream: this page carries nav chrome and live internals
 * by design, and is never referenced from any capture-safe page.
 *
 * Data: live WS (/ws/live) via useLiveSocket — agents_state, system_health,
 * modes_state and agent_logs frames. No mock data: every row comes from a
 * real WS frame; empty feeds render honest empty states.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { apiFetch } from "@/lib/api";
import { useLiveSocket } from "@/lib/useLiveSocket";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, EmptyState } from "@/components/ui";
import { StreamControl } from "@/components/stream-control";
import ConnectionBanner from "@/components/live/ConnectionBanner";

function statusTone(
  status: string | undefined
): "cyan" | "red" | "amber" | "green" | "slate" {
  const s = (status ?? "").toLowerCase();
  if (s === "active" || s === "online" || s === "connected" || s === "running" || s === "ok")
    return "green";
  if (s === "degraded" || s === "warning" || s === "starting" || s === "stopping")
    return "amber";
  if (s === "error" || s === "offline" || s === "disconnected" || s === "failed")
    return "red";
  return "slate";
}

function levelTone(
  level: string | undefined
): "cyan" | "red" | "amber" | "green" | "slate" {
  const l = (level ?? "").toLowerCase();
  if (l === "error" || l === "critical") return "red";
  if (l === "warn" || l === "warning") return "amber";
  if (l === "info") return "cyan";
  return "slate";
}

function formatTime(ts: unknown): string {
  if (typeof ts === "number") {
    const d = new Date(ts > 1e12 ? ts : ts * 1000);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString();
  }
  if (typeof ts === "string") {
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
  }
  return "—";
}

function prettyKey(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

interface AgentRow {
  id: string;
  name: string;
  status?: string;
  cpu?: string;
  mem?: string;
  latency?: string;
  tasks?: number;
}

interface LogRow {
  id?: string;
  timestamp?: unknown;
  from?: string;
  to?: string;
  message?: string;
  level?: string;
}

/** One row of GET /api/v1/cluster/nodes (Phase 7 / W3). */
interface ClusterNode {
  node_id: string;
  hostname: string;
  roles: string[];
  started_at: string;
  version: string;
  heartbeat_age_s: number;
  status: string;
  agent_count: number;
  standalone: boolean;
}

function heartbeatDotClass(status: string | undefined): string {
  const s = (status ?? "").toLowerCase();
  if (s === "healthy") return "bg-emerald-400";
  if (s === "degraded") return "bg-amber-400";
  return "bg-red-400";
}

function shortId(nodeId: string): string {
  return nodeId.length > 10 ? `${nodeId.slice(0, 10)}…` : nodeId;
}

/**
 * Cluster card — "Cluster — sare nodes connected rahe".
 *
 * Polls GET /api/v1/cluster/nodes every 15s (same cadence as the backend
 * heartbeat). Lists every connected node with role badges, agent count,
 * heartbeat age and a health dot (green <30s, amber 30–45s, red stale /
 * unreachable). Data is 100% live from the backend registry — no mock
 * nodes; when Redis is down the backend honestly reports a single
 * `standalone` entry for this process instead of inventing peers.
 */
function ClusterCard({ token }: { token: string | null }) {
  const [nodes, setNodes] = useState<ClusterNode[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await apiFetch<ClusterNode[]>("/api/v1/cluster/nodes", {
          token,
        });
        if (!cancelled) {
          setNodes(Array.isArray(data) ? data : []);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setNodes(null);
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    };
    load();
    const timer = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [token]);

  return (
    <Card title="Cluster — sare nodes connected rahe">
      {error && (
        <EmptyState
          message={`Cluster API unreachable: ${error} — retrying every 15s.`}
        />
      )}
      {!error && nodes === null && (
        <EmptyState message="Loading cluster nodes…" />
      )}
      {!error && nodes !== null && nodes.length === 0 && (
        <EmptyState message="No nodes registered — the registry heartbeat may not have run yet." />
      )}
      {!error && nodes !== null && nodes.length > 0 && (
        <ul className="space-y-2">
          {nodes.map((n) => (
            <li
              key={n.node_id}
              className="flex flex-wrap items-center gap-3 rounded-lg bg-white/5 px-3 py-2.5"
            >
              <span
                className={`h-2.5 w-2.5 shrink-0 rounded-full ${heartbeatDotClass(
                  n.status
                )}`}
                title={`status: ${n.status ?? "unknown"}`}
              />
              <div className="min-w-0">
                <div className="truncate font-mono text-sm font-medium">
                  {shortId(n.node_id)}
                  <span className="ml-2 font-sans text-white/50">
                    {n.hostname}
                  </span>
                </div>
                <div className="text-xs text-white/40">
                  {n.agent_count} agents ·{" "}
                  {typeof n.heartbeat_age_s === "number"
                    ? `${Math.round(n.heartbeat_age_s)}s ago`
                    : "—"}{" "}
                  · v{n.version}
                  {n.standalone && " · standalone (Redis unreachable)"}
                </div>
              </div>
              <div className="ml-auto flex flex-wrap items-center gap-1.5">
                {(Array.isArray(n.roles) ? n.roles : []).map((r) => (
                  <Badge key={r} tone="cyan">
                    {r}
                  </Badge>
                ))}
                {n.standalone && <Badge tone="amber">standalone</Badge>}
                <Badge tone={statusTone(n.status)}>{n.status ?? "—"}</Badge>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ControlRoomInner() {
  const { token, logout } = useAuth();
  const { status, reconnect, retryCount, agents, health, modes, agentLogs } =
    useLiveSocket();

  const handleExpired = useCallback(() => {
    // A 401 from the stream card means the JWT died; the guard will
    // redirect to /login once auth state clears.
    logout();
  }, [logout]);

  const agentRows = useMemo<AgentRow[]>(
    () =>
      (Array.isArray(agents) ? agents : []).map((a, i) => ({
        id: String(a?.id ?? a?.name ?? i),
        name: String(a?.name ?? a?.id ?? `agent-${i}`),
        status: typeof a?.status === "string" ? a.status : undefined,
        cpu: typeof a?.cpu === "string" ? a.cpu : undefined,
        mem: typeof a?.mem === "string" ? a.mem : undefined,
        latency: typeof a?.latency === "string" ? a.latency : undefined,
        tasks: typeof a?.tasks === "number" ? a.tasks : undefined,
      })),
    [agents]
  );

  const logRows = useMemo<LogRow[]>(
    () => (Array.isArray(agentLogs) ? agentLogs : []).slice(0, 50),
    [agentLogs]
  );

  const errorRows = useMemo<LogRow[]>(
    () =>
      logRows.filter((l) => {
        const lvl = (l.level ?? "").toLowerCase();
        return lvl === "error" || lvl === "critical";
      }),
    [logRows]
  );

  const healthEntries = useMemo<[string, string][]>(() => {
    if (!health || typeof health !== "object" || Array.isArray(health))
      return [];
    return Object.entries(health as Record<string, unknown>).map(([k, v]) => [
      k,
      typeof v === "string" || typeof v === "number" ? String(v) : "—",
    ]);
  }, [health]);

  const modeEntries = useMemo<[string, boolean][]>(() => {
    if (!modes || typeof modes !== "object") return [];
    return Object.entries(modes as Record<string, unknown>).map(([k, v]) => [
      k,
      v === true,
    ]);
  }, [modes]);

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <ConnectionBanner
        status={status}
        retryCount={retryCount}
        onReconnect={reconnect}
      />
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8">
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">Control Room</h1>
            <p className="text-sm text-white/50">
              Processing section — agents, pipeline and logs
            </p>
          </div>
          <Badge tone="amber">INTERNAL — NOT ON STREAM</Badge>
        </div>
        <nav className="flex flex-wrap gap-2 text-sm" aria-label="Control pages">
          <Link
            href="/control/match-strip"
            className="rounded border border-cyan-800 bg-cyan-950/40 px-3 py-1.5 text-cyan-200 hover:border-cyan-500"
          >
            Match strip →
          </Link>
          <Link
            href="/control/rundown"
            className="rounded border border-zinc-700 px-3 py-1.5 text-zinc-300 hover:border-cyan-500"
          >
            Rundown →
          </Link>
          <Link
            href="/control/observers"
            className="rounded border border-zinc-700 px-3 py-1.5 text-zinc-300 hover:border-cyan-500"
          >
            Observers →
          </Link>
        </nav>

        <StreamControl token={token} onSessionExpired={handleExpired} />

        <ClusterCard token={token} />

        <Card title="Agents">
          {agentRows.length === 0 && (
            <EmptyState message="No agent frames received yet — waiting on the live socket." />
          )}
          {agentRows.length > 0 && (
            <>
              {/* desktop: full table */}
              <div className="hidden overflow-x-auto md:block">
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
                          <Badge tone={statusTone(a.status)}>
                            {a.status ?? "—"}
                          </Badge>
                        </td>
                        <td className="py-2 pr-4 text-white/70">{a.cpu ?? "—"}</td>
                        <td className="py-2 pr-4 text-white/70">{a.mem ?? "—"}</td>
                        <td className="py-2 pr-4 text-white/70">
                          {a.latency ?? "—"}
                        </td>
                        <td className="py-2 text-[#00f0ff]">{a.tasks ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* phones: stacked cards, one per agent */}
              <div className="grid gap-3 md:hidden">
                {agentRows.map((a) => (
                  <div
                    key={a.id}
                    className="rounded-lg border border-white/10 bg-black/30 px-4 py-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-white/90">
                        {a.name}
                      </span>
                      <Badge tone={statusTone(a.status)}>
                        {a.status ?? "—"}
                      </Badge>
                    </div>
                    <dl className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
                      <div className="flex items-baseline justify-between gap-2">
                        <dt className="text-white/40">CPU</dt>
                        <dd className="text-white/75">{a.cpu ?? "—"}</dd>
                      </div>
                      <div className="flex items-baseline justify-between gap-2">
                        <dt className="text-white/40">Mem</dt>
                        <dd className="text-white/75">{a.mem ?? "—"}</dd>
                      </div>
                      <div className="flex items-baseline justify-between gap-2">
                        <dt className="text-white/40">Latency</dt>
                        <dd className="text-white/75">{a.latency ?? "—"}</dd>
                      </div>
                      <div className="flex items-baseline justify-between gap-2">
                        <dt className="text-white/40">Tasks</dt>
                        <dd className="text-[#00f0ff]">{a.tasks ?? "—"}</dd>
                      </div>
                    </dl>
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Pipeline / event bus health">
            {healthEntries.length === 0 && modeEntries.length === 0 && (
              <EmptyState message="No health frames received yet — waiting on the live socket." />
            )}
            {healthEntries.length > 0 && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {healthEntries.map(([k, v]) => (
                  <div key={k} className="rounded-lg bg-white/5 p-3">
                    <div className="text-xs text-white/50">{prettyKey(k)}</div>
                    <div className="text-lg font-semibold text-[#00f0ff]">
                      {v}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {modeEntries.length > 0 && (
              <div className="mt-4">
                <div className="mb-2 text-xs text-white/50">Modes</div>
                <div className="flex flex-wrap gap-2">
                  {modeEntries.map(([k, v]) => (
                    <Badge key={k} tone={v ? "green" : "slate"}>
                      {prettyKey(k)}: {v ? "ON" : "OFF"}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </Card>

          <Card title="Recent errors">
            {errorRows.length === 0 && (
              <EmptyState message="No errors in the recent agent logs." />
            )}
            {errorRows.length > 0 && (
              <ul className="space-y-2">
                {errorRows.slice(0, 10).map((l, i) => (
                  <li
                    key={l.id ?? i}
                    className="rounded-lg border border-[#ff3366]/20 bg-[#ff3366]/5 px-3 py-2"
                  >
                    <div className="flex items-center gap-2 text-xs">
                      <Badge tone={levelTone(l.level)}>
                        {l.level ?? "error"}
                      </Badge>
                      <span className="text-white/40">
                        {formatTime(l.timestamp)}
                      </span>
                      <span className="text-white/60">
                        {l.from ?? "—"} → {l.to ?? "—"}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-zinc-300">
                      {l.message ?? "—"}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <Card title="Agent logs">
          {logRows.length === 0 && (
            <EmptyState message="No agent logs received yet — waiting on the live socket." />
          )}
          {logRows.length > 0 && (
            <>
              {/* desktop: full table */}
              <div className="hidden overflow-x-auto md:block">
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
                      <tr
                        key={l.id ?? i}
                        className="border-b border-white/5 align-top"
                      >
                        <td className="whitespace-nowrap py-2 pr-4 text-xs text-white/40">
                          {formatTime(l.timestamp)}
                        </td>
                        <td className="whitespace-nowrap py-2 pr-4 text-white/70">
                          {l.from ?? "—"} → {l.to ?? "—"}
                        </td>
                        <td className="py-2 pr-4 text-white/85">
                          {l.message ?? "—"}
                        </td>
                        <td className="py-2">
                          <Badge tone={levelTone(l.level)}>
                            {l.level ?? "—"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* phones: stacked cards, one per log line */}
              <div className="space-y-2 md:hidden">
                {logRows.map((l, i) => (
                  <div
                    key={l.id ?? i}
                    className="rounded-lg border border-white/10 bg-black/30 px-3 py-2.5"
                  >
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <Badge tone={levelTone(l.level)}>
                        {l.level ?? "—"}
                      </Badge>
                      <span className="text-white/40">
                        {formatTime(l.timestamp)}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-white/60">
                        {l.from ?? "—"} → {l.to ?? "—"}
                      </span>
                    </div>
                    <p className="mt-1 break-words text-sm text-white/85">
                      {l.message ?? "—"}
                    </p>
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function ControlRoomPage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <ControlRoomInner />
    </RequireRole>
  );
}
