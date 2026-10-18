/**
 * AEGIS AI — lower-third template: Banner.
 *
 * Full-width top banner with a gradient wash: the event label on the left
 * in accent, the commentary line beside it. Drops in from the top.
 */

import type { LowerThirdProps } from '../registry';
import { LowerThirdStandby } from './lowerthird-standby';

export default function LowerThirdBanner({
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
    <div key={animKey} className="gfx-anim-drop-in absolute left-0 right-0 top-0">
      <div
        className="flex items-center gap-6 px-8 py-4 backdrop-blur-sm"
        style={{
          background:
            'linear-gradient(180deg, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.55) 70%, transparent 100%)',
        }}
      >
        <div
          className="shrink-0 rounded px-3 py-1.5 font-mono text-sm font-bold tracking-[0.3em] text-black"
          style={{ backgroundColor: 'var(--ov-accent)' }}
        >
          {title}
        </div>
        <div className="truncate text-2xl font-extrabold">{subtitle}</div>
      </div>
    </div>
  );
}
