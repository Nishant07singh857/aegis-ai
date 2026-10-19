"use client";

/**
 * AEGIS AI — shared master-data CRUD primitives for the admin pages.
 *
 * Used by /admin/teams, /admin/players and /admin/tournament. All Tailwind,
 * matching the dark esports-console theme. Text/SVG only — no emojis.
 *
 * Conventions:
 * - Backend returns a `{ data: ... }` envelope on reads and writes; `unwrap`
 *   tolerates a bare payload as well so the pages work either way.
 * - No mock, seed or demo data anywhere: empty lists render honest
 *   "nothing yet" states.
 * - Every destructive action is gated behind a confirmation dialog.
 * - Mutations refetch the list and surface success/error feedback via toast.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";

/* ------------------------------------------------------------------ types */

export interface Team {
  id: string;
  name: string;
  tag?: string | null;
  logo_url?: string | null;
  colors?: unknown;
  region?: string | null;
  is_active?: boolean | null;
}

export interface Player {
  id: string;
  team_id?: string | null;
  name: string;
  ign?: string | null;
  role?: string | null;
  avatar_url?: string | null;
  is_active?: boolean | null;
}

export interface Tournament {
  id: string;
  name: string;
  slug?: string | null;
  status?: string | null;
  points_per_kill?: number | null;
  placement_points?: unknown;
  rules?: unknown;
  is_active?: boolean | null;
}

export type ApiResult<T> = { data: T } | T;

/** Pull the payload out of a `{ data }` envelope (tolerates bare payloads). */
export function unwrap<T>(res: ApiResult<T> | null | undefined): T | null {
  if (res === null || res === undefined) return null;
  if (typeof res === "object" && "data" in (res as Record<string, unknown>)) {
    return (res as { data: T }).data;
  }
  return res as T;
}

/** Normalize a read result to an array, tolerating envelope and bare shapes. */
export function unwrapList<T>(res: ApiResult<T[]> | null | undefined): T[] {
  const payload = unwrap(res);
  return Array.isArray(payload) ? payload : [];
}

/* --------------------------------------------------------------- errors */

export interface ApiProblem {
  status?: number;
  message: string;
}

export function apiErrorInfo(e: unknown): ApiProblem {
  if (e instanceof ApiError) {
    return { status: e.status, message: `(${e.status}) ${e.body || "Request failed"}` };
  }
  if (e instanceof Error) return { message: e.message };
  return { message: "Request failed" };
}

/* ----------------------------------------------------------------- toast */

export interface Toast {
  id: number;
  kind: "success" | "error";
  message: string;
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (kind: Toast["kind"], message: string) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, kind, message }]);
      window.setTimeout(() => dismiss(id), 4500);
    },
    [dismiss]
  );

  return { toasts, show, dismiss };
}

export function Toasts({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div
      className="pointer-events-none fixed inset-x-0 bottom-4 z-[70] mx-auto flex w-full max-w-md flex-col gap-2 px-4"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onDismiss(t.id)}
          className={`pointer-events-auto min-h-[44px] rounded-xl border px-4 py-3 text-left text-sm shadow-lg backdrop-blur transition-colors ${
            t.kind === "success"
              ? "border-emerald-300/40 bg-emerald-500/15 text-emerald-200"
              : "border-[#ff3366]/40 bg-[#ff3366]/15 text-[#ffb3c2]"
          }`}
        >
          <span className="mr-2 font-mono text-[11px] uppercase tracking-wider">
            {t.kind === "success" ? "Saved" : "Error"}
          </span>
          {t.message}
        </button>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------------- modal */

export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm sm:items-center sm:p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className={`max-h-[92vh] w-full overflow-y-auto rounded-t-2xl border border-white/10 bg-[#111118] p-5 sm:rounded-2xl ${
          wide ? "max-w-3xl" : "max-w-lg"
        }`}
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-white/60 hover:bg-white/10 hover:text-white"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M5 5l10 10M15 5L5 15"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- confirm dlg */

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  requireText,
  requireHint,
  onConfirm,
  onCancel,
  busy = false,
}: {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  /** When set, the user must type this exact text to enable the button. */
  requireText?: string;
  requireHint?: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const [typed, setTyped] = useState("");
  const canConfirm = !requireText || typed.trim() === requireText;

  return (
    <Modal title={title} onClose={onCancel}>
      <div className="text-sm text-white/75">{body}</div>
      {requireText && (
        <div className="mt-4">
          <label
            htmlFor="confirm-type"
            className="mb-1 block font-mono text-xs uppercase tracking-wider text-white/50"
          >
            {requireHint ?? `Type "${requireText}" to confirm`}
          </label>
          <FieldInput
            id="confirm-type"
            value={typed}
            onChange={(v) => setTyped(v)}
            placeholder={requireText}
            autoComplete="off"
          />
        </div>
      )}
      <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <ActionButton variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </ActionButton>
        <ActionButton
          variant="danger"
          onClick={onConfirm}
          disabled={!canConfirm || busy}
        >
          {busy ? "Deleting…" : confirmLabel}
        </ActionButton>
      </div>
    </Modal>
  );
}

/* -------------------------------------------------------------- controls */

const INPUT_CLASSES =
  "w-full min-h-[44px] rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-sm text-white placeholder:text-white/30 focus:border-[#00f0ff]/60 focus:outline-none focus:ring-1 focus:ring-[#00f0ff]/40";

export function FieldInput({
  id,
  value,
  onChange,
  ...rest
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  return (
    <input
      id={id}
      className={INPUT_CLASSES}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      {...rest}
    />
  );
}

export function Field({
  label,
  htmlFor,
  required = false,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  required?: boolean;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="mb-1 block font-mono text-xs uppercase tracking-wider text-white/50"
      >
        {label}
        {required && <span className="text-[#ff3366]"> *</span>}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-white/40">{hint}</p>}
    </div>
  );
}

export function FieldSelect({
  id,
  value,
  onChange,
  options,
  allowEmpty = false,
  emptyLabel = "—",
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  allowEmpty?: boolean;
  emptyLabel?: string;
}) {
  return (
    <select
      id={id}
      className={`${INPUT_CLASSES} [&>option]:bg-[#111118]`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {allowEmpty && <option value="">{emptyLabel}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function FieldTextArea({
  id,
  value,
  onChange,
  rows = 5,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  return (
    <textarea
      id={id}
      rows={rows}
      placeholder={placeholder}
      className={`${INPUT_CLASSES} min-h-[44px] font-mono text-xs`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      spellCheck={false}
    />
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className="flex min-h-[44px] min-w-[44px] items-center gap-3"
    >
      <span
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
          checked ? "bg-[#00f0ff]/70" : "bg-white/15"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
            checked ? "translate-x-6" : "translate-x-1"
          }`}
        />
      </span>
      <span className="text-sm text-white/70">
        {checked ? "Active" : "Inactive"}
      </span>
    </button>
  );
}

type ButtonVariant = "primary" | "danger" | "ghost";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary:
    "border-[#00f0ff]/50 bg-[#00f0ff]/15 text-[#00f0ff] hover:bg-[#00f0ff]/25 disabled:opacity-40",
  danger:
    "border-[#ff3366]/50 bg-[#ff3366]/15 text-[#ff99ab] hover:bg-[#ff3366]/25 disabled:opacity-40",
  ghost: "border-white/15 bg-white/5 text-zinc-200 hover:border-white/30 disabled:opacity-40",
};

export function ActionButton({
  variant = "ghost",
  children,
  className = "",
  ...rest
}: {
  variant?: ButtonVariant;
  children: ReactNode;
  className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={`inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border px-4 py-2 font-mono text-xs uppercase tracking-wider transition-colors ${BUTTON_STYLES[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <div className="relative">
      <svg
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/40"
        viewBox="0 0 20 20"
        fill="none"
        aria-hidden="true"
      >
        <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="2" />
        <path d="M13.5 13.5L17 17" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <input
        type="search"
        aria-label="Search"
        className={`${INPUT_CLASSES} pl-9`}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

/* ----------------------------------------------------------------- list */

export interface AdminListState<T> {
  items: T[] | null;
  error: ApiProblem | null;
  reload: () => Promise<void>;
}

/** Load a master-data collection (honest empty on success with no rows). */
export function useAdminList<T>(
  path: string | null,
  token: string | null
): AdminListState<T> {
  const [items, setItems] = useState<T[] | null>(null);
  const [error, setError] = useState<ApiProblem | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!path || !token) return;
    let cancelled = false;
    (async () => {
      setError(null);
      try {
        const res = await apiFetch<ApiResult<T[]>>(path, { token });
        if (!cancelled) setItems(unwrapList(res));
      } catch (e) {
        if (!cancelled) {
          setError(apiErrorInfo(e));
          setItems(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [path, token, nonce]);

  const reload = useCallback(async () => {
    setNonce((n) => n + 1);
  }, []);

  return { items, error, reload };
}

/** Standard loading/error/empty/list rendering for a master-data list. */
export function ListBody<T>({
  list,
  emptyMessage,
  loadingLabel,
  children,
}: {
  list: AdminListState<T>;
  emptyMessage: string;
  loadingLabel: string;
  children: (items: T[]) => ReactNode;
}) {
  if (list.error) {
    return (
      <ErrorState
        message={list.error.message}
        onRetry={() => void list.reload()}
      />
    );
  }
  if (list.items === null) return <Loading label={loadingLabel} />;
  if (list.items.length === 0) return <EmptyState message={emptyMessage} />;
  return <>{children(list.items)}</>;
}

/** JSON validation helper for the JSON-editor fields. */
export function parseJsonField(
  raw: string,
  fieldName: string
): { ok: true; value: unknown } | { ok: false; error: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, value: {} };
  try {
    return { ok: true, value: JSON.parse(trimmed) };
  } catch (e) {
    return {
      ok: false,
      error: `${fieldName} is not valid JSON: ${e instanceof Error ? e.message : "parse error"}`,
    };
  }
}

/* ------------------------------------------------------------------ page */

export function AdminPageShell({
  title,
  subtitle,
  backHref = "/admin",
  children,
}: {
  title: string;
  subtitle: string;
  backHref?: string;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">{title}</h1>
            <p className="text-sm text-white/50">{subtitle}</p>
          </div>
          <a
            href={backHref}
            className="inline-flex min-h-[44px] items-center text-sm text-white/60 hover:text-[#00f0ff]"
          >
            ← Admin
          </a>
        </div>
        <div className="flex flex-col gap-4">{children}</div>
      </div>
    </div>
  );
}

export function ActiveBadge({ active }: { active: boolean }) {
  return <Badge tone={active ? "green" : "slate"}>{active ? "Active" : "Inactive"}</Badge>;
}

/** Card wrapper used by the inner pages of each admin route. */
export function SectionCard({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card title={title} action={action}>
      {children}
    </Card>
  );
}
