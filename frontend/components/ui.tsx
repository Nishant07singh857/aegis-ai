"use client";

/**
 * AEGIS AI — shared UI primitives. All Tailwind, matching the dark
 * esports-console theme (see app/globals.css). Text/SVG only — no emojis.
 */

import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Card({ title, action, children, className = "" }: CardProps) {
  return (
    <section
      className={`bg-white/5 border border-white/10 rounded-xl p-4 sm:p-5 ${className}`}
    >
      {(title || action) && (
        <div className="flex items-center justify-between gap-3 mb-3">
          {title && (
            <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-zinc-400">
              {title}
            </h2>
          )}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

type BadgeTone = "cyan" | "red" | "amber" | "green" | "slate";

const BADGE_STYLES: Record<BadgeTone, string> = {
  cyan: "text-[#00f0ff] border-[#00f0ff]/40 bg-[#00f0ff]/10",
  red: "text-[#ff3366] border-[#ff3366]/40 bg-[#ff3366]/10",
  amber: "text-amber-300 border-amber-300/40 bg-amber-300/10",
  green: "text-emerald-300 border-emerald-300/40 bg-emerald-300/10",
  slate: "text-slate-300 border-white/15 bg-white/5",
};

export function Badge({
  tone = "slate",
  children,
}: {
  tone?: BadgeTone;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider ${BADGE_STYLES[tone]}`}
    >
      {children}
    </span>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div
      className="flex items-center gap-3 text-zinc-400 py-8"
      role="status"
      aria-live="polite"
    >
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/20 border-t-[#00f0ff]" />
      <span className="font-mono text-sm">{label}…</span>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="rounded-xl border border-[#ff3366]/30 bg-[#ff3366]/5 p-5">
      <p className="font-mono text-xs uppercase tracking-[0.2em] text-[#ff3366] mb-2">
        Error
      </p>
      <p className="text-sm text-zinc-300">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-lg border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs uppercase tracking-wider text-zinc-200 hover:border-[#00f0ff]/50 hover:text-[#00f0ff] transition-colors max-md:min-h-[44px]"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-white/15 bg-white/[0.02] p-8 text-center">
      <p className="font-mono text-sm text-zinc-500">{message}</p>
    </div>
  );
}
