/**
 * AEGIS AI — shared animation utilities for the graphics templates
 * (Phase 8, W2 excellence pass).
 *
 * Single source of truth (JS side) for stagger timing and exit-animation
 * orchestration. CSS-side tokens (durations, easings) live in
 * graphics.css; GFX_EXIT_MS below must stay equal to --gfx-dur-fast so a
 * row unmounts exactly when its exit animation finishes.
 *
 * Rules inherited from graphics.css: transform / opacity ONLY —
 * GPU-friendly, 60fps inside an OBS browser source.
 */

import { useEffect, useRef, useState } from 'react';

/** Exit-animation length in ms — mirrors --gfx-dur-fast in graphics.css. */
export const GFX_EXIT_MS = 280;

/** Default stagger step for list rows (player boards, table rows). */
export const GFX_STAGGER_ROW_MS = 60;
/** Default stagger step for the standings card grid. */
export const GFX_STAGGER_CARD_MS = 45;
/** Stagger stops growing after this many items (caps total cascade time). */
export const GFX_STAGGER_CAP = 9;

/**
 * "120ms"-style delay string for staggered reveals. The index is
 * clamped to [0, cap] so long lists don't cascade forever.
 */
export function staggerDelay(
  index: number,
  stepMs: number = GFX_STAGGER_ROW_MS,
  cap: number = GFX_STAGGER_CAP
): string {
  const i = Math.min(Math.max(Math.floor(index), 0), cap);
  return `${i * stepMs}ms`;
}

export type ExitPhase = 'enter' | 'exit' | 'gone';

/**
 * Tracks a boolean `present` flag through enter → exit → gone phases.
 * When `present` flips false the phase becomes 'exit' for `exitMs` —
 * render the old content with a gfx-anim-*-out class — then 'gone'.
 * Flipping back to true mid-exit cancels the exit and returns to 'enter'.
 */
export function useExitingFlag(
  present: boolean,
  exitMs: number = GFX_EXIT_MS
): ExitPhase {
  const [phase, setPhase] = useState<ExitPhase>(present ? 'enter' : 'gone');
  const phaseRef = useRef<ExitPhase>(phase);
  phaseRef.current = phase;

  useEffect(() => {
    if (present) {
      if (phaseRef.current !== 'enter') setPhase('enter');
      return;
    }
    if (phaseRef.current === 'enter') {
      setPhase('exit');
      const t = setTimeout(() => setPhase('gone'), exitMs);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [present, exitMs]);

  return phase;
}

/**
 * useExitingFlag plus content freezing: while exiting, the last-present
 * `content` is returned so the exit animation renders the old card
 * rather than blank/empty props.
 */
export function useExitingContent<T>(
  present: boolean,
  content: T,
  exitMs: number = GFX_EXIT_MS
): { phase: ExitPhase; content: T } {
  const phase = useExitingFlag(present, exitMs);
  const frozen = useRef(content);
  if (present) frozen.current = content;
  return { phase, content: frozen.current };
}

export interface ExitingRow<T> {
  key: string;
  item: T;
  exiting: boolean;
}

/**
 * List version of the exit orchestration: items removed from `items`
 * (e.g. killfeed rows whose TTL expired — the page drops them from the
 * prop) stay mounted with `exiting: true` for `exitMs` so a
 * gfx-anim-*-out animation can play before they unmount. New items enter
 * with `exiting: false`.
 *
 * Ordering: live rows follow `items` order; exiting rows keep their
 * previous relative order after the live rows — for a newest-first
 * killfeed the expired row is the oldest (already at the bottom), so it
 * doesn't visually jump when it starts exiting.
 *
 * `keyOf` must be stable (define it at module scope).
 */
export function useExitingList<T>(
  items: readonly T[],
  keyOf: (item: T) => string,
  exitMs: number = GFX_EXIT_MS
): ExitingRow<T>[] {
  const [rows, setRows] = useState<ExitingRow<T>[]>(() =>
    items.map((item) => ({ key: keyOf(item), item, exiting: false }))
  );
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const prev = rowsRef.current;
    const prevByKey = new Map(prev.map((r) => [r.key, r]));
    const liveKeys = new Set<string>();
    const next: ExitingRow<T>[] = [];
    let changed = false;

    for (const item of items) {
      const key = keyOf(item);
      liveKeys.add(key);
      const existing = prevByKey.get(key);
      if (existing) {
        if (existing.exiting) {
          // Re-appeared while exiting: cancel the scheduled removal.
          const t = timers.current.get(key);
          if (t !== undefined) {
            clearTimeout(t);
            timers.current.delete(key);
          }
        }
        next.push({ key, item, exiting: false });
        if (existing.item !== item || existing.exiting) changed = true;
      } else {
        next.push({ key, item, exiting: false });
        changed = true;
      }
    }

    // Live-key order changed with no add/remove? That counts too.
    if (!changed) {
      const prevLive = prev.filter((r) => liveKeys.has(r.key));
      changed =
        prevLive.length !== next.length ||
        prevLive.some((r, i) => r.key !== next[i].key);
    }

    // Removed items: schedule their unmount, mark them exiting in place.
    for (const r of prev) {
      if (liveKeys.has(r.key)) continue;
      if (r.exiting) {
        next.push(r);
        continue;
      }
      const key = r.key;
      const t = setTimeout(() => {
        timers.current.delete(key);
        setRows((cur) =>
          cur.some((x) => x.key === key && x.exiting)
            ? cur.filter((x) => x.key !== key)
            : cur
        );
      }, exitMs);
      timers.current.set(key, t);
      next.push({ ...r, exiting: true });
      changed = true;
    }

    if (changed) {
      rowsRef.current = next;
      setRows(next);
    }
  }, [items, keyOf, exitMs]);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((t) => clearTimeout(t));
      pending.clear();
    };
  }, []);

  return rows;
}
