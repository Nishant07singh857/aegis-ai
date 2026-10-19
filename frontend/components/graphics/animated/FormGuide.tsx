/**
 * AEGIS AI — animated story graphic: Team Form Guide (Phase 19, W2).
 *
 * Last-5 W/L badges pop in one-by-one (0.2s stagger each), with a 🔥
 * indicator when the team won 4+ of the last 5.
 *
 * Story: { type: 'form' | 'comeback', title, body, data: FormData }.
 * Templates: 'badges' (default) | 'timeline'.
 * Unknown template ids fall back to 'badges'.
 */

'use client';

import { WaitingFeed } from '@/components/overlay-kit';
import type { AnimationStyle } from './animation-style';
import { AnimationStyleProvider, useAnimationStyle } from './animation-style';
import type { FormData, FormTemplate, Story } from './types';

const BADGE_STYLE: Record<'W' | 'L' | 'D', string> = {
  W: 'bg-emerald-500/20 border-emerald-400/70 text-emerald-300',
  L: 'bg-red-500/20 border-red-400/70 text-red-300',
  D: 'bg-zinc-500/20 border-zinc-400/60 text-zinc-300',
};

const BADGE_GLYPH: Record<'W' | 'L' | 'D', string> = {
  W: 'W',
  L: 'L',
  D: 'D',
};

function FormBadges({ results }: { results: FormData['results'] }) {
  const { reducedMotion } = useAnimationStyle();
  return (
    <div className="flex items-center gap-2" role="list" aria-label="Last 5 match results">
      {results.map((r, i) => (
        <div
          key={i}
          role="listitem"
          aria-label={`Match ${i + 1}: ${r === 'W' ? 'win' : r === 'L' ? 'loss' : 'draw'}`}
          className={`${reducedMotion ? '' : 'ag-badge'} flex h-10 w-10 items-center justify-center rounded-lg border-2 font-mono text-sm font-black ${
            BADGE_STYLE[r]
          }`}
          style={{ animationDelay: `${i * 200}ms` }}
        >
          {BADGE_GLYPH[r]}
        </div>
      ))}
    </div>
  );
}

function HotIndicator({ wins, count }: { wins: number; count: number }) {
  if (wins < 4) return null;
  const { reducedMotion } = useAnimationStyle();
  return (
    <span
      className={`${reducedMotion ? '' : 'ag-badge'} ml-1 inline-flex items-center gap-1 rounded-full bg-[#f5b301]/15 px-2.5 py-1 font-mono text-xs font-black tracking-widest text-[#f5b301]`}
      style={{ animationDelay: `${count * 200}ms` }}
      title={`${wins} wins in the last ${count}`}
    >
      🔥 HOT FORM
    </span>
  );
}

export interface FormGuideProps {
  story: Story<FormData>;
  template?: FormTemplate | string;
  animationStyle?: AnimationStyle;
  className?: string;
}

export default function FormGuide({
  story,
  template = 'badges',
  animationStyle,
  className = '',
}: FormGuideProps) {
  const view = (
    <FormGuideView story={story} template={template} className={className} />
  );
  return animationStyle ? (
    <AnimationStyleProvider value={animationStyle}>
      {view}
    </AnimationStyleProvider>
  ) : (
    view
  );
}

function FormGuideView({
  story,
  template = 'badges',
  className = '',
}: Omit<FormGuideProps, 'animationStyle'>) {
  const { enterClass } = useAnimationStyle();
  const { team_tag, results = [], note } = story.data;

  if (!team_tag || results.length === 0)
    return <WaitingFeed what="FORM GUIDE FEED" />;

  const last5 = results.slice(-5);
  const wins = last5.filter((r) => r === 'W').length;
  const tpl: FormTemplate = template === 'timeline' ? 'timeline' : 'badges';

  const caption = note ?? story.body;

  if (tpl === 'timeline') {
    return (
      <div
        className={`w-full max-w-md rounded-xl border border-white/10 bg-[#0a0e14]/95 p-5 shadow-xl ${className}`}
      >
        <div className={enterClass('up')}>
          <div className="font-mono text-xs font-bold tracking-[0.3em] text-white/50">
            FORM — LAST {last5.length}
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-xl font-black tracking-tight text-white">
              {team_tag}
            </span>
            <HotIndicator wins={wins} count={last5.length} />
          </div>
        </div>
        <div className="mt-4 space-y-0">
          {last5.map((r, i) => (
            <TimelineRow key={i} result={r} index={i} total={last5.length} />
          ))}
        </div>
        {caption && (
          <p className="mt-4 text-sm leading-relaxed text-white/60">{caption}</p>
        )}
      </div>
    );
  }

  // badges (default)
  return (
    <div
      className={`w-full max-w-md rounded-xl border border-white/10 bg-[#0a0e14]/95 p-5 shadow-xl ${className}`}
    >
      <div className={enterClass('up')}>
        <div className="font-mono text-xs font-bold tracking-[0.3em] text-white/50">
          FORM — LAST {last5.length}
        </div>
        <div className="mt-1 text-xl font-black tracking-tight text-white">
          {team_tag}
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center">
        <FormBadges results={last5} />
        <HotIndicator wins={wins} count={last5.length} />
      </div>
      {caption && (
        <p className="mt-4 text-sm leading-relaxed text-white/60">{caption}</p>
      )}
    </div>
  );
}

function TimelineRow({
  result,
  index,
  total,
}: {
  result: 'W' | 'L' | 'D';
  index: number;
  total: number;
}) {
  const { reducedMotion } = useAnimationStyle();
  const dot =
    result === 'W'
      ? 'bg-emerald-400'
      : result === 'L'
        ? 'bg-red-400'
        : 'bg-zinc-400';
  return (
    <div
      className={`${reducedMotion ? '' : 'ag-badge'} flex items-center gap-3 py-1.5`}
      style={{ animationDelay: `${index * 200}ms` }}
    >
      <span className={`h-3 w-3 shrink-0 rounded-full ${dot}`} aria-hidden />
      <span className="font-mono text-xs text-white/50">
        MATCH {total - index}
      </span>
      <span
        className={`font-mono text-sm font-black ${
          result === 'W'
            ? 'text-emerald-300'
            : result === 'L'
              ? 'text-red-300'
              : 'text-zinc-300'
        }`}
      >
        {result === 'W' ? 'WIN' : result === 'L' ? 'LOSS' : 'DRAW'}
      </span>
    </div>
  );
}
