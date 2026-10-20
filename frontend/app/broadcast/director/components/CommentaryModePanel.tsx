"use client";

/**
 * AEGIS AI — commentary mode panel (Phase 22 / M13).
 *
 * Director control for the 5 PRD commentary modes. Each mode has real
 * backend behaviour (see backend/app/audio/commentary_modes.py):
 *   Hindi Main / English Main — single voice, mode language + pacing
 *   Bilingual Blend — alternating voices + dual-track (hi+en) rendering
 *   Silent — no AI audio; text-only lower-thirds + captions
 *   Human Assist — AI writes text suggestions, the human caster speaks
 *
 * In Human Assist the pending suggestions are listed here for the caster.
 * All state comes from GET /api/v1/audio/commentary-mode — nothing is
 * invented client-side.
 */

import { useCallback, useEffect, useState } from "react";
import { Badge, Card, ErrorState } from "@/components/ui";
import {
  getCasterSuggestions,
  getCommentaryMode,
  setCommentaryMode,
  type CasterSuggestion,
  type CommentaryModeId,
  type CommentaryModeState,
} from "@/lib/audio";

const MODE_DESCRIPTIONS: Record<CommentaryModeId, string> = {
  hindi_main: "Hindi voice (hi_IN-pratham), hype-leaning, 3s pacing",
  english_main: "English voice (en_US-amy), analysis-leaning, 4s pacing",
  bilingual_blend: "Alternating Hindi/English + dual-track recording",
  silent: "AI muted — text-only lower-thirds and captions",
  human_assist: "AI suggests lines below; the human caster speaks",
};

export function CommentaryModePanel({ token }: { token: string | null }) {
  const [state, setState] = useState<CommentaryModeState | null>(null);
  const [suggestions, setSuggestions] = useState<CasterSuggestion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await getCommentaryMode(token);
      setState(s);
      setError(null);
      if (s.mode === "human_assist") {
        const res = await getCasterSuggestions(token);
        setSuggestions(res.suggestions);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    }
  }, [token]);

  useEffect(() => {
    void refresh();
    const t = setInterval(refresh, 8000);
    return () => clearInterval(t);
  }, [refresh]);

  const choose = async (mode: CommentaryModeId) => {
    setBusy(true);
    try {
      const s = await setCommentaryMode(token, mode);
      setState(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Commentary mode">
      {error && (
        <div className="mb-3">
          <ErrorState message={error} onRetry={() => setError(null)} />
        </div>
      )}
      {!state && !error && (
        <p className="text-sm text-white/50">Loading commentary mode…</p>
      )}
      {state && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {state.all_modes.map((m) => {
              const active = state.mode === m.mode;
              return (
                <button
                  key={m.mode}
                  type="button"
                  disabled={busy || active}
                  onClick={() => void choose(m.mode)}
                  aria-pressed={active}
                  className={`rounded-lg border px-3 py-2.5 text-sm font-semibold transition ${
                    active
                      ? "border-cyan-400 bg-cyan-400/15 text-cyan-200"
                      : "border-white/10 bg-white/5 text-white/60 hover:border-white/25 hover:text-white"
                  } disabled:opacity-60`}
                >
                  {m.label}
                </button>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={state.speaks ? "green" : "slate"}>
              {state.speaks ? "AI speaking" : "AI not speaking"}
            </Badge>
            {state.dual_track && <Badge tone="cyan">dual-track hi+en</Badge>}
            {state.suggestions_only && <Badge tone="amber">suggestions only</Badge>}
            <span className="text-xs text-white/50">
              {MODE_DESCRIPTIONS[state.mode]}
            </span>
          </div>
          {state.mode === "human_assist" && (
            <div className="rounded-lg bg-black/30 p-3">
              <p className="mb-2 text-sm font-semibold text-white/80">
                Caster suggestions (read these aloud — AI stays silent)
              </p>
              {suggestions.length === 0 ? (
                <p className="text-xs text-white/45">
                  No suggestions yet — they appear as match events generate
                  commentary lines.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {suggestions.slice(0, 8).map((s, i) => (
                    <li key={i} className="text-sm text-white/85">
                      • {s.text}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
