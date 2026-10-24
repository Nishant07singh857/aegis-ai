/**
 * AEGIS AI — Analyst Dashboard shared types & helpers (Phase 11, W3;
 * contract-aligned in the Integrity Fix Pack, C5).
 *
 * Backend endpoints:
 *   GET /api/v1/analysis/rotations?team_id=&match_id=
 *   GET /api/v1/analysis/drops?match_id=
 *   GET /api/v1/analysis/zones?match_id=
 *   GET /api/v1/dossiers/fights?team_id=&match_id=
 *   GET /api/v1/dossiers/dossier?team_id=
 *   GET /api/v1/dossiers/report/match/{id}.pdf
 *   GET /api/v1/dossiers/report/team/{team_id}.pdf
 *
 * Shapes below mirror the backend pydantic schemas EXACTLY — do not add
 * fields the backend does not send:
 *   backend/app/analysis/schemas.py  — RotationResponse (list payload),
 *     DropsPayload { events, team_stats }, ZonesPayload { phases,
 *     tendency }, all wrapped in AnalysisEnvelope { data, meta }.
 *   backend/app/dossiers/schemas.py  — DossierOut, FightDetailOut,
 *     wrapped as { data } (fights add a meta block).
 * Endpoints may be missing, may 404, or may return empty data — every
 * reader degrades to an honest empty state; nothing is ever mocked.
 */

export interface EnvelopeMeta {
  source?: string;
  coverage?: string;
  [key: string]: unknown;
}

export interface Unwrapped<T> {
  data: T;
  meta: EnvelopeMeta | null;
}

/**
 * Unwrap { data, meta } envelopes. Tolerates bare payloads (the object or
 * array itself) so the UI keeps working regardless of the exact shape the
 * sibling backend worker ships, as long as no fields are fabricated.
 */
export function unwrapEnvelope<T>(json: unknown, fallback: T): Unwrapped<T> {
  if (json !== null && typeof json === "object") {
    const rec = json as Record<string, unknown>;
    if ("data" in rec) {
      const meta =
        rec.meta !== null && typeof rec.meta === "object"
          ? (rec.meta as EnvelopeMeta)
          : null;
      return { data: (rec.data ?? fallback) as T, meta };
    }
  }
  return { data: (json ?? fallback) as T, meta: null };
}

/** The honest one-line coverage note for a response (from API meta). */
export function coverageNote(meta: EnvelopeMeta | null, fallback: string): string {
  const c = meta?.coverage;
  if (typeof c === "string" && c.trim().length > 0) return c.trim();
  return fallback;
}

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------

export interface TeamLite {
  id: string;
  name: string;
  tag?: string | null;
  color?: string | null;
  colors?: Record<string, string> | null;
  source?: "db" | "demo";
}

const FALLBACK_PALETTE = [
  "#00f0ff", "#ff3366", "#a3e635", "#f59e0b", "#8b5cf6",
  "#f472b6", "#34d399", "#60a5fa", "#facc15", "#fb7185",
  "#2dd4bf", "#c084fc",
];

export function teamColorOf(
  t: { color?: string | null; colors?: Record<string, string> | null },
  index = 0
): string {
  return (
    t.color ||
    (t.colors && (t.colors.primary || t.colors.main)) ||
    FALLBACK_PALETTE[index % FALLBACK_PALETTE.length]
  );
}

export function colorFromId(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return FALLBACK_PALETTE[h % FALLBACK_PALETTE.length];
}

// ---------------------------------------------------------------------------
// Fights — backend/app/dossiers/schemas.py FightDetailOut, verbatim fields.
// ---------------------------------------------------------------------------

export interface FightPlayerRef {
  player_id: string;
  ign: string;
  team_id?: string | null;
  team_name?: string | null;
}

export interface FightTeamRef {
  team_id: string;
  team_name: string;
  players?: FightPlayerRef[];
}

export interface FightRow {
  id: string;
  match_id: string;
  map_name?: string | null;
  start_timestamp_ms: number;
  end_timestamp_ms?: number | null;
  duration_ms?: number | null;
  location_x?: number | null;
  location_y?: number | null;
  intensity_score?: number;
  teams?: FightTeamRef[];
  winner?: { team_id: string; team_name: string } | null;
  /** "'<team name> won' | 'disengaged' | 'ongoing' | 'unknown'." */
  outcome: string;
  third_parties?: {
    team_id: string;
    team_name: string;
    first_action_at_ms?: number | null;
    delay_after_start_ms?: number | null;
    evidence?: string;
  }[];
  total_kills?: number;
  total_knocks?: number;
  utility_used?: Record<string, unknown>;
}

export function fightOutcome(f: FightRow): string {
  return String(f.outcome ?? "").toLowerCase();
}

/** Names of the fight's teams other than `ownTeamId` (all teams when the
 *  caller's team is unknown — e.g. the match timeline). */
export function fightOpponent(f: FightRow, ownTeamId?: string | null): string {
  const teams = Array.isArray(f.teams) ? f.teams : [];
  const others = ownTeamId
    ? teams.filter((t) => t.team_id !== ownTeamId)
    : teams;
  const names = others.map((t) => t.team_name).filter(Boolean);
  return names.length > 0 ? names.join(", ") : "unknown opponent";
}

/** Won/lost for one team, derived from the backend `winner` field only.
 *  No winner (disengaged / ongoing / unknown) is reported as-is. */
export function fightResultForTeam(
  f: FightRow,
  teamId: string
): "won" | "lost" | string {
  if (f.winner && typeof f.winner.team_id === "string") {
    return f.winner.team_id === teamId ? "won" : "lost";
  }
  return fightOutcome(f) || "unknown";
}

// ---------------------------------------------------------------------------
// Dossier (team) — backend/app/dossiers/schemas.py DossierOut, verbatim.
// ---------------------------------------------------------------------------

export interface PlaystyleData {
  /** aggressive | balanced | passive | insufficient_data */
  label: string;
  evidence: string;
  fights_per_match?: number | null;
  aggression_score?: number | null;
  matches_sampled?: number;
}

export interface StatEdge {
  metric: string;
  /** Human-readable, e.g. 'fight win rate 62% vs 41% tournament avg'. */
  label: string;
  team_value?: number | null;
  tournament_avg?: number | null;
  delta_pct?: number | null;
}

export interface DropLocationTendency {
  location?: string;
  matches?: number;
  [key: string]: unknown;
}

export interface RotationTimingTendency {
  early?: number;
  mid?: number;
  late?: number;
  unknown?: number;
  label?: string;
  note?: string;
}

export interface CentralityTendency {
  label?: string;
  note?: string;
  team_centroid?: [number, number];
  tournament_centroid?: [number, number];
  distance_from_tournament_centroid?: number | null;
  median_team_distance?: number | null;
  located_fights?: number;
  [key: string]: unknown;
}

export interface DossierTendencies {
  drop_locations?: DropLocationTendency[];
  drop_location_note?: string;
  rotation_timing?: RotationTimingTendency;
  centrality?: CentralityTendency;
}

export interface HeadToHeadRow {
  opponent_team_id: string;
  opponent_name: string;
  fights?: number;
  won?: number;
  lost?: number;
  disengaged?: number;
  kills_for?: number;
  kills_against?: number;
}

export interface DossierData {
  team_id?: string;
  team_name?: string;
  computed_at?: string;
  /** 'fresh' | 'recomputed' | 'insufficient_data' */
  data_freshness?: string;
  matches_sampled?: number;
  fights_sampled?: number;
  playstyle?: PlaystyleData;
  strengths?: StatEdge[];
  weaknesses?: StatEdge[];
  comparison_note?: string;
  tendencies?: DossierTendencies;
  head_to_head?: HeadToHeadRow[];
  notes?: string[];
}

export function dossierTeamName(d: DossierData): string {
  return d.team_name || "Unknown team";
}

export function dossierPlaystyle(d: DossierData): string | null {
  const label = d.playstyle?.label;
  return typeof label === "string" && label ? label : null;
}

export function dossierHeadToHead(d: DossierData): HeadToHeadRow[] {
  return Array.isArray(d.head_to_head) ? d.head_to_head : [];
}

/** Win rate over DECIDED fights only (won / (won + lost)); null when no
 *  fight against this opponent was decided — never a fabricated 0%. */
export function h2hWinRate(r: HeadToHeadRow): number | null {
  const won = typeof r.won === "number" ? r.won : 0;
  const lost = typeof r.lost === "number" ? r.lost : 0;
  if (won + lost === 0) return null;
  return won / (won + lost);
}

export function h2hName(r: HeadToHeadRow): string {
  return String(r.opponent_name ?? r.opponent_team_id ?? "—");
}

// ---------------------------------------------------------------------------
// Match analysis — backend/app/analysis/schemas.py, verbatim fields.
// ---------------------------------------------------------------------------

/** RotationResponse — the rotations payload is a bare list of these. */
export interface RotationPoint {
  id?: string;
  match_id?: string;
  team_id: string;
  timestamp_ms?: number;
  from_zone?: string | null;
  to_zone?: string | null;
  game_time_ms?: number | null;
  /** Polyline [[x, y], ...] in map metres. */
  waypoints?: [number, number][];
  vehicle_used?: boolean;
  fights_encountered?: string[];
  rotation_order?: number | null;
  distance_m?: number | null;
  waypoint_count?: number;
  computed_distance_m?: number;
  source?: string;
}

/** DropResponse — one drop event. */
export interface DropPoint {
  id?: string;
  match_id?: string;
  team_id: string;
  map_name?: string | null;
  location_x?: number | null;
  location_y?: number | null;
  named_location?: string | null;
  timestamp_ms?: number;
  contested?: boolean;
  contested_by?: string[];
  survived_drop?: boolean | null;
  source?: string;
}

/** DropLocationStat (inside DropTeamStats.favorite_locations). */
export interface DropLocationStat {
  named_location: string;
  map_name?: string | null;
  drops: number;
  contested: number;
}

/** DropTeamStats. */
export interface DropTeamStat {
  team_id: string;
  total_drops: number;
  contested_drops: number;
  known_outcomes: number;
  survived: number;
  /** survived / known outcomes; null when no outcome is known. */
  survival_rate?: number | null;
  favorite_locations?: DropLocationStat[];
}

/** DropsPayload — the drops `data` is this OBJECT, not a list (C5a). */
export interface DropsData {
  events: DropPoint[];
  team_stats: DropTeamStat[];
}

export const EMPTY_DROPS: DropsData = { events: [], team_stats: [] };

/** ZonePhaseResponse — one zone phase. Coordinates/radius in map metres. */
export interface ZonePhase {
  id?: string;
  match_id?: string;
  phase_number: number;
  center_x: number;
  center_y: number;
  radius: number;
  timestamp_ms?: number;
  source?: string;
}

/** ZoneTendency — per-team edge-vs-centre tendency for a match. */
export interface ZoneTendencyRow {
  team_id: string;
  match_id: string;
  phases_recorded: number;
  phases_with_position: number;
  /** Mean distance from zone centre / zone radius; <1 inside, >1 outside. */
  avg_normalized_distance?: number | null;
  /** center | mid-zone | edge | outside | unknown */
  tendency: string;
  avg_distance_traveled_per_phase_m?: number | null;
  notes?: string[];
}

/** ZonesPayload — the zones `data` is this OBJECT, not a list (C5a). */
export interface ZonesData {
  phases: ZonePhase[];
  tendency: ZoneTendencyRow[];
}

export const EMPTY_ZONES: ZonesData = { phases: [], tendency: [] };

/** Normalize a map coordinate to the 0–1000 schematic space. null = unknown. */
export function normCoord(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  // Backends may store normalized 0–1 coordinates; scale those up.
  const scaled = v >= 0 && v <= 1 ? v * 1000 : v;
  return Math.min(1000, Math.max(0, scaled));
}

/**
 * Build ONE shared coordinate transform for a match's recorded geometry.
 *
 * Analysis coordinates are map metres (an Erangel centre is ~4000,4000),
 * but the payloads carry no map extent, so no absolute metre→schematic
 * conversion exists. The honest schematic transform is a uniform fit:
 * every plotted value (rotation waypoints, drop/fight locations, zone
 * centres AND radii) is scaled by the same factor derived from the
 * largest extent present in THIS match's data. Relative positions —
 * the only thing a schematic claims — stay truthful; nothing is clamped
 * to a corner or stretched per-axis. Already-normalized (0–1) and
 * already-schematic (≤1000) spaces pass through unchanged.
 */
export function fitCoordScaler(values: number[]): (v: number) => number | null {
  const finite = values.filter(
    (v) => typeof v === "number" && Number.isFinite(v)
  );
  const maxAbs = finite.length > 0 ? Math.max(...finite.map(Math.abs)) : 0;
  const factor = maxAbs <= 1 ? 1000 : maxAbs <= 1000 ? 1 : 1000 / maxAbs;
  return (v: number) => {
    if (typeof v !== "number" || !Number.isFinite(v)) return null;
    return Math.min(1000, Math.max(0, v * factor));
  };
}

// ---------------------------------------------------------------------------
// Schematic transforms (pure — shared by the match page and its checks).
// Structural shapes match app/analyst/components/MapSchematic props.
// ---------------------------------------------------------------------------

export interface SchematicPoint {
  x: number;
  y: number;
  phase?: string;
}

export interface SchematicRotationTeamLike {
  teamId: string;
  teamName: string;
  color: string;
  points: SchematicPoint[];
}

export interface SchematicZoneLike {
  x: number;
  y: number;
  r: number;
  phase: string;
}

export interface SchematicDropLike {
  x: number;
  y: number;
  label: string;
  color: string;
  teamId: string | null;
}

export interface SchematicFightLike {
  x: number;
  y: number;
  label: string;
  outcome?: string;
  /** First participating team (pin colour/attribution). */
  teamId: string | null;
  /** ALL participating teams — a fight belongs to every team in it. */
  teamIds: string[];
}

/** Rotation events → per-team polylines from their real `waypoints`. */
export function rotationTeamsFromEvents(
  events: RotationPoint[],
  scale: (v: number) => number | null,
  teamNameOf: (teamId: string) => string
): SchematicRotationTeamLike[] {
  const byId = new Map<string, SchematicRotationTeamLike>();
  const ordered = [...events].sort(
    (a, b) => (a.rotation_order ?? 0) - (b.rotation_order ?? 0)
  );
  for (const ev of ordered) {
    const teamId = typeof ev.team_id === "string" ? ev.team_id : "";
    if (!teamId) continue;
    const wps = Array.isArray(ev.waypoints) ? ev.waypoints : [];
    const pts: SchematicPoint[] = [];
    for (const wp of wps) {
      if (!Array.isArray(wp) || wp.length < 2) continue;
      const x = scale(wp[0]);
      const y = scale(wp[1]);
      if (x === null || y === null) continue;
      pts.push({
        x,
        y,
        phase:
          ev.rotation_order != null ? `R${ev.rotation_order}` : undefined,
      });
    }
    if (pts.length === 0) continue;
    let team = byId.get(teamId);
    if (!team) {
      team = {
        teamId,
        teamName: teamNameOf(teamId),
        color: colorFromId(teamId),
        points: [],
      };
      byId.set(teamId, team);
    }
    team.points.push(...pts);
  }
  return [...byId.values()];
}

/** Zone phases → schematic circles (centre + radius share the transform). */
export function schematicZonesFromPhases(
  phases: ZonePhase[],
  scale: (v: number) => number | null
): SchematicZoneLike[] {
  const out: SchematicZoneLike[] = [];
  for (const z of phases) {
    const x = scale(z.center_x);
    const y = scale(z.center_y);
    const r = scale(z.radius);
    if (x === null || y === null || r === null) continue;
    out.push({ x, y, r, phase: `P${z.phase_number}` });
  }
  return out;
}

/** Drop events → schematic markers (only events with real coordinates). */
export function schematicDropsFromEvents(
  events: DropPoint[],
  scale: (v: number) => number | null
): SchematicDropLike[] {
  const out: SchematicDropLike[] = [];
  for (const d of events) {
    if (typeof d.location_x !== "number" || typeof d.location_y !== "number")
      continue;
    const x = scale(d.location_x);
    const y = scale(d.location_y);
    if (x === null || y === null) continue;
    const teamId = typeof d.team_id === "string" && d.team_id ? d.team_id : null;
    out.push({
      x,
      y,
      label: d.named_location || teamId || "drop",
      color: teamId ? colorFromId(teamId) : "#00f0ff",
      teamId,
    });
  }
  return out;
}

/** Fight rows → schematic pins (only fights with real coordinates). */
export function schematicFightsFromRows(
  rows: FightRow[],
  scale: (v: number) => number | null
): SchematicFightLike[] {
  const out: SchematicFightLike[] = [];
  for (const f of rows) {
    if (typeof f.location_x !== "number" || typeof f.location_y !== "number")
      continue;
    const x = scale(f.location_x);
    const y = scale(f.location_y);
    if (x === null || y === null) continue;
    const teams = Array.isArray(f.teams) ? f.teams : [];
    const label = `${teams.map((t) => t.team_name).join(" vs ") || "fight"}${f.outcome ? ` · ${f.outcome}` : ""}`;
    out.push({
      x,
      y,
      label,
      outcome: fightOutcome(f),
      teamId: teams.length > 0 ? teams[0].team_id : null,
      teamIds: teams.map((t) => t.team_id),
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Grenade trajectories (Phase 23, W4) — ESTIMATED arcs, endpoint-based only.
// GET /api/v1/analysis/grenade-trajectories?match_id=
// ---------------------------------------------------------------------------

export interface GrenadeTrajectoryRecord {
  id?: string;
  match_id?: string;
  ts?: number;
  /** Always true — the backend refuses to store unlabelled records. */
  estimated?: boolean;
  method?: string;
  confidence_basis?: string;
  source?: string;
  weapon?: string | null;
  coordinate_space?: string;
  distance?: number;
  distance_units?: string;
  apex_height?: number;
  thrower?: {
    player?: string | null;
    team?: string | null;
    position?: [number, number];
    position_basis?: string;
  };
  explosion?: {
    position?: [number, number];
    position_basis?: string;
  };
  victim?: { player?: string | null; team?: string | null } | null;
  arc_points?: { x: number; y: number; h?: number }[];
}

export interface SchematicTrajectoryData {
  id: string;
  points: { x: number; y: number; h: number }[];
  /** apex_height / distance (unitless, same-space ratio). */
  apexRatio: number;
  label: string;
}

/**
 * Convert a trajectory record to schematic (0–1000) points, or null when
 * that would require inventing a coordinate conversion.
 *
 * Only two spaces map honestly onto the analyst schematic:
 *   "normalized_0_1"    — the backend's normalized map space (x1000);
 *   "schematic_0_1000"  — already schematic.
 * Anything else (minimap_region_pixels — a zoomed viewport, NOT the whole
 * map; frame_pixels — screen space) has no honest whole-map mapping, so
 * the arc is NOT drawn rather than drawn in the wrong place.
 */
export function trajectorySchematic(
  t: GrenadeTrajectoryRecord,
  index: number
): SchematicTrajectoryData | null {
  const space = String(t.coordinate_space ?? "");
  if (space !== "normalized_0_1" && space !== "schematic_0_1000") return null;
  const raw = Array.isArray(t.arc_points) ? t.arc_points : [];
  const points: { x: number; y: number; h: number }[] = [];
  for (const p of raw) {
    const x = normCoord(p?.x);
    const y = normCoord(p?.y);
    if (x === null || y === null) return null;
    points.push({ x, y, h: typeof p.h === "number" ? p.h : 0 });
  }
  if (points.length < 2) return null;
  const distance = typeof t.distance === "number" ? t.distance : 0;
  const apex = typeof t.apex_height === "number" ? t.apex_height : 0;
  const apexRatio = distance > 0 && apex > 0 ? apex / distance : 0.25;
  const thrower = t.thrower?.player || "thrower";
  const victim = t.victim?.player || "explosion";
  return {
    id: String(t.id ?? `traj-${index}`),
    points,
    apexRatio,
    label: `${thrower} → ${victim}`,
  };
}
