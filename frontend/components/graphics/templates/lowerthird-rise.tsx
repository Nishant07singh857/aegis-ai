/**
 * AEGIS AI — lower-third template: Rise.
 *
 * Bottom-centre card that fades and rises into place: the event label in
 * accent above the commentary line, with a thin accent underline.
 */

import type { LowerThirdProps } from '../registry';
import { LowerThirdStandby } from './lowerthird-standby';

export default function LowerThirdRise({
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
    <div
      key={animKey}
      className="gfx-anim-rise-up-centered absolute bottom-12 left-1/2 w-max max-w-[52rem]"
    >
      <div className="overflow-hidden rounded-xl bg-black/75 backdrop-blur-sm">
        <div className="px-8 py-4 text-center">
          <div
            className="mb-1.5 font-mono text-xs font-bold tracking-[0.4em]"
            style={{ color: 'var(--ov-accent)' }}
          >
            {title}
          </div>
          <div className="text-3xl font-extrabold leading-snug">{subtitle}</div>
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
