"use client";

/**
 * AEGIS AI — Director Style (Clone) panel (Phase 27, W4).
 *
 * Shown on the director console next to the mode selector. Clone mode
 * is the same live director firing autonomously, but its learnable
 * numbers come from the LEARNED profile (the adjustments a human
 * approved in the Director Learning panel) instead of the factory
 * defaults. This panel shows, from the real APIs only:
 *
 *   - whose decisions the profile learned from (contributors +
 *     learned-from counts, GET /director-learning/status)
 *   - who is driving right now: the AI, or a human who grabbed
 *     control (director state clone_grabbed / clone_grabbed_by)
 *   - Grab control / Release buttons (POST /director/clone/grab,
 *     /release) — grab works only in clone mode; the backend 409s
 *     otherwise and its message is shown as-is
 *
 * Cold start, honestly: when no adjustment has ever been applied
 * there is NO profile — the panel says so in plain words with the
 * real decided-fate count from the API, and clone drives on default
 * settings. When the learning mode is OFF, applied adjustments are
 * dormant and clone also drives on defaults — the panel notes that
 * too. Nothing here is hardcoded: every count comes from the status
 * API, every state from the director state.
 */

import { useCallback, useEffect, useState } from "react";
import { Badge, Card, ErrorState, Loading } from "@/components/ui";
import {
  formatDirectorUpdatedAt,
  postDirectorCloneGrab,
  postDirectorCloneRelease,
  type DirectorState,
} from "@/lib/director";
import {
  getLearningStatus,
  type LearningStatus,
} from "@/lib/director-learning";

function actionErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Request failed.";
}

export function ClonePanel({
  token,
  state,
  onStateRefresh,
}: {
  token: string | null;
  state: DirectorState;
  /** Re-read the authoritative director state after a grab/release. */
  onStateRefresh: () => Promise<void> | void;
}) {
  const [status, setStatus] = useState<LearningStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await getLearningStatus(token);
      setStatus(s);
      setError(null);
    } catch (e) {
      setError(actionErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 15000);
    return () => clearInterval(timer);
  }, [refresh]);

  const run = useCallback(
    async (label: string, fn: () => Promise<void>) => {
      setBusy(label);
      setError(null);
      try {
        await fn();
        await onStateRefresh();
        await refresh();
      } catch (e) {
        setError(actionErrorMessage(e));
      } finally {
        setBusy(null);
      }
    },
    [onStateRefresh, refresh]
  );

  const inClone = state.mode === "clone";
  const grabbed = inClone && state.clone_grabbed;
  const profile = status?.profile;
  const learningOff = status?.learning_mode === "off";
  const coldStart = profile !== undefined && !profile.exists;

  return (
    <Card
      title="Director Style — Clone"
      action={
        grabbed ? (
          <Badge tone="amber">Manual grab</Badge>
        ) : inClone ? (
          <Badge tone="cyan">AI driving</Badge>
        ) : (
          <Badge tone="slate">Clone not active</Badge>
        )
      }
    >
      {loading && <Loading label="Loading learned style" />}
      {error && (
        <div className="mb-3">
          <ErrorState message={error} onRetry={() => void refresh()} />
        </div>
      )}

      {!loading && status && profile && (
        <div className="flex flex-col gap-4">
          {/* Who is driving right now (director state, not a guess). */}
          <div className="rounded-lg border border-white/10 bg-black/30 px-4 py-3">
            {!inClone && (
              <p className="text-sm text-white/70">
                Clone mode abhi active nahi hai — upar wale Mode selector
                se <strong>Director Style — Clone</strong> chuno. Tab AI
                tumhare seekhe hue style ke saath chalega.
              </p>
            )}
            {inClone && !grabbed && (
              <p className="text-sm text-white/85">
                <strong>AI driving</strong> — clone seekhe hue style ke
                saath chal raha hai
                {coldStart || learningOff
                  ? " (abhi default settings par — neeche dekho)"
                  : ""}
                .
              </p>
            )}
            {grabbed && (
              <p className="text-sm text-white/85">
                <strong>
                  Manual grab — {state.clone_grabbed_by ?? "koi director"}
                </strong>{" "}
                abhi haath se chala raha hai; AI chup hai. Grab{" "}
                {state.clone_grabbed_at
                  ? formatDirectorUpdatedAt(state.clone_grabbed_at)
                  : ""}{" "}
                par liya gaya tha. Release karte hi clone wapas seekhe
                hue style par chalega.
              </p>
            )}
          </div>

          {/* Cold start: no profile yet — say it plainly, with the
              real counts from the API. */}
          {coldStart && (
            <div className="rounded-lg border border-amber-300/25 bg-amber-300/[0.05] px-4 py-3">
              <p className="text-sm text-amber-100/90">
                Abhi style seekha nahi gaya — pehle kam se kam 10 faislo
                par director feedback chahiye.
              </p>
              <p className="mt-1 text-xs text-white/55">
                Abhi tak ke asal number: {profile.learned_from.decided_fates}{" "}
                faisle hue hain, {profile.learned_from.labeled_decisions}{" "}
                decisions par label laga hai,{" "}
                {profile.learned_from.divergences} manual-mode antar note
                hue hain. Jab pehla badlav approved hoga, profile ban
                jayega.
              </p>
            </div>
          )}

          {/* Learning OFF: clone drives on defaults, adjustments dormant. */}
          {learningOff && (
            <p className="rounded-lg border border-white/10 bg-white/[0.03] px-4 py-3 text-sm text-white/60">
              Learning mode OFF hai — clone abhi default settings par
              chalega; seekhe hue badlav dormant hain, delete nahi hue.
            </p>
          )}

          {/* Learned profile: who taught it + from how much evidence. */}
          {profile.exists && (
            <div className="rounded-lg border border-white/10 bg-black/30 px-4 py-3">
              <p className="text-sm text-white/85">
                Profile bana hua hai — {profile.applied_count} badlav lagu
                hain. Seekha gaya hai: {profile.learned_from.decided_fates}{" "}
                faislo, {profile.learned_from.labeled_decisions} labels aur{" "}
                {profile.learned_from.divergences} manual-mode antar se.
              </p>
              {profile.contributors.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {profile.contributors.map((c) => (
                    <Badge key={c.username} tone="cyan">
                      {c.username} · {c.decided_count} faisle
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-xs text-white/45">
                  Contributor list khaali hai — kisi faisle par decider ka
                  naam record nahi hua.
                </p>
              )}
            </div>
          )}

          {/* Grab / Release — wired to the real endpoints. Grab is
              only meaningful in clone mode; outside it the button is
              disabled with the reason, never a fake success. */}
          <div className="flex flex-wrap items-center gap-3">
            {!grabbed ? (
              <button
                type="button"
                disabled={busy !== null || !inClone}
                onClick={() =>
                  void run("grab", () => postDirectorCloneGrab(token))
                }
                title={
                  inClone
                    ? "Take manual control inside clone mode"
                    : "Pehle Clone mode chuno — grab sirf clone mode me hota hai"
                }
                className="rounded-lg bg-violet-500/80 px-5 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "grab" ? "Grabbing…" : "Grab control"}
              </button>
            ) : (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void run("release", () => postDirectorCloneRelease(token))
                }
                className="rounded-lg bg-emerald-400 px-5 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "release" ? "Releasing…" : "Release — AI ko wapas do"}
              </button>
            )}
            {!inClone && (
              <span className="text-xs text-white/45">
                Grab sirf Clone mode me milega.
              </span>
            )}
            {grabbed && (
              <span className="text-xs text-white/45">
                Grab chhode bina, bina kisi human write ke rehne par clone
                khud wapas le lega (inactivity auto-release).
              </span>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
