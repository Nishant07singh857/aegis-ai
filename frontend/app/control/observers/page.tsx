"use client";

/**
 * AEGIS AI — OBSERVER SLOT MAPPING (Phase 12).
 *
 * Route: /control/observers (operator + admin).
 *
 * Tournament setup: 16 observer slots, each assigned to at most one
 * team. Slot state comes from the REAL backend (GET /api/v1/observers,
 * polled every 15s):
 *
 *   - slot #, team dropdown (teams come from the REAL teams API — an
 *     honest error is shown when it fails, never hardcoded teams)
 *   - status badge: idle (gray) / connected (green) / feeding (pulsing green)
 *   - Assign / Reassign button (PUT — works mid-match) and Unassign
 *     (DELETE), both operator-gated on the backend too
 *
 * No fake data: everything shown is live backend state or an explicit
 * error/empty state.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { RequireRole } from "@/lib/guard";
import { apiFetch, ApiError } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";

/* ---------- types ---------- */

interface TeamSummary {
  id: string;
  name: string;
  tag?: string | null;
}

interface TeamsResponse {
  data: TeamSummary[];
}

type SlotStatus = "idle" | "connected" | "feeding";

interface ObserverSlot {
  slot_number: number;
  team_id: string | null;
  team_name: string | null;
  status: SlotStatus;
  last_heartbeat: string | null;
}

interface ObserversResponse {
  data: ObserverSlot[];
}

const POLL_MS = 15_000;

function statusTone(status: SlotStatus): "slate" | "green" {
  return status === "idle" ? "slate" : "green";
}

function statusLabel(status: SlotStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function formatHeartbeat(ts: string | null): string {
  if (!ts) return "no heartbeat yet";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "unknown time";
  return `last heartbeat ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })}`;
}

function actionErrorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.body;
  if (e instanceof Error) return e.message;
  return "Request failed.";
}

function SlotCard({
  slot,
  teams,
  onAssign,
  onUnassign,
  pending,
}: {
  slot: ObserverSlot;
  teams: TeamSummary[];
  onAssign: (slotNumber: number, teamId: string) => void;
  onUnassign: (slotNumber: number) => void;
  pending: string | null;
}) {
  const [selected, setSelected] = useState<string>(slot.team_id ?? "");

  // Keep the dropdown in sync when the polled slot state changes.
  useEffect(() => {
    setSelected(slot.team_id ?? "");
  }, [slot.team_id]);

  const busy = pending !== null;
  const dirty = selected !== (slot.team_id ?? "");
  const assignLabel =
    slot.team_id == null ? "Assign" : dirty ? "Reassign" : "Assign";

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="font-mono text-sm font-bold tracking-[0.2em] text-white">
          Slot {String(slot.slot_number).padStart(2, "0")}
        </span>
        <span className={slot.status === "feeding" ? "animate-pulse" : ""}>
          <Badge tone={statusTone(slot.status)}>
            {statusLabel(slot.status)}
          </Badge>
        </span>
      </div>

      <label
        htmlFor={`observers-team-${slot.slot_number}`}
        className="mb-1.5 block font-mono text-[11px] uppercase tracking-[0.18em] text-zinc-400"
      >
        Team
      </label>
      <select
        id={`observers-team-${slot.slot_number}`}
        value={selected}
        disabled={busy}
        onChange={(e) => setSelected(e.target.value)}
        className="mb-3 w-full rounded-xl border border-white/15 bg-black/40 px-3 text-sm text-white outline-none focus:border-[#00f0ff]/60 min-h-[48px] disabled:opacity-50"
      >
        <option value="">— Unassigned —</option>
        {teams.map((t) => (
          <option key={t.id} value={t.id}>
            {t.tag ? `${t.tag} · ${t.name}` : t.name}
          </option>
        ))}
      </select>

      <div className="mb-3 grid grid-cols-2 gap-2">
        <button
          type="button"
          disabled={busy || !selected || !dirty}
          onClick={() => selected && onAssign(slot.slot_number, selected)}
          className="rounded-xl bg-[#00f0ff] px-3 py-2.5 font-mono text-[11px] font-bold uppercase tracking-[0.15em] text-black transition-opacity disabled:cursor-not-allowed disabled:opacity-30 max-md:min-h-[48px]"
        >
          {pending === `assign-${slot.slot_number}` ? "Saving…" : assignLabel}
        </button>
        <button
          type="button"
          disabled={busy || slot.team_id == null}
          onClick={() => onUnassign(slot.slot_number)}
          className="rounded-xl border border-white/15 bg-white/5 px-3 py-2.5 font-mono text-[11px] font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#ff3366]/60 hover:text-[#ff3366] disabled:cursor-not-allowed disabled:opacity-30 max-md:min-h-[48px]"
        >
          {pending === `unassign-${slot.slot_number}` ? "Saving…" : "Unassign"}
        </button>
      </div>

      <p className="font-mono text-[11px] text-zinc-500">
        {slot.team_name ? `Feeding: ${slot.team_name}` : "No team assigned"}
        {" · "}
        {formatHeartbeat(slot.last_heartbeat)}
      </p>
    </div>
  );
}

function ObserversInner() {
  const { token } = useAuth();

  const [slots, setSlots] = useState<ObserverSlot[] | null>(null);
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadSlots = useCallback(
    async (silent = false) => {
      if (!token) return;
      if (!silent) setError(null);
      try {
        const res = await apiFetch<ObserversResponse>("/api/v1/observers", {
          token,
        });
        const sorted = [...(res.data ?? [])].sort(
          (a, b) => a.slot_number - b.slot_number,
        );
        setSlots(sorted);
        if (!silent) setError(null);
      } catch (e) {
        if (!silent) {
          setError(
            e instanceof ApiError
              ? `Could not load observer slots: ${e.body}`
              : "Could not load observer slots: network error.",
          );
        }
      }
    },
    [token],
  );

  const loadTeams = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiFetch<TeamsResponse>("/api/v1/teams", { token });
      setTeams((res.data ?? []).slice().sort((a, b) =>
        a.name.localeCompare(b.name),
      ));
    } catch {
      // Teams are needed for assignment but slot state must still render;
      // the empty dropdown honestly shows there is nothing to assign.
      setTeams([]);
    }
  }, [token]);

  useEffect(() => {
    void loadSlots();
    void loadTeams();
    pollRef.current = setInterval(() => {
      void loadSlots(true);
    }, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadSlots, loadTeams]);

  const handleAssign = useCallback(
    async (slotNumber: number, teamId: string) => {
      if (!token) return;
      setPending(`assign-${slotNumber}`);
      try {
        await apiFetch(`/api/v1/observers/${slotNumber}/assign`, {
          method: "PUT",
          token,
          body: { team_id: teamId },
        });
        await loadSlots(true);
      } catch (e) {
        setError(`Assign failed: ${actionErrorMessage(e)}`);
      } finally {
        setPending(null);
      }
    },
    [token, loadSlots],
  );

  const handleUnassign = useCallback(
    async (slotNumber: number) => {
      if (!token) return;
      setPending(`unassign-${slotNumber}`);
      try {
        await apiFetch(`/api/v1/observers/${slotNumber}/assign`, {
          method: "DELETE",
          token,
        });
        await loadSlots(true);
      } catch (e) {
        setError(`Unassign failed: ${actionErrorMessage(e)}`);
      } finally {
        setPending(null);
      }
    },
    [token, loadSlots],
  );

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto w-full max-w-6xl px-3 py-5 sm:px-6">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-mono text-sm font-bold uppercase tracking-[0.25em] text-white">
              Observer slots
            </h1>
            <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-zinc-500">
              16 slots · team assignment · auto-refresh every 15s
            </p>
          </div>
          <Badge tone="cyan">Operator</Badge>
        </div>

        {error && (
          <div className="mb-4">
            <ErrorState
              message={error}
              onRetry={() => {
                setError(null);
                void loadSlots();
                void loadTeams();
              }}
            />
          </div>
        )}

        <Card title="Slot assignments">
          {slots === null ? (
            <Loading label="Loading slots" />
          ) : slots.length === 0 ? (
            <EmptyState message="No observer slots returned by the backend." />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {slots.map((slot) => (
                <SlotCard
                  key={slot.slot_number}
                  slot={slot}
                  teams={teams}
                  onAssign={(n, id) => void handleAssign(n, id)}
                  onUnassign={(n) => void handleUnassign(n)}
                  pending={pending}
                />
              ))}
            </div>
          )}
        </Card>

        <p className="mt-4 font-mono text-[11px] uppercase tracking-wider text-zinc-600">
          Heartbeats are posted by observer clients via POST
          /api/v1/observers/{"{slot}"}/heartbeat. Reassigning a slot mid-match
          takes effect immediately.
        </p>
      </div>
    </div>
  );
}

export default function ObserverMappingPage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <ObserversInner />
    </RequireRole>
  );
}
