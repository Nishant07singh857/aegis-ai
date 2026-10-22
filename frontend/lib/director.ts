/**
 * AEGIS AI — broadcast director state client (Phase 7).
 *
 * W1 backend contract (GET/POST /api/v1/director/*, WS "director.state" /
 * "director.suggestion"):
 *   GET  /api/v1/director/state
 *     -> {"mode": "full_auto"|"semi_auto"|"manual"|"clone",
 *         "clone_grabbed": bool, "clone_grabbed_by": str|null,
 *         "clone_grabbed_at": str|null
 *           (Phase 27: clone-mode manual grab — while grabbed the human
 *           drives and the AI is silent; absent on older backends),
 *         "scene": "main"|"standings"|"killfeed_focus"|"commentary"|"map_cam",
 *         "scene_entered_at": str (Phase 18: ISO of the last scene change;
 *           drives the map-cam 30s maximum hold),
 *         "preview": {"slot": int 1..16, "scene": str|null,
 *                     "reason": str, "score": number|null} | null
 *           (Phase 25 P0-3: the decision loop's next candidate feed,
 *           pre-positioned in the director multiview's PVW tile. Absent /
 *           null on backends predating the field — the PVW tile then shows
 *           an honest "No preview candidate" standby, never a dummy feed.),
 *         "overlays": {"scorebug": bool, "killfeed": bool},
 *         "lower_third": {"visible": bool, "title": str, "subtitle": str},
 *         "graphics": {"scorebug_template_id": str,
 *                      "lowerthird_template_id": str,
 *                      "standings_template_id": str,
 *                      "killfeed_template_id": str},
 *         "updated_by": str|null, "updated_at": str}
 *   GET  /api/v1/director/suggestions
 *     -> [{"id": str, "type": "scene"|"overlay"|"lower_third",
 *          "payload": {...}, "reason": str, "confidence": 0..1,
 *          "created_at": str, "expires_at": str, "status": "pending"}]
 *   POST /api/v1/director/mode        {"mode": "full_auto"|"semi_auto"|"manual"|"clone"}
 *   POST /api/v1/director/clone/grab     (Phase 27, CLONE mode only; 409 outside)
 *   POST /api/v1/director/clone/release  (Phase 27; no-op state when not grabbed)
 *   POST /api/v1/director/scene       {"scene": ...}
 *   POST /api/v1/director/camera      {"slot": 1..16} (Phase 17 W2, MANUAL)
 *   POST /api/v1/director/overlays    {"scorebug"?: bool, "killfeed"?: bool}
 *   POST /api/v1/director/lower-third {"visible": bool, "title"?: str, "subtitle"?: str}
 *   POST /api/v1/director/suggestions/{id}/approve
 *   POST /api/v1/director/suggestions/{id}/reject
 *
 * Graphics template selection (Phase 8):
 *   POST /api/v1/director/graphics
 *     {"scorebug_template_id"?: str, "lowerthird_template_id"?: str,
 *      "standings_template_id"?: str, "killfeed_template_id"?: str}
 *     Operator+; allowed in SEMI_AUTO and MANUAL mode, rejected with 409
 *     in FULL_AUTO (unlike scene/overlay/lower-third writes, which need
 *     MANUAL). Unknown template ids are rejected with 422. At least one
 *     id is required. The new graphics ship in the "director.state" WS
 *     broadcast like every other director change.
 *
 * Scene/overlay/lower-third writes are rejected unless the director is in
 * MANUAL mode (409); the UI surfaces the backend's message honestly. No
 * mock data: every value comes from the real backend or a real WS frame;
 * a malformed state is rejected, never guessed.
 */

import { apiFetch } from "./api";

export type DirectorMode = "full_auto" | "semi_auto" | "manual" | "clone";

/**
 * Fresh-show default mode (Phase 28) — mirrors the backend canonical
 * default (director_service.DEFAULT_DIRECTOR_MODE, env-overridable via
 * AEGIS_DIRECTOR_DEFAULT_MODE). Used only when no state has loaded yet:
 * AI salaah deta hai, insaan ka aakhri faisla — a new show starts in
 * semi_auto until the operator explicitly picks another mode.
 */
export const DEFAULT_DIRECTOR_MODE: DirectorMode = "semi_auto";
// Phase 18 (W3): "map_cam" is the tactical-overview scene (Visual
// Broadcast Bible §2). It auto-returns to "main" after 30s max in every
// mode — see MAP_CAM_MAX_HOLD_SECONDS.
export type DirectorScene =
  | "main"
  | "standings"
  | "killfeed_focus"
  | "commentary"
  | "map_cam";

/**
 * Bible §2: map cam holds 15-30s, then a hard cut back to action. The
 * backend enforces the 30s maximum in every mode; the frontend shows the
 * countdown and re-polls state at expiry.
 */
export const MAP_CAM_MAX_HOLD_SECONDS = 30;
export type SuggestionType = "scene" | "overlay" | "lower_third";

export interface DirectorOverlays {
  scorebug: boolean;
  killfeed: boolean;
}

export interface DirectorLowerThird {
  visible: boolean;
  title: string;
  subtitle: string;
}

/**
 * Active graphics template per type (Phase 8). Keys match the backend
 * DirectorGraphics schema exactly. The frontend never invents ids: every
 * value comes from the backend state or a ?template= override resolved
 * against the template registry.
 */
export interface DirectorGraphics {
  scorebug_template_id: string;
  lowerthird_template_id: string;
  standings_template_id: string;
  killfeed_template_id: string;
}

/**
 * Phase 25 (P0-3): the decision loop's preview candidate — the feed the
 * director will cut to next, pre-positioned in the multiview PVW tile.
 *
 * Backend contract (Phase 25 W1, verified against
 * DirectorService._default_state + LiveDirectorService._update_preview):
 * `preview: {slot, scene, team_id, fight_id, reason, score, breakdown,
 * updated_at} | null`.
 *   slot    observer camera slot 1..16 of the candidate feed (required)
 *   scene   scene the cut would land on ("main" today)
 *   team_id candidate team id (id, not display name — the tile prefers
 *           the observer mapping's team name and falls back to this)
 *   reason  human-readable pipeline reason, e.g.
 *           "Top candidate: <team_id> (score 82.0)"
 *   score   unified Interest Score, 0..100 float (a 0..1 fraction is
 *           also accepted and rendered as a percentage — see
 *           formatPreviewScore). Null when the pipeline reports none.
 *
 * Absent/null on older backends: normalizeDirectorState maps every
 * missing or malformed preview to null and the PVW tile shows an honest
 * standby — a preview is never invented client-side.
 */
export interface DirectorPreview {
  slot: number;
  scene: string | null;
  reason: string;
  score: number | null;
  /** Candidate team id from the pipeline (null when unreported). */
  team_id: string | null;
  /** Fight the candidate belongs to (null for heat/intel candidates). */
  fight_id: string | null;
}

/** Fresh-state defaults — mirrors the backend GRAPHICS_TEMPLATE_DEFAULTS. */
export const DEFAULT_DIRECTOR_GRAPHICS: DirectorGraphics = {
  scorebug_template_id: "scorebug-classic",
  lowerthird_template_id: "lowerthird-slide",
  standings_template_id: "standings-table",
  killfeed_template_id: "killfeed-ticker",
};

export interface DirectorState {
  mode: DirectorMode;
  scene: DirectorScene;
  /**
   * Phase 18 (W3): ISO timestamp of the last scene change (backend stamps
   * it on every real scene change). Drives the map-cam auto-return
   * countdown. "" when the backend predates the field.
   */
  scene_entered_at: string;
  overlays: DirectorOverlays;
  lower_third: DirectorLowerThird;
  /**
   * Present on every state after normalizeDirectorState (defaults to 1 —
   * the backend predating Phase 17 W2 emits no active_camera block).
   * Phase 17 W2: the active observer camera slot (1..16) shown on the
   * broadcast output video layer (GET /api/v1/video/feed/{slot}).
   */
  active_camera: number;
  /**
   * Present on every state after normalizeDirectorState (defaults are
   * filled in when the backend payload predates Phase 8). Consumers can
   * read it directly — no undefined checks needed post-normalisation.
   */
  graphics: DirectorGraphics;
  /**
   * Phase 25 (P0-3): preview candidate for the PVW tile. Present on
   * every state after normalizeDirectorState — null when the backend
   * has no candidate (or predates the field). Never undefined, never
   * fabricated.
   */
  preview: DirectorPreview | null;
  /**
   * Phase 27: clone-mode manual grab. Present on every state after
   * normalizeDirectorState (defaults filled in for older backends:
   * not grabbed, nobody, no stamp). Only meaningful in clone mode.
   */
  clone_grabbed: boolean;
  clone_grabbed_by: string | null;
  clone_grabbed_at: string | null;
  updated_by: string | null;
  updated_at: string;
}

export const DIRECTOR_MODE_OPTIONS: readonly {
  value: DirectorMode;
  label: string;
  description: string;
}[] = [
  {
    value: "full_auto",
    label: "Full Auto",
    description: "AI runs the broadcast",
  },
  {
    value: "semi_auto",
    label: "Semi-Auto",
    description: "AI suggests, you approve",
  },
  {
    value: "manual",
    label: "Manual",
    description: "You control everything",
  },
  {
    value: "clone",
    label: "Director Style — Clone",
    description: "AI drives with your learned style",
  },
];

export const DIRECTOR_SCENES: readonly DirectorScene[] = [
  "main",
  "standings",
  "killfeed_focus",
  "commentary",
  "map_cam",
];

export const SCENE_LABELS: Record<DirectorScene, string> = {
  main: "Main",
  standings: "Standings",
  killfeed_focus: "Killfeed focus",
  commentary: "Commentary",
  map_cam: "Map cam",
};

/** Strict shape check: a malformed director state is rejected, not patched. */
export function isDirectorState(value: unknown): value is DirectorState {
  if (value === null || typeof value !== "object") return false;
  const s = value as Record<string, unknown>;
  if (
    s.mode !== "full_auto" &&
    s.mode !== "semi_auto" &&
    s.mode !== "manual" &&
    s.mode !== "clone"
  )
    return false;
  // clone grab fields (Phase 27) are optional for backward
  // compatibility; when present they must be bool / string-or-null.
  if (
    s.clone_grabbed !== undefined &&
    s.clone_grabbed !== null &&
    typeof s.clone_grabbed !== "boolean"
  )
    return false;
  for (const key of ["clone_grabbed_by", "clone_grabbed_at"] as const) {
    const v = s[key];
    if (v !== undefined && v !== null && typeof v !== "string") return false;
  }
  if (typeof s.scene !== "string" || !DIRECTOR_SCENES.includes(s.scene as DirectorScene)) {
    return false;
  }
  const ov = s.overlays;
  if (ov === null || typeof ov !== "object") return false;
  const overlays = ov as Record<string, unknown>;
  if (typeof overlays.scorebug !== "boolean") return false;
  if (typeof overlays.killfeed !== "boolean") return false;
  const lt = s.lower_third;
  if (lt === null || typeof lt !== "object") return false;
  const lowerThird = lt as Record<string, unknown>;
  if (typeof lowerThird.visible !== "boolean") return false;
  // title/subtitle default to "" when absent; visible=false must never
  // crash the renderer.
  // Phase 18 (W3): scene_entered_at is optional for backward
  // compatibility (a backend predating the field emits no stamp); when
  // present it must be a string.
  const enteredAt = s.scene_entered_at;
  if (
    enteredAt !== undefined &&
    enteredAt !== null &&
    typeof enteredAt !== "string"
  )
    return false;
  // active_camera is optional for backward compatibility (a backend
  // predating Phase 17 W2 emits no active_camera field); when present it
  // must be an integer slot 1..16.
  const cam = s.active_camera;
  if (
    cam !== undefined &&
    cam !== null &&
    !(typeof cam === "number" && Number.isInteger(cam) && cam >= 1 && cam <= 16)
  )
    return false;
  // graphics is optional for backward compatibility (a backend predating
  // Phase 8 emits no graphics block); when present it must be an object
  // whose known keys are strings. Unknown ids are NOT rejected here —
  // the template registry falls back to the per-type default at render.
  const g = s.graphics;
  if (g !== undefined && g !== null) {
    if (typeof g !== "object") return false;
    const graphics = g as Record<string, unknown>;
    for (const key of [
      "scorebug_template_id",
      "lowerthird_template_id",
      "standings_template_id",
      "killfeed_template_id",
    ] as const) {
      const v = graphics[key];
      if (v !== undefined && typeof v !== "string") return false;
    }
  }
  // preview (Phase 25) is deliberately NOT validated here: a malformed
  // preview must never reject an otherwise-good director state. It is
  // sanitised to null by normalizeDirectorState instead.
  return true;
}

/** Strict shape check for the graphics block of a director state. */
export function isDirectorGraphics(value: unknown): value is DirectorGraphics {
  if (value === null || typeof value !== "object") return false;
  const g = value as Record<string, unknown>;
  return (
    typeof g.scorebug_template_id === "string" &&
    typeof g.lowerthird_template_id === "string" &&
    typeof g.standings_template_id === "string" &&
    typeof g.killfeed_template_id === "string"
  );
}

/**
 * Sanitise the raw `preview` field of a director state (Phase 25).
 * Returns a DirectorPreview only when the payload carries a valid
 * observer slot (integer 1..16); every other shape — absent, null,
 * malformed — becomes null (the PVW tile's honest standby). The scene
 * is kept only when it names a known director scene; reason defaults
 * to "" and score to null rather than an invented value.
 */
export function normalizeDirectorPreview(raw: unknown): DirectorPreview | null {
  if (raw === null || raw === undefined || typeof raw !== "object") {
    return null;
  }
  const p = raw as Record<string, unknown>;
  const slot = p.slot;
  if (
    typeof slot !== "number" ||
    !Number.isInteger(slot) ||
    slot < 1 ||
    slot > 16
  ) {
    return null;
  }
  const scene =
    typeof p.scene === "string" &&
    (DIRECTOR_SCENES as readonly string[]).includes(p.scene)
      ? p.scene
      : null;
  const reason = typeof p.reason === "string" ? p.reason : "";
  const score =
    typeof p.score === "number" && Number.isFinite(p.score) ? p.score : null;
  const team_id =
    typeof p.team_id === "string" && p.team_id ? p.team_id : null;
  const fight_id =
    typeof p.fight_id === "string" && p.fight_id ? p.fight_id : null;
  return { slot, scene, reason, score, team_id, fight_id };
}

/**
 * Display form of a preview score. The decision pipeline's unified
 * score is 0..100 (POVSelector convention); a 0..1 fraction is rendered
 * as the equivalent percentage. Null stays null — callers omit the
 * score entirely instead of showing a guessed number.
 */
export function formatPreviewScore(score: number | null): string | null {
  if (score === null || !Number.isFinite(score)) return null;
  return String(Math.round(score <= 1 ? score * 100 : score));
}

/** Normalise a validated state so optional text fields are always strings. */
export function normalizeDirectorState(state: DirectorState): DirectorState {
  const rawGraphics = (state as { graphics?: unknown }).graphics;
  const graphics: DirectorGraphics = { ...DEFAULT_DIRECTOR_GRAPHICS };
  if (rawGraphics !== null && typeof rawGraphics === "object") {
    const g = rawGraphics as Record<string, unknown>;
    (Object.keys(graphics) as (keyof DirectorGraphics)[]).forEach((key) => {
      const v = g[key];
      // Empty/unknown ids are kept as-is when non-empty; the registry
      // falls back to the per-type default at render time.
      if (typeof v === "string" && v) graphics[key] = v;
    });
  }
  // Phase 17 W2: default to camera slot 1 when the backend payload
  // predates the active_camera field.
  const rawCam = (state as { active_camera?: unknown }).active_camera;
  const active_camera =
    typeof rawCam === "number" &&
    Number.isInteger(rawCam) &&
    rawCam >= 1 &&
    rawCam <= 16
      ? rawCam
      : 1;
  // Phase 18 (W3): default the scene-change stamp to "" when the
  // backend payload predates it; the map-cam countdown falls back to the
  // local render time in that case.
  const rawEnteredAt = (state as { scene_entered_at?: unknown })
    .scene_entered_at;
  const scene_entered_at =
    typeof rawEnteredAt === "string" ? rawEnteredAt : "";
  // Phase 25 (P0-3): sanitise the preview candidate (null when the
  // backend has none or predates the field).
  const preview = normalizeDirectorPreview(
    (state as { preview?: unknown }).preview
  );
  // Phase 27: clone grab fields default honestly (not grabbed) when
  // the backend payload predates them.
  const rawGrab = state as {
    clone_grabbed?: unknown;
    clone_grabbed_by?: unknown;
    clone_grabbed_at?: unknown;
  };
  const clone_grabbed = rawGrab.clone_grabbed === true;
  const clone_grabbed_by =
    typeof rawGrab.clone_grabbed_by === "string" && rawGrab.clone_grabbed_by
      ? rawGrab.clone_grabbed_by
      : null;
  const clone_grabbed_at =
    typeof rawGrab.clone_grabbed_at === "string" && rawGrab.clone_grabbed_at
      ? rawGrab.clone_grabbed_at
      : null;
  return {
    ...state,
    graphics,
    active_camera,
    scene_entered_at,
    preview,
    clone_grabbed,
    clone_grabbed_by,
    clone_grabbed_at,
    lower_third: {
      visible: state.lower_third.visible,
      title:
        typeof state.lower_third.title === "string" ? state.lower_third.title : "",
      subtitle:
        typeof state.lower_third.subtitle === "string"
          ? state.lower_third.subtitle
          : "",
    },
    updated_by: state.updated_by ?? null,
    updated_at: typeof state.updated_at === "string" ? state.updated_at : "",
  };
}

function unwrapState(res: unknown): unknown {
  if (res !== null && typeof res === "object" && "data" in res) {
    return (res as { data: unknown }).data;
  }
  return res;
}

function requireState(res: unknown): DirectorState {
  const raw = unwrapState(res);
  if (!isDirectorState(raw)) {
    throw new Error(
      "The director service returned an unexpected state shape — not applying it."
    );
  }
  return normalizeDirectorState(raw);
}

/** GET /api/v1/director/state via the configured API base. */
export async function getDirectorState(token?: string | null): Promise<DirectorState> {
  const res = await apiFetch<unknown>("/api/v1/director/state", {
    token: token ?? undefined,
  });
  return requireState(res);
}

/**
 * Same GET against an explicit API base (overlay pages support ?api=, which
 * apiFetch's build-time base cannot express). Auth is optional: the output
 * page may be captured by an unauthenticated OBS source and relies on the
 * WS director.state frames in that case.
 */
export async function getDirectorStateFrom(
  apiBase: string,
  token?: string | null
): Promise<DirectorState> {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(
    `${apiBase.replace(/\/+$/, "")}/api/v1/director/state`,
    { headers }
  );
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const data: unknown = await response.json();
      if (data !== null && typeof data === "object") {
        const record = data as Record<string, unknown>;
        for (const key of ["error", "detail", "message"]) {
          if (typeof record[key] === "string" && (record[key] as string)) {
            message = record[key] as string;
            break;
          }
        }
      }
    } catch {
      // Keep the HTTP status fallback.
    }
    throw new Error(message);
  }
  return requireState(await response.json());
}

/** POST /api/v1/director/mode */
export async function postDirectorMode(
  token: string | null,
  mode: DirectorMode
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director/mode", {
    method: "POST",
    token: token ?? undefined,
    body: { mode },
  });
}

/**
 * POST /api/v1/director/clone/grab (Phase 27) — grab manual control
 * inside CLONE mode. The backend 409s outside clone mode and surfaces
 * its message; the actor is the bearer token's user (no body).
 */
export async function postDirectorCloneGrab(
  token: string | null
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director/clone/grab", {
    method: "POST",
    token: token ?? undefined,
  });
}

/**
 * POST /api/v1/director/clone/release (Phase 27) — release a clone
 * grab; the clone director resumes with the learned profile. When
 * nothing is grabbed the backend returns the unchanged state.
 */
export async function postDirectorCloneRelease(
  token: string | null
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director/clone/release", {
    method: "POST",
    token: token ?? undefined,
  });
}

/** POST /api/v1/director/scene */
export async function postDirectorScene(
  token: string | null,
  scene: DirectorScene
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director/scene", {
    method: "POST",
    token: token ?? undefined,
    body: { scene },
  });
}

/** POST /api/v1/director/overlays */
export async function postDirectorOverlays(
  token: string | null,
  overlays: { scorebug?: boolean; killfeed?: boolean }
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director/overlays", {
    method: "POST",
    token: token ?? undefined,
    body: overlays,
  });
}

/** POST /api/v1/director/lower-third */
export async function postDirectorLowerThird(
  token: string | null,
  lowerThird: { visible: boolean; title?: string; subtitle?: string }
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director/lower-third", {
    method: "POST",
    token: token ?? undefined,
    body: lowerThird,
  });
}

/**
 * POST /api/v1/director/graphics — set the active template for one or more
 * graphic types (Phase 8).
 *
 * Allowed in SEMI_AUTO and MANUAL mode; the backend answers 409 in
 * FULL_AUTO (the AI drives the output there) and 422 for unknown template
 * ids. apiFetch surfaces the backend's message, so the UI shows it
 * honestly instead of guessing.
 */
export async function setGraphics(
  token: string | null,
  graphics: Partial<DirectorGraphics>
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director/graphics", {
    method: "POST",
    token: token ?? undefined,
    body: graphics,
  });
}

/** POST /api/v1/director/camera (Phase 17 W2) */
export async function postDirectorCamera(
  token: string | null,
  slot: number
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director/camera", {
    method: "POST",
    token: token ?? undefined,
    body: { slot },
  });
}

/** Human-readable timestamp for the "last updated by/at" line. */
export function formatDirectorUpdatedAt(updatedAt: string): string {
  if (!updatedAt) return "—";
  const d = new Date(updatedAt);
  if (Number.isNaN(d.getTime())) return updatedAt;
  return d.toLocaleString();
}

/* ── AI suggestions (SEMI_AUTO mode) ─────────────────────────────── */

export interface DirectorSuggestionPayload {
  scene?: string;
  overlay?: string;
  visible?: boolean;
  title?: string;
  subtitle?: string;
  [key: string]: unknown;
}

export interface DirectorSuggestion {
  id: string;
  type: SuggestionType;
  payload: DirectorSuggestionPayload;
  reason: string;
  confidence: number;
  created_at: string;
  expires_at: string;
  status: string;
}

const SUGGESTION_TYPES: readonly string[] = ["scene", "overlay", "lower_third"];

/** Strict shape check for a director.suggestion WS frame / REST entry. */
export function isDirectorSuggestion(value: unknown): value is DirectorSuggestion {
  if (value === null || typeof value !== "object") return false;
  const s = value as Record<string, unknown>;
  if (typeof s.id !== "string" || !s.id) return false;
  if (typeof s.type !== "string" || !SUGGESTION_TYPES.includes(s.type)) return false;
  if (s.payload === null || typeof s.payload !== "object") return false;
  if (typeof s.reason !== "string" || !s.reason) return false;
  if (typeof s.confidence !== "number" || s.confidence < 0 || s.confidence > 1)
    return false;
  if (typeof s.created_at !== "string" || typeof s.expires_at !== "string")
    return false;
  if (typeof s.status !== "string" || !s.status) return false;
  return true;
}

/** Seconds until the suggestion expires (<= 0 when already expired). */
export function suggestionExpiresIn(s: DirectorSuggestion, nowMs: number): number {
  const end = new Date(s.expires_at).getTime();
  if (Number.isNaN(end)) return 0;
  return Math.max(0, Math.round((end - nowMs) / 1000));
}

/** Plain-words description of what the suggestion would do. */
export function describeSuggestion(s: DirectorSuggestion): string {
  const p = s.payload ?? {};
  if (s.type === "scene") {
    const scene = typeof p.scene === "string" ? p.scene : "";
    const label = SCENE_LABELS[scene as DirectorScene] ?? scene;
    return `Switch scene to ${label || "—"}`;
  }
  if (s.type === "overlay") {
    const overlay = typeof p.overlay === "string" ? p.overlay : "";
    return `${p.visible ? "Show" : "Hide"} ${overlay || "overlay"} overlay`;
  }
  if (typeof p.visible === "boolean" && !p.visible) return "Hide lower-third";
  const title = typeof p.title === "string" && p.title ? `"${p.title}"` : "lower-third";
  const subtitle =
    typeof p.subtitle === "string" && p.subtitle ? ` — ${p.subtitle}` : "";
  return `Show ${title}${subtitle}`;
}

function requireSuggestions(res: unknown): DirectorSuggestion[] {
  const raw = Array.isArray(res) ? res : unwrapState(res);
  if (!Array.isArray(raw)) {
    throw new Error(
      "The director service returned an unexpected suggestions shape — not applying it."
    );
  }
  return raw.filter(isDirectorSuggestion);
}

/** GET /api/v1/director/suggestions — pending AI proposals (operator+). */
export async function getDirectorSuggestions(
  token?: string | null
): Promise<DirectorSuggestion[]> {
  const res = await apiFetch<unknown>("/api/v1/director/suggestions", {
    token: token ?? undefined,
  });
  return requireSuggestions(res);
}

/** POST /api/v1/director/suggestions/{id}/approve */
export async function approveDirectorSuggestion(
  token: string | null,
  id: string
): Promise<void> {
  await apiFetch<unknown>(`/api/v1/director/suggestions/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    token: token ?? undefined,
  });
}

/** POST /api/v1/director/suggestions/{id}/reject */
export async function rejectDirectorSuggestion(
  token: string | null,
  id: string
): Promise<void> {
  await apiFetch<unknown>(`/api/v1/director/suggestions/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    token: token ?? undefined,
  });
}
