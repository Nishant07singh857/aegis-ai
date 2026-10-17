'use client';

/**
 * AEGIS AI — broadcast DIRECTOR output page.
 *
 * Route: /broadcast/output
 *
 * A full 1920×1080 layout designed to be screen-captured by a headless
 * Chromium (never clicked): the scorebug top-center, the kill ticker
 * top-right, and the lower-third commentary graphic bottom-left — all
 * rendered in one frame so a single capture gets the whole broadcast.
 *
 * Data: live WS (/ws/live) via useLiveSocket; zone phase / match clock
 * from GET /api/v1/analytics/summary (polled every 15s); latest commentary
 * line from GET /api/v1/commentary/latest (polled every 3s). Zero mock
 * data: WaitingFeed covers the frame until the socket goes live, and each
 * section shows an honest standby line when its feed is quiet.
 *
 * Query params:
 *   ?match=<id>    Display label only (default "LIVE"); the WS carries the
 *                  single live match feed, see scorebug header for details.
 *   ?accent=<hex>  Accent colour, e.g. ?accent=%2300f0ff (default #00f0ff).
 *   ?api=<url>     Backend API base override (also re-points the WS).
 *   ?bg=<mode>     "transparent" (default — OBS/chroma friendly) or "brand"
 *                  (opaque branded backdrop when the capture needs a plate).
 *   ?ttl=<secs>    Kill ticker visibility in seconds (default 12, 3–60).
 *   ?hold=<secs>   Lower-third visibility in seconds (default 6, 2–30).
 */

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  useLiveSocket,
  type KillEvent,
  type Team,
} from '@/lib/useLiveSocket';
import { useOverlayParams } from '@/lib/overlay-params';
import { OverlayRoot, ConnBadge, WaitingFeed } from '@/components/overlay-kit';

const KILL_MAX_VISIBLE = 8;
const KILL_TTL_DEFAULT = 12;
const SUMMARY_POLL_MS = 15_000;
const COMMENTARY_POLL_MS = 3_000;
const HOLD_DEFAULT = 6;

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

function clampNum(
  raw: string | null,
  fallback: number,
  min: number,
  max: number
): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.round(n)));
}

function fmtClock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
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

  const teamBlock = (
    team: Team | undefined,
    align: 'left' | 'right'
  ) => {
    if (!team)
      return (
        <div className="flex w-44 items-center justify-center px-4 py-2">
          <span className="font-mono text-[11px] tracking-[0.25em] text-white/35">
            NO DATA
          </span>
        </div>
      );
    return (
      <div
        className={`flex w-44 items-center gap-3 px-4 py-2 ${
          align === 'right' ? 'flex-row-reverse text-right' : ''
        }`}
      >
        <span
          className="inline-block h-9 w-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: team.color ?? 'var(--ov-accent)' }}
        />
        <div className="min-w-0">
          <div className="truncate text-xl font-extrabold tracking-wide">
            {team.tag ?? team.name}
          </div>
          <div className="font-mono text-[11px] tracking-[0.2em] text-white/60">
            {team.kills ?? 0} KILLS
          </div>
        </div>
        <div
          className={`text-4xl font-black tabular-nums ${
            align === 'right' ? 'mr-auto' : 'ml-auto'
          }`}
        >
          {team.total_pts ?? 0}
        </div>
      </div>
    );
  };

  return (
    <div className="absolute left-1/2 top-4 -translate-x-1/2">
      <div className="overflow-hidden rounded-xl bg-black/70 backdrop-blur-sm">
        <div
          className="h-1 w-full"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        />
        <div className="flex items-stretch divide-x divide-white/10">
          {teamBlock(first, 'left')}
          <div className="flex min-w-40 flex-col items-center justify-center gap-0.5 px-5 py-2">
            <div className="flex items-center gap-2 font-mono text-[11px] tracking-[0.25em] text-white/70">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  live ? 'bg-emerald-400' : 'bg-amber-400 ov-anim-pulse'
                }`}
              />
              {matchLabel}
            </div>
            <div className="font-mono text-sm font-bold tracking-[0.2em]">
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
              {standings.length > 0 ? `${teamsAlive}/${standings.length} ALIVE` : '—'}
            </div>
          </div>
          {teamBlock(second, 'right')}
        </div>
      </div>
    </div>
  );
}

/* -------------------------------- kill ticker ------------------------------ */

function KillRow({ kill }: { kill: KillEvent }) {
  return (
    <div className="ov-anim-slide-in flex items-center gap-2.5 rounded-lg bg-black/70 px-4 py-2 backdrop-blur-sm">
      <div className="min-w-0 flex-1 truncate text-right text-lg font-bold">
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
      <div className="min-w-0 flex-1 truncate text-lg font-bold">
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
}: {
  killFeed: KillEvent[];
  ttl: number;
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

  return (
    <div className="absolute right-5 top-5 flex w-[380px] flex-col gap-2">
      {visible.map((kill) => (
        <KillRow key={kill.id} kill={kill} />
      ))}
    </div>
  );
}

/* -------------------------------- lower third ------------------------------ */

function DirectorLowerThird({
  api,
  hold,
  live,
}: {
  api: string;
  hold: number;
  live: boolean;
}) {
  const [line, setLine] = useState<CommentaryLine | null>(null);
  const [visible, setVisible] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
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
          latest.timestamp > seenTs.current
        ) {
          seenTs.current = latest.timestamp;
          setLine(latest);
          setVisible(true);
          if (hideTimer.current) clearTimeout(hideTimer.current);
          hideTimer.current = setTimeout(() => {
            if (!cancelled) setVisible(false);
          }, hold * 1000);
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
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, [api, hold]);

  const label = (line?.type ?? 'AEGIS AI').toUpperCase();

  if (line && visible) {
    return (
      <div
        key={`${line.timestamp}-${line.text}`}
        className="ov-anim-rise-in absolute bottom-12 left-8 max-w-[46rem]"
      >
        <div className="overflow-hidden rounded-r-xl rounded-l-sm bg-black/75 backdrop-blur-sm">
          <div className="flex items-stretch">
            <div
              className="w-2 shrink-0"
              style={{ backgroundColor: 'var(--ov-accent)' }}
            />
            <div className="px-6 py-4">
              <div
                className="mb-1 font-mono text-xs font-bold tracking-[0.35em]"
                style={{ color: 'var(--ov-accent)' }}
              >
                {label}
              </div>
              <div className="text-3xl font-extrabold leading-snug">
                {line.text}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="absolute bottom-12 left-8">
      <div className="rounded bg-black/50 px-4 py-2 font-mono text-[11px] tracking-[0.3em] text-white/50">
        {live ? 'STANDBY — AWAITING COMMENTARY' : 'WAITING FOR COMMENTARY FEED'}
      </div>
    </div>
  );
}

/* ---------------------------------- page ----------------------------------- */

function BroadcastOutputInner() {
  const params = useOverlayParams();
  const sp = useSearchParams();
  const bg = sp.get('bg') === 'brand' ? 'brand' : 'transparent';
  const ttl = clampNum(sp.get('ttl'), KILL_TTL_DEFAULT, 3, 60);
  const hold = clampNum(sp.get('hold'), HOLD_DEFAULT, 2, 30);

  const { status, retryCount, standings, killFeed } = useLiveSocket(params.api);
  const [summary, setSummary] = useState<SummaryData | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${params.api}/api/v1/analytics/summary`);
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
  }, [params.api]);

  const live = status === 'live';
  const matchLabel =
    params.match === 'live' ? 'LIVE' : params.match.toUpperCase();

  return (
    <OverlayRoot accent={params.accent}>
      {bg === 'brand' && (
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
          <DirectorScorebug
            standings={standings}
            summary={summary}
            matchLabel={matchLabel}
            live={live}
          />
          <DirectorKillfeed killFeed={killFeed} ttl={ttl} />
          <DirectorLowerThird api={params.api} hold={hold} live={live} />
        </>
      )}

      <ConnBadge status={status} retryCount={retryCount} />
    </OverlayRoot>
  );
}

export default function BroadcastOutputPage() {
  return (
    <Suspense fallback={null}>
      <BroadcastOutputInner />
    </Suspense>
  );
}
