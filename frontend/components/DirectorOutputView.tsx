'use client';

/**
 * AEGIS AI — shared broadcast output renderer (Phase 6).
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
 * Responsive (W2): overlay graphics are laid out for 1920x1080 capture, so
 * every fixed width / large type size below carries a `max-sm:` counterpart
 * that shrinks it for phone viewports (<640px). At sm:+ the classes are
 * untouched, so /broadcast/output (1920x1080 capture) renders identically.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  useLiveSocket,
  type KillEvent,
  type Team,
} from '@/lib/useLiveSocket';
import { useAuth } from '@/lib/auth';
import {
  getDirectorStateFrom,
  SCENE_LABELS,
} from '@/lib/director';
import type { DirectorState } from '@/lib/director';
import { OverlayRoot, ConnBadge, WaitingFeed } from '@/components/overlay-kit';

const KILL_MAX_VISIBLE = 8;
const COMMENTARY_POLL_MS = 3_000;
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

interface SummaryData {
  zone_phase?: number;
  teams_alive?: number;
  match_time_seconds?: number;
}

interface CommentaryLine {
  timestamp: number;
  text: string;
  type: string;
}

function fmtClock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/** Latest AI commentary line, polled. null until the feed says something. */
function useLatestCommentary(api: string): CommentaryLine | null {
  const [line, setLine] = useState<CommentaryLine | null>(null);
  const seenTs = useRef<number>(-1);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${api}/api/v1/commentary/latest`);
        if (!res.ok) return;
        const body = (await res.json()) as { data?: CommentaryLine[] };
        const log = Array.isArray(body.data) ? body.data : [];
        const latest = log[log.length - 1];
        if (
          !cancelled &&
          latest &&
          typeof latest.text === 'string' &&
          typeof latest.timestamp === 'number' &&
          latest.timestamp > seenTs.current
        ) {
          seenTs.current = latest.timestamp;
          setLine(latest);
        }
      } catch {
        /* commentary endpoint unreachable — keep last line */
      }
    };
    poll();
    const t = setInterval(poll, COMMENTARY_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [api]);

  return line;
}

/* --------------------------------- scorebug -------------------------------- */

function DirectorScorebug({
  standings,
  summary,
  matchLabel,
  live,
}: {
  standings: Team[];
  summary: SummaryData | null;
  matchLabel: string;
  live: boolean;
}) {
  const sorted = useMemo(
    () => [...standings].sort((a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0)),
    [standings]
  );
  const [first, second] = sorted;
  const teamsAlive =
    summary?.teams_alive ?? standings.filter((t) => t.alive).length;

  const teamBlock = (team: Team | undefined, align: 'left' | 'right') => {
    if (!team)
      return (
        <div className="flex w-44 items-center justify-center px-4 py-2 max-sm:w-28 max-sm:px-2">
          <span className="font-mono text-[11px] tracking-[0.25em] text-white/35">
            NO DATA
          </span>
        </div>
      );
    return (
      <div
        className={`flex w-44 items-center gap-3 px-4 py-2 max-sm:w-28 max-sm:gap-2 max-sm:px-2.5 ${
          align === 'right' ? 'flex-row-reverse text-right' : ''
        }`}
      >
        <span
          className="inline-block h-9 w-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: team.color ?? 'var(--ov-accent)' }}
        />
        <div className="min-w-0">
          <div className="truncate text-xl font-extrabold tracking-wide max-sm:text-sm">
            {team.tag ?? team.name}
          </div>
          <div className="font-mono text-[11px] tracking-[0.2em] text-white/60">
            {team.kills ?? 0} KILLS
          </div>
        </div>
        <div
          className={`text-4xl font-black tabular-nums max-sm:text-xl ${
            align === 'right' ? 'mr-auto' : 'ml-auto'
          }`}
        >
          {team.total_pts ?? 0}
        </div>
      </div>
    );
  };

  return (
    <div className="absolute left-1/2 top-4 -translate-x-1/2 max-sm:top-2">
      <div className="overflow-hidden rounded-xl bg-black/70 backdrop-blur-sm">
        <div
          className="h-1 w-full"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="flex items-stretch divide-x divide-white/10">
          {teamBlock(first, 'left')}
          <div className="flex min-w-40 flex-col items-center justify-center gap-0.5 px-5 py-2 max-sm:min-w-0 max-sm:px-3">
            <div className="flex items-center gap-2 font-mono text-[11px] tracking-[0.25em] text-white/70">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  live ? 'bg-emerald-400' : 'bg-amber-400 ov-anim-pulse'
                }`}
              />
              {matchLabel}
            </div>
            <div className="font-mono text-sm font-bold tracking-[0.2em] max-sm:text-xs">
              {summary?.zone_phase != null
                ? `ZONE ${summary.zone_phase}`
                : 'ZONE —'}
              {summary?.match_time_seconds != null && (
                <span className="text-white/60">
                  {' '}
                  · {fmtClock(summary.match_time_seconds)}
                </span>
              )}
            </div>
            <div className="font-mono text-[11px] tracking-[0.2em] text-white/60">
              {standings.length > 0
                ? `${teamsAlive}/${standings.length} ALIVE`
                : '—'}
            </div>
          </div>
          {teamBlock(second, 'right')}
        </div>
      </div>
    </div>
  );
}

/* -------------------------------- kill ticker ------------------------------ */

function KillRow({ kill, large }: { kill: KillEvent; large?: boolean }) {
  return (
    <div
      className={`ov-anim-slide-in flex items-center gap-2.5 rounded-lg bg-black/70 backdrop-blur-sm ${
        large
          ? 'px-6 py-3.5 max-sm:px-4 max-sm:py-2.5'
          : 'px-4 py-2 max-sm:px-3 max-sm:py-1.5'
      }`}
    >
      <div
        className={`min-w-0 flex-1 truncate text-right font-bold ${
          large ? 'text-2xl max-sm:text-lg' : 'text-lg max-sm:text-sm'
        }`}
      >
        <span className="text-white">{kill.killer}</span>
        <span className="ml-2 font-mono text-[11px] tracking-[0.15em] text-white/55">
          {kill.killerTeam}
        </span>
      </div>
      {kill.isKnock ? (
        <span
          className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-bold tracking-[0.15em] text-black"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        >
          KNOCK
        </span>
      ) : (
        <span className="shrink-0 font-mono text-sm font-bold text-red-400">✕</span>
      )}
      <div
        className={`min-w-0 flex-1 truncate font-bold ${
          large ? 'text-2xl max-sm:text-lg' : 'text-lg max-sm:text-sm'
        }`}
      >
        <span className="text-white/90">{kill.victim}</span>
        <span className="ml-2 font-mono text-[11px] tracking-[0.15em] text-white/55">
          {kill.victimTeam}
        </span>
      </div>
      {kill.weapon ? (
        <span className="shrink-0 font-mono text-[11px] uppercase tracking-[0.1em] text-white/50">
          {kill.weapon}
        </span>
      ) : null}
    </div>
  );
}

function DirectorKillfeed({
  killFeed,
  ttl,
  focus,
}: {
  killFeed: KillEvent[];
  ttl: number;
  /** killfeed_focus scene: centered, larger, the centerpiece. */
  focus?: boolean;
}) {
  const [, setTick] = useState(0);

  // Re-render every second so expirations drop off on time.
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const visible = useMemo(() => {
    const nowSec = Date.now() / 1000;
    return killFeed
      .filter((k) => nowSec - k.timestamp <= ttl)
      .slice(0, KILL_MAX_VISIBLE);
  }, [killFeed, ttl]);

  if (focus) {
    return (
      <div className="absolute left-1/2 top-1/2 w-[760px] -translate-x-1/2 -translate-y-1/2 max-sm:w-[calc(100vw-2rem)]">
        <div className="mb-3 text-center font-mono text-sm font-bold tracking-[0.4em] text-white/70">
          KILL FEED
        </div>
        <div className="flex flex-col gap-2.5">
          {visible.length === 0 ? (
            <div className="rounded-xl bg-black/50 px-6 py-4 text-center font-mono text-sm tracking-[0.3em] text-white/45">
              NO KILLS IN THE LAST {ttl}s
            </div>
          ) : (
            visible.map((kill) => <KillRow key={kill.id} kill={kill} large />)
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="absolute right-5 top-5 flex w-[380px] flex-col gap-2 max-sm:left-2.5 max-sm:right-2.5 max-sm:top-24 max-sm:w-auto">
      {visible.map((kill) => (
        <KillRow key={kill.id} kill={kill} />
      ))}
    </div>
  );
}

/* ------------------------------ lower thirds ------------------------------- */

function AutoLowerThird({
  api,
  hold,
  live,
}: {
  api: string;
  hold: number;
  live: boolean;
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

  const label = (line?.type ?? 'AEGIS AI').toUpperCase();

  if (line && visible) {
    return (
      <div
        key={`${line.timestamp}-${line.text}`}
        className="ov-anim-rise-in absolute bottom-12 left-8 max-w-[46rem] max-sm:bottom-5 max-sm:left-4 max-sm:right-4"
      >
        <div className="overflow-hidden rounded-r-xl rounded-l-sm bg-black/75 backdrop-blur-sm">
          <div className="flex items-stretch">
            <div
              className="w-2 shrink-0"
              style={{ backgroundColor: 'var(--ov-accent)' }}
            />
            <div className="px-6 py-4 max-sm:px-4 max-sm:py-3">
              <div
                className="mb-1 font-mono text-xs font-bold tracking-[0.35em]"
                style={{ color: 'var(--ov-accent)' }}
              >
                {label}
              </div>
              <div className="text-3xl font-extrabold leading-snug max-sm:text-xl">
                {line.text}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="absolute bottom-12 left-8 max-sm:bottom-5 max-sm:left-4">
      <div className="rounded bg-black/50 px-4 py-2 font-mono text-[11px] tracking-[0.3em] text-white/50">
        {live ? 'STANDBY — AWAITING COMMENTARY' : 'WAITING FOR COMMENTARY FEED'}
      </div>
    </div>
  );
}

/** Manual-mode lower third: the operator's title/subtitle, exactly as directed. */
function OperatorLowerThird({
  title,
  subtitle,
}: {
  title: string;
  subtitle: string;
}) {
  return (
    <div className="absolute bottom-12 left-8 max-w-[46rem] max-sm:bottom-5 max-sm:left-4 max-sm:right-4">
      <div className="overflow-hidden rounded-r-xl rounded-l-sm bg-black/75 backdrop-blur-sm">
        <div className="flex items-stretch">
          <div
            className="w-2 shrink-0"
            style={{ backgroundColor: 'var(--ov-accent)' }}
          />
          <div className="px-6 py-4 max-sm:px-4 max-sm:py-3">
            <div
              className="mb-1 font-mono text-xs font-bold tracking-[0.35em]"
              style={{ color: 'var(--ov-accent)' }}
            >
              {title || 'LOWER THIRD'}
            </div>
            <div className="text-3xl font-extrabold leading-snug max-sm:text-xl">
              {subtitle || '—'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------ scene extras ------------------------------- */

/** MANUAL standings scene: big standings table, real WS data only. */
function StandingsFocus({ standings }: { standings: Team[] }) {
  const rows = useMemo(
    () =>
      [...standings]
        .sort((a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0))
        .slice(0, 8),
    [standings]
  );

  return (
    <div className="absolute left-1/2 top-1/2 w-[640px] -translate-x-1/2 -translate-y-1/2 max-sm:w-[calc(100vw-2rem)]">
      <div className="overflow-hidden rounded-xl bg-black/75 backdrop-blur-sm">
        <div
          className="h-1.5 w-full"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="px-8 py-6 max-sm:px-4 max-sm:py-4">
          <div className="mb-4 flex items-center justify-between">
            <span className="font-mono text-base font-bold tracking-[0.4em] text-white/85 max-sm:text-sm max-sm:tracking-[0.25em]">
              STANDINGS
            </span>
            <span className="font-mono text-xs tracking-[0.3em] text-white/40">
              TOP {rows.length}
            </span>
          </div>
          {rows.length === 0 ? (
            <div className="py-10 text-center font-mono text-sm tracking-[0.3em] text-white/45">
              NO STANDINGS DATA
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              {rows.map((t, i) => (
                <div
                  key={t.id ?? i}
                  className={`grid grid-cols-[3rem_1fr_4.5rem_4.5rem_4.5rem] items-center gap-2 rounded-lg px-4 py-2.5 max-sm:grid-cols-[1.75rem_1fr_2.75rem_2.75rem_2.75rem] max-sm:gap-1.5 max-sm:px-2.5 max-sm:py-2 ${
                    t.alive === false ? 'bg-white/[0.03] opacity-50' : 'bg-white/[0.06]'
                  }`}
                >
                  <span className="font-mono text-xl font-black text-white/70 max-sm:text-base">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <span className="flex items-center gap-3 truncate max-sm:gap-2">
                    <span
                      className="inline-block h-8 w-1.5 shrink-0 rounded-full"
                      style={{
                        backgroundColor: t.color ?? 'var(--ov-accent)',
                      }}
                    />
                    <span className="truncate text-2xl font-extrabold max-sm:text-lg">
                      {t.tag ?? t.name}
                    </span>
                    {t.alive === false && (
                      <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px] tracking-[0.2em] text-white/50">
                        ELIM
                      </span>
                    )}
                  </span>
                  <span className="text-right font-mono text-lg text-white/70 max-sm:text-sm">
                    {t.kills ?? 0}K
                  </span>
                  <span className="text-right font-mono text-lg text-white/70 max-sm:text-sm">
                    {t.placement_pts ?? 0}P
                  </span>
                  <span className="text-right font-mono text-2xl font-black max-sm:text-lg">
                    {t.total_pts ?? 0}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
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

  const [summary, setSummary] = useState<SummaryData | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${api}/api/v1/analytics/summary`);
        if (!res.ok) return;
        const body = (await res.json()) as { data?: SummaryData };
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
            <DirectorScorebug
              standings={standings}
              summary={summary}
              matchLabel={match}
              live={live}
            />
          )}

          {/* kill ticker: in the killfeed_focus scene it is replaced by the
              large focused feed (no duplication). */}
          {showKillfeed && scene !== 'killfeed_focus' && (
            <DirectorKillfeed killFeed={killFeed} ttl={ttl} />
          )}

          {/* manual scene centerpieces */}
          {manual && scene === 'standings' && (
            <StandingsFocus standings={standings} />
          )}
          {manual && scene === 'killfeed_focus' && showKillfeed && (
            <DirectorKillfeed killFeed={killFeed} ttl={ttl} focus />
          )}
          {manual && scene === 'commentary' && <CommentaryFocus api={api} />}

          {/* lower third */}
          {manual ? (
            effective!.lower_third.visible ? (
              <OperatorLowerThird
                title={effective!.lower_third.title}
                subtitle={effective!.lower_third.subtitle}
              />
            ) : null
          ) : (
            <AutoLowerThird api={api} hold={hold} live={live} />
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
