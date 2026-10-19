/**
 * AEGIS AI — animated story graphic: Milestone Alert (Phase 19, W2).
 *
 * Slides up from the bottom + pulse, gold border, auto-dismisses after
 * 5s (slide-down exit). A manual ✕ also dismisses early.
 *
 * Story: { type: 'milestone', title, body, data: MilestoneData }.
 * Templates: 'banner' (default) | 'ticker' | 'fullscreen'.
 * Unknown template ids fall back to 'banner'.
 */

'use client';

import { WaitingFeed } from '@/components/overlay-kit';
import type { AnimationStyle } from './animation-style';
import { AnimationStyleProvider, useAnimationStyle } from './animation-style';
import { useAutoDismiss } from './_hooks';
import type { MilestoneData, MilestoneTemplate, Story } from './types';

/** Re-key on a new story so the entrance + 5s timer restart. */
function storyKey(story: Story<MilestoneData>): string {
  return `${story.title}::${story.body}::${story.data.player ?? ''}`;
}

function GoldShell({
  children,
  enter,
  exiting,
  className = '',
  onDismiss,
}: {
  children: React.ReactNode;
  enter: string;
  exiting: boolean;
  className?: string;
  onDismiss: () => void;
}) {
  return (
    <div
      role="alert"
      className={`${exiting ? 'ag-exit-slide-down' : `ag-pulse ${enter}`}
        rounded-2xl border-2 border-[#f5b301]/80 bg-[#0a0e14]/95 shadow-[0_0_40px_rgba(245,179,1,0.3)] ${className}`}
    >
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss milestone alert"
        className="absolute right-3 top-3 rounded-full border border-white/15 px-2 py-0.5 font-mono text-xs text-white/50 transition-colors hover:border-white/40 hover:text-white"
      >
        ✕
      </button>
      {children}
    </div>
  );
}

export interface MilestoneAlertProps {
  story: Story<MilestoneData>;
  template?: MilestoneTemplate | string;
  animationStyle?: AnimationStyle;
  /** Override the 5s auto-dismiss (0 = stay until dismissed manually). */
  dismissAfterMs?: number;
  className?: string;
}

export default function MilestoneAlert({
  story,
  template = 'banner',
  animationStyle,
  dismissAfterMs = 5000,
  className = '',
}: MilestoneAlertProps) {
  const view = (
    <MilestoneAlertView
      key={storyKey(story)}
      story={story}
      template={template}
      dismissAfterMs={dismissAfterMs}
      className={className}
    />
  );
  return animationStyle ? (
    <AnimationStyleProvider value={animationStyle}>
      {view}
    </AnimationStyleProvider>
  ) : (
    view
  );
}

function MilestoneAlertView({
  story,
  template = 'banner',
  dismissAfterMs,
  className = '',
}: Omit<MilestoneAlertProps, 'animationStyle'>) {
  const { enterClass, reducedMotion } = useAnimationStyle();
  const { phase, dismiss } = useAutoDismiss(dismissAfterMs);
  const { player, team_tag, milestone } = story.data;

  if (phase === 'gone') return null;
  const exiting = phase === 'exit';
  const enter = reducedMotion ? 'ag-enter-none' : enterClass('up');

  const headline = milestone ?? story.body ?? story.title;
  const hasContent = player || headline;

  if (!hasContent) return <WaitingFeed what="MILESTONE FEED" />;

  const tpl: MilestoneTemplate =
    template === 'ticker' || template === 'fullscreen' ? template : 'banner';

  if (tpl === 'ticker') {
    return (
      <div
        role="alert"
        className={`w-full overflow-hidden border-y-2 border-[#f5b301]/70 bg-[#0a0e14]/95 py-2 ${
          exiting ? 'ag-exit-fade' : ''
        } ${className}`}
      >
        <div className="ag-ticker-inner font-mono text-sm font-bold tracking-wide text-[#f5b301]">
          <span className="mx-8">⚡ MILESTONE</span>
          {player && <span className="mx-8 text-white">{player}</span>}
          <span className="mx-8">{headline}</span>
          <span className="mx-8 text-white/40">⚡ MILESTONE</span>
          {player && <span className="mx-8 text-white">{player}</span>}
          <span className="mx-8">{headline}</span>
        </div>
      </div>
    );
  }

  if (tpl === 'fullscreen') {
    return (
      <div
        className={`flex min-h-[50vh] items-center justify-center bg-black/70 p-8 ${className}`}
      >
        <GoldShell enter={enter} exiting={exiting} onDismiss={dismiss} className="relative w-full max-w-2xl p-10 text-center">
          <div className="font-mono text-sm font-black tracking-[0.4em] text-[#f5b301]">
            ⚡ MILESTONE ALERT
          </div>
          {player && (
            <div className="mt-4 text-5xl font-black tracking-tight text-white">
              {player}
            </div>
          )}
          {team_tag && (
            <div className="mt-1 font-mono text-sm tracking-[0.25em] text-[#00f0ff]/80">
              {team_tag}
            </div>
          )}
          <p className="mt-4 text-xl font-bold text-white/85">{headline}</p>
        </GoldShell>
      </div>
    );
  }

  // banner (default): bottom-anchored alert card.
  return (
    <div className={`w-full max-w-2xl ${className}`}>
      <GoldShell enter={enter} exiting={exiting} onDismiss={dismiss} className="relative p-5 pl-6">
        <div className="font-mono text-xs font-black tracking-[0.35em] text-[#f5b301]">
          ⚡ MILESTONE ALERT
        </div>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-3">
          {player && (
            <span className="text-2xl font-black tracking-tight text-white">
              {player}
            </span>
          )}
          {team_tag && (
            <span className="font-mono text-xs tracking-[0.2em] text-[#00f0ff]/80">
              {team_tag}
            </span>
          )}
        </div>
        <p className="mt-1.5 text-base font-bold text-white/85">{headline}</p>
      </GoldShell>
    </div>
  );
}
