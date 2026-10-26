"use client";

/**
 * AEGIS AI — viewer page (Phase 6).
 *
 * Route: /watch (all roles, including viewer).
 *
 * Embeds the director output layout (components/DirectorOutputView — the
 * same renderer /broadcast/output uses) with zero controls.
 *
 * LIVENESS LIMIT (Phase 32, K-C2 — comment only, do not re-wire):
 * GET /api/v1/stream/status now always reports state "deprecated"
 * (the API stream path is 410 Gone; the shell streamer is official —
 * see docs/STREAMING.md §0), so `streamIsRunning` below can never
 * return true and this page always shows the honest "stream offline"
 * standby, even while the shell streamer is actually running. Shell-
 * streamer liveness is unreported anywhere; a future out-of-band
 * health signal is needed — resurrecting the API status is NOT the
 * fix. No mock data: every value comes from the real status endpoint.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, API_BASE } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RequireRole } from "@/lib/guard";
import { DirectorOutputView } from "@/components/DirectorOutputView";
import { Loading } from "@/components/ui";

const STATUS_POLL_MS = 10_000;

interface RawStreamStatus {
  data?: RawStreamStatus;
  state?: unknown;
}

function streamIsRunning(raw: RawStreamStatus | null): boolean | null {
  if (!raw || typeof raw !== "object") return null;
  const r =
    raw.data && typeof raw.data === "object" ? raw.data : raw;
  if (typeof r.state !== "string") return null;
  return r.state.toLowerCase() === "running";
}

function WatchInner() {
  const router = useRouter();
  const { token, logout } = useAuth();
  // Phase 22: viewer captions toggle (WebVTT sidecar cues, rendered by
  // DirectorOutputView from /api/v1/audio/captions/current).
  const [captions, setCaptions] = useState(false);
  // null = not yet known; boolean = resolved.
  const [running, setRunning] = useState<boolean | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const raw = await apiFetch<RawStreamStatus>("/api/v1/stream/status", {
        token: token ?? undefined,
      });
      setRunning(streamIsRunning(raw) ?? false);
      setStatusError(null);
    } catch (e: unknown) {
      const err = e as { status?: number; message?: string };
      setRunning(false);
      setStatusError(err?.message ?? "Could not reach the stream status.");
    }
  }, [token]);

  useEffect(() => {
    void loadStatus();
    const t = setInterval(() => void loadStatus(), STATUS_POLL_MS);
    return () => clearInterval(t);
  }, [loadStatus]);

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <div className="relative h-dvh w-screen overflow-hidden bg-black text-white">
      {/* minimal overlay header — badge + sign out only, no stream controls */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 sm:gap-3 sm:px-5 sm:py-3">
        <span className="font-mono text-xs font-bold tracking-[0.2em] text-white/80 sm:text-sm sm:tracking-[0.3em]">
          AEGIS <span className="text-[#00f0ff]">AI</span>
        </span>
        <div className="pointer-events-auto flex items-center gap-2 sm:gap-3">
          {running === true ? (
            <span className="flex items-center gap-2 rounded-full border border-red-500/60 bg-red-500/15 px-3 py-1.5 font-mono text-xs font-bold tracking-[0.25em] text-red-400 sm:px-4">
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-red-500" />
              LIVE
            </span>
          ) : running === false ? (
            <span className="rounded-full border border-amber-300/40 bg-amber-300/10 px-3 py-1.5 font-mono text-xs font-bold tracking-[0.25em] text-amber-300 sm:px-4">
              STREAM OFFLINE
            </span>
          ) : (
            <span className="rounded-full border border-white/15 bg-white/5 px-3 py-1.5 font-mono text-xs tracking-[0.25em] text-white/40 sm:px-4">
              CHECKING…
            </span>
          )}
          <button
            type="button"
            onClick={() => setCaptions((c) => !c)}
            aria-pressed={captions}
            className={`pointer-events-auto rounded-lg border px-3 py-1.5 font-mono text-xs uppercase tracking-wider backdrop-blur transition-colors max-md:min-h-[44px] max-md:px-4 ${
              captions
                ? "border-[#00f0ff]/60 bg-[#00f0ff]/15 text-[#00f0ff]"
                : "border-white/15 bg-black/50 text-zinc-300 hover:border-white/35"
            }`}
          >
            CC {captions ? "On" : "Off"}
          </button>
          <button
            type="button"
            onClick={handleLogout}
            className="pointer-events-auto rounded-lg border border-white/15 bg-black/50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-zinc-300 backdrop-blur transition-colors hover:border-[#ff3366]/60 hover:text-[#ff3366] max-md:min-h-[44px] max-md:px-4"
          >
            Logout
          </button>
        </div>
      </div>

      {running === null ? (
        <div className="flex h-full items-center justify-center">
          <Loading label="Checking stream status" />
        </div>
      ) : running ? (
        <DirectorOutputView
          api={API_BASE}
          accent="#00f0ff"
          match="LIVE"
          ttl={12}
          hold={6}
          captions={captions}
        />
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
          <span className="rounded-full border border-amber-300/40 bg-amber-300/10 px-5 py-2 font-mono text-sm font-bold tracking-[0.3em] text-amber-300">
            STREAM OFFLINE
          </span>
          <p className="max-w-md text-sm text-white/55">
            The broadcast has not started yet. This page updates automatically
            when the stream goes live.
          </p>
          {statusError && (
            <p className="font-mono text-xs text-white/35">{statusError}</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function WatchPage() {
  return (
    <RequireRole roles={["viewer", "operator", "admin"]}>
      <WatchInner />
    </RequireRole>
  );
}
