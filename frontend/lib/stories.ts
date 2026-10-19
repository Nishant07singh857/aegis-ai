/**
 * AEGIS AI — AI storytelling client (Phase 19).
 *
 * Backend contract (GET/POST /api/v1/stories/* — W1):
 *   GET  /api/v1/stories/available?match_id=&type=&status=
 *     -> StoryResponse[] (bare JSON array)
 *   POST /api/v1/stories/generate
 *     {"match_id": str (required), "types": [str] (required, min 1),
 *      "context": {...}} -> StoryResponse[] (201, bare array)
 *   GET  /api/v1/stories/{id}            -> StoryResponse
 *   POST /api/v1/stories/{id}/trigger
 *     {"template_id"?: str, "animation_style"?: str} -> StoryResponse
 *   POST /api/v1/stories/{id}/dismiss    -> StoryResponse
 *   GET  /api/v1/stories/settings?tournament_id=
 *     -> {"tournament_id": str|null, "settings": [{story_type, template, animation_style}]}
 *   PUT  /api/v1/stories/settings
 *     {"tournament_id"?: str|null, "settings": [{story_type, template, animation_style}]}
 *     -> {"tournament_id": str|null, "settings": [...]}
 *
 * Backend story_type ids: head_to_head, win_probability, player_comparison,
 * team_form, zone_advantage, milestone, comeback.
 * Backend statuses: "generated" | "triggered" | "dismissed".
 *
 * Roles (enforced server-side; this client only surfaces answers):
 *   available/get/settings-GET : director, admin, operator
 *   generate/trigger/dismiss/settings-PUT : director, admin ONLY
 *
 * This module is the INTEGRATION TRANSLATION LAYER between the backend
 * contract above and W2's animated graphics contract
 * (frontend/components/graphics/animated/types.ts):
 *   - backend story_type ids -> W2 StoryType ids
 *     (head_to_head->h2h, team_form->form, zone_advantage->zone)
 *   - backend data payloads -> W2 component data shapes
 *   - backend statuses -> UI on_air flag
 *   - UI settings records -> backend settings payload
 *
 * No mock data: every value comes from the real backend; failures surface
 * the backend's message honestly. Nothing is invented — missing fields
 * become undefined and components render "—".
 */

import { apiFetch } from "./api";
import {
  STORY_TEMPLATES as W2_STORY_TEMPLATES,
  STORY_TYPE_LABELS as W2_STORY_TYPE_LABELS,
  type StoryType as W2StoryType,
} from "@/components/graphics/animated/types";

/* ------------------------------------------------------------------ */
/* Type-id translation: backend <-> W2                                 */
/* ------------------------------------------------------------------ */

/** Backend story_type -> W2 StoryType. */
const BACKEND_TO_W2_TYPE: Record<string, W2StoryType> = {
  head_to_head: "h2h",
  win_probability: "win_probability",
  player_comparison: "player_comparison",
  team_form: "form",
  zone_advantage: "zone",
  milestone: "milestone",
  comeback: "comeback",
};

/** W2 StoryType -> backend story_type. */
const W2_TO_BACKEND_TYPE: Record<string, string> = Object.fromEntries(
  Object.entries(BACKEND_TO_W2_TYPE).map(([b, w]) => [w, b])
);

/** All 7 backend story_type ids (for generate requests). */
export const BACKEND_STORY_TYPES: readonly string[] = Object.keys(
  BACKEND_TO_W2_TYPE
);

/** Map a backend story_type to the W2 renderer id (or null if unknown). */
export function toW2StoryType(backendType: string): W2StoryType | null {
  const w = BACKEND_TO_W2_TYPE[backendType];
  return w ?? null;
}

/** Map a W2 renderer id back to the backend story_type. */
export function toBackendStoryType(w2Type: string): string {
  return W2_TO_BACKEND_TYPE[w2Type] ?? w2Type;
}

/** The 7 story types W2's animated components render. */
export type { W2StoryType as StoryType };

const W2_STORY_TYPES: readonly string[] = Object.keys(W2_STORY_TEMPLATES);

export function isKnownStoryType(t: string): t is W2StoryType {
  return (W2_STORY_TYPES as readonly string[]).includes(t);
}

/** Canonical type label from W2's registry; falls back to the raw key. */
export function storyTypeLabel(type: string): string {
  return isKnownStoryType(type) ? W2_STORY_TYPE_LABELS[type] : type;
}

/**
 * Layout template ids for a story type, straight from W2's template
 * registry (2–3 per type). Unknown types get the generic fallback set so
 * the picker still works honestly.
 */
export function templatesForStoryType(type: string): readonly string[] {
  if (isKnownStoryType(type)) return W2_STORY_TEMPLATES[type];
  return ["classic", "modern", "minimal"];
}

/** Human-friendly template label, e.g. "side-by-side" → "Side by side". */
export function templateLabel(templateId: string): string {
  return templateId
    .split(/[-_]/g)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/* ------------------------------------------------------------------ */
/* Story shapes                                                        */
/* ------------------------------------------------------------------ */

/** Raw backend StoryResponse (W1 contract). */
export interface BackendStoryResponse {
  id: string;
  match_id?: string | null;
  tournament_id?: string | null;
  story_type: string;
  title: string;
  body: string;
  data?: Record<string, unknown> | null;
  confidence?: number | null;
  status: string;
  generated_at?: string | null;
  triggered_by?: string | null;
}

/** Story lifecycle state as reported by the backend. */
export type BackendStoryStatus = "generated" | "triggered" | "dismissed";

/**
 * UI story: backend response translated to W2's graphics contract.
 * `type` is the W2 renderer id; `backendType` keeps the raw backend id
 * for settings mapping. `data` is translated to the W2 component shape;
 * untranslatable fields are dropped (components render "—", never fakes).
 */
export interface Story {
  id: string;
  /** W2 renderer id (h2h | win_probability | player_comparison | form | zone | milestone | comeback). */
  type: string;
  /** Raw backend story_type (head_to_head | ... | comeback). */
  backendType: string;
  type_label?: string | null;
  title: string;
  body: string;
  /** Translated to the W2 component data shape. */
  data: Record<string, unknown>;
  status: string;
  /** True when backend status === "triggered". */
  on_air: boolean;
  match_id?: string | null;
  tournament_id?: string | null;
  confidence?: number | null;
  generated_at?: string | null;
  triggered_by?: string | null;
}

export interface StoryTemplateOption {
  id: string;
  label: string;
}

/**
 * Template options for a story, as {id,label} pairs for the picker:
 * W2's registry ids (2–3 per known type) with generated labels.
 */
export function templateOptionsForStory(
  story: Pick<Story, "type">
): readonly StoryTemplateOption[] {
  return templatesForStoryType(story.type).map((id) => ({
    id,
    label: templateLabel(id),
  }));
}

export interface AvailableStoriesResponse {
  stories: Story[];
  match_id?: string | null;
}

export interface GenerateStoriesResult {
  generated: number;
  stories: Story[];
}

/**
 * Per-tournament storytelling defaults (Phase 19 settings panel):
 * default layout template + default animation style per story type,
 * keyed by W2 renderer id in the UI.
 */
export interface StorySettings {
  tournament_id?: string | null;
  default_template: Record<string, string>;
  animation_style: Record<string, string>;
}

/** Animation styles the preview pane can render (W2 animation system). */
export const STORY_ANIMATIONS = ["slide", "fade", "scale", "none"] as const;
export type StoryAnimation = (typeof STORY_ANIMATIONS)[number];

export function isStoryAnimation(v: unknown): v is StoryAnimation {
  return (
    typeof v === "string" &&
    (STORY_ANIMATIONS as readonly string[]).includes(v)
  );
}

/* ------------------------------------------------------------------ */
/* Data translation: backend payloads -> W2 component shapes           */
/* ------------------------------------------------------------------ */

function asRecord(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object"
    ? (v as Record<string, unknown>)
    : {};
}

function asString(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}

function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

/**
 * Translate one backend story `data` dict to the W2 component shape for
 * its type. Only real backend fields are mapped; anything missing stays
 * undefined so components render "—". Composed strings (notes/evidence)
 * are formatted from real fields, never invented.
 */
function translateStoryData(
  backendType: string,
  raw: Record<string, unknown>
): Record<string, unknown> {
  switch (backendType) {
    case "head_to_head": {
      // -> H2HData { player_a, player_b, winner?, note? }
      const a = asRecord(raw.entity_a);
      const b = asRecord(raw.entity_b);
      const winsA = asNumber(a.wins);
      const winsB = asNumber(b.wins);
      const winner =
        winsA !== null && winsB !== null
          ? winsA > winsB
            ? "a"
            : winsB > winsA
              ? "b"
              : undefined
          : undefined;
      return {
        player_a: { name: str(a.name, "—") },
        player_b: { name: str(b.name, "—") },
        ...(winner ? { winner } : {}),
        ...(asString(raw.key_stat) ? { note: raw.key_stat } : {}),
      };
    }
    case "win_probability": {
      // -> WinProbData { teams: [{team_tag, probability}], delta_note? }
      const probs = Array.isArray(raw.probabilities)
        ? (raw.probabilities as unknown[])
        : [];
      const teams = probs.map((p) => {
        const r = asRecord(p);
        return {
          team_tag: str(r.team_name, "—"),
          probability: asNumber(r.win_probability),
        };
      });
      const leader = probs.length > 0 ? asRecord(probs[0]) : {};
      const delta = asNumber(leader.delta);
      return {
        teams,
        ...(delta !== null
          ? {
              delta_note: `${delta > 0 ? "+" : ""}${delta}% pichhle calc se`,
            }
          : {}),
      };
    }
    case "player_comparison": {
      // -> H2HData (same renderer)
      const pa = asRecord(raw.player_a);
      const pb = asRecord(raw.player_b);
      const pick = (r: Record<string, unknown>) => ({
        name: str(r.name, "—"),
        finishes: asNumber(r.finishes),
        damage: asNumber(r.avg_damage),
        rating: asNumber(r.rating),
        clutches: asNumber(r.clutches),
      });
      const fa = asNumber(pa.finishes);
      const fb = asNumber(pb.finishes);
      const winner =
        fa !== null && fb !== null
          ? fa > fb
            ? "a"
            : fb > fa
              ? "b"
              : undefined
          : undefined;
      return {
        player_a: pick(pa),
        player_b: pick(pb),
        ...(winner ? { winner } : {}),
      };
    }
    case "team_form": {
      // -> FormData { team_tag, results, note? }
      const results = Array.isArray(raw.results)
        ? (raw.results as unknown[]).filter(
            (r): r is "W" | "L" | "D" =>
              r === "W" || r === "L" || r === "D"
          )
        : [];
      return {
        team_tag: str(raw.team_name, "—"),
        results,
        ...(asString(raw.form_label) ? { note: raw.form_label } : {}),
      };
    }
    case "zone_advantage": {
      // -> ZoneData { zone_number?, team_tag?, evidence? }
      const similar = asNumber(raw.similar_zones);
      const top3 = asNumber(raw.top3_count);
      const evidence =
        similar !== null && top3 !== null
          ? `pichhle ${similar} similar zones me ${top3} baar top-3`
          : undefined;
      return {
        zone_number: asNumber(raw.zone_phase),
        team_tag: asString(raw.favored_team_name),
        ...(evidence ? { evidence } : {}),
      };
    }
    case "milestone": {
      // -> MilestoneData { player?, team_tag?, milestone? }
      const remaining = asNumber(raw.remaining);
      const target = asNumber(raw.target);
      const kind = str(raw.kind, "milestone");
      const milestone =
        remaining !== null && target !== null
          ? `${target} ${kind} se ${remaining} door`
          : undefined;
      return {
        player: asString(raw.player_name),
        ...(milestone ? { milestone } : {}),
      };
    }
    case "comeback": {
      // W2 renders comeback via FormGuide, which needs W/L results the
      // backend does not provide (only placements). Pass the honest
      // narrative fields; results stay empty so the component shows its
      // honest WaitingFeed state instead of invented W/L badges.
      const jump = asNumber(raw.jump);
      const fromP = asNumber(raw.from_placement);
      const toP = asNumber(raw.to_placement);
      const note =
        fromP !== null && toP !== null && jump !== null
          ? `${fromP} se ${toP} — ${jump} position ka jump`
          : undefined;
      return {
        team_tag: asString(raw.team_name),
        results: [],
        ...(note ? { note } : {}),
      };
    }
    default:
      return { ...raw };
  }
}

/**
 * Translate a raw backend StoryResponse to the UI Story shape.
 * Unknown backend types are kept with their raw id (panel falls back
 * honestly); data passes through untranslated.
 */
export function toUIStory(raw: BackendStoryResponse): Story {
  const w2type = toW2StoryType(raw.story_type);
  const data = asRecord(raw.data);
  return {
    id: raw.id,
    type: w2type ?? raw.story_type,
    backendType: raw.story_type,
    title: raw.title,
    body: raw.body,
    data: w2type ? translateStoryData(raw.story_type, data) : data,
    status: raw.status,
    on_air: raw.status === "triggered",
    match_id: raw.match_id ?? null,
    tournament_id: raw.tournament_id ?? null,
    confidence: raw.confidence ?? null,
    generated_at: raw.generated_at ?? null,
    triggered_by: raw.triggered_by ?? null,
  };
}

function asStoryArray(data: unknown): Story[] {
  const raws: unknown[] = Array.isArray(data)
    ? data
    : data !== null && typeof data === "object" &&
        Array.isArray((data as Record<string, unknown>).stories)
      ? ((data as Record<string, unknown>).stories as unknown[])
      : [];
  return raws
    .filter(
      (r): r is BackendStoryResponse =>
        r !== null &&
        typeof r === "object" &&
        typeof (r as Record<string, unknown>).id === "string" &&
        typeof (r as Record<string, unknown>).story_type === "string"
    )
    .map(toUIStory);
}

/* ------------------------------------------------------------------ */
/* Settings translation: UI records <-> backend settings array        */
/* ------------------------------------------------------------------ */

function normalizeSettings(data: unknown): StorySettings {
  const rec =
    data !== null && typeof data === "object"
      ? (data as Record<string, unknown>)
      : {};
  const default_template: Record<string, string> = {};
  const animation_style: Record<string, string> = {};
  const items = Array.isArray(rec.settings)
    ? (rec.settings as unknown[])
    : [];
  for (const it of items) {
    const r = asRecord(it);
    const backendType = asString(r.story_type);
    if (!backendType) continue;
    const w2 = toW2StoryType(backendType) ?? backendType;
    const tpl = asString(r.template);
    const anim = asString(r.animation_style);
    if (tpl) default_template[w2] = tpl;
    if (anim && isStoryAnimation(anim)) animation_style[w2] = anim;
  }
  return {
    tournament_id:
      typeof rec.tournament_id === "string" ? rec.tournament_id : null,
    default_template,
    animation_style,
  };
}

function toBackendSettingsPayload(settings: StorySettings): {
  tournament_id: string | null;
  settings: Array<{
    story_type: string;
    template: string;
    animation_style: string;
  }>;
} {
  const keys = new Set([
    ...Object.keys(settings.default_template),
    ...Object.keys(settings.animation_style),
  ]);
  const items: Array<{
    story_type: string;
    template: string;
    animation_style: string;
  }> = [];
  for (const w2key of keys) {
    const template = settings.default_template[w2key];
    const animation_style = settings.animation_style[w2key];
    if (!template || !animation_style) continue;
    items.push({
      story_type: toBackendStoryType(w2key),
      template,
      animation_style,
    });
  }
  return { tournament_id: settings.tournament_id ?? null, settings: items };
}

/* ------------------------------------------------------------------ */
/* API calls                                                           */
/* ------------------------------------------------------------------ */

function qs(matchId?: string | null): string {
  return matchId ? `?match_id=${encodeURIComponent(matchId)}` : "";
}

/** GET /api/v1/stories/available — director, admin, operator. */
export async function getAvailableStories(
  token: string | null,
  matchId?: string | null
): Promise<AvailableStoriesResponse> {
  const data = await apiFetch<unknown>(
    `/api/v1/stories/available${qs(matchId)}`,
    { token: token ?? undefined }
  );
  return {
    stories: asStoryArray(data),
    match_id:
      data !== null && typeof data === "object"
        ? ((data as { match_id?: unknown }).match_id as string | null) ?? null
        : null,
  };
}

export interface GenerateOptions {
  /** Backend story_type ids; defaults to all 7. */
  types?: string[];
  /** Extra entity ids per type (entity_a_id, player_a_id, team_id, ...). */
  context?: Record<string, unknown>;
}

/**
 * POST /api/v1/stories/generate — director, admin.
 * match_id is REQUIRED by the backend (422 otherwise), so callers must
 * supply a real match id — never an invented one.
 */
export async function generateStories(
  token: string | null,
  matchId: string,
  opts?: GenerateOptions
): Promise<GenerateStoriesResult> {
  const data = await apiFetch<unknown>("/api/v1/stories/generate", {
    method: "POST",
    token: token ?? undefined,
    body: {
      match_id: matchId,
      types: opts?.types ?? [...BACKEND_STORY_TYPES],
      context: opts?.context ?? {},
    },
  });
  const stories = asStoryArray(data);
  return { generated: stories.length, stories };
}

export interface TriggerOptions {
  template_id?: string;
  animation_style?: StoryAnimation;
}

/**
 * POST /api/v1/stories/{id}/trigger — director, admin.
 * Puts the story on the broadcast output. The chosen template/animation
 * ride in the request body and are published on the story.trigger event.
 */
export async function triggerStory(
  token: string | null,
  storyId: string,
  opts?: TriggerOptions
): Promise<Story | null> {
  const body: Record<string, string> = {};
  if (opts?.template_id) body.template_id = opts.template_id;
  if (opts?.animation_style) body.animation_style = opts.animation_style;
  const data = await apiFetch<unknown>(
    `/api/v1/stories/${encodeURIComponent(storyId)}/trigger`,
    {
      method: "POST",
      token: token ?? undefined,
      body: Object.keys(body).length > 0 ? body : {},
    }
  );
  if (data !== null && typeof data === "object") {
    return toUIStory(data as BackendStoryResponse);
  }
  return null;
}

/**
 * POST /api/v1/stories/{id}/dismiss — director, admin.
 * Removes the story from the broadcast output.
 */
export async function dismissStory(
  token: string | null,
  storyId: string
): Promise<void> {
  await apiFetch<unknown>(
    `/api/v1/stories/${encodeURIComponent(storyId)}/dismiss`,
    { method: "POST", token: token ?? undefined, body: {} }
  );
}

/** GET /api/v1/stories/settings — director, admin. */
export async function getStorySettings(
  token: string | null,
  tournamentId?: string | null
): Promise<StorySettings> {
  const data = await apiFetch<unknown>(
    `/api/v1/stories/settings${tournamentId ? `?tournament_id=${encodeURIComponent(tournamentId)}` : ""}`,
    { token: token ?? undefined }
  );
  return normalizeSettings(data);
}

/** PUT /api/v1/stories/settings — director, admin. */
export async function putStorySettings(
  token: string | null,
  settings: StorySettings
): Promise<StorySettings> {
  const data = await apiFetch<unknown>("/api/v1/stories/settings", {
    method: "PUT",
    token: token ?? undefined,
    body: toBackendSettingsPayload(settings),
  });
  return normalizeSettings(data);
}
