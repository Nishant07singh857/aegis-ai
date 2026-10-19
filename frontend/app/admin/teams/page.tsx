"use client";

/**
 * /admin/teams — master-data management for teams.
 *
 * Team contract (fixed): GET/POST /api/v1/teams,
 * GET/PUT/PATCH/DELETE /api/v1/teams/{id}, `{ data }` envelope.
 * Fields: id, name, tag, logo_url, colors, region, is_active.
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
  ListBody,
  Modal,
  SearchBox,
  SectionCard,
  Toasts,
  Toggle,
  apiErrorInfo,
  parseJsonField,
  useAdminList,
  useToast,
  type ApiResult,
  type Team,
} from "../../components/admin-crud";

interface TeamFormState {
  name: string;
  tag: string;
  logo_url: string;
  region: string;
  colors: string;
  is_active: boolean;
}

const EMPTY_FORM: TeamFormState = {
  name: "",
  tag: "",
  logo_url: "",
  region: "",
  colors: "",
  is_active: true,
};

function formFromTeam(t: Team): TeamFormState {
  const colors = t.colors;
  return {
    name: t.name ?? "",
    tag: t.tag ?? "",
    logo_url: t.logo_url ?? "",
    region: t.region ?? "",
    colors:
      typeof colors === "string"
        ? colors
        : Array.isArray(colors)
          ? colors.join(", ")
          : colors != null
            ? JSON.stringify(colors)
            : "",
    is_active: t.is_active ?? true,
  };
}

/**
 * The API stores `colors` opaquely; accept JSON (object/array) or a
 * comma-separated list and send it in the matching shape.
 */
function colorsPayload(raw: string): { value?: unknown; error?: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { value: undefined };
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    const parsed = parseJsonField(trimmed, "Colors");
    return parsed.ok ? { value: parsed.value } : { error: parsed.error };
  }
  if (trimmed.includes(",")) {
    return { value: trimmed.split(",").map((c) => c.trim()).filter(Boolean) };
  }
  return { value: trimmed };
}

function TeamForm({
  initial,
  saving,
  serverError,
  onSubmit,
  onCancel,
}: {
  initial: TeamFormState;
  saving: boolean;
  serverError: string | null;
  onSubmit: (form: TeamFormState) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<TeamFormState>(initial);
  const [localError, setLocalError] = useState<string | null>(null);
  const set = useCallback(
    <K extends keyof TeamFormState>(key: K, value: TeamFormState[K]) => {
      setForm((f) => ({ ...f, [key]: value }));
    },
    []
  );

  const submit = (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!form.name.trim()) {
      setLocalError("Team name is required.");
      return;
    }
    setLocalError(null);
    onSubmit(form);
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field label="Team name" htmlFor="team-name" required>
        <FieldInput
          id="team-name"
          value={form.name}
          onChange={(v) => set("name", v)}
          placeholder="e.g. Orangutan"
          autoFocus
        />
      </Field>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Tag" htmlFor="team-tag">
          <FieldInput
            id="team-tag"
            value={form.tag}
            onChange={(v) => set("tag", v)}
            placeholder="e.g. OG"
            maxLength={12}
          />
        </Field>
        <Field label="Region" htmlFor="team-region">
          <FieldInput
            id="team-region"
            value={form.region}
            onChange={(v) => set("region", v)}
            placeholder="e.g. India"
          />
        </Field>
      </div>
      <Field label="Logo URL" htmlFor="team-logo">
        <FieldInput
          id="team-logo"
          type="url"
          value={form.logo_url}
          onChange={(v) => set("logo_url", v)}
          placeholder="https://…"
        />
      </Field>
      <Field
        label="Colors"
        htmlFor="team-colors"
        hint='Optional. JSON like ["#ff0000", "#000000"] or a comma-separated list.'
      >
        <FieldInput
          id="team-colors"
          value={form.colors}
          onChange={(v) => set("colors", v)}
          placeholder='["#ff0000", "#000000"]'
        />
      </Field>
      <Field label="Status" htmlFor="team-active">
        <Toggle
          checked={form.is_active}
          onChange={(v) => set("is_active", v)}
          label="Team active status"
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
          {saving ? "Saving…" : "Save team"}
        </ActionButton>
      </div>
    </form>
  );
}

function TeamsPageInner() {
  const router = useRouter();
  const { token, isAuthed, logout } = useAuth();
  const { toasts, show, dismiss } = useToast();

  const list = useAdminList<Team>("/api/v1/teams", token);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Team | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Team | null>(null);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthed) router.replace("/login");
  }, [isAuthed, router]);

  const rows = useMemo(() => {
    const items = list.items ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((t) =>
      [t.name, t.tag, t.region].some((v) => (v ?? "").toLowerCase().includes(q))
    );
  }, [list.items, query]);

  const closeForm = useCallback(() => {
    setEditing(null);
    setCreating(false);
    setServerError(null);
  }, []);

  const saveTeam = useCallback(
    async (form: TeamFormState) => {
      if (!token) return;
      const colors = colorsPayload(form.colors);
      if (colors.error) {
        setServerError(colors.error);
        return;
      }
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        is_active: form.is_active,
      };
      if (form.tag.trim()) payload.tag = form.tag.trim();
      if (form.logo_url.trim()) payload.logo_url = form.logo_url.trim();
      if (form.region.trim()) payload.region = form.region.trim();
      if (colors.value !== undefined) payload.colors = colors.value;

      setSaving(true);
      setServerError(null);
      try {
        if (editing) {
          await apiFetch<ApiResult<Team>>(`/api/v1/teams/${editing.id}`, {
            method: "PUT",
            body: payload,
            token,
          });
          show("success", `Team "${payload.name}" updated.`);
        } else {
          await apiFetch<ApiResult<Team>>("/api/v1/teams", {
            method: "POST",
            body: payload,
            token,
          });
          show("success", `Team "${payload.name}" created.`);
        }
        closeForm();
        await list.reload();
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
    [token, editing, list, closeForm, show, logout, router]
  );

  const toggleActive = useCallback(
    async (team: Team) => {
      if (!token) return;
      try {
        await apiFetch<ApiResult<Team>>(`/api/v1/teams/${team.id}`, {
          method: "PATCH",
          body: { is_active: !(team.is_active ?? true) },
          token,
        });
        show(
          "success",
          `Team "${team.name}" ${team.is_active ?? true ? "deactivated" : "activated"}.`
        );
        await list.reload();
      } catch (e) {
        show("error", `Could not update "${team.name}": ${apiErrorInfo(e).message}`);
      }
    },
    [token, list, show]
  );

  const deleteTeam = useCallback(async () => {
    if (!token || !deleting) return;
    setSaving(true);
    try {
      await apiFetch(`/api/v1/teams/${deleting.id}`, { method: "DELETE", token });
      show("success", `Team "${deleting.name}" deleted.`);
      setDeleting(null);
      await list.reload();
    } catch (e) {
      show("error", `Could not delete "${deleting.name}": ${apiErrorInfo(e).message}`);
    } finally {
      setSaving(false);
    }
  }, [token, deleting, list, show]);

  if (!isAuthed) return null;

  return (
    <AdminPageShell
      title="Teams"
      subtitle="Master data: teams that appear in standings, overlays and analysis"
    >
      <Toasts toasts={toasts} onDismiss={dismiss} />
      <SectionCard
        title="Teams"
        action={
          <ActionButton variant="primary" onClick={() => setCreating(true)}>
            + Add team
          </ActionButton>
        }
      >
        <div className="mb-4 max-w-md">
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="Search teams by name, tag or region…"
          />
        </div>
        <ListBody
          list={list}
          loadingLabel="Loading teams…"
          emptyMessage="No teams yet — add your first team."
        >
          {(items) =>
            rows.length === 0 ? (
              <p className="py-6 text-center font-mono text-sm text-zinc-500">
                {items.length === 0
                  ? "No teams yet — add your first team."
                  : "No teams match your search."}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-white/10 text-xs text-white/50">
                      <th className="pb-2 pr-4 font-medium">Team</th>
                      <th className="pb-2 pr-4 font-medium">Tag</th>
                      <th className="pb-2 pr-4 font-medium">Region</th>
                      <th className="pb-2 pr-4 font-medium">Status</th>
                      <th className="pb-2 font-medium">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((t) => (
                      <tr key={t.id} className="border-b border-white/5 align-middle">
                        <td className="py-2 pr-4">
                          <div className="flex items-center gap-3">
                            {t.logo_url ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={t.logo_url}
                                alt=""
                                className="h-8 w-8 shrink-0 rounded-full bg-white/10 object-cover"
                                loading="lazy"
                              />
                            ) : (
                              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/10 font-mono text-xs text-white/60">
                                {(t.name ?? "?").slice(0, 2).toUpperCase()}
                              </span>
                            )}
                            <span className="font-medium">{t.name}</span>
                          </div>
                        </td>
                        <td className="py-2 pr-4">
                          {t.tag ? <Badge tone="cyan">{t.tag}</Badge> : "—"}
                        </td>
                        <td className="py-2 pr-4 text-white/70">{t.region || "—"}</td>
                        <td className="py-2 pr-4">
                          <button
                            type="button"
                            onClick={() => void toggleActive(t)}
                            aria-label={`${t.is_active ?? true ? "Deactivate" : "Activate"} team ${t.name}`}
                            className="rounded-lg hover:opacity-80"
                            title="Toggle active"
                          >
                            <ActiveBadge active={t.is_active ?? true} />
                          </button>
                        </td>
                        <td className="py-2">
                          <div className="flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => {
                                setEditing(t);
                                setServerError(null);
                              }}
                              aria-label={`Edit team ${t.name}`}
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
                              onClick={() => setDeleting(t)}
                              aria-label={`Delete team ${t.name}`}
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

      {(creating || editing) && (
        <Modal title={creating ? "Add team" : `Edit team — ${editing?.name}`} onClose={closeForm}>
          <TeamForm
            initial={editing ? formFromTeam(editing) : EMPTY_FORM}
            saving={saving}
            serverError={serverError}
            onSubmit={(f) => void saveTeam(f)}
            onCancel={closeForm}
          />
        </Modal>
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete team "${deleting.name}"?`}
          body={
            <>
              <p>
                This permanently removes <strong className="text-white">{deleting.name}</strong>
                {deleting.tag ? ` (${deleting.tag})` : ""} and its player assignments from the
                master data. This cannot be undone.
              </p>
              <p className="mt-2 text-white/50">
                To confirm, type the team name exactly.
              </p>
            </>
          }
          confirmLabel="Delete team"
          requireText={deleting.name}
          onConfirm={() => void deleteTeam()}
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

export default function TeamsPage() {
  return (
    <RequireRole roles={["admin"]}>
      <TeamsPageInner />
    </RequireRole>
  );
}
