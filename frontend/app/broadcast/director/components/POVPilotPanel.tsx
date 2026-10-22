"use client";

/**
 * AEGIS AI — Observer Auto-Pilot panel (Phase 29, W4).
 *
 * Director-console section over /api/v1/observer-pilot: per-feed POV
 * summary (who is spectated on each observer feed, or an honest
 * "POV unknown"), the pending semi_auto suggestions with one-tap
 * approve/reject, the pilot kill-switch, manual override chips
 * (4 roster players per feed), and the recent POV decisions from
 * the durable journal (rule + reason).
 *
 * Honesty rules: nothing renders from invented data — the panel
 * shows the backend's status or an explicit unavailable state.
 * The kill-switch reflects the backend's PilotControl; while OFF,
 * overrides are disabled here AND blocked by the backend (409).
 * Calibration flags come from W3's TapCalibration ledger: the
 * built-in default tap points are UNCALIBRATED estimates until a
 * scrim measures them per device, and the panel says so.
 */

import { useCallback, useEffect, useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";
import {
  approvePilotSuggestion,
  getPilotDecisions,
  getPilotStatus,
  pilotOverride,
  povLabel,
  rejectPilotSuggestion,
  setPilotKillSwitch,
  spectatedPlayer,
  type POVDecisionRow,
  type PilotStatus,
} from "@/lib/observer-pilot";

function actionErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Request failed.";
}

const BEHAVIOUR_LABELS: Record<string, string> = {
  auto_execute: "Auto-execute (tap apne aap)",
  suggest: "Suggestion (approve karna hoga)",
  shadow: "Shadow only (koi tap nahi)",
  off_kill_switch: "OFF — kill-switch band hai",
};

export function POVPilotPanel({ token }: { token: string | null }) {
  const [status, setStatus] = useState<PilotStatus | null>(null);
  const [decisions, setDecisions] = useState<POVDecisionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([
        getPilotStatus(token),
        getPilotDecisions(token, 20),
      ]);
      setStatus(s);
      setDecisions(d);
      setError(null);
    } catch (e) {
      setError(actionErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh]);

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

  const killOff = status?.kill_switch.enabled === false;
  const behaviourLabel = status?.behaviour
    ? (BEHAVIOUR_LABELS[status.behaviour] ?? status.behaviour)
    : null;

  return (
    <Card
      title="Observer Auto-Pilot (POV)"
      action={<Badge tone="cyan">Phase 29</Badge>}
    >
      {loading && <Loading label="Loading pilot status" />}
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

      {!loading && status && !status.running && (
        <EmptyState message="Pilot service is process me nahi chal raha — koi tap nahi hoga, aur kuch invented bhi nahi dikhega." />
      )}

      {!loading && status && status.running && (
        <div className="flex flex-col gap-5">
          {/* Kill-switch + behaviour + calibration honesty */}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={busy !== null || !status.kill_switch.control_wired}
              aria-pressed={!killOff}
              onClick={() =>
                void run("kill-switch", async () => {
                  await setPilotKillSwitch(token, killOff);
                  setNotice(
                    killOff
                      ? "Kill-switch ON — pilot mode ke hisaab se tap kar sakta hai."
                      : "Kill-switch OFF — ab koi tap nahi hoga, kisi bhi mode me."
                  );
                })
              }
              className={`rounded-lg border px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] transition-all disabled:cursor-not-allowed disabled:opacity-40 ${
                killOff
                  ? "border-[#ff3366] bg-[#ff3366]/15 text-[#ff3366]"
                  : "border-emerald-300 bg-emerald-300/15 text-emerald-300"
              }`}
            >
              {busy === "kill-switch"
                ? "…"
                : killOff
                  ? "Kill-switch: OFF"
                  : "Kill-switch: ON"}
            </button>
            {behaviourLabel && <Badge tone="slate">{behaviourLabel}</Badge>}
            <Badge tone="slate">Mode: {status.mode ?? "unknown"}</Badge>
            {!status.executor_available && (
              <Badge tone="amber">Executor unavailable — taps no_device</Badge>
            )}
            {status.calibration.available &&
              status.calibration.default_calibrated === false && (
                <Badge tone="amber">
                  Tap points UNCALIBRATED — scrim me per-device set karo
                </Badge>
              )}
            {status.calibration.available &&
              status.calibration.default_calibrated === true && (
                <Badge tone="green">Tap points calibrated</Badge>
              )}
          </div>

          {/* Pending suggestions (semi_auto) */}
          {status.pending_suggestions.length > 0 && (
            <div>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-bold uppercase tracking-[0.15em] text-white/70">
                Pending POV suggestions
                <Badge tone="amber">{status.pending_suggestions.length}</Badge>
              </h3>
              <div className="flex flex-col gap-3">
                {status.pending_suggestions.map((s) => (
                  <div
                    key={s.id}
                    className="rounded-xl border border-white/10 bg-black/30 px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-bold text-white/90">
                        CAM {s.slot_number} → idx {s.to_index}
                      </span>
                      <Badge tone="slate">{s.rule}</Badge>
                    </div>
                    <p className="mt-2 text-sm text-white/65">{s.reason}</p>
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        disabled={busy !== null || killOff}
                        onClick={() =>
                          void run(`approve-${s.id}`, async () => {
                            await approvePilotSuggestion(token, s.id);
                            setNotice("Suggestion approve ho gaya — tap execute hua.");
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
                          void run(`reject-${s.id}`, async () => {
                            await rejectPilotSuggestion(token, s.id);
                            setNotice("Suggestion reject ho gaya.");
                          })
                        }
                        className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#ff3366]/60 hover:text-[#ff3366] disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Per-feed POV + override chips */}
          <div>
            <h3 className="mb-2 text-sm font-bold uppercase tracking-[0.15em] text-white/70">
              Per-feed POV
            </h3>
            {status.feeds.length === 0 ? (
              <EmptyState message="Abhi kisi feed ka POV state nahi aaya — monitor feed padhega to yahan dikhega." />
            ) : (
              <div className="flex flex-col gap-3">
                {status.feeds.map((feed) => {
                  const spectated = spectatedPlayer(feed);
                  return (
                    <div
                      key={feed.slot_number}
                      className="rounded-xl border border-white/10 bg-black/30 px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm font-bold text-white/90">
                          CAM {feed.slot_number}
                        </span>
                        <Badge tone={spectated ? "green" : "amber"}>
                          {povLabel(feed)}
                          {spectated
                            ? ` · idx ${spectated.roster_index} · ${spectated.status}`
                            : ""}
                        </Badge>
                        {feed.pov.stale && <Badge tone="slate">stale</Badge>}
                        {feed.last_decision && (
                          <Badge tone="slate">{feed.last_decision.rule}</Badge>
                        )}
                      </div>
                      {feed.last_note && !feed.pending_suggestion && (
                        <p className="mt-2 text-xs text-white/45">
                          Kyun nahi badla: {feed.last_note.reason}
                        </p>
                      )}
                      <div className="mt-3 flex flex-wrap gap-2">
                        {feed.pov.players.map((p) => (
                          <button
                            key={p.roster_index}
                            type="button"
                            disabled={
                              busy !== null ||
                              killOff ||
                              p.roster_index === feed.pov.spectated_index
                            }
                            title={
                              killOff
                                ? "Kill-switch OFF hai — override bhi blocked hai"
                                : `CAM ${feed.slot_number} ko ${p.name ?? `idx ${p.roster_index}`} par le jao (manual override, journal hoga)`
                            }
                            onClick={() =>
                              void run(
                                `override-${feed.slot_number}-${p.roster_index}`,
                                async () => {
                                  await pilotOverride(
                                    token,
                                    feed.slot_number,
                                    p.roster_index
                                  );
                                  setNotice(
                                    `CAM ${feed.slot_number} override bhej diya — verification journal me milega.`
                                  );
                                }
                              )
                            }
                            className={`rounded-lg border px-3 py-1.5 font-mono text-xs transition-all disabled:cursor-not-allowed disabled:opacity-40 ${
                              p.is_spectated
                                ? "border-[#00f0ff] bg-[#00f0ff]/15 text-[#00f0ff]"
                                : "border-white/10 bg-white/5 text-white/70 hover:border-white/25 hover:text-white"
                            }`}
                          >
                            {p.name ?? `idx ${p.roster_index}`} · {p.status}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Recent POV decisions (durable journal) */}
          <div>
            <h3 className="mb-2 text-sm font-bold uppercase tracking-[0.15em] text-white/70">
              Recent POV decisions
            </h3>
            {decisions.length === 0 ? (
              <EmptyState message="Journal me abhi koi POV decision nahi hai." />
            ) : (
              <div className="flex flex-col gap-2">
                {decisions.map((d) => (
                  <div
                    key={d.id}
                    className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono font-bold text-white/85">
                        CAM {d.slot_number}
                      </span>
                      <Badge tone="slate">{d.fate}</Badge>
                      {d.rule && <Badge tone="slate">{d.rule}</Badge>}
                      {d.verification && (
                        <Badge tone="slate">{d.verification}</Badge>
                      )}
                      <span className="text-xs text-white/40">
                        {new Date(d.ts).toLocaleTimeString()}
                      </span>
                    </div>
                    {d.reason && (
                      <p className="mt-1 text-xs leading-relaxed text-white/55">
                        {d.reason}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
