"use client";

/**
 * AEGIS AI — true multiview for the director console (Phase 25, P0-3).
 *
 * Broadcast TD layout (research §2.2 / §6 item 7), desktop-first:
 *
 *   ┌───────────── PGM (large) ─────────────┬──────── PVW ────────┐
 *   │ program feed (director active_camera) │ preview candidate   │
 *   ├────────────── GFX Preview ───────────┬── Replay A ──┬── B ──┤
 *   │ next queued story as it would air    │ offered      │       │
 *   └──────────────────────────────────────┴──────────────┴───────┘
 *   Status row: mode · match phase · zone timer · feed health ·
 *               ISO recording · last director decision
 *
 * Grammar guardrails (Blueprint §1 C5/U9): PVW is a PREVIEW — a
 * separate tile beside PGM, never an inset over it, and there is no
 * control anywhere in this component that puts a second live fight on
 * screen (no PiP-with-fight, no split view). A simultaneous second
 * fight is buffered and surfaces only as an offered REPLAY below.
 *
 * Honesty rules: every tile renders real backend state or an explicit
 * standby. The preview comes from director.state.preview (Phase 25
 * contract in lib/director.ts); when it is absent the PVW tile shows
 * "No preview candidate" and streams nothing — never a dummy feed.
 * PGM/PVW follow the backend state on the next poll/WS frame; this
 * component keeps no local copy of what is on air that could diverge.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import { API_BASE } from "@/lib/api";
import {
  SCENE_LABELS,
  formatPreviewScore,
  type DirectorScene,
  type DirectorState,
} from "@/lib/director";
import {
  dismissReplayEntry,
  gateBlockLabel,
  getDecisionLog,
  getFeedHealth,
  getMatchPhase,
  getRecordingStatus,
  getReplayQueue,
  matchPhaseLabel,
  takeReplayEntry,
  type DecisionLogResult,
  type FeedHealth,
  type MatchPhase,
  type RecordingStatus,
  type ReplayQueueEntry,
  type ReplayQueueResult,
} from "@/lib/multiview";
import {
  getPilotStatus,
  povLabel,
  type PilotFeed,
} from "@/lib/observer-pilot";
import { getAvailableStories, isKnownStoryType, type Story } from "@/lib/stories";
import { useZoneClock } from "@/lib/useZoneClock";
import type { ZoneStateFrame } from "@/lib/useLiveSocket";
import { GameVideo } from "@/components/broadcast/game-video";
import { useObservedTeam } from "@/components/broadcast/game-overlays";
import StoryGraphic from "@/components/graphics/animated/StoryGraphic";
import type { Story as W2Story } from "@/components/graphics/animated/types";

/* ------------------------------------------------------------------ */
/* Shared tile shell                                                   */
/* ------------------------------------------------------------------ */

function TileShell({
  badge,
  title,
  accent,
  children,
  footer,
}: {
  badge: React.ReactNode;
  title: string;
  /** Border accent distinguishing the tile's role at a glance. */
  accent: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div
      className={`flex flex-col overflow-hidden rounded-xl border-2 bg-black/40 ${accent}`}
    >
      <div className="flex items-center justify-between gap-3 border-b border-white/10 px-3 py-2">
        <span className="flex items-center gap-2">{badge}</span>
        <span className="min-w-0 truncate font-mono text-[11px] uppercase tracking-[0.15em] text-white/50">
          {title}
        </span>
      </div>
      <div className="relative flex-1">{children}</div>
      {footer && (
        <div className="border-t border-white/10 px-3 py-2">{footer}</div>
      )}
    </div>
  );
}

function StandbyBody({
  headline,
  lines,
}: {
  headline: string;
  lines: string[];
}) {
  return (
    <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 bg-[#0b0d14] px-6 text-center">
      <div className="font-mono text-lg font-extrabold tracking-[0.25em] text-white/70">
        {headline}
      </div>
      {lines.map((line) => (
        <p key={line} className="max-w-md text-xs leading-relaxed text-white/45">
          {line}
        </p>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* POV marker (Phase 29, W4): who is spectated INSIDE a feed          */
/* ------------------------------------------------------------------ */

/**
 * Spectated-player label for a tile header. Shows the real name from
 * the pilot status, or an honest "POV unknown" — never a guess. When
 * the spectated index changes, the badge flashes for a few seconds
 * (the POV-change marker) with the last decision/note reason as its
 * tooltip, so a camera move inside the feed is visible, not silent.
 */
function PovMarker({ feed }: { feed: PilotFeed | null }) {
  const [flash, setFlash] = useState(false);
  const prev = useRef<number | null | undefined>(undefined);
  const idx = feed?.pov.spectated_index ?? null;
  useEffect(() => {
    if (prev.current !== undefined && prev.current !== idx) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 4000);
      prev.current = idx;
      return () => clearTimeout(t);
    }
    prev.current = idx;
    return undefined;
  }, [idx]);
  if (!feed) return null;
  const reason =
    feed.last_decision?.reason ?? feed.last_note?.reason ?? undefined;
  return (
    <span title={reason}>
      <Badge tone={flash ? "amber" : "slate"}>
        {flash ? "POV changed · " : "POV: "}
        {povLabel(feed)}
      </Badge>
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* PGM tile                                                            */
/* ------------------------------------------------------------------ */

function PgmTile({
  state,
  token,
  povFeed,
}: {
  state: DirectorState;
  token: string | null;
  povFeed?: PilotFeed | null;
}) {
  const team = useObservedTeam(
    API_BASE,
    state.active_camera,
    token ?? undefined
  );
  const sceneLabel = SCENE_LABELS[state.scene] ?? state.scene;
  return (
    <TileShell
      accent="border-[#ff3366]/70"
      badge={
        <>
          <Badge tone="red">● PGM</Badge>
          <Badge tone="slate">On air</Badge>
          <PovMarker feed={povFeed ?? null} />
        </>
      }
      title={`CAM ${state.active_camera}${team?.teamName ? ` · ${team.teamName}` : ""} · ${sceneLabel}`}
      footer={
        <p className="text-xs text-white/50">
          Program feed — scene{" "}
          <span className="font-semibold text-white/80">{sceneLabel}</span>,
          camera{" "}
          <span className="font-semibold text-white/80">
            CAM {state.active_camera}
            {team?.teamName ? ` (${team.teamName})` : ""}
          </span>
          . The full composite with overlays airs on /broadcast/output;
          this tile is the same game-video source.
        </p>
      }
    >
      <div className="relative aspect-video w-full overflow-hidden bg-black">
        <GameVideo api={API_BASE} slot={state.active_camera} />
      </div>
    </TileShell>
  );
}

/* ------------------------------------------------------------------ */
/* PVW tile                                                            */
/* ------------------------------------------------------------------ */

function PvwTile({
  state,
  token,
  canTake,
  takeNote,
  takeBusy,
  onTake,
  povFeed,
}: {
  state: DirectorState;
  token: string | null;
  povFeed?: PilotFeed | null;
  /** True only in MANUAL mode (camera/scene writes are MANUAL-gated). */
  canTake: boolean;
  /** Why Take is unavailable in the current mode ("" when available). */
  takeNote: string;
  takeBusy: boolean;
  onTake: () => void;
}) {
  const preview = state.preview;
  const previewTeam = useObservedTeam(
    API_BASE,
    preview?.slot ?? state.active_camera,
    token ?? undefined
  );
  const scoreLabel = preview ? formatPreviewScore(preview.score) : null;
  // The pipeline's reason already embeds its score ("Top candidate: X
  // (score 82.0)") — append ours only when it does not.
  const reasonHasScore = preview?.reason.toLowerCase().includes("score");
  const reasonLine = preview
    ? preview.reason
      ? `PVW — ${preview.reason}${scoreLabel && !reasonHasScore ? ` · score ${scoreLabel}` : ""}`
      : `PVW — CAM ${preview.slot} candidate${scoreLabel ? ` · score ${scoreLabel}` : ""}`
    : "";

  const footer = preview ? (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-white/60">{reasonLine}</p>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={!canTake || takeBusy}
          onClick={onTake}
          title={
            canTake
              ? "Cut the program to the preview feed (existing director camera/scene writes)"
              : takeNote
          }
          className="rounded-lg bg-emerald-400 px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
        >
          {takeBusy ? "Taking…" : "Take preview"}
        </button>
        <span className="text-xs text-white/45">
          {canTake
            ? "Take writes through the backend — PGM/PVW update from director state, never from local state."
            : takeNote}
        </span>
      </div>
    </div>
  ) : undefined;

  return (
    <TileShell
      accent="border-emerald-300/60 border-dashed"
      badge={
        <>
          <Badge tone="green">PVW</Badge>
          <Badge tone="slate">Preview — not on air</Badge>
          <PovMarker feed={povFeed ?? null} />
        </>
      }
      title={
        preview
          ? `CAM ${preview.slot} · ${previewTeam?.teamName ?? preview.team_id ?? "candidate"}${preview.scene ? ` · ${SCENE_LABELS[preview.scene as DirectorScene] ?? preview.scene}` : ""}`
          : "Standby — PVW"
      }
      footer={footer}
    >
      {!preview ? (
        <StandbyBody
          headline="NO PREVIEW CANDIDATE"
          lines={[
            "Standby — PVW. The decision loop has not promoted a next feed.",
            "PVW stays empty rather than streaming a dummy feed; a candidate appears here the moment the pipeline promotes one.",
          ]}
        />
      ) : preview.slot === state.active_camera ? (
        <StandbyBody
          headline="ALREADY ON PROGRAM"
          lines={[
            `The preview candidate is CAM ${preview.slot} — the feed already on PGM. No cut is needed.`,
            reasonLine,
          ]}
        />
      ) : (
        <div className="relative aspect-video w-full overflow-hidden bg-black">
          <GameVideo api={API_BASE} slot={preview.slot} />
          {/* NOT-ON-AIR strip: PVW must never be mistakable for program.
              It labels the preview tile itself — it is not an inset and
              never renders over the PGM tile. */}
          <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 bg-emerald-400/85 py-1 text-center font-mono text-[10px] font-extrabold tracking-[0.3em] text-black">
            PREVIEW — NOT ON AIR
          </div>
        </div>
      )}
    </TileShell>
  );
}

/* ------------------------------------------------------------------ */
/* GFX Preview tile                                                    */
/* ------------------------------------------------------------------ */

function toW2Story(story: Story): W2Story | null {
  if (!isKnownStoryType(story.type)) return null;
  return {
    type: story.type,
    title: story.title,
    body: story.body,
    data: story.data,
  };
}

function GfxTile({ stories }: { stories: Story[] }) {
  // Next queued graphic = first story still waiting to air. A story
  // already triggered is on air (not "next"); dismissed ones are gone.
  const queued = stories.find((s) => s.status === "generated") ?? null;
  const onAir = stories.find((s) => s.on_air) ?? null;
  const w2 = queued ? toW2Story(queued) : null;
  return (
    <TileShell
      accent="border-violet-300/50"
      badge={
        queued ? (
          <Badge tone="green">GFX ready</Badge>
        ) : (
          <Badge tone="slate">Standby — GFX</Badge>
        )
      }
      title={queued ? `Standby — ${queued.title}` : "GFX Preview"}
      footer={
        <p className="text-xs text-white/50">
          {onAir
            ? `On air now: “${onAir.title}”. `
            : ""}
          Preview only — take a graphic from the AI Storytelling panel
          below; this tile never airs anything itself.
        </p>
      }
    >
      {queued ? (
        <div className="flex aspect-video w-full flex-col bg-black/60 p-3">
          <div className="min-h-0 flex-1 overflow-hidden">
            {w2 ? (
              <StoryGraphic story={w2} />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
                <p className="text-sm font-semibold text-white/85">
                  {queued.title}
                </p>
                <p className="text-xs text-white/55">{queued.body}</p>
                <p className="font-mono text-[10px] uppercase tracking-wider text-white/35">
                  No animated renderer for this story type — shown as text
                </p>
              </div>
            )}
          </div>
          <p className="mt-2 truncate text-xs text-white/60">
            <span className="font-semibold text-white/85">{queued.title}</span>
            {queued.body ? ` — ${queued.body}` : ""}
          </p>
        </div>
      ) : (
        <StandbyBody
          headline="STANDBY — GFX"
          lines={[
            "No graphic queued. The next generated story appears here, rendered as it would air, before anyone takes it.",
          ]}
        />
      )}
    </TileShell>
  );
}

/* ------------------------------------------------------------------ */
/* Replay A/B tiles                                                    */
/* ------------------------------------------------------------------ */

function ReplayTile({
  channel,
  entry,
  queueAvailable,
  gateBlockedReason,
  busy,
  notice,
  onTake,
  onDismiss,
}: {
  channel: "A" | "B";
  entry: ReplayQueueEntry | null;
  queueAvailable: boolean;
  /**
   * Queue-level lull-gate refusal for a take right now (humanised
   * blocked_by list), or null when the gate is open / unreported.
   * Applies to READY entries: Take is disabled and this is shown.
   */
  gateBlockedReason: string | null;
  busy: boolean;
  /** Last refusal/error for this entry, surfaced verbatim. */
  notice: string | null;
  onTake: (entry: ReplayQueueEntry) => void;
  onDismiss: (entry: ReplayQueueEntry) => void;
}) {
  const badge = !queueAvailable ? (
    <Badge tone="slate">Replay {channel}</Badge>
  ) : !entry ? (
    <Badge tone="slate">Standby — Replay {channel}</Badge>
  ) : entry.state === "READY" ? (
    <Badge tone="green">Replay ready</Badge>
  ) : entry.state === "TAKEN" ? (
    <Badge tone="cyan">Taken</Badge>
  ) : entry.state === "QUEUED" ? (
    <Badge tone="amber">Queued</Badge>
  ) : (
    <Badge tone="amber">Preparing</Badge>
  );

  const takeBlockedReason =
    entry?.state === "READY" ? gateBlockedReason : null;
  const takeDisabled =
    busy || !entry || entry.state !== "READY" || takeBlockedReason !== null;

  return (
    <TileShell
      accent="border-white/20"
      badge={badge}
      title={`Replay ${channel}`}
      footer={
        entry ? (
          <div className="flex flex-col gap-2">
            {entry.state === "PREPARING" && (
              <p className="text-xs text-white/50">
                Package is still preparing — Take unlocks when it is offered
                READY.
              </p>
            )}
            {entry.state === "QUEUED" && (
              <p className="text-xs text-white/50">
                Prepared and waiting — it is offered (READY) when an A/B
                slot frees.
              </p>
            )}
            {takeBlockedReason && (
              <p className="rounded-lg border border-amber-300/30 bg-amber-300/10 px-2.5 py-1.5 text-xs text-amber-200">
                Take not allowed: {takeBlockedReason}
              </p>
            )}
            {notice && (
              <p className="rounded-lg border border-[#ff3366]/30 bg-[#ff3366]/10 px-2.5 py-1.5 text-xs text-red-200">
                {notice}
              </p>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                disabled={takeDisabled}
                onClick={() => entry && onTake(entry)}
                title={
                  takeBlockedReason ??
                  (entry.state !== "READY"
                    ? "Only a READY (offered) replay can be taken."
                    : "Take this replay to program (backend enforces the lull gate and mode rules)")
                }
                className="rounded-lg bg-[#00f0ff] px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? "Working…" : "Take"}
              </button>
              <button
                type="button"
                disabled={busy || entry.state === "TAKEN"}
                onClick={() => entry && onDismiss(entry)}
                className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#ff3366]/60 hover:text-[#ff3366] disabled:cursor-not-allowed disabled:opacity-40"
              >
                Dismiss
              </button>
            </div>
          </div>
        ) : undefined
      }
    >
      {!queueAvailable ? (
        <StandbyBody
          headline="QUEUE UNAVAILABLE"
          lines={[
            "The replay queue endpoint has not answered — no replay state is shown rather than invented.",
          ]}
        />
      ) : !entry ? (
        <StandbyBody
          headline="NO REPLAY READY"
          lines={[
            `Standby — Replay ${channel}. Packaged moments are offered here after a fight; the director takes them in a lull — never over a live fight.`,
          ]}
        />
      ) : (
        <div className="flex aspect-video w-full flex-col justify-center gap-2 bg-[#0b0d14] px-5">
          <p className="text-base font-bold leading-snug text-white/90">
            {entry.label || "Untitled moment"}
          </p>
          {entry.teams.length > 0 && (
            <p className="text-sm text-white/60">{entry.teams.join(" · ")}</p>
          )}
          {entry.reason && (
            <p className="line-clamp-2 text-xs leading-relaxed text-white/50">
              {entry.reason}
            </p>
          )}
          <p className="font-mono text-[11px] uppercase tracking-wider text-white/40">
            {entry.state}
            {entry.slot ? ` · from CAM ${entry.slot}` : ""}
            {` · ${entry.is_video ? "video clip" : "event replay"}`}
            {entry.created_at
              ? ` · ${new Date(entry.created_at).toLocaleTimeString()}`
              : ""}
          </p>
        </div>
      )}
    </TileShell>
  );
}

/* ------------------------------------------------------------------ */
/* Status row                                                          */
/* ------------------------------------------------------------------ */

function StatusChip({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-lg bg-black/30 px-3 py-2">
      <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.2em] text-white/40">
        {label}
      </span>
      <span className="min-w-0 truncate text-sm text-white/85">{children}</span>
    </div>
  );
}

function StatusRow({
  state,
  zone,
  feedHealth,
  recording,
  decisions,
  phase,
}: {
  state: DirectorState;
  zone: ZoneStateFrame | null;
  feedHealth: FeedHealth;
  recording: RecordingStatus;
  decisions: DecisionLogResult;
  phase: MatchPhase;
}) {
  const zoneClock = useZoneClock(zone);
  const modeLabelText =
    state.mode === "full_auto"
      ? "Full Auto"
      : state.mode === "semi_auto"
        ? "Semi-Auto"
        : state.mode === "clone"
          ? state.clone_grabbed
            ? "Clone (grabbed)"
            : "Clone"
          : "Manual";
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      <StatusChip label="Mode">
        <Badge
          tone={
            state.mode === "full_auto" || state.mode === "clone"
              ? "cyan"
              : state.mode === "semi_auto"
                ? "amber"
                : "green"
          }
        >
          {modeLabelText}
        </Badge>
      </StatusChip>
      <StatusChip label="Phase">
        {phase.available && phase.phase
          ? `${matchPhaseLabel(phase.phase)}${phase.aliveCount !== null ? ` · ${phase.aliveCount} alive` : ""}${phase.zonePhase !== null ? ` · zone ${phase.zonePhase}` : ""}`
          : "unknown"}
      </StatusChip>
      <StatusChip label="Zone">
        {zoneClock.remainingLabel
          ? `${zoneClock.phaseName ?? "Zone"} · ${zoneClock.remainingLabel} left`
          : zoneClock.status === "complete"
            ? "Complete"
            : "—"}
      </StatusChip>
      <StatusChip label="Feeds">
        {feedHealth.available ? (
          <>
            <span className="text-emerald-300">{feedHealth.ok} ok</span>
            {" · "}
            <span className="text-amber-300">
              {feedHealth.degraded} degraded
            </span>
            {" · "}
            <span className="text-red-300">{feedHealth.down} down</span>
            <span className="text-white/40"> / {feedHealth.total}</span>
          </>
        ) : (
          "unknown"
        )}
      </StatusChip>
      <StatusChip label="ISO">
        {!recording.available ? (
          "unknown"
        ) : recording.feedsRecording !== null &&
          recording.totalFeeds !== null &&
          recording.totalFeeds > 0 ? (
          <>
            <span
              className={
                recording.feedsRecording > 0
                  ? "text-red-300"
                  : "text-white/60"
              }
            >
              {recording.feedsRecording > 0 ? "● REC" : "○ Not recording"}
            </span>
            {` ${recording.feedsRecording}/${recording.totalFeeds} feeds`}
          </>
        ) : recording.recording ? (
          <span className="text-red-300">● REC</span>
        ) : (
          <span className="text-white/60">○ Not recording</span>
        )}
      </StatusChip>
      {/* Decision-log tail: hidden entirely when the endpoint is
          unavailable — a last decision is never fabricated. */}
      {decisions.available && (
        <StatusChip label="Last decision">
          {decisions.last
            ? `${decisions.last.action}${decisions.last.reason ? ` — ${decisions.last.reason}` : ""}`
            : "No decisions recorded yet"}
        </StatusChip>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Multiview root                                                      */
/* ------------------------------------------------------------------ */

const REPLAY_POLL_MS = 5_000;
const HEALTH_POLL_MS = 8_000;
const PHASE_POLL_MS = 10_000;
const STORIES_POLL_MS = 15_000;

export function Multiview({
  token,
  state,
  zone,
  onTakePreview,
  takePreviewBusy,
}: {
  token: string | null;
  state: DirectorState;
  zone: ZoneStateFrame | null;
  /** Cut program to the preview via the page's existing director writes. */
  onTakePreview: () => void;
  takePreviewBusy: boolean;
}) {
  const [queue, setQueue] = useState<ReplayQueueResult>({
    available: false,
    entries: [],
    gate: { canTake: null, blockedBy: [] },
  });
  const queueSeen = useRef(false);
  const [feedHealth, setFeedHealth] = useState<FeedHealth>({
    available: false,
    ok: 0,
    degraded: 0,
    down: 0,
    total: 0,
    captureRunning: null,
  });
  const [recording, setRecording] = useState<RecordingStatus>({
    available: false,
    recording: null,
    feedsRecording: null,
    totalFeeds: null,
  });
  const [decisions, setDecisions] = useState<DecisionLogResult>({
    available: false,
    last: null,
  });
  const decisionsSeen = useRef(false);
  const [phase, setPhase] = useState<MatchPhase>({
    available: false,
    phase: null,
    reason: null,
    aliveCount: null,
    zonePhase: null,
  });
  const [stories, setStories] = useState<Story[]>([]);
  /* Phase 29 (W4): per-feed POV (spectated player inside each feed),
     polled from the pilot status — tiles show it in their headers.
     A failed poll keeps the last known POV; no POV is ever invented. */
  const [povFeeds, setPovFeeds] = useState<Record<number, PilotFeed>>({});
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const s = await getPilotStatus(token);
        if (cancelled) return;
        const bySlot: Record<number, PilotFeed> = {};
        for (const feed of s.feeds) bySlot[feed.slot_number] = feed;
        setPovFeeds(bySlot);
      } catch {
        /* keep last known POV; tiles simply show nothing new */
      }
    };
    void load();
    const t = setInterval(() => void load(), 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [token]);
  const [replayBusyId, setReplayBusyId] = useState<string | null>(null);
  const [replayNotices, setReplayNotices] = useState<Record<string, string>>(
    {}
  );

  /* Replay queue poll (5s). A transient failure keeps the last known
     queue rather than flashing "unavailable" (codebase pattern). */
  const refreshQueue = useCallback(async () => {
    const r = await getReplayQueue(token);
    if (r.available) {
      queueSeen.current = true;
      setQueue(r);
    } else if (!queueSeen.current) {
      setQueue(r);
    }
  }, [token]);

  useEffect(() => {
    void refreshQueue();
    const t = setInterval(() => void refreshQueue(), REPLAY_POLL_MS);
    return () => clearInterval(t);
  }, [refreshQueue]);

  /* Feed health + ISO recording poll (8s). */
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const [h, r] = await Promise.all([
        getFeedHealth(token),
        getRecordingStatus(token),
      ]);
      if (cancelled) return;
      if (h.available) setFeedHealth(h);
      if (r.available) setRecording(r);
    };
    void load();
    const t = setInterval(() => void load(), HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [token]);

  /* Decision-log tail poll (8s) — same keep-last-known pattern. */
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const d = await getDecisionLog(token);
      if (cancelled) return;
      if (d.available) {
        decisionsSeen.current = true;
        setDecisions(d);
      } else if (!decisionsSeen.current) {
        setDecisions(d);
      }
    };
    void load();
    const t = setInterval(() => void load(), HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [token]);

  /* Match phase poll (10s). */
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const p = await getMatchPhase(token);
      if (!cancelled && p.available) setPhase(p);
    };
    void load();
    const t = setInterval(() => void load(), PHASE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [token]);

  /* Stories for the GFX preview tile (15s, same cadence as the
     StorytellingPanel below — this tile is its preview surface). */
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await getAvailableStories(token);
        if (!cancelled) setStories(res.stories);
      } catch {
        /* keep last known stories; the tile's standby covers the rest */
      }
    };
    void load();
    const t = setInterval(() => void load(), STORIES_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [token]);

  const runReplayAction = useCallback(
    async (
      entry: ReplayQueueEntry,
      action: (id: string) => Promise<{ ok: boolean; reason: string | null }>
    ) => {
      setReplayBusyId(entry.id);
      setReplayNotices((prev) => {
        const next = { ...prev };
        delete next[entry.id];
        return next;
      });
      try {
        const res = await action(entry.id);
        if (!res.ok) {
          // Surface the backend's refusal verbatim — never hide it.
          setReplayNotices((prev) => ({
            ...prev,
            [entry.id]: res.reason ?? "The backend refused without a reason.",
          }));
        }
      } finally {
        setReplayBusyId(null);
        void refreshQueue();
      }
    },
    [refreshQueue]
  );

  const onTakeReplay = useCallback(
    (entry: ReplayQueueEntry) =>
      void runReplayAction(entry, (id) => takeReplayEntry(token, id)),
    [runReplayAction, token]
  );
  const onDismissReplay = useCallback(
    (entry: ReplayQueueEntry) =>
      void runReplayAction(entry, (id) => dismissReplayEntry(token, id)),
    [runReplayAction, token]
  );

  // Tile assignment: the queue's own A/B offer slots win
  // (slot_label on READY entries); remaining entries fill in state
  // order READY -> QUEUED -> PREPARING -> TAKEN.
  const orderedEntries = [...queue.entries].sort((a, b) => {
    const rank = (e: ReplayQueueEntry) =>
      e.state === "READY"
        ? 0
        : e.state === "QUEUED"
          ? 1
          : e.state === "PREPARING"
            ? 2
            : 3;
    return rank(a) - rank(b);
  });
  const replayA =
    queue.entries.find((e) => e.slot_label === "A") ??
    orderedEntries[0] ??
    null;
  const replayB =
    queue.entries.find(
      (e) => e.slot_label === "B" && e.id !== replayA?.id
    ) ??
    orderedEntries.find((e) => e.id !== replayA?.id) ??
    null;

  // Queue-level lull gate: when closed, every READY take is disabled
  // with the blockers named (fight on program / phase suppression /
  // final-circle lock) — and a take attempt's own 409 refusal is
  // still surfaced per-entry below.
  const gateBlockedReason =
    queue.gate.canTake === false
      ? queue.gate.blockedBy.length > 0
        ? queue.gate.blockedBy.map(gateBlockLabel).join("; ")
        : "the lull gate is closed"
      : null;

  const canTakePreview =
    state.mode === "manual" ||
    (state.mode === "clone" && state.clone_grabbed);
  const takeNote =
    state.mode === "full_auto"
      ? "Full Auto: the AI takes its own preview cuts — switch to Manual to take it yourself."
      : state.mode === "semi_auto"
        ? "Semi-Auto: camera writes need Manual mode — approve the matching suggestion below, or switch to Manual."
        : state.mode === "clone" && !state.clone_grabbed
          ? "Clone: the AI takes its own preview cuts — Grab control in the Clone panel to take it yourself."
          : "";

  return (
    <section
      aria-label="Director multiview"
      className="rounded-xl border border-white/10 bg-white/5 p-4 sm:p-5"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-zinc-400">
          Multiview — Program / Preview
        </h2>
        <Badge tone="slate">TD layout</Badge>
      </div>

      {/* PGM + PVW: side-by-side tiles. PVW is a separate preview tile —
          it never overlays or insets into PGM. */}
      <div className="grid gap-4 xl:grid-cols-2">
        <PgmTile
          state={state}
          token={token}
          povFeed={povFeeds[state.active_camera] ?? null}
        />
        <PvwTile
          state={state}
          token={token}
          povFeed={
            povFeeds[state.preview?.slot ?? state.active_camera] ?? null
          }
          canTake={canTakePreview}
          takeNote={takeNote}
          takeBusy={takePreviewBusy}
          onTake={onTakePreview}
        />
      </div>

      <p className="mt-3 text-xs leading-relaxed text-white/45">
        Grammar note: PVW is a preview, not a second on-air picture. A
        simultaneous second fight is never shown beside the program fight —
        it is buffered and offered as a replay below, to be taken in a lull.
      </p>

      {/* GFX preview + Replay A/B */}
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <GfxTile stories={stories} />
        <ReplayTile
          channel="A"
          entry={replayA}
          queueAvailable={queue.available}
          gateBlockedReason={gateBlockedReason}
          busy={replayA !== null && replayBusyId === replayA.id}
          notice={replayA ? (replayNotices[replayA.id] ?? null) : null}
          onTake={onTakeReplay}
          onDismiss={onDismissReplay}
        />
        <ReplayTile
          channel="B"
          entry={replayB}
          queueAvailable={queue.available}
          gateBlockedReason={gateBlockedReason}
          busy={replayB !== null && replayBusyId === replayB.id}
          notice={replayB ? (replayNotices[replayB.id] ?? null) : null}
          onTake={onTakeReplay}
          onDismiss={onDismissReplay}
        />
      </div>

      {/* Status row */}
      <div className="mt-4">
        <StatusRow
          state={state}
          zone={zone}
          feedHealth={feedHealth}
          recording={recording}
          decisions={decisions}
          phase={phase}
        />
      </div>
    </section>
  );
}
