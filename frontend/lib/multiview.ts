/**
 * AEGIS AI — director multiview clients (Phase 25, P0-3 / P1-7).
 *
 * Typed clients for the surfaces the true-multiview director console
 * consumes beyond the director state itself. Every fetcher is HONEST:
 * an endpoint that does not exist yet (404), is unreachable, or returns
 * a shape this client cannot verify resolves to `available: false`
 * (with empty data) instead of throwing or inventing values — the tiles
 * render their standby / hidden states from that flag.
 *
 * Contracts consumed — VERIFIED against the Phase 25 backend workers'
 * landed code (services/replay/queue.py, services/replay/recorder.py,
 * services/live_director/service.py, api/v1/replay/router.py,
 * api/v1/director/router.py). Parsers stay tolerant of envelope and
 * naming drift, but the shapes below are the real ones:
 *
 *   Replay queue (P0-2):
 *     GET  /api/v1/replay/queue -> ReplayQueueStatus
 *          {"counts": {state: n}, "ready_slots": {"A": id, "B": id},
 *           "can_take_now": {"can_take": bool, "blocked_by": [str],
 *             "phase": str, "active_fights_on_pgm": [...],
 *             "director_mode": str, "active_camera": int},
 *           "entries": [ReplayQueueEntry]}
 *          Entry: {"id", "kind" (e.g. "squad_wipe"), "score",
 *           "reason", "player", "team", "opponents": [str],
 *           "slot_number": int|null, "state": lowercase
 *           "preparing"|"queued"|"ready"|"taken"|"expired"|"dismissed",
 *           "slot_label": "A"|"B"|null, "is_video": bool,
 *           "created_at": epoch float, "created_at_iso": str}
 *          Terminal states (expired/dismissed) are filtered out here.
 *     POST /api/v1/replay/queue/{id}/take    -> 200 + entry on take;
 *          a refusal answers 409 with
 *          {"detail": {"message": str, "gate": {...}}} — the UI
 *          surfaces `message` verbatim, never hides it.
 *     POST /api/v1/replay/queue/{id}/dismiss -> 200 + entry; a refusal
 *          answers 409 with {"detail": str}.
 *
 *   ISO recording status (P0-2):
 *     GET  /api/v1/replay/recorder/status -> RecorderStatus
 *          {"running": bool, "feeds": {feed_id: {"state":
 *           "recording"|"no_source"|"disk_full_paused"|"stopped", ...}},
 *           "total_bytes": int, ...}
 *          No count is ever derived from the video-capture state —
 *          capture running is not recording.
 *
 *   Decision log (P0-0 unified director):
 *     GET  /api/v1/director/decision-log
 *       -> {"running": bool, "decisions": [...], "status": {...}|null}
 *          Entry: {"timestamp": str, "outcome":
 *           "switch"|"hold"|"rejected_challenger"|"suggestion",
 *           "rule": str, "reason": str, "margin_actual": float|null,
 *           "challenger"/"incumbent": {...}, ...} (oldest first).
 *          The console shows only the tail (last decision); when the
 *          endpoint is unavailable — or the director is not running
 *          and the log is empty — the element is hidden entirely.
 *
 *   Feed health (exists today):
 *     GET  /api/v1/video/status -> VideoStatusResponse
 *          {"capture_running": bool, "counts": {status: n},
 *           "slots": [{"slot_number", "status", "health_status", ...}]}
 *          ok / degraded / down are counted per slot from
 *          health_status first, then the connection status.
 *
 *   Match phase (exists today):
 *     GET  /api/v1/flight/phase -> PhaseTracker snapshot
 *          {"phase": str, "reason": str, "alive_count": int|null,
 *           "zone_phase": int|null, ...}
 */

import { apiFetch, ApiError, API_BASE } from "./api";

/* ------------------------------------------------------------------ */
/* Shared helpers                                                      */
/* ------------------------------------------------------------------ */

function asRecord(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === "object"
    ? (v as Record<string, unknown>)
    : null;
}

function asString(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Unwrap the common list envelopes: bare array, {data}, or a named key. */
function asList(data: unknown, keys: string[]): unknown[] {
  if (Array.isArray(data)) return data;
  const rec = asRecord(data);
  if (!rec) return [];
  for (const key of keys) {
    if (Array.isArray(rec[key])) return rec[key] as unknown[];
  }
  return [];
}

/** True when the failure means "endpoint not built yet" (404). */
function isMissing(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

/** Strip apiFetch's "API <status>: " prefix for inline display. */
export function cleanApiMessage(e: unknown): string {
  const raw = e instanceof Error ? e.message : "Request failed.";
  return raw.replace(/^API \d{3}:\s*/, "");
}

/* ------------------------------------------------------------------ */
/* Replay queue (P0-2 contract)                                        */
/* ------------------------------------------------------------------ */

export type ReplayEntryState = "PREPARING" | "QUEUED" | "READY" | "TAKEN";

export interface ReplayQueueEntry {
  id: string;
  /** Moment label: the entry kind humanised ("squad_wipe" -> "Squad wipe"). */
  label: string;
  /** The queue's own reason text for the moment ("" when unreported). */
  reason: string;
  /** Team names involved: the moment's team + opponents. */
  teams: string[];
  state: ReplayEntryState;
  /** Offer slot while READY: "A" | "B" (null otherwise). */
  slot_label: "A" | "B" | null;
  /** Observer slot the replay was recorded from, when reported. */
  slot: number | null;
  /** True when a real video clip was cut from an ISO recording. */
  is_video: boolean;
  created_at: string | null;
}

export interface ReplayGate {
  /** Queue-level lull gate (can_take_now). Null when unreported. */
  canTake: boolean | null;
  /** Blocker tokens, e.g. ["fight_on_pgm"] / ["phase_suppressed"]. */
  blockedBy: string[];
}

export interface ReplayQueueResult {
  /** False when the queue endpoint is missing/unreachable/malformed. */
  available: boolean;
  entries: ReplayQueueEntry[];
  gate: ReplayGate;
}

/** Human label for a lull-gate blocker token (queue's own vocabulary). */
export function gateBlockLabel(token: string): string {
  const labels: Record<string, string> = {
    fight_on_pgm: "a fight is live on program",
    phase_suppressed: "late-phase replay suppression is active",
    final_circle_lock: "final-circle lock is active",
    fight_state_unknown: "fight state is unknown — gate fails closed",
  };
  return labels[token] ?? token.replace(/_/g, " ");
}

function humanizeKind(kind: string): string {
  const words = kind.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

function normalizeReplayEntry(raw: unknown): ReplayQueueEntry | null {
  const r = asRecord(raw);
  if (!r) return null;
  const id = asString(r.id) ?? asString(r.entry_id) ?? asString(r.moment_id);
  if (!id) return null;
  const rawState = (
    asString(r.state) ??
    asString(r.status) ??
    ""
  ).toUpperCase();
  // Terminal states never occupy a tile.
  if (rawState === "EXPIRED" || rawState === "DISMISSED") return null;
  // "OFFERED" is the blueprint's pre-READY name for the same state.
  const state: ReplayEntryState =
    rawState === "READY" || rawState === "OFFERED"
      ? "READY"
      : rawState === "TAKEN"
        ? "TAKEN"
        : rawState === "QUEUED"
          ? "QUEUED"
          : "PREPARING";
  const teams: string[] = [];
  const team = asString(r.team);
  if (team) teams.push(team);
  if (Array.isArray(r.opponents)) {
    for (const t of r.opponents) {
      const name = asString(t);
      if (name && !teams.includes(name)) teams.push(name);
    }
  }
  if (teams.length === 0 && Array.isArray(r.teams)) {
    for (const t of r.teams) {
      const name = asString(t);
      if (name) teams.push(name);
    }
  }
  if (teams.length === 0) {
    const a = asString(r.team_a);
    const b = asString(r.team_b);
    if (a) teams.push(a);
    if (b) teams.push(b);
  }
  const slotRaw = asNumber(r.slot_number) ?? asNumber(r.slot);
  const createdEpoch = asNumber(r.created_at);
  const slotLabelRaw = asString(r.slot_label);
  return {
    id,
    label:
      asString(r.label) ??
      asString(r.moment_label) ??
      asString(r.title) ??
      (asString(r.kind) ? humanizeKind(asString(r.kind) as string) : ""),
    reason: asString(r.reason) ?? "",
    teams,
    state,
    slot_label:
      slotLabelRaw === "A" || slotLabelRaw === "B" ? slotLabelRaw : null,
    slot:
      slotRaw !== null && Number.isInteger(slotRaw) && slotRaw >= 1 && slotRaw <= 16
        ? slotRaw
        : null,
    is_video: r.is_video === true,
    created_at:
      asString(r.created_at_iso) ??
      asString(r.created_at) ??
      (createdEpoch !== null
        ? new Date(createdEpoch * 1000).toISOString()
        : null),
  };
}

function parseReplayGate(data: unknown): ReplayGate {
  const rec = asRecord(data);
  const gate = rec ? asRecord(rec.can_take_now) : null;
  if (!gate) return { canTake: null, blockedBy: [] };
  const blockedBy: string[] = [];
  if (Array.isArray(gate.blocked_by)) {
    for (const b of gate.blocked_by) {
      const t = asString(b);
      if (t) blockedBy.push(t);
    }
  }
  return {
    canTake: typeof gate.can_take === "boolean" ? gate.can_take : null,
    blockedBy,
  };
}

/** GET /api/v1/replay/queue — the offer/take replay queue (P0-2). */
export async function getReplayQueue(
  token: string | null
): Promise<ReplayQueueResult> {
  try {
    const data = await apiFetch<unknown>("/api/v1/replay/queue", {
      token: token ?? undefined,
    });
    const entries = asList(data, ["entries", "queue", "data"])
      .map(normalizeReplayEntry)
      .filter((e): e is ReplayQueueEntry => e !== null);
    return { available: true, entries, gate: parseReplayGate(data) };
  } catch {
    // Missing endpoint / unreachable / malformed: the tiles show their
    // "queue unavailable" standby — nothing is invented.
    return { available: false, entries: [], gate: { canTake: null, blockedBy: [] } };
  }
}

export interface ReplayActionResult {
  ok: boolean;
  /** Refusal / error reason from the backend, verbatim, when !ok. */
  reason: string | null;
}

/**
 * Extract a refusal message from a queue-action error body. The take
 * endpoint nests it: {"detail": {"message": str, "gate": {...}}};
 * dismiss uses a plain {"detail": str}. Both are surfaced verbatim.
 */
function refusalMessage(body: unknown, status: number): string {
  const rec = asRecord(body);
  if (rec) {
    const detail = rec.detail;
    const detailRec = asRecord(detail);
    if (detailRec) {
      const msg = asString(detailRec.message) ?? asString(detailRec.reason);
      if (msg) return msg;
    }
    if (typeof detail === "string" && detail) return detail;
    const msg =
      asString(rec.message) ?? asString(rec.error) ?? asString(rec.reason);
    if (msg) return msg;
  }
  return `HTTP ${status}`;
}

/**
 * POST a queue action with direct fetch (not apiFetch) so the nested
 * 409 refusal detail survives parsing — the refusal reason is a
 * first-class part of this UI, never hidden behind "HTTP 409".
 */
async function postQueueAction(
  token: string | null,
  id: string,
  action: "take" | "dismiss"
): Promise<ReplayActionResult> {
  try {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(
      `${API_BASE}/api/v1/replay/queue/${encodeURIComponent(id)}/${action}`,
      { method: "POST", headers, body: "{}" }
    );
    if (res.ok) return { ok: true, reason: null };
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body — fall through to the status line */
    }
    return { ok: false, reason: refusalMessage(body, res.status) };
  } catch (e) {
    return {
      ok: false,
      reason: e instanceof Error ? e.message : "Request failed.",
    };
  }
}

/** POST /api/v1/replay/queue/{id}/take — take an offered replay to air. */
export async function takeReplayEntry(
  token: string | null,
  id: string
): Promise<ReplayActionResult> {
  return postQueueAction(token, id, "take");
}

/** POST /api/v1/replay/queue/{id}/dismiss — drop an offered replay. */
export async function dismissReplayEntry(
  token: string | null,
  id: string
): Promise<ReplayActionResult> {
  return postQueueAction(token, id, "dismiss");
}

/* ------------------------------------------------------------------ */
/* ISO recording status (P0-2 contract)                                */
/* ------------------------------------------------------------------ */

export interface RecordingStatus {
  /** False when no recording-status endpoint answers. */
  available: boolean;
  recording: boolean | null;
  /** Feeds currently ISO-recording; null when the API reports none. */
  feedsRecording: number | null;
  totalFeeds: number | null;
}

function parseRecordingStatus(data: unknown): RecordingStatus | null {
  const rec = asRecord(data);
  if (!rec) return null;
  // RecorderStatus (verified): {"running": bool, "feeds": {id: feed}}.
  const recording =
    typeof rec.running === "boolean"
      ? rec.running
      : typeof rec.recording === "boolean"
        ? rec.recording
        : typeof rec.is_recording === "boolean"
          ? rec.is_recording
          : null;
  let feedsRecording =
    asNumber(rec.feeds_recording) ??
    asNumber(rec.recording_feeds) ??
    asNumber(rec.active_feeds) ??
    asNumber(rec.recording_count);
  let totalFeeds = asNumber(rec.total_feeds) ?? asNumber(rec.total);
  const feeds = rec.feeds ?? rec.slots;
  // The real payload's feeds is a dict keyed by feed_id whose values
  // carry "state"; an array of per-feed records is also accepted.
  const feedList: unknown[] = Array.isArray(feeds)
    ? feeds
    : feeds !== null && typeof feeds === "object"
      ? Object.values(feeds as Record<string, unknown>)
      : [];
  if (feedList.length > 0) {
    if (totalFeeds === null) totalFeeds = feedList.length;
    if (feedsRecording === null) {
      feedsRecording = feedList.filter((f) => {
        const fr = asRecord(f);
        if (!fr) return false;
        return (
          fr.recording === true ||
          fr.is_recording === true ||
          asString(fr.state) === "recording" ||
          asString(fr.status) === "recording"
        );
      }).length;
    }
  }
  if (recording === null && feedsRecording === null) return null;
  return { available: true, recording, feedsRecording, totalFeeds };
}

/**
 * GET /api/v1/replay/recorder/status (the verified P0-2 path;
 * /recording/status kept as a fallback probe).
 * `available: false` when neither endpoint exists — the status row
 * then shows "ISO unknown" rather than guessing from capture state.
 */
export async function getRecordingStatus(
  token: string | null
): Promise<RecordingStatus> {
  const paths = ["/api/v1/replay/recorder/status", "/api/v1/replay/recording/status"];
  for (const path of paths) {
    try {
      const data = await apiFetch<unknown>(path, {
        token: token ?? undefined,
      });
      const parsed = parseRecordingStatus(data);
      if (parsed) return parsed;
    } catch (e) {
      if (!isMissing(e)) break; // a real error: don't probe further
    }
  }
  return {
    available: false,
    recording: null,
    feedsRecording: null,
    totalFeeds: null,
  };
}

/* ------------------------------------------------------------------ */
/* Decision log (P0-0 contract)                                        */
/* ------------------------------------------------------------------ */

export interface DecisionEntry {
  /** What the director did, e.g. "switch" / "hold" / "take_replay". */
  action: string;
  /** Why — the pipeline's own words. "" when unreported. */
  reason: string;
  at: string | null;
}

export interface DecisionLogResult {
  /** False when the endpoint is missing/unreachable — UI hides it. */
  available: boolean;
  /** Most recent decision, or null when the log is genuinely empty. */
  last: DecisionEntry | null;
}

function normalizeDecision(raw: unknown): DecisionEntry | null {
  const r = asRecord(raw);
  if (!r) return null;
  // Verified entry shape: the verb is "outcome" (switch / hold /
  // rejected_challenger / suggestion), with "rule" naming the gate
  // that decided it. Older/alternate namings stay tolerated.
  const outcome =
    asString(r.outcome) ??
    asString(r.action) ??
    asString(r.decision) ??
    asString(r.type);
  if (!outcome) return null;
  const rule = asString(r.rule);
  return {
    action: rule ? `${outcome} (${rule})` : outcome,
    reason:
      asString(r.reason) ?? asString(r.detail) ?? asString(r.message) ?? "",
    at:
      asString(r.timestamp) ??
      asString(r.created_at) ??
      asString(r.at) ??
      null,
  };
}

/**
 * GET /api/v1/director/decision-log (verified P0-0 path;
 * /director/decisions kept as a fallback probe).
 * Returns only the tail entry — the status row shows the last
 * decision (what switched/held and why), nothing more. When the
 * endpoint reports the live director is not running AND the log is
 * empty, the result is `available: false` so the UI hides the chip
 * instead of showing a permanent "no decisions" placeholder.
 */
export async function getDecisionLog(
  token: string | null
): Promise<DecisionLogResult> {
  const paths = ["/api/v1/director/decision-log", "/api/v1/director/decisions"];
  for (const path of paths) {
    try {
      const data = await apiFetch<unknown>(path, {
        token: token ?? undefined,
      });
      const list = asList(data, ["decisions", "log", "entries", "data"])
        .map(normalizeDecision)
        .filter((d): d is DecisionEntry => d !== null);
      const rec = asRecord(data);
      const running = rec ? rec.running : undefined;
      if (running === false && list.length === 0) {
        return { available: false, last: null };
      }
      return {
        available: true,
        last: list.length > 0 ? list[list.length - 1] : null,
      };
    } catch (e) {
      if (!isMissing(e)) break;
    }
  }
  return { available: false, last: null };
}

/* ------------------------------------------------------------------ */
/* Feed health — GET /api/v1/video/status (exists today)               */
/* ------------------------------------------------------------------ */

export interface FeedHealth {
  /** False when the endpoint fails or reports no slot data at all. */
  available: boolean;
  ok: number;
  degraded: number;
  down: number;
  total: number;
  captureRunning: boolean | null;
}

const OK_HEALTH = new Set(["healthy", "ok", "good", "live"]);
const DEGRADED_HEALTH = new Set(["degraded", "warning", "warn", "unstable"]);
const DOWN_HEALTH = new Set(["down", "critical", "offline", "error", "failed"]);

/**
 * Count per-feed health from the video status payload. health_status
 * (Phase 22 feed-health score) wins; the connection status is the
 * fallback: connected = ok, reconnecting = degraded,
 * disconnected / no_feed = down.
 */
export function summarizeFeedHealth(data: unknown): FeedHealth {
  const rec = asRecord(data);
  if (!rec) {
    return {
      available: false,
      ok: 0,
      degraded: 0,
      down: 0,
      total: 0,
      captureRunning: null,
    };
  }
  const captureRunning =
    typeof rec.capture_running === "boolean" ? rec.capture_running : null;
  let ok = 0;
  let degraded = 0;
  let down = 0;
  const slots = Array.isArray(rec.slots) ? rec.slots : null;
  if (slots && slots.length > 0) {
    for (const s of slots) {
      const slot = asRecord(s);
      if (!slot) continue;
      const health = (asString(slot.health_status) ?? "").toLowerCase();
      const status = (asString(slot.status) ?? "").toLowerCase();
      if (OK_HEALTH.has(health)) ok += 1;
      else if (DEGRADED_HEALTH.has(health)) degraded += 1;
      else if (DOWN_HEALTH.has(health)) down += 1;
      else if (status === "connected") ok += 1;
      else if (status === "reconnecting") degraded += 1;
      else down += 1; // disconnected / no_feed / unknown per-slot state
    }
    return {
      available: true,
      ok,
      degraded,
      down,
      total: slots.length,
      captureRunning,
    };
  }
  // No per-slot rows: fall back to the aggregate counts map, honestly
  // labelled by the same status vocabulary.
  const counts = asRecord(rec.counts);
  if (counts) {
    for (const [key, value] of Object.entries(counts)) {
      const n = asNumber(value);
      if (n === null) continue;
      const k = key.toLowerCase();
      if (k === "connected") ok += n;
      else if (k === "reconnecting") degraded += n;
      else down += n;
    }
    const total = ok + degraded + down;
    if (total > 0) {
      return { available: true, ok, degraded, down, total, captureRunning };
    }
  }
  return {
    available: false,
    ok: 0,
    degraded: 0,
    down: 0,
    total: 0,
    captureRunning,
  };
}

/** GET /api/v1/video/status — per-feed health for the status row. */
export async function getFeedHealth(
  token: string | null
): Promise<FeedHealth> {
  try {
    const data = await apiFetch<unknown>("/api/v1/video/status", {
      token: token ?? undefined,
    });
    return summarizeFeedHealth(data);
  } catch {
    return {
      available: false,
      ok: 0,
      degraded: 0,
      down: 0,
      total: 0,
      captureRunning: null,
    };
  }
}

/* ------------------------------------------------------------------ */
/* Match phase — GET /api/v1/flight/phase (exists today)               */
/* ------------------------------------------------------------------ */

export interface MatchPhase {
  available: boolean;
  /** Raw phase id, e.g. "mid_game" / "final_circle" / "not_started". */
  phase: string | null;
  reason: string | null;
  aliveCount: number | null;
  zonePhase: number | null;
}

/** Human label for a PhaseTracker phase id. */
export function matchPhaseLabel(phase: string | null): string {
  if (!phase) return "—";
  const labels: Record<string, string> = {
    not_started: "Not started",
    unknown: "Unknown",
    plane: "Plane",
    parachute: "Parachute",
    loot: "Loot",
    early: "Early",
    mid_game: "Mid game",
    late_game: "Late game",
    final_circle: "Final circle",
    ended: "Ended",
  };
  return labels[phase] ?? phase.replace(/_/g, " ");
}

/** GET /api/v1/flight/phase — current match phase (PhaseTracker). */
export async function getMatchPhase(
  token: string | null
): Promise<MatchPhase> {
  try {
    const data = await apiFetch<unknown>("/api/v1/flight/phase", {
      token: token ?? undefined,
    });
    const rec = asRecord(data);
    if (!rec) {
      return {
        available: false,
        phase: null,
        reason: null,
        aliveCount: null,
        zonePhase: null,
      };
    }
    return {
      available: true,
      phase: asString(rec.phase),
      reason: asString(rec.reason),
      aliveCount: asNumber(rec.alive_count),
      zonePhase: asNumber(rec.zone_phase),
    };
  } catch {
    return {
      available: false,
      phase: null,
      reason: null,
      aliveCount: null,
      zonePhase: null,
    };
  }
}
