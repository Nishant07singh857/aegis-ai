"use client";

/**
 * AEGIS AI — Director Learning panel (Phase 27, W4).
 *
 * Director-console section over /api/v1/director-learning. This is
 * transparent rule-tuning over counted outcomes — NOT machine
 * learning, and the panel never pretends otherwise: no confidence
 * scores, no percentages about the future, only real counts, real
 * knob values, and the engine's own plain-language reasons.
 *
 * Visibility: director + admin only (the backend enforces the same —
 * writes are director/admin, and this panel returns null for every
 * other role; the director page itself is RequireRole-gated, so this
 * is a second, panel-level check like StorytellingPanel's).
 *
 * Contents:
 *   - Stats header: total decisions journalised, labeled count,
 *     suggestion approval rate (approved / decided, from the API).
 *   - Learning mode toggle OFF / SUGGEST / AUTO with honest one-line
 *     explainers, plus an "Abhi analyze karo" on-demand pass.
 *   - Pending proposals: the engine's reason (numbers included),
 *     knob before -> after, bucket context; Approve / Reject.
 *   - "Seekha hua": applied adjustments as plain lesson lines, each
 *     with a Rollback button (restores the exact earlier value).
 */

import { useCallback, useEffect, useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";
import { useRole } from "@/lib/useRole";
import {
  analyzeLearning,
  approveLearningProposal,
  describeBucket,
  formatKnobValue,
  getLearningAdjustments,
  getLearningProposals,
  getLearningStatus,
  putLearningMode,
  rejectLearningProposal,
  rollbackLearningAdjustment,
  LEARNING_MODES,
  type LearningAdjustment,
  type LearningMode,
  type LearningStatus,
} from "@/lib/director-learning";

function actionErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Request failed.";
}

const MODE_EXPLAINERS: Record<LearningMode, string> = {
  off: "OFF: koi naya badlav nahi banega; seekhe hue badlav dormant rahenge, delete nahi honge.",
  suggest: "SUGGEST: badlav pehle tumhari manzoori mangega.",
  auto: "AUTO: badlav seedha lagu ho jayega — rollback kabhi bhi ho sakta hai.",
};

const MODE_LABELS: Record<LearningMode, string> = {
  off: "OFF",
  suggest: "SUGGEST",
  auto: "AUTO",
};

function knobUnit(status: LearningStatus | null, knob: string): string {
  return status?.knobs.find((k) => k.name === knob)?.unit ?? "";
}

function AdjustmentLine({
  adj,
  unit,
  action,
}: {
  adj: LearningAdjustment;
  unit: string;
  action?: React.ReactNode;
}) {
  const n = adj.evidence?.n;
  return (
    <div className="rounded-xl border border-white/10 bg-black/30 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-bold text-white/90">
          {adj.knob}
        </span>
        <span className="font-mono text-sm text-white/70">
          {formatKnobValue(adj.before_value)} →{" "}
          {formatKnobValue(adj.after_value)}
          {unit ? ` ${unit}` : ""}
        </span>
        <Badge tone="slate">{describeBucket(adj.bucket)}</Badge>
        {typeof n === "number" && <Badge tone="slate">{n} samples</Badge>}
      </div>
      <p className="mt-2 text-sm text-white/65">{adj.reason}</p>
      {action}
    </div>
  );
}

export function DirectorLearningPanel({ token }: { token: string | null }) {
  const { role } = useRole();
  const canControl = role === "director" || role === "admin";

  const [status, setStatus] = useState<LearningStatus | null>(null);
  const [proposals, setProposals] = useState<LearningAdjustment[]>([]);
  const [applied, setApplied] = useState<LearningAdjustment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, p, a] = await Promise.all([
        getLearningStatus(token),
        getLearningProposals(token),
        getLearningAdjustments(token, "applied"),
      ]);
      setStatus(s);
      setProposals(p);
      setApplied(a);
      setError(null);
    } catch (e) {
      setError(actionErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!canControl) {
      setLoading(false);
      return;
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 15000);
    return () => clearInterval(timer);
  }, [refresh, canControl]);

  const run = useCallback(
    async (label: string, fn: () => Promise<void>) => {
      setBusy(label);
      setError(null);
      setNotice(null);
      try {
        await fn();
        await refresh();
      } catch (e) {
        setError(actionErrorMessage(e));
      } finally {
        setBusy(null);
      }
    },
    [refresh]
  );

  // Role gate (after hooks): only director/admin ever see this panel.
  if (!canControl) return null;

  const stats = status?.journal_stats;
  const fates = stats?.fates_by_fate ?? {};
  const decided =
    (fates.approved ?? 0) + (fates.rejected ?? 0) + (fates.modified ?? 0);
  const approvalRate =
    decided > 0 ? Math.round(((fates.approved ?? 0) / decided) * 100) : null;
  const learningMode: LearningMode = status?.learning_mode ?? "suggest";

  return (
    <Card
      title="Director Learning"
      action={<Badge tone="cyan">Director / Admin</Badge>}
    >
      {loading && <Loading label="Loading learning status" />}
      {error && (
        <div className="mb-3">
          <ErrorState message={error} onRetry={() => void refresh()} />
        </div>
      )}
      {notice && (
        <p className="mb-3 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-sm text-emerald-200">
          {notice}
        </p>
      )}

      {!loading && status && stats && (
        <div className="flex flex-col gap-5">
          {/* Stats header — real counts from the journal only. */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-white/10 bg-black/30 px-4 py-3">
              <div className="font-mono text-[11px] uppercase tracking-wider text-white/40">
                Kul decisions (journal)
              </div>
              <div className="mt-1 text-xl font-bold text-white/90">
                {stats.decisions_total}
              </div>
            </div>
            <div className="rounded-lg border border-white/10 bg-black/30 px-4 py-3">
              <div className="font-mono text-[11px] uppercase tracking-wider text-white/40">
                Label lage decisions
              </div>
              <div className="mt-1 text-xl font-bold text-white/90">
                {stats.labels_total}
              </div>
            </div>
            <div className="rounded-lg border border-white/10 bg-black/30 px-4 py-3">
              <div className="font-mono text-[11px] uppercase tracking-wider text-white/40">
                Suggestion approval rate
              </div>
              <div className="mt-1 text-xl font-bold text-white/90">
                {approvalRate !== null ? `${approvalRate}%` : "—"}
              </div>
              <div className="text-xs text-white/45">
                {decided > 0
                  ? `${fates.approved ?? 0} approved / ${decided} faisle`
                  : "abhi koi faisla nahi hua"}
              </div>
            </div>
          </div>

          {/* Learning mode toggle + on-demand analysis. */}
          <div>
            <div className="flex flex-wrap items-center gap-2">
              {LEARNING_MODES.map((m) => {
                const active = learningMode === m;
                return (
                  <button
                    key={m}
                    type="button"
                    disabled={busy !== null || active}
                    aria-pressed={active}
                    onClick={() =>
                      void run(`mode-${m}`, async () => {
                        await putLearningMode(token, m);
                        setNotice(`Learning mode ab ${MODE_LABELS[m]} hai.`);
                      })
                    }
                    className={`rounded-lg border px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] transition-all ${
                      active
                        ? "border-[#00f0ff] bg-[#00f0ff]/15 text-[#00f0ff]"
                        : "border-white/10 bg-white/5 text-white/55 hover:border-white/25 hover:text-white/85"
                    } disabled:cursor-not-allowed disabled:opacity-40`}
                  >
                    {busy === `mode-${m}` ? "…" : MODE_LABELS[m]}
                  </button>
                );
              })}
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void run("analyze", async () => {
                    const created = await analyzeLearning(token);
                    setNotice(
                      created.length > 0
                        ? `Analysis ne ${created.length} naya badlav banaya.`
                        : "Analysis chala — koi naya badlav nahi bana (ya to mode OFF hai, ya kisi rule ke paas kaafi samples nahi hain)."
                    );
                  })
                }
                className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#00f0ff]/60 hover:text-[#00f0ff] disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "analyze" ? "Analyzing…" : "Abhi analyze karo"}
              </button>
            </div>
            <p className="mt-2 text-sm text-white/55">
              {MODE_EXPLAINERS[learningMode]}
            </p>
          </div>

          {/* Pending proposals. */}
          <div>
            <h3 className="mb-2 flex items-center gap-2 text-sm font-bold uppercase tracking-[0.15em] text-white/70">
              Pending badlav
              <Badge tone={proposals.length ? "amber" : "slate"}>
                {proposals.length}
              </Badge>
            </h3>
            {proposals.length === 0 ? (
              <EmptyState message="Koi pending badlav nahi — engine tabhi prastav deta hai jab kisi bucket me kaafi faisle/labels ikatthe ho jayein." />
            ) : (
              <div className="flex flex-col gap-3">
                {proposals.map((adj) => (
                  <AdjustmentLine
                    key={adj.id}
                    adj={adj}
                    unit={knobUnit(status, adj.knob)}
                    action={
                      <div className="mt-3 flex gap-2">
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() =>
                            void run(`approve-${adj.id}`, async () => {
                              await approveLearningProposal(token, adj.id);
                              setNotice(
                                `${adj.knob} ka badlav lagu ho gaya — clone ab nayi value use karega.`
                              );
                            })
                          }
                          className="rounded-lg bg-emerald-400 px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() =>
                            void run(`reject-${adj.id}`, async () => {
                              await rejectLearningProposal(token, adj.id);
                              setNotice(`${adj.knob} ka prastav reject ho gaya.`);
                            })
                          }
                          className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#ff3366]/60 hover:text-[#ff3366] disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          Reject
                        </button>
                      </div>
                    }
                  />
                ))}
              </div>
            )}
          </div>

          {/* Seekha hua — applied adjustments with rollback. */}
          <div>
            <h3 className="mb-2 text-sm font-bold uppercase tracking-[0.15em] text-white/70">
              Seekha hua (lagu badlav)
            </h3>
            {applied.length === 0 ? (
              <EmptyState message="Abhi kuch seekha hua lagu nahi hai — approved badlav yahan dikhenge." />
            ) : (
              <div className="flex flex-col gap-3">
                {applied.map((adj) => (
                  <AdjustmentLine
                    key={adj.id}
                    adj={adj}
                    unit={knobUnit(status, adj.knob)}
                    action={
                      <div className="mt-3 flex flex-wrap items-center gap-3">
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() =>
                            void run(`rollback-${adj.id}`, async () => {
                              await rollbackLearningAdjustment(token, adj.id);
                              setNotice(
                                `${adj.knob} wapas ${formatKnobValue(adj.before_value)} par gaya — bilkul pehle jaisa.`
                              );
                            })
                          }
                          className="rounded-lg border border-amber-300/40 bg-amber-300/10 px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-amber-200 transition-colors hover:bg-amber-300/20 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {busy === `rollback-${adj.id}`
                            ? "Rolling back…"
                            : "Rollback"}
                        </button>
                        <span className="text-xs text-white/45">
                          {adj.parent_id
                            ? "Ye ek rollback ka badlav hai. "
                            : ""}
                          Lagu kiya: {adj.applied_by ?? "—"}
                        </span>
                      </div>
                    }
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
