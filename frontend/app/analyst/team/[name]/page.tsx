"use client";

/**
 * AEGIS AI — TEAM DOSSIER (Phase 11, W3).
 *
 * Route: /analyst/team/[name] (operator + admin). Coach-facing dossier:
 * header with playstyle badge, stat grid, strengths / weaknesses, tendencies,
 * head-to-head table, fight history, and a Download Dossier PDF button.
 *
 * Data: GET /api/v1/dossiers/dossier?team_id= (+ fights endpoint for the
 * fight list). Empty responses render the honest coverage note from the
 * API meta — never fabricated.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { apiFetch, ApiError, API_BASE } from "@/lib/api";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, ErrorState, Loading } from "@/components/ui";
import { StatCard } from "../../components/StatCard";
import { AnalystEmptyState } from "../../components/EmptyState";
import {
  unwrapEnvelope,
  coverageNote,
  teamColorOf,  dossierPlaystyle,
  dossierHeadToHead,
  dossierFights,
  fightOutcome,
  fightOpponent,
  h2hName,
  type EnvelopeMeta,
  type TeamLite,
  type DossierData,
  type FightRow,
} from "../../components/types";

function prettyStatKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatStatValue(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
  return v;
}

function formatTime(ts: unknown): string {
  if (typeof ts === "number") {
    const d = new Date(ts > 1e12 ? ts : ts * 1000);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
  }
  if (typeof ts === "string") {
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
  }
  return "—";
}

function FightOutcomeBadge({ outcome }: { outcome: string }) {
  const o = outcome.toLowerCase();
  if (!o) return <Badge tone="slate">unknown</Badge>;
  if (o.includes("win") || o === "w") return <Badge tone="green">won</Badge>;
  if (o.includes("loss") || o === "l") return <Badge tone="red">lost</Badge>;
  return <Badge tone="amber">{outcome}</Badge>;
}

function TeamDossierInner() {
  const params = useParams<{ name: string }>();
  const teamName = decodeURIComponent(params?.name ?? "");
  const { token } = useAuth();

  const [team, setTeam] = useState<TeamLite | null>(null);
  const [teamError, setTeamError] = useState<string | null>(null);
  const [dossier, setDossier] = useState<DossierData | null>(null);
  const [dossierMeta, setDossierMeta] = useState<EnvelopeMeta | null>(null);
  const [dossierError, setDossierError] = useState<string | null>(null);
  const [extraFights, setExtraFights] = useState<FightRow[] | null>(null);
  const [extraFightsMeta, setExtraFightsMeta] = useState<EnvelopeMeta | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setTeamError(null);
    setDossierError(null);

    let found: TeamLite | null = null;
    try {
      const json = await apiFetch<unknown>("/api/v1/teams", {
        token: token ?? undefined,
      });
      const { data } = unwrapEnvelope<TeamLite[]>(json, []);
      const list = Array.isArray(data) ? data : [];
      found =
        list.find((t) => t.name.toLowerCase() === teamName.toLowerCase()) ??
        null;
      setTeam(found);
      if (!found) {
        setTeamError(
          `No team named “${teamName}” in the roster. Pick a team from the analyst dashboard.`
        );
        setLoading(false);
        return;
      }
    } catch (e) {
      setTeamError(
        e instanceof ApiError ? `Roster lookup failed: ${e.body}` : "Roster lookup failed."
      );
      setLoading(false);
      return;
    }

    // Dossier body.
    try {
      const json = await apiFetch<unknown>(
        `/api/v1/dossiers/dossier?team_id=${encodeURIComponent(found.id)}`,
        { token: token ?? undefined }
      );
      const { data, meta } = unwrapEnvelope<DossierData>(json, {});
      setDossier(data && typeof data === "object" ? data : {});
      setDossierMeta(meta);
    } catch (e) {
      setDossier(null);
      setDossierError(
        e instanceof ApiError && e.status === 404
          ? "The dossier endpoint is not available in this build (404)."
          : e instanceof ApiError
            ? `Dossier request failed: ${e.body}`
            : "Dossier request failed."
      );
    }

    // Fight history (best-effort; dossier may already carry it).
    try {
      const json = await apiFetch<unknown>(
        `/api/v1/dossiers/fights?team_id=${encodeURIComponent(found.id)}`,
        { token: token ?? undefined }
      );
      const { data, meta } = unwrapEnvelope<FightRow[]>(json, []);
      setExtraFights(Array.isArray(data) ? data : []);
      setExtraFightsMeta(meta);
    } catch {
      setExtraFights(null);
    }

    setLoading(false);
  }, [teamName, token]);

  useEffect(() => {
    void load();
  }, [load]);

  const fights: FightRow[] = useMemo(() => {
    const fromDossier = dossier ? dossierFights(dossier) : [];
    if (fromDossier.length > 0) return fromDossier;
    return extraFights ?? [];
  }, [dossier, extraFights]);

  const h2h = useMemo(() => (dossier ? dossierHeadToHead(dossier) : []), [dossier]);

  const statsEntries = useMemo(() => {
    const s = dossier?.stats;
    if (!s || typeof s !== "object") return [];
    return Object.entries(s).filter(
      ([, v]) => v !== null && v !== undefined && v !== ""
    );
  }, [dossier]);

  const tendencies = dossier?.tendencies;
  const pdfUrl = team
    ? `${API_BASE}/api/v1/dossiers/report/team/${encodeURIComponent(team.id)}.pdf`
    : null;

  return (
    <div className="min-h-screen bg-[#0a0a0f] p-4 text-white md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <Link
          href="/analyst"
          className="font-mono text-xs uppercase tracking-widest text-cyan-300/70 hover:text-cyan-300"
        >
          ← Analyst dashboard
        </Link>

        {loading ? (
          <Loading label="Loading dossier" />
        ) : teamError || !team ? (
          <ErrorState
            message={teamError ?? "Team not found."}
            onRetry={() => void load()}
          />
        ) : (
          <>
            {/* header */}
            <div className="flex flex-wrap items-center gap-4">
              <span
                className="inline-block h-10 w-10 rounded-xl"
                style={{ backgroundColor: teamColorOf(team) }}
              />
              <div className="min-w-0 flex-1">
                <h1 className="truncate text-2xl font-bold md:text-3xl">
                  {team.name}
                </h1>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  {team.tag ? (
                    <span className="font-mono text-sm text-white/40">
                      {team.tag}
                    </span>
                  ) : null}
                  {dossier && dossierPlaystyle(dossier) ? (
                    <Badge tone="cyan">{dossierPlaystyle(dossier)}</Badge>
                  ) : null}
                  {team.source === "demo" ? (
                    <Badge tone="amber">demo · simulated data</Badge>
                  ) : null}
                </div>
              </div>
              {pdfUrl ? (
                <a
                  href={pdfUrl}
                  download
                  className="rounded-lg bg-cyan-400/90 px-4 py-2.5 text-sm font-semibold text-black hover:bg-cyan-300"
                >
                  Download Dossier PDF
                </a>
              ) : null}
            </div>

            {/* dossier body */}
            {dossierError && !dossier ? (
              <ErrorState message={dossierError} onRetry={() => void load()} />
            ) : !dossier ||
              (statsEntries.length === 0 &&
                h2h.length === 0 &&
                fights.length === 0 &&
                (!dossier.strengths || dossier.strengths.length === 0) &&
                (!dossier.weaknesses || dossier.weaknesses.length === 0)) ? (
              <AnalystEmptyState
                title="No dossier data"
                message={coverageNote(
                  dossierMeta,
                  dossierError ??
                    "The analysis pipeline has not produced a dossier for this team yet."
                )}
              />
            ) : (
              <>
                {dossierMeta ? (
                  <p className="font-mono text-xs text-white/40">
                    coverage: {coverageNote(dossierMeta, "dossier loaded")}
                  </p>
                ) : null}

                {/* stat grid */}
                {statsEntries.length > 0 ? (
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
                    {statsEntries.map(([k, v]) => (
                      <StatCard
                        key={k}
                        label={prettyStatKey(k)}
                        value={formatStatValue(v)}
                      />
                    ))}
                  </div>
                ) : null}

                {/* strengths / weaknesses */}
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <Card title="Strengths">
                    {dossier.strengths && dossier.strengths.length > 0 ? (
                      <ul className="space-y-2">
                        {dossier.strengths.map((s, i) => (
                          <li
                            key={i}
                            className="flex gap-2 text-sm text-white/80"
                          >
                            <span className="text-emerald-400">▲</span>
                            <span>{s}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-white/40">
                        No strengths recorded for this team yet.
                      </p>
                    )}
                  </Card>
                  <Card title="Weaknesses">
                    {dossier.weaknesses && dossier.weaknesses.length > 0 ? (
                      <ul className="space-y-2">
                        {dossier.weaknesses.map((w, i) => (
                          <li
                            key={i}
                            className="flex gap-2 text-sm text-white/80"
                          >
                            <span className="text-rose-400">▼</span>
                            <span>{w}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-white/40">
                        No weaknesses recorded for this team yet.
                      </p>
                    )}
                  </Card>
                </div>

                {/* tendencies */}
                <Card title="Tendencies">
                  {tendencies &&
                  (tendencies.favorite_drops?.length ||
                    tendencies.rotation_timing ||
                    tendencies.edge_vs_center ||
                    tendencies.notes?.length) ? (
                    <dl className="space-y-3 text-sm">
                      {tendencies.favorite_drops &&
                      tendencies.favorite_drops.length > 0 ? (
                        <div>
                          <dt className="font-mono text-xs uppercase tracking-widest text-white/40">
                            Favorite drops
                          </dt>
                          <dd className="mt-1 flex flex-wrap gap-2">
                            {tendencies.favorite_drops.map((d, i) => (
                              <Badge key={i} tone="cyan">
                                {d}
                              </Badge>
                            ))}
                          </dd>
                        </div>
                      ) : null}
                      {tendencies.rotation_timing ? (
                        <div>
                          <dt className="font-mono text-xs uppercase tracking-widest text-white/40">
                            Rotation timing
                          </dt>
                          <dd className="mt-1 text-white/80">
                            {tendencies.rotation_timing}
                          </dd>
                        </div>
                      ) : null}
                      {tendencies.edge_vs_center ? (
                        <div>
                          <dt className="font-mono text-xs uppercase tracking-widest text-white/40">
                            Edge vs center
                          </dt>
                          <dd className="mt-1 text-white/80">
                            {tendencies.edge_vs_center}
                          </dd>
                        </div>
                      ) : null}
                      {tendencies.notes && tendencies.notes.length > 0 ? (
                        <div>
                          <dt className="font-mono text-xs uppercase tracking-widest text-white/40">
                            Notes
                          </dt>
                          <dd className="mt-1 space-y-1 text-white/80">
                            {tendencies.notes.map((n, i) => (
                              <p key={i}>· {n}</p>
                            ))}
                          </dd>
                        </div>
                      ) : null}
                    </dl>
                  ) : (
                    <p className="text-sm text-white/40">
                      No tendency data recorded for this team yet.
                    </p>
                  )}
                </Card>

                {/* head to head */}
                <Card title="Head-to-head">
                  {h2h.length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[480px] text-sm">
                        <thead>
                          <tr className="border-b border-white/10 font-mono text-xs uppercase tracking-widest text-white/40">
                            <th className="py-2 text-left">Opponent</th>
                            <th className="py-2 text-right">Matches</th>
                            <th className="py-2 text-right">Wins</th>
                            <th className="py-2 text-right">Losses</th>
                            <th className="py-2 text-right">Win rate</th>
                          </tr>
                        </thead>
                        <tbody>
                          {h2h.map((r, i) => (
                            <tr
                              key={i}
                              className="border-b border-white/5 last:border-0"
                            >
                              <td className="py-2 font-medium">{h2hName(r)}</td>
                              <td className="py-2 text-right font-mono">
                                {r.matches ?? "—"}
                              </td>
                              <td className="py-2 text-right font-mono text-emerald-300">
                                {r.wins ?? "—"}
                              </td>
                              <td className="py-2 text-right font-mono text-rose-300">
                                {r.losses ?? "—"}
                              </td>
                              <td className="py-2 text-right font-mono">
                                {typeof r.win_rate === "number"
                                  ? `${(r.win_rate <= 1 ? r.win_rate * 100 : r.win_rate).toFixed(0)}%`
                                  : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-white/40">
                      No head-to-head records for this team yet.
                    </p>
                  )}
                </Card>

                {/* fight history */}
                <Card
                  title="Fight history"
                  action={
                    extraFightsMeta ? (
                      <span className="font-mono text-xs text-white/40">
                        {coverageNote(extraFightsMeta, "")}
                      </span>
                    ) : undefined
                  }
                >
                  {fights.length > 0 ? (
                    <ul className="space-y-2">
                      {fights.map((f, i) => (
                        <li
                          key={f.fight_id ?? i}
                          className="flex flex-wrap items-center gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm"
                        >
                          <FightOutcomeBadge outcome={fightOutcome(f)} />
                          <span className="font-medium">
                            vs {fightOpponent(f)}
                          </span>
                          {typeof f.kills === "number" ? (
                            <span className="font-mono text-xs text-white/50">
                              {f.kills} kills
                            </span>
                          ) : null}
                          {typeof f.placement === "number" ? (
                            <span className="font-mono text-xs text-white/50">
                              #{f.placement}
                            </span>
                          ) : null}
                          {f.phase ? (
                            <Badge tone="slate">{f.phase}</Badge>
                          ) : null}
                          <span className="ml-auto font-mono text-xs text-white/40">
                            {f.match_id ? `${f.match_id} · ` : ""}
                            {formatTime(f.timestamp)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <AnalystEmptyState
                      title="No fights"
                      message={coverageNote(
                        extraFightsMeta,
                        "No fights recorded for this team yet."
                      )}
                    />
                  )}
                </Card>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function TeamDossierPage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <TeamDossierInner />
    </RequireRole>
  );
}