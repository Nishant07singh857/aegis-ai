"use client";

/**
 * AEGIS AI — ANTI-CHEAT REVIEW (Phase 22).
 *
 * Route: /control/anti-cheat (admin + director only — the API enforces
 * the same gate server-side).
 *
 * Lists the flags raised by the VISION-DERIVABLE anti-cheat checks
 * (impossible position jumps, kill-feed rate anomalies, alive-count
 * inconsistencies) with the full evidence payload each check fired on,
 * and Confirm / Dismiss actions for human review. A flag is a review
 * item, never an automatic ban.
 *
 * The checks panel is equally honest: checks that need aim/movement
 * telemetry a spectator feed cannot produce are listed as DISABLED
 * with the reason, straight from GET /api/v1/anti-cheat/checks.
 *
 * No mock data: every row comes from GET /api/v1/anti-cheat/flags;
 * an empty list renders an honest empty state.
 *
 * INTERNAL — not on stream.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, EmptyState } from "@/components/ui";

interface AntiCheatFlag {
  id: string;
  match_id: string | null;
  check_name: string;
  cheat_type: string;
  player_id: string | null;
  player_name: string | null;
  team_id: string | null;
  team_name: string | null;
  confidence: string;
  confidence_score: number;
  description: string;
  evidence: Record<string, unknown>;
  status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  detected_at: string;
}

interface CheckInfo {
  check: string;
  cheat_type: string;
  status: string;
  reason: string;
}

interface ChecksResponse {
  live: CheckInfo[];
  disabled: CheckInfo[];
}

const FLAGS_PATH = "/api/v1/anti-cheat/flags";
const CHECKS_PATH = "/api/v1/anti-cheat/checks";

function confidenceTone(
  confidence: string
): "red" | "amber" | "cyan" | "slate" {
  if (confidence === "certain" || confidence === "high") return "red";
  if (confidence === "moderate") return "amber";
  if (confidence === "low" || confidence === "suspicious") return "cyan";
  return "slate";
}

function statusTone(status: string): "amber" | "red" | "green" | "slate" {
  if (status === "pending") return "amber";
  if (status === "confirmed") return "red";
  if (status === "dismissed") return "green";
  return "slate";
}

function AntiCheatReview() {
  const { token } = useAuth();
  const [flags, setFlags] = useState<AntiCheatFlag[] | null>(null);
  const [checks, setChecks] = useState<ChecksResponse | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("pending");
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const query = statusFilter ? `?status=${statusFilter}` : "";
      const [flagRows, checkRows] = await Promise.all([
        apiFetch<AntiCheatFlag[]>(`${FLAGS_PATH}${query}`, { token }),
        apiFetch<ChecksResponse>(CHECKS_PATH, { token }),
      ]);
      setFlags(flagRows);
      setChecks(checkRows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load flags.");
    }
  }, [token, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const review = useCallback(
    async (flag: AntiCheatFlag, action: "confirm" | "dismiss") => {
      if (!token) return;
      setBusyId(flag.id);
      try {
        const updated = await apiFetch<AntiCheatFlag>(
          `${FLAGS_PATH}/${flag.id}/review`,
          { token, method: "POST", body: { action } }
        );
        setFlags((rows) =>
          (rows ?? []).map((r) => (r.id === updated.id ? updated : r))
        );
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Review failed.");
      } finally {
        setBusyId(null);
      }
    },
    [token]
  );

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8">
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">
              Anti-Cheat Review
            </h1>
            <p className="text-sm text-white/50">
              Vision-derivable checks only — every flag is a human review
              item, never an automatic ban.
            </p>
          </div>
          <Badge tone="amber">INTERNAL — NOT ON STREAM</Badge>
        </div>

        <nav className="flex flex-wrap gap-2 text-sm" aria-label="Control pages">
          <Link
            href="/control"
            className="rounded border border-zinc-700 px-3 py-1.5 text-zinc-300 hover:border-cyan-500"
          >
            ← Control room
          </Link>
        </nav>

        {error && (
          <div className="rounded border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}

        <Card title="Flagged incidents">
          <div className="mb-4 flex flex-wrap gap-2 text-sm">
            {["pending", "confirmed", "dismissed", ""].map((value) => (
              <button
                key={value || "all"}
                onClick={() => setStatusFilter(value)}
                className={`rounded border px-3 py-1.5 ${
                  statusFilter === value
                    ? "border-cyan-500 bg-cyan-950/40 text-cyan-200"
                    : "border-zinc-700 text-zinc-300 hover:border-cyan-500"
                }`}
              >
                {value === "" ? "All" : value}
              </button>
            ))}
          </div>

          {flags === null && (
            <EmptyState message="Loading flags…" />
          )}
          {flags !== null && flags.length === 0 && (
            <EmptyState
              message={
                statusFilter
                  ? `No ${statusFilter} flags. The vision checks raise a flag only on real observations — an empty list is the honest state.`
                  : "No anti-cheat flags recorded yet."
              }
            />
          )}
          {flags !== null && flags.length > 0 && (
            <ul className="space-y-3">
              {flags.map((flag) => (
                <li
                  key={flag.id}
                  className="rounded border border-zinc-800 bg-zinc-950/40 p-4"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={confidenceTone(flag.confidence)}>
                      {flag.confidence} · {flag.confidence_score.toFixed(0)}
                    </Badge>
                    <Badge tone={statusTone(flag.status)}>{flag.status}</Badge>
                    <span className="font-mono text-sm text-cyan-200">
                      {flag.check_name}
                    </span>
                    <span className="text-sm text-white/60">
                      ({flag.cheat_type})
                    </span>
                    <span className="ml-auto text-xs text-white/40">
                      {new Date(flag.detected_at).toLocaleString()}
                    </span>
                  </div>

                  <p className="mt-2 text-sm text-white/85">
                    {flag.description}
                  </p>
                  <p className="mt-1 text-xs text-white/50">
                    {flag.team_name ?? flag.team_id ?? "Unknown team"}
                    {flag.player_name || flag.player_id
                      ? ` · ${flag.player_name ?? flag.player_id}`
                      : ""}
                    {flag.match_id ? ` · match ${flag.match_id}` : ""}
                    {flag.reviewed_by
                      ? ` · reviewed by ${flag.reviewed_by}`
                      : ""}
                  </p>

                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      onClick={() =>
                        setExpandedId(expandedId === flag.id ? null : flag.id)
                      }
                      className="rounded border border-zinc-700 px-3 py-1.5 text-sm text-zinc-200 hover:border-cyan-500"
                    >
                      {expandedId === flag.id ? "Hide evidence" : "Evidence"}
                    </button>
                    {flag.status === "pending" && (
                      <>
                        <button
                          disabled={busyId === flag.id}
                          onClick={() => void review(flag, "confirm")}
                          className="rounded border border-red-700 bg-red-950/40 px-3 py-1.5 text-sm text-red-200 hover:border-red-400 disabled:opacity-50"
                        >
                          Confirm
                        </button>
                        <button
                          disabled={busyId === flag.id}
                          onClick={() => void review(flag, "dismiss")}
                          className="rounded border border-green-800 bg-green-950/30 px-3 py-1.5 text-sm text-green-200 hover:border-green-500 disabled:opacity-50"
                        >
                          Dismiss
                        </button>
                      </>
                    )}
                  </div>

                  {expandedId === flag.id && (
                    <pre className="mt-3 overflow-x-auto rounded bg-black/60 p-3 text-xs text-zinc-300">
                      {JSON.stringify(flag.evidence, null, 2)}
                    </pre>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Checks — live vs disabled">
          {checks === null && <EmptyState message="Loading check registry…" />}
          {checks !== null && (
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold text-green-300">
                  LIVE on vision data ({checks.live.length})
                </h3>
                <ul className="space-y-2">
                  {checks.live.map((c) => (
                    <li
                      key={c.check}
                      className="rounded border border-green-900/60 bg-green-950/20 p-3 text-sm"
                    >
                      <span className="font-mono text-green-200">{c.check}</span>
                      <span className="text-white/50"> ({c.cheat_type})</span>
                      <p className="mt-1 text-xs text-white/60">{c.reason}</p>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold text-zinc-300">
                  DISABLED — telemetry not available ({checks.disabled.length})
                </h3>
                <ul className="space-y-2">
                  {checks.disabled.map((c) => (
                    <li
                      key={c.check}
                      className="rounded border border-zinc-800 bg-zinc-950/40 p-3 text-sm"
                    >
                      <span className="font-mono text-zinc-300">{c.check}</span>
                      <span className="text-white/50"> ({c.cheat_type})</span>
                      <p className="mt-1 text-xs text-white/60">{c.reason}</p>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function AntiCheatPage() {
  return (
    <RequireRole roles={["admin", "director"]}>
      <AntiCheatReview />
    </RequireRole>
  );
}
