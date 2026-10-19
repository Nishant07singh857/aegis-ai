/**
 * AEGIS AI — animated story graphic: Zone Advantage (Phase 19, W2).
 *
 * Zone number + favoured team, a stylised map with the zone ring
 * highlighted (zoom/fade entrance), and the evidence line verbatim
 * from the story.
 *
 * Story: { type: 'zone', title, body, data: ZoneData }.
 * Templates: 'card' (default) | 'overlay'.
 * Unknown template ids fall back to 'card'.
 *
 * Honesty note: the map is a stylised illustration, captioned as such —
 * it never pretends to be the real in-game zone position. Only the
 * zone number, team and evidence text come from the story data.
 */

'use client';

import { WaitingFeed } from '@/components/overlay-kit';
import type { AnimationStyle } from './animation-style';
import { AnimationStyleProvider, useAnimationStyle } from './animation-style';
import type { Story, ZoneData, ZoneTemplate } from './types';

/** Stylised zone map: dark grid + pulsing zone ring. Decorative — the
 *  caption says so, so it never masquerades as real game footage. */
function ZoneMap({ zoneLabel }: { zoneLabel: string }) {
  const { enterClass } = useAnimationStyle();
  return (
    <div className={`${enterClass('up')} relative`}>
      <svg
        viewBox="0 0 200 200"
        className="h-44 w-44"
        role="img"
        aria-label={`Stylised map illustration for ${zoneLabel}`}
      >
        <rect x="0" y="0" width="200" height="200" rx="12" fill="#0d141c" />
        {Array.from({ length: 9 }, (_, i) => (
          <g key={i} stroke="#1d2a38" strokeWidth="1">
            <line x1={(i + 1) * 20} y1="0" x2={(i + 1) * 20} y2="200" />
            <line x1="0" y1={(i + 1) * 20} x2="200" y2={(i + 1) * 20} />
          </g>
        ))}
        {/* decorative landmass hints */}
        <path
          d="M20 150 Q60 110 45 70 Q80 90 120 60 Q160 80 150 130 Q120 170 70 165 Z"
          fill="#16222e"
          stroke="#24384d"
          strokeWidth="1.5"
        />
        {/* zone ring */}
        <circle
          cx="100"
          cy="100"
          r="52"
          fill="none"
          stroke="#00f0ff"
          strokeWidth="2.5"
        />
        <circle
          className="ag-zone-ring"
          cx="100"
          cy="100"
          r="52"
          fill="none"
          stroke="#00f0ff"
          strokeWidth="2"
        />
        <circle cx="100" cy="100" r="5" fill="#f5b301" />
        <text
          x="100"
          y="188"
          textAnchor="middle"
          fill="#8fa3b8"
          fontSize="9"
          fontFamily="monospace"
          letterSpacing="2"
        >
          STYLISED MAP
        </text>
      </svg>
    </div>
  );
}

export interface ZoneAdvantageProps {
  story: Story<ZoneData>;
  template?: ZoneTemplate | string;
  animationStyle?: AnimationStyle;
  className?: string;
}

export default function ZoneAdvantage({
  story,
  template = 'card',
  animationStyle,
  className = '',
}: ZoneAdvantageProps) {
  const view = (
    <ZoneAdvantageView story={story} template={template} className={className} />
  );
  return animationStyle ? (
    <AnimationStyleProvider value={animationStyle}>
      {view}
    </AnimationStyleProvider>
  ) : (
    view
  );
}

function ZoneAdvantageView({
  story,
  template = 'card',
  className = '',
}: Omit<ZoneAdvantageProps, 'animationStyle'>) {
  const { enterClass } = useAnimationStyle();
  const { zone_number, team_tag, evidence } = story.data;

  if (zone_number == null && !team_tag && !evidence && !story.body)
    return <WaitingFeed what="ZONE ADVANTAGE FEED" />;

  const zoneLabel =
    zone_number != null ? `ZONE ${zone_number}` : 'ZONE';
  const tpl: ZoneTemplate = template === 'overlay' ? 'overlay' : 'card';

  if (tpl === 'overlay') {
    // Transparent lower-third style: map thumb + evidence, no box.
    return (
      <div
        className={`flex items-center gap-4 rounded-r-2xl border-l-4 border-[#00f0ff] bg-gradient-to-r from-[#0a0e14]/95 to-transparent py-3 pl-4 pr-8 ${className}`}
      >
        <div className={enterClass('left')}>
          <svg
            viewBox="0 0 200 200"
            className="h-20 w-20"
            role="img"
            aria-label={`Stylised map illustration for ${zoneLabel}`}
          >
            <rect x="0" y="0" width="200" height="200" rx="24" fill="#0d141c" />
            <circle cx="100" cy="100" r="60" fill="none" stroke="#00f0ff" strokeWidth="8" />
            <circle
              className="ag-zone-ring"
              cx="100"
              cy="100"
              r="60"
              fill="none"
              stroke="#00f0ff"
              strokeWidth="6"
            />
            <circle cx="100" cy="100" r="14" fill="#f5b301" />
          </svg>
        </div>
        <div className={enterClass('up')}>
          <div className="font-mono text-xs font-black tracking-[0.3em] text-[#00f0ff]">
            {zoneLabel}
            {team_tag ? ` — ${team_tag} KA ADVANTAGE` : ' ADVANTAGE'}
          </div>
          {(evidence ?? story.body) && (
            <p className="mt-1 max-w-md text-sm font-bold leading-snug text-white/85">
              &ldquo;{evidence ?? story.body}&rdquo;
            </p>
          )}
        </div>
      </div>
    );
  }

  // card (default)
  return (
    <div
      className={`w-full max-w-md rounded-2xl border border-[#00f0ff]/25 bg-[#0a0e14]/95 p-5 shadow-xl ${className}`}
    >
      <div className={`${enterClass('up')} flex items-center justify-between`}>
        <div>
          <div className="font-mono text-xs font-bold tracking-[0.3em] text-[#00f0ff]">
            {zoneLabel}
          </div>
          <h2 className="mt-1 text-xl font-black tracking-tight text-white">
            {team_tag ? `${team_tag} ka advantage` : story.title}
          </h2>
        </div>
        <ZoneMap zoneLabel={zoneLabel} />
      </div>
      {(evidence ?? story.body) && (
        <p className="mt-4 border-l-2 border-[#f5b301]/60 pl-3 text-sm italic leading-relaxed text-white/70">
          &ldquo;{evidence ?? story.body}&rdquo;
        </p>
      )}
    </div>
  );
}
