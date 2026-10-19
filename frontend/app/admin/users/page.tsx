"use client";

/**
 * /admin/users — Tournament-Scoped User Management (Phase 16).
 *
 * Admin-only. User list with search, role + tournament filters, per-row
 * role badge, tournament, team (+ auto slot number), active toggle, and
 * actions: reset password (shown once), activate/deactivate, delete.
 * Plus: create-user modal, bulk observer creation with a printable
 * credential sheet, and the end-of-tournament kill switch.
 *
 * Backend contract (W1): POST/GET/PATCH/DELETE /api/v1/users,
 * POST /api/v1/users/bulk-observers,
 * POST /api/v1/users/tournament/{tid}/deactivate. `{ data }` envelope.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RequireRole } from "@/lib/guard";
import { Badge } from "@/components/ui";
import {
  ActionButton,
  ActiveBadge,
  AdminPageShell,
  ConfirmDialog,
  FieldSelect,
  ListBody,
  Modal,
  SearchBox,
  SectionCard,
  Toasts,
  apiErrorInfo,
  useAdminList,
  useToast,
  type ApiResult,
  type Team,
} from "@/components/admin-crud";
import {
  USER_ROLES,
  slotLabel,
  type ManagedUser,
  type TeamWithSlot,
  type TournamentOption,
  type UserRole,
} from "./components/types";
import { CreateUserModal, type UserFormPayload } from "./components/user-form";
import { BulkObservers } from "./components/bulk-observers";
import { KillSwitch } from "./components/kill-switch";

const TOURNAMENTS_PATH = "/api/v1/tournament";
const TEAMS_PATH = "/api/v1/teams";
const USERS_PATH = "/api/v1/users";

function roleTone(role: UserRole): "red" | "cyan" | "amber" | "green" | "slate" {
  switch (role) {
    case "admin":
      return "red";
    case "operator":
      return "cyan";
    case "director":
      return "amber";
    case "spotter":
      return "green";
    case "observer":
      return "cyan";
    default:
      return "slate";
  }
}

/** Crypto-strong 12-char password (letters + digits). */
function generatePassword(): string {
  const alphabet =
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(14));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* user can select manually */
    }
  };
  return (
    <ActionButton
      variant="ghost"
      onClick={() => void copy()}
      aria-label={label}
    >
      {copied ? "Copied ✓" : "Copy"}
    </ActionButton>
  );
}

/** One-time password display: password lives only here, never in toasts. */
function OneTimePasswordModal({
  username,
  password,
  context,
  onClose,
}: {
  username: string;
  password: string;
  context: string;
  onClose: () => void;
}) {
  return (
    <Modal title={context} onClose={onClose}>
      <p className="text-sm text-amber-300">
        ⚠️ This password is shown only once. Copy it now — it cannot be
        retrieved later.
      </p>
      <div className="mt-3 rounded-lg border border-white/10 bg-black/40 p-4">
        <div className="text-xs text-white/50">Username</div>
        <div className="font-mono text-lg text-white">{username}</div>
        <div className="mt-2 text-xs text-white/50">Password</div>
        <div className="font-mono text-lg text-[#00f0ff]">{password}</div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <CopyButton text={password} label={`Copy password for ${username}`} />
        <ActionButton variant="primary" onClick={onClose}>
          Done
        </ActionButton>
      </div>
    </Modal>
  );
}

function UsersPageInner() {
  const router = useRouter();
  const { token, isAuthed, logout } = useAuth();
  const { toasts, show, dismiss } = useToast();

  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [tournamentFilter, setTournamentFilter] = useState("");

  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<ManagedUser | null>(null);
  const [resetBusy, setResetBusy] = useState(false);
  const [newPassword, setNewPassword] = useState<{
    username: string;
    password: string;
    context: string;
  } | null>(null);
  const [deleting, setDeleting] = useState<ManagedUser | null>(null);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthed) router.replace("/login");
  }, [isAuthed, router]);

  const listPath = useMemo(() => {
    const params = new URLSearchParams();
    if (roleFilter) params.set("role", roleFilter);
    if (tournamentFilter) params.set("tournament_id", tournamentFilter);
    const qs = params.toString();
    return qs ? `${USERS_PATH}?${qs}` : USERS_PATH;
  }, [roleFilter, tournamentFilter]);

  const users = useAdminList<ManagedUser>(token ? listPath : null, token);
  const tournaments = useAdminList<TournamentOption>(token ? TOURNAMENTS_PATH : null, token);
  const teams = useAdminList<Team>(token ? TEAMS_PATH : null, token);

  const tournamentOptions: TournamentOption[] = useMemo(
    () => (tournaments.items ?? []).map((t) => ({ id: t.id, name: t.name, status: t.status })),
    [tournaments.items]
  );
  const teamOptions: TeamWithSlot[] = useMemo(
    () =>
      (teams.items ?? []).map((t) => ({
        id: t.id,
        name: t.name,
        tag: t.tag,
        slot_number: (t as { slot_number?: number | null }).slot_number ?? null,
      })),
    [teams.items]
  );

  const rows = useMemo(() => {
    const items = users.items ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((u) =>
      [u.username, u.team_name, u.tournament_name]
        .some((v) => (v ?? "").toLowerCase().includes(q))
    );
  }, [users.items, query]);

  const handle401 = useCallback(() => {
    logout();
    router.replace("/login");
  }, [logout, router]);

  const closeForm = useCallback(() => {
    setCreating(false);
    setServerError(null);
  }, []);

  const createUser = useCallback(
    async (payload: UserFormPayload) => {
      if (!token) return;
      setSaving(true);
      setServerError(null);
      try {
        const body: Record<string, unknown> = {
          username: payload.username,
          role: payload.role,
        };
        if (payload.tournament_id) body.tournament_id = payload.tournament_id;
        if (payload.team_id) body.team_id = payload.team_id;
        if (payload.password) body.password = payload.password;
        const res = await apiFetch<
          ApiResult<{ id: string; username: string; generated_password?: string }>
        >(USERS_PATH, { method: "POST", body, token });
        const created =
          res && typeof res === "object" && "data" in res
            ? (res as { data: { username: string; generated_password?: string } }).data
            : (res as { username: string; generated_password?: string });
        closeForm();
        await users.reload();
        if (created.generated_password) {
          setNewPassword({
            username: created.username,
            password: created.generated_password,
            context: `User "${created.username}" created`,
          });
        } else {
          show("success", `User "${created.username}" created.`);
        }
      } catch (e) {
        if (apiErrorInfo(e).status === 401) {
          handle401();
          return;
        }
        setServerError(apiErrorInfo(e).message);
      } finally {
        setSaving(false);
      }
    },
    [token, users, closeForm, show, handle401]
  );

  const resetPassword = useCallback(async () => {
    if (!token || !resetting) return;
    const pw = generatePassword();
    setResetBusy(true);
    try {
      await apiFetch(`${USERS_PATH}/${resetting.id}`, {
        method: "PATCH",
        body: { password: pw },
        token,
      });
      setNewPassword({
        username: resetting.username,
        password: pw,
        context: `Password reset for "${resetting.username}"`,
      });
      setResetting(null);
      await users.reload();
    } catch (e) {
      if (apiErrorInfo(e).status === 401) {
        handle401();
        return;
      }
      show("error", `Could not reset password: ${apiErrorInfo(e).message}`);
    } finally {
      setResetBusy(false);
    }
  }, [token, resetting, users, show, handle401]);

  const toggleActive = useCallback(
    async (u: ManagedUser) => {
      if (!token) return;
      const next = !(u.is_active ?? true);
      try {
        await apiFetch(`${USERS_PATH}/${u.id}`, {
          method: "PATCH",
          body: { is_active: next },
          token,
        });
        show("success", `User "${u.username}" ${next ? "activated" : "deactivated"}.`);
        await users.reload();
      } catch (e) {
        if (apiErrorInfo(e).status === 401) {
          handle401();
          return;
        }
        show("error", `Could not update "${u.username}": ${apiErrorInfo(e).message}`);
      }
    },
    [token, users, show, handle401]
  );

  const deleteUser = useCallback(async () => {
    if (!token || !deleting) return;
    setSaving(true);
    try {
      await apiFetch(`${USERS_PATH}/${deleting.id}`, { method: "DELETE", token });
      show("success", `User "${deleting.username}" deleted.`);
      setDeleting(null);
      await users.reload();
    } catch (e) {
      if (apiErrorInfo(e).status === 401) {
        handle401();
        return;
      }
      show("error", `Could not delete "${deleting.username}": ${apiErrorInfo(e).message}`);
    } finally {
      setSaving(false);
    }
  }, [token, deleting, users, show, handle401]);

  const notify = useCallback(
    (kind: "success" | "error", msg: string) => show(kind, msg),
    [show]
  );
  const reloadUsers = useCallback(() => users.reload(), [users]);

  if (!isAuthed || !token) return null;

  return (
    <AdminPageShell
      title="Users"
      subtitle="Tournament-scoped accounts: staff, spotters and observers"
    >
      <Toasts toasts={toasts} onDismiss={dismiss} />

      <SectionCard
        title="Users"
        action={
          <ActionButton variant="primary" onClick={() => setCreating(true)}>
            + Add user
          </ActionButton>
        }
      >
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="Search by username, team or tournament…"
          />
          <FieldSelect
            id="filter-role"
            value={roleFilter}
            onChange={setRoleFilter}
            options={USER_ROLES.map((r) => ({ value: r, label: r }))}
            allowEmpty
            emptyLabel="All roles"
          />
          <FieldSelect
            id="filter-tournament"
            value={tournamentFilter}
            onChange={setTournamentFilter}
            options={tournamentOptions.map((t) => ({ value: t.id, label: t.name }))}
            allowEmpty
            emptyLabel="All tournaments"
          />
        </div>
        <ListBody
          list={users}
          loadingLabel="Loading users…"
          emptyMessage="No users yet — add your first user."
        >
          {(items) =>
            rows.length === 0 ? (
              <p className="py-6 text-center font-mono text-sm text-zinc-500">
                {items.length === 0
                  ? "No users yet — add your first user."
                  : "No users match your search."}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-white/10 text-xs text-white/50">
                      <th className="pb-2 pr-4 font-medium">Username</th>
                      <th className="pb-2 pr-4 font-medium">Role</th>
                      <th className="pb-2 pr-4 font-medium">Tournament</th>
                      <th className="pb-2 pr-4 font-medium">Team / Slot</th>
                      <th className="pb-2 pr-4 font-medium">Status</th>
                      <th className="pb-2 font-medium">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((u) => (
                      <tr key={u.id} className="border-b border-white/5 align-middle">
                        <td className="py-2 pr-4 font-mono">{u.username}</td>
                        <td className="py-2 pr-4">
                          <Badge tone={roleTone(u.role)}>{u.role}</Badge>
                        </td>
                        <td className="py-2 pr-4 text-white/70">
                          {u.tournament_name ?? "—"}
                        </td>
                        <td className="py-2 pr-4">
                          {u.team_name ? (
                            <span className="inline-flex items-center gap-2">
                              <span className="text-white/85">{u.team_name}</span>
                              <Badge tone={u.slot_number != null ? "cyan" : "slate"}>
                                {slotLabel(u.slot_number)}
                              </Badge>
                            </span>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          <button
                            type="button"
                            onClick={() => void toggleActive(u)}
                            aria-label={`${u.is_active ?? true ? "Deactivate" : "Activate"} user ${u.username}`}
                            className="flex min-h-[44px] items-center rounded-lg hover:opacity-80"
                            title="Toggle active"
                          >
                            <ActiveBadge active={u.is_active ?? true} />
                          </button>
                        </td>
                        <td className="py-2">
                          <div className="flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => setResetting(u)}
                              aria-label={`Reset password for ${u.username}`}
                              title="Reset password"
                              className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg px-2 text-xs text-white/60 hover:bg-white/10 hover:text-[#00f0ff]"
                            >
                              Reset pw
                            </button>
                            <button
                              type="button"
                              onClick={() => setDeleting(u)}
                              aria-label={`Delete user ${u.username}`}
                              title="Delete user"
                              className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-white/60 hover:bg-[#ff3366]/10 hover:text-[#ff99ab]"
                            >
                              <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                                <path
                                  d="M4 6h12M8 4h4M6 6l1 11h6l1-11M9.5 10v4M12.5 10v4"
                                  stroke="currentColor"
                                  strokeWidth="1.6"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                />
                              </svg>
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </ListBody>
      </SectionCard>

      <SectionCard title="Bulk observers">
        <BulkObservers
          token={token}
          tournaments={tournamentOptions}
          onDone={reloadUsers}
          notify={notify}
        />
      </SectionCard>

      <SectionCard title="End of tournament">
        <KillSwitch
          token={token}
          tournaments={tournamentOptions}
          onDone={reloadUsers}
          notify={notify}
        />
      </SectionCard>

      {creating && (
        <CreateUserModal
          teams={teamOptions}
          tournaments={tournamentOptions}
          saving={saving}
          serverError={serverError}
          defaultTournamentId={tournamentFilter || null}
          onSubmit={(p) => void createUser(p)}
          onCancel={closeForm}
        />
      )}

      {resetting && (
        <ConfirmDialog
          title={`Reset password for "${resetting.username}"?`}
          body={
            <>
              <p>
                A new secure password will be generated for{" "}
                <strong className="text-white">{resetting.username}</strong> and shown to you
                once. The old password stops working immediately.
              </p>
              <p className="mt-2 text-white/50">
                Copy the new password and hand it to the user securely.
              </p>
            </>
          }
          confirmLabel="Generate new password"
          onConfirm={() => void resetPassword()}
          onCancel={() => setResetting(null)}
          busy={resetBusy}
        />
      )}

      {newPassword && (
        <OneTimePasswordModal
          username={newPassword.username}
          password={newPassword.password}
          context={newPassword.context}
          onClose={() => setNewPassword(null)}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete user "${deleting.username}"?`}
          body={
            <>
              <p>
                This permanently removes <strong className="text-white">{deleting.username}</strong>{" "}
                ({deleting.role}
                {deleting.tournament_name ? ` · ${deleting.tournament_name}` : ""}). This cannot
                be undone.
              </p>
              <p className="mt-2 text-white/50">
                To confirm, type the username exactly.
              </p>
            </>
          }
          confirmLabel="Delete user"
          requireText={deleting.username}
          onConfirm={() => void deleteUser()}
          onCancel={() => setDeleting(null)}
          busy={saving}
        />
      )}
    </AdminPageShell>
  );
}

export default function UsersPage() {
  return (
    <RequireRole roles={["admin"]}>
      <UsersPageInner />
    </RequireRole>
  );
}
