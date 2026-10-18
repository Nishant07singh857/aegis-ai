# Graphics Templates (Phase 8)

The on-air look of every broadcast graphic is a **template**: a React
component registered in `frontend/components/graphics/registry.ts`.
Operators pick the active template per graphic type in the
[template gallery](/broadcast/graphics) or the director console picker;
the choice is stored in the director state (`graphics` block) and
broadcast to every surface over WS `director.state`.

## The 12 templates

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
  status: ConnStatus }` (already sorted and sliced to the visible row
  count by the page).
- **lower-third** — `{ visible: boolean, title: string, subtitle: string,
  accent: string, status: ConnStatus, animKey?: string }`
  (`title` = event label e.g. "WIPE", `subtitle` = commentary line
  text; the page owns show/hide timing, the template owns the design
  and the standby state).
