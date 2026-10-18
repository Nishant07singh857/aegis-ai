/**
 * AEGIS AI — lower-third template: Slide.
 *
 * Port of the original /overlays/lower-third design: bottom-left card with
 * an accent bar, the event label and the commentary line, sliding in from
 * the left whenever a new line arrives.
 */

import type { LowerThirdProps } from '../registry';
import { LowerThirdStandby } from './lowerthird-standby';

export default function LowerThirdSlide({
  visible,
  title,
  subtitle,
  status,
  animKey,
}: LowerThirdProps) {
  if (!visible || !subtitle) {
    return <LowerThirdStandby status={status} />;
  }

  return (
    <div key={animKey} className="gfx-anim-slide-in-left absolute bottom-12 left-8 max-w-[46rem]">
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
              {title}
            </div>
            <div className="text-3xl font-extrabold leading-snug">
              {subtitle}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
