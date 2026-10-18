'use client';

/**
 * AEGIS AI — PiP layout (Phase 10, graphics-staging).
 *
 * Composition primitive for picture-in-picture broadcast frames:
 * a full-bleed main slot plus an inset slot pinned to one of the four
 * corners. Controlled by the backend PiP service
 * (GET/POST /api/v1/pip/state; WS "pip.state" frames):
 *
 *   { enabled, main_source, inset_source, inset_position,
 *     updated_by, updated_at }
 *
 * inset_position is one of "top_left" | "top_right" | "bottom_left" |
 * "bottom_right". When `enabled` is false only the main slot renders.
 *
 * This component only lays out the slots — it never fabricates content.
 * Callers pass the real main view and the real inset view as children;
 * a caller that has nothing live for a slot should pass its own honest
 * standby, not a fake feed.
 */

import type { ReactNode } from 'react';

export type InsetPosition =
  | 'top_left'
  | 'top_right'
  | 'bottom_left'
  | 'bottom_right';

export interface PiPLayoutProps {
  /** Show the inset. When false, only the main slot renders. */
  enabled: boolean;
  /** Corner the inset is pinned to. */
  insetPosition: InsetPosition;
  /** Full-bleed main view. */
  mainSlot: ReactNode;
  /** Inset view (e.g. the map cam). */
  insetSlot: ReactNode;
  /** Label shown on the inset frame; defaults to the inset source key. */
  insetLabel?: string;
  className?: string;
}

const INSET_POSITIONS: Record<InsetPosition, string> = {
  top_left: 'left-3 top-3',
  top_right: 'right-3 top-3',
  bottom_left: 'bottom-3 left-3',
  bottom_right: 'bottom-3 right-3',
};

export function PiPLayout({
  enabled,
  insetPosition,
  mainSlot,
  insetSlot,
  insetLabel,
  className = '',
}: PiPLayoutProps) {
  const corner = INSET_POSITIONS[insetPosition] ?? INSET_POSITIONS.bottom_right;

  return (
    <div className={`relative h-full w-full overflow-hidden bg-black ${className}`}>
      {/* main view */}
      <div className="absolute inset-0">{mainSlot}</div>

      {/* inset */}
      {enabled && (
        <div
          className={`absolute ${corner} aspect-video w-[26%] min-w-[180px] max-w-[420px] overflow-hidden rounded-lg border-2 border-white/25 bg-black shadow-2xl shadow-black/60`}
          data-testid="pip-inset"
        >
          {insetSlot}
          {insetLabel && (
            <div className="pointer-events-none absolute left-1.5 top-1.5 rounded bg-black/65 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-white/80">
              {insetLabel}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
