'use client';

/**
 * AEGIS AI — shared broadcast output renderer (Phase 6; graphics templates
 * wired in Phase 8).
 *
 * Used by /broadcast/output (OBS capture source) and /watch (viewer page).
 * Zero controls: this component only renders. All data comes from the live
 * WS (/ws/live) via useLiveSocket, the analytics/commentary REST endpoints,
 * and the director state service.
 *
 * Director behaviour (lib/director.ts contract):
 *   - Polls GET /api/v1/director/state on mount (auth when available; the
 *     capture page may be unauthenticated) and subscribes to the WS
 *     "director.state" frames; the WS frame is the freshest source and wins.
 *   - On WS reconnect, re-fetches the REST state so a missed broadcast
 *     cannot leave the output showing a stale scene.
 *   - AUTO mode (or unknown director state): the existing AI-driven
 *     behaviour is unchanged.
 *   - MANUAL mode: the operator's scene + overlays + lower-third are
 *     rendered exactly as directed; anything not directed is not rendered
 *     (no standby placeholders are invented for manual mode).
 *   - Empty data -> honest standby states, never invented content.
 *
 * Graphics templates (Phase 8): every graphic is rendered through the
 * template the director state selects
 * (director.state graphics.<type>_template_id), resolved via
 * lib/graphics.ts against the registry in components/graphics. The
 * templates own their visual design and their own honest empty states;
 * this view only feeds them live data and owns scene layout.
 *
 * Responsive (W2): overlay graphics are laid out for 1920x1080 capture, so
 * every fixed width / large type size below carries a `max-sm:` counterpart
 * that shrinks it for phone viewports (<640px). At sm:+ the classes are
 * untouched, so /broadcast/output (1920x1080 capture) renders identically.
 *
 * Video layer (Phase 17 W2): the game video behind the graphics is
 * components/broadcast/game-video (MJPEG from GET /api/v1/video/feed/{slot},
 * slot from director.state active_camera). Overlays render above it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  useLiveSocket,
  type ConnStatus,
  type KillEvent,
} from '@/lib/useLiveSocket';
import { useAuth } from '@/lib/auth';
import {
  getDirectorStateFrom,
  SCENE_LABELS,
} from '@/lib/director';
import type { DirectorState } from '@/lib/director';
import { useLatestCommentary } from '@/lib/useCommentary';
import { usePlayerStats } from '@/lib/usePlayerStats';
import { resolveTemplate } from '@/lib/graphics';
import type { TemplateDef } from '@/lib/graphics';
import type { ScorebugSummary } from '@/components/graphics/registry';
import { OverlayRoot, ConnBadge, WaitingFeed } from '@/components/overlay-kit';
import { GameVideo, type VideoLayerMode } from '@/components/broadcast/game-video';
import { MapCamView } from '@/components/broadcast/map-cam-view';
import {
  GameTopBar,
  GameKillfeed,
  GameStandingsPanel,
  FacecamBox,
  PlayerPanel,
  useObserved,
  useObservedTeam,
} from '@/components/broadcast/game-overlays';
import { TopCenterStats } from '@/components/broadcast/top-center-stats';
import { WwcdCards } from '@/components/graphics/animated';

const KILL_MAX_VISIBLE = 8;
const SUMMARY_POLL_MS = 15_000;
/** Calm post-exhaustion WS retry interval for 24/7 capture pages. */
const OFFLINE_RETRY_MS = 30_000;

export interface DirectorOutputViewProps {
  /** Resolved backend API base (may come from a ?api= override). */
  api: string;
  /** Accent colour, #rrggbb. */
  accent?: string;
  /** Match label, already upper-cased ("LIVE" default). */
  match?: string;
  /** Kill ticker visibility in seconds. */
  ttl?: number;
  /** Lower-third hold in seconds. */
  hold?: number;
  /** Opaque branded backdrop instead of transparent. */
  brand?: boolean;
  /**
   * Video layer mode (Phase 17 W2): "real" streams the active observer's
   * game feed (GET /api/v1/video/feed/{slot}); "simulator" streams a
   * watermarked test pattern (output page ?video=sim).
   */
  video?: VideoLayerMode;
  /**
   * Captions overlay (Phase 22): when true, the currently-active
   * commentary caption cue (GET /api/v1/audio/captions/current, from the
   * WebVTT sidecar store) renders as a bottom-centre caption bar.
   * Default off — the /watch page exposes the viewer toggle.
   */
  captions?: boolean;
  /**
   * Phase 32 (K-C1): short-lived signed feed token, propagated into the
   * game-video <img> src. The /broadcast/output page reads it from its
   * own ?feed_token= query param (headless capture path).
   */
  feedToken?: string;
}

/** TTL-filtered kill list, newest first; re-renders every second so
 *  expirations drop off on time. */
function useVisibleKills(killFeed: KillEvent[], ttl: number): KillEvent[] {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return useMemo(() => {
    const nowSec = Date.now() / 1000;
    return killFeed
      .filter((k) => nowSec - k.timestamp <= ttl)
      .slice(0, KILL_MAX_VISIBLE);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [killFeed, ttl, tick]);
}

/** Auto-mode lower third: latest commentary line, shown for `hold`
 *  seconds per line. The template owns the design and the standby state;
 *  this wrapper owns the timing. */
function AutoLowerThird({
  api,
  hold,
  accent,
  status,
  templateDef,
  positionClass,
}: {
  api: string;
  hold: number;
  accent: string;
  status: ConnStatus;
  templateDef: TemplateDef;
  positionClass?: string;
}) {
  const line = useLatestCommentary(api);
  const [visible, setVisible] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shownTs = useRef<number>(-1);

  useEffect(() => {
    if (line && line.timestamp !== shownTs.current) {
      shownTs.current = line.timestamp;
      setVisible(true);
      if (hideTimer.current) clearTimeout(hideTimer.current);
      hideTimer.current = setTimeout(() => setVisible(false), hold * 1000);
    }
    return () => {
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, [line, hold]);

  const T = templateDef.component;
  return (
    <T
      visible={visible}
      title={(line?.type ?? 'AEGIS AI').toUpperCase()}
      subtitle={line?.text ?? ''}
      accent={accent}
      status={status}
      animKey={line ? String(line.timestamp) : 'standby'}
      positionClass={positionClass}
    />
  );
}

/** MANUAL commentary scene: the latest AI commentary line, large. */
function CommentaryFocus({ api }: { api: string }) {
  const line = useLatestCommentary(api);
  const label = (line?.type ?? 'AEGIS AI').toUpperCase();

  return (
    <div className="absolute left-1/2 top-1/2 w-[900px] -translate-x-1/2 -translate-y-1/2 text-center max-sm:w-[calc(100vw-2rem)]">
      <div className="overflow-hidden rounded-xl bg-black/75 backdrop-blur-sm">
        <div
          className="h-1.5 w-full"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="px-10 py-8 max-sm:px-5 max-sm:py-5">
          <div
            className="mb-3 font-mono text-sm font-bold tracking-[0.4em]"
            style={{ color: 'var(--ov-accent)' }}
          >
            {label}
          </div>
          {line ? (
            <div className="text-4xl font-extrabold leading-snug max-sm:text-2xl">
              {line.text}
            </div>
          ) : (
            <div className="font-mono text-lg tracking-[0.3em] text-white/45 max-sm:text-sm">
              STANDBY — AWAITING COMMENTARY
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------- view ----------------------------------- */

/** Phase 22 captions bar: polls the active caption cue (1s) and renders
 *  it while it is live. Renders nothing when no cue is active — captions
 *  are never invented. */
function CaptionsBar({ api, token }: { api: string; token?: string }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${api}/api/v1/audio/captions/current`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) return;
        const body = (await res.json()) as {
          caption?: { text?: string } | null;
        };
        if (!cancelled) setText(body.caption?.text ?? null);
      } catch {
        /* captions endpoint unreachable — show nothing */
      }
    };
    void poll();
    const t = setInterval(poll, 1000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [api, token]);
  if (!text) return null;
  return (
    <div className="absolute bottom-[16%] left-1/2 z-30 w-[min(900px,80vw)] -translate-x-1/2 text-center">
      <span className="inline-block rounded-md bg-black/80 px-4 py-2 text-2xl font-semibold leading-snug text-white max-sm:text-base">
        {text}
      </span>
    </div>
  );
}

export function DirectorOutputView({
  api,
  accent = '#00f0ff',
  match = 'LIVE',
  ttl = 12,
  hold = 6,
  brand = false,
  video = 'real',
  captions = false,
  feedToken,
}: DirectorOutputViewProps) {
  const {
    status,
    retryCount,
    reconnect,
    standings,
    killFeed,
    directorState,
    zone,
  } = useLiveSocket(api);
  const { token } = useAuth();
  // Per-player stats for the player-leaderboard standings templates; []
  // until a game-stats feed records them (those templates render honestly).
  const players = usePlayerStats(api);
  const [polledState, setPolledState] = useState<DirectorState | null>(null);
  const [stateError, setStateError] = useState<string | null>(null);

  const loadState = useCallback(async () => {
    try {
      const s = await getDirectorStateFrom(api, token ?? undefined);
      setPolledState(s);
      setStateError(null);
    } catch (e) {
      // Capture sources may be unauthenticated; the WS director.state
      // frames are the fallback. Record the error for honesty.
      setStateError(e instanceof Error ? e.message : 'Director state unreachable.');
    }
  }, [api, token]);

  // Poll on mount.
  useEffect(() => {
    void loadState();
  }, [loadState]);

  // Reconcile on reconnect: a missed WS broadcast must not leave a stale scene.
  const prevStatus = useRef(status);
  useEffect(() => {
    if (prevStatus.current !== 'live' && status === 'live') {
      void loadState();
    }
    prevStatus.current = status;
  }, [status, loadState]);

  // Post-exhaustion keep-alive for 24/7 capture pages: once the socket's own
  // backoff gives up (status 'offline'), retry calmly every 30s. The
  // reconnect() call resets the backoff counters and flips to
  // 'connecting'/'reconnecting', which disarms this interval until the
  // socket goes offline again — no reconnect storm.
  useEffect(() => {
    if (status !== 'offline') return;
    const t = setInterval(() => {
      reconnect();
    }, OFFLINE_RETRY_MS);
    return () => clearInterval(t);
  }, [status, reconnect]);

  // The WS frame is freshest (broadcast on every director change);
  // the REST poll is the initial/reconciled snapshot.
  const effective: DirectorState | null = directorState ?? polledState;
  const manual = effective?.mode === 'manual';

  // Active graphics templates, from the director state (normalised, so
  // graphics is always present — pre-Phase-8 payloads get the defaults).
  // NOTE (Phase 18 W2): the always-on gameplay chrome (top bar, killfeed,
  // standings panel, facecam, player panel) is Bible-fixed and rendered by
  // the dedicated game-overlays components below — not via the template
  // picker. The picked templates still drive the manual fullscreen scenes
  // (standings, killfeed_focus) and the lower third.
  const killfeedDef = resolveTemplate('killfeed', null, effective);
  const standingsDef = resolveTemplate('standings', null, effective);
  const lowerThirdDef = resolveTemplate('lower-third', null, effective);
  const KillfeedT = killfeedDef.component;
  const StandingsT = standingsDef.component;
  const LowerThirdT = lowerThirdDef.component;

  const [summary, setSummary] = useState<ScorebugSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${api}/api/v1/analytics/summary`);
        if (!res.ok) return;
        const body = (await res.json()) as { data?: ScorebugSummary };
        if (!cancelled && body.data) setSummary(body.data);
      } catch {
        /* feed may be down — keep last known values */
      }
    };
    load();
    const t = setInterval(load, SUMMARY_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [api]);

  const live = status === 'live';
  const visibleKills = useVisibleKills(killFeed, ttl);
  const standingsTop8 = useMemo(
    () =>
      [...standings]
        .sort((a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0))
        .slice(0, 8),
    [standings]
  );

  const showScorebug = !manual || (effective?.overlays.scorebug ?? false);
  const showKillfeed = !manual || (effective?.overlays.killfeed ?? false);
  const scene = manual ? effective!.scene : 'main';
  // Phase 18 (W3): the map-cam scene is honoured in EVERY director mode
  // (full_auto included) — the backend hard-cuts it back to "main" after
  // 30s max (Bible §2). Other scenes stay manual-only, as before.
  const showMapCam = effective?.scene === 'map_cam';

  // Active observer camera slot — drives the game video layer AND the
  // facecam/player panel (Bible §3: the facecam switches simultaneously
  // with the camera; both are keyed on this slot for a hard cut).
  const slot = effective?.active_camera ?? 1;
  // Slot → observed team, for the facecam + player panel identity
  // (falls back to "CAM n" when the mapping is unavailable — never a
  // guessed team or player).
  const observedTeam = useObservedTeam(api, slot, token ?? undefined);
  // Phase 24 (W3): live observed-player values from the vision-
  // intelligence status snapshot (GET /api/v1/vision-intelligence/status).
  // All-null when unavailable — children render "—", never a guess.
  const observed = useObserved(api, token ?? undefined);
  const observedColor = useMemo(
    () =>
      observedTeam?.teamId
        ? standings.find((t) => t.id === observedTeam.teamId)?.color
        : undefined,
    [standings, observedTeam]
  );

  // Phase 20 (W1, G1): team colors for the WWCD% cards, from live standings.
  const colorByTeam = useMemo(() => {
    const m: Record<string, string> = {};
    for (const t of standings) if (t.color) m[t.id] = t.color;
    return m;
  }, [standings]);

  // Lower-third broadcast zone (Bible §1): the free strip x 13-34%,
  // y 86-100% — clear of the facecam (x 0-12%) and the player panel
  // (x 35-65%). The rise template's animation rests at translate(-50%),
  // so it anchors at the strip's centre (23.5%); the banner drops below
  // the top bar instead of overlapping it.
  const LT_BROADCAST_POS: Record<string, string> = {
    'lowerthird-slide': 'bottom-[1%] left-[13%] w-[21%]',
    'lowerthird-rise': 'bottom-[1%] left-[23.5%] w-[21%]',
    'lowerthird-banner': 'left-0 right-0 top-[4%]',
  };
  const lowerThirdPos =
    LT_BROADCAST_POS[lowerThirdDef.id] ?? 'bottom-[1%] left-[13%] w-[21%]';

  return (
    <OverlayRoot accent={accent}>
      {brand && (
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(1200px 700px at 50% 20%, rgba(0,240,255,0.10), rgba(10,10,15,0.96) 70%), linear-gradient(180deg, #0b0d14 0%, #08090d 100%)',
          }}
        >
          <div className="absolute bottom-4 right-6 font-mono text-xs tracking-[0.4em] text-white/20">
            AEGIS AI · BROADCAST
          </div>
        </div>
      )}

      {!live ? (
        <WaitingFeed what="DIRECTOR FEED" />
      ) : showMapCam ? (
        /* Phase 18 (W3): map-cam scene (Bible §2) — honoured in EVERY
           director mode. The tactical map is the background, so the game
           video layer and gameplay chrome stay off; the view renders the
           full Bible §2 frame: same top bar, minimap inset, persistent
           team panel, 2x2 facecam grid, zone timer. */
        <MapCamView
          match={match}
          standings={standings}
          visibleKills={visibleKills}
          players={players}
          zone={zone}
          summary={summary}
          sceneEnteredAt={effective?.scene_entered_at ?? ''}
          onHoldExpired={() => {
            // The 30s hold expired: re-poll director state. The backend
            // enforces the hard cut on this read (and its 5s ticker
            // broadcasts it), so the view lands back on gameplay promptly
            // even if the WS frame was missed.
            void loadState();
          }}
        />
      ) : (
        <>
          {/* Phase 17 W2: game video layer at the back (z-0, first in DOM).
              The camera slot follows director.state active_camera; all
              graphics overlays below render above it. */}
          <GameVideo api={api} slot={slot} mode={video} feedToken={feedToken} />

          {/* Bible §1 top bar: y 0-4%, x 0-100% thin dark strip. */}
          {showScorebug && (
            <GameTopBar
              standings={standings}
              summary={summary}
              matchLabel={match}
              aliveCount={observed.alive_count}
            />
          )}

          {/* Phase 20 (W1): top-center column (Bible V2 §1c). The PMGO
              slot India leaves empty — WWCD% prediction cards (G1) stacked
              with the PMGC-style observed-team stat card (G2), keyed on
              the camera slot so an observer switch hard-cuts the stat
              card (Bible §3). Both are data-driven and honest: no feed
              value renders as "—", never invented. */}
          <div className="absolute left-1/2 top-[4.5%] z-20 flex w-[min(480px,44vw)] -translate-x-1/2 flex-col gap-1.5 max-sm:w-[88vw]">
            <WwcdCards
              api={api}
              token={token ?? undefined}
              colorByTeam={colorByTeam}
            />
            <TopCenterStats
              key={slot}
              slot={slot}
              teamId={observedTeam?.teamId ?? null}
              teamName={observedTeam?.teamName ?? null}
              teamColor={observedColor}
              standings={standings}
              players={players}
            />
          </div>

          {/* Bible §1 killfeed: x 0-18%, y 4-20%, max 4 entries, newest
              first, ~5s fade. In the killfeed_focus scene it is replaced
              by the focused feed (no duplication). The component renders
              nothing while the feed is quiet — an empty killfeed is
              normal, not a feed failure. */}
          {showKillfeed && scene !== 'killfeed_focus' && visibleKills.length > 0 && (
            <GameKillfeed events={visibleKills} />
          )}

          {/* Bible §1 + §9 rule #1: the team standings panel is ALWAYS
              visible during gameplay (x 84-100%, y 8-95%). It takes no
              visibility props and has no hide path — no mode change,
              camera switch, scene or overlay toggle can remove it. It
              stays up even in the manual fullscreen scenes. */}
          <GameStandingsPanel standings={standings} summary={summary} />

          {/* Bible §1 facecam: x 0-12%, y 82-100%, label above. Keyed on
              the camera slot so it hard-cuts simultaneously with the game
              video when the director switches camera. Honest placeholder
              until a real facecam feed exists — never a fake video. */}
          <FacecamBox
            key={slot}
            slot={slot}
            teamName={observedTeam?.teamName ?? null}
            matchLabel={match}
          />

          {/* Bible §1 player panel: x 35-65%, y 86-100%. Anchored on the
              observed team (real data); player-level fields render "—"
              until a feed supplies them. */}
          <PlayerPanel
            slot={slot}
            teamTag={observedTeam?.teamName ?? null}
            teamColor={observedColor}
            observed={observed}
          />

          {/* manual scene centerpieces */}
          {manual && scene === 'standings' && (
            <StandingsT
              standings={standingsTop8}
              accent={accent}
              status={status}
              killFeed={killFeed}
              players={players}
            />
          )}
          {manual && scene === 'killfeed_focus' && showKillfeed && (
            <KillfeedT
              events={visibleKills}
              accent={accent}
              status={status}
            />
          )}
          {manual && scene === 'commentary' && <CommentaryFocus api={api} />}

          {/* lower third: confined to the Bible-free strip (x 13-34%,
              y 86-100%) via positionClass — clear of the facecam and the
              player panel. */}
          {manual ? (
            effective!.lower_third.visible ? (
              <LowerThirdT
                visible
                title={effective!.lower_third.title || 'LOWER THIRD'}
                subtitle={effective!.lower_third.subtitle}
                accent={accent}
                status={status}
                animKey={`${effective!.lower_third.title}-${effective!.lower_third.subtitle}`}
                positionClass={lowerThirdPos}
              />
            ) : null
          ) : (
            <AutoLowerThird
              api={api}
              hold={hold}
              accent={accent}
              status={status}
              templateDef={lowerThirdDef}
              positionClass={lowerThirdPos}
            />
          )}

          {/* director-mode marker: confirms the operator's scene is live.
              Bottom-right, below the standings panel (ends at y 95%) — the
              old top-left spot overlapped the Bible top bar / killfeed. */}
          {manual && (
            <div className="absolute bottom-2 right-4 z-30 rounded bg-black/50 px-3 py-1.5 font-mono text-[10px] tracking-[0.25em] text-white/40">
              MANUAL · {SCENE_LABELS[scene].toUpperCase()}
            </div>
          )}
        </>
      )}

      {captions && live && <CaptionsBar api={api} token={token ?? undefined} />}

      <ConnBadge status={status} retryCount={retryCount} onRetry={reconnect} />
    </OverlayRoot>
  );
}
