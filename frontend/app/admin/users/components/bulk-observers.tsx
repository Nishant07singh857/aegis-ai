"use client";

/**
 * Bulk-observer creator for /admin/users.
 *
 * Tournament select + count (default 16) → POST /api/v1/users/bulk-observers
 * → printable credential sheet: username | password | team | slot, with
 * per-row copy, "copy all", and a print stylesheet.
 *
 * Security: passwords are returned once by the backend. The sheet lives only
 * in component state and is cleared when the user closes it or leaves the
 * route — never persisted.
 */

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import {
  ActionButton,
  Field,
  FieldInput,
  FieldSelect,
  apiErrorInfo,
  type ApiResult,
} from "@/components/admin-crud";
import { Badge } from "@/components/ui";
import {
  slotLabel,
  type ObserverCredential,
  type TournamentOption,
} from "./types";

interface BulkResponse {
  credentials: ObserverCredential[];
}

function credentialsToText(creds: ObserverCredential[]): string {
  return creds
    .map(
      (c) =>
        `${c.username}\t${c.password}\t${c.team_name ?? "—"}\t${c.slot_number ?? "—"}`
    )
    .join("\n");
}

export function BulkObservers({
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
  const [tournamentId, setTournamentId] = useState(
    tournaments[0]?.id ?? ""
  );
  const [count, setCount] = useState("16");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creds, setCreds] = useState<ObserverCredential[] | null>(null);
  const [copiedRow, setCopiedRow] = useState<string | null>(null);

  // Default to the first tournament once the list loads.
  useEffect(() => {
    if (!tournamentId && tournaments.length > 0) {
      setTournamentId(tournaments[0].id);
    }
  }, [tournaments, tournamentId]);

  const create = async () => {
    const n = parseInt(count, 10);
    if (!tournamentId) {
      setError("Select a tournament first.");
      return;
    }
    if (!Number.isFinite(n) || n < 1 || n > 64) {
      setError("Count must be between 1 and 64.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch<ApiResult<BulkResponse>>(
        "/api/v1/users/bulk-observers",
        {
          method: "POST",
          body: { tournament_id: tournamentId, count: n },
          token,
        }
      );
      // Accept {data:{credentials}}, {credentials}, or a bare array.
      const unwrapped: unknown =
        (res as { data?: unknown } | null)?.data ?? res;
      const rows: ObserverCredential[] =
        (unwrapped as { credentials?: ObserverCredential[] }).credentials ??
        (Array.isArray(unwrapped)
          ? (unwrapped as ObserverCredential[])
          : []);
      if (!rows.length) {
        setError("The server returned no credentials. Nothing was created.");
        return;
      }
      setCreds(rows);
      notify("success", `${rows.length} observer accounts created.`);
      onDone();
    } catch (e) {
      setError(apiErrorInfo(e).message);
    } finally {
      setBusy(false);
    }
  };

  const copyText = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedRow(label);
      window.setTimeout(() => setCopiedRow((c) => (c === label ? null : c)), 1500);
    } catch {
      setError("Copy failed — select the text manually.");
    }
  };

  if (creds) {
    return (
      <div>
        {/* Print-only CSS: when printing, show just the credential sheet. */}
        <style>{`
          @media print {
            body * { visibility: hidden; }
            #credential-sheet, #credential-sheet * { visibility: visible; }
            #credential-sheet { position: absolute; left: 0; top: 0; width: 100%; background: #fff; color: #000; }
            #credential-sheet table { width: 100%; border-collapse: collapse; }
            #credential-sheet th, #credential-sheet td { border: 1px solid #000; padding: 6px 10px; text-align: left; }
            #credential-sheet .no-print { display: none !important; }
          }
        `}</style>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <p className="mr-auto text-sm text-amber-300">
            ⚠️ Passwords are shown only once — print or copy them now.
          </p>
          <ActionButton
            variant="ghost"
            className="no-print"
            onClick={() => void copyText(credentialsToText(creds), "all")}
          >
            {copiedRow === "all" ? "Copied ✓" : "Copy all"}
          </ActionButton>
          <ActionButton
            variant="ghost"
            className="no-print"
            onClick={() => window.print()}
          >
            Print sheet
          </ActionButton>
          <ActionButton
            variant="ghost"
            className="no-print"
            onClick={() => setCreds(null)}
          >
            Done
          </ActionButton>
        </div>
        <div id="credential-sheet" className="overflow-x-auto rounded-lg border border-white/10">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-white/10 text-xs text-white/50">
                <th className="p-2 pr-4 font-medium">Username</th>
                <th className="p-2 pr-4 font-medium">Password</th>
                <th className="p-2 pr-4 font-medium">Team</th>
                <th className="p-2 pr-4 font-medium">Slot</th>
                <th className="no-print p-2 font-medium">
                  <span className="sr-only">Copy</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {creds.map((c) => (
                <tr key={c.username} className="border-b border-white/5">
                  <td className="p-2 pr-4 font-mono">{c.username}</td>
                  <td className="p-2 pr-4 font-mono text-[#00f0ff]">{c.password}</td>
                  <td className="p-2 pr-4">{c.team_name ?? "—"}</td>
                  <td className="p-2 pr-4">
                    <Badge tone={c.slot_number != null ? "cyan" : "slate"}>
                      {slotLabel(c.slot_number)}
                    </Badge>
                  </td>
                  <td className="no-print p-2">
                    <button
                      type="button"
                      onClick={() =>
                        void copyText(
                          `${c.username}\t${c.password}\t${c.team_name ?? "—"}\t${c.slot_number ?? "—"}`,
                          c.username
                        )
                      }
                      aria-label={`Copy credentials for ${c.username}`}
                      className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg px-2 text-xs text-white/60 hover:bg-white/10 hover:text-[#00f0ff]"
                    >
                      {copiedRow === c.username ? "✓" : "Copy"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Tournament" htmlFor="bulk-tournament" required>
          <FieldSelect
            id="bulk-tournament"
            value={tournamentId}
            onChange={setTournamentId}
            options={tournaments.map((t) => ({ value: t.id, label: t.name }))}
            allowEmpty
            emptyLabel={
              tournaments.length === 0 ? "No tournaments yet" : "Select tournament…"
            }
          />
        </Field>
        <Field label="Observer count" htmlFor="bulk-count" required>
          <FieldInput
            id="bulk-count"
            type="number"
            value={count}
            onChange={setCount}
            min={1}
            max={64}
            step={1}
          />
        </Field>
      </div>
      <p className="text-xs text-white/40">
        One observer account per slot, auto-named and auto-assigned to teams by
        slot order. Passwords are generated server-side and shown once on the
        credential sheet.
      </p>
      {error && (
        <p className="text-sm text-[#ff99ab]" role="alert">
          {error}
        </p>
      )}
      <div>
        <ActionButton variant="primary" onClick={() => void create()} disabled={busy}>
          {busy ? "Creating…" : `Create ${count || "…"} observers`}
        </ActionButton>
      </div>
    </div>
  );
}
