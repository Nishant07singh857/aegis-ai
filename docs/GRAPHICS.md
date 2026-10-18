# Graphics Templates (Phase 8)

The on-air look of every broadcast graphic is a **template**: a React
component registered in `frontend/components/graphics/registry.ts`.
Operators pick the active template per graphic type in the
[template gallery](/broadcast/graphics) or the director console picker;
the choice is stored in the director state (`graphics` block) and
broadcast to every surface over WS `director.state`.

## The 15 templates

| Type        | ID                 | Name               | Default |
|-------------|--------------------|--------------------|---------|
| scorebug    | `scorebug-classic` | Scorebug — Classic | ✓       |
| scorebug    | `scorebug-modern`  | Scorebug — Modern  |         |
| scorebug    | `scorebug-minimal` | Scorebug — Minimal |         |
| lower-third | `lowerthird-slide` | Lower-third — Slide| ✓       |
| lower-third | `lowerthird-rise`  | Lower-third — Rise |         |
| lower-third | `lowerthird-banner`| Lower-third — Banner|        |
| standings   | `standings-table`  | Standings — Table  | ✓       |
| standings   | `standings-cards`  | Standings — Cards  |         |
| standings   | `standings-mvp`    | Standings — MVP Board |      |
| standings   | `standings-eliminator` | Standings — Eliminator Race |  |
| standings   | `standings-igl`    | Standings — Best IGL |        |
| standings   | `standings-finals-mvp` | Standings — Finals MVP |    |
| standings   | `standings-spotlight` | Standings — Team Spotlight | |
| killfeed    | `killfeed-ticker`  | Killfeed — Ticker  | ✓       |
| killfeed    | `killfeed-stack`   | Killfeed — Stack   |         |

Template IDs are a **fixed contract** shared by the frontend registry,
the backend validator (`backend/app/services/director_service.py`) and
the docs — never rename one.

## How a template gets chosen (precedence)

1. `?template=<id>` on an overlay page URL pins one template for that
   OBS source (e.g. `/overlays/scorebug?template=scorebug-minimal`).
2. Otherwise the director state's `graphics.<type>_template_id`
   (the operator's gallery/console choice, live over WS).
3. Otherwise the per-type default (marked ✓ above).

Unknown IDs at any level fall back to the type default — a bad URL or a
stale ID can never render a blank overlay.

## Where templates render

- **Overlay pages** (`/overlays/scorebug|killfeed|standings|lower-third`):
  each page fetches its data exactly as before, then renders the
  resolved template component. `?template=` is honoured per the
  precedence above.
- **`DirectorOutputView`** (`/broadcast/output` OBS capture, `/watch`):
  renders the director state's active template per type. Scenes keep
  their data role (e.g. the standings scene shows the top 8); the
  template owns the visual design.
- **Gallery** (`/broadcast/graphics`, operator/admin): live previews of
  every template with real feed data, plus "Set Active".
- **Director console** (`/broadcast/director`, operator/admin):
  per-type template buttons.

## Mode rules

Template selection is an operator write via
`POST /api/v1/director/graphics` (see `docs/DIRECTOR.md`). Unlike
scene/overlay/lower-third writes (manual-only), it is allowed in
**SEMI_AUTO and MANUAL** — the backend answers **409 in FULL_AUTO**,
where the AI drives the output.

## Adding a new template (3 steps)

**1. Create the component** in
`frontend/components/graphics/templates/<template-id>.tsx`.

It must:
- accept the per-type props interface from `registry.ts`
  (`ScorebugProps` / `LowerThirdProps` / `StandingsProps` /
  `KillfeedProps`) — the pages feed it live data through exactly
  these props,
- be self-positioning for a 1920×1080 overlay (`absolute ...`; the
  overlay root is the positioning context),
- handle empty data honestly: render `WaitingFeed` from
  `@/components/overlay-kit` (or the shared standby for lower-thirds)
  — **never invent numbers or text**,
- use `var(--ov-accent)` for the accent colour (set by the page),
- animate with the `gfx-anim-*` classes from `graphics.css`
  (transform/opacity only — GPU-friendly inside OBS),
- take no paid APIs, no secrets, no placeholders.

**2. Register it** in `frontend/components/graphics/registry.ts`: add a
`TEMPLATES` entry with a stable `id`, the `type`, a display `name` and
a one-line `description` (shown in the gallery).

**3. Allow-list the ID on the backend** in
`backend/app/services/director_service.py`: add the ID to the matching
tuple (`SCOREBUG_TEMPLATES`, `LOWER_THIRD_TEMPLATES`,
`STANDINGS_TEMPLATES` or `KILLFEED_TEMPLATES`).
`GRAPHICS_TEMPLATE_OPTIONS` and the API validator pick it up
automatically; without this step `POST /director/graphics` rejects the
ID with 422. Update the ID tables in this doc and in
`docs/DIRECTOR.md` in the same change.

## Template props contract (what pages pass)

- **scorebug** — `{ standings: Team[], summary: ScorebugSummary | null,
  accent: string, status: ConnStatus, matchLabel: string }`
  (summary is the polled `/api/v1/analytics/summary`, null until it
  returns).
- **killfeed** — `{ events: KillEvent[], accent: string,
  status: ConnStatus }` (events are TTL-filtered, newest-first, by the
  page).
- **standings** — `{ standings: Team[], accent: string,
  status: ConnStatus, movement?: Record<teamId, 'up'|'down'|'same'>,
  killFeed?: KillEvent[], players?: PlayerStat[] }` (standings already
  sorted and sliced to the visible row count by the page).
  - `movement` — per-team rank movement vs the previous match; rendered
    by `standings-table` as ▲/▼/— with a legend. Absent = no
    previous-match snapshot exists, so no arrows render (never faked).
  - `killFeed` — TTL-filtered live kill events, newest-first; rendered
    by `standings-eliminator`, which tallies per-player finishes for
    the current match (real feed data).
  - `players` — per-player stat lines (the `PlayerStat` interface in
    `registry.ts`) from `GET /api/v1/matches/{id}/player-stats`,
    rendered by `standings-mvp`, `standings-igl` and
    `standings-finals-mvp`. `[]` until a game-stats feed records them.
- **lower-third** — `{ visible: boolean, title: string, subtitle: string,
  accent: string, status: ConnStatus, animKey?: string }`
  (`title` = event label e.g. "WIPE", `subtitle` = commentary line
  text; the page owns show/hide timing, the template owns the design
  and the standby state).

## Player-leaderboard data contract (honest-data rule)

The five player-focused standings templates (`standings-mvp`,
`standings-eliminator`, `standings-igl`, `standings-finals-mvp`,
`standings-spotlight`) never invent numbers. How each field is sourced:

| Field | Real source today | Status |
|-------|-------------------|--------|
| Team rank / tag / name / logo / color / total_pts / kills / wwcd | WS standings frames (`Team`) | ✅ real |
| Player finishes (Eliminator) | Live kill feed (`killer` + `killerTeam` per event) | ✅ real, this match only |
| Player finishes / damage / survival / kills (MVP/IGL/Finals boards) | `GET /api/v1/matches/{id}/player-stats` ← `MatchPlayerStats` rows recorded by a game-stats feed | ⚠️ API exists, empty until a feed records data |
| IGL flag | Roster `Player.role = "IGL"` via the same endpoint | ⚠️ real when rosters exist |
| MVP rating, IGL rating, Finals MVP rating | Feed-provided only | ❌ unavailable — renders "—" |
| Knocks, avg points, top-5s, avg survival, avg finishes | Not in any feed schema | ❌ unavailable — renders "—" |
| Team damage / knocks / avg survival (Spotlight) | Not in team frames | ❌ unavailable — renders "—" |
| Movement arrows (Table) | `movement` prop, previous-match snapshot | ❌ unavailable — no arrows |

Deliberate anti-patterns the templates (and this API) avoid:

- **Distributing team kills across players** (e.g. `team.kills // 4`) and
  presenting the result as per-player data — the existing ad-hoc
  `/api/v1/players/mvp` and `/api/v1/matches/mvp` demo endpoints do this
  (`damage: team.kills * 100`); the player boards must never read them.
- **Deriving a rating from finishes** — an MVP rating on the ~1.0x (or
  finals ~3.2x) scale from kills alone is a fabricated scale, so the
  boards render "—" until a feed supplies real ratings.
- **Tournament-wide eliminator from one match's feed** — the Eliminator
  board is labelled "THIS MATCH"; extrapolating a tournament race is not
  honest.

### What a feed needs to deliver to light these boards up

1. A game-stats ingestion path that calls
   `MatchService.record_player_stats(match_id, [...])` (already exists;
   nothing calls it yet). That immediately populates finishes, damage
   and survival on the MVP / Finals boards via
   `GET /api/v1/matches/{id}/player-stats`.
2. Roster `Player` rows with `role: "IGL"` to light up the IGL board's
   row list (WWCD joins from team frames automatically).
3. An IGL/finals rating feed writing `mvp_score` (or a dedicated column
   per board) to replace the "—" rating cells.
4. A previous-match standings snapshot service to populate the
   `movement` prop on the standings overlay (currently unfilled —
   `standings-table` renders no arrows until it exists).
