'use client';

import type { ConnStatus } from '../../lib/useLiveSocket';

interface Props {
  status: ConnStatus;
  retryCount: number;
  onReconnect: () => void;
}

const TONE: Record<ConnStatus, { dot: string; label: string; text: string }> = {
  connecting: { dot: 'bg-cyan-400', label: 'CONNECTING', text: 'text-cyan-300' },
  live: { dot: 'bg-emerald-400 animate-pulse', label: 'LIVE', text: 'text-emerald-300' },
  reconnecting: { dot: 'bg-amber-400 animate-pulse', label: 'RECONNECTING', text: 'text-amber-300' },
  offline: { dot: 'bg-red-500', label: 'OFFLINE', text: 'text-red-400' },
};

export default function ConnectionBanner({ status, retryCount, onReconnect }: Props) {
  const tone = TONE[status];
  return (
    <div className="sticky top-0 z-40 border-b border-white/10 bg-[#0a0a0f]/95 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <span className={`h-2.5 w-2.5 rounded-full ${tone.dot}`} aria-hidden="true" />
          <span className={`text-sm font-semibold tracking-widest ${tone.text}`}>{tone.label}</span>
          {status === 'reconnecting' && (
            <span className="text-xs text-slate-400">attempt {retryCount} — retrying…</span>
          )}
          {status === 'offline' && (
            <span className="text-xs text-slate-400">connection lost after {retryCount} attempts</span>
          )}
        </div>
        {(status === 'offline' || status === 'reconnecting') && (
          <button
            type="button"
            onClick={onReconnect}
            className="rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-white transition hover:border-[#00f0ff]/60 hover:text-[#00f0ff]"
          >
            Reconnect
          </button>
        )}
      </div>
    </div>
  );
}
