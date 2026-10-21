# Grenade Trajectory — Honest Estimated Version (Phase 23, W4)

## What this feature is — and is not

This feature draws an **estimated** thrower → explosion arc on the analyst
map when a grenade kill / explosion is observed **and both endpoints are
actually known**. It is an endpoint-based range/angle visualization:

- **Known (when a feed reports them):** where the thrower stood, where the
  explosion happened.
- **Derived by a model, never measured:** the arc between them — a
  symmetric projectile parabola under standard gravity at an *assumed*
  45° launch angle, sampled into a polyline.
- **Never claimed:** that the grenade flew along the drawn path, that the
  angle/apex/flight-time are real, or that anything was tracked mid-flight.

Every record carries, permanently:

```json
{
  "estimated": true,
  "method": "endpoint_arc_estimate",
  "confidence_basis": "endpoints_only",
  "launch_angle_basis": "assumed_default_45deg"
}
```

The in-memory store **refuses** any record without `estimated: true` or
without both endpoint positions, and the analyst UI renders every arc
dashed with the word **ESTIMATED** as visible text on the map (plus a
"GRENADE ARC — ESTIMATED" badge and a legend entry) — not just a tooltip.

## Why true projectile tracking is not measurable from a spectator feed

AEGIS works from spectator-mode observer screens only. A thrown frag
grenade in that feed is effectively unmeasurable mid-flight:

1. **Frame budget.** A grenade crosses a typical engagement distance in
   roughly a second. At broadcast frame rates the projectile itself is
   visible for only a handful of frames — often **1–2 frames** as a few
   dark pixels — and the Phase 14/21 vision pipeline samples even slower
   (~2.3 FPS CPU-only in honest profiling).
2. **Occlusion.** The arc passes behind terrain, buildings, smoke and
   other players. A spectator camera is not locked to the grenade; most
   of the flight is simply not on any observer's screen.
3. **No depth.** A single 2D screen projection cannot distinguish "high
   arc far away" from "flat arc nearby". Recovering a 3D trajectory from
   one monocular, moving, spectator viewpoint is not a calibration
   problem — the information is not in the pixels.
4. **No identity linkage.** Even spotting a flash and a thrower in the
   same match does not prove which throw produced which explosion
   without paired endpoint reporting from the feed.

Any system claiming a *measured* grenade trajectory from this feed is
fabricating. AEGIS therefore estimates from endpoints only, and labels
the estimate everywhere it appears.

## Coordinate spaces (the plumbing this is built on)

Positions in AEGIS today live in **incompatible** spaces, and this
feature never converts between them (no honest conversion exists):

| Space | Producer | Meaning | Accuracy limits |
|---|---|---|---|
| `minimap_region_pixels` | `MinimapReader` → `POSITION_UPDATE.player_positions` | Pixel centroid of a marker **inside the minimap crop** of one observer's screen. Keys are detector-internal (`teammate_0`, `enemy_1`) — anonymous, not player names. | The in-game minimap is a **zoomed viewport** around the observed player, not the whole map, and its scale/offset are uncalibrated — so these pixels cannot be placed on the whole-map analyst schematic. Marker centroids are ±a few px; team attribution comes from the observer slot, and `enemy_*` markers have no known team. |
| `frame_pixels` | Utility detector → `UTILITY.position` (`position_space`) | Pixel location of an explosion flash / smoke / fire on the observer's full frame. | Screen space, not map space. Perspective makes equal pixel distances mean wildly different world distances. The detector also **does not know who threw the item** (documented in `vision/runtime.py`). |
| `normalized_0_1` / `schematic_0_1000` | Analysis ingest family | Whole-map coordinates in the analyst schematic's 0–1000 space (`normCoord` in `frontend/app/analyst/components/types.ts`). | Only as accurate as whatever produced them; the schematic island is hand-drawn, so even "accurate" points are schematic-grade, not survey-grade. |

The engine is space-agnostic: it computes geometry in whatever single
space both endpoints were reported in, and reports `distance_units` as
`coordinate_units` unless a calibrated `units_per_meter` is configured
(`AEGIS_GRENADE_UNITS_PER_METER`). Without a scale it reports **no**
flight time and **no** launch speed — those would be invented numbers.

## When a trajectory is produced (exact firing rules)

The consumer (`backend/app/services/grenade_trajectory/`) subscribes to
`kill_event`, `knock_event`, `utility_event`, `position_update`. A
record is created **only if all** of these hold:

1. The event indicates a frag grenade:
   - kill/knock `weapon` contains "grenade" (e.g. W2's `frag_grenade`)
     — excluding smoke/molotov/stun/flash/decoy labels; or
   - `utility_type` is grenade / frag / explosion.
2. A thrower position is known: an explicit event field
   (`thrower_position` / `killer_position` / `attacker_position` /
   nested `thrower.position`), or a fresh (≤15 s) last-known position
   from position tracking under the thrower's exact name/key.
3. An explosion position is known: an explicit field
   (`explosion_position` / `blast_position`; `position` on utility
   events), **or** the victim's position as a labelled proxy (see
   below), direct or from position tracking.
4. Both endpoints resolve to the **same declared coordinate space**
   (event-level `coordinate_space` / `position_space`, or per-position
   `<key>_space`). Undeclared or mismatched → no record.
5. The endpoints differ (zero-range → no arc).

**Victim-position proxy.** A frag grenade kills at blast radius, so when
only the victim's position is known it is used as the explosion-endpoint
proxy and the record says so in data, not just docs:
`explosion.position_basis` is `victim_position_proxy_event_field` or
`victim_position_proxy_last_known_tracking`. It is never upgraded to
"observed".

**Explosion-only observations produce NO trajectory.** Decision
(documented per the Phase 23 contract): they are counted in the
consumer stats (`explosion_only_observations`) so the gap stays visible,
but nothing is stored — an arc with one endpoint would need an invented
second endpoint.

**Dedup:** one record per source event id per match.

### Production reality today

With the current production feeds, **this pipeline produces zero
records**, by design:

- Vision `UTILITY` grenade events carry a `frame_pixels` flash position
  but **no thrower** (the detector cannot know) → explosion-only.
- Killfeed kill/knock events carry names and (with W2) a truthful
  `weapon`, but **no positions**.
- Minimap `POSITION_UPDATE` markers are anonymous detector keys, not
  player names, so they can never be honestly paired with a named
  killer/victim.

The pipeline fires the moment a feed reports paired, named endpoints —
e.g. a W3 world-side explosion event that includes a thrower position,
an operator/simulator feed that attaches positions to kill events, or a
future named-position source feeding `positions_by_player`. That is the
intended state: a correct pipeline awaiting real position data beats a
fake demo.

## API

```
GET /api/v1/analysis/grenade-trajectories?match_id=<id>
```

Any authenticated user (same auth as the other analysis reads).
Response is the standard `AnalysisEnvelope`:

```json
{
  "data": [
    {
      "id": "…", "match_id": "…", "ts": 1700000000.0,
      "estimated": true,
      "method": "endpoint_arc_estimate",
      "confidence_basis": "endpoints_only",
      "source": "kill_event.weapon",
      "weapon": "frag_grenade",
      "thrower":   { "player": "…", "team": "…",
                     "position": [x, y], "position_basis": "event_field" },
      "explosion": { "position": [x, y],
                     "position_basis": "observed_event_field" },
      "victim":    { "player": "…", "team": "…" },
      "coordinate_space": "minimap_region_pixels",
      "distance": 50.0, "distance_units": "coordinate_units",
      "launch_angle_deg": 45.0,
      "launch_angle_basis": "assumed_default_45deg",
      "apex_height": 12.5, "apex_units": "coordinate_units",
      "flight_time_s": null, "launch_speed_mps": null,
      "arc_points": [ { "x": …, "y": …, "h": … }, … ],
      "note": "ESTIMATED arc derived from the thrower and explosion endpoints only. …"
    }
  ],
  "meta": { "source": "feed", "coverage": "N ESTIMATED grenade arc(s) — …" }
}
```

Persistence is in-memory (bounded at 500 records per match). No DB
table fits this derived artifact and **no migration was added** — if
durable storage is wanted later, that needs its own approval.

## Frontend rendering

The analyst match view (`/analyst/match/[id]`) fetches the endpoint and
passes drawable records to `MapSchematic`. A record is drawn **only**
when its coordinate space is `normalized_0_1` or `schematic_0_1000` —
the two spaces that map honestly onto the schematic. Records in any
other space are counted in a visible note under the map ("N ESTIMATED
grenade arc(s) not drawn: their coordinate space has no honest mapping
onto this schematic map") instead of being force-fitted. Drawn arcs are
amber, dashed, bowed upward by the modelled apex ratio, with a ring at
the thrower endpoint, a burst at the explosion endpoint, the word
**ESTIMATED** in visible text at the apex, a corner badge, and a legend
entry.

## Files

- `backend/app/services/grenade_trajectory/engine.py` — arc estimator.
- `backend/app/services/grenade_trajectory/store.py` — label-enforcing
  in-memory store.
- `backend/app/services/grenade_trajectory/service.py` — bus consumer +
  PositionBook (freshness-windowed last-known positions).
- `backend/app/analysis/router.py` — `GET /analysis/grenade-trajectories`.
- `backend/app/main.py` — lifespan wiring (guarded; failure never blocks
  startup, mirroring the Phase 22 vision-intelligence pattern).
- `backend/tests/unit/test_grenade_trajectory.py` — 31 tests.
- `frontend/app/analyst/components/MapSchematic.tsx`,
  `frontend/app/analyst/components/types.ts`,
  `frontend/app/analyst/match/[id]/page.tsx` — rendering + fetch.

## Labelling contract (summary)

1. `estimated: true` on **every** record and in the API coverage text.
2. **ESTIMATED** as visible UI text wherever an arc is drawn.
3. No record without both endpoints. No invented positions, no space
   conversions, no silent proxies — the proxy is in the data.
4. Assumed quantities (angle, apex, flight time) are labelled as model
   outputs with their basis, in the record itself.
