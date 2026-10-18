# AEGIS Team Analysis — Product Brief

**Product:** AEGIS Team Analysis — a sellable B2B analytics product for esports team coaches and analysts.
**Status:** Built (Phase 11). Dashboard at `/analyst`, APIs under `/api/v1/analysis/*` and `/api/v1/dossiers/*`.

## What it is

A coach opens the Analyst Dashboard, picks a team, and gets a complete dossier:
rotations, drop spots, zone tendencies, fight history, playstyle profile, strengths/weaknesses,
head-to-head records — exportable as professional PDF reports (the deliverable a team pays for).

## Who buys it

- **Team coaches / analysts** — prep for opponents: where they drop, how they rotate, how they fight.
- **Esports orgs** — scouting and roster decisions backed by data.
- **Tournament broadcasters** — analyst-desk material (tendencies, head-to-head graphics).

## What's included

| Component | Path | Description |
|---|---|---|
| Analyst Dashboard | `/analyst` | Team selector, coverage overview, match opener |
| Team dossier view | `/analyst/team/[name]` | Playstyle badge, stats, strengths/weaknesses, tendencies, head-to-head, fight history, PDF download |
| Match timeline view | `/analyst/match/[id]` | Rotation paths + fight markers + zone phases on a schematic map, PDF download |
| Rotation/drop/zone APIs | `/api/v1/analysis/*` | Rotations, drops, zone phases + per-team stats; ingest endpoints for simulator/feed |
| Fight log + dossier APIs | `/api/v1/dossiers/*` | Fight log, computed team dossiers (cached in `team_intelligence.profile`) |
| PDF reports | `/api/v1/dossiers/report/match/{id}.pdf`, `/report/team/{id}.pdf` | Professional match reports and team dossiers (fpdf2, zero cost) |

## How it works

1. **Data in:** rotation/drop/zone/fight events are ingested via `POST /api/v1/analysis/ingest/*`
   and `/api/v1/dossiers/ingest/fight` (operator+). Sources: the match simulator today,
   a live game feed via the feed adapter (see `docs/GAME_FEED.md`) when available.
2. **Computation:** dossiers are computed from real DB data — playstyle label comes with its
   evidence (fights/match, aggression score); strengths/weaknesses are top/bottom statistical
   edges vs the tournament average; head-to-head from fight + kill events.
3. **Delivery:** dashboard for interactive work, PDFs for the sellable artifact
   (pre-match opponent brief, post-match review).

## Honest limitations

- **No position data, no analysis.** Rotations, drops, and zone tendencies require x/y position
  data from the simulator or a live feed. Without it, every endpoint returns empty data with a
  clear `meta.coverage` note — the product never invents rotations or fights.
- **Dossiers need volume.** Strengths/weaknesses need ≥5 fights sampled; thin data is labeled
  `insufficient_data` rather than guessed.
- **PDFs are data summaries**, not video breakdowns — they complement VOD review, not replace it.
- **Map is schematic.** The match view draws a stylized SVG map, not the real Erangel/Miramar
  geometry. Real map tiles can be dropped in later without changing the API.

## Pricing sketch (for the user, not implemented)

Per-team monthly dossier subscription, or per-tournament opponent packs (all 16 teams × PDF).
The PDFs are the unit of sale; the dashboard is the retention hook.
