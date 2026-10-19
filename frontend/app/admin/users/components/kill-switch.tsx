"use client";

/**
 * Tournament "kill switch" for /admin/users.
 *
 * Select a tournament → big red button → type-to-confirm (the tournament
 * name) → POST /api/v1/users/tournament/{id}/deactivate →
 * "{N} accounts deactivated".
 *
 * Deactivation is soft: accounts are disabled, not deleted, so the
 * tournament can be re-opened by re-activating individual users.
 */

import { useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import {
  ActionButton,
  ConfirmDialog,
  Field,
  FieldSelect,
  apiErrorInfo,
  type ApiResult,
} from "@/components/admin-crud";
import { Badge } from "@/components/ui";
import type { TournamentOption } from "./types";

export function KillSwitch({
  token,
  tournaments,
  onDone,
  notify,
}: {
  token: string;
  tournaments: TournamentOption[];
  onDone: () => void;
  notify: (kind: "success" | "error", msg: string) => void;
}) {
  const [tournamentId, setTournamentId] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<number | null>(null);

  const selected = useMemo(
    () => tournaments.find((t) => t.id === tournamentId) ?? null,
    [tournaments, tournamentId]
  );

  const deactivate = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch<ApiResult<{ deactivated?: number }>>(
        `/api/v1/users/tournament/${selected.id}/deactivate`,
        { method: "POST", token }
      );
      const body = (
        res && typeof res === "object" && "data" in res
          ? (res as { data: { deactivated?: number } }).data
          : (res as { deactivated?: number })
      ) ?? {};
      const n = typeof body.deactivated === "number" ? body.deactivated : 0;
      setResult(n);
      setConfirming(false);
      notify("success", `${n} accounts deactivated for "${selected.name}".`);
      onDone();
    } catch (e) {
      setError(apiErrorInfo(e).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="max-w-md">
        <Field label="Tournament to end" htmlFor="kill-tournament" required>
          <FieldSelect
            id="kill-tournament"
            value={tournamentId}
            onChange={(v) => {
              setTournamentId(v);
              setResult(null);
              setError(null);
            }}
            options={tournaments.map((t) => ({ value: t.id, label: t.name }))}
            allowEmpty
            emptyLabel={
              tournaments.length === 0 ? "No tournaments yet" : "Select tournament…"
            }
          />
        </Field>
      </div>

      <div className="rounded-lg border border-[#ff3366]/30 bg-[#ff3366]/5 p-4">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="font-semibold text-[#ff99ab]">End tournament</span>
          {selected?.status && <Badge tone="slate">{selected.status}</Badge>}
        </div>
        <p className="mb-4 text-sm text-white/60">
          Deactivates <strong className="text-white">every user account</strong>{" "}
          scoped to {selected ? <strong className="text-white">“{selected.name}”</strong> : "the selected tournament"} —
          spotters, observers, directors and operators. Accounts are disabled,
          not deleted, and can be re-activated later. Admin accounts are not
          affected.
        </p>
        {error && (
          <p className="mb-3 text-sm text-[#ff99ab]" role="alert">
            {error}
          </p>
        )}
        {result != null && (
          <p className="mb-3 text-sm text-green-400" role="status">
            ✅ {result} account{result === 1 ? "" : "s"} deactivated.
          </p>
        )}
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={!selected || busy}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border border-[#ff3366] bg-[#ff3366]/15 px-6 py-3 font-mono text-sm uppercase tracking-widest text-[#ff99ab] transition-colors hover:bg-[#ff3366]/30 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Working…" : "End tournament"}
        </button>
      </div>

      {confirming && selected && (
        <ConfirmDialog
          title={`End tournament "${selected.name}"?`}
          body={
            <>
              <p>
                This deactivates <strong className="text-white">all user accounts</strong>{" "}
                scoped to <strong className="text-white">{selected.name}</strong> —
                they will no longer be able to sign in.
              </p>
              <p className="mt-2 text-white/50">
                To confirm, type the tournament name exactly.
              </p>
            </>
          }
          confirmLabel="Deactivate accounts"
          requireText={selected.name}
          onConfirm={() => void deactivate()}
          onCancel={() => setConfirming(false)}
          busy={busy}
        />
      )}
    </div>
  );
}
