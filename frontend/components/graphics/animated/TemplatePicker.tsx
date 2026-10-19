/**
 * AEGIS AI — TemplatePicker + AnimationStylePicker (Phase 19, W2).
 *
 * The graphics-section operator UI for the animated story graphics:
 * pick one of the 2–3 layout templates per story type, and pick the
 * animation style (slide | fade | scale | none). The style picker is
 * wired to the AnimationStyleProvider context, so it drives every
 * story graphic on the page at once; a per-graphic `animationStyle`
 * prop can still override it for a single graphic.
 */

'use client';

import type { AnimationStyle } from './animation-style';
import {
  ANIMATION_STYLES,
  ANIMATION_STYLE_LABELS,
  useAnimationStyle,
} from './animation-style';
import { STORY_TEMPLATES, STORY_TYPE_LABELS, type StoryType } from './types';

export function AnimationStylePicker({
  className = '',
}: {
  className?: string;
}) {
  const { style, setStyle } = useAnimationStyle();
  return (
    <div className={className} role="group" aria-label="Animation style">
      <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-white/45">
        Animation style
      </div>
      <div className="inline-flex rounded-lg border border-white/10 bg-black/40 p-1">
        {ANIMATION_STYLES.map((s: AnimationStyle) => (
          <button
            key={s}
            type="button"
            onClick={() => setStyle(s)}
            aria-pressed={style === s}
            className={`rounded-md px-3 py-1.5 font-mono text-xs font-bold uppercase tracking-[0.12em] transition-all ${
              style === s
                ? 'bg-[#00f0ff] text-black shadow-[0_0_12px_rgba(0,240,255,0.4)]'
                : 'text-white/55 hover:text-white'
            }`}
          >
            {ANIMATION_STYLE_LABELS[s]}
          </button>
        ))}
      </div>
    </div>
  );
}

export interface TemplatePickerProps {
  storyType: StoryType;
  /** Currently selected template id for this story type. */
  template: string;
  onTemplateChange: (template: string) => void;
  className?: string;
}

/**
 * Template buttons for one story type + the shared animation style
 * picker. Must be rendered inside an <AnimationStyleProvider> for the
 * style picker to work (it no-ops gracefully without one).
 */
export function TemplatePicker({
  storyType,
  template,
  onTemplateChange,
  className = '',
}: TemplatePickerProps) {
  const templates = STORY_TEMPLATES[storyType] ?? [];
  return (
    <div className={`space-y-3 ${className}`}>
      <div role="group" aria-label={`${STORY_TYPE_LABELS[storyType]} template`}>
        <div className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-white/45">
          {STORY_TYPE_LABELS[storyType]} — template
        </div>
        <div className="inline-flex flex-wrap gap-1 rounded-lg border border-white/10 bg-black/40 p-1">
          {templates.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => onTemplateChange(t)}
              aria-pressed={template === t}
              className={`rounded-md px-3 py-1.5 font-mono text-xs font-bold tracking-[0.08em] transition-all ${
                template === t
                  ? 'bg-[#f5b301] text-black shadow-[0_0_12px_rgba(245,179,1,0.4)]'
                  : 'text-white/55 hover:text-white'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      <AnimationStylePicker />
    </div>
  );
}
