/**
 * AEGIS AI — lower-third template: Eliminated (Phase 31B payoff pack).
 *
 * The missing "TEAM X ELIMINATED" banner for the existing
 * team.eliminated event: a stark bottom-left card with a red
 * ELIMINATED pill and the team line beside it. It renders ONLY what
 * the lower-third props carry (title/subtitle from the publisher —
 * see backend/app/services/live_director/payoff.py): the template
 * invents no team name, no placement, no finishes. An empty subtitle
 * falls back to the shared standby pill, exactly like the other
 * lower-third templates, so a banner can never appear for an
 * unnamed team. Graphic only — it never moves a camera, never opens
 * PiP, and never cuts: the switch (if any) is the director's own
 * hard cut, elsewhere.
 *
 * Selectable as ?template=lowerthird-eliminated on
 * /overlays/lower-third and via the graphics template picker. The
 * default lower-third template is unchanged (lowerthird-slide).
 */

import type { LowerThirdProps } from '../registry';
import { useExitingContent } from './_anim';
import { LowerThirdStandby } from './_lowerthird-standby';

export default function LowerThirdEliminated({
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
          <div className="w-2 shrink-0 bg-red-600" />
          <div className="px-6 py-4">
            <div className="mb-1">
              <span className="rounded bg-red-600 px-3 py-1 font-mono text-xs font-bold tracking-[0.35em] text-white">
                {content.title || 'ELIMINATED'}
              </span>
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
