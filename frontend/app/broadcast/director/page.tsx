"use client";

/**
 * AEGIS AI — DIRECTOR CONSOLE (Phase 6).
 *
 * Route: /broadcast/director (operator + admin).
 *
 * Live control surface for the director service:
 *   - big AUTO/MANUAL mode toggle (always enabled)
 *   - scene buttons: Main / Standings / Killfeed focus / Commentary
 *   - overlay toggles: scorebug, killfeed
 *   - lower-third composer: title + subtitle, Show/Hide
 *
 * In AUTO mode the scene/overlay/lower-third controls are disabled with an
 * explanation ("Switch to MANUAL to take control"); the mode toggle stays
 * enabled. The current director state is polled on mount and kept fresh
 * via the WS "director.state" frames (reconciled on reconnect). Failed
 * calls (e.g. 409 in auto mode) surface the backend's message honestly —
 * no state is invented client-side.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLiveSocket } from "@/lib/useLiveSocket";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";
import {
  DIRECTOR_SCENES,
  SCENE_LABELS,
  formatDirectorUpdatedAt,
  getDirectorState,
  postDirectorLowerThird,
  postDirectorMode,
  postDirectorOverlays,
  postDirectorScene,
} from "@/lib/director";
import type { DirectorState } from "@/lib/director";

function actionErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Request failed.";
}

function Toggle({
  on,
  onChange,
  disabled,
  label,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
  disabled: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-8 w-14 shrink-0 rounded-full border transition-colors ${
        disabled
          ? "cursor-not-allowed border-white/10 bg-white/5 opacity-40"
          : on
            ? "border-[#00f0ff]/60 bg-[#00f0ff]/30"
            : "border-white/20 bg-white/10 hover:border-white/35"
      }`}
    >
      <span
        className={`absolute top-1 h-6 w-6 rounded-full transition-all ${
          on ? "left-7 bg-[#00f0ff]" : "left-1 bg-white/60"
        }`}
      />
    </button>
  );
}

function DirectorConsoleInner() {
  const { token } = useAuth();
  const { directorState, status: wsStatus } = useLiveSocket();

  const [state, setState] = useState<DirectorState | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [subtitle, setSubtitle] = useState("");

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const s = await getDirectorState(token);
      setState(s);
      // Seed the composer from the live lower-third so the operator sees
      // what is currently on screen.
      setTitle(s.lower_third.title);
      setSubtitle(s.lower_third.subtitle);
    } catch (e) {
      setLoadError(actionErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  // WS frames are broadcast on every director change — apply them as they
  // arrive (they are the freshest source).
  useEffect(() => {
    if (directorState) {
      setState(directorState);
      setTitle(directorState.lower_third.title);
      setSubtitle(directorState.lower_third.subtitle);
    }
  }, [directorState]);

  // Reconcile on reconnect: a missed WS broadcast must not leave a stale
  // console behind.
  const prevWs = useRef(wsStatus);
  useEffect(() => {
    if (prevWs.current !== "live" && wsStatus === "live") void load();
    prevWs.current = wsStatus;
  }, [wsStatus, load]);

  const run = useCallback(
    async (label: string, fn: () => Promise<void>) => {
      setBusy(label);
      setActionError(null);
      try {
        await fn();
        // Re-read the authoritative state after every successful write.
        await load();
      } catch (e) {
        setActionError(actionErrorMessage(e));
      } finally {
        setBusy(null);
      }
    },
    [load]
  );

  const auto = state?.mode !== "manual";
  const controlsDisabled = busy !== null || auto;

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-8 text-white">
      <div>
        <h1 className="text-2xl font-bold text-[#00f0ff]">Director Console</h1>
        <p className="text-sm text-white/50">
          Take manual control of the broadcast output, or hand it to the AI
        </p>
      </div>

      {loading && <Loading label="Loading director state" />}

      {!loading && loadError && (
        <ErrorState message={loadError} onRetry={() => void load()} />
      )}

      {!loading && !loadError && !state && (
        <EmptyState message="The director service did not return a state — try again." />
      )}

      {!loading && state && (
        <>
          {/* mode toggle */}
          <Card title="Mode">
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-2 gap-3">
                {(["auto", "manual"] as const).map((mode) => {
                  const active = state.mode === mode;
                  const isBusy = busy === `mode-${mode}`;
                  return (
                    <button
                      key={mode}
                      type="button"
                      disabled={busy !== null}
                      onClick={() =>
                        void run(`mode-${mode}`, () =>
                          postDirectorMode(token, mode)
                        )
                      }
                      aria-pressed={active}
                      className={`rounded-xl border-2 px-6 py-5 font-mono text-lg font-bold uppercase tracking-[0.2em] transition-all ${
                        active
                          ? mode === "auto"
                            ? "border-[#00f0ff] bg-[#00f0ff]/15 text-[#00f0ff] shadow-[0_0_24px_rgba(0,240,255,0.25)]"
                            : "border-amber-300 bg-amber-300/15 text-amber-300 shadow-[0_0_24px_rgba(252,211,77,0.25)]"
                          : "border-white/10 bg-white/5 text-white/40 hover:border-white/25 hover:text-white/70"
                      } ${busy !== null ? "cursor-wait opacity-70" : ""}`}
                    >
                      {isBusy ? "Switching…" : mode}
                    </button>
                  );
                })}
              </div>
              <p className="text-sm text-white/50">
                {state.mode === "auto"
                  ? "AUTO: the AI director chooses scenes and overlays. Switch to MANUAL to take control."
                  : "MANUAL: you are driving the output. Switch to AUTO to hand control back to the AI."}
              </p>
            </div>
          </Card>

          {/* scenes */}
          <Card
            title="Scene"
            action={
              auto ? (
                <Badge tone="slate">Locked in AUTO</Badge>
              ) : (
                <Badge tone="amber">Manual control</Badge>
              )
            }
          >
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {DIRECTOR_SCENES.map((scene) => {
                const active = state.scene === scene;
                return (
                  <button
                    key={scene}
                    type="button"
                    disabled={controlsDisabled}
                    onClick={() =>
                      void run(`scene-${scene}`, () =>
                        postDirectorScene(token, scene)
                      )
                    }
                    aria-pressed={active}
                    className={`rounded-xl border px-4 py-4 font-mono text-sm font-bold uppercase tracking-wider transition-all ${
                      active
                        ? "border-[#00f0ff] bg-[#00f0ff]/15 text-[#00f0ff]"
                        : "border-white/10 bg-white/5 text-white/55 hover:border-white/25 hover:text-white/85"
                    } disabled:cursor-not-allowed disabled:opacity-40`}
                  >
                    {busy === `scene-${scene}`
                      ? "Switching…"
                      : SCENE_LABELS[scene]}
                  </button>
                );
              })}
            </div>
          </Card>

          {/* overlays */}
          <Card
            title="Overlays"
            action={
              auto ? (
                <Badge tone="slate">Locked in AUTO</Badge>
              ) : (
                <Badge tone="amber">Manual control</Badge>
              )
            }
          >
            <div className="flex flex-col gap-4">
              {(
                [
                  { key: "scorebug", label: "Scorebug" },
                  { key: "killfeed", label: "Killfeed" },
                ] as const
              ).map(({ key, label }) => (
                <div
                  key={key}
                  className="flex items-center justify-between gap-4 rounded-lg border border-white/10 bg-black/30 px-4 py-3"
                >
                  <div>
                    <div className="text-sm font-semibold text-white/90">
                      {label}
                    </div>
                    <div className="font-mono text-[11px] uppercase tracking-wider text-white/40">
                      {state.overlays[key] ? "On air" : "Hidden"}
                    </div>
                  </div>
                  <Toggle
                    on={state.overlays[key]}
                    disabled={controlsDisabled}
                    label={`${label} overlay`}
                    onChange={(next) =>
                      void run(`overlay-${key}`, () =>
                        postDirectorOverlays(token, { [key]: next })
                      )
                    }
                  />
                </div>
              ))}
            </div>
          </Card>

          {/* lower third composer */}
          <Card
            title="Lower third"
            action={
              auto ? (
                <Badge tone="slate">Locked in AUTO</Badge>
              ) : state.lower_third.visible ? (
                <Badge tone="green">On air</Badge>
              ) : (
                <Badge tone="slate">Hidden</Badge>
              )
            }
          >
            <div className="flex flex-col gap-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="flex flex-col gap-1.5">
                  <span className="font-mono text-xs uppercase tracking-wider text-zinc-400">
                    Title
                  </span>
                  <input
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    disabled={controlsDisabled}
                    maxLength={80}
                    placeholder="e.g. MATCH POINT"
                    className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-[#00f0ff]/60 disabled:cursor-not-allowed disabled:opacity-40"
                  />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="font-mono text-xs uppercase tracking-wider text-zinc-400">
                    Subtitle
                  </span>
                  <input
                    type="text"
                    value={subtitle}
                    onChange={(e) => setSubtitle(e.target.value)}
                    disabled={controlsDisabled}
                    maxLength={160}
                    placeholder="e.g. Team Falcons need one more round"
                    className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-[#00f0ff]/60 disabled:cursor-not-allowed disabled:opacity-40"
                  />
                </label>
              </div>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  disabled={controlsDisabled}
                  onClick={() =>
                    void run("lt-show", () =>
                      postDirectorLowerThird(token, {
                        visible: true,
                        title: title.trim(),
                        subtitle: subtitle.trim(),
                      })
                    )
                  }
                  className="rounded-lg bg-[#00f0ff] px-6 py-2.5 font-mono text-xs font-bold uppercase tracking-[0.15em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-30"
                >
                  {busy === "lt-show" ? "Showing…" : "Show"}
                </button>
                <button
                  type="button"
                  disabled={controlsDisabled}
                  onClick={() =>
                    void run("lt-hide", () =>
                      postDirectorLowerThird(token, { visible: false })
                    )
                  }
                  className="rounded-lg border border-white/15 bg-white/5 px-6 py-2.5 font-mono text-xs font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#ff3366]/60 hover:text-[#ff3366] disabled:cursor-not-allowed disabled:opacity-30"
                >
                  {busy === "lt-hide" ? "Hiding…" : "Hide"}
                </button>
              </div>
            </div>
          </Card>

          {auto && (
            <div className="rounded-xl border border-[#00f0ff]/25 bg-[#00f0ff]/5 px-4 py-3">
              <p className="text-sm text-[#00f0ff]">
                Scene, overlay and lower-third controls are disabled in AUTO
                mode. Switch to MANUAL to take control.
              </p>
            </div>
          )}

          {actionError && (
            <ErrorState
              message={actionError}
              onRetry={() => setActionError(null)}
            />
          )}

          {/* current state summary */}
          <Card title="Current state">
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              <div className="flex items-center justify-between gap-3 rounded-lg bg-black/30 px-4 py-2.5">
                <dt className="text-white/50">Mode</dt>
                <dd>
                  <Badge tone={state.mode === "auto" ? "cyan" : "amber"}>
                    {state.mode}
                  </Badge>
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3 rounded-lg bg-black/30 px-4 py-2.5">
                <dt className="text-white/50">Scene</dt>
                <dd className="font-semibold text-white/90">
                  {SCENE_LABELS[state.scene]}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3 rounded-lg bg-black/30 px-4 py-2.5">
                <dt className="text-white/50">Overlays</dt>
                <dd className="flex gap-2">
                  <Badge tone={state.overlays.scorebug ? "green" : "slate"}>
                    scorebug {state.overlays.scorebug ? "on" : "off"}
                  </Badge>
                  <Badge tone={state.overlays.killfeed ? "green" : "slate"}>
                    killfeed {state.overlays.killfeed ? "on" : "off"}
                  </Badge>
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3 rounded-lg bg-black/30 px-4 py-2.5">
                <dt className="text-white/50">Lower third</dt>
                <dd>
                  <Badge
                    tone={state.lower_third.visible ? "green" : "slate"}
                  >
                    {state.lower_third.visible ? "visible" : "hidden"}
                  </Badge>
                </dd>
              </div>
            </dl>
            <p className="mt-3 font-mono text-xs text-white/40">
              Last updated by{" "}
              <span className="text-white/70">
                {state.updated_by ?? "—"}
              </span>{" "}
              at{" "}
              <span className="text-white/70">
                {formatDirectorUpdatedAt(state.updated_at)}
              </span>
            </p>
          </Card>
        </>
      )}
    </div>
  );
}

export default function DirectorConsolePage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <DirectorConsoleInner />
    </RequireRole>
  );
}
