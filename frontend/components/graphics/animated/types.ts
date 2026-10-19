/**
 * AEGIS AI — animated story graphics types (Phase 19, W2).
 *
 * Shape of the backend story dict produced by the AI storyteller:
 *   { type, title, body, data }
 *
 * Honest-data contract (same as the Phase 8 registry): every numeric
 * field is `number | null | undefined`. `null`/missing means the feed
 * has not delivered that value — components render "—" and never invent
 * a number. Strings default to the story title/body, never to invented
 * names.
 */

/** The 7 story types from docs/AI_STORYTELLER.md. */
export type StoryType =
  | 'win_probability'
  | 'h2h'
  | 'player_comparison'
  | 'form'
  | 'zone'
  | 'milestone'
  | 'comeback';

/**
 * Backend story dict. `data` is narrowed per component with the
 * interfaces below; unknown extra keys are ignored, never rendered.
 */
export interface Story<D = Record<string, unknown>> {
  type: StoryType;
  title: string;
  body: string;
  data: D;
}

/* ------------------------------------------------------------------ */
/* Win probability                                                     */
/* ------------------------------------------------------------------ */

export interface WinProbTeam {
  team_tag: string;
  /** 0–100. Null/missing = feed has not delivered → "—". */
  probability?: number | null;
  /** Hex accent for the team's bar; falls back to cyan/gold rank order. */
  color?: string;
}

export interface WinProbData {
  teams?: WinProbTeam[];
  /** Optional note, e.g. "pichhle zone se +5%". */
  delta_note?: string;
}

export type WinProbTemplate = 'bars' | 'compact' | 'fullscreen';

/* ------------------------------------------------------------------ */
/* Head-to-head / player comparison                                    */
/* ------------------------------------------------------------------ */

export interface H2HPlayer {
  name: string;
  team_tag?: string;
  finishes?: number | null;
  damage?: number | null;
  /** MVP rating (~1.0x scale). Feed-provided only — never derived. */
  rating?: number | null;
  knocks?: number | null;
  clutches?: number | null;
}

export interface H2HData {
  player_a: H2HPlayer;
  player_b: H2HPlayer;
  /**
   * Explicit winner when the storyteller declares one ('a' | 'b').
   * Absent → the component compares finishes, then rating; still no
   * clear winner → no glow, both cards equal size.
   */
  winner?: 'a' | 'b';
  /** e.g. "12 baar aamne-saamne, Scout ne 8 baar jeeta". */
  note?: string;
}

export type H2HTemplate = 'side-by-side' | 'top-bottom' | 'minimal';

/* ------------------------------------------------------------------ */
/* Milestone alert                                                     */
/* ------------------------------------------------------------------ */

export interface MilestoneData {
  player?: string;
  team_tag?: string;
  /** e.g. "50 finishes se 1 door". Shown verbatim from the story. */
  milestone?: string;
}

export type MilestoneTemplate = 'banner' | 'ticker' | 'fullscreen';

/* ------------------------------------------------------------------ */
/* Form guide                                                          */
/* ------------------------------------------------------------------ */

export interface FormData {
  team_tag: string;
  /** Oldest → newest, last 5 matches. */
  results: Array<'W' | 'L' | 'D'>;
  /** e.g. "Soul ne last 5 me se 4 jeete". */
  note?: string;
}

export type FormTemplate = 'badges' | 'timeline';

/* ------------------------------------------------------------------ */
/* Zone advantage                                                        */
/* ------------------------------------------------------------------ */

export interface ZoneData {
  zone_number?: number | null;
  team_tag?: string;
  /** Evidence line, e.g. "pichhle 5 similar zones me 3 baar top-3". */
  evidence?: string;
}

export type ZoneTemplate = 'card' | 'overlay';

/* ------------------------------------------------------------------ */
/* Per-component template ids, for the TemplatePicker                  */
/* ------------------------------------------------------------------ */

export const STORY_TEMPLATES: Record<StoryType, readonly string[]> = {
  win_probability: ['bars', 'compact', 'fullscreen'],
  h2h: ['side-by-side', 'top-bottom', 'minimal'],
  player_comparison: ['side-by-side', 'top-bottom', 'minimal'],
  milestone: ['banner', 'ticker', 'fullscreen'],
  form: ['badges', 'timeline'],
  zone: ['card', 'overlay'],
  // Comeback stories reuse the form-guide renderer (a comeback IS a
  // form narrative: position jump across recent matches).
  comeback: ['badges', 'timeline'],
};

export const STORY_TYPE_LABELS: Record<StoryType, string> = {
  win_probability: 'Win probability',
  h2h: 'Head-to-head',
  player_comparison: 'Player comparison',
  form: 'Team form',
  zone: 'Zone advantage',
  milestone: 'Milestone alert',
  comeback: 'Comeback story',
};

/** Any single template id accepted by the story components. */
export type StoryTemplateId = string;
