/**
 * AEGIS AI — animated story graphic: Head-to-Head comparison
 * (Phase 19, W2).
 *
 * Cricket-style: the two cards slide in from the sides (0.8s), the
 * winner's card is larger with a glow, numbers count up.
 *
 * Stories: { type: 'h2h' | 'player_comparison', title, body, data }.
 * Templates: 'side-by-side' (default) | 'top-bottom' | 'minimal'.
 * Unknown template ids fall back to 'side-by-side'.
 *
 * Winner: explicit `data.winner` first; otherwise the higher finishes,
 * then the higher rating. No clear winner → equal cards, no glow —
 * never faked.
 */

'use client';

import { WaitingFeed } from '@/components/overlay-kit';
import type { AnimationStyle } from './animation-style';
import { AnimationStyleProvider, useAnimationStyle } from './animation-style';
import { fmtCounted, useCountUp } from './_hooks';
import type { H2HData, H2HPlayer, H2HTemplate, Story } from './types';

type Side = 'a' | 'b';

function decideWinner(data: H2HData): Side | null {
  if (data.winner === 'a' || data.winner === 'b') return data.winner;
  const fa = data.player_a.finishes;
  const fb = data.player_b.finishes;
  if (fa != null && fb != null && fa !== fb) return fa > fb ? 'a' : 'b';
  const ra = data.player_a.rating;
  const rb = data.player_b.rating;
  if (ra != null && rb != null && ra !== rb) return ra > rb ? 'a' : 'b';
  return null;
}

function StatLine({ label, value, format }: {
  label: string;
  value: number | null | undefined;
  format?: (n: number) => string;
}) {
  const counted = useCountUp(value, 1000);
  return (
    <div className="flex flex-col items-center">
      <span className="font-mono text-2xl font-black tabular-nums text-white">
        {fmtCounted(counted, format)}
      </span>
      <span className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.25em] text-white/45">
        {label}
      </span>
    </div>
  );
}

function PlayerCard({
  player,
  side,
  winner,
  from,
  delay,
  large,
}: {
  player: H2HPlayer;
  side: Side;
  winner: Side | null;
  from: 'left' | 'right';
  delay: number;
  large?: boolean;
}) {
  const { enterClass } = useAnimationStyle();
  const isWinner = winner === side;
  return (
    <div
      className={`${enterClass(from)} relative flex-1 rounded-2xl border bg-[#0a0e14]/95 p-5 text-center shadow-xl ${
        isWinner
          ? 'border-[#f5b301]/70 shadow-[0_0_36px_rgba(245,179,1,0.25)]'
          : 'border-white/10'
      } ${large ? 'sm:scale-[1.06]' : ''}`}
      style={{ animationDelay: `${delay}ms` }}
      aria-label={`${player.name}${isWinner ? ', winner' : ''}`}
    >
      {isWinner && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-[#f5b301] px-3 py-0.5 font-mono text-[10px] font-black tracking-[0.2em] text-black">
          WINNER
        </div>
      )}
      <div className="truncate text-xl font-black tracking-tight text-white">
        {player.name}
      </div>
      {player.team_tag && (
        <div className="mt-0.5 font-mono text-xs tracking-[0.2em] text-[#00f0ff]/80">
          {player.team_tag}
        </div>
      )}
      <div className="mt-4 grid grid-cols-3 gap-2">
        <StatLine label="Finishes" value={player.finishes} />
        <StatLine
          label="Damage"
          value={player.damage}
          format={(n) => String(Math.round(n))}
        />
        <StatLine
          label="Rating"
          value={player.rating}
          format={(n) => n.toFixed(1)}
        />
      </div>
      {(player.knocks != null || player.clutches != null) && (
        <div className="mt-3 flex items-center justify-center gap-4 font-mono text-xs text-white/55">
          {player.knocks != null && (
            <span>
              KNK <span className="font-bold text-white/85">{player.knocks}</span>
            </span>
          )}
          {player.clutches != null && (
            <span>
              CLUTCH <span className="font-bold text-white/85">{player.clutches}</span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function VsBadge({ delay }: { delay: number }) {
  const { enterClass } = useAnimationStyle();
  return (
    <div
      className={`${enterClass('up')} flex items-center justify-center`}
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-full border border-[#f5b301]/60 bg-[#f5b301]/10 font-mono text-sm font-black tracking-widest text-[#f5b301]">
        VS
      </div>
    </div>
  );
}

export interface H2HCardsProps {
  story: Story<H2HData>;
  template?: H2HTemplate | string;
  animationStyle?: AnimationStyle;
  className?: string;
}

export default function H2HCards({
  story,
  template = 'side-by-side',
  animationStyle,
  className = '',
}: H2HCardsProps) {
  const view = (
    <H2HCardsView story={story} template={template} className={className} />
  );
  return animationStyle ? (
    <AnimationStyleProvider value={animationStyle}>
      {view}
    </AnimationStyleProvider>
  ) : (
    view
  );
}

function H2HCardsView({
  story,
  template = 'side-by-side',
  className = '',
}: Omit<H2HCardsProps, 'animationStyle'>) {
  const { enterClass } = useAnimationStyle();
  const data = story.data;
  const a = data.player_a;
  const b = data.player_b;

  if (!a?.name || !b?.name)
    return <WaitingFeed what="HEAD-TO-HEAD FEED" />;

  const winner = decideWinner(data);
  const tpl: H2HTemplate =
    template === 'top-bottom' || template === 'minimal'
      ? template
      : 'side-by-side';

  const footnote = (
    <>
      {data.note && (
        <p className="mt-4 text-center text-sm leading-relaxed text-white/60">
          {data.note}
        </p>
      )}
      {story.body && !data.note && (
        <p className="mt-4 text-center text-sm leading-relaxed text-white/60">
          {story.body}
        </p>
      )}
    </>
  );

  if (tpl === 'minimal') {
    const fa = useCountUp(a.finishes, 1000);
    const fb = useCountUp(b.finishes, 1000);
    return (
      <div
        className={`w-full max-w-xl rounded-xl border border-white/10 bg-[#0a0e14]/95 px-5 py-4 shadow-xl ${className}`}
      >
        <div className={`${enterClass('up')} flex items-center justify-between gap-4`}>
          <div className="min-w-0 flex-1 text-left">
            <div className="truncate text-lg font-black text-white">{a.name}</div>
            <div className="font-mono text-2xl font-black tabular-nums text-[#00f0ff]">
              {fmtCounted(fa)}
            </div>
          </div>
          <div className="shrink-0 font-mono text-xs font-black tracking-[0.3em] text-[#f5b301]">
            VS
          </div>
          <div className="min-w-0 flex-1 text-right">
            <div className="truncate text-lg font-black text-white">{b.name}</div>
            <div className="font-mono text-2xl font-black tabular-nums text-[#f5b301]">
              {fmtCounted(fb)}
            </div>
          </div>
        </div>
        <div className="mt-1 text-center font-mono text-[10px] uppercase tracking-[0.25em] text-white/40">
          finishes — {story.title}
        </div>
      </div>
    );
  }

  if (tpl === 'top-bottom') {
    return (
      <div className={`w-full max-w-md ${className}`}>
        <div className={`${enterClass('up')} mb-3 text-center`}>
          <div className="font-mono text-xs font-bold tracking-[0.3em] text-[#f5b301]">
            HEAD-TO-HEAD
          </div>
          <h2 className="mt-1 text-xl font-black text-white">{story.title}</h2>
        </div>
        <div className="space-y-3">
          <PlayerCard player={a} side="a" winner={winner} from="left" delay={0} />
          <VsBadge delay={150} />
          <PlayerCard player={b} side="b" winner={winner} from="right" delay={300} />
        </div>
        {footnote}
      </div>
    );
  }

  // side-by-side (default): cards slide in from the sides, 0.8s.
  return (
    <div className={`w-full max-w-3xl ${className}`}>
      <div className={`${enterClass('up')} mb-4 text-center`}>
        <div className="font-mono text-xs font-bold tracking-[0.3em] text-[#f5b301]">
          HEAD-TO-HEAD
        </div>
        <h2 className="mt-1 text-2xl font-black tracking-tight text-white">
          {story.title}
        </h2>
      </div>
      <div className="flex items-stretch gap-3 sm:gap-4">
        <PlayerCard
          player={a}
          side="a"
          winner={winner}
          from="left"
          delay={0}
          large={winner === 'a'}
        />
        <VsBadge delay={200} />
        <PlayerCard
          player={b}
          side="b"
          winner={winner}
          from="right"
          delay={0}
          large={winner === 'b'}
        />
      </div>
      {footnote}
    </div>
  );
}
