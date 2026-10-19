'use client';

/**
 * AEGIS AI — Bible-measured in-game overlay chrome (Phase 18, W2).
 *
 * Pixel positions follow docs/VISUAL_BROADCAST_BIBLE.md §1 (frame-measured
 * from Krafton BMSD 2026 GF Day 1), scaled to the 1920×1080 capture frame.
 * All sizes below are % of frame unless noted:
 *
 *   GameTopBar        x 0-100%,  y 0-4%    thin dark strip
 *   GameKillfeed       x 0-18%,   y 4-20%   3-4 entries, ~17% wide, ~4% tall
 *   GameStandingsPanel x 84-100%, y 8-95%   ALWAYS visible during gameplay
 *   FacecamBox         x 0-12%,   y 82-100% label above, synced to camera
 *   PlayerPanel        x 35-65%,  y 86-100% observed-player strip
 *   Center             x 15-84%,  y 4-82%   kept clean (game video only)
 *
 * Honest-data rule (Phase 9): no feed value is ever invented. Fields the
 * live feed does not supply (per-player alive counts, map name, observed
 * player identity, health/weapon/boosts, player stats) render "—" or an
 * explicit NOT-CONNECTED placeholder — never plausible-looking fakes.
 */

import { useEffect, useState } from 'react';
import type { KillEvent, Team } from '@/lib/useLiveSocket';
import { fmtClock, type ScorebugSummary } from '@/components/graphics/registry';
import { useExitingList } from '@/components/graphics/templates/_anim';

/* ------------------------- observed-team resolution ------------------------ */

/** Slot → assigned team, from GET /api/v1/observers (auth when available). */
export interface ObservedTeam {
  teamId: string | null;
  teamName: string | null;
}

interface ObserverSlotRow {
  slot_number?: number;
  team_id?: string | null;
  team_name?: string | null;
}

/**
 * Resolve the active camera slot to its assigned observer team. The
 * observers endpoint requires auth; the capture page may be
 * unauthenticated, so a failure degrades to null (callers fall back to
 * "CAM n") rather than inventing a team.
 */
export function useObservedTeam(
  api: string,
  slot: number,
  token?: string
): ObservedTeam | null {
  const [team, setTeam] = useState<ObservedTeam | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${api.replace(/\/+$/, '')}/api/v1/observers`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) {
          if (!cancelled) setTeam(null);
          return;
        }
        const body = (await res.json()) as { data?: ObserverSlotRow[] };
        const row = (body.data ?? []).find((r) => r.slot_number === slot);
        if (!cancelled) {
          setTeam(
            row
              ? { teamId: row.team_id ?? null, teamName: row.team_name ?? null }
              : null
          );
        }
      } catch {
        if (!cancelled) setTeam(null);
      }
    };
    load();
    const t = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [api, slot, token]);

  return team;
}

/* --------------------------------- top bar --------------------------------- */

/**
 * Bible §1: thin dark strip, full width, y 0-4%.
 * "Remaining N" | "Teams N" | "Elim N" | Map name | Timer | LIVE badge.
 *
 * Remaining (players) and map name have no live feed today → "—", never
 * guessed. Teams/Elim counts come from the team frames (alive flag).
 */
export function GameTopBar({
  standings,
  summary,
  matchLabel,
}: {
  standings: Team[];
  summary: ScorebugSummary | null;
  matchLabel: string;
}) {
  const teamsTotal = standings.length;
  const teamsElim = standings.filter((t) => t.alive === false).length;
  const teamsAlive = teamsTotal - teamsElim;
  const clock =
    summary?.match_time_seconds != null
      ? fmtClock(summary.match_time_seconds)
      : '—';

  return (
    <div className="absolute inset-x-0 top-0 z-20 flex h-[4%] items-center justify-between bg-black/60 px-5 backdrop-blur-sm">
      <div className="flex items-center gap-6 font-mono text-[13px] font-bold tracking-[0.2em] text-white max-sm:gap-3 max-sm:text-[10px]">
        <span>
          REMAINING <span className="text-white/45">—</span>
        </span>
        <span>
          TEAMS{' '}
          <span className="text-white">
            {teamsTotal > 0 ? teamsAlive : '—'}
          </span>
        </span>
        <span>
          ELIM <span className="text-red-400">{teamsElim}</span>
        </span>
      </div>
      <div className="flex items-center gap-6 font-mono text-[13px] font-bold tracking-[0.2em] text-white/80 max-sm:gap-3 max-sm:text-[10px]">
        <span>
          MAP <span className="text-white/45">—</span>
        </span>
        <span className="tabular-nums text-white">{clock}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-500 ov-anim-pulse" />
        <span className="font-mono text-[13px] font-black tracking-[0.3em] text-white max-sm:text-[10px]">
          {matchLabel}
        </span>
      </div>
    </div>
  );
}

/* -------------------------------- killfeed --------------------------------- */

const KILLFEED_MAX_VISIBLE = 4; // Bible §1: 3-4 entries visible
const killKey = (kill: KillEvent) => kill.id;

function KillfeedEntry({
  kill,
  exiting,
}: {
  kill: KillEvent;
  exiting: boolean;
}) {
  return (
    <div
      className={`flex w-full items-center gap-2 rounded-md bg-black/70 px-3 py-2 backdrop-blur-sm transition-opacity duration-300 ${
        exiting ? 'opacity-0' : 'opacity-100'
      }`}
    >
      <span className="min-w-0 flex-1 truncate text-right text-[15px] font-bold text-white">
        {kill.killer}
      </span>
      {kill.isKnock ? (
        <span
          className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-bold tracking-[0.15em] text-black"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        >
          KNOCK
        </span>
      ) : (
        <span className="shrink-0 font-mono text-sm font-bold text-red-400">
          ✕
        </span>
      )}
      <span className="min-w-0 flex-1 truncate text-[15px] font-bold text-yellow-300">
        {kill.victim}
      </span>
      {kill.weapon ? (
        <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.1em] text-white/50">
          {kill.weapon}
        </span>
      ) : null}
    </div>
  );
}

/**
 * Bible §1: x 0-18%, y 4-20%. Each entry ~17% wide, ~4% tall; dark
 * background, white/yellow text; "Killer [weapon] Victim"; new kills push
 * from the top; old ones fade after the page TTL (~5s per the Bible).
 */
export function GameKillfeed({ events }: { events: KillEvent[] }) {
  const rows = useExitingList(
    events.slice(0, KILLFEED_MAX_VISIBLE),
    killKey
  );
  if (rows.length === 0) return null;

  return (
    <div className="absolute left-0 top-[4%] z-20 flex max-h-[16%] w-[18%] flex-col gap-1.5 overflow-hidden px-2 pt-2 max-sm:w-[44%]">
      {rows.map(({ key, item, exiting }) => (
        <KillfeedEntry key={key} kill={item} exiting={exiting} />
      ))}
    </div>
  );
}

/* ------------------------------ standings panel ---------------------------- */

function AliveBars({ alive }: { alive: boolean }) {
  // Per-player alive status is not in the team frames — the four segments
  // are uniform per team (honest at team granularity, never faked
  // per-player). Eliminated teams dim.
  return (
    <span className="flex shrink-0 items-center gap-[3px]" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <span
          key={i}
          className={`h-1.5 w-5 rounded-sm ${
            alive ? 'bg-white/70' : 'bg-red-500/25'
          }`}
        />
      ))}
    </span>
  );
}

/**
 * Bible §1 + §9 rule #1: x 84-100%, y 8-95%. ALWAYS VISIBLE during
 * gameplay — this component takes no visibility props and has no hide
 * path; the parent renders it unconditionally whenever the feed is live.
 *
 * Header: "Stage N" + zone timer. Rows: rank #, team name, finishes,
 * alive-player bars. Footer: ALIVE | KNOCK | ELIM tabs (KNOCK has no
 * count — the feed carries no knocked-player state, so no count is shown
 * rather than an invented one).
 */
export function GameStandingsPanel({
  standings,
  summary,
}: {
  standings: Team[];
  summary: ScorebugSummary | null;
}) {
  const sorted = [...standings].sort(
    (a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0)
  );
  const aliveCount = sorted.filter((t) => t.alive !== false).length;
  const elimCount = sorted.length - aliveCount;
  const clock =
    summary?.match_time_seconds != null
      ? fmtClock(summary.match_time_seconds)
      : '—';

  return (
    <div className="absolute bottom-[5%] right-0 top-[8%] z-20 flex w-[16%] flex-col bg-black/60 backdrop-blur-sm max-sm:w-[36%]">
      {/* header: stage + zone timer */}
      <div className="flex shrink-0 items-center justify-between border-b border-white/10 px-3 py-2">
        <span className="font-mono text-[12px] font-black tracking-[0.25em] text-white max-sm:text-[10px]">
          {summary?.zone_phase != null ? `STAGE ${summary.zone_phase}` : 'STAGE —'}
        </span>
        <span className="font-mono text-[12px] font-bold tabular-nums text-white/80 max-sm:text-[10px]">
          {clock}
        </span>
      </div>

      {/* 16 ranked rows */}
      <div className="flex min-h-0 flex-1 flex-col justify-start gap-[3px] overflow-hidden px-2 py-2">
        {sorted.length === 0 ? (
          <div className="flex flex-1 items-center justify-center px-2 text-center font-mono text-[10px] tracking-[0.25em] text-white/40">
            AWAITING TEAM DATA
          </div>
        ) : (
          sorted.slice(0, 16).map((team, i) => {
            const eliminated = team.alive === false;
            return (
              <div
                key={team.id}
                className={`flex items-center gap-2 rounded px-1.5 py-[7px] ${
                  eliminated ? 'opacity-40' : ''
                } ${i === 0 ? 'bg-white/10' : ''}`}
              >
                <span className="w-6 shrink-0 font-mono text-[13px] font-black tabular-nums text-white/70 max-sm:text-[11px]">
                  {i + 1}
                </span>
                <span
                  className="h-4 w-1 shrink-0 rounded-full"
                  style={{
                    backgroundColor: team.color ?? 'var(--ov-accent)',
                  }}
                />
                <span className="min-w-0 flex-1 truncate text-[13px] font-extrabold tracking-wide text-white max-sm:text-[11px]">
                  {team.tag ?? team.name}
                </span>
                <span className="shrink-0 font-mono text-[12px] font-bold tabular-nums text-white/85 max-sm:text-[10px]">
                  {team.kills ?? 0}
                </span>
                <AliveBars alive={!eliminated} />
              </div>
            );
          })
        )}
      </div>

      {/* footer tabs */}
      <div className="flex shrink-0 items-center justify-around border-t border-white/10 px-2 py-2 font-mono text-[10px] font-bold tracking-[0.2em] text-white/50 max-sm:text-[8px]">
        <span>
          ALIVE <span className="text-emerald-400">{aliveCount}</span>
        </span>
        <span>KNOCK</span>
        <span>
          ELIM <span className="text-red-400">{elimCount}</span>
        </span>
      </div>
    </div>
  );
}

/* --------------------------------- facecam --------------------------------- */

/**
 * Bible §1: x 0-12%, y 82-100%, label above.
 *
 * Sync: the parent keys this component by the active camera slot, so a
 * director camera switch remounts it instantly (hard cut, per Bible §3 —
 * no fade), simultaneously with the game video layer which is keyed the
 * same way.
 *
 * There is no real facecam feed endpoint today, so this renders an honest
 * placeholder: initials avatar + the observed team name (resolved from
 * the observer-slot mapping) + an explicit NOT-CONNECTED line. NEVER a
 * fake video, and never another player's cam — when the observed team is
 * unknown it falls back to "CAM n", not a guessed identity.
 */
export function FacecamBox({
  slot,
  teamName,
  matchLabel,
}: {
  slot: number;
  teamName: string | null;
  matchLabel: string;
}) {
  const initials = teamName
    ? teamName.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || `#${slot}`
    : `#${slot}`;

  return (
    <>
      {/* label above the cam: tournament/match branding + camera */}
      <div className="absolute bottom-[18%] left-0 z-20 w-[12%] pb-1.5 text-center max-sm:hidden">
        <div className="font-mono text-[11px] font-black tracking-[0.3em] text-white/90">
          {matchLabel}
        </div>
        <div className="mt-0.5 truncate font-mono text-[10px] tracking-[0.2em] text-white/50">
          CAM {slot}
          {teamName ? ` · ${teamName}` : ''}
        </div>
      </div>
      {/* the cam frame */}
      <div
        className="absolute bottom-0 left-0 z-20 flex h-[18%] w-[12%] flex-col items-center justify-center gap-1.5 border-r border-t border-white/10 bg-black/70 backdrop-blur-sm max-sm:hidden"
        aria-label={
          teamName
            ? `Facecam placeholder for ${teamName} (camera ${slot}) — no facecam feed connected`
            : `Facecam placeholder for camera ${slot} — no facecam feed connected`
        }
      >
        <span className="flex h-14 w-14 items-center justify-center rounded-full border border-white/20 bg-white/5 font-mono text-xl font-black tracking-widest text-white/80">
          {initials}
        </span>
        <span className="max-w-full truncate px-2 text-[13px] font-extrabold tracking-wide text-white">
          {teamName ?? `CAM ${slot}`}
        </span>
        <span className="font-mono text-[9px] tracking-[0.25em] text-white/40">
          FACECAM — NO FEED
        </span>
      </div>
    </>
  );
}

/* ------------------------------- player panel ------------------------------ */

/**
 * Bible §1: x 35-65%, y 86-100%. Observed player name, health bar,
 * weapon + ammo, boost levels, stats row.
 *
 * No observed-player identity / health / weapon feed exists today, so
 * the panel is anchored on the observed TEAM (resolved from the camera
 * slot — real data) and every player-level field renders "—" per the
 * Phase 9 honest-data rule.
 */
export function PlayerPanel({
  slot,
  teamTag,
  teamColor,
}: {
  slot: number;
  teamTag: string | null;
  teamColor?: string;
}) {
  return (
    <div className="absolute bottom-0 left-[35%] z-20 flex h-[14%] w-[30%] flex-col justify-between border-t border-white/10 bg-black/65 px-5 py-2.5 backdrop-blur-sm max-sm:left-[12%] max-sm:w-[76%]">
      <div className="flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className="h-8 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: teamColor ?? 'var(--ov-accent)' }}
          />
          <span className="truncate text-[22px] font-black tracking-wide text-white max-sm:text-[16px]">
            {teamTag ?? `CAM ${slot}`}
          </span>
          <span className="shrink-0 font-mono text-[10px] tracking-[0.25em] text-white/45">
            OBSERVED · CAM {slot}
          </span>
        </div>
        <span className="shrink-0 font-mono text-[11px] tracking-[0.2em] text-white/45">
          PLAYER —
        </span>
      </div>

      <div className="flex items-center gap-3">
        <span className="shrink-0 font-mono text-[10px] font-bold tracking-[0.25em] text-white/55">
          HP
        </span>
        <div className="h-2 flex-1 rounded-full bg-white/10" aria-hidden />
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-white/45">
          —
        </span>
      </div>

      <div className="flex items-center gap-5 font-mono text-[11px] tracking-[0.15em] text-white/55">
        <span>
          WEAPON <span className="text-white/35">—</span>
        </span>
        <span>
          AMMO <span className="text-white/35">—</span>
        </span>
        <span className="flex items-center gap-1.5">
          BOOST
          <span className="h-1.5 w-10 rounded-sm bg-white/10" aria-hidden />
          <span className="h-1.5 w-10 rounded-sm bg-white/10" aria-hidden />
          <span className="text-white/35">—</span>
        </span>
      </div>

      <div className="flex items-center gap-6 border-t border-white/10 pt-1.5 font-mono text-[12px] font-bold tracking-[0.2em] text-white/60">
        <span>
          FINISHES <span className="text-white/35">—</span>
        </span>
        <span>
          DAMAGE <span className="text-white/35">—</span>
        </span>
        <span>
          KNOCKS <span className="text-white/35">—</span>
        </span>
      </div>
    </div>
  );
}
