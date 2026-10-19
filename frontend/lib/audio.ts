/**
 * AEGIS AI — broadcast audio pipeline client (W3).
 *
 * Backend contract (GET/POST /api/v1/audio/*):
 *   GET  /api/v1/audio/status
 *     -> {"tts": {"synthesis_enabled": bool, "tts_engine": str, ...},
 *         "mixer": {"commentary_volume": 0..1.5, "game_volume": 0..1.5,
 *                   "master_mute": bool, "updated_at": float},
 *         "feed": {"feed_running": bool, "fifo_path": str, ...},
 *         "game_audio": {"route": "none"|"scrcpy"|"manual",
 *                        "recording": bool, "notes": [...], ...}}
 *   POST /api/v1/audio/commentary   {"text": str, "profile_id"?: str, "style"?: str}
 *     -> 202 {"queued": bool, "synthesized": bool, "clip_id"?: str, ...}
 *   GET  /api/v1/audio/clips?limit=N
 *   GET  /api/v1/audio/mixer            -> mixer state
 *   POST /api/v1/audio/mixer           {"commentary_volume"?: 0..1.5,
 *                                       "game_volume"?: 0..1.5,
 *                                       "master_mute"?: bool}
 *   POST /api/v1/audio/feed/start | /api/v1/audio/feed/stop
 *   GET  /api/v1/audio/game/status
 *   POST /api/v1/audio/game/start | /api/v1/audio/game/stop
 *
 * Reads need any authenticated user; writes need operator+. No mock
 * data: every value comes from the real backend; failures surface the
 * backend's message honestly.
 */

import { apiFetch } from "./api";

export interface MixerState {
  commentary_volume: number;
  game_volume: number;
  master_mute: boolean;
  updated_at: number;
}

export interface TtsStatus {
  synthesis_enabled: boolean;
  tts_engine: string;
  active_profile?: string;
  total_synthesized?: number;
  avg_latency_ms?: number;
  error?: string;
}

export interface FeedStatus {
  feed_running: boolean;
  fifo_path: string;
  fifo_exists: boolean;
  queued_chunks: number;
  queued_seconds: number;
  clips_kept: number;
  ffmpeg_available: boolean;
  sample_format: string;
}

export interface GameAudioStatus {
  adb_installed: boolean;
  scrcpy_installed: boolean;
  scrcpy_version: string | null;
  adb_devices: Array<{ serial: string; detail: string }>;
  android_api: number | null;
  scrcpy_route_possible: boolean;
  alsa_capture_devices: string[];
  route: "none" | "scrcpy" | "manual";
  recording: boolean;
  record_path: string | null;
  last_error: string | null;
  notes: string[];
}

export interface AudioStatus {
  tts: TtsStatus;
  mixer: MixerState;
  feed: FeedStatus;
  game_audio: GameAudioStatus;
}

export interface CommentaryLineResult {
  queued: boolean;
  synthesized: boolean;
  clip_id?: string;
  duration_seconds?: number;
  feed_running?: boolean;
  reason?: string;
}

/** GET /api/v1/audio/status */
export async function getAudioStatus(
  token: string | null
): Promise<AudioStatus> {
  return apiFetch<AudioStatus>("/api/v1/audio/status", {
    token: token ?? undefined,
  });
}

/** POST /api/v1/audio/commentary — operator+ */
export async function postCommentaryLine(
  token: string | null,
  text: string,
  style?: string
): Promise<CommentaryLineResult> {
  return apiFetch<CommentaryLineResult>("/api/v1/audio/commentary", {
    method: "POST",
    token: token ?? undefined,
    body: { text, style: style ?? undefined },
  });
}

/** POST /api/v1/audio/mixer — operator+ */
export async function postMixerLevels(
  token: string | null,
  levels: {
    commentary_volume?: number;
    game_volume?: number;
    master_mute?: boolean;
  }
): Promise<MixerState> {
  return apiFetch<MixerState>("/api/v1/audio/mixer", {
    method: "POST",
    token: token ?? undefined,
    body: levels,
  });
}

/** POST /api/v1/audio/feed/start — operator+ */
export async function startCommentaryFeed(
  token: string | null
): Promise<{ running: boolean; fifo?: string }> {
  return apiFetch<{ running: boolean; fifo?: string }>(
    "/api/v1/audio/feed/start",
    { method: "POST", token: token ?? undefined }
  );
}

/** POST /api/v1/audio/game/start — operator+ (409 with honest reason when impossible) */
export async function startGameAudio(
  token: string | null
): Promise<{ recording: boolean; route: string; note?: string }> {
  return apiFetch<{ recording: boolean; route: string; note?: string }>(
    "/api/v1/audio/game/start",
    { method: "POST", token: token ?? undefined }
  );
}

/** POST /api/v1/audio/game/stop — operator+ */
export async function stopGameAudio(
  token: string | null
): Promise<{ recording: boolean; route: string }> {
  return apiFetch<{ recording: boolean; route: string }>(
    "/api/v1/audio/game/stop",
    { method: "POST", token: token ?? undefined }
  );
}
