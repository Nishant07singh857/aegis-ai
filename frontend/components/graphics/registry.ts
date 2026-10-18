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

export interface StandingsProps {
  /** Already sorted + sliced to the visible row count by the page. */
  standings: Team[];
  accent: string;
  status: ConnStatus;
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
