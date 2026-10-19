"use client";

/**
 * AEGIS AI — role model and role-aware hook (Phase 6, extended Phase 16).
 *
 * Roles (Phase 16 tournament-scoped user management; hierarchy:
 * admin > operator/director > spotter/observer > viewer):
 *   admin    — everything, including /admin
 *   operator — /control (control room), /watch, /teams, /commentary, /live
 *   director — /broadcast/director (the broadcast director console)
 *   spotter  — /spotter (mobile event-logging console)
 *   observer — /observer (team-scoped observer console; sees only their team)
 *   viewer   — /watch only (+ /login)
 *
 * The role is resolved once at login:
 *   1. the login response body's `role` field, when it is one of the known
 *      roles;
 *   2. otherwise the JWT `role` claim, falling back to `is_superuser`;
 *   3. safe default: "viewer".
 *
 * (The JWT claim is the authoritative source — the body field is just a
 * convenience echo from the login endpoint when present. Phase 16 tokens
 * also carry `team_id` and `tournament_id` claims; read those with
 * decodeJwtClaims, not from any client-side store.)
 *
 * Display-only helper: roles are enforced server-side by the API. The UI
 * only uses the role to hide routes it could never use.
 */

import { useAuth } from "./auth";

export type Role =
  | "admin"
  | "operator"
  | "director"
  | "spotter"
  | "observer"
  | "viewer";

const KNOWN_ROLES: readonly Role[] = [
  "admin",
  "operator",
  "director",
  "spotter",
  "observer",
  "viewer",
];

/** Post-login landing page per role (Phase 16 redirect matrix). */
export const ROLE_HOME: Record<Role, string> = {
  observer: "/observer",
  spotter: "/spotter",
  director: "/broadcast/director",
  admin: "/admin",
  operator: "/control",
  viewer: "/watch",
};

function isRole(value: unknown): value is Role {
  return (
    typeof value === "string" &&
    (KNOWN_ROLES as readonly string[]).includes(value)
  );
}

/** Read a JWT payload without verifying it. Display-only, never authoritative. */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    let b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    // base64url omits padding; atob needs it.
    b64 += "=".repeat((4 - (b64.length % 4)) % 4);
    const json = atob(b64);
    const payload: unknown = JSON.parse(json);
    return payload !== null && typeof payload === "object"
      ? (payload as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/**
 * Phase 16 tournament-scope claims carried by the JWT (the payload is the
 * source of truth; there is no client-side store for these).
 *
 * Returns null claims when the token has no payload or the claims are
 * absent (e.g. a pre-Phase-16 token). Callers must handle the nulls
 * honestly — never invent a team id.
 */
export interface JwtScopeClaims {
  teamId: string | null;
  tournamentId: string | null;
  username: string | null;
}

export function decodeJwtClaims(token: string): JwtScopeClaims {
  const payload = decodeJwtPayload(token);
  const str = (v: unknown): string | null =>
    typeof v === "string" && v.length > 0 ? v : null;
  return {
    teamId: payload ? str(payload.team_id) : null,
    tournamentId: payload ? str(payload.tournament_id) : null,
    username: payload ? str(payload.username) : null,
  };
}

/**
 * Resolve the effective role for a fresh login.
 *
 * @param loginRole the `role` field from the login response (may be absent
 *                  on older backends)
 * @param token     the access token that was just issued
 */
export function resolveRole(loginRole: unknown, token: string): Role {
  if (isRole(loginRole)) return loginRole;
  const payload = decodeJwtPayload(token);
  if (payload) {
    if (isRole(payload.role)) return payload.role;
    // The pre-Phase-6 backend only issued admin tokens (single operator
    // login with ADMIN_USERNAME/ADMIN_PASSWORD).
    if (payload.is_superuser === true) return "admin";
  }
  // Safe default: the least privileged role. The user can still reach
  // /watch, and a backend that knows better returns a real role.
  return "viewer";
}

export function useRole(): {
  role: Role;
  isAdmin: boolean;
  isOperator: boolean;
  isViewer: boolean;
} {
  const { role } = useAuth();
  return {
    role,
    isAdmin: role === "admin",
    isOperator: role === "operator" || role === "admin",
    isViewer: role === "viewer",
  };
}
