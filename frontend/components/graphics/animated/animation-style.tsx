/**
 * AEGIS AI — animation style context (Phase 19, W2).
 *
 * The graphics-section operator can change the animation style per
 * graphic: slide (default) | fade | scale | none. The style flows
 * through `AnimationStyleProvider` and components read it with
 * `useAnimationStyle()` — an explicit `animationStyle` prop on any
 * story component overrides the context for that graphic.
 *
 * All styles use transform / opacity only (GPU-accelerated, 60fps in
 * an OBS browser source). `prefers-reduced-motion` is respected both
 * in CSS (animated.css) and in the JS count-up hooks: with reduced
 * motion everything snaps to its final state.
 */

'use client';

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type AnimationStyle = 'slide' | 'fade' | 'scale' | 'none';

export const ANIMATION_STYLES: readonly AnimationStyle[] = [
  'slide',
  'fade',
  'scale',
  'none',
];

export const ANIMATION_STYLE_LABELS: Record<AnimationStyle, string> = {
  slide: 'Slide',
  fade: 'Fade',
  scale: 'Scale',
  none: 'None',
};

interface AnimationStyleContextValue {
  style: AnimationStyle;
  setStyle: (s: AnimationStyle) => void;
  /** Whether the OS asked for reduced motion — components snap instead. */
  reducedMotion: boolean;
  /**
   * Entrance class for a fresh element.
   * @param from 'left' | 'right' | 'up' — slide direction; ignored by
   *             the fade/scale/none styles.
   */
  enterClass: (from?: 'left' | 'right' | 'up') => string;
}

const AnimationStyleContext =
  createContext<AnimationStyleContextValue | null>(null);

export function AnimationStyleProvider({
  children,
  /** Controlled value; absent = internal state seeded by `initial`. */
  value,
  initial = 'slide',
  onChange,
}: {
  children: ReactNode;
  value?: AnimationStyle;
  initial?: AnimationStyle;
  onChange?: (s: AnimationStyle) => void;
}) {
  const [internal, setInternal] = useState<AnimationStyle>(initial);
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReducedMotion(mq.matches);
    const onChangeMq = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mq.addEventListener('change', onChangeMq);
    return () => mq.removeEventListener('change', onChangeMq);
  }, []);

  const style = value ?? internal;
  const setStyle = useMemo(
    () => (s: AnimationStyle) => {
      if (value === undefined) setInternal(s);
      onChange?.(s);
    },
    [value, onChange]
  );

  const enterClass = useMemo(
    () => (from: 'left' | 'right' | 'up' = 'up') => {
      if (reducedMotion) return 'ag-enter-none';
      switch (style) {
        case 'fade':
          return 'ag-enter-fade';
        case 'scale':
          return 'ag-enter-scale';
        case 'none':
          return 'ag-enter-none';
        case 'slide':
        default:
          return `ag-enter-slide ag-from-${from}`;
      }
    },
    [style, reducedMotion]
  );

  const ctx = useMemo<AnimationStyleContextValue>(
    () => ({ style, setStyle, reducedMotion, enterClass }),
    [style, setStyle, reducedMotion, enterClass]
  );

  return (
    <AnimationStyleContext.Provider value={ctx}>
      {children}
    </AnimationStyleContext.Provider>
  );
}

/**
 * Read the current animation style. Components prefer an explicit
 * `animationStyle` prop (per-graphic override) over the context value:
 *
 *   const { enterClass } = useAnimationStyle(props.animationStyle);
 */
export function useAnimationStyle(
  override?: AnimationStyle
): AnimationStyleContextValue {
  const ctx = useContext(AnimationStyleContext);
  const fallback = useMemo<AnimationStyleContextValue>(
    () => ({
      style: 'slide',
      setStyle: () => {},
      reducedMotion: false,
      enterClass: (from: 'left' | 'right' | 'up' = 'up') =>
        `ag-enter-slide ag-from-${from}`,
    }),
    []
  );

  const base = ctx ?? fallback;
  const style = override ?? base.style;

  return useMemo<AnimationStyleContextValue>(
    () => ({
      style,
      setStyle: base.setStyle,
      reducedMotion: base.reducedMotion,
      enterClass: (from: 'left' | 'right' | 'up' = 'up') => {
        if (base.reducedMotion) return 'ag-enter-none';
        switch (style) {
          case 'fade':
            return 'ag-enter-fade';
          case 'scale':
            return 'ag-enter-scale';
          case 'none':
            return 'ag-enter-none';
          case 'slide':
          default:
            return `ag-enter-slide ag-from-${from}`;
        }
      },
    }),
    [base, style]
  );
}
