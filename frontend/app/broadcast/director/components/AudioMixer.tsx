"use client";

/**
 * AEGIS AI — audio mixer panel for the director console (W3).
 *
 * Controls the broadcast audio pipeline:
 *   - commentary volume slider (software gain on the TTS PCM feed)
 *   - game audio volume slider (gain hint applied in the FFmpeg mix)
 *   - master mute toggle
 *   - TTS engine status (Piper offline / disabled)
 *   - commentary feed start (PCM FIFO that FFmpeg picks up)
 *   - game-audio capture start/stop (scrcpy route or manual cable)
 *   - test commentary line -> synthesized + queued to the feed
 *
 * Every value comes from GET /api/v1/audio/status; failed calls surface
 * the backend's message honestly — no state is invented client-side.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Card, ErrorState } from "@/components/ui";
import {
  getAudioStatus,
  postCommentaryLine,
  postMixerLevels,
  startCommentaryFeed,
  startGameAudio,
  stopGameAudio,
  type AudioStatus,
} from "@/lib/audio";

function actionErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Request failed.";
}

const VOICE_STYLES = ["normal", "excited", "calm", "dramatic", "shout"] as const;

export function AudioMixer({ token }: { token: string | null }) {
  const [status, setStatus] = useState<AudioStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testLine, setTestLine] = useState("");
  const [testStyle, setTestStyle] = useState<string>("excited");
  const [testResult, setTestResult] = useState<string | null>(null);
  const postTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await getAudioStatus(token);
      setStatus(s);
      setError(null);
    } catch (e) {
      setError(actionErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => clearInterval(timer);
  }, [refresh]);

  /** Debounced mixer write so slider drags don't hammer the API. */
  const pushLevels = useCallback(
    (levels: { commentary_volume?: number; game_volume?: number; master_mute?: boolean }) => {
      if (postTimer.current) clearTimeout(postTimer.current);
      postTimer.current = setTimeout(async () => {
        try {
          const next = await postMixerLevels(token, levels);
          setStatus((s) => (s ? { ...s, mixer: next } : s));
        } catch (e) {
          setError(actionErrorMessage(e));
        }
      }, 350);
    },
    [token]
  );

  const onSlider = (key: "commentary_volume" | "game_volume", value: number) => {
    setStatus((s) =>
      s ? { ...s, mixer: { ...s.mixer, [key]: value } } : s
    );
    pushLevels({ [key]: value });
  };

  const onMute = (muted: boolean) => {
    setStatus((s) => (s ? { ...s, mixer: { ...s.mixer, master_mute: muted } } : s));
    pushLevels({ master_mute: muted });
  };

  const doAction = useCallback(
    async (label: string, fn: () => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await fn();
        await refresh();
      } catch (e) {
        setError(`${label}: ${actionErrorMessage(e)}`);
      } finally {
        setBusy(false);
      }
    },
    [refresh]
  );

  const speakTest = async () => {
    const text = testLine.trim();
    if (!text) return;
    setBusy(true);
    setTestResult(null);
    setError(null);
    try {
      const res = await postCommentaryLine(token, text, testStyle);
      setTestResult(
        res.synthesized
          ? `Queued ✓ — clip ${res.clip_id} (${res.duration_seconds?.toFixed(1)}s)${
              res.feed_running ? ", on the live feed" : ", feed not running"
            }.`
          : `Not synthesized: ${res.reason ?? "unknown reason"}`
      );
      await refresh();
    } catch (e) {
      setError(actionErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const mixer = status?.mixer;
  const tts = status?.tts;
  const feed = status?.feed;
  const game = status?.game_audio;

  return (
    <Card title="Audio mixer">
      {loading && <p className="text-sm text-white/50">Loading audio state…</p>}
      {error && (
        <div className="mb-3">
          <ErrorState message={error} onRetry={() => setError(null)} />
        </div>
      )}
      {status && mixer && tts && feed && game && (
        <div className="space-y-4">
          {/* engine + feed status row */}
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={tts.synthesis_enabled ? "green" : "slate"}>
              TTS {tts.synthesis_enabled ? `on (${tts.tts_engine})` : "off — no engine"}
            </Badge>
            <Badge tone={feed.feed_running ? "green" : "slate"}>
              feed {feed.feed_running ? "live" : "stopped"}
            </Badge>
            <Badge tone={game.recording ? "green" : game.route === "none" ? "slate" : "cyan"}>
              game audio {game.recording ? `recording (${game.route})` : game.route === "none" ? "no hardware" : `route: ${game.route}`}
            </Badge>
          </div>
          {!tts.synthesis_enabled && (
            <p className="text-xs text-amber-300/80">
              Commentary voice is disabled: set AEGIS_TTS_ENGINE=piper and
              AEGIS_TTS_MODEL_PATH on the backend (see docs/AUDIO.md). The
              broadcast stays silent rather than faking audio.
            </p>
          )}

          {/* volume sliders */}
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 flex items-center justify-between text-sm text-white/70">
                Commentary volume
                <span className="font-mono text-white/90">
                  {Math.round(mixer.commentary_volume * 100)}%
                </span>
              </span>
              <input
                type="range"
                min={0}
                max={150}
                value={Math.round(mixer.commentary_volume * 100)}
                disabled={busy}
                onChange={(e) => onSlider("commentary_volume", Number(e.target.value) / 100)}
                className="w-full accent-cyan-400"
              />
            </label>
            <label className="block">
              <span className="mb-1 flex items-center justify-between text-sm text-white/70">
                Game audio volume
                <span className="font-mono text-white/90">
                  {Math.round(mixer.game_volume * 100)}%
                </span>
              </span>
              <input
                type="range"
                min={0}
                max={150}
                value={Math.round(mixer.game_volume * 100)}
                disabled={busy}
                onChange={(e) => onSlider("game_volume", Number(e.target.value) / 100)}
                className="w-full accent-cyan-400"
              />
            </label>
          </div>

          {/* mute + feed/game controls */}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={busy}
              onClick={() => onMute(!mixer.master_mute)}
              className={`rounded-lg px-4 py-2 text-sm font-semibold transition ${
                mixer.master_mute
                  ? "bg-red-500/80 text-white hover:bg-red-500"
                  : "bg-white/10 text-white/80 hover:bg-white/20"
              }`}
            >
              {mixer.master_mute ? "🔇 Unmute master" : "🔊 Mute master"}
            </button>
            {!feed.feed_running && (
              <button
                type="button"
                disabled={busy}
                onClick={() => doAction("Start feed", () => startCommentaryFeed(token))}
                className="rounded-lg bg-cyan-500/80 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"
              >
                ▶ Start commentary feed
              </button>
            )}
            {!game.recording && game.scrcpy_route_possible && (
              <button
                type="button"
                disabled={busy}
                onClick={() => doAction("Start game audio", () => startGameAudio(token))}
                className="rounded-lg bg-emerald-500/80 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                🎮 Capture game audio
              </button>
            )}
            {game.recording && (
              <button
                type="button"
                disabled={busy}
                onClick={() => doAction("Stop game audio", () => stopGameAudio(token))}
                className="rounded-lg bg-white/10 px-4 py-2 text-sm font-semibold text-white/80 hover:bg-white/20 disabled:opacity-50"
              >
                ■ Stop game audio
              </button>
            )}
          </div>
          {game.notes.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-xs text-white/50">
              {game.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}

          {/* test commentary line */}
          <div className="rounded-lg bg-black/30 p-3">
            <p className="mb-2 text-sm font-semibold text-white/80">Test commentary line</p>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                type="text"
                value={testLine}
                disabled={busy}
                onChange={(e) => setTestLine(e.target.value)}
                placeholder="e.g. Kya baat hai! Chicken dinner!"
                className="min-w-0 flex-1 rounded-lg bg-white/10 px-3 py-2 text-sm text-white placeholder:text-white/30 focus:outline-none focus:ring-1 focus:ring-cyan-400"
              />
              <select
                value={testStyle}
                disabled={busy}
                onChange={(e) => setTestStyle(e.target.value)}
                className="rounded-lg bg-white/10 px-3 py-2 text-sm text-white focus:outline-none"
              >
                {VOICE_STYLES.map((s) => (
                  <option key={s} value={s} className="bg-zinc-900">
                    {s}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={busy || !testLine.trim()}
                onClick={speakTest}
                className="rounded-lg bg-cyan-500/80 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"
              >
                🎙 Speak
              </button>
            </div>
            {testResult && (
              <p className="mt-2 text-xs text-white/60">{testResult}</p>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
