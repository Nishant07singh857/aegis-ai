"use client";

/**
 * AEGIS AI — TEAM DOSSIER (Phase 11, W3; contract-aligned in the
 * Integrity Fix Pack, C5b).
 *
 * Route: /analyst/team/[name] (operator + admin). Coach-facing dossier:
 * header with playstyle badge, stat grid, strengths / weaknesses,
 * tendencies, head-to-head table, fight history, and a Download Dossier
 * PDF button.
 *
 * Data: GET /api/v1/dossiers/dossier?team_id= (+ fights endpoint for the
 * fight list). Shapes mirror backend/app/dossiers/schemas.py EXACTLY:
 *   playstyle  — OBJECT { label, evidence, fights_per_match,
 *                aggression_score, matches_sampled } (never a bare string)
 *   strengths / weaknesses — list of StatEdge OBJECTS
 *                { metric, label, team_value, tournament_avg, delta_pct }
 *   tendencies — { drop_locations: [{location, matches}],
 *                drop_location_note, rotation_timing: {…}, centrality: {…} }
 *   head_to_head — [{ opponent_team_id, opponent_name, fights, won, lost,
 *                disengaged, kills_for, kills_against }]
 * Rendering any of those as raw React children crashes the page
 * ("Objects are not valid as a React child") — every section below
 * formats the real fields. Empty responses render the honest coverage
 * note from the API meta — never fabricated.
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
  teamColorOf,
  dossierPlaystyle,
  dossierHeadToHead,
  fightOpponent,
  fightResultForTeam,
  h2hName,
  h2hWinRate,
  type EnvelopeMeta,
  type TeamLite,
  type DossierData,
  type StatEdge,
  type FightRow,
} from "../../components/types";

function formatNumber(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

/** Fight timestamps are match-relative ms in feed data and epoch ms in
 *  some producers — render each honestly instead of forcing a date. */
function formatFightTime(ts: number | undefined): string {
  if (typeof ts !== "number" || !Number.isFinite(ts)) return "—";
  if (ts >= 1e11) return new Date(ts).toLocaleString();
  const totalSec = Math.floor(ts / 1000);
  return `${Math.floor(totalSec / 60)}:${String(totalSec % 60).padStart(2, "0")} match time`;
}

function FightResultBadge({
  result,
  rawOutcome,
}: {
  result: string;
  rawOutcome: string;
}) {
  if (result === "won") return <Badge tone="green">won</Badge>;
  if (result === "lost") return <Badge tone="red">lost</Badge>;
  return <Badge tone="amber">{rawOutcome || "unknown"}</Badge>;
}

/** One strengths/weaknesses row — a StatEdge OBJECT, formatted from its
 *  real fields (label is the human line; the numbers sit underneath). */
function StatEdgeRow({ edge, tone }: { edge: StatEdge; tone: "up" | "down" }) {
  const parts: string[] = [];
  if (edge.metric) parts.push(edge.metric);
  if (typeof edge.team_value === "number" || typeof edge.tournament_avg === "number") {
    parts.push(
      `team ${formatNumber(edge.team_value)} vs avg ${formatNumber(edge.tournament_avg)}`
    );
  }
  if (typeof edge.delta_pct === "number") {
    parts.push(`Δ ${edge.delta_pct > 0 ? "+" : ""}${formatNumber(edge.delta_pct)}%`);
  }
  return (
    <li className="flex gap-2 text-sm text-white/80">
      <span className={tone === "up" ? "text-emerald-400" : "text-rose-400"}>
        {tone === "up" ? "▲" : "▼"}
      </span>
      <span>
        <span>{edge.label}</span>
        {parts.length > 0 ? (
          <span className="mt-0.5 block font-mono text-xs text-white/40">
            {parts.join(" · ")}
          </span>
        ) : null}
      </span>
    </li>
  );
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

    // Fight history (the dossier itself carries no fight list — the
    // fights endpoint is the only source).
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

  const fights: FightRow[] = extraFights ?? [];

  const h2h = useMemo(() => (dossier ? dossierHeadToHead(dossier) : []), [dossier]);

  const playstyle = dossier?.playstyle ?? null;
  const playstyleLabel = dossier ? dossierPlaystyle(dossier) : null;

  const statEntries = useMemo<[string, string][]>(() => {
    if (!dossier) return [];
    const out: [string, string][] = [];
    if (typeof dossier.matches_sampled === "number")
      out.push(["Matches sampled", String(dossier.matches_sampled)]);
    if (typeof dossier.fights_sampled === "number")
      out.push(["Fights sampled", String(dossier.fights_sampled)]);
    if (typeof playstyle?.fights_per_match === "number")
      out.push(["Fights / match", formatNumber(playstyle.fights_per_match)]);
    if (typeof playstyle?.aggression_score === "number")
      out.push(["Aggression score", formatNumber(playstyle.aggression_score)]);
    if (dossier.data_freshness)
      out.push(["Data freshness", dossier.data_freshness.replace(/_/g, " ")]);
    return out;
  }, [dossier, playstyle]);

  const tendencies = dossier?.tendencies;
  const hasTendencies = Boolean(
    tendencies &&
      ((tendencies.drop_locations && tendencies.drop_locations.length > 0) ||
        tendencies.drop_location_note ||
        tendencies.rotation_timing?.label ||
        tendencies.rotation_timing?.note ||
        tendencies.centrality?.label ||
        tendencies.centrality?.note)
  );

  const hasDossierContent = Boolean(
    dossier &&
      (statEntries.length > 0 ||
        h2h.length > 0 ||
        fights.length > 0 ||
        (dossier.strengths && dossier.strengths.length > 0) ||
        (dossier.weaknesses && dossier.weaknesses.length > 0) ||
        (playstyleLabel && playstyleLabel !== "insufficient_data") ||
        hasTendencies ||
        (dossier.notes && dossier.notes.length > 0))
  );

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
                  {playstyleLabel ? (
                    <Badge tone="cyan">{playstyleLabel.replace(/_/g, " ")}</Badge>
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
            ) : !dossier || !hasDossierContent ? (
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
                {dossier.computed_at ? (
                  <p className="font-mono text-xs text-white/40">
                    computed at {dossier.computed_at}
                  </p>
                ) : null}

                {/* stat grid — only fields DossierOut actually carries */}
                {statEntries.length > 0 ? (
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
                    {statEntries.map(([k, v]) => (
                      <StatCard key={k} label={k} value={v} />
                    ))}
                  </div>
                ) : null}

                {/* playstyle — an OBJECT: label + evidence, formatted */}
                {playstyle ? (
                  <Card title="Playstyle">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="cyan">
                        {playstyle.label.replace(/_/g, " ")}
                      </Badge>
                      {typeof playstyle.matches_sampled === "number" ? (
                        <span className="font-mono text-xs text-white/40">
                          {playstyle.matches_sampled} matches sampled
                        </span>
                      ) : null}
                    </div>
                    {playstyle.evidence ? (
                      <p className="mt-2 text-sm text-white/75">
                        {playstyle.evidence}
                      </p>
                    ) : null}
                  </Card>
                ) : null}

                {dossier.comparison_note ? (
                  <p className="text-sm text-white/55">
                    {dossier.comparison_note}
                  </p>
                ) : null}

                {/* strengths / weaknesses — lists of StatEdge objects */}
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <Card title="Strengths">
                    {dossier.strengths && dossier.strengths.length > 0 ? (
                      <ul className="space-y-2">
                        {dossier.strengths.map((s, i) => (
                          <StatEdgeRow key={i} edge={s} tone="up" />
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
                          <StatEdgeRow key={i} edge={w} tone="down" />
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-white/40">
                        No weaknesses recorded for this team yet.
                      </p>
                    )}
                  </Card>
                </div>

                {/* tendencies — drop_locations / rotation_timing / centrality */}
                <Card title="Tendencies">
                  {hasTendencies && tendencies ? (
                    <dl className="space-y-3 text-sm">
                      {tendencies.drop_locations &&
                      tendencies.drop_locations.length > 0 ? (
                        <div>
                          <dt className="font-mono text-xs uppercase tracking-widest text-white/40">
                            Favorite drops
                          </dt>
                          <dd className="mt-1 flex flex-wrap gap-2">
                            {tendencies.drop_locations.map((d, i) => (
                              <Badge key={i} tone="cyan">
                                {String(d.location ?? "unknown")}
                                {typeof d.matches === "number"
                                  ? ` · ${d.matches}×`
                                  : ""}
                              </Badge>
                            ))}
                          </dd>
                          {tendencies.drop_location_note ? (
                            <p className="mt-1 text-xs text-white/40">
                              {tendencies.drop_location_note}
                            </p>
                          ) : null}
                        </div>
                      ) : tendencies.drop_location_note ? (
                        <div>
                          <dt className="font-mono text-xs uppercase tracking-widest text-white/40">
                            Favorite drops
                          </dt>
                          <dd className="mt-1 text-white/60">
                            {tendencies.drop_location_note}
                          </dd>
                        </div>
                      ) : null}
                      {tendencies.rotation_timing &&
                      (tendencies.rotation_timing.label ||
                        tendencies.rotation_timing.note) ? (
                        <div>
                          <dt className="font-mono text-xs uppercase tracking-widest text-white/40">
                            Rotation timing
                          </dt>
                          <dd className="mt-1 text-white/80">
                            {tendencies.rotation_timing.label ?? ""}
                            {typeof tendencies.rotation_timing.early ===
                              "number" ||
                            typeof tendencies.rotation_timing.mid ===
                              "number" ||
                            typeof tendencies.rotation_timing.late ===
                              "number" ? (
                              <span className="ml-2 font-mono text-xs text-white/50">
                                early {tendencies.rotation_timing.early ?? 0}{" "}
                                · mid {tendencies.rotation_timing.mid ?? 0} ·
                                late {tendencies.rotation_timing.late ?? 0}
                              </span>
                            ) : null}
                            {tendencies.rotation_timing.note ? (
                              <span className="mt-0.5 block text-xs text-white/40">
                                {tendencies.rotation_timing.note}
                              </span>
                            ) : null}
                          </dd>
                        </div>
                      ) : null}
                      {tendencies.centrality &&
                      (tendencies.centrality.label ||
                        tendencies.centrality.note) ? (
                        <div>
                          <dt className="font-mono text-xs uppercase tracking-widest text-white/40">
                            Centrality (edge vs center)
                          </dt>
                          <dd className="mt-1 text-white/80">
                            {tendencies.centrality.label ?? ""}
                            {typeof tendencies.centrality
                              .distance_from_tournament_centroid ===
                            "number" ? (
                              <span className="ml-2 font-mono text-xs text-white/50">
                                centroid distance{" "}
                                {formatNumber(
                                  tendencies.centrality
                                    .distance_from_tournament_centroid
                                )}
                                {typeof tendencies.centrality
                                  .median_team_distance === "number"
                                  ? ` · median team ${formatNumber(tendencies.centrality.median_team_distance)}`
                                  : ""}
                              </span>
                            ) : null}
                            {tendencies.centrality.note ? (
                              <span className="mt-0.5 block text-xs text-white/40">
                                {tendencies.centrality.note}
                              </span>
                            ) : null}
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

                {/* dossier notes */}
                {dossier.notes && dossier.notes.length > 0 ? (
                  <Card title="Notes">
                    <ul className="space-y-1 text-sm text-white/70">
                      {dossier.notes.map((n, i) => (
                        <li key={i}>· {n}</li>
                      ))}
                    </ul>
                  </Card>
                ) : null}

                {/* head to head — HeadToHeadOut fields only; the backend
                    sends no "matches"/"win_rate": fights is the match
                    count and win rate is derived from decided fights. */}
                <Card title="Head-to-head">
                  {h2h.length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[560px] text-sm">
                        <thead>
                          <tr className="border-b border-white/10 font-mono text-xs uppercase tracking-widest text-white/40">
                            <th className="py-2 text-left">Opponent</th>
                            <th className="py-2 text-right">Fights</th>
                            <th className="py-2 text-right">Won</th>
                            <th className="py-2 text-right">Lost</th>
                            <th className="py-2 text-right">Disengaged</th>
                            <th className="py-2 text-right">Kills for</th>
                            <th className="py-2 text-right">Kills against</th>
                            <th className="py-2 text-right">Win rate (decided)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {h2h.map((r, i) => {
                            const rate = h2hWinRate(r);
                            return (
                              <tr
                                key={r.opponent_team_id ?? i}
                                className="border-b border-white/5 last:border-0"
                              >
                                <td className="py-2 font-medium">{h2hName(r)}</td>
                                <td className="py-2 text-right font-mono">
                                  {r.fights ?? "—"}
                                </td>
                                <td className="py-2 text-right font-mono text-emerald-300">
                                  {r.won ?? "—"}
                                </td>
                                <td className="py-2 text-right font-mono text-rose-300">
                                  {r.lost ?? "—"}
                                </td>
                                <td className="py-2 text-right font-mono">
                                  {r.disengaged ?? "—"}
                                </td>
                                <td className="py-2 text-right font-mono">
                                  {r.kills_for ?? "—"}
                                </td>
                                <td className="py-2 text-right font-mono">
                                  {r.kills_against ?? "—"}
                                </td>
                                <td className="py-2 text-right font-mono">
                                  {rate !== null
                                    ? `${(rate * 100).toFixed(0)}%`
                                    : "—"}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-white/40">
                      No head-to-head records for this team yet.
                    </p>
                  )}
                </Card>

                {/* fight history — FightDetailOut rows */}
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
                          key={f.id ?? i}
                          className="flex flex-wrap items-center gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm"
                        >
                          <FightResultBadge
                            result={fightResultForTeam(f, team.id)}
                            rawOutcome={f.outcome ?? ""}
                          />
                          <span className="font-medium">
                            vs {fightOpponent(f, team.id)}
                          </span>
                          {typeof f.total_kills === "number" ? (
                            <span className="font-mono text-xs text-white/50">
                              {f.total_kills} kills
                            </span>
                          ) : null}
                          {typeof f.total_knocks === "number" ? (
                            <span className="font-mono text-xs text-white/50">
                              {f.total_knocks} knocks
                            </span>
                          ) : null}
                          {f.map_name ? (
                            <Badge tone="slate">{f.map_name}</Badge>
                          ) : null}
                          {f.third_parties && f.third_parties.length > 0 ? (
                            <Badge tone="amber">
                              3rd party:{" "}
                              {f.third_parties
                                .map((t) => t.team_name)
                                .join(", ")}
                            </Badge>
                          ) : null}
                          <span className="ml-auto font-mono text-xs text-white/40">
                            {f.match_id ? `${f.match_id} · ` : ""}
                            {formatFightTime(f.start_timestamp_ms)}
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
