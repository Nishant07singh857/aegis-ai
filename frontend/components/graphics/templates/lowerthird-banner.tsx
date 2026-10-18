/**
 * AEGIS AI — lower-third template: Banner (Phase 8, W2 excellence pass).
 *
 * Full-width top banner with a gradient wash: the event label on the left
 * in accent, the commentary line beside it. Drops in from the top;
 * hiding it slides it back up before the standby pill returns — the
 * last-shown line stays frozen while it exits.
 */

import type { LowerThirdProps } from '../registry';
import { useExitingContent } from './_anim';
import { LowerThirdStandby } from './_lowerthird-standby';

export default function LowerThirdBanner({
  visible,
  title,
  subtitle,
  status,
  animKey,
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
      className={`absolute left-0 right-0 top-0 ${
        phase === 'exit' ? 'gfx-anim-slide-out-up' : 'gfx-anim-drop-in'
      }`}
    >
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
          {content.title}
        </div>
        <div className="truncate text-2xl font-extrabold">
          {content.subtitle}
        </div>
      </div>
    </div>
  );
}
