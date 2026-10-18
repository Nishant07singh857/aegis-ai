"use client";

/**
 * AEGIS AI — Analyst dashboard stat card (Phase 11, W3).
 * Dark panel, mono data value, muted label. Local to /analyst.
 */

export function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-4">
      <div className="text-[11px] font-medium uppercase tracking-widest text-white/40">
        {label}
      </div>
      <div className="mt-2 font-mono text-2xl font-semibold text-white">
        {value}
      </div>
      {sub ? (
        <div className="mt-1 text-xs text-white/40">{sub}</div>
      ) : null}
    </div>
  );
}
