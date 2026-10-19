'use client';

/**
 * AEGIS AI — Map Cam (Phase 10, graphics-staging).
 *
 * Top-down tactical map with live team markers. The island outlines are
 * ORIGINAL schematic drawings — hand-authored SVG polygons, clearly
 * labelled "schematic", not copies of any game asset.
 *
 * ── Live data contract (NOT yet implemented by the backend) ──────────
 * No match service exposes live team positions today (match_service.py
 * only persists post-match stats: placements, kills, damage — no x/y).
 * When a live observer feed lands, it should arrive as either:
 *
 *   WS frame: { "type": "positions.state",
 *               "data": { "map": "erangel"|"miramar"|"rondo",
 *                         "positions": [ { "team_id", "team_name",
 *                                          "x", "y", "alive_count" } ] } }
 *
 *   REST:     GET /api/v1/match/positions  ->
 *             { "map": "erangel",
 *               "positions": [ { "team_id", "team_name",
 *                                "x", "y", "alive_count" } ] }
 *
 * x/y are normalised 0–100 (0,0 = top-left of the viewBox). Until such a
 * feed exists this component renders an honest standby state — it never
 * fabricates positions.
 */

import { useMemo } from 'react';

export type MapId = 'erangel' | 'miramar' | 'rondo';

export interface TeamPosition {
  team_id: string;
  team_name: string;
  /** Normalised 0–100, 0 = top-left of the map frame. */
  x: number;
  /** Normalised 0–100, 0 = top-left of the map frame. */
  y: number;
  alive_count: number;
}

/**
 * Real zone-circle geometry (Phase 18, W3). Normalised 0–100 like team
 * positions. The circle is drawn ONLY from this prop — when it is null
 * no circle is rendered and the parent shows the zone phase + timer
 * (real data) with an honest "zone geometry unavailable" note. Zone
 * geometry is never invented: today the zone service exposes phases and
 * the countdown only; geometry arrives when a live observer/vision feed
 * provides it (see the file header contract).
 */
export interface ZoneGeometry {
  cx: number;
  cy: number;
  /** Radius in the same 0–100 normalised units. */
  r: number;
}

export interface MapCamProps {
  map: MapId;
  positions: TeamPosition[];
  className?: string;
  /** Real safe-zone circle geometry; null = not drawn (never invented). */
  zone?: ZoneGeometry | null;
  /** Real next-zone circle geometry; null = not drawn. */
  nextZone?: ZoneGeometry | null;
  /**
   * viewBox override for zoomed insets (e.g. the Bible's top-right
   * minimap: "40 30 45 45"). Defaults to the full "0 0 100 100" frame.
   */
  viewBox?: string;
}

/** Marker colours, assigned deterministically from team_id (presentation
 *  only — not data). Exported so the map-cam side panel and facecam grid
 *  use the same team colours as the map markers. */
export const MARKER_PALETTE = [
  '#ff0055',
  '#00ffcc',
  '#ffcc00',
  '#7c3aed',
  '#3b82f6',
  '#f97316',
  '#10b981',
  '#f59e0b',
  '#ec4899',
  '#22d3ee',
  '#a3e635',
  '#f43f5e',
  '#8b5cf6',
  '#14b8a6',
  '#eab308',
  '#60a5fa',
];

export function markerColor(teamId: string): string {
  let hash = 0;
  for (let i = 0; i < teamId.length; i += 1) {
    hash = (hash * 31 + teamId.charCodeAt(i)) >>> 0;
  }
  return MARKER_PALETTE[hash % MARKER_PALETTE.length];
}

function clamp100(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.min(100, Math.max(0, n));
}

interface Poi {
  x: number;
  y: number;
  label: string;
}

/** Original stylised schematic outlines. Each map: a set of island
 *  polygons (viewBox 0 0 100 100) plus a few schematic POI dots. These
 *  are deliberately rough — they read as diagrams, not game maps. */
const MAP_ART: Record<
  MapId,
  { label: string; islands: string[]; pois: Poi[] }
> = {
  erangel: {
    label: 'Erangel (schematic)',
    islands: [
      // Large northern mainland — wobbly hand-drawn outline.
      'M18,22 L30,14 L44,17 L52,10 L64,16 L70,28 L66,40 L72,52 L64,60 L54,56 L48,66 L36,70 L28,62 L20,56 L14,44 L16,32 Z',
      // Southern island across a strait.
      'M40,78 L52,74 L60,80 L56,90 L44,92 L36,86 Z',
      // Small islet east.
      'M78,66 L84,64 L86,70 L80,72 Z',
    ],
    pois: [
      { x: 32, y: 30, label: 'City A' },
      { x: 58, y: 44, label: 'Mill' },
      { x: 48, y: 83, label: 'Docks' },
    ],
  },
  miramar: {
    label: 'Miramar (schematic)',
    islands: [
      // Western mass split by a dry river channel from the east mass.
      'M8,18 L26,10 L40,16 L38,30 L30,40 L34,54 L28,66 L16,70 L6,60 L10,44 Z',
      'M46,14 L66,8 L84,16 L88,32 L78,44 L82,58 L70,68 L56,62 L50,48 L44,34 Z',
      // Southern tip island.
      'M52,78 L64,76 L68,86 L56,90 Z',
    ],
    pois: [
      { x: 22, y: 36, label: 'Quarry' },
      { x: 66, y: 30, label: 'Casino' },
      { x: 60, y: 83, label: 'Pier' },
    ],
  },
  rondo: {
    label: 'Rondo (schematic)',
    islands: [
      // West island.
      'M10,30 L22,20 L34,26 L32,44 L22,56 L10,52 Z',
      // East island, slightly larger.
      'M48,22 L64,14 L78,22 L80,40 L72,54 L56,58 L44,46 Z',
      // Northern islet.
      'M60,6 L68,4 L70,10 L62,12 Z',
      // Southern islet chain.
      'M30,78 L38,76 L40,82 L32,84 Z',
      'M48,84 L54,82 L56,88 L50,90 Z',
    ],
    pois: [
      { x: 22, y: 40, label: 'Harbour' },
      { x: 62, y: 38, label: 'Stadium' },
      { x: 52, y: 86, label: 'Ferry' },
    ],
  },
};

/** Approximate pill width for a team-name tag at fontSize 3.2 monospace. */
function namePillWidth(name: string): number {
  return Math.max(10, name.length * 2.05 + 3.4);
}

function MapCamInner({
  map,
  positions,
  zone,
  nextZone,
  viewBox,
}: {
  map: MapId;
  positions: TeamPosition[];
  zone: ZoneGeometry | null;
  nextZone: ZoneGeometry | null;
  viewBox: string;
}) {
  const art = MAP_ART[map] ?? MAP_ART.erangel;

  const grid = useMemo(() => {
    const lines: number[] = [];
    for (let i = 10; i < 100; i += 10) lines.push(i);
    return lines;
  }, []);

  return (
    <svg
      viewBox={viewBox}
      className="h-full w-full"
      role="img"
      aria-label={`${art.label} tactical map`}
      preserveAspectRatio="xMidYMid meet"
    >
      <rect x="0" y="0" width="100" height="100" fill="#060b14" rx="2" />
      {/* grid */}
      {grid.map((g) => (
        <g key={g} stroke="#ffffff" strokeOpacity="0.06" strokeWidth="0.25">
          <line x1={g} y1="0" x2={g} y2="100" />
          <line x1="0" y1={g} x2="100" y2={g} />
        </g>
      ))}
      {/* island outlines */}
      {art.islands.map((d, i) => (
        <path
          key={i}
          d={d}
          fill="#0e2a1d"
          fillOpacity="0.85"
          stroke="#34d399"
          strokeOpacity="0.55"
          strokeWidth="0.6"
        />
      ))}
      {/* schematic POIs */}
      {art.pois.map((p) => (
        <g key={p.label}>
          <circle cx={p.x} cy={p.y} r="1" fill="#94a3b8" fillOpacity="0.7" />
          <text
            x={p.x + 1.8}
            y={p.y + 1.2}
            fontSize="3"
            fill="#94a3b8"
            fillOpacity="0.9"
            fontFamily="ui-monospace, monospace"
          >
            {p.label}
          </text>
        </g>
      ))}
      {/* zone circles — drawn ONLY from real geometry (never invented).
          White = current safe zone, cyan dashed = next zone. */}
      {zone && (
        <g>
          <circle
            cx={clamp100(zone.cx)}
            cy={clamp100(zone.cy)}
            r={Math.max(0.5, Math.min(100, zone.r))}
            fill="#ffffff"
            fillOpacity="0.05"
            stroke="#ffffff"
            strokeOpacity="0.9"
            strokeWidth="0.7"
            strokeDasharray="2.4 1.6"
          />
        </g>
      )}
      {nextZone && (
        <circle
          cx={clamp100(nextZone.cx)}
          cy={clamp100(nextZone.cy)}
          r={Math.max(0.5, Math.min(100, nextZone.r))}
          fill="none"
          stroke="#22d3ee"
          strokeOpacity="0.85"
          strokeWidth="0.6"
          strokeDasharray="1.6 1.4"
        />
      )}
      {/* team markers: coloured dot + name pill (Bible §2: coloured
          pills/tags with player names, e.g. "WTxSpraygod"). Pills render
          only for real positions — never for invented ones. */}
      {positions.map((p) => {
        const x = clamp100(p.x);
        const y = clamp100(p.y);
        const color = markerColor(p.team_id);
        const pillW = namePillWidth(p.team_name);
        return (
          <g key={p.team_id}>
            <title>
              {p.team_name} — {p.alive_count} alive
            </title>
            <circle
              cx={x}
              cy={y}
              r="4.6"
              fill={color}
              fillOpacity="0.25"
              stroke="none"
            />
            <circle
              cx={x}
              cy={y}
              r="2.1"
              fill={color}
              stroke="#ffffff"
              strokeWidth="0.5"
            />
            {/* name pill above the marker */}
            <g transform={`translate(${x - pillW / 2}, ${y - 11.4})`}>
              <rect
                width={pillW}
                height="5.4"
                rx="2.7"
                fill={color}
                fillOpacity="0.92"
                stroke="#ffffff"
                strokeOpacity="0.65"
                strokeWidth="0.4"
              />
              <text
                x={pillW / 2}
                y="3.9"
                textAnchor="middle"
                fontSize="3.2"
                fontWeight="700"
                fill="#ffffff"
                fontFamily="ui-monospace, monospace"
              >
                {p.team_name}
              </text>
            </g>
            {/* alive-count badge right of the marker */}
            <g transform={`translate(${x + 2.8}, ${y + 1.4})`}>
              <rect
                width="7.4"
                height="5"
                rx="1"
                fill="#000000"
                fillOpacity="0.75"
                stroke={color}
                strokeWidth="0.4"
              />
              <text
                x="3.7"
                y="3.8"
                textAnchor="middle"
                fontSize="3.4"
                fontWeight="700"
                fill="#ffffff"
                fontFamily="ui-monospace, monospace"
              >
                {p.alive_count}
              </text>
            </g>
          </g>
        );
      })}
      {/* compass */}
      <g transform="translate(6,10)" opacity="0.8">
        <path d="M0,-3 L1.6,2 L0,1 L-1.6,2 Z" fill="#f8fafc" />
        <text y="7" textAnchor="middle" fontSize="3.4" fill="#f8fafc" fontFamily="ui-monospace, monospace">
          N
        </text>
      </g>
      {/* schematic disclaimer */}
      <text
        x="98"
        y="97.5"
        textAnchor="end"
        fontSize="2.8"
        fill="#64748b"
        fontFamily="ui-monospace, monospace"
        letterSpacing="0.4"
      >
        SCHEMATIC — NOT A GAME ASSET
      </text>
    </svg>
  );
}

/**
 * MapCam — top-down tactical map with team markers.
 *
 * `map` selects the schematic island drawing; `positions` are the live
 * team markers (see the file header for the expected live feed). With no
 * positions the frame renders an honest standby instead of invented dots.
 * `zone` / `nextZone` draw real zone-circle geometry when a live feed
 * provides it — they are never synthesised.
 */
export function MapCam({
  map,
  positions,
  className = '',
  zone = null,
  nextZone = null,
  viewBox = '0 0 100 100',
  chrome = true,
}: MapCamProps & {
  /**
   * Show the "Map cam ·" label and the honest standby overlay. Set false
   * for tiny insets (e.g. the minimap) where the parent frame already
   * carries the standby state — avoids duplicated, unreadably small text.
   */
  chrome?: boolean;
}) {
  const art = MAP_ART[map] ?? MAP_ART.erangel;
  const live = Array.isArray(positions) ? positions : [];

  return (
    <div className={`relative overflow-hidden bg-[#060b14] ${className}`}>
      <MapCamInner
        map={map}
        positions={live}
        zone={zone}
        nextZone={nextZone}
        viewBox={viewBox}
      />
      {chrome && (
        <div className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-emerald-300">
          Map cam · {art.label}
        </div>
      )}
      {chrome && live.length === 0 ? (
        <div className="absolute inset-0 flex items-center justify-center bg-black/35">
          <p className="max-w-[70%] text-center text-xs text-white/60">
            Waiting for position data — no live observer feed connected.
            Team positions and zone-circle geometry are not shown until a
            real feed provides them.
          </p>
        </div>
      ) : (
        <div className="pointer-events-none absolute bottom-2 left-2 max-h-1/2 overflow-hidden rounded bg-black/60 px-2 py-1">
          <ul className="space-y-0.5 text-[10px] text-white/80">
            {live.slice(0, 8).map((p) => (
              <li key={p.team_id} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: markerColor(p.team_id) }}
                />
                <span className="truncate">{p.team_name}</span>
                <span className="text-white/50">×{p.alive_count}</span>
              </li>
            ))}
            {live.length > 8 && (
              <li className="text-white/40">+{live.length - 8} more</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
