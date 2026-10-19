/**
 * AEGIS AI — animated story graphic: Win Probability Bar (Phase 19, W2).
 *
 * Cricket-style: bars fill left → right (1s scaleX), percentages count
 * up, the leader's bar glows, ●LIVE badge pulses.
 *
 * Story: { type: 'win_probability', title, body, data: WinProbData }.
 * Feed cadence: the storyteller recomputes roughly every 30s; when a new
 * story arrives the bars glide (1s CSS transition) and numbers count up
 * from their previous values instead of jumping.
 *
 * Templates: 'bars' (default) | 'compact' | 'fullscreen'. Unknown ids
 * fall back to 'bars' — a bad prop can never render blank.
 */

'use client';

import type { CSSProperties } from 'react';
import { WaitingFeed } from '@/components/overlay-kit';
import type { AnimationStyle } from './animation-style';
import { AnimationStyleProvider, useAnimationStyle } from './animation-style';
import { fmtCounted, useCountUp } from './_hooks';
import type { Story, WinProbData, WinProbTemplate } from './types';

const BAR_FALLBACK_COLORS = ['#00f0ff', '#f5b301', '#7c5cff', '#3ddc84'];

function clampProb(p: number | null | undefined): number | null {
  if (p == null || !Number.isFinite(p)) return null;
  return Math.min(100, Math.max(0, p));
}

function TeamRow({
  tag,
  probability,
  color,
  leader,
  index,
  compact,
}: {
  tag: string;
  probability: number | null | undefined;
  color?: string;
  leader: boolean;
  index: number;
  compact: boolean;
}) {
  const { enterClass } = useAnimationStyle();
  const prob = clampProb(probability);
  const counted = useCountUp(prob, 1000);
  const fill = prob == null ? 0 : prob / 100;
  const barColor = color ?? BAR_FALLBACK_COLORS[index % BAR_FALLBACK_COLORS.length];

  return (
    <div
      className={enterClass('left')}
      style={{ animationDelay: `${index * 120}ms` }}
    >
      <div className="flex items-center justify-between gap-3">
        <span
          className={`truncate font-bold tracking-wide text-white/95 ${
            compact ? 'text-xs' : 'text-sm'
          }`}
        >
          {tag}
        </span>
        <span
          className={`font-mono font-bold tabular-nums ${
            leader ? 'text-[#f5b301]' : 'text-white/90'
          } ${compact ? 'text-sm' : 'text-lg'}`}
          aria-label={`${tag} win probability ${prob == null ? 'unknown' : `${Math.round(prob)} percent`}`}
        >
          {fmtCounted(counted, (n) => `${Math.round(n)}%`)}
        </span>
      </div>
      <div className={`relative mt-1 ${compact ? 'h-2.5' : 'h-4'}`}>
        {leader && prob != null && <div className="ag-glow" aria-hidden />}
        <div className="ag-bar-track absolute inset-0">
          <div
            className="ag-bar-fill"
            style={
              {
                '--ag-fill': String(fill),
                background: `linear-gradient(90deg, ${barColor}cc, ${barColor})`,
              } as CSSProperties
            }
          />
        </div>
      </div>
    </div>
  );
}

function Header({ live = true }: { live?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="font-mono text-xs font-bold tracking-[0.3em] text-white/60">
        WIN PROBABILITY
      </span>
      {live && (
        <span className="flex items-center gap-1.5 rounded bg-red-500/15 px-2 py-0.5 font-mono text-[11px] font-bold tracking-[0.2em] text-red-400">
          <span className="ag-live-dot" aria-hidden />
          LIVE
        </span>
      )}
    </div>
  );
}

export interface WinProbBarProps {
  story: Story<WinProbData>;
  template?: WinProbTemplate | string;
  animationStyle?: AnimationStyle;
  className?: string;
}

export default function WinProbBar({
  story,
  template = 'bars',
  animationStyle,
  className = '',
}: WinProbBarProps) {
  // The per-graphic `animationStyle` prop overrides the context for this
  // graphic's whole subtree, so nested rows pick it up.
  const view = (
    <WinProbBarView story={story} template={template} className={className} />
  );
  return animationStyle ? (
    <AnimationStyleProvider value={animationStyle}>
      {view}
    </AnimationStyleProvider>
  ) : (
    view
  );
}

function WinProbBarView({
  story,
  template = 'bars',
  className = '',
}: Omit<WinProbBarProps, 'animationStyle'>) {
  const teams = [...(story.data.teams ?? [])]
    .filter((t) => t.team_tag)
    .sort(
      (a, b) => (clampProb(b.probability) ?? -1) - (clampProb(a.probability) ?? -1)
    );

  if (teams.length === 0) return <WaitingFeed what="WIN PROBABILITY FEED" />;

  const tpl: WinProbTemplate =
    template === 'compact' || template === 'fullscreen' ? template : 'bars';
  const visible = tpl === 'compact' ? teams.slice(0, 3) : teams.slice(0, 6);

  if (tpl === 'fullscreen') {
    return (
      <div
        className={`flex min-h-[60vh] items-center justify-center bg-black/80 p-8 ${className}`}
      >
        <div className="w-full max-w-3xl rounded-2xl border border-white/10 bg-[#0a0e14]/95 p-8 shadow-2xl">
          <Header />
          <h2 className="mt-4 text-3xl font-black tracking-tight text-white">
            {story.title}
          </h2>
          {story.body && (
            <p className="mt-2 text-sm leading-relaxed text-white/60">
              {story.body}
            </p>
          )}
          <div className="mt-6 space-y-5">
            {visible.map((t, i) => (
              <TeamRow
                key={t.team_tag}
                tag={t.team_tag}
                probability={t.probability}
                color={t.color}
                leader={i === 0}
                index={i}
                compact={false}
              />
            ))}
          </div>
          {story.data.delta_note && (
            <p className="mt-5 font-mono text-xs text-[#00f0ff]/80">
              {story.data.delta_note}
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`w-full max-w-md rounded-xl border border-white/10 bg-[#0a0e14]/95 p-4 shadow-xl ${
        tpl === 'compact' ? 'p-3' : ''
      } ${className}`}
    >
      <Header />
      <div className={`space-y-3 ${tpl === 'compact' ? 'mt-2 space-y-2' : 'mt-3'}`}>
        {visible.map((t, i) => (
          <TeamRow
            key={t.team_tag}
            tag={t.team_tag}
            probability={t.probability}
            color={t.color}
            leader={i === 0}
            index={i}
            compact={tpl === 'compact'}
          />
        ))}
      </div>
      {story.data.delta_note && tpl !== 'compact' && (
        <p className="mt-3 font-mono text-[11px] text-[#00f0ff]/80">
          {story.data.delta_note}
        </p>
      )}
    </div>
  );
}
