'use client';

/**
 * AEGIS AI — Match Point badge (Phase 10, staging).
 *
 * Small gold "MATCH POINT" badge for teams on match point. Props only —
 * no fetching, no state — so it drops straight into any graphic.
 *
 * INTEGRATION POINT (Phase 9): after Phase 9 lands, render inside the
 * scorebug / standings templates (frontend/components/graphics/templates/)
 * next to the team name whenever the team appears in the
 * `matchpoint.state` WS frame's `last_evaluation.on_match_point` list:
 *
 *   {onMatchPoint && <MatchPointBadge teamName={t.name} reason={t.reason} />}
 *
 * The badge shows the reason (e.g. "Reached the 40-point match-point
 * threshold" or the finals-day smash reason) as a tooltip / title text.
 */

import type { CSSProperties } from 'react';

const BADGE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.35rem',
  background: 'linear-gradient(180deg, #ffe9a8 0%, #ffd166 50%, #d4a017 100%)',
  color: '#2b2113',
  fontWeight: 800,
  fontSize: '0.7rem',
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  padding: '0.25rem 0.6rem',
  borderRadius: 999,
  boxShadow: '0 0 12px rgba(255,209,102,0.45)',
  whiteSpace: 'nowrap',
  fontFamily: "'Rajdhani','Segoe UI',system-ui,sans-serif",
};

export default function MatchPointBadge({
  teamName,
  reason,
}: {
  /** Displayed in the tooltip for operator clarity. */
  teamName?: string;
  /** Why the team is on match point (from the backend evaluation). */
  reason: string;
}) {
  const title = teamName ? `${teamName}: ${reason}` : reason;
  return (
    <span style={BADGE} title={title} role="status" aria-label={`Match point: ${title}`}>
      <span aria-hidden="true">◆</span> Match Point
    </span>
  );
}
