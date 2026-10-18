'use client';

/**
 * AEGIS AI — shared commentary-line hook (Phase 8).
 *
 * Extracted from DirectorOutputView's module-local hook so the overlay
 * pages, the output view and the graphics gallery all read the same live
 * source: GET /api/v1/commentary/latest (polled every 3s). The commentary
 * stream is not exposed by useLiveSocket, so HTTP polling is the honest
 * source. Returns the latest line, or null until the feed says something —
 * never invented content.
 */

import { useEffect, useRef, useState } from 'react';

export interface CommentaryLine {
  timestamp: number;
  text: string;
  type: string;
}

const POLL_MS = 3_000;

/** Latest AI commentary line; null until the feed delivers one. */
export function useLatestCommentary(api: string): CommentaryLine | null {
  const [line, setLine] = useState<CommentaryLine | null>(null);
  const seenTs = useRef<number>(-1);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${api}/api/v1/commentary/latest`);
        if (!res.ok) return;
        const body = (await res.json()) as { data?: CommentaryLine[] };
        const log = Array.isArray(body.data) ? body.data : [];
        const latest = log[log.length - 1];
        if (
          !cancelled &&
          latest &&
          typeof latest.text === 'string' &&
          typeof latest.timestamp === 'number' &&
          latest.timestamp > seenTs.current
        ) {
          seenTs.current = latest.timestamp;
          setLine(latest);
        }
      } catch {
        /* commentary endpoint unreachable — keep last line */
      }
    };
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [api]);

  return line;
}
