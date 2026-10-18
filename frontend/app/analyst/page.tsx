"use client";

/**
 * AEGIS AI — ANALYST DASHBOARD (Phase 11, W3).
 *
 * Route: /analyst (operator + admin). The product a paying coach sees:
 * pick a team to open its dossier, or enter a match id to open the match
 * timeline. Overview cards report what the analysis backend actually has —
 * every empty state carries the honest coverage note from the API envelope.
 *
 * No mock data: team list comes from GET /api/v1/teams; coverage cards probe
 * the analysis/dossier endpoints live.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { apiFetch, ApiError } from "@/lib/api";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, ErrorState, Loading } from "@/components/ui";
import { StatCard } from "./components/StatCard";
import { AnalystEmptyState } from "./components/EmptyState";
import {
  unwrapEnvelope,
  coverageNote,
  type EnvelopeMeta,
  type TeamLite,
} from "./components/types";

interface EndpointProbe {
  path: string;
  label: string;
  ok: boolean;
  count: number | null;
  coverage: string;
}

function probeErrorMessage(path: string, e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404)
      return `Not available yet — ${path} returned 404. The analysis backend has not shipped this endpoint in this build.`;
    if (e.status === 401 || e.status === 403)
      return `Not available — ${path} returned ${e.status} (check your role / sign in again).`;
    return `Probe failed — ${path} returned ${e.status}: ${e.body || "no detail"}.`;
  }
  return `Probe failed — ${path}: could not reach the backend.`;
}

function SourceBadge({ source }: { source: TeamLite["source"] }) {
  return source === "db" ? (
    <Badge tone="green">database</Badge>
  ) : source === "demo" ? (
    <Badge tone="amber">demo · simulated data</Badge>
  ) : null;
}

function AnalystHomeInner() {
  const router = useRouter();
  const { token } = useAuth();
  const [teams, setTeams] = useState<TeamLite[] | null>(null);
  const [teamsError, setTeamsError] = useState<string | null>(null);
  const [teamsMeta, setTeamsMeta] = useState<EnvelopeMeta | null>(null);
  const [probes, setProbes] = useState<EndpointProbe[] | null>(null);
  const [query, setQuery] = useState("");
  const [matchId, setMatchId] = useState("");

  const probeOne = useCallback(
    async (
      path: string,
      label: string
    ): Promise<EndpointProbe> => {
      try {
        const json = await apiFetch<unknown>(path, {
          token: token ?? undefined,
        });
        const { data, meta } = unwrapEnvelope<unknown>(json, null);
        const count = Array.isArray(data)
          ? data.length
          : data === null || data === undefined
            ? 0
            : 1;
        return {
          path,
          label,
          ok: true,
          count,
          coverage: coverageNote(
            meta,
            `${label}: endpoint responded with ${count} record${count === 1 ? "" : "s"}.`
          ),
        };
      } catch (e) {
        return { path, label, ok: false, count: null, coverage: probeErrorMessage(path, e) };
      }
    },
    [token]
  );

  const load = useCallback(async () => {
    setTeamsError(null);
    try {
      const json = await apiFetch<unknown>("/api/v1/teams", {
        token: token ?? undefined,
      });
      const { data, meta } = unwrapEnvelope<TeamLite[]>(json, []);
      setTeams(Array.isArray(data) ? data : []);
      setTeamsMeta(meta);
    } catch (e) {
      setTeamsError(
        e instanceof ApiError ? `Teams list failed: ${e.body}` : "Teams list failed."
      );
      setTeams(null);
    }

    const results = await Promise.all([
      probeOne("/api/v1/analysis/rotations", "Rotations"),
      probeOne("/api/v1/analysis/drops", "Drops"),
      probeOne("/api/v1/analysis/zones", "Zones"),
      probeOne("/api/v1/dossiers/fights", "Fights"),
    ]);
    setProbes(results);
  }, [probeOne, token]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !teams) return teams;
    return teams.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        (t.tag ?? "").toLowerCase().includes(q)
    );
  }, [teams, query]);

  const demoCount = teams?.filter((t) => t.source === "demo").length ?? 0;

  return (
    <div className="min-h-screen bg-[#0a0a0f] p-4 text-white md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        {/* header */}
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="font-mono text-xs uppercase tracking-widest text-cyan-300/70">
              AEGIS AI · Coach intel
            </div>
            <h1 className="mt-1 text-2xl font-bold md:text-3xl">
              Analyst Dashboard
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-white/50">
              Dossiers and match timelines built from the analysis pipeline —
              every number on this page comes from the backend, or the page
              says so.
            </p>
          </div>
          <Badge tone="cyan">operator / admin</Badge>
        </div>

        {/* overview cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Teams tracked"
            value={teams === null ? "—" : String(teams.length)}
            sub={
              teamsError
                ? teamsError
                : demoCount > 0
                  ? `${demoCount} marked demo · simulated data`
                  : coverageNote(teamsMeta, "Live team roster.")
            }
          />
          <StatCard
            label="Rotations ingested"
            value={
              probes === null
                ? "—"
                : String(probes[0].count ?? "—")
            }
            sub={probes === null ? "probing…" : probes[0].coverage}
          />
          <StatCard
            label="Zones ingested"
            value={probes === null ? "—" : String(probes[2].count ?? "—")}
            sub={probes === null ? "probing…" : probes[2].coverage}
          />
          <StatCard
            label="Fights recorded"
            value={probes === null ? "—" : String(probes[3].count ?? "—")}
            sub={probes === null ? "probing…" : probes[3].coverage}
          />
        </div>

        {/* data coverage detail */}
        <Card
          title="Data coverage"
          action={
            <button
              onClick={() => void load()}
              className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white/70 hover:border-cyan-300/50 hover:text-white"
            >
              Refresh
            </button>
          }
        >
          {probes === null ? (
            <Loading label="Probing analysis endpoints" />
          ) : (
            <ul className="space-y-2">
              {probes.map((p) => (
                <li
                  key={p.path}
                  className="flex flex-wrap items-center gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2"
                >
                  <Badge tone={p.ok ? "green" : "slate"}>{p.label}</Badge>
                  <code className="font-mono text-xs text-white/40">{p.path}</code>
                  <span className="w-full text-xs text-white/55 md:w-auto md:flex-1">
                    {p.coverage}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* team picker */}
          <Card title="Team dossier">
            {teams === null ? (
              teamsError ? (
                <ErrorState message={teamsError} onRetry={() => void load()} />
              ) : (
                <Loading label="Loading teams" />
              )
            ) : teams.length === 0 ? (
              <AnalystEmptyState
                title="No teams"
                message="The team roster is empty. Add teams first (Teams page) — dossiers are built per team from the roster."
              />
            ) : (
              <>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search teams…"
                  className="mb-3 w-full rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-sm text-white placeholder:text-white/30 focus:border-cyan-300/60 focus:outline-none"
                />
                <ul className="max-h-80 space-y-1 overflow-y-auto pr-1">
                  {(filtered ?? []).map((t) => (
                    <li key={t.id}>
                      <button
                        onClick={() =>
                          router.push(`/analyst/team/${encodeURIComponent(t.name)}`)
                        }
                        className="flex w-full items-center gap-3 rounded-lg border border-transparent px-3 py-2 text-left hover:border-white/15 hover:bg-white/5"
                      >
                        <span
                          className="inline-block h-3 w-3 shrink-0 rounded-full"
                          style={{
                            backgroundColor:
                              t.color ||
                              (t.colors && t.colors.primary) ||
                              "#334155",
                          }}
                        />
                        <span className="min-w-0 flex-1 truncate text-sm font-medium">
                          {t.name}
                        </span>
                        {t.tag ? (
                          <span className="font-mono text-xs text-white/40">
                            {t.tag}
                          </span>
                        ) : null}
                        <SourceBadge source={t.source} />
                        <span className="text-white/30">→</span>
                      </button>
                    </li>
                  ))}
                </ul>
                {filtered !== null && filtered.length === 0 && (
                  <p className="py-4 text-center text-sm text-white/40">
                    No teams match “{query}”.
                  </p>
                )}
              </>
            )}
          </Card>

          {/* match picker + quick links */}
          <div className="space-y-4">
            <Card title="Match timeline">
              <p className="text-sm text-white/50">
                Enter a match id to open its rotation map, zone phases, fight
                pins and per-team summary.
              </p>
              <form
                className="mt-3 flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  const id = matchId.trim();
                  if (id) router.push(`/analyst/match/${encodeURIComponent(id)}`);
                }}
              >
                <input
                  value={matchId}
                  onChange={(e) => setMatchId(e.target.value)}
                  placeholder="match id, e.g. bmsd-2026-d1-m3"
                  className="min-w-0 flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-2 font-mono text-sm text-white placeholder:text-white/30 focus:border-cyan-300/60 focus:outline-none"
                />
                <button
                  type="submit"
                  className="shrink-0 rounded-lg bg-cyan-400/90 px-4 py-2 text-sm font-semibold text-black hover:bg-cyan-300"
                >
                  Open
                </button>
              </form>
            </Card>

            <Card title="Quick links">
              <ul className="space-y-2 text-sm">
                <li>
                  <Link
                    href="/teams"
                    className="text-cyan-300 hover:underline"
                  >
                    Team roster →
                  </Link>
                  <span className="text-white/40"> — manage tracked teams</span>
                </li>
                <li>
                  <Link
                    href="/control"
                    className="text-cyan-300 hover:underline"
                  >
                    Control room →
                  </Link>
                  <span className="text-white/40">
                    {" "}— pipeline health behind the analysis data
                  </span>
                </li>
              </ul>
              <p className="mt-3 border-t border-white/10 pt-3 text-xs text-white/40">
                Dossier and match-report PDFs are offered as downloads on the
                team and match pages, served by the backend report endpoints.
              </p>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AnalystHomePage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <AnalystHomeInner />
    </RequireRole>
  );
}
