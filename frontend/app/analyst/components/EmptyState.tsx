"use client";

/**
 * AEGIS AI — Analyst dashboard empty state (Phase 11, W3).
 * Always carries the honest coverage message from the API; never filler.
 */

export function AnalystEmptyState({
  title,
  message,
}: {
  title: string;
  message: string;
}) {
  return (
    <div className="rounded-xl border border-dashed border-white/15 bg-white/[0.02] p-6 text-center">
      <div className="font-mono text-sm font-semibold uppercase tracking-widest text-cyan-300/80">
        {title}
      </div>
      <p className="mx-auto mt-2 max-w-md text-sm text-white/50">{message}</p>
    </div>
  );
}
