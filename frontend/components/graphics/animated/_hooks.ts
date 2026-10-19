/**
 * AEGIS AI — shared hooks for the animated story graphics (Phase 19, W2).
 *
 * useCountUp: JS-driven number count-up (percentages, finishes,
 * damage…). Uses requestAnimationFrame with an ease-out curve; when the
 * OS asks for reduced motion the value snaps to the target immediately.
 * transform/opacity-only CSS cannot animate numbers, so this is the one
 * place where JS owns a visual animation.
 */

'use client';

import { useEffect, useRef, useState } from 'react';

const EASE_OUT = (p: number) => 1 - Math.pow(1 - p, 3);

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/**
 * Animate `target` → displayed value over `durationMs`, starting from the
 * previously displayed value so 30s win-probability updates glide
 * instead of jumping. Null/undefined target = "—" (honest empty).
 */
export function useCountUp(
  target: number | null | undefined,
  durationMs = 1000
): number | null {
  const [value, setValue] = useState<number | null>(
    target == null ? null : 0
  );
  const prev = useRef<number>(0);
  const reduced = useRef(false);

  useEffect(() => {
    reduced.current = prefersReducedMotion();
  }, []);

  useEffect(() => {
    if (target == null) {
      setValue(null);
      return;
    }
    if (reduced.current || durationMs <= 0) {
      prev.current = target;
      setValue(target);
      return;
    }
    const from = prev.current;
    const to = target;
    if (from === to) {
      setValue(to);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / durationMs);
      const v = from + (to - from) * EASE_OUT(p);
      prev.current = v;
      setValue(v);
      if (p < 1) raf = requestAnimationFrame(tick);
      else prev.current = to;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);

  return value;
}

/**
 * Format a counted-up value, or "—" when the feed delivered nothing.
 */
export function fmtCounted(
  value: number | null,
  format: (n: number) => string = (n) => String(Math.round(n))
): string {
  return value == null ? '—' : format(value);
}

/** Enter → exit → gone orchestration for auto-dismissing graphics. */
export type DismissPhase = 'enter' | 'exit' | 'gone';

/**
 * Starts the `exit` phase `dismissAfterMs` after mount (0/undefined =
 * never auto-dismisses), then `gone` `exitMs` later so the exit CSS can
 * play. Returns a `dismiss()` that starts the exit immediately.
 */
export function useAutoDismiss(
  dismissAfterMs: number | undefined,
  exitMs = 380
): { phase: DismissPhase; dismiss: () => void } {
  const [phase, setPhase] = useState<DismissPhase>('enter');
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  useEffect(() => {
    if (!dismissAfterMs || dismissAfterMs <= 0) return;
    const enter = setTimeout(() => {
      if (phaseRef.current === 'enter') setPhase('exit');
    }, dismissAfterMs);
    return () => clearTimeout(enter);
  }, [dismissAfterMs]);

  useEffect(() => {
    if (phase !== 'exit') return;
    const gone = setTimeout(() => setPhase('gone'), exitMs);
    return () => clearTimeout(gone);
  }, [phase, exitMs]);

  const dismiss = () => {
    if (phaseRef.current === 'enter') setPhase('exit');
  };

  return { phase, dismiss };
}
