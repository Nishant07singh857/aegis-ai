/**
 * AEGIS AI — Analyst Dashboard shared types & helpers (Phase 11, W3).
 *
 * Backend endpoints (built by sibling workers, same phase):
 *   GET /api/v1/analysis/rotations?team_id=&match_id=
 *   GET /api/v1/analysis/drops
 *   GET /api/v1/analysis/zones
 *   GET /api/v1/dossiers/fights
 *   GET /api/v1/dossiers/dossier?team_id=
 *   GET /api/v1/dossiers/report/match/{id}.pdf
 *   GET /api/v1/dossiers/report/team/{team_id}.pdf
 *
 * Response envelope: { "data": [...], "meta": { "source": ..., "coverage": ... } }.
 * Endpoints may be missing, may 404, or may return empty data — every reader
 * below degrades to an honest empty state; nothing is ever mocked.
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
// Dossier (team) shapes — all fields optional; the backend is the source.
// ---------------------------------------------------------------------------

export interface HeadToHeadRow {
  team_id?: string;
  team_name?: string;
  opponent?: string;
  opponent_name?: string;
  matches?: number;
  wins?: number;
  losses?: number;
  win_rate?: number;
}

export interface FightRow {
  fight_id?: string;
  match_id?: string;
  opponent?: string;
  opponent_name?: string;
  team_id?: string;
  location_x?: number;
  location_y?: number;
  x?: number;
  y?: number;
  outcome?: string;
  result?: string;
  kills?: number;
  placement?: number;
  phase?: string;
  timestamp?: string | number;
}

export interface DossierTendencies {
  favorite_drops?: string[];
  rotation_timing?: string | null;
  edge_vs_center?: string | null;
  notes?: string[];
  [key: string]: unknown;
}

export interface DossierData {
  team_id?: string;
  team_name?: string;
  name?: string;
  tag?: string | null;
  playstyle?: string;
  play_style?: string;
  color?: string | null;
  stats?: Record<string, string | number | null | undefined>;
  strengths?: string[];
  weaknesses?: string[];
  tendencies?: DossierTendencies;
  head_to_head?: HeadToHeadRow[];
  h2h?: HeadToHeadRow[];
  fight_history?: FightRow[];
  fights?: FightRow[];
}

export function dossierTeamName(d: DossierData): string {
  return d.team_name || d.name || "Unknown team";
}

export function dossierPlaystyle(d: DossierData): string | null {
  return d.playstyle || d.play_style || null;
}

export function dossierHeadToHead(d: DossierData): HeadToHeadRow[] {
  return d.head_to_head || d.h2h || [];
}

export function dossierFights(d: DossierData): FightRow[] {
  return d.fight_history || d.fights || [];
}

// ---------------------------------------------------------------------------
// Match analysis shapes
// ---------------------------------------------------------------------------

export interface RotationPoint {
  team_id?: string;
  team?: string;
  team_name?: string;
  name?: string;
  tag?: string;
  color?: string;
  waypoint?: number;
  order?: number;
  sequence?: number;
  location_x?: number;
  location_y?: number;
  x?: number;
  y?: number;
  phase?: string;
  timestamp?: string | number;
}

export interface ZonePhase {
  phase?: string;
  stage?: string;
  circle_center_x?: number;
  cx?: number;
  x?: number;
  circle_center_y?: number;
  cy?: number;
  y?: number;
  radius?: number;
  r?: number;
}

export interface DropPoint {
  team_id?: string;
  team?: string;
  team_name?: string;
  label?: string;
  location_x?: number;
  location_y?: number;
  x?: number;
  y?: number;
  color?: string;
}

/** Normalize a map coordinate to the 0–1000 schematic space. null = unknown. */
export function normCoord(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  // Backends may store normalized 0–1 coordinates; scale those up.
  const scaled = v >= 0 && v <= 1 ? v * 1000 : v;
  return Math.min(1000, Math.max(0, scaled));
}

export function pointX(p: { location_x?: unknown; x?: unknown }): number | null {
  const v = normCoord(p.location_x);
  return v ?? normCoord(p.x);
}

export function pointY(p: { location_y?: unknown; y?: unknown }): number | null {
  const v = normCoord(p.location_y);
  return v ?? normCoord(p.y);
}

export function rotationTeamId(p: RotationPoint, index: number): string {
  return String(p.team_id ?? p.team ?? p.team_name ?? p.name ?? `team-${index}`);
}

export function rotationTeamName(p: RotationPoint, id: string): string {
  return String(p.team_name ?? p.name ?? p.team ?? p.tag ?? id);
}

export function rotationOrder(p: RotationPoint, index: number): number {
  const v = p.waypoint ?? p.order ?? p.sequence;
  return typeof v === "number" && Number.isFinite(v) ? v : index;
}

export function zoneRadius(z: ZonePhase): number | null {
  const v = z.radius ?? z.r;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
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

export function fightOutcome(f: FightRow): string {
  return String(f.outcome ?? f.result ?? "").toLowerCase();
}

export function fightOpponent(f: FightRow): string {
  return String(f.opponent_name ?? f.opponent ?? "unknown opponent");
}

export function h2hName(r: HeadToHeadRow): string {
  return String(r.team_name ?? r.opponent_name ?? r.opponent ?? r.team_id ?? "—");
}
