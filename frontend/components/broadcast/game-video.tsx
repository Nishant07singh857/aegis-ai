'use client';

/**
 * AEGIS AI — broadcast video layer (Phase 17 W2).
 *
 * Route usage: rendered at the back of DirectorOutputView
 * (/broadcast/output OBS capture source and /watch viewer page).
 *
 * Displays the active observer's game feed as an MJPEG <img> stream from
 * GET /api/v1/video/feed/{slot} (multipart/x-mixed-replace; no WebRTC).
 * The active camera slot comes from the director state (active_camera,
 * kept in sync with the W4 capture manager's on-air selection).
 *
 * Modes:
 *   - "real" (default): the live game feed. When the slot has no feed,
 *     the backend streams an honestly-labelled "NO FEED" slate, and when
 *     the stream itself fails (backend down, capture never started) this
 *     component renders its own "NO FEED — connect observer device" slate.
 *     Nothing here ever fakes game footage.
 *   - "simulator": ?video=sim forces a watermarked test pattern
 *     (backend ?sim=1); a big "SIMULATED FEED" badge is also overlaid so
 *     no frame can be mistaken for a real game.
 *
 * Graphics overlays (scorebug, killfeed, standings, lower-thirds) render
 * ABOVE this layer — it is z-0 and comes first in DOM order inside
 * DirectorOutputView.
 */

import { useEffect, useState } from 'react';

export type VideoLayerMode = 'real' | 'simulator';

export interface GameVideoProps {
  /** Resolved backend API base (may come from a ?api= override). */
  api: string;
  /** Active observer camera slot (1..16) from the director state. */
  slot: number;
  /** "real" or "simulator" (output page ?video=sim). */
  mode?: VideoLayerMode;
}

type FeedState = 'connecting' | 'live' | 'error';

export function GameVideo({ api, slot, mode = 'real' }: GameVideoProps) {
  const [feedState, setFeedState] = useState<FeedState>('connecting');
  const simulator = mode === 'simulator';

  const base = api.replace(/\/+$/, '');
  // The MJPEG endpoint is intentionally unauthenticated (same trust model
  // as /ws/live): the output page is captured by an unauthenticated
  // headless Chromium. No token can be attached to an <img> anyway.
  const src = `${base}/api/v1/video/feed/${slot}${simulator ? '?sim=1' : ''}`;

  // Reset the load state whenever the camera/mode changes so a stale
  // "error" or "live" does not survive a slot switch.
  useEffect(() => {
    setFeedState('connecting');
  }, [slot, mode, api]);

  const showSlate = feedState === 'error';

  return (
    <div
      className="absolute inset-0 z-0 overflow-hidden bg-black"
      aria-label={
        simulator
          ? `Simulated camera feed, slot ${slot}`
          : `Observer camera feed, slot ${slot}`
      }
    >
      {!showSlate ? (
        <img
          key={`${slot}-${mode}`}
          src={src}
          alt=""
          draggable={false}
          className="h-full w-full object-cover"
          onLoad={() => setFeedState('live')}
          onError={() => setFeedState('error')}
        />
      ) : (
        /* Honest no-feed slate: the stream itself failed (backend down or
           the capture service never started). Never fake footage. */
        <div className="flex h-full w-full flex-col items-center justify-center bg-[#0b0d14] px-8 text-center">
          <div className="font-mono text-5xl font-extrabold tracking-[0.3em] text-[#ff5050]">
            NO FEED
          </div>
          <div className="mt-4 font-mono text-sm tracking-[0.2em] text-white/70">
            CAM {slot} — CONNECT OBSERVER DEVICE
          </div>
          <div className="mt-2 max-w-xl font-mono text-xs leading-relaxed text-white/40">
            The game video stream is unreachable. Start capture on tournament
            day (POST /api/v1/video/capture/start) or enable the simulator
            with ?video=sim.
          </div>
        </div>
      )}

      {/* connecting shimmer: only over a black frame, never over content */}
      {feedState === 'connecting' && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/60">
          <div className="animate-pulse font-mono text-xs tracking-[0.4em] text-white/50">
            CONNECTING CAM {slot}…
          </div>
        </div>
      )}

      {/* camera identity: the Bible top bar has no camera chip and the
          bottom-left is the facecam zone, so the slot is surfaced by the
          facecam label above instead of a chip here. */}

      {/* simulator watermark: always visible, unmistakable. Sits below
          the Bible top bar (y 0-4%) so it never covers match info. */}
      {simulator && (
        <div className="pointer-events-none absolute inset-x-0 top-[5%] flex justify-center">
          <div className="rounded border-2 border-[#00f0ff]/80 bg-black/70 px-6 py-2 font-mono text-xl font-extrabold tracking-[0.35em] text-[#00f0ff]">
            SIMULATED FEED
          </div>
        </div>
      )}
    </div>
  );
}
