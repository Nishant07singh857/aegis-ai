/**
 * AEGIS AI — typed HTTP client for the backend REST API.
 *
 * Contract used by all frontend pages:
 *   import { apiFetch, API_BASE } from '@/lib/api';
 *   const teams = await apiFetch<Team[]>('/api/v1/teams', { token });
 *
 * No mock data lives here — every call hits the real backend.
 */

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`API ${status}: ${body}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

interface ApiFetchOptions {
  method?: string;
  body?: unknown;
  token?: string | null;
}

/** Best-effort extraction of an error message from an error JSON payload. */
function extractErrorMessage(payload: unknown, fallback: string): string {
  if (payload !== null && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    for (const key of ["error", "detail", "message"]) {
      const value = record[key];
      if (typeof value === "string" && value.length > 0) {
        return value;
      }
    }
  }
  return fallback;
}

export async function apiFetch<T>(
  path: string,
  opts?: ApiFetchOptions
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (opts?.token) {
    headers["Authorization"] = `Bearer ${opts.token}`;
  }
  const response = await fetch(`${API_BASE}${path}`, {
    method: opts?.method ?? "GET",
    headers,
    body: opts?.body === undefined ? undefined : JSON.stringify(opts.body),
  });

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const data: unknown = await response.json();
      message = extractErrorMessage(data, message);
    } catch {
      // Non-JSON error body: fall back to raw text.
      try {
        const text = await response.text();
        if (text) message = text;
      } catch {
        // Leave the fallback message.
      }
    }
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  const text = await response.text();
  return (text ? (JSON.parse(text) as T) : (undefined as T));
}

export { API_BASE };
