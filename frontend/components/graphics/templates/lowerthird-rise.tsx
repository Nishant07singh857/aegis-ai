/**
 * AEGIS AI — lower-third template: Rise (Phase 8, W2 excellence pass).
 *
 * Bottom-centre card that fades and rises into place: the event label in
 * accent above the commentary line, with a thin accent underline.
 * Hiding the graphic sinks it back down (centred, like the entrance)
 * before the standby pill returns — the last-shown line stays frozen
 * while it exits.
 */

import type { LowerThirdProps } from '../registry';
import { useExitingContent } from './_anim';
import { LowerThirdStandby } from './_lowerthird-standby';

export default function LowerThirdRise({
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
      className={`absolute ${
        positionClass ?? 'bottom-12 left-1/2 w-max max-w-[52rem]'
      } ${
        phase === 'exit'
          ? 'gfx-anim-sink-down-centered'
          : 'gfx-anim-rise-up-centered'
      }`}
    >
      <div className="overflow-hidden rounded-xl bg-black/75 backdrop-blur-sm">
        <div className="px-8 py-4 text-center">
          <div
            className="mb-1.5 font-mono text-xs font-bold tracking-[0.4em]"
            style={{ color: 'var(--ov-accent)' }}
          >
            {content.title}
          </div>
          <div className="text-3xl font-extrabold leading-snug">
            {content.subtitle}
          </div>
        </div>
        <div
          className="h-1 w-full"
          style={{
            background:
              'linear-gradient(90deg, transparent, var(--ov-accent), transparent)',
          }}
        />
      </div>
    </div>
  );
}
