"use client";

/**
 * AEGIS AI — DIRECTOR CONSOLE (Phase 7).
 *
 * Route: /broadcast/director (operator + admin).
 *
 * Live control surface for the director service:
 *   - 3-way mode selector: Full Auto / Semi-Auto / Manual (always enabled)
 *   - scene buttons: Main / Standings / Killfeed focus / Commentary
 *     (MANUAL mode only)
 *   - overlay toggles: scorebug, killfeed (MANUAL mode only)
 *   - lower-third composer: title + subtitle, Show/Hide (MANUAL mode only)
 *   - graphics template picker: per-type template buttons
 *     (SEMI_AUTO + MANUAL; the backend 409s in FULL_AUTO)
 *   - suggestion queue in SEMI-AUTO: live AI proposals with reason,
 *     confidence, expiry countdown, Approve / Reject
 *
 * Modes:
 *   - FULL_AUTO: the AI drives scenes/overlays directly; no approvals.
 *   - SEMI_AUTO: the AI never writes — it proposes suggestions; the
 *     operator approves (applies like a manual write) or rejects.
 *   - MANUAL: the operator controls everything directly; the AI is silent.
 *
 * The current director state is polled on mount and kept fresh via the WS
 * "director.state" frames; the suggestion queue is seeded from
 * GET /director/suggestions and kept fresh via "director.suggestion"
 * frames, reconciled on reconnect. Failed calls surface the backend's
 * message honestly — no state is invented client-side.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { useLiveSocket } from "@/lib/useLiveSocket";
import { RequireRole } from "@/lib/guard";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";
import {
  DIRECTOR_MODE_OPTIONS,
  DIRECTOR_SCENES,
  SCENE_LABELS,
  approveDirectorSuggestion,
  describeSuggestion,
  formatDirectorUpdatedAt,
  getDirectorState,
  getDirectorSuggestions,
  postDirectorLowerThird,
  postDirectorMode,
  postDirectorOverlays,
  postDirectorScene,
  rejectDirectorSuggestion,
  setGraphics,
  suggestionExpiresIn,
} from "@/lib/director";
import type {
  DirectorGraphics,
  DirectorMode,
  DirectorState,
  DirectorSuggestion,
} from "@/lib/director";
import {
  GRAPHIC_STATE_KEYS,
  GRAPHIC_TYPES,
  GRAPHIC_TYPE_LABELS,
  isActiveTemplate,
  templatesFor,
} from "@/lib/graphics";

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

function modeBadgeTone(mode: DirectorMode): "cyan" | "amber" | "green" {
  if (mode === "full_auto") return "cyan";
  if (mode === "semi_auto") return "amber";
  return "green";
}

function modeLabel(mode: DirectorMode): string {
  return (
    DIRECTOR_MODE_OPTIONS.find((o) => o.value === mode)?.label ?? mode
  );
}

function SuggestionCard({
  suggestion,
  nowMs,
  busy,
  onApprove,
  onReject,
}: {
  suggestion: DirectorSuggestion;
  nowMs: number;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const secondsLeft = suggestionExpiresIn(suggestion, nowMs);
  const confidencePct = Math.round(suggestion.confidence * 100);
  const urgent = secondsLeft <= 15;
  return (
    <div className="rounded-xl border border-amber-300/25 bg-amber-300/[0.04] px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-bold text-white/95">
            {describeSuggestion(suggestion)}
          </div>
          <p className="mt-1 text-xs text-white/55">{suggestion.reason}</p>
        </div>
        <Badge tone={urgent ? "red" : "slate"}>
          {secondsLeft > 0 ? `${secondsLeft}s` : "expired"}
        </Badge>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white/10">
          <div
            className="h-full rounded-full bg-[#00f0ff]"
            style={{ width: `${confidencePct}%` }}
            aria-hidden="true"
          />
        </div>
        <span className="shrink-0 font-mono text-[11px] text-white/50">
          {confidencePct}% confident
        </span>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onApprove}
          className="rounded-lg bg-emerald-400 px-5 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
        >
          Approve
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onReject}
          className="rounded-lg border border-white/15 bg-white/5 px-5 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#ff3366]/60 hover:text-[#ff3366] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Reject
        </button>
      </div>
    </div>
  );
}

function DirectorConsoleInner() {
  const { token } = useAuth();
  const {
    directorState,
    directorSuggestion,
    suggestionSeq,
    status: wsStatus,
  } = useLiveSocket();

  const [state, setState] = useState<DirectorState | null>(null);
  const [suggestions, setSuggestions] = useState<DirectorSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [nowMs, setNowMs] = useState(() => Date.now());

  // Live expiry countdowns for the suggestion queue.
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [s, q] = await Promise.all([
        getDirectorState(token),
        getDirectorSuggestions(token),
      ]);
      setState(s);
      // Seed the composer from the live lower-third so the operator sees
      // what is currently on screen.
      setTitle(s.lower_third.title);
      setSubtitle(s.lower_third.subtitle);
      setSuggestions(q);
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

  // Suggestion frames: pending proposals join the queue; decided ones
  // (approved/rejected/expired) leave it. Local countdowns prune expired
  // entries at render time; the backend prunes lazily too.
  useEffect(() => {
    if (!directorSuggestion) return;
    const frame = directorSuggestion;
    setSuggestions((prev) => {
      if (frame.status === "pending") {
        if (prev.some((s) => s.id === frame.id)) {
          return prev.map((s) => (s.id === frame.id ? frame : s));
        }
        return [...prev, frame].sort((a, b) =>
          a.created_at < b.created_at ? -1 : 1
        );
      }
      return prev.filter((s) => s.id !== frame.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestionSeq]);

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

  const mode: DirectorMode = state?.mode ?? "full_auto";
  const manual = mode === "manual";
  const semiAuto = mode === "semi_auto";
  const controlsDisabled = busy !== null || !manual;
  // Graphics template selection is the one operator write allowed in
  // SEMI_AUTO as well — only FULL_AUTO 409s (backend rule).
  const graphicsLocked = mode === "full_auto";
  const graphicsDisabled = busy !== null || graphicsLocked;
  const visibleSuggestions = suggestions.filter(
    (s) => suggestionExpiresIn(s, nowMs) > 0
  );

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-8 text-white">
      <div>
        <h1 className="text-2xl font-bold text-[#00f0ff]">Director Console</h1>
        <p className="text-sm text-white/50">
          Run the broadcast on AI, approve AI suggestions, or drive it
          yourself
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
          {/* 3-way mode selector */}
          <Card title="Mode">
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {DIRECTOR_MODE_OPTIONS.map((option) => {
                  const active = mode === option.value;
                  const isBusy = busy === `mode-${option.value}`;
                  const tone =
                    option.value === "full_auto"
                      ? "cyan"
                      : option.value === "semi_auto"
                        ? "amber"
                        : "green";
                  return (
                    <button
                      key={option.value}
                      type="button"
                      disabled={busy !== null}
                      onClick={() =>
                        void run(`mode-${option.value}`, () =>
                          postDirectorMode(token, option.value)
                        )
                      }
                      aria-pressed={active}
                      className={`flex flex-col items-center gap-1 rounded-xl border-2 px-4 py-4 text-center transition-all ${
                        active
                          ? tone === "cyan"
                            ? "border-[#00f0ff] bg-[#00f0ff]/15 shadow-[0_0_24px_rgba(0,240,255,0.25)]"
                            : tone === "amber"
                              ? "border-amber-300 bg-amber-300/15 shadow-[0_0_24px_rgba(252,211,77,0.25)]"
                              : "border-emerald-300 bg-emerald-300/15 shadow-[0_0_24px_rgba(110,231,183,0.25)]"
                          : "border-white/10 bg-white/5 hover:border-white/25"
                      } ${busy !== null ? "cursor-wait opacity-70" : ""}`}
                    >
                      <span
                        className={`font-mono text-base font-bold uppercase tracking-[0.15em] ${
                          active
                            ? tone === "cyan"
                              ? "text-[#00f0ff]"
                              : tone === "amber"
                                ? "text-amber-300"
                                : "text-emerald-300"
                            : "text-white/40"
                        }`}
                      >
                        {isBusy ? "Switching…" : option.label}
                      </span>
                      <span className="text-xs text-white/50">
                        {option.description}
                      </span>
                    </button>
                  );
                })}
              </div>
              <p className="text-sm text-white/50">
                {mode === "full_auto" &&
                  "FULL AUTO: the AI director drives scenes and overlays. No approvals needed."}
                {mode === "semi_auto" &&
                  "SEMI-AUTO: the AI proposes suggestions below but never acts on its own. Approve or reject each one — nothing applies without you."}
                {mode === "manual" &&
                  "MANUAL: you are driving the output directly. The AI is silent."}
              </p>
            </div>
          </Card>

          {/* suggestion queue (SEMI-AUTO) */}
          {semiAuto && (
            <Card
              title="Suggestion queue"
              action={
                <Badge tone={visibleSuggestions.length ? "amber" : "slate"}>
                  {visibleSuggestions.length} pending
                </Badge>
              }
            >
              {visibleSuggestions.length === 0 ? (
                <EmptyState message="No pending suggestions — the AI proposes only when it has something genuine (e.g. a fight worth a killfeed focus)." />
              ) : (
                <div className="flex flex-col gap-3">
                  {visibleSuggestions.map((s) => (
                    <SuggestionCard
                      key={s.id}
                      suggestion={s}
                      nowMs={nowMs}
                      busy={busy !== null}
                      onApprove={() =>
                        void run(`approve-${s.id}`, () =>
                          approveDirectorSuggestion(token, s.id)
                        )
                      }
                      onReject={() =>
                        void run(`reject-${s.id}`, () =>
                          rejectDirectorSuggestion(token, s.id)
                        )
                      }
                    />
                  ))}
                </div>
              )}
            </Card>
          )}

          {/* scenes */}
          <Card
            title="Scene"
            action={
              manual ? (
                <Badge tone="green">Manual control</Badge>
              ) : (
                <Badge tone="slate">
                  {semiAuto ? "Approve suggestions" : "Locked in Full Auto"}
                </Badge>
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
              manual ? (
                <Badge tone="green">Manual control</Badge>
              ) : (
                <Badge tone="slate">
                  {semiAuto ? "Approve suggestions" : "Locked in Full Auto"}
                </Badge>
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

          {/* graphics templates */}
          <Card
            title="Graphics Templates"
            action={
              graphicsLocked ? (
                <Badge tone="slate">Locked in Full Auto</Badge>
              ) : (
                <Badge tone="green">
                  {semiAuto ? "Semi-Auto" : "Manual control"}
                </Badge>
              )
            }
          >
            <div className="flex flex-col gap-3">
              {GRAPHIC_TYPES.map((type) => (
                <div
                  key={type}
                  className="rounded-lg border border-white/10 bg-black/30 px-4 py-3"
                >
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold text-white/90">
                      {GRAPHIC_TYPE_LABELS[type]}
                    </span>
                    <Link
                      href="/broadcast/graphics"
                      className="font-mono text-[11px] uppercase tracking-wider text-[#00f0ff]/80 transition-colors hover:text-[#00f0ff]"
                    >
                      Gallery →
                    </Link>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {templatesFor(type).map((def) => {
                      const active = isActiveTemplate(state, type, def.id);
                      const isBusy = busy === `gfx-${def.id}`;
                      return (
                        <button
                          key={def.id}
                          type="button"
                          disabled={graphicsDisabled || active}
                          title={
                            graphicsLocked
                              ? "Template changes need Semi-Auto or Manual mode — the backend rejects them in Full Auto (409)."
                              : def.description
                          }
                          onClick={() => {
                            const body: Partial<DirectorGraphics> = {};
                            body[GRAPHIC_STATE_KEYS[type]] = def.id;
                            void run(`gfx-${def.id}`, () =>
                              setGraphics(token, body)
                            );
                          }}
                          aria-pressed={active}
                          className={`rounded-lg border px-3.5 py-2 font-mono text-xs font-bold uppercase tracking-wider transition-all ${
                            active
                              ? "border-[#00f0ff] bg-[#00f0ff]/15 text-[#00f0ff]"
                              : "border-white/10 bg-white/5 text-white/55 hover:border-white/25 hover:text-white/85"
                          } disabled:cursor-not-allowed disabled:opacity-40`}
                        >
                          {isBusy ? "Setting…" : def.name}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
              {graphicsLocked && (
                <p className="text-sm text-white/60">
                  Template changes need Semi-Auto or Manual mode — the
                  backend rejects them while the director is in Full Auto
                  (409).
                </p>
              )}
            </div>
          </Card>

          {/* lower third composer */}
          <Card
            title="Lower third"
            action={
              manual ? (
                state.lower_third.visible ? (
                  <Badge tone="green">On air</Badge>
                ) : (
                  <Badge tone="slate">Hidden</Badge>
                )
              ) : (
                <Badge tone="slate">
                  {semiAuto ? "Approve suggestions" : "Locked in Full Auto"}
                </Badge>
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

          {!manual && (
            <div className="rounded-xl border border-white/15 bg-white/5 px-4 py-3">
              <p className="text-sm text-white/60">
                {mode === "full_auto"
                  ? "AI is driving — no approvals needed. Scene, overlay and lower-third controls are disabled; switch to Semi-Auto or Manual to take control."
                  : "Scene, overlay and lower-third controls are disabled in Semi-Auto. Approve a suggestion above to apply it, or switch to Manual for direct control."}
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
                  <Badge tone={modeBadgeTone(mode)}>{modeLabel(mode)}</Badge>
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
              <div className="flex items-center justify-between gap-3 rounded-lg bg-black/30 px-4 py-2.5 sm:col-span-2">
                <dt className="shrink-0 text-white/50">Templates</dt>
                <dd className="flex flex-wrap justify-end gap-2">
                  {GRAPHIC_TYPES.map((type) => (
                    <Badge key={type} tone="cyan">
                      {GRAPHIC_TYPE_LABELS[type]}:{' '}
                      {templatesFor(type).find(
                        (t) =>
                          t.id ===
                          state.graphics[GRAPHIC_STATE_KEYS[type]]
                      )?.name ?? state.graphics[GRAPHIC_STATE_KEYS[type]]}
                    </Badge>
                  ))}
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
