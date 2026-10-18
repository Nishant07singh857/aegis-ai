/**
 * AEGIS AI — graphics template registry (Phase 8).
 *
 * The broadcast graphics system: every /overlays/* page fetches its data
 * exactly as before, then renders through one of the templates registered
 * here. The template is chosen with ?template=<id> (unknown ids fall back
 * to the per-type default), so existing OBS browser-source URLs keep
 * working unchanged.
 *
 * Template IDs are FIXED — do not add new ones without updating the
 * director graphics picker and the docs in the same change.
 *
 * Honest-data rule: templates never invent numbers. When the feed has not
 * delivered data yet, each template renders the shared WaitingFeed empty
 * state (imported from the overlay kit).
 */

import type { ComponentType } from 'react';
import type { ConnStatus, Team, KillEvent } from '@/lib/useLiveSocket';

import ScorebugClassic from './templates/scorebug-classic';
import ScorebugModern from './templates/scorebug-modern';
import ScorebugMinimal from './templates/scorebug-minimal';
import LowerThirdSlide from './templates/lowerthird-slide';
import LowerThirdRise from './templates/lowerthird-rise';
import LowerThirdBanner from './templates/lowerthird-banner';
import StandingsTable from './templates/standings-table';
import StandingsCards from './templates/standings-cards';
import StandingsMvp from './templates/standings-mvp';
import StandingsEliminator from './templates/standings-eliminator';
import StandingsIgl from './templates/standings-igl';
import StandingsFinalsMvp from './templates/standings-finals-mvp';
import StandingsSpotlight from './templates/standings-spotlight';
import KillfeedTicker from './templates/killfeed-ticker';
import KillfeedStack from './templates/killfeed-stack';

export type GraphicType = 'scorebug' | 'lower-third' | 'standings' | 'killfeed';

/** Zone phase / clock block polled from GET /api/v1/analytics/summary. */
export interface ScorebugSummary {
  zone_phase?: number;
  teams_alive?: number;
  match_time_seconds?: number;
}

export interface ScorebugProps {
  /** Raw standings frames from useLiveSocket (templates sort as needed). */
  standings: Team[];
  /** Analytics summary, or null while the poll has not returned. */
  summary: ScorebugSummary | null;
  accent: string;
  status: ConnStatus;
  /** Display label derived from ?match= ("LIVE" unless overridden). */
  matchLabel: string;
}

export interface LowerThirdProps {
  /** Whether the graphic should be on screen right now. */
  visible: boolean;
  /** Event label, e.g. "WIPE" (already upper-cased by the page). */
  title: string;
  /** Commentary line text. Empty until the first real line arrives. */
  subtitle: string;
  accent: string;
  status: ConnStatus;
  /** Key that changes when a new line arrives (re-triggers the animation). */
  animKey?: string;
}

/**
 * Per-player stat line for the player-leaderboard standings templates
 * (MVP, Eliminator, Best IGL, Finals MVP).
 *
 * Honesty contract: every numeric field is `number | null | undefined`.
 * `null`/missing means the feed has not delivered that value — the
 * templates render "—" and never invent it. Population sources:
 *   - finishes:   GET /api/v1/matches/{id}/player-stats (recorded by a
 *                 game-stats feed), or the live kill feed for the
 *                 Eliminator board (killer names, this match only).
 *   - damage / survival_seconds / knocks: player-stats feed only.
 *   - rating:     feed-provided MVP/IGL rating only — NEVER derived from
 *                 team totals (see the honesty note in docs/GRAPHICS.md).
 *   - is_igl:     from the roster (Player.role = "IGL").
 *   - wwcd:       joined from the team standings frames (team-level data).
 *   - avg_points / top_5s / avg_finishes: tournament feeds only.
 */
export interface PlayerStat {
  /** Stable id (DB player id, or a feed-provided id). */
  playerId: string;
  /** Display name (IGN); falls back to playerId when unknown. */
  playerName: string;
  teamId: string;
  teamTag: string;
  teamColor?: string;
  finishes?: number | null;
  damage?: number | null;
  survivalSeconds?: number | null;
  knocks?: number | null;
  /** MVP rating (~1.0x scale) — feed-provided only. */
  rating?: number | null;
  /** IGL rating — feed-provided only. */
  iglRating?: number | null;
  /** Finals MVP rating (higher scale) — feed-provided only. */
  finalsMvpRating?: number | null;
  isIgl?: boolean;
  avgPoints?: number | null;
  wwcd?: number | null;
  top5s?: number | null;
  avgFinishes?: number | null;
}

export interface StandingsProps {
  /** Already sorted + sliced to the visible row count by the page. */
  standings: Team[];
  accent: string;
  status: ConnStatus;
  /**
   * Movement vs the previous match per team id, for standings-table.
   * Absent = the feed has no previous-match snapshot, so the template
   * renders no arrows (never fakes them).
   */
  movement?: Record<string, 'up' | 'down' | 'same'>;
  /**
   * TTL-filtered live kill events, newest-first (from useLiveSocket).
   * Used by standings-eliminator to tally per-player finishes for the
   * current match — real feed data.
   */
  killFeed?: KillEvent[];
  /**
   * Per-player stat lines for the player-leaderboard templates
   * (standings-mvp / standings-igl / standings-finals-mvp), fetched by
   * the page from GET /api/v1/matches/{id}/player-stats. Empty = no
   * feed data yet; those templates render their honest empty state.
   */
  players?: PlayerStat[];
}

export interface KillfeedProps {
  /** TTL-filtered, newest-first kill events from the page. */
  events: KillEvent[];
  accent: string;
  status: ConnStatus;
}

export interface TemplateDef {
  id: string;
  name: string;
  type: GraphicType;
  description: string;
  /** `any` is contained here: template components are typed through the
   *  per-type prop interfaces above at their definition site. */
  component: ComponentType<any>;
}

export const TEMPLATES: TemplateDef[] = [
  {
    id: 'scorebug-classic',
    name: 'Scorebug — Classic',
    type: 'scorebug',
    description:
      'The original scorebug: top-centre bar with the two leading teams (tag, points, kills), LIVE badge, zone phase, match clock and alive count.',
    component: ScorebugClassic,
  },
  {
    id: 'scorebug-modern',
    name: 'Scorebug — Modern',
    type: 'scorebug',
    description:
      'Rounded pill bar with a gradient accent underline: team chips with points, centred LIVE / zone / clock block.',
    component: ScorebugModern,
  },
  {
    id: 'scorebug-minimal',
    name: 'Scorebug — Minimal',
    type: 'scorebug',
    description:
      'Thin full-width top strip: leading team tags with points and the alive count only. Nothing else on screen.',
    component: ScorebugMinimal,
  },
  {
    id: 'lowerthird-slide',
    name: 'Lower-third — Slide',
    type: 'lower-third',
    description:
      'The original lower-third: bottom-left card with accent bar, event label and commentary line, sliding in from the left.',
    component: LowerThirdSlide,
  },
  {
    id: 'lowerthird-rise',
    name: 'Lower-third — Rise',
    type: 'lower-third',
    description:
      'Bottom-centre card that fades and rises into place; event label above the commentary line.',
    component: LowerThirdRise,
  },
  {
    id: 'lowerthird-banner',
    name: 'Lower-third — Banner',
    type: 'lower-third',
    description:
      'Full-width top banner with a gradient wash: event label on the left, commentary line beside it.',
    component: LowerThirdBanner,
  },
  {
    id: 'standings-table',
    name: 'Standings — Table',
    type: 'standings',
    description:
      'The original standings table: rank, team, kills, placement and total points; eliminated teams dimmed with an ELIM tag.',
    component: StandingsTable,
  },
  {
    id: 'standings-cards',
    name: 'Standings — Cards',
    type: 'standings',
    description:
      'Team cards in a grid: rank, colour chip, tag, total points and kills per card with a staggered fade-in.',
    component: StandingsCards,
  },
  {
    id: 'standings-mvp',
    name: 'Standings — MVP Board',
    type: 'standings',
    description:
      'Per-player MVP board: rank, player, team, MVP rating, finishes, damage, survival and knocks with staggered row reveals. Feeds deliver player stats via GET /api/v1/matches/{id}/player-stats; unavailable fields render "—", never invented.',
    component: StandingsMvp,
  },
  {
    id: 'standings-eliminator',
    name: 'Standings — Eliminator Race',
    type: 'standings',
    description:
      'Top-5 finish leaders tallied live from the kill feed (this match), with a callout emphasising the #1 vs #2 gap. No kill feed yet → honest empty state.',
    component: StandingsEliminator,
  },
  {
    id: 'standings-igl',
    name: 'Standings — Best IGL',
    type: 'standings',
    description:
      'Best-IGL board: rank, IGL, team, IGL rating, avg points, WWCD (from team frames), top-5s, avg survival and avg finishes. Fields the feed has not delivered render "—".',
    component: StandingsIgl,
  },
  {
    id: 'standings-finals-mvp',
    name: 'Standings — Finals MVP',
    type: 'standings',
    description:
      'Finals MVP board on the higher finals rating scale. Same honest-data contract as the MVP board.',
    component: StandingsFinalsMvp,
  },
  {
    id: 'standings-spotlight',
    name: 'Standings — Team Spotlight',
    type: 'standings',
    description:
      'Big bold featured-team card for the current leader: rank, tag, points, kills, logo, plus damage / knocks / avg survival when a feed provides them (otherwise "—").',
    component: StandingsSpotlight,
  },
  {
    id: 'killfeed-ticker',
    name: 'Killfeed — Ticker',
    type: 'killfeed',
    description:
      'The original kill ticker: newest kill slides in at the top-right, killer → victim with KNOCK badge or ✕, weapon shown.',
    component: KillfeedTicker,
  },
  {
    id: 'killfeed-stack',
    name: 'Killfeed — Stack',
    type: 'killfeed',
    description:
      'Stacked kill cards that fade in as kills land; accent edge, killer → victim and weapon on each card.',
    component: KillfeedStack,
  },
];

export const DEFAULT_TEMPLATES: Record<GraphicType, string> = {
  scorebug: 'scorebug-classic',
  'lower-third': 'lowerthird-slide',
  standings: 'standings-table',
  killfeed: 'killfeed-ticker',
};

/**
 * Look up a template for a graphic type. Unknown ids (including an empty
 * ?template=) fall back to the type's default so a bad URL can never
 * render a blank overlay.
 */
export function getTemplate(type: GraphicType, id: string): TemplateDef {
  const exact = TEMPLATES.find((t) => t.type === type && t.id === id);
  if (exact) return exact;
  const fallback = TEMPLATES.find(
    (t) => t.type === type && t.id === DEFAULT_TEMPLATES[type]
  );
  if (!fallback) {
    throw new Error(`No template registered for graphic type "${type}"`);
  }
  return fallback;
}

/** Small shared helper: seconds → m:ss match clock. */
export function fmtClock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}
