"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "../lib/api";
import { useAuth } from "../lib/auth";
import { RequireRole } from "../lib/guard";
import { Badge, Card, EmptyState, ErrorState, Loading } from "../components/ui";

interface CommentaryLine {
  timestamp?: string | null;
  text?: string | null;
  type?: string | null;
}

interface Highlight {
  [k: string]: unknown;
}

interface ApiErrorLike {
  status?: number;
  body?: unknown;
}

function apiErrorMessage(e: unknown): string {
  const err = e as ApiErrorLike;
  if (typeof err?.status === "number") return `Request failed with status ${err.status}`;
  return "Request failed";
}

function typeTone(type: string | null | undefined): "cyan" | "red" | "amber" | "green" | "slate" {
  const t = (type ?? "").toLowerCase();
  if (t.includes("kill") || t.includes("fight") || t.includes("wipe")) return "red";
  if (t.includes("zone") || t.includes("warn")) return "amber";
  if (t.includes("wwcd") || t.includes("win")) return "green";
  return "cyan";
}

function formatTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (!Number.isNaN(d.getTime())) return d.toLocaleTimeString();
  return ts;
}

function CommentaryPageInner() {
  const { token } = useAuth();
  const [lines, setLines] = useState<CommentaryLine[] | null>(null);
  const [linesError, setLinesError] = useState<string | null>(null);
  const [highlights, setHighlights] = useState<Highlight[] | null>(null);
  const [highlightsError, setHighlightsError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch<{ data: CommentaryLine[] }>("/api/v1/commentary/latest", {
        token: token ?? undefined,
      });
      setLines(res.data ?? []);
      setLinesError(null);
    } catch (e) {
      setLinesError(apiErrorMessage(e));
    }
    try {
      const res = await apiFetch<{ data: Highlight[] }>("/api/v1/highlights", {
        token: token ?? undefined,
      });
      setHighlights(res.data ?? []);
      setHighlightsError(null);
    } catch (e) {
      setHighlightsError(apiErrorMessage(e));
    }
  }, [token]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 10_000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto max-w-4xl px-4 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">Commentary</h1>
            <p className="text-sm text-white/50">Live lines and highlights · refreshes every 10s</p>
          </div>
          <Link href="/" className="text-sm text-white/60 hover:text-[#00f0ff]">
            ← Dashboard
          </Link>
        </div>

        <Card title="Latest commentary">
          {!lines && !linesError && <Loading label="Loading commentary…" />}
          {linesError && <ErrorState message={linesError} onRetry={() => void load()} />}
          {lines && lines.length === 0 && (
            <EmptyState message="No commentary yet — lines appear as the match runs." />
          )}
          {lines && lines.length > 0 && (
            <ul className="flex flex-col gap-3">
              {lines.map((line, i) => (
                <li
                  key={`${line.timestamp ?? i}-${i}`}
                  className="flex items-start gap-3 rounded-lg bg-white/5 px-4 py-3"
                >
                  <span className="mt-0.5 shrink-0 text-xs text-white/40">
                    {formatTime(line.timestamp)}
                  </span>
                  <p className="flex-1 text-sm text-white/85">{line.text ?? "—"}</p>
                  <Badge tone={typeTone(line.type)}>{line.type ?? "info"}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Highlights">
          {!highlights && !highlightsError && <Loading label="Loading highlights…" />}
          {highlightsError && <ErrorState message={highlightsError} onRetry={() => void load()} />}
          {highlights && <EmptyState message="No highlights yet." />}
        </Card>
      </div>
    </div>
  );
}

/** Route guard: operator + admin. Viewers land on /watch. */
export default function CommentaryPage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <CommentaryPageInner />
    </RequireRole>
  );
}
