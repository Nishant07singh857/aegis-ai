/**
 * AEGIS AI — lower-third template: Slide (Phase 8, W2 excellence pass).
 *
 * Port of the original /overlays/lower-third design: bottom-left card with
 * an accent bar, the event label and the commentary line, sliding in from
 * the left whenever a new line arrives (key={animKey} re-triggers the
 * entrance). Hiding the graphic plays a mirrored slide-out exit before
 * the standby pill returns — the last-shown line stays frozen while it
 * exits, never a blank card.
 */

import type { LowerThirdProps } from '../registry';
import { useExitingContent } from './_anim';
import { LowerThirdStandby } from './_lowerthird-standby';

export default function LowerThirdSlide({
  visible,
  title,
  subtitle,
  status,
  animKey,
  positionClass,
}: LowerThirdProps) {
  const { phase, content } = useExitingContent(visible && subtitle !== '', {
    title,
    subtitle,
  });

  if (phase === 'gone') {
    return <LowerThirdStandby status={status} />;
  }

  return (
    <div
      key={animKey}
      className={`absolute ${positionClass ?? 'bottom-12 left-8 max-w-[46rem]'} ${
        phase === 'exit' ? 'gfx-anim-slide-out-left' : 'gfx-anim-slide-in-left'
      }`}
    >
      <div className="overflow-hidden rounded-r-xl rounded-l-sm bg-black/75 backdrop-blur-sm">
        <div className="flex items-stretch">
          <div
            className="w-2 shrink-0"
            style={{ backgroundColor: 'var(--ov-accent)' }}
          />
          <div className="px-6 py-4">
            <div
              className="mb-1 font-mono text-xs font-bold tracking-[0.35em]"
              style={{ color: 'var(--ov-accent)' }}
            >
              {content.title}
            </div>
            <div className="text-3xl font-extrabold leading-snug">
              {content.subtitle}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
