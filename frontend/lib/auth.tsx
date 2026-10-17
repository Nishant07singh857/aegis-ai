"use client";

/**
 * AEGIS AI — operator authentication.
 *
 * The backend exposes a single operator login:
 *   POST /api/v1/auth/token  (application/x-www-form-urlencoded)
 *   -> 200 { access_token, token_type: "bearer" }
 *   -> 401 invalid credentials
 *   -> 503 password login not configured (ADMIN_PASSWORD unset)
 *
 * The JWT is stored in localStorage under 'aegis_token' and injected as
 * `Authorization: Bearer <token>` by lib/api.ts's apiFetch. It is also usable
 * as ?token= on the /ws/live control channel (browsers can't set WS headers).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import { API_BASE } from "./api";

const TOKEN_KEY = "aegis_token";

interface AuthContextValue {
  token: string | null;
  isAuthed: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

interface TokenResponse {
  access_token?: string;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    try {
      setToken(window.localStorage.getItem(TOKEN_KEY));
    } catch {
      setToken(null);
    }
  }, []);

  const login = useCallback(
    async (username: string, password: string): Promise<void> => {
      const body = new URLSearchParams({ username, password }).toString();
      const response = await fetch(`${API_BASE}/api/v1/auth/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
      });

      if (!response.ok) {
        if (response.status === 401) {
          throw new Error("Invalid username or password.");
        }
        if (response.status === 503) {
          throw new Error(
            "Password login is not configured on this server (set ADMIN_PASSWORD)."
          );
        }
        let message = "";
        try {
          const data = (await response.json()) as {
            error?: unknown;
            detail?: unknown;
            message?: unknown;
          };
          const raw =
            typeof data.error === "string"
              ? data.error
              : typeof data.detail === "string"
                ? data.detail
                : typeof data.message === "string"
                  ? data.message
                  : "";
          message = raw;
        } catch {
          // Non-JSON error body; fall through to the generic message.
        }
        throw new Error(message || `API ${response.status}`);
      }

      const data = (await response.json()) as TokenResponse;
      if (!data.access_token) {
        throw new Error("Login succeeded but the server returned no token.");
      }
      try {
        window.localStorage.setItem(TOKEN_KEY, data.access_token);
      } catch {
        // Storage unavailable (e.g. private mode) — keep the token in memory.
      }
      setToken(data.access_token);
    },
    []
  );

  const logout = useCallback((): void => {
    try {
      window.localStorage.removeItem(TOKEN_KEY);
    } catch {
      // Storage unavailable — clearing in-memory state is enough.
    }
    setToken(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ token, isAuthed: token !== null, login, logout }),
    [token, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside an <AuthProvider>.");
  }
  return ctx;
}
