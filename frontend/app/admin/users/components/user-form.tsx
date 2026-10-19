"use client";

/**
 * Create-user modal for /admin/users.
 *
 * Fields: username, role, tournament, optional password.
 * When role = observer, a team dropdown appears; picking a team
 * auto-shows its slot number next to it ("Team Soul → Slot 3").
 * Password is optional — leaving it blank lets the backend auto-generate
 * one (shown once on the list page after creation).
 */

import { useMemo, useState } from "react";
import {
  ActionButton,
  Field,
  FieldInput,
  FieldSelect,
  Modal,
} from "@/components/admin-crud";
import { Badge } from "@/components/ui";
import {
  USER_ROLES,
  slotLabel,
  type TeamWithSlot,
  type TournamentOption,
  type UserRole,
} from "./types";

export interface UserFormPayload {
  username: string;
  role: UserRole;
  tournament_id: string | null;
  team_id: string | null;
  password: string | null;
}

export function CreateUserModal({
  teams,
  tournaments,
  saving,
  serverError,
  defaultTournamentId,
  onSubmit,
  onCancel,
}: {
  teams: TeamWithSlot[];
  tournaments: TournamentOption[];
  saving: boolean;
  serverError: string | null;
  defaultTournamentId: string | null;
  onSubmit: (payload: UserFormPayload) => void;
  onCancel: () => void;
}) {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<UserRole>("observer");
  const [tournamentId, setTournamentId] = useState(defaultTournamentId ?? "");
  const [teamId, setTeamId] = useState("");
  const [password, setPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const selectedTeam = useMemo(
    () => teams.find((t) => t.id === teamId) ?? null,
    [teams, teamId]
  );

  const isObserver = role === "observer";

  const submit = (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!username.trim()) {
      setLocalError("Username is required.");
      return;
    }
    if (isObserver && !teamId) {
      setLocalError("Observer accounts must be assigned to a team.");
      return;
    }
    setLocalError(null);
    onSubmit({
      username: username.trim(),
      role,
      tournament_id: tournamentId || null,
      team_id: isObserver ? teamId : null,
      password: password.trim() ? password.trim() : null,
    });
  };

  return (
    <Modal title="Create user" onClose={onCancel}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Username" htmlFor="user-username" required>
          <FieldInput
            id="user-username"
            value={username}
            onChange={setUsername}
            placeholder="e.g. observer-slot-3"
            autoFocus
            maxLength={64}
          />
        </Field>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Role" htmlFor="user-role" required>
            <FieldSelect
              id="user-role"
              value={role}
              onChange={(v) => setRole(v as UserRole)}
              options={USER_ROLES.map((r) => ({ value: r, label: r }))}
            />
          </Field>
          <Field label="Tournament" htmlFor="user-tournament">
            <FieldSelect
              id="user-tournament"
              value={tournamentId}
              onChange={setTournamentId}
              options={tournaments.map((t) => ({ value: t.id, label: t.name }))}
              allowEmpty
              emptyLabel="None (global account)"
            />
          </Field>
        </div>

        {isObserver && (
          <Field
            label="Team (observer)"
            htmlFor="user-team"
            required
            hint="Pick a team — its slot number appears automatically."
          >
            <div className="flex items-center gap-2">
              <div className="flex-1">
                <FieldSelect
                  id="user-team"
                  value={teamId}
                  onChange={setTeamId}
                  options={teams.map((t) => ({ value: t.id, label: t.name }))}
                  allowEmpty
                  emptyLabel="Select a team…"
                />
              </div>
              <Badge tone={selectedTeam?.slot_number != null ? "cyan" : "slate"}>
                {selectedTeam ? slotLabel(selectedTeam.slot_number) : "—"}
              </Badge>
            </div>
            {selectedTeam && (
              <p className="mt-1 text-xs text-white/50">
                {selectedTeam.name} → {slotLabel(selectedTeam.slot_number)}
              </p>
            )}
          </Field>
        )}

        <Field
          label="Password"
          htmlFor="user-password"
          hint="Leave blank to auto-generate a secure password."
        >
          <FieldInput
            id="user-password"
            type="text"
            value={password}
            onChange={setPassword}
            placeholder="Auto-generated if left blank"
            autoComplete="new-password"
            maxLength={128}
          />
        </Field>

        {(localError || serverError) && (
          <p className="text-sm text-[#ff99ab]" role="alert">
            {localError ?? serverError}
          </p>
        )}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <ActionButton variant="ghost" onClick={onCancel} disabled={saving}>
            Cancel
          </ActionButton>
          <ActionButton variant="primary" type="submit" disabled={saving}>
            {saving ? "Creating…" : "Create user"}
          </ActionButton>
        </div>
      </form>
    </Modal>
  );
}
