"use client";

/**
 * /admin/players — master-data management for players.
 *
 * Player contract (fixed): GET/POST /api/v1/players,
 * GET/PUT/PATCH/DELETE /api/v1/players/{id}, GET /api/v1/teams/{id}/players,
 * `{ data }` envelope. Fields: id, team_id, name, ign, role
 * (IGL|assault|support|sniper), avatar_url, is_active.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { RequireRole } from "../../lib/guard";
import { Badge } from "../../components/ui";
import {
  ActionButton,
  ActiveBadge,
  AdminPageShell,
  ConfirmDialog,
  Field,
  FieldInput,
  FieldSelect,
  ListBody,
  Modal,
  SearchBox,
  SectionCard,
  Toasts,
  Toggle,
  apiErrorInfo,
  useAdminList,
  useToast,
  type ApiResult,
  type Player,
  type Team,
} from "../../components/admin-crud";

const PLAYER_ROLES = [
  { value: "IGL", label: "IGL" },
  { value: "assault", label: "Assault" },
  { value: "support", label: "Support" },
  { value: "sniper", label: "Sniper" },
];

interface PlayerFormState {
  name: string;
  ign: string;
  role: string;
  team_id: string;
  avatar_url: string;
  is_active: boolean;
}

const EMPTY_FORM: PlayerFormState = {
  name: "",
  ign: "",
  role: "",
  team_id: "",
  avatar_url: "",
  is_active: true,
};

function formFromPlayer(p: Player): PlayerFormState {
  return {
    name: p.name ?? "",
    ign: p.ign ?? "",
    role: p.role ?? "",
    team_id: p.team_id ?? "",
    avatar_url: p.avatar_url ?? "",
    is_active: p.is_active ?? true,
  };
}

function PlayerForm({
  initial,
  teams,
  saving,
  serverError,
  onSubmit,
  onCancel,
}: {
  initial: PlayerFormState;
  teams: Team[];
  saving: boolean;
  serverError: string | null;
  onSubmit: (form: PlayerFormState) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<PlayerFormState>(initial);
  const [localError, setLocalError] = useState<string | null>(null);
  const set = useCallback(
    <K extends keyof PlayerFormState>(key: K, value: PlayerFormState[K]) => {
      setForm((f) => ({ ...f, [key]: value }));
    },
    []
  );

  const submit = (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!form.name.trim()) {
      setLocalError("Player name is required.");
      return;
    }
    setLocalError(null);
    onSubmit(form);
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field label="Player name" htmlFor="player-name" required>
        <FieldInput
          id="player-name"
          value={form.name}
          onChange={(v) => set("name", v)}
          placeholder="e.g. Naman S"
          autoFocus
        />
      </Field>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="In-game name (IGN)" htmlFor="player-ign">
          <FieldInput
            id="player-ign"
            value={form.ign}
            onChange={(v) => set("ign", v)}
            placeholder="e.g. NINJAJOD"
          />
        </Field>
        <Field label="Role" htmlFor="player-role">
          <FieldSelect
            id="player-role"
            value={form.role}
            onChange={(v) => set("role", v)}
            options={PLAYER_ROLES}
            allowEmpty
            emptyLabel="Select role…"
          />
        </Field>
      </div>
      <Field
        label="Team"
        htmlFor="player-team"
        hint={teams.length === 0 ? "No teams exist yet — create one on the Teams page first." : undefined}
      >
        <FieldSelect
          id="player-team"
          value={form.team_id}
          onChange={(v) => set("team_id", v)}
          options={teams.map((t) => ({
            value: t.id,
            label: `${t.name}${t.is_active ?? true ? "" : " (inactive)"}`,
          }))}
          allowEmpty
          emptyLabel="Unassigned"
        />
      </Field>
      <Field label="Avatar URL" htmlFor="player-avatar">
        <FieldInput
          id="player-avatar"
          type="url"
          value={form.avatar_url}
          onChange={(v) => set("avatar_url", v)}
          placeholder="https://…"
        />
      </Field>
      <Field label="Status" htmlFor="player-active">
        <Toggle
          checked={form.is_active}
          onChange={(v) => set("is_active", v)}
          label="Player active status"
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
        <ActionButton variant="primary" disabled={saving} type="submit">
          {saving ? "Saving…" : "Save player"}
        </ActionButton>
      </div>
    </form>
  );
}

function PlayersPageInner() {
  const router = useRouter();
  const { token, isAuthed, logout } = useAuth();
  const { toasts, show, dismiss } = useToast();

  const playersList = useAdminList<Player>("/api/v1/players", token);
  const teamsList = useAdminList<Team>("/api/v1/teams", token);

  const [query, setQuery] = useState("");
  const [teamFilter, setTeamFilter] = useState("");
  const [editing, setEditing] = useState<Player | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Player | null>(null);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthed) router.replace("/login");
  }, [isAuthed, router]);

  const teams = useMemo(() => teamsList.items ?? [], [teamsList.items]);
  const teamById = useMemo(
    () => new Map(teams.map((t) => [t.id, t])),
    [teams]
  );

  const filtered = useMemo(() => {
    const items = playersList.items ?? [];
    const q = query.trim().toLowerCase();
    return items.filter((p) => {
      if (teamFilter) {
        if (teamFilter === "__unassigned") {
          if (p.team_id) return false;
        } else if (p.team_id !== teamFilter) {
          return false;
        }
      }
      if (!q) return true;
      return [p.name, p.ign, p.role].some((v) =>
        (v ?? "").toLowerCase().includes(q)
      );
    });
  }, [playersList.items, query, teamFilter]);

  /** Players grouped by team id, preserving team order, unassigned last. */
  const groups = useMemo(() => {
    const order: string[] = [];
    const map = new Map<string, Player[]>();
    for (const p of filtered) {
      const key = p.team_id && teamById.has(p.team_id) ? p.team_id : "__unassigned";
      if (!map.has(key)) {
        map.set(key, []);
        order.push(key);
      }
      map.get(key)!.push(p);
    }
    // Keep teams in master-data order, unassigned last.
    const teamOrder = new Map(teams.map((t, i) => [t.id, i]));
    order.sort((a, b) => {
      if (a === "__unassigned") return 1;
      if (b === "__unassigned") return -1;
      return (teamOrder.get(a) ?? 9999) - (teamOrder.get(b) ?? 9999);
    });
    return order.map((key) => ({ key, players: map.get(key)! }));
  }, [filtered, teamById, teams]);

  const closeForm = useCallback(() => {
    setEditing(null);
    setCreating(false);
    setServerError(null);
  }, []);

  const savePlayer = useCallback(
    async (form: PlayerFormState) => {
      if (!token) return;
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        is_active: form.is_active,
      };
      if (form.ign.trim()) payload.ign = form.ign.trim();
      if (form.role) payload.role = form.role;
      if (form.team_id) payload.team_id = form.team_id;
      if (form.avatar_url.trim()) payload.avatar_url = form.avatar_url.trim();

      setSaving(true);
      setServerError(null);
      try {
        if (editing) {
          await apiFetch<ApiResult<Player>>(`/api/v1/players/${editing.id}`, {
            method: "PUT",
            body: payload,
            token,
          });
          show("success", `Player "${payload.name}" updated.`);
        } else {
          await apiFetch<ApiResult<Player>>("/api/v1/players", {
            method: "POST",
            body: payload,
            token,
          });
          show("success", `Player "${payload.name}" created.`);
        }
        closeForm();
        await playersList.reload();
      } catch (e) {
        if (e instanceof Error && "status" in e && (e as { status?: number }).status === 401) {
          logout();
          router.replace("/login");
          return;
        }
        setServerError(apiErrorInfo(e).message);
      } finally {
        setSaving(false);
      }
    },
    [token, editing, playersList, closeForm, show, logout, router]
  );

  const toggleActive = useCallback(
    async (player: Player) => {
      if (!token) return;
      try {
        await apiFetch<ApiResult<Player>>(`/api/v1/players/${player.id}`, {
          method: "PATCH",
          body: { is_active: !(player.is_active ?? true) },
          token,
        });
        show(
          "success",
          `Player "${player.name}" ${player.is_active ?? true ? "deactivated" : "activated"}.`
        );
        await playersList.reload();
      } catch (e) {
        show("error", `Could not update "${player.name}": ${apiErrorInfo(e).message}`);
      }
    },
    [token, playersList, show]
  );

  const deletePlayer = useCallback(async () => {
    if (!token || !deleting) return;
    setSaving(true);
    try {
      await apiFetch(`/api/v1/players/${deleting.id}`, { method: "DELETE", token });
      show("success", `Player "${deleting.name}" deleted.`);
      setDeleting(null);
      await playersList.reload();
    } catch (e) {
      show("error", `Could not delete "${deleting.name}": ${apiErrorInfo(e).message}`);
    } finally {
      setSaving(false);
    }
  }, [token, deleting, playersList, show]);

  if (!isAuthed) return null;

  return (
    <AdminPageShell
      title="Players"
      subtitle="Master data: players grouped by team"
    >
      <Toasts toasts={toasts} onDismiss={dismiss} />
      <SectionCard
        title="Players"
        action={
          <ActionButton variant="primary" onClick={() => setCreating(true)}>
            + Add player
          </ActionButton>
        }
      >
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="Search players by name, IGN or role…"
          />
          <FieldSelect
            id="player-team-filter"
            value={teamFilter}
            onChange={setTeamFilter}
            options={[
              ...teams.map((t) => ({ value: t.id, label: t.name })),
              { value: "__unassigned", label: "Unassigned" },
            ]}
            allowEmpty
            emptyLabel="All teams"
          />
        </div>
        <ListBody
          list={playersList}
          loadingLabel="Loading players…"
          emptyMessage="No players yet — add your first player."
        >
          {(items) =>
            groups.length === 0 ? (
              <p className="py-6 text-center font-mono text-sm text-zinc-500">
                {items.length === 0
                  ? "No players yet — add your first player."
                  : "No players match your search."}
              </p>
            ) : (
              <div className="flex flex-col gap-6">
                {groups.map(({ key, players }) => (
                  <div key={key}>
                    <div className="mb-2 flex items-center gap-2 border-b border-white/10 pb-2">
                      {key !== "__unassigned" && teamById.get(key)?.logo_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={teamById.get(key)!.logo_url!}
                          alt=""
                          className="h-6 w-6 rounded-full bg-white/10 object-cover"
                          loading="lazy"
                        />
                      ) : null}
                      <h3 className="text-sm font-semibold text-white">
                        {key === "__unassigned"
                          ? "Unassigned"
                          : (teamById.get(key)?.name ?? "Unknown team")}
                      </h3>
                      <Badge tone="slate">{players.length}</Badge>
                      {key !== "__unassigned" && teamById.get(key)?.tag && (
                        <Badge tone="cyan">{teamById.get(key)!.tag}</Badge>
                      )}
                    </div>
                    <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
                      {players.map((p) => (
                        <li
                          key={p.id}
                          className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/[0.03] p-2"
                        >
                          {p.avatar_url ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={p.avatar_url}
                              alt=""
                              className="h-10 w-10 shrink-0 rounded-full bg-white/10 object-cover"
                              loading="lazy"
                            />
                          ) : (
                            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white/10 font-mono text-xs text-white/60">
                              {(p.ign || p.name || "?").slice(0, 2).toUpperCase()}
                            </span>
                          )}
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium text-white">
                              {p.ign || p.name}
                              {p.ign && p.name && p.ign !== p.name && (
                                <span className="ml-2 font-normal text-white/40">{p.name}</span>
                              )}
                            </div>
                            <div className="mt-0.5 flex items-center gap-2">
                              {p.role && <Badge tone="cyan">{p.role}</Badge>}
                              <ActiveBadge active={p.is_active ?? true} />
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center">
                            <button
                              type="button"
                              onClick={() => void toggleActive(p)}
                              aria-label={`${p.is_active ?? true ? "Deactivate" : "Activate"} player ${p.name}`}
                              className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-white/60 hover:bg-white/10 hover:text-[#00f0ff]"
                              title="Toggle active"
                            >
                              <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                                <path
                                  d="M10 3a7 7 0 015.5 11.3M10 3a7 7 0 00-5.5 11.3"
                                  stroke="currentColor"
                                  strokeWidth="1.6"
                                  strokeLinecap="round"
                                />
                                <circle cx="10" cy="13.5" r="2" stroke="currentColor" strokeWidth="1.6" />
                              </svg>
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setEditing(p);
                                setServerError(null);
                              }}
                              aria-label={`Edit player ${p.name}`}
                              className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-white/60 hover:bg-white/10 hover:text-[#00f0ff]"
                            >
                              <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                                <path
                                  d="M13.5 3.5l3 3L7 16l-4 1 1-4 9.5-9.5z"
                                  stroke="currentColor"
                                  strokeWidth="1.6"
                                  strokeLinejoin="round"
                                />
                              </svg>
                            </button>
                            <button
                              type="button"
                              onClick={() => setDeleting(p)}
                              aria-label={`Delete player ${p.name}`}
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
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )
          }
        </ListBody>
        {teamsList.error && (
          <p className="mt-3 text-xs text-amber-300" role="alert">
            Could not load teams for the team picker: {teamsList.error.message}
          </p>
        )}
      </SectionCard>

      {(creating || editing) && (
        <Modal title={creating ? "Add player" : `Edit player — ${editing?.name}`} onClose={closeForm}>
          <PlayerForm
            initial={editing ? formFromPlayer(editing) : EMPTY_FORM}
            teams={teams}
            saving={saving}
            serverError={serverError}
            onSubmit={(f) => void savePlayer(f)}
            onCancel={closeForm}
          />
        </Modal>
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete player "${deleting.name}"?`}
          body={
            <p>
              This permanently removes{" "}
              <strong className="text-white">{deleting.ign || deleting.name}</strong> from the
              master data. This cannot be undone.
            </p>
          }
          confirmLabel="Delete player"
          onConfirm={() => void deletePlayer()}
          onCancel={() => setDeleting(null)}
          busy={saving}
        />
      )}

      <p className="text-xs text-white/30">
        <Link href="/admin" className="hover:text-[#00f0ff]">
          ← Back to Admin
        </Link>
      </p>
    </AdminPageShell>
  );
}

export default function PlayersPage() {
  return (
    <RequireRole roles={["admin"]}>
      <PlayersPageInner />
    </RequireRole>
  );
}
