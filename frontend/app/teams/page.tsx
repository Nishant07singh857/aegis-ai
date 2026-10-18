"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { apiFetch } from "../lib/api";
import { useAuth } from "../lib/auth";
import { RequireRole } from "../lib/guard";
import { Badge, Card, EmptyState, ErrorState, Loading } from "../components/ui";

interface Team {
  id: string;
  name: string;
  tag?: string | null;
  logo?: string | null;
  logo_url?: string | null;
  color?: string | null;
  colors?: Record<string, string> | null;
  rank?: number | null;
  kills?: number | null;
  placement_pts?: number | null;
  total_pts?: number | null;
  wwcd?: number | null;
  alive?: boolean | null;
  players?: unknown;
  source: "db" | "demo";
}

interface ApiErrorLike {
  status?: number;
  body?: unknown;
}

function apiErrorMessage(e: unknown): string {
  const err = e as ApiErrorLike;
  const body = err?.body;
  if (body && typeof body === "object") {
    const msg = (body as { message?: unknown; detail?: unknown }).message;
    if (typeof msg === "string" && msg) return msg;
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  if (typeof err?.status === "number") return `Request failed with status ${err.status}`;
  return "Request failed";
}

function teamColor(t: Team): string {
  return t.color || (t.colors && t.colors.primary) || "#1f2937";
}

function playersOf(t: Team): string[] {
  return Array.isArray(t.players) ? t.players.filter((p): p is string => typeof p === "string") : [];
}

function SourceBadge({ source }: { source: "db" | "demo" }) {
  return source === "db" ? (
    <Badge tone="green">database</Badge>
  ) : (
    <Badge tone="amber">demo · simulated data</Badge>
  );
}

function TeamsPageInner() {
  const { token, isAuthed } = useAuth();
  const [teams, setTeams] = useState<Team[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Team | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [tag, setTag] = useState("");
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formOk, setFormOk] = useState<string | null>(null);

  const loadTeams = useCallback(async () => {
    setListError(null);
    try {
      const res = await apiFetch<{ data: Team[] }>("/api/v1/teams", { token: token ?? undefined });
      setTeams(res.data ?? []);
    } catch (e) {
      setTeams(null);
      setListError(apiErrorMessage(e));
    }
  }, [token]);

  useEffect(() => {
    void loadTeams();
  }, [loadTeams]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    apiFetch<{ data: Team }>(`/api/v1/teams/${encodeURIComponent(selectedId)}`, {
      token: token ?? undefined,
    })
      .then((res) => setDetail(res.data))
      .catch((e: unknown) => setDetailError(apiErrorMessage(e)))
      .finally(() => setDetailLoading(false));
  }, [selectedId, token]);

  async function handleAddTeam(ev: FormEvent) {
    ev.preventDefault();
    if (!name.trim()) return;
    setFormBusy(true);
    setFormError(null);
    setFormOk(null);
    try {
      const res = await apiFetch<{ data: Team }>("/api/v1/teams", {
        method: "POST",
        token: token ?? undefined,
        body: { name: name.trim(), tag: tag.trim() || undefined },
      });
      setFormOk(`Team "${res.data.name}" added.`);
      setName("");
      setTag("");
      await loadTeams();
    } catch (e) {
      setFormError(apiErrorMessage(e));
    } finally {
      setFormBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <div className="mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#00f0ff]">Teams</h1>
            <p className="text-sm text-white/50">Tournament squads from the teams API</p>
          </div>
          <Link href="/" className="text-sm text-white/60 hover:text-[#00f0ff]">
            ← Dashboard
          </Link>
        </div>

        {selectedId && (
          <Card title="Team detail">
            <div className="mb-3 flex justify-end">
              <button
                onClick={() => setSelectedId(null)}
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-1 text-sm text-white/70 hover:text-white"
              >
                Close
              </button>
            </div>
            {detailLoading && <Loading label="Loading team…" />}
            {!detailLoading && detailError && <ErrorState message={detailError} onRetry={() => setSelectedId(selectedId)} />}
            {!detailLoading && !detailError && detail && (
              <div className="flex flex-col gap-6 md:flex-row">
                <div
                  className="flex h-28 w-28 shrink-0 items-center justify-center rounded-xl text-5xl"
                  style={{ backgroundColor: teamColor(detail) }}
                >
                  {detail.logo ? detail.logo : detail.tag?.slice(0, 2).toUpperCase() ?? detail.name.slice(0, 2).toUpperCase()}
                </div>
                <div className="flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-xl font-bold">{detail.name}</h2>
                    {detail.tag && <Badge tone="cyan">{detail.tag}</Badge>}
                    <SourceBadge source={detail.source} />
                    {typeof detail.alive === "boolean" && (
                      <Badge tone={detail.alive ? "green" : "red"}>{detail.alive ? "ALIVE" : "ELIMINATED"}</Badge>
                    )}
                    {typeof detail.rank === "number" && <Badge tone="slate">Rank #{detail.rank}</Badge>}
                  </div>
                  <dl className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                    {[
                      ["Kills", detail.kills],
                      ["Placement pts", detail.placement_pts],
                      ["Total pts", detail.total_pts],
                      ["WWCD", detail.wwcd],
                    ].map(([label, value]) => (
                      <div key={label as string} className="rounded-lg bg-white/5 p-3">
                        <dt className="text-xs text-white/50">{label}</dt>
                        <dd className="text-lg font-semibold text-[#00f0ff]">
                          {typeof value === "number" ? value : "—"}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <div className="mt-4">
                    <h3 className="mb-2 text-sm font-semibold text-white/70">Players</h3>
                    {playersOf(detail).length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {playersOf(detail).map((p) => (
                          <span key={p} className="rounded-lg bg-white/5 px-3 py-1 text-sm text-white/80">
                            {p}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <EmptyState message="No player roster available for this team." />
                    )}
                  </div>
                </div>
              </div>
            )}
          </Card>
        )}

        {isAuthed && (
          <Card title="Add team">
            <form onSubmit={handleAddTeam} className="flex flex-col gap-3 md:flex-row md:items-end">
              <div className="flex-1">
                <label htmlFor="team-name" className="mb-1 block text-xs text-white/50">
                  Name (required)
                </label>
                <input
                  id="team-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  maxLength={200}
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white placeholder-white/30 focus:border-[#00f0ff] focus:outline-none"
                  placeholder="e.g. Team Nova"
                />
              </div>
              <div className="w-full md:w-40">
                <label htmlFor="team-tag" className="mb-1 block text-xs text-white/50">
                  Tag (optional)
                </label>
                <input
                  id="team-tag"
                  value={tag}
                  onChange={(e) => setTag(e.target.value)}
                  maxLength={32}
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white placeholder-white/30 focus:border-[#00f0ff] focus:outline-none"
                  placeholder="e.g. NV"
                />
              </div>
              <button
                type="submit"
                disabled={formBusy || !name.trim()}
                className="rounded-lg bg-[#00f0ff] px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
              >
                {formBusy ? "Adding…" : "Add team"}
              </button>
            </form>
            {formError && <p className="mt-3 text-sm text-[#ff3366]">{formError}</p>}
            {formOk && <p className="mt-3 text-sm text-emerald-400">{formOk}</p>}
          </Card>
        )}

        <div className="mt-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold">All teams</h2>
          <button
            onClick={() => void loadTeams()}
            className="rounded-lg border border-white/10 bg-white/5 px-3 py-1 text-sm text-white/70 hover:text-white"
          >
            Refresh
          </button>
        </div>

        <div className="mt-4">
          {!teams && !listError && <Loading label="Loading teams…" />}
          {listError && <ErrorState message={listError} onRetry={() => void loadTeams()} />}
          {teams && teams.length === 0 && <EmptyState message="No teams found." />}
          {teams && teams.length > 0 && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {teams.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setSelectedId(t.id)}
                  className={`rounded-xl border bg-white/5 p-4 text-left transition hover:border-[#00f0ff]/60 ${
                    selectedId === t.id ? "border-[#00f0ff]/70" : "border-white/10"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div
                      className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg text-2xl"
                      style={{ backgroundColor: teamColor(t) }}
                    >
                      {t.logo ? t.logo : (t.tag ?? t.name).slice(0, 2).toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-semibold">{t.name}</div>
                      <div className="text-xs text-white/50">{t.tag ?? "—"}</div>
                    </div>
                    <SourceBadge source={t.source} />
                  </div>
                  <div className="mt-3 flex items-center gap-2 text-sm">
                    <span className="text-white/50">Kills</span>
                    <span className="font-semibold text-[#00f0ff]">
                      {typeof t.kills === "number" ? t.kills : "—"}
                    </span>
                    <span className="text-white/50">·</span>
                    <span className="text-white/50">Pts</span>
                    <span className="font-semibold text-[#00f0ff]">
                      {typeof t.total_pts === "number" ? t.total_pts : "—"}
                    </span>
                    <span className="ml-auto">
                      {typeof t.alive === "boolean" && (
                        <Badge tone={t.alive ? "green" : "red"}>{t.alive ? "ALIVE" : "OUT"}</Badge>
                      )}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Route guard: operator + admin. Viewers land on /watch. */
export default function TeamsPage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <TeamsPageInner />
    </RequireRole>
  );
}
