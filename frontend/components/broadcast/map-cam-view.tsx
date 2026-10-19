'use client';

/**
 * AEGIS AI — map cam scene view (Phase 18, W3).
 *
 * Rebuilds the map-cam mode to the Visual Broadcast Bible §2 measured
 * layout. The always-on chrome is shared with gameplay (Phase 18, W2
 * game-overlays — the Bible says the top bar and right panel are the SAME
 * in map cam as in gameplay):
 *
 *   - Top bar: GameTopBar (same match info bar as gameplay).
 *   - Top-right: minimap inset (~15% wide) with the zoomed focus area +
 *     location label, docked left of the team panel.
 *   - Right panel: GameStandingsPanel, PERSISTENT (x 84-100%, y 8-95%),
 *     identical to gameplay.
 *   - Bottom-right: 2x2 grid of player facecams (NOT the single facecam
 *     box — this differs from gameplay mode).
 *   - Center: aerial map with team markers (coloured name pills).
 *   - Zone circle (real geometry only) + zone timer always visible.
 *
 * Hold rule (Bible §2): 15-30s per map-cam beat, then a hard cut back to
 * action. The backend enforces the 30s maximum in every director mode
 * (director_service.enforce_scene_holds); this view shows the countdown
 * and re-polls director state at expiry so the cut lands promptly.
 *
 * Honest-data contract: team positions come from a live feed when one
 * exists (see graphics-staging/map-cam.tsx) — with no feed the map shows
 * the zone phase + timer and an honest standby, never invented dots.
 * Facecam cells show real player names (standings / player-stats feeds);
 * there is no player-cam video feed in the system today, so cells are
 * labelled placeholders in the same honest style as the gameplay
 * FacecamBox — never fake video. `camFeeds` wires real per-cell feeds
 * the moment they exist.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { KillEvent, Team, ZoneStateFrame } from '@/lib/useLiveSocket';
import type { PlayerStat, ScorebugSummary } from '@/components/graphics/registry';
import { MAP_CAM_MAX_HOLD_SECONDS } from '@/lib/director';
import { useZoneClock } from '@/lib/useZoneClock';
import {
  MapCam,
  type MapId,
  type TeamPosition,
  type ZoneGeometry,
} from '@/components/graphics-staging/map-cam';
import {
  GameTopBar,
  GameKillfeed,
  GameStandingsPanel,
} from './game-overlays';

export interface MapCamViewProps {
  match: string;
  standings: Team[];
  /** TTL-filtered kills, newest first (same list the gameplay scene shows). */
  visibleKills: KillEvent[];
  players: PlayerStat[];
  zone: ZoneStateFrame | null;
  summary: ScorebugSummary | null;
  /** ISO timestamp of the scene change (director.state scene_entered_at). */
  sceneEnteredAt: string;
  /** Called once when the 30s hold expires (parent re-polls director state). */
  onHoldExpired: () => void;
  /** Live team positions; [] until a position feed exists (honest standby). */
  positions?: TeamPosition[];
  /** Real zone-circle geometry; null = circle not drawn (never invented). */
  zoneGeometry?: ZoneGeometry | null;
  /** Schematic map art to draw; defaults to erangel (the live match's map
   *  is not exposed to the output page today — follow-up). */
  map?: MapId;
  /**
   * Real per-cell facecam feeds (MJPEG/img URLs) when they exist; null
   * per cell = honest labelled placeholder. No player-cam feeds exist in
   * the system today — the default is all placeholders.
   */
  camFeeds?: (string | null)[];
}

function formatHold(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  return `0:${String(s).padStart(2, '0')}`;
}

/** Real player names for the 4 facecam cells: alive teams' rosters first,
 *  then the player-stats feed. Never invented — nulls stay placeholders. */
function facecamNames(
  standings: Team[],
  players: PlayerStat[]
): (string | null)[] {
  const names: (string | null)[] = [];
  const aliveFirst = [...standings].sort((a, b) =>
    a.alive === false ? 1 : b.alive === false ? -1 : 0
  );
  for (const t of aliveFirst) {
    for (const p of t.players ?? []) {
      if (p && names.length < 4 && !names.includes(p)) names.push(p);
    }
    if (names.length >= 4) break;
  }
  for (const ps of players) {
    if (names.length >= 4) break;
    if (ps.playerName && !names.includes(ps.playerName))
      names.push(ps.playerName);
  }
  while (names.length < 4) names.push(null);
  return names;
}

/** Zoomed minimap viewBox around the live position centroid (real,
 *  derived data). Falls back to the full frame with no positions. */
function minimapViewBox(positions: TeamPosition[]): {
  viewBox: string;
  label: string;
  zoomed: boolean;
} {
  if (positions.length === 0) {
    return { viewBox: '0 0 100 100', label: 'MAP OVERVIEW', zoomed: false };
  }
  const cx =
    positions.reduce((s, p) => s + Math.min(100, Math.max(0, p.x)), 0) /
    positions.length;
  const cy =
    positions.reduce((s, p) => s + Math.min(100, Math.max(0, p.y)), 0) /
    positions.length;
  const size = 45;
  const x = Math.min(100 - size, Math.max(0, cx - size / 2));
  const y = Math.min(100 - size, Math.max(0, cy - size / 2));
  return {
    viewBox: `${x.toFixed(1)} ${y.toFixed(1)} ${size} ${size}`,
    label: 'TACTICAL ZOOM',
    zoomed: true,
  };
}

/**
 * One cell of the Bible §2 2x2 facecam grid. Same honest style as the
 * gameplay FacecamBox: initials avatar + real player name + an explicit
 * NO FEED line — never a fake video frame.
 */
function FacecamCell({
  index,
  name,
  feed,
}: {
  index: number;
  name: string | null;
  feed: string | null;
}) {
  const initials = name
    ? name.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || `P${index + 1}`
    : `P${index + 1}`;

  return (
    <div
      className="relative aspect-video overflow-hidden rounded-md border border-white/10 bg-black/70 backdrop-blur-sm"
      aria-label={
        feed
          ? `Player facecam ${index + 1}${name ? ` — ${name}` : ''}`
          : `Player facecam ${index + 1} placeholder${
              name ? ` — ${name}` : ''
            } — no facecam feed connected`
      }
    >
      {feed ? (
        // Real per-player feed, when one exists.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={feed}
          alt={name ? `${name} facecam` : `Facecam ${index + 1}`}
          className="h-full w-full object-cover"
        />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1 px-2 text-center">
          <span className="flex h-9 w-9 items-center justify-center rounded-full border border-white/20 bg-white/5 font-mono text-sm font-black tracking-widest text-white/80">
            {initials}
          </span>
          <span className="max-w-full truncate text-[12px] font-extrabold tracking-wide text-white">
            {name ?? 'AWAITING CAMS'}
          </span>
          <span className="font-mono text-[8px] tracking-[0.25em] text-white/40">
            FACECAM — NO FEED
          </span>
        </div>
      )}
    </div>
  );
}

export function MapCamView({
  match,
  standings,
  visibleKills,
  players,
  zone,
  summary,
  sceneEnteredAt,
  onHoldExpired,
  positions = [],
  zoneGeometry = null,
  map = 'erangel',
  camFeeds = [null, null, null, null],
}: MapCamViewProps) {
  const clock = useZoneClock(zone);
  const mountAt = useRef(Date.now());
  const [now, setNow] = useState(() => Date.now());
  const expiredRef = useRef(false);

  // 1s ticker for the hold countdown (and the zone clock hook's own tick).
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // Hold start: backend stamp when available, else this view's mount time
  // (a backend predating Phase 18 emits no scene_entered_at).
  const enteredMs = useMemo(() => {
    const parsed = new Date(sceneEnteredAt).getTime();
    return Number.isNaN(parsed) ? mountAt.current : parsed;
  }, [sceneEnteredAt]);

  const holdRemaining = Math.max(
    0,
    MAP_CAM_MAX_HOLD_SECONDS - (now - enteredMs) / 1000
  );

  // At expiry, ask the parent to re-poll director state: the backend has
  // (or will, on that read) hard-cut back to "main". Fires once.
  useEffect(() => {
    if (holdRemaining > 0 || expiredRef.current) return;
    expiredRef.current = true;
    const t = setTimeout(onHoldExpired, 400);
    return () => clearTimeout(t);
  }, [holdRemaining, onHoldExpired]);

  const names = useMemo(
    () => facecamNames(standings, players),
    [standings, players]
  );
  const mini = useMemo(() => minimapViewBox(positions), [positions]);

  const zonePillLabel =
    clock.status === 'active' && clock.phaseName
      ? `${clock.phaseName.toUpperCase()} · ${clock.remainingLabel ?? '—:—'}`
      : clock.status === 'complete'
        ? 'ZONE COMPLETE'
        : 'ZONE — · —:—';

  return (
    <div
      className="absolute inset-0 overflow-hidden bg-black"
      aria-label="Map cam — tactical overview"
    >
      {/* center: aerial map (fills the frame; panels overlay on top) */}
      <MapCam
        map={map}
        positions={positions}
        zone={zoneGeometry}
        className="absolute inset-0 h-full w-full"
      />

      {/* top bar: same match info bar as gameplay (Bible §2) */}
      <GameTopBar standings={standings} summary={summary} matchLabel={match} />

      {/* killfeed: top-left, same as gameplay */}
      {visibleKills.length > 0 && <GameKillfeed events={visibleKills} />}

      {/* zone timer + map-cam hold countdown: always visible, top-center
          (Bible §2: "Always shows zone circle + timer"). The circle itself
          renders on the map from real geometry only — never invented. */}
      <div className="absolute left-1/2 top-[5.5%] z-10 flex -translate-x-1/2 items-center gap-2 max-sm:top-[8%]">
        <span className="rounded-full bg-black/70 px-3 py-1 font-mono text-[11px] font-bold tracking-[0.2em] text-amber-300 ring-1 ring-amber-300/30">
          ZONE {zonePillLabel}
        </span>
        <span
          className={`rounded-full px-3 py-1 font-mono text-[11px] font-bold tracking-[0.2em] ring-1 ${
            holdRemaining <= 10
              ? 'bg-red-500/25 text-red-200 ring-red-400/40'
              : 'bg-black/70 text-white/70 ring-white/20'
          }`}
          title="Bible §2: map cam holds 15-30s, then auto-returns to action"
        >
          MAP CAM {formatHold(holdRemaining)}
        </span>
      </div>

      {/* top-right minimap inset (~15% wide): zoomed focus area + location
          label. It docks left of the persistent team panel, which occupies
          x 84-100% (Bible §2 gives both the inset and the panel the
          top-right; the panel wins the edge). */}
      <figure className="absolute right-[17.5%] top-[8%] z-10 w-[13%] max-sm:hidden">
        <div className="overflow-hidden rounded-md ring-1 ring-white/25">
          <MapCam
            map={map}
            positions={positions}
            zone={zoneGeometry}
            viewBox={mini.viewBox}
            chrome={false}
            className="aspect-square w-full"
          />
        </div>
        <figcaption className="mt-1 text-center font-mono text-[9px] font-bold tracking-[0.25em] text-white/60">
          {mini.label}
          {mini.zoomed && clock.phaseName
            ? ` · ${clock.phaseName.toUpperCase()}`
            : ''}
        </figcaption>
      </figure>

      {/* right panel: same 16-team standings, PERSISTENT (x 84-100%,
          y 8-95%) — the identical component gameplay uses (Bible §2) */}
      <GameStandingsPanel standings={standings} summary={summary} />

      {/* bottom-right: 2x2 player facecam grid (Bible §2: grid, NOT the
          single gameplay facecam) */}
      <div className="absolute bottom-[3%] right-[17.5%] z-10 w-[14%] max-sm:hidden">
        <div className="mb-1 font-mono text-[9px] font-bold tracking-[0.3em] text-white/50">
          PLAYER CAMS
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {names.map((name, i) => (
            <FacecamCell
              key={i}
              index={i}
              name={name}
              feed={camFeeds[i] ?? null}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
