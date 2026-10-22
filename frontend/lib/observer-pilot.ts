/**
 * AEGIS AI — observer auto-pilot client (Phase 29, W4).
 *
 * Backend contract (operator+ on every route):
 *   GET  /api/v1/observer-pilot/status    -> PilotStatus
 *   POST /api/v1/observer-pilot/kill-switch {enabled}
 *   POST /api/v1/observer-pilot/suggestions/{id}/approve | /reject
 *   POST /api/v1/observer-pilot/override {slot_number, roster_index}
 *   GET  /api/v1/observer-pilot/decisions?slot=&limit=
 *        -> {journal_running, decisions: [POVDecisionRow]}
 *
 * No mock data: every value comes from the backend. A malformed
 * status payload is rejected, never guessed — the panel then shows
 * its error state instead of invented POV names. When identity is
 * unknown the backend says so (identity_known=false, name null) and
 * the UI renders "POV unknown" — a name is never invented here either.
 */

import { apiFetch } from "./api";

export interface PilotPlayer {
  roster_index: number;
  player_id: string | null;
  name: string | null;
  status: string; // alive | knocked | dead | unknown
  hp_fraction: number | null;
  hp_velocity: number | null;
  is_spectated: boolean;
}

export interface PilotFeedPOV {
  slot_number: number;
  source_id: string;
  team_id: string | null;
  observed_at: number;
  spectated_index: number | null;
  identity_known: boolean;
  identity_source: string;
  stale: boolean;
  players: PilotPlayer[];
}

export interface PilotSuggestion {
  id: string;
  slot_number: number;
  to_index: number;
  from_index: number | null;
  rule: string;
  reason: string;
  created_at: number;
  expires_at: number;
}

export interface PilotNote {
  slot_number: number;
  reason: string;
  blocked_rule: string | null;
  at: number;
}

export interface PilotFeed {
  slot_number: number;
  source_id: string;
  pov: PilotFeedPOV;
  behaviour: string;
  pending_suggestion: PilotSuggestion | null;
  last_decision: {
    rule: string;
    reason: string;
    from_index: number | null;
    to_index: number;
    decided_at: number;
  } | null;
  last_note: PilotNote | null;
  last_tap_at: number | null;
}

export interface PilotStatus {
  running: boolean;
  mode: string | null;
  behaviour: string | null;
  kill_switch: {
    enabled: boolean | null;
    disabled_slots: number[];
    control_wired: boolean;
  };
  executor_available: boolean;
  calibration: {
    available: boolean;
    profiles?: string[];
    calibrated_profiles?: string[];
    default_calibrated?: boolean;
    devices: Record<string, { calibrated: boolean }>;
  };
  feeds: PilotFeed[];
  pending_suggestions: PilotSuggestion[];
}

export interface POVDecisionRow {
  id: string;
  ts: string;
  slot_number: number;
  team_id: string | null;
  from_index: number | null;
  to_index: number | null;
  rule: string | null;
  reason: string | null;
  mode: string | null;
  fate: string;
  verification: string | null;
  decided_by: string | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object";
}

/** Spectated player of one feed, or null when the POV is unknown. */
export function spectatedPlayer(feed: PilotFeed): PilotPlayer | null {
  if (!feed.pov.identity_known || feed.pov.spectated_index === null) {
    return null;
  }
  return (
    feed.pov.players.find(
      (p) => p.roster_index === feed.pov.spectated_index
    ) ?? null
  );
}

/** "GodL" when known, honest "POV unknown" otherwise — never a guess. */
export function povLabel(feed: PilotFeed): string {
  const player = spectatedPlayer(feed);
  if (!player) return "POV unknown";
  return player.name ?? "POV unknown";
}

export async function getPilotStatus(
  token?: string | null
): Promise<PilotStatus> {
  const res = await apiFetch<unknown>("/api/v1/observer-pilot/status", {
    token: token ?? undefined,
  });
  if (!isRecord(res) || !Array.isArray(res.feeds) || !isRecord(res.kill_switch)) {
    throw new Error("The pilot service returned an unexpected status shape.");
  }
  return res as unknown as PilotStatus;
}

export async function getPilotDecisions(
  token: string | null,
  limit = 20
): Promise<POVDecisionRow[]> {
  const res = await apiFetch<unknown>(
    `/api/v1/observer-pilot/decisions?limit=${limit}`,
    { token: token ?? undefined }
  );
  if (!isRecord(res) || !Array.isArray(res.decisions)) return [];
  return res.decisions as POVDecisionRow[];
}

export async function setPilotKillSwitch(
  token: string | null,
  enabled: boolean
): Promise<void> {
  await apiFetch<unknown>("/api/v1/observer-pilot/kill-switch", {
    method: "POST",
    token: token ?? undefined,
    body: { enabled },
  });
}

export async function approvePilotSuggestion(
  token: string | null,
  id: string
): Promise<void> {
  await apiFetch<unknown>(
    `/api/v1/observer-pilot/suggestions/${encodeURIComponent(id)}/approve`,
    { method: "POST", token: token ?? undefined }
  );
}

export async function rejectPilotSuggestion(
  token: string | null,
  id: string
): Promise<void> {
  await apiFetch<unknown>(
    `/api/v1/observer-pilot/suggestions/${encodeURIComponent(id)}/reject`,
    { method: "POST", token: token ?? undefined }
  );
}

export async function pilotOverride(
  token: string | null,
  slotNumber: number,
  rosterIndex: number
): Promise<void> {
  await apiFetch<unknown>("/api/v1/observer-pilot/override", {
    method: "POST",
    token: token ?? undefined,
    body: { slot_number: slotNumber, roster_index: rosterIndex },
  });
}
