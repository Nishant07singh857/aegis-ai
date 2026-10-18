/**
 * AEGIS AI — broadcast director state client (Phase 7).
 *
 * W1 backend contract (GET/POST /api/v1/director/*, WS "director.state" /
 * "director.suggestion"):
 *   GET  /api/v1/director/state
 *     -> {"mode": "full_auto"|"semi_auto"|"manual",
 *         "scene": "main"|"standings"|"killfeed_focus"|"commentary",
 *         "overlays": {"scorebug": bool, "killfeed": bool},
 *         "lower_third": {"visible": bool, "title": str, "subtitle": str},
 *         "updated_by": str|null, "updated_at": str}
 *   GET  /api/v1/director/suggestions
 *     -> [{"id": str, "type": "scene"|"overlay"|"lower_third",
 *          "payload": {...}, "reason": str, "confidence": 0..1,
 *          "created_at": str, "expires_at": str, "status": "pending"}]
 *   POST /api/v1/director/mode        {"mode": "full_auto"|"semi_auto"|"manual"}
 *   POST /api/v1/director/scene       {"scene": ...}
 *   POST /api/v1/director/overlays    {"scorebug"?: bool, "killfeed"?: bool}
 *   POST /api/v1/director/lower-third {"visible": bool, "title"?: str, "subtitle"?: str}
 *   POST /api/v1/director/suggestions/{id}/approve
 *   POST /api/v1/director/suggestions/{id}/reject
 *
 * Scene/overlay/lower-third writes are rejected unless the director is in
 * MANUAL mode (409); the UI surfaces the backend's message honestly. No
 * mock data: every value comes from the real backend or a real WS frame;
 * a malformed state is rejected, never guessed.
 */

import { apiFetch } from "./api";

export type DirectorMode = "full_auto" | "semi_auto" | "manual";
export type DirectorScene = "main" | "standings" | "killfeed_focus" | "commentary";
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

export interface DirectorState {
  mode: DirectorMode;
  scene: DirectorScene;
  overlays: DirectorOverlays;
  lower_third: DirectorLowerThird;
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
];

export const DIRECTOR_SCENES: readonly DirectorScene[] = [
  "main",
  "standings",
  "killfeed_focus",
  "commentary",
];

export const SCENE_LABELS: Record<DirectorScene, string> = {
  main: "Main",
  standings: "Standings",
  killfeed_focus: "Killfeed focus",
  commentary: "Commentary",
};

/** Strict shape check: a malformed director state is rejected, not patched. */
export function isDirectorState(value: unknown): value is DirectorState {
  if (value === null || typeof value !== "object") return false;
  const s = value as Record<string, unknown>;
  if (
    s.mode !== "full_auto" &&
    s.mode !== "semi_auto" &&
    s.mode !== "manual"
  )
    return false;
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
  return true;
}

/** Normalise a validated state so optional text fields are always strings. */
export function normalizeDirectorState(state: DirectorState): DirectorState {
  return {
    ...state,
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
