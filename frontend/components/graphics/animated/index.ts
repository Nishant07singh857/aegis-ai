/**
 * AEGIS AI — animated story graphics barrel (Phase 19, W2).
 *
 * Cricket-style animated graphics for the AI storyteller's 7 story
 * types. Import animated.css once — it is already pulled in by the
 * /broadcast, /overlays and /watch layouts.
 */

export { default as WinProbBar } from './WinProbBar';
export { default as H2HCards } from './H2HCards';
export { default as MilestoneAlert } from './MilestoneAlert';
export { default as FormGuide } from './FormGuide';
export { default as ZoneAdvantage } from './ZoneAdvantage';
export { default as StoryGraphic } from './StoryGraphic';
export { TemplatePicker, AnimationStylePicker } from './TemplatePicker';
export {
  AnimationStyleProvider,
  useAnimationStyle,
  ANIMATION_STYLES,
  ANIMATION_STYLE_LABELS,
} from './animation-style';
export type { AnimationStyle } from './animation-style';
export {
  STORY_TEMPLATES,
  STORY_TYPE_LABELS,
} from './types';
export type {
  Story,
  StoryType,
  StoryTemplateId,
  WinProbData,
  WinProbTeam,
  WinProbTemplate,
  H2HData,
  H2HPlayer,
  H2HTemplate,
  MilestoneData,
  MilestoneTemplate,
  FormData,
  FormTemplate,
  ZoneData,
  ZoneTemplate,
} from './types';
export { default as WwcdCards } from './wwcd-cards';
