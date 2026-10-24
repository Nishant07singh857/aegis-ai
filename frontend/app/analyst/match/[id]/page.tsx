"use client";

/**
 * AEGIS AI — MATCH TIMELINE (Phase 11, W3; contract-aligned in the
 * Integrity Fix Pack, C5a).
 *
 * Route: /analyst/match/[id] (operator + admin). Coach-facing match report:
 * schematic SVG map with per-team rotation paths, drop markers, zone-phase
 * circles and fight pins; zone phase list + per-team zone tendency; drops
 * (events + per-team drop stats); per-team summary; fight list; and a
 * Download Match Report PDF button.
 *
 * Data (all live, all optional) — shapes mirror
 * backend/app/analysis/schemas.py and backend/app/dossiers/schemas.py:
 *   GET /api/v1/analysis/rotations?match_id=   → data: RotationResponse[]
 *   GET /api/v1/analysis/drops?match_id=       → data: { events, team_stats }
 *   GET /api/v1/analysis/zones?match_id=       → data: { phases, tendency }
 *   GET /api/v1/analysis/grenade-trajectories?match_id=  (ESTIMATED arcs)
 *   GET /api/v1/dossiers/fights?match_id=      → data: FightDetailOut[]
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
  SchematicTrajectory,
} from "../../components/MapSchematic";
import {
  unwrapEnvelope,
  coverageNote,
  colorFromId,
  fitCoordScaler,
  rotationTeamsFromEvents,
  schematicZonesFromPhases,
  schematicDropsFromEvents,
  schematicFightsFromRows,
  fightResultForTeam,
  trajectorySchematic,
  EMPTY_DROPS,
  EMPTY_ZONES,
  type EnvelopeMeta,
  type RotationPoint,
  type DropsData,
  type ZonesData,
  type FightRow,
  type GrenadeTrajectoryRecord,
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

function formatMs(ts: number | undefined): string {
  if (typeof ts !== "number" || !Number.isFinite(ts)) return "—";
  const totalSec = Math.floor(ts / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function MatchTimelineInner() {
  const params = useParams<{ id: string }>();
  const matchId = decodeURIComponent(params?.id ?? "");
  const { token } = useAuth();

  const [loading, setLoading] = useState(true);
  const [rotations, setRotations] =
    useState<EndpointState<RotationPoint[]>>(emptyEndpoint([]));
  const [drops, setDrops] =
    useState<EndpointState<DropsData>>(emptyEndpoint(EMPTY_DROPS));
  const [zones, setZones] =
    useState<EndpointState<ZonesData>>(emptyEndpoint(EMPTY_ZONES));
  const [fights, setFights] =
    useState<EndpointState<FightRow[]>>(emptyEndpoint([]));
  const [trajectories, setTrajectories] =
    useState<EndpointState<GrenadeTrajectoryRecord[]>>(emptyEndpoint([]));
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
    const [r, d, z, f, t] = await Promise.all([
      loadEndpoint<RotationPoint[]>(`/api/v1/analysis/rotations?${q}`, []),
      loadEndpoint<DropsData>(`/api/v1/analysis/drops?${q}`, EMPTY_DROPS),
      loadEndpoint<ZonesData>(`/api/v1/analysis/zones?${q}`, EMPTY_ZONES),
      loadEndpoint<FightRow[]>(`/api/v1/dossiers/fights?${q}`, []),
      loadEndpoint<GrenadeTrajectoryRecord[]>(
        `/api/v1/analysis/grenade-trajectories?${q}`,
        []
      ),
    ]);
    setRotations(r);
    // Drops/zones payloads are envelope OBJECTS ({events, team_stats} /
    // {phases, tendency}) — normalize defensively so a malformed block
    // degrades to an honest empty state instead of a crash (C5a).
    setDrops({
      ...d,
      data: {
        events: Array.isArray(d.data?.events) ? d.data.events : [],
        team_stats: Array.isArray(d.data?.team_stats) ? d.data.team_stats : [],
      },
    });
    setZones({
      ...z,
      data: {
        phases: Array.isArray(z.data?.phases) ? z.data.phases : [],
        tendency: Array.isArray(z.data?.tendency) ? z.data.tendency : [],
      },
    });
    setFights(f);
    setTrajectories(t);
    setLoading(false);
  }, [matchId, loadEndpoint]);

  useEffect(() => {
    void load();
  }, [load]);

  // ---- transforms ---------------------------------------------------------

  // One shared coordinate transform for everything plotted on the
  // schematic (see fitCoordScaler in types.ts): analysis coordinates are
  // map metres with no map extent in the payload, so the schematic is a
  // uniform fit over this match's recorded extents — relative positions
  // only, never per-axis stretching or corner-clamping.
  const coordScale = useMemo(() => {
    const values: number[] = [];
    rotations.data.forEach((r) => {
      (Array.isArray(r.waypoints) ? r.waypoints : []).forEach((wp) => {
        if (Array.isArray(wp)) values.push(wp[0], wp[1]);
      });
    });
    drops.data.events.forEach((d) => {
      if (typeof d.location_x === "number") values.push(d.location_x);
      if (typeof d.location_y === "number") values.push(d.location_y);
    });
    zones.data.phases.forEach((z) => {
      values.push(z.center_x, z.center_y, z.center_x + z.radius, z.center_y + z.radius);
    });
    fights.data.forEach((f) => {
      if (typeof f.location_x === "number") values.push(f.location_x);
      if (typeof f.location_y === "number") values.push(f.location_y);
    });
    return fitCoordScaler(values);
  }, [rotations.data, drops.data, zones.data, fights.data]);

  const fittedFromMetres = useMemo(() => {
    const all = [
      ...zones.data.phases.map((z) => z.center_x + z.radius),
      ...drops.data.events.map((d) => d.location_x ?? 0),
    ];
    return all.some((v) => typeof v === "number" && v > 1000);
  }, [zones.data, drops.data]);

  const rotationTeams: SchematicRotationTeam[] = useMemo(
    () =>
      rotationTeamsFromEvents(rotations.data, coordScale, (id) => id).map(
        (t) => ({
          teamId: t.teamId,
          teamName: t.teamName,
          color: t.color,
          points: t.points,
        })
      ),
    [rotations.data, coordScale]
  );

  const schematicZones: SchematicZone[] = useMemo(
    () => schematicZonesFromPhases(zones.data.phases, coordScale),
    [zones.data, coordScale]
  );

  const schematicFights = useMemo(
    () => schematicFightsFromRows(fights.data, coordScale),
    [fights.data, coordScale]
  );

  const schematicDrops = useMemo(
    () => schematicDropsFromEvents(drops.data.events, coordScale),
    [drops.data, coordScale]
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
    drops.data.events.forEach((d) => {
      if (!d.team_id) return;
      const s = ensure(d.team_id, d.team_id, colorFromId(d.team_id));
      if (!s.drop && d.named_location) s.drop = d.named_location;
    });
    fights.data.forEach((f) => {
      (Array.isArray(f.teams) ? f.teams : []).forEach((t) => {
        const s = ensure(t.team_id, t.team_name, colorFromId(t.team_id));
        s.name = t.team_name || s.name;
        s.fights += 1;
        const res = fightResultForTeam(f, t.team_id);
        if (res === "won") s.won += 1;
        else if (res === "lost") s.lost += 1;
      });
    });
    return [...map.values()].sort((a, b) => b.waypoints - a.waypoints);
  }, [rotationTeams, drops.data, fights.data]);

  const teamNameOf = useCallback(
    (teamId: string): string =>
      summaries.find((s) => s.id === teamId)?.name ?? teamId,
    [summaries]
  );

  const visibleRotations = selectedTeam
    ? rotationTeams.filter((t) => t.teamId === selectedTeam)
    : rotationTeams;

  const visibleFights = selectedTeam
    ? schematicFights.filter((f) => f.teamIds.includes(selectedTeam))
    : schematicFights;

  const visibleDrops = selectedTeam
    ? schematicDrops.filter((d) => d.teamId === selectedTeam)
    : schematicDrops;

  const visibleFightRows = selectedTeam
    ? fights.data.filter((f) =>
        (Array.isArray(f.teams) ? f.teams : []).some(
          (t) => t.team_id === selectedTeam
        )
      )
    : fights.data;

  // ESTIMATED grenade arcs: only records whose coordinate space maps
  // honestly onto the schematic are drawn (see trajectorySchematic);
  // the rest are counted and reported, never force-fitted.
  const schematicTrajectories: SchematicTrajectory[] = useMemo(
    () =>
      trajectories.data.flatMap((t, i) => {
        const s = trajectorySchematic(t, i);
        return s ? [{ id: s.id, points: s.points, apexRatio: s.apexRatio, label: s.label }] : [];
      }),
    [trajectories.data]
  );
  const unmappableTrajectories =
    trajectories.data.length - schematicTrajectories.length;

  const coverageLines = [
    rotations,
    drops,
    zones,
    fights,
    trajectories,
  ].map((e) =>
    e.ok
      ? coverageNote(e.meta, "loaded")
      : (e.error ?? "no data")
  );

  const hasAnyData =
    rotationTeams.length > 0 ||
    zones.data.phases.length > 0 ||
    zones.data.tendency.length > 0 ||
    fights.data.length > 0 ||
    drops.data.events.length > 0 ||
    drops.data.team_stats.length > 0 ||
    schematicTrajectories.length > 0;

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
                {["Rotations", "Drops", "Zones", "Fights", "Grenade trajectories (ESTIMATED)"].map((label, i) => (
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
                      trajectories={schematicTrajectories}
                      selectedTeamId={selectedTeam}
                    />
                    {fittedFromMetres ? (
                      <p className="mt-2 text-center font-mono text-xs text-white/40">
                        Schematic fitted to this match&apos;s recorded
                        coordinates (map metres) — relative positions only,
                        not to scale.
                      </p>
                    ) : null}
                    {unmappableTrajectories > 0 ? (
                      <p className="mt-2 text-center font-mono text-xs text-amber-300/70">
                        {unmappableTrajectories} ESTIMATED grenade arc(s) not
                        drawn: their coordinate space has no honest mapping
                        onto this schematic map.
                      </p>
                    ) : null}
                    {selectedTeam ? (
                      <p className="mt-2 text-center font-mono text-xs text-white/40">
                        Showing {teamNameOf(selectedTeam)} — other teams dimmed
                      </p>
                    ) : null}
                  </div>

                  {/* side column */}
                  <div className="space-y-4">
                    <Card title="Zone phases">
                      {zones.data.phases.length > 0 ? (
                        <ol className="space-y-2">
                          {zones.data.phases.map((z, i) => (
                            <li
                              key={z.id ?? i}
                              className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm"
                            >
                              <Badge tone="cyan">P{z.phase_number}</Badge>
                              <span className="font-mono text-xs text-white/50">
                                centre ({Math.round(z.center_x)},{" "}
                                {Math.round(z.center_y)}) m · r{" "}
                                {Math.round(z.radius)} m
                              </span>
                              <span className="ml-auto font-mono text-xs text-white/40">
                                {formatMs(z.timestamp_ms)}
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

                    <Card title="Zone tendency (per team)">
                      {zones.data.tendency.length > 0 ? (
                        <ul className="space-y-2">
                          {zones.data.tendency
                            .filter(
                              (t) => !selectedTeam || t.team_id === selectedTeam
                            )
                            .map((t, i) => (
                              <li
                                key={`${t.team_id}-${i}`}
                                className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2"
                              >
                                <div className="flex items-center gap-2">
                                  <span className="text-sm font-semibold">
                                    {teamNameOf(t.team_id)}
                                  </span>
                                  <Badge tone="cyan">{t.tendency}</Badge>
                                </div>
                                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-white/50">
                                  <span>
                                    position in {t.phases_with_position}/
                                    {t.phases_recorded} phases
                                  </span>
                                  {typeof t.avg_normalized_distance ===
                                  "number" ? (
                                    <span>
                                      avg dist from centre:{" "}
                                      {t.avg_normalized_distance.toFixed(2)}×
                                      radius
                                    </span>
                                  ) : null}
                                  {typeof t.avg_distance_traveled_per_phase_m ===
                                  "number" ? (
                                    <span>
                                      {Math.round(
                                        t.avg_distance_traveled_per_phase_m
                                      )}{" "}
                                      m / phase
                                    </span>
                                  ) : null}
                                </div>
                                {t.notes && t.notes.length > 0 ? (
                                  <ul className="mt-1 space-y-0.5 text-xs text-white/45">
                                    {t.notes.map((n, j) => (
                                      <li key={j}>· {n}</li>
                                    ))}
                                  </ul>
                                ) : null}
                              </li>
                            ))}
                        </ul>
                      ) : (
                        <p className="text-sm text-white/40">
                          No zone tendency recorded for this match yet.
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
                      {visibleFightRows.length > 0 ? (
                        <ul className="space-y-2">
                          {visibleFightRows.map((f, i) => (
                            <li
                              key={f.id ?? i}
                              className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge
                                  tone={
                                    f.winner
                                      ? "green"
                                      : (f.outcome ?? "")
                                            .toLowerCase()
                                            .includes("disengag")
                                        ? "amber"
                                        : "slate"
                                  }
                                >
                                  {f.outcome || "unknown"}
                                </Badge>
                                <span className="ml-auto font-mono text-xs text-white/40">
                                  {formatMs(f.start_timestamp_ms)}
                                  {typeof f.total_kills === "number"
                                    ? ` · ${f.total_kills} kills`
                                    : ""}
                                  {typeof f.total_knocks === "number"
                                    ? ` · ${f.total_knocks} knocks`
                                    : ""}
                                </span>
                              </div>
                              <div className="mt-1 text-white/75">
                                {(Array.isArray(f.teams) ? f.teams : [])
                                  .map((t) => t.team_name)
                                  .join(" vs ") || "unknown teams"}
                                {f.map_name ? (
                                  <span className="text-white/40">
                                    {" "}
                                    · {f.map_name}
                                  </span>
                                ) : null}
                              </div>
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

                {/* drops: events + per-team stats (DropsPayload) */}
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <Card title="Drop events">
                    {drops.data.events.length > 0 ? (
                      <ul className="space-y-2">
                        {drops.data.events
                          .filter(
                            (d) => !selectedTeam || d.team_id === selectedTeam
                          )
                          .map((d, i) => (
                            <li
                              key={d.id ?? i}
                              className="flex flex-wrap items-center gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm"
                            >
                              <span className="font-medium">
                                {teamNameOf(d.team_id)}
                              </span>
                              <span className="text-white/60">
                                {d.named_location ?? "unnamed location"}
                                {d.map_name ? ` · ${d.map_name}` : ""}
                              </span>
                              {d.contested ? (
                                <Badge tone="amber">
                                  contested
                                  {d.contested_by && d.contested_by.length > 0
                                    ? ` by ${d.contested_by.length}`
                                    : ""}
                                </Badge>
                              ) : null}
                              {d.survived_drop === true ? (
                                <Badge tone="green">survived drop</Badge>
                              ) : d.survived_drop === false ? (
                                <Badge tone="red">eliminated at drop</Badge>
                              ) : null}
                              <span className="ml-auto font-mono text-xs text-white/40">
                                {formatMs(d.timestamp_ms)}
                              </span>
                            </li>
                          ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-white/40">
                        {coverageNote(
                          drops.meta,
                          drops.error ?? "No drop events for this match."
                        )}
                      </p>
                    )}
                  </Card>

                  <Card title="Drop stats (per team)">
                    {drops.data.team_stats.length > 0 ? (
                      <ul className="space-y-2">
                        {drops.data.team_stats
                          .filter(
                            (s) => !selectedTeam || s.team_id === selectedTeam
                          )
                          .map((s) => (
                            <li
                              key={s.team_id}
                              className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2"
                            >
                              <div className="text-sm font-semibold">
                                {teamNameOf(s.team_id)}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-white/50">
                                <span>{s.total_drops} drops</span>
                                <span>{s.contested_drops} contested</span>
                                {typeof s.survival_rate === "number" ? (
                                  <span>
                                    drop survival{" "}
                                    {(s.survival_rate * 100).toFixed(0)}% (
                                    {s.survived}/{s.known_outcomes} known)
                                  </span>
                                ) : (
                                  <span>drop survival unknown</span>
                                )}
                              </div>
                              {s.favorite_locations &&
                              s.favorite_locations.length > 0 ? (
                                <div className="mt-1.5 flex flex-wrap gap-1.5">
                                  {s.favorite_locations.map((loc, j) => (
                                    <Badge key={j} tone="cyan">
                                      {loc.named_location} · {loc.drops}
                                      {loc.contested > 0
                                        ? ` · ${loc.contested} contested`
                                        : ""}
                                    </Badge>
                                  ))}
                                </div>
                              ) : null}
                            </li>
                          ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-white/40">
                        No per-team drop stats for this match yet.
                      </p>
                    )}
                  </Card>
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
