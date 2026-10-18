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
}: {
  api: string;
  hold: number;
  accent: string;
  status: ConnStatus;
  templateDef: TemplateDef;
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

export function DirectorOutputView({
  api,
  accent = '#00f0ff',
  match = 'LIVE',
  ttl = 12,
  hold = 6,
  brand = false,
}: DirectorOutputViewProps) {
  const { status, retryCount, reconnect, standings, killFeed, directorState } =
    useLiveSocket(api);
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
  const scorebugDef = resolveTemplate('scorebug', null, effective);
  const killfeedDef = resolveTemplate('killfeed', null, effective);
  const standingsDef = resolveTemplate('standings', null, effective);
  const lowerThirdDef = resolveTemplate('lower-third', null, effective);
  const ScorebugT = scorebugDef.component;
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
      ) : (
        <>
          {showScorebug && (
            <ScorebugT
              standings={standings}
              summary={summary}
              accent={accent}
              status={status}
              matchLabel={match}
            />
          )}

          {/* kill ticker: in the killfeed_focus scene it is replaced by the
              focused feed (no duplication). The template is only mounted
              while there are visible kills — an empty killfeed is normal,
              not a feed failure, so it renders nothing instead of a
              "waiting" state. */}
          {showKillfeed &&
            scene !== 'killfeed_focus' &&
            visibleKills.length > 0 && (
              <KillfeedT
                events={visibleKills}
                accent={accent}
                status={status}
              />
            )}

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

          {/* lower third */}
          {manual ? (
            effective!.lower_third.visible ? (
              <LowerThirdT
                visible
                title={effective!.lower_third.title || 'LOWER THIRD'}
                subtitle={effective!.lower_third.subtitle}
                accent={accent}
                status={status}
                animKey={`${effective!.lower_third.title}-${effective!.lower_third.subtitle}`}
              />
            ) : null
          ) : (
            <AutoLowerThird
              api={api}
              hold={hold}
              accent={accent}
              status={status}
              templateDef={lowerThirdDef}
            />
          )}

          {/* director-mode marker: confirms the operator's scene is live */}
          {manual && (
            <div className="absolute left-5 top-5 rounded bg-black/50 px-3 py-1.5 font-mono text-[10px] tracking-[0.25em] text-white/40">
              MANUAL · {SCENE_LABELS[scene].toUpperCase()}
            </div>
          )}
        </>
      )}

      <ConnBadge status={status} retryCount={retryCount} onRetry={reconnect} />
    </OverlayRoot>
  );
}
