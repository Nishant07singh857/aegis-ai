"use client";

/**
 * AEGIS AI — Analyst dashboard schematic match map (Phase 11, W3).
 *
 * A fully hand-drawn SVG schematic (no external assets): water, stylized
 * island silhouette, faint grid, zone-phase circles, per-team rotation
 * polylines with waypoint dots, drop markers and fight pins.
 *
 * Coordinate space is 0–1000 in both axes (see normCoord in types.ts).
 */

export interface SchematicRotationTeam {
  teamId: string;
  teamName: string;
  color: string;
  points: { x: number; y: number; phase?: string }[];
}

export interface SchematicFight {
  x: number;
  y: number;
  label: string;
  outcome?: string;
}

export interface SchematicZone {
  x: number;
  y: number;
  r: number;
  phase: string;
}

export interface SchematicDrop {
  x: number;
  y: number;
  label: string;
  color: string;
}

/**
 * An ESTIMATED grenade arc (Phase 23, W4). Points are the ground track
 * in schematic space with a modelled height `h` per sample; the arc is
 * drawn bowed upward by apexRatio so the estimate reads as a throw arc.
 * It is ALWAYS rendered dashed and labelled ESTIMATED — the projectile
 * was never tracked (see docs/GRENADE_TRAJECTORY.md).
 */
export interface SchematicTrajectory {
  id: string;
  points: { x: number; y: number; h: number }[];
  apexRatio: number;
  label?: string;
}

const ISLAND_MAIN =
  "M120,310 C150,190 300,120 450,140 C600,160 720,120 800,220 " +
  "C870,300 845,420 900,520 C940,610 880,700 780,730 C700,760 640,860 " +
  "520,880 C400,900 300,830 260,730 C225,645 120,600 110,480 " +
  "C103,400 102,355 120,310 Z";

const ISLAND_SMALL =
  "M820,850 C850,830 890,840 895,870 C898,898 860,915 832,905 C808,896 805,862 820,850 Z";

function markerId(teamId: string): string {
  return `aegis-arrow-${teamId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function outcomeFill(outcome: string | undefined): string {
  const o = (outcome ?? "").toLowerCase();
  if (o.includes("win") || o.includes("won") || o === "w") return "#34d399";
  if (o.includes("loss") || o.includes("lost") || o === "l") return "#ff3366";
  return "#f59e0b";
}

/** Bow one trajectory's ground track into an arc polyline (screen pts). */
function trajectoryArcPoints(
  t: SchematicTrajectory
): { pts: { x: number; y: number }[]; apexPt: { x: number; y: number } } {
  const pts = t.points;
  const start = pts[0];
  const end = pts[pts.length - 1];
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const d = Math.hypot(dx, dy) || 1;
  // Perpendicular, flipped to bow toward the top of the screen.
  let px = -dy / d;
  let py = dx / d;
  if (py > 0) {
    px = -px;
    py = -py;
  }
  const maxH = Math.max(...pts.map((p) => p.h), 0);
  const bow = Math.min(150, Math.max(6, d * t.apexRatio));
  const out = pts.map((p, i) => {
    const f = pts.length > 1 ? i / (pts.length - 1) : 0;
    const shape = maxH > 0 ? p.h / maxH : 4 * f * (1 - f);
    return { x: p.x + px * bow * shape, y: p.y + py * bow * shape };
  });
  const mid = out[Math.floor(out.length / 2)] ?? out[0];
  return { pts: out, apexPt: mid };
}

export function MapSchematic({
  rotations,
  fights,
  zones,
  drops = [],
  trajectories = [],
  selectedTeamId = null,
}: {
  rotations: SchematicRotationTeam[];
  fights: SchematicFight[];
  zones: SchematicZone[];
  drops?: SchematicDrop[];
  trajectories?: SchematicTrajectory[];
  selectedTeamId?: string | null;
}) {
  const dimmed = (teamId: string) =>
    selectedTeamId !== null && selectedTeamId !== teamId ? 0.18 : 1;

  return (
    <div className="relative overflow-hidden rounded-xl border border-white/10 bg-[#0a0a0f]">
      {trajectories.length > 0 ? (
        <div className="absolute left-3 top-3 z-10 rounded-md border border-amber-300/50 bg-black/70 px-2.5 py-1.5 font-mono text-[11px] font-bold uppercase tracking-widest text-amber-300">
          Grenade arc — ESTIMATED · endpoint-based, not tracked
        </div>
      ) : null}
      <svg
        viewBox="0 0 1000 1000"
        className="block h-auto w-full"
        role="img"
        aria-label="Schematic match map with rotation paths, zone phases and fight markers"
      >
        <defs>
          <radialGradient id="aegis-water" cx="50%" cy="45%" r="75%">
            <stop offset="0%" stopColor="#0d1626" />
            <stop offset="100%" stopColor="#070b14" />
          </radialGradient>
          {rotations.map((team) => (
            <marker
              key={markerId(team.teamId)}
              id={markerId(team.teamId)}
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L10,5 L0,10 z" fill={team.color} />
            </marker>
          ))}
        </defs>

        {/* water */}
        <rect x="0" y="0" width="1000" height="1000" fill="url(#aegis-water)" />

        {/* grid */}
        {Array.from({ length: 9 }, (_, i) => (i + 1) * 100).map((g) => (
          <g key={g} stroke="rgba(255,255,255,0.05)" strokeWidth="1">
            <line x1={g} y1="0" x2={g} y2="1000" />
            <line x1="0" y1={g} x2="1000" y2={g} />
          </g>
        ))}

        {/* land */}
        <g>
          <path d={ISLAND_MAIN} fill="#101c2e" stroke="rgba(0,240,255,0.25)" strokeWidth="2" />
          <path d={ISLAND_SMALL} fill="#101c2e" stroke="rgba(0,240,255,0.25)" strokeWidth="2" />
          {/* contour hints */}
          <path
            d="M250,400 C350,320 520,300 640,360 C740,410 720,540 620,600 C520,660 360,620 300,520 C260,460 220,440 250,400 Z"
            fill="none"
            stroke="rgba(255,255,255,0.07)"
            strokeWidth="1.5"
          />
        </g>

        {/* zone phases: dashed circles, phase 1 largest drawn first */}
        {zones.map((z, i) => (
          <g key={`zone-${i}`}>
            <title>{`Zone phase ${z.phase}`}</title>
            <circle
              cx={z.x}
              cy={z.y}
              r={z.r}
              fill="rgba(0,240,255,0.03)"
              stroke="rgba(0,240,255,0.55)"
              strokeWidth="2"
              strokeDasharray="10 8"
            />
            <text
              x={z.x}
              y={z.y - z.r - 8}
              textAnchor="middle"
              fill="rgba(0,240,255,0.75)"
              fontSize="22"
              fontFamily="ui-monospace, monospace"
            >
              {z.phase}
            </text>
          </g>
        ))}

        {/* drop markers */}
        {drops.map((d, i) => (
          <g key={`drop-${i}`} opacity={selectedTeamId ? 0.9 : 1}>
            <title>{`Drop: ${d.label}`}</title>
            <rect
              x={d.x - 9}
              y={d.y - 9}
              width="18"
              height="18"
              rx="4"
              fill="none"
              stroke={d.color}
              strokeWidth="2.5"
              transform={`rotate(45 ${d.x} ${d.y})`}
            />
            <text
              x={d.x}
              y={d.y + 34}
              textAnchor="middle"
              fill="rgba(255,255,255,0.65)"
              fontSize="18"
              fontFamily="ui-monospace, monospace"
            >
              {d.label.length > 14 ? `${d.label.slice(0, 13)}…` : d.label}
            </text>
          </g>
        ))}

        {/* rotation paths */}
        {rotations.map((team) => (
          <g key={team.teamId} opacity={dimmed(team.teamId)}>
            <title>{`Rotation path: ${team.teamName}`}</title>
            {team.points.length > 1 && (
              <polyline
                points={team.points.map((p) => `${p.x},${p.y}`).join(" ")}
                fill="none"
                stroke={team.color}
                strokeWidth="4"
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity="0.85"
                markerEnd={`url(#${markerId(team.teamId)})`}
              />
            )}
            {team.points.map((p, i) => (
              <g key={i}>
                <title>
                  {`${team.teamName} · waypoint ${i + 1}${p.phase ? ` · ${p.phase}` : ""}`}
                </title>
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={i === 0 ? 11 : 8}
                  fill={team.color}
                  stroke="#0a0a0f"
                  strokeWidth="3"
                />
                {i === 0 && (
                  <text
                    x={p.x}
                    y={p.y - 20}
                    textAnchor="middle"
                    fill={team.color}
                    fontSize="20"
                    fontWeight="700"
                    fontFamily="ui-monospace, monospace"
                  >
                    {team.teamName.length > 10
                      ? `${team.teamName.slice(0, 9)}…`
                      : team.teamName}
                  </text>
                )}
              </g>
            ))}
          </g>
        ))}

        {/* grenade trajectories — ESTIMATED arcs (endpoint-based only) */}
        {trajectories.map((t) => {
          const { pts, apexPt } = trajectoryArcPoints(t);
          const start = t.points[0];
          const end = t.points[t.points.length - 1];
          return (
            <g key={`traj-${t.id}`}>
              <title>
                {`ESTIMATED grenade arc${t.label ? ` · ${t.label}` : ""} — endpoint-based estimate, projectile not tracked`}
              </title>
              <polyline
                points={pts.map((p) => `${p.x},${p.y}`).join(" ")}
                fill="none"
                stroke="#f59e0b"
                strokeWidth="3.5"
                strokeDasharray="9 7"
                strokeLinecap="round"
                opacity="0.9"
              />
              {/* thrower endpoint */}
              <circle
                cx={start.x}
                cy={start.y}
                r="8"
                fill="none"
                stroke="#f59e0b"
                strokeWidth="3"
              />
              {/* explosion endpoint */}
              <circle cx={end.x} cy={end.y} r="7" fill="#f59e0b" />
              <circle
                cx={end.x}
                cy={end.y}
                r="14"
                fill="none"
                stroke="#f59e0b"
                strokeWidth="2"
                opacity="0.6"
              />
              {/* the ESTIMATED label is visible text, never just a tooltip */}
              <text
                x={apexPt.x}
                y={apexPt.y - 14}
                textAnchor="middle"
                fill="#fbbf24"
                fontSize="21"
                fontWeight="800"
                fontFamily="ui-monospace, monospace"
              >
                ESTIMATED
              </text>
              {t.label ? (
                <text
                  x={apexPt.x}
                  y={apexPt.y + 14}
                  textAnchor="middle"
                  fill="rgba(251,191,36,0.7)"
                  fontSize="16"
                  fontFamily="ui-monospace, monospace"
                >
                  {t.label.length > 26 ? `${t.label.slice(0, 25)}…` : t.label}
                </text>
              ) : null}
            </g>
          );
        })}

        {/* fight pins */}
        {fights.map((f, i) => (
          <g key={`fight-${i}`}>
            <title>{f.label}</title>
            <line
              x1={f.x}
              y1={f.y}
              x2={f.x}
              y2={f.y - 30}
              stroke={outcomeFill(f.outcome)}
              strokeWidth="4"
            />
            <circle
              cx={f.x}
              cy={f.y - 38}
              r="12"
              fill={outcomeFill(f.outcome)}
              stroke="#0a0a0f"
              strokeWidth="3"
            />
            <text
              x={f.x}
              y={f.y - 31}
              textAnchor="middle"
              fill="#0a0a0f"
              fontSize="15"
              fontWeight="800"
              fontFamily="ui-monospace, monospace"
            >
              ✕
            </text>
          </g>
        ))}
      </svg>

      {/* legend */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-white/10 px-4 py-3 text-xs text-white/55">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-6 rounded bg-cyan-300" />
          rotation path
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-full border-2 border-dashed border-cyan-300/70" />
          zone phase
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rotate-45 rounded-[3px] border-2 border-white/60" />
          drop point
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-full bg-emerald-400" />
          fight won
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-full bg-rose-500" />
          fight lost
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-full bg-amber-400" />
          fight unknown
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0 w-6 border-t-[3px] border-dashed border-amber-400" />
          grenade arc — ESTIMATED (endpoints only)
        </span>
      </div>
    </div>
  );
}
