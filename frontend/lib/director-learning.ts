/**
 * AEGIS AI — director learning loop client (Phase 27, W4).
 *
 * Backend contract (GET/POST/PUT /api/v1/director-learning/*):
 *   GET  /status     -> {learning_mode, profile {exists, applied_count,
 *                        contributors [{username, decided_count}],
 *                        learned_from {labeled_decisions, decided_fates,
 *                        divergences}}, journal_stats {...},
 *                        journal {running, stats, ...} | null,
 *                        knobs [{name, default, effective_learned,
 *                        effective_factory, min_value, max_value, step,
 *                        unit, why}]}
 *   GET  /proposals  -> [Adjustment]   (status "proposed")
 *   POST /proposals/{id}/approve | /reject     (director/admin)
 *   GET  /adjustments?status=          -> [Adjustment]
 *   POST /adjustments/{id}/rollback    -> the compensating Adjustment
 *   GET  /mode -> {mode}   PUT /mode {mode: "off"|"suggest"|"auto"}
 *   POST /analyze -> [Adjustment] created by this pass (may be empty)
 *
 * Reads need operator/director/admin; writes director/admin. This is
 * transparent rule-tuning over counted outcomes — NOT machine
 * learning. No mock data: every value comes from the backend; a
 * malformed payload is rejected, never guessed.
 */

import { apiFetch } from "./api";

export type LearningMode = "off" | "suggest" | "auto";

export const LEARNING_MODES: readonly LearningMode[] = [
  "off",
  "suggest",
  "auto",
];

export interface LearningAdjustment {
  id: string;
  ts: string;
  status: string; // "proposed" | "applied" | "rejected" | "rolled_back"
  knob: string;
  bucket: Record<string, unknown>;
  before_value: number;
  after_value: number;
  reason: string;
  evidence: Record<string, unknown>;
  learning_mode: string;
  applied_by: string | null;
  parent_id: string | null;
}

export interface LearningContributor {
  username: string;
  decided_count: number;
}

export interface LearningKnob {
  name: string;
  default: number;
  effective_learned: number;
  effective_factory: number;
  min_value: number;
  max_value: number;
  step: number;
  unit: string;
  why: string;
}

export interface LearningStatus {
  learning_mode: LearningMode;
  profile: {
    exists: boolean;
    applied_count: number;
    contributors: LearningContributor[];
    learned_from: {
      labeled_decisions: number;
      decided_fates: number;
      divergences: number;
    };
  };
  journal_stats: {
    decisions_total: number;
    decisions_by_mode: Record<string, number>;
    decisions_by_outcome: Record<string, number>;
    fates_by_fate: Record<string, number>;
    divergences_by_kind: Record<string, number>;
    labels_total: number;
  };
  journal: {
    running: boolean;
    stats: Record<string, number>;
    ring_size: number;
    suggestion_index_size: number;
    open_modified_watches: number;
    writes_observed: number;
  } | null;
  knobs: LearningKnob[];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object";
}

function isAdjustment(v: unknown): v is LearningAdjustment {
  if (!isRecord(v)) return false;
  return (
    typeof v.id === "string" &&
    typeof v.status === "string" &&
    typeof v.knob === "string" &&
    typeof v.before_value === "number" &&
    typeof v.after_value === "number" &&
    typeof v.reason === "string"
  );
}

function requireAdjustments(res: unknown): LearningAdjustment[] {
  if (!Array.isArray(res) || !res.every(isAdjustment)) {
    throw new Error(
      "The learning service returned an unexpected adjustments shape — not applying it."
    );
  }
  return res as LearningAdjustment[];
}

function requireStatus(res: unknown): LearningStatus {
  if (!isRecord(res)) {
    throw new Error("The learning service returned an unexpected status shape.");
  }
  const s = res as Record<string, unknown>;
  if (
    typeof s.learning_mode !== "string" ||
    !(LEARNING_MODES as readonly string[]).includes(s.learning_mode) ||
    !isRecord(s.profile) ||
    !isRecord(s.journal_stats) ||
    !Array.isArray(s.knobs)
  ) {
    throw new Error("The learning service returned an unexpected status shape.");
  }
  return res as unknown as LearningStatus;
}

/** GET /api/v1/director-learning/status (operator/director/admin). */
export async function getLearningStatus(
  token?: string | null
): Promise<LearningStatus> {
  const res = await apiFetch<unknown>("/api/v1/director-learning/status", {
    token: token ?? undefined,
  });
  return requireStatus(res);
}

/** GET /api/v1/director-learning/proposals — pending proposals. */
export async function getLearningProposals(
  token?: string | null
): Promise<LearningAdjustment[]> {
  const res = await apiFetch<unknown>("/api/v1/director-learning/proposals", {
    token: token ?? undefined,
  });
  return requireAdjustments(res);
}

/** GET /api/v1/director-learning/adjustments?status= */
export async function getLearningAdjustments(
  token: string | null,
  statusFilter?: string
): Promise<LearningAdjustment[]> {
  const query = statusFilter
    ? `?status=${encodeURIComponent(statusFilter)}`
    : "";
  const res = await apiFetch<unknown>(
    `/api/v1/director-learning/adjustments${query}`,
    { token: token ?? undefined }
  );
  return requireAdjustments(res);
}

/** POST /api/v1/director-learning/proposals/{id}/approve (director/admin). */
export async function approveLearningProposal(
  token: string | null,
  id: string
): Promise<void> {
  await apiFetch<unknown>(
    `/api/v1/director-learning/proposals/${encodeURIComponent(id)}/approve`,
    { method: "POST", token: token ?? undefined }
  );
}

/** POST /api/v1/director-learning/proposals/{id}/reject (director/admin). */
export async function rejectLearningProposal(
  token: string | null,
  id: string
): Promise<void> {
  await apiFetch<unknown>(
    `/api/v1/director-learning/proposals/${encodeURIComponent(id)}/reject`,
    { method: "POST", token: token ?? undefined }
  );
}

/** POST /api/v1/director-learning/adjustments/{id}/rollback (director/admin). */
export async function rollbackLearningAdjustment(
  token: string | null,
  id: string
): Promise<void> {
  await apiFetch<unknown>(
    `/api/v1/director-learning/adjustments/${encodeURIComponent(id)}/rollback`,
    { method: "POST", token: token ?? undefined }
  );
}

/** PUT /api/v1/director-learning/mode (director/admin). */
export async function putLearningMode(
  token: string | null,
  mode: LearningMode
): Promise<void> {
  await apiFetch<unknown>("/api/v1/director-learning/mode", {
    method: "PUT",
    token: token ?? undefined,
    body: { mode },
  });
}

/** POST /api/v1/director-learning/analyze — one on-demand analysis pass. */
export async function analyzeLearning(
  token: string | null
): Promise<LearningAdjustment[]> {
  const res = await apiFetch<unknown>("/api/v1/director-learning/analyze", {
    method: "POST",
    token: token ?? undefined,
  });
  return requireAdjustments(res);
}

/** "margin_default" -> bucket line, e.g. "phase mid_game · ACTIVE_COMBAT · margin_default". */
export function describeBucket(bucket: Record<string, unknown>): string {
  const parts = ["phase", "engagement_state", "rule_family"]
    .map((k) => bucket[k])
    .filter((v): v is string => typeof v === "string" && v.length > 0);
  return parts.length > 0 ? parts.join(" · ") : "sab buckets";
}

/** Format a knob value without float dust (0.08 stays 0.08). */
export function formatKnobValue(v: number): string {
  return String(Math.round(v * 10000) / 10000);
}
