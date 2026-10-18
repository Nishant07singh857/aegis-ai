"use client";

/**
 * AEGIS AI — MATCH TIMELINE (Phase 11, W3).
 *
 * Route: /analyst/match/[id] (operator + admin). Coach-facing match report:
 * schematic SVG map with per-team rotation paths, drop markers, zone-phase
 * circles and fight pins; zone phase list; per-team summary; fight list;
 * and a Download Match Report PDF button.
 *
 * Data (all live, all optional):
 *   GET /api/v1/analysis/rotations?match_id=
 *   GET /api/v1/analysis/drops?match_id=
 *   GET /api/v1/analysis/zones?match_id=
 *   GET /api/v1/dossiers/fights?match_id=
 * Anything missing renders the honest coverage note from the API meta —
 * no data is ever invented.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { apiFetch, ApiError, API_BASE } from "@/lib/api";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, Loading } from "@/components/ui";
import { AnalystEmptyState } from "../../components/EmptyState";
import { MapSchematic } from "../../components/MapSchematic";
import type {
  SchematicRotationTeam,
  SchematicFight,
  SchematicZone,
  SchematicDrop,
} from "../../components/MapSchematic";
import {
  unwrapEnvelope,
  coverageNote,
  colorFromId,
  normCoord,
  pointX,
  pointY,
  rotationTeamId,
  rotationTeamName,
  rotationOrder,
  zoneRadius,
  fightOutcome,
  fightOpponent,
  type EnvelopeMeta,
  type RotationPoint,
  type ZonePhase,
  type DropPoint,
  type FightRow,
} from "../../components/types";

interface EndpointState<T> {
  ok: boolean;
  data: T;
  meta: EnvelopeMeta | null;
  error: string | null;
}

function emptyEndpoint<T>(fallback: T): EndpointState<T> {
  return { ok: false, data: fallback, meta: null, error: null };
}

interface TeamSummary {
  id: string;
  name: string;
  color: string;
  waypoints: number;
  fights: number;
  won: number;
  lost: number;
  drop: string | null;
}

function MatchTimelineInner() {
  const params = useParams<{ id: string }>();
  const matchId = decodeURIComponent(params?.id ?? "");
  const { token } = useAuth();

  const [loading, setLoading] = useState(true);
  const [rotations, setRotations] =
    useState<EndpointState<RotationPoint[]>>(emptyEndpoint([]));
  const [drops, setDrops] =
    useState<EndpointState<DropPoint[]>>(emptyEndpoint([]));
  const [zones, setZones] =
    useState<EndpointState<ZonePhase[]>>(emptyEndpoint([]));
  const [fights, setFights] =
    useState<EndpointState<FightRow[]>>(emptyEndpoint([]));
  const [selectedTeam, setSelectedTeam] = useState<string | null>(null);

  const loadEndpoint = useCallback(
    async <T,>(path: string, fallback: T): Promise<EndpointState<T>> => {
      try {
        const json = await apiFetch<unknown>(path, {
          token: token ?? undefined,
        });
        const { data, meta } = unwrapEnvelope<T>(json, fallback);
        return { ok: true, data, meta, error: null };
      } catch (e) {
        const msg =
          e instanceof ApiError && e.status === 404
            ? `${path} is not available in this build (404).`
            : e instanceof ApiError
              ? `${path} failed: ${e.body}`
              : `${path} failed: unreachable.`;
        return { ok: false, data: fallback, meta: null, error: msg };
      }
    },
    [token]
  );

  const load = useCallback(async () => {
    setLoading(true);
    const q = `match_id=${encodeURIComponent(matchId)}`;
    const [r, d, z, f] = await Promise.all([
      loadEndpoint<RotationPoint[]>(`/api/v1/analysis/rotations?${q}`, []),
      loadEndpoint<DropPoint[]>(`/api/v1/analysis/drops?${q}`, []),
      loadEndpoint<ZonePhase[]>(`/api/v1/analysis/zones?${q}`, []),
      loadEndpoint<FightRow[]>(`/api/v1/dossiers/fights?${q}`, []),
    ]);
    setRotations(r);
    setDrops(d);
    setZones(z);
    setFights(f);
    setLoading(false);
  }, [matchId, loadEndpoint]);

  useEffect(() => {
    void load();
  }, [load]);

  // ---- transforms ---------------------------------------------------------

  const rotationTeams: SchematicRotationTeam[] = useMemo(() => {
    const byId = new Map<string, SchematicRotationTeam>();
    const ordered = [...rotations.data].sort(
      (a, b) => rotationOrder(a, 0) - rotationOrder(b, 0)
    );
    ordered.forEach((p, i) => {
      const id = rotationTeamId(p, i);
      const x = pointX(p);
      const y = pointY(p);
      if (x === null || y === null) return;
      let team = byId.get(id);
      if (!team) {
        team = {
          teamId: id,
          teamName: rotationTeamName(p, id),
          color: p.color || colorFromId(id),
          points: [],
        };
        byId.set(id, team);
      }
      team.points.push({ x, y, phase: p.phase });
    });
    return [...byId.values()];
  }, [rotations.data]);

  const schematicZones: SchematicZone[] = useMemo(
    () =>
      zones.data.flatMap((z, i) => {
        const x = pointX({ location_x: z.circle_center_x ?? z.cx ?? z.x });
        const y = pointY({ location_y: z.circle_center_y ?? z.cy ?? z.y });
        const raw = zoneRadius(z);
        if (x === null || y === null || raw === null) return [];
        // Clamp to the schematic: a radius larger than the map is a
        // different unit (meters) — treat it as full-map.
        const r = Math.min(480, normCoord(raw) ?? 480);
        return [
          {
            x,
            y,
            r,
            phase: String(z.phase ?? z.stage ?? `P${i + 1}`),
          },
        ];
      }),
    [zones.data]
  );

  const schematicFights: (SchematicFight & { teamId: string | null })[] =
    useMemo(() => {
      const out: (SchematicFight & { teamId: string | null })[] = [];
      fights.data.forEach((f) => {
        const x = pointX(f);
        const y = pointY(f);
        if (x === null || y === null) return;
        const outcome = fightOutcome(f);
        const rawName =
          (f as { team_name?: unknown }).team_name ??
          (f as { team?: unknown }).team ??
          null;
        const teamNameStr =
          typeof rawName === "string" && rawName ? rawName : null;
        const teamId =
          typeof f.team_id === "string" && f.team_id ? f.team_id : teamNameStr;
        const label = `${teamNameStr ? `${teamNameStr} vs ` : ""}${fightOpponent(f)}${outcome ? ` · ${outcome}` : ""}`;
        out.push({ x, y, label, outcome, teamId });
      });
      return out;
    }, [fights.data]);

  const schematicDrops: (SchematicDrop & { teamId: string | null })[] =
    useMemo(
      () =>
        drops.data.flatMap((d) => {
          const x = pointX(d);
          const y = pointY(d);
          if (x === null || y === null) return [];
          const teamId =
            typeof d.team_id === "string" && d.team_id
              ? d.team_id
              : typeof d.team_name === "string" && d.team_name
                ? d.team_name
                : null;
          const label = String(
            d.label ?? d.team_name ?? d.team ?? teamId ?? "drop"
          );
          return [
            {
              x,
              y,
              label,
              color: d.color || (teamId ? colorFromId(teamId) : "#00f0ff"),
              teamId,
            },
          ];
        }),
      [drops.data]
    );

  const summaries: TeamSummary[] = useMemo(() => {
    const map = new Map<string, TeamSummary>();
    const ensure = (id: string, name: string, color: string): TeamSummary => {
      let s = map.get(id);
      if (!s) {
        s = {
          id,
          name,
          color,
          waypoints: 0,
          fights: 0,
          won: 0,
          lost: 0,
          drop: null,
        };
        map.set(id, s);
      }
      return s;
    };
    rotationTeams.forEach((t) => {
      const s = ensure(t.teamId, t.teamName, t.color);
      s.waypoints = t.points.length;
    });
    schematicDrops.forEach((d) => {
      if (!d.teamId) return;
      const s = ensure(d.teamId, d.label, d.color);
      if (!s.drop) s.drop = d.label;
    });
    schematicFights.forEach((f) => {
      if (!f.teamId) return;
      const s = ensure(f.teamId, f.teamId, colorFromId(f.teamId));
      s.fights += 1;
      const o = (f.outcome ?? "").toLowerCase();
      if (o.includes("win")) s.won += 1;
      else if (o.includes("loss")) s.lost += 1;
    });
    return [...map.values()].sort((a, b) => b.waypoints - a.waypoints);
  }, [rotationTeams, schematicDrops, schematicFights]);

  const visibleRotations = selectedTeam
    ? rotationTeams.filter((t) => t.teamId === selectedTeam)
    : rotationTeams;

  const visibleFights = selectedTeam
    ? schematicFights.filter((f) => f.teamId === selectedTeam)
    : schematicFights;

  const visibleDrops = selectedTeam
    ? schematicDrops.filter((d) => d.teamId === selectedTeam)
    : schematicDrops;

  const coverageLines = [
    rotations,
    drops,
    zones,
    fights,
  ].map((e) =>
    e.ok
      ? coverageNote(e.meta, "loaded")
      : (e.error ?? "no data")
  );

  const hasAnyData =
    rotationTeams.length > 0 ||
    schematicZones.length > 0 ||
    schematicFights.length > 0 ||
    schematicDrops.length > 0;

  const pdfUrl = `${API_BASE}/api/v1/dossiers/report/match/${encodeURIComponent(matchId)}.pdf`;

  return (
    <div className="min-h-screen bg-[#0a0a0f] p-4 text-white md:p-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <Link
          href="/analyst"
          className="font-mono text-xs uppercase tracking-widest text-cyan-300/70 hover:text-cyan-300"
        >
          ← Analyst dashboard
        </Link>

        {/* header */}
        <div className="flex flex-wrap items-center gap-4">
          <div className="min-w-0 flex-1">
            <div className="font-mono text-xs uppercase tracking-widest text-cyan-300/70">
              Match timeline
            </div>
            <h1 className="mt-1 break-all font-mono text-xl font-bold md:text-2xl">
              {matchId}
            </h1>
          </div>
          <a
            href={pdfUrl}
            download
            className="rounded-lg bg-cyan-400/90 px-4 py-2.5 text-sm font-semibold text-black hover:bg-cyan-300"
          >
            Download Match Report PDF
          </a>
        </div>

        {loading ? (
          <Loading label="Loading match analysis" />
        ) : (
          <>
            {/* coverage banner */}
            <Card title="Data coverage">
              <ul className="space-y-1 text-xs text-white/55">
                {["Rotations", "Drops", "Zones", "Fights"].map((label, i) => (
                  <li key={label} className="flex gap-2">
                    <Badge tone={coverageLines[i].includes("404") || coverageLines[i].includes("failed") ? "slate" : "green"}>
                      {label}
                    </Badge>
                    <span>{coverageLines[i]}</span>
                  </li>
                ))}
              </ul>
            </Card>

            {!hasAnyData ? (
              <AnalystEmptyState
                title="No match data"
                message="The analysis pipeline has no rotations, zones, drops or fights for this match id yet. Check the coverage notes above — the endpoints may still be shipping in this build."
              />
            ) : (
              <>
                {/* team filter */}
                {summaries.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={() => setSelectedTeam(null)}
                      className={`rounded-full border px-3 py-1.5 font-mono text-xs ${
                        selectedTeam === null
                          ? "border-cyan-300 bg-cyan-300/15 text-cyan-200"
                          : "border-white/15 text-white/60 hover:border-white/30"
                      }`}
                    >
                      All teams
                    </button>
                    {summaries.map((s) => (
                      <button
                        key={s.id}
                        onClick={() =>
                          setSelectedTeam(selectedTeam === s.id ? null : s.id)
                        }
                        className={`flex items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-xs ${
                          selectedTeam === s.id
                            ? "border-cyan-300 bg-cyan-300/15 text-cyan-200"
                            : "border-white/15 text-white/60 hover:border-white/30"
                        }`}
                      >
                        <span
                          className="inline-block h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: s.color }}
                        />
                        {s.name}
                      </button>
                    ))}
                  </div>
                ) : null}

                <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_360px]">
                  {/* map */}
                  <div className="xl:sticky xl:top-4 xl:self-start">
                    <MapSchematic
                      rotations={visibleRotations}
                      fights={visibleFights}
                      zones={schematicZones}
                      drops={visibleDrops}
                      selectedTeamId={selectedTeam}
                    />
                    {selectedTeam ? (
                      <p className="mt-2 text-center font-mono text-xs text-white/40">
                        Showing {summaries.find((s) => s.id === selectedTeam)?.name ?? selectedTeam} — other teams dimmed
                      </p>
                    ) : null}
                  </div>

                  {/* side column */}
                  <div className="space-y-4">
                    <Card title="Zone phases">
                      {schematicZones.length > 0 ? (
                        <ol className="space-y-2">
                          {schematicZones.map((z, i) => (
                            <li
                              key={i}
                              className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm"
                            >
                              <Badge tone="cyan">{z.phase}</Badge>
                              <span className="font-mono text-xs text-white/50">
                                ({Math.round(z.x)}, {Math.round(z.y)}) · r
                                {Math.round(z.r)}
                              </span>
                            </li>
                          ))}
                        </ol>
                      ) : (
                        <p className="text-sm text-white/40">
                          {coverageNote(
                            zones.meta,
                            zones.error ?? "No zone data for this match."
                          )}
                        </p>
                      )}
                    </Card>

                    <Card title="Per-team summary">
                      {summaries.length > 0 ? (
                        <ul className="space-y-2">
                          {summaries
                            .filter((s) => !selectedTeam || s.id === selectedTeam)
                            .map((s) => (
                              <li
                                key={s.id}
                                className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2"
                              >
                                <div className="flex items-center gap-2">
                                  <span
                                    className="inline-block h-3 w-3 rounded-full"
                                    style={{ backgroundColor: s.color }}
                                  />
                                  <span className="text-sm font-semibold">
                                    {s.name}
                                  </span>
                                </div>
                                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-white/50">
                                  <span>{s.waypoints} waypoints</span>
                                  <span>{s.fights} fights</span>
                                  {s.won > 0 ? (
                                    <span className="text-emerald-300">
                                      {s.won} won
                                    </span>
                                  ) : null}
                                  {s.lost > 0 ? (
                                    <span className="text-rose-300">
                                      {s.lost} lost
                                    </span>
                                  ) : null}
                                  {s.drop ? <span>drop: {s.drop}</span> : null}
                                </div>
                              </li>
                            ))}
                        </ul>
                      ) : (
                        <p className="text-sm text-white/40">
                          No team data for this match.
                        </p>
                      )}
                    </Card>

                    <Card title="Fights">
                      {visibleFights.length > 0 ? (
                        <ul className="space-y-2">
                          {visibleFights.map((f, i) => (
                            <li
                              key={i}
                              className="flex flex-wrap items-center gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm"
                            >
                              <Badge
                                tone={
                                  (f.outcome ?? "").includes("win")
                                    ? "green"
                                    : (f.outcome ?? "").includes("loss")
                                      ? "red"
                                      : "amber"
                                }
                              >
                                {f.outcome || "unknown"}
                              </Badge>
                              <span className="min-w-0 flex-1">{f.label}</span>
                              <span className="font-mono text-xs text-white/40">
                                ({Math.round(f.x)}, {Math.round(f.y)})
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-sm text-white/40">
                          {coverageNote(
                            fights.meta,
                            fights.error ?? "No fights for this match."
                          )}
                        </p>
                      )}
                    </Card>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function MatchTimelinePage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <MatchTimelineInner />
    </RequireRole>
  );
}
