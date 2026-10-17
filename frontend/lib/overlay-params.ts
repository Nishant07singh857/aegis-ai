'use client';

/**
 * AEGIS AI — overlay query-param plumbing.
 *
 * Every overlay route honours the same query params (documented in each
 * page's header comment):
 *
 *   ?match=<id>     Match to display. Default "live". The backend WS
 *                   (/ws/live) currently broadcasts the single live match feed,
 *                   so the value is used as a display label / reserved for a
 *                   future per-match subscription — no filtering is applied.
 *   ?accent=<hex>   Accent colour override, e.g. ?accent=%2300f0ff.
 *                   Must be #rgb or #rrggbb; anything else falls back to the
 *                   default cyan (#00f0ff).
 *   ?api=<url>      Override for the backend API base (defaults to
 *                   NEXT_PUBLIC_API_URL or http://localhost:8000). Also
 *                   re-points the live WebSocket derived from it.
 */

import { useSearchParams } from 'next/navigation';
import { API_BASE } from './api';

export interface OverlayParams {
  /** Match label; "live" unless ?match= names one. */
  match: string;
  /** Sanitised #rrggbb accent colour. */
  accent: string;
  /** Backend API base URL actually in use. */
  api: string;
}

export const DEFAULT_ACCENT = '#00f0ff';

/** Accept only #rgb / #rrggbb so a stray value can't break the layout. */
export function sanitizeAccent(raw: string | null): string {
  if (!raw) return DEFAULT_ACCENT;
  const v = raw.trim();
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v) ? v : DEFAULT_ACCENT;
}

/**
 * Read overlay params from the URL. Must be called inside a <Suspense>
 * boundary (every overlay page wraps its inner component in one).
 */
export function useOverlayParams(): OverlayParams {
  const sp = useSearchParams();
  const api = (sp.get('api') || '').trim().replace(/\/+$/, '') || API_BASE;
  const match = (sp.get('match') || '').trim() || 'live';
  return { match, accent: sanitizeAccent(sp.get('accent')), api };
}
