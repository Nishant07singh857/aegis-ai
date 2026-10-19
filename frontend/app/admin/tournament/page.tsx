"use client";

/**
 * /admin/tournament — master-data management for tournaments.
 *
 * Tournament contract (fixed): GET/POST /api/v1/tournament,
 * GET/PUT/PATCH/DELETE /api/v1/tournament/{id},
 * POST /api/v1/tournament/{id}/activate, `{ data }` envelope.
 * Fields: id, name, slug, status, points_per_kill, placement_points (object),
 * rules (object, incl. match_point thresholds), is_active.
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
  FieldTextArea,
  ListBody,
  Modal,
  SearchBox,
  SectionCard,
  Toasts,
  apiErrorInfo,
  parseJsonField,
  useAdminList,
  useToast,
  type ApiResult,
  type Tournament,
} from "../../components/admin-crud";

function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

interface TournamentFormState {
  name: string;
  slug: string;
  status: string;
  points_per_kill: string;
  placement_points: string;
  rules: string;
}

const EMPTY_FORM: TournamentFormState = {
  name: "",
  slug: "",
  status: "",
  points_per_kill: "1",
  placement_points: '{\n  "1": 10\n}',
  rules: '{\n  "match_point": {\n    "enabled": false,\n    "threshold": 55\n  }\n}',
};

function formFromTournament(t: Tournament): TournamentFormState {
  return {
    name: t.name ?? "",
    slug: t.slug ?? "",
    status: t.status ?? "",
    points_per_kill:
      t.points_per_kill === null || t.points_per_kill === undefined
        ? ""
        : String(t.points_per_kill),
    placement_points: prettyJson(t.placement_points),
    rules: prettyJson(t.rules),
  };
}

function TournamentForm({
  initial,
  saving,
  serverError,
  onSubmit,
  onCancel,
}: {
  initial: TournamentFormState;
  saving: boolean;
  serverError: string | null;
  onSubmit: (form: TournamentFormState) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<TournamentFormState>(initial);
  const [localError, setLocalError] = useState<string | null>(null);
  const set = useCallback(
    <K extends keyof TournamentFormState>(key: K, value: TournamentFormState[K]) => {
      setForm((f) => ({ ...f, [key]: value }));
    },
    []
  );

  const submit = (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!form.name.trim()) {
      setLocalError("Tournament name is required.");
      return;
    }
    if (form.points_per_kill.trim() !== "" && Number.isNaN(Number(form.points_per_kill))) {
      setLocalError("Points per kill must be a number.");
      return;
    }
    const pp = parseJsonField(form.placement_points, "Placement points");
    if (!pp.ok) {
      setLocalError(pp.error);
      return;
    }
    const rules = parseJsonField(form.rules, "Rules");
    if (!rules.ok) {
      setLocalError(rules.error);
      return;
    }
    setLocalError(null);
    onSubmit(form);
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field label="Tournament name" htmlFor="tour-name" required>
        <FieldInput
          id="tour-name"
          value={form.name}
          onChange={(v) => set("name", v)}
          placeholder="e.g. AEGIS Invitational 2026"
          autoFocus
        />
      </Field>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Field label="Slug" htmlFor="tour-slug" hint="URL-friendly identifier.">
          <FieldInput
            id="tour-slug"
            value={form.slug}
            onChange={(v) => set("slug", v)}
            placeholder="e.g. aegis-inv-2026"
          />
        </Field>
        <Field label="Status" htmlFor="tour-status" hint='e.g. "upcoming", "live", "completed".'>
          <FieldInput
            id="tour-status"
            value={form.status}
            onChange={(v) => set("status", v)}
            placeholder="e.g. upcoming"
          />
        </Field>
        <Field label="Points per kill" htmlFor="tour-ppk">
          <FieldInput
            id="tour-ppk"
            type="number"
            inputMode="numeric"
            min="0"
            step="1"
            value={form.points_per_kill}
            onChange={(v) => set("points_per_kill", v)}
            placeholder="1"
          />
        </Field>
      </div>
      <Field
        label="Placement points"
        htmlFor="tour-placement"
        hint="JSON object mapping placement to points, e.g. {&quot;1&quot;: 10, &quot;2&quot;: 6}. Empty means none."
      >
        <FieldTextArea
          id="tour-placement"
          value={form.placement_points}
          onChange={(v) => set("placement_points", v)}
          rows={4}
          placeholder='{ "1": 10, "2": 6 }'
        />
      </Field>
      <Field
        label="Rules"
        htmlFor="tour-rules"
        hint="JSON object. Match Point / Smash format is expressed under the match_point key: { &quot;match_point&quot;: { &quot;enabled&quot;: true, &quot;threshold&quot;: 55, &quot;name&quot;: &quot;Match Point&quot; } }. Extra keys are allowed."
      >
        <FieldTextArea
          id="tour-rules"
          value={form.rules}
          onChange={(v) => set("rules", v)}
          rows={7}
          placeholder='{ "match_point": { "enabled": false, "threshold": 55 } }'
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
          {saving ? "Saving…" : "Save tournament"}
        </ActionButton>
      </div>
    </form>
  );
}

function TournamentPageInner() {
  const router = useRouter();
  const { token, isAuthed, logout } = useAuth();
  const { toasts, show, dismiss } = useToast();

  const list = useAdminList<Tournament>("/api/v1/tournament", token);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Tournament | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Tournament | null>(null);
  const [saving, setSaving] = useState(false);
  const [activatingId, setActivatingId] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthed) router.replace("/login");
  }, [isAuthed, router]);

  const rows = useMemo(() => {
    const items = list.items ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((t) =>
      [t.name, t.slug, t.status].some((v) => (v ?? "").toLowerCase().includes(q))
    );
  }, [list.items, query]);

  const closeForm = useCallback(() => {
    setEditing(null);
    setCreating(false);
    setServerError(null);
  }, []);

  const saveTournament = useCallback(
    async (form: TournamentFormState) => {
      if (!token) return;
      const pp = parseJsonField(form.placement_points, "Placement points");
      const rules = parseJsonField(form.rules, "Rules");
      if (!pp.ok || !rules.ok) {
        setServerError(!pp.ok ? pp.error : (rules as { error: string }).error);
        return;
      }
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        placement_points: pp.value,
        rules: rules.value,
      };
      if (form.slug.trim()) payload.slug = form.slug.trim();
      if (form.status.trim()) payload.status = form.status.trim();
      if (form.points_per_kill.trim() !== "") {
        payload.points_per_kill = Number(form.points_per_kill);
      }

      setSaving(true);
      setServerError(null);
      try {
        if (editing) {
          await apiFetch<ApiResult<Tournament>>(`/api/v1/tournament/${editing.id}`, {
            method: "PUT",
            body: payload,
            token,
          });
          show("success", `Tournament "${payload.name}" updated.`);
        } else {
          await apiFetch<ApiResult<Tournament>>("/api/v1/tournament", {
            method: "POST",
            body: payload,
            token,
          });
          show("success", `Tournament "${payload.name}" created.`);
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

  const activateTournament = useCallback(
    async (t: Tournament) => {
      if (!token) return;
      setActivatingId(t.id);
      try {
        await apiFetch<ApiResult<Tournament>>(`/api/v1/tournament/${t.id}/activate`, {
          method: "POST",
          token,
        });
        show("success", `Tournament "${t.name}" is now active.`);
        await list.reload();
      } catch (e) {
        show("error", `Could not activate "${t.name}": ${apiErrorInfo(e).message}`);
      } finally {
        setActivatingId(null);
      }
    },
    [token, list, show]
  );

  const deleteTournament = useCallback(async () => {
    if (!token || !deleting) return;
    setSaving(true);
    try {
      await apiFetch(`/api/v1/tournament/${deleting.id}`, {
        method: "DELETE",
        token,
      });
      show("success", `Tournament "${deleting.name}" deleted.`);
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
      title="Tournaments"
      subtitle="Master data: tournaments, scoring rules and the active event"
    >
      <Toasts toasts={toasts} onDismiss={dismiss} />
      <SectionCard
        title="Tournaments"
        action={
          <ActionButton variant="primary" onClick={() => setCreating(true)}>
            + Add tournament
          </ActionButton>
        }
      >
        <div className="mb-4 max-w-md">
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="Search tournaments by name, slug or status…"
          />
        </div>
        <ListBody
          list={list}
          loadingLabel="Loading tournaments…"
          emptyMessage="No tournaments yet — add your first tournament."
        >
          {(items) =>
            rows.length === 0 ? (
              <p className="py-6 text-center font-mono text-sm text-zinc-500">
                {items.length === 0
                  ? "No tournaments yet — add your first tournament."
                  : "No tournaments match your search."}
              </p>
            ) : (
              <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {rows.map((t) => (
                  <li
                    key={t.id}
                    className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-base font-semibold text-white">
                          {t.name}
                        </div>
                        <div className="mt-0.5 text-xs text-white/40">
                          {t.slug || "no slug"}
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        {(t.is_active ?? false) && <Badge tone="green">Active</Badge>}
                        {t.status && <Badge tone="slate">{t.status}</Badge>}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-white/50">
                      <span>
                        Points/kill:{" "}
                        <span className="text-[#00f0ff]">
                          {t.points_per_kill ?? "—"}
                        </span>
                      </span>
                      <span>
                        Status:{" "}
                        <ActiveBadge active={t.is_active ?? false} />
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {!(t.is_active ?? false) && (
                        <ActionButton
                          variant="primary"
                          onClick={() => void activateTournament(t)}
                          disabled={activatingId !== null}
                          className="flex-1 sm:flex-none"
                        >
                          {activatingId === t.id ? "Activating…" : "Activate"}
                        </ActionButton>
                      )}
                      <ActionButton
                        variant="ghost"
                        onClick={() => {
                          setEditing(t);
                          setServerError(null);
                        }}
                        className="flex-1 sm:flex-none"
                      >
                        Edit
                      </ActionButton>
                      <ActionButton
                        variant="danger"
                        onClick={() => setDeleting(t)}
                        className="flex-1 sm:flex-none"
                      >
                        Delete
                      </ActionButton>
                    </div>
                  </li>
                ))}
              </ul>
            )
          }
        </ListBody>
      </SectionCard>

      {(creating || editing) && (
        <Modal
          title={creating ? "Add tournament" : `Edit tournament — ${editing?.name}`}
          onClose={closeForm}
          wide
        >
          <TournamentForm
            initial={editing ? formFromTournament(editing) : EMPTY_FORM}
            saving={saving}
            serverError={serverError}
            onSubmit={(f) => void saveTournament(f)}
            onCancel={closeForm}
          />
        </Modal>
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete tournament "${deleting.name}"?`}
          body={
            <p>
              This permanently removes{" "}
              <strong className="text-white">{deleting.name}</strong> and its scoring
              configuration from the master data. This cannot be undone.
            </p>
          }
          confirmLabel="Delete tournament"
          onConfirm={() => void deleteTournament()}
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

export default function TournamentPage() {
  return (
    <RequireRole roles={["admin"]}>
      <TournamentPageInner />
    </RequireRole>
  );
}
