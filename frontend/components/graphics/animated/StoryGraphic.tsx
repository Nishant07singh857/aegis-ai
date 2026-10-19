/**
 * AEGIS AI — StoryGraphic dispatcher (Phase 19, W2).
 *
 * One entry point: given a backend story dict
 *   { type, title, body, data }
 * render the right animated graphic with the right layout template.
 *
 * Type → component:
 *   win_probability      → WinProbBar
 *   h2h / player_comparison → H2HCards
 *   milestone            → MilestoneAlert
 *   form / comeback      → FormGuide (a comeback is a form narrative:
 *                          position jump across recent matches)
 *   zone                 → ZoneAdvantage
 *
 * Each component narrows `story.data` itself and renders honest "—" /
 * WaitingFeed states when the feed has not delivered values.
 */

'use client';

import type { AnimationStyle } from './animation-style';
import WinProbBar from './WinProbBar';
import H2HCards from './H2HCards';
import MilestoneAlert from './MilestoneAlert';
import FormGuide from './FormGuide';
import ZoneAdvantage from './ZoneAdvantage';
import type {
  FormData,
  H2HData,
  MilestoneData,
  Story,
  StoryType,
  WinProbData,
  ZoneData,
} from './types';

export interface StoryGraphicProps {
  story: Story;
  /** Layout template id — validated per component, bad ids fall back. */
  template?: string;
  /** Per-graphic animation-style override (context otherwise). */
  animationStyle?: AnimationStyle;
  className?: string;
}

export default function StoryGraphic({
  story,
  template,
  animationStyle,
  className = '',
}: StoryGraphicProps) {
  const type: StoryType = story.type;
  const common = { template, animationStyle, className };

  switch (type) {
    case 'win_probability':
      return (
        <WinProbBar story={story as Story<WinProbData>} {...common} />
      );
    case 'h2h':
    case 'player_comparison':
      return (
        <H2HCards story={story as unknown as Story<H2HData>} {...common} />
      );
    case 'milestone':
      return (
        <MilestoneAlert story={story as Story<MilestoneData>} {...common} />
      );
    case 'form':
    case 'comeback':
      return (
        <FormGuide story={story as unknown as Story<FormData>} {...common} />
      );
    case 'zone':
      return <ZoneAdvantage story={story as Story<ZoneData>} {...common} />;
    default:
      return null;
  }
}
