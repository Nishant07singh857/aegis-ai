/**
 * Shared types for /admin/users (Phase 16 — Tournament-Scoped User Management).
 * These mirror the W1 backend contract; fields beyond the contract are
 * optional so the UI degrades honestly if the backend lags.
 */

export type UserRole =
  | "admin"
  | "operator"
  | "director"
  | "spotter"
  | "observer"
  | "viewer";

export const USER_ROLES: UserRole[] = [
  "admin",
  "operator",
  "director",
  "spotter",
  "observer",
  "viewer",
];

export interface ManagedUser {
  id: string;
  username: string;
  role: UserRole;
  tournament_id?: string | null;
  tournament_name?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  slot_number?: number | null;
  is_active?: boolean | null;
  created_at?: string | null;
}

/** Team with an optional slot number (slot binding ships with Phase 16). */
export interface TeamWithSlot {
  id: string;
  name: string;
  tag?: string | null;
  slot_number?: number | null;
}

export interface TournamentOption {
  id: string;
  name: string;
  status?: string | null;
}

export interface ObserverCredential {
  username: string;
  password: string;
  team_id?: string | null;
  team_name?: string | null;
  slot_number?: number | null;
}

export function slotLabel(slot: number | null | undefined): string {
  return slot == null ? "—" : `Slot ${slot}`;
}
