'use client';

/**
 * AEGIS AI — GRAPHICS TEMPLATE GALLERY (Phase 8).
 *
 * Route: /broadcast/graphics (operator + admin).
 *
 * Every registered broadcast graphics template, grouped by type, each
 * with a LIVE preview: the real template component fed by the same live
 * data the overlay pages use (WS standings / kill feed via useLiveSocket,
 * commentary via the shared useCommentary hook, analytics summary polled
 * like /overlays/scorebug). No mock data anywhere — a template whose feed
 * has nothing to show renders its own honest empty state.
 *
 * "Set Active" writes the choice to the director
 * (POST /api/v1/director/graphics); the template currently selected in
 * the director state carries an ACTIVE badge. Template selection is an
 * operator write: allowed in SEMI_AUTO and MANUAL mode, rejected with
 * 409 in FULL_AUTO — the gallery says so instead of pretending.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import type { CSSProperties } from 'react';
import { useAuth } from '@/lib/auth';
import { useLiveSocket } from '@/lib/useLiveSocket';
import { RequireRole } from '@/lib/guard';
import { Badge, Card, EmptyState, ErrorState, Loading } from '@/components/ui';
import { API_BASE } from '@/lib/api';
import { DEFAULT_ACCENT } from '@/lib/overlay-params';
import { useLatestCommentary } from '@/lib/useCommentary';
import {
  formatDirectorUpdatedAt,
  getDirectorState,
  setGraphics,
  type DirectorGraphics,
  type DirectorState,
} from '@/lib/director';
import {
  GRAPHIC_STATE_KEYS,
  GRAPHIC_TYPES,
  GRAPHIC_TYPE_LABELS,
  isActiveTemplate,
  templatesFor,
  type GraphicType,
  type TemplateDef,
} from '@/lib/graphics';

interface SummaryData {
  zone_phase?: number;
  teams_alive?: number;
  match_time_seconds?: number;
}

const SUMMARY_POLL_MS = 15_000;
const KILL_TTL_SECONDS = 12;

function actionErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return 'Request failed.';
}

/** Live preview of one template: the real component, real live data. */
function TemplatePreview({
  def,
  standings,
  summary,
  killFeed,
  status,
}: {
  def: TemplateDef;
  standings: ReturnType<typeof useLiveSocket>['standings'];
  summary: SummaryData | null;
  killFeed: ReturnType<typeof useLiveSocket>['killFeed'];
  status: ReturnType<typeof useLiveSocket>['status'];
}) {
  const Component = def.component;
  const accent = DEFAULT_ACCENT;

  const body = useMemo(() => {
    switch (def.type) {
      case 'scorebug':
        return (
          <Component
            standings={standings}
            summary={summary}
            accent={accent}
            status={status}
            matchLabel="LIVE"
          />
        );
      case 'killfeed': {
        const nowSec = Date.now() / 1000;
        const events = killFeed
          .filter((k) => nowSec - k.timestamp <= KILL_TTL_SECONDS)
          .slice(0, 8);
        return <Component events={events} accent={accent} status={status} />;
      }
      case 'standings': {
        const rows = [...standings]
          .sort((a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0))
          .slice(0, 5);
        return <Component standings={rows} accent={accent} status={status} />;
      }
      case 'lower-third':
        // The commentary line is fetched in TemplateCard below so the
        // preview body stays a pure function of its props; unreachable.
        return null;
    }
  }, [def, standings, summary, killFeed, status, accent]);

  return (
    <div
      className="relative h-56 overflow-hidden rounded-lg bg-black/60 sm:h-64"
      style={{ '--ov-accent': accent } as CSSProperties}
      aria-label={`Live preview of ${def.name}`}
    >
      <div className="pointer-events-none absolute inset-0 select-none">
        {body}
      </div>
      <div className="absolute bottom-2 right-2 rounded bg-black/70 px-2 py-0.5 font-mono text-[10px] tracking-[0.25em] text-white/50">
        LIVE PREVIEW
      </div>
    </div>
  );
}

function LowerThirdPreview({
  def,
  status,
}: {
  def: TemplateDef;
  status: ReturnType<typeof useLiveSocket>['status'];
}) {
  const line = useLatestCommentary(API_BASE);
  const Component = def.component;
  const accent = DEFAULT_ACCENT;
  return (
    <div
      className="relative h-56 overflow-hidden rounded-lg bg-black/60 sm:h-64"
      style={{ '--ov-accent': accent } as CSSProperties}
      aria-label={`Live preview of ${def.name}`}
    >
      <div className="pointer-events-none absolute inset-0 select-none">
        <Component
          visible={line !== null}
          title={line ? line.type.toUpperCase() : ''}
          subtitle={line ? line.text : ''}
          accent={accent}
          status={status}
          animKey={line ? String(line.timestamp) : 'standby'}
        />
      </div>
      <div className="absolute bottom-2 right-2 rounded bg-black/70 px-2 py-0.5 font-mono text-[10px] tracking-[0.25em] text-white/50">
        LIVE PREVIEW
      </div>
    </div>
  );
}

function TemplateCard({
  def,
  type,
  state,
  locked,
  busy,
  standings,
  summary,
  killFeed,
  status,
  onActivate,
}: {
  def: TemplateDef;
  type: GraphicType;
  state: DirectorState | null;
  locked: boolean;
  busy: boolean;
  standings: ReturnType<typeof useLiveSocket>['standings'];
  summary: SummaryData | null;
  killFeed: ReturnType<typeof useLiveSocket>['killFeed'];
  status: ReturnType<typeof useLiveSocket>['status'];
  onActivate: () => void;
}) {
  const active = isActiveTemplate(state, type, def.id);
  return (
    <div
      className={`flex flex-col gap-3 rounded-xl border p-4 transition-colors ${
        active
          ? 'border-[#00f0ff]/70 bg-[#00f0ff]/[0.06] shadow-[0_0_24px_rgba(0,240,255,0.12)]'
          : 'border-white/10 bg-white/[0.03]'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-bold text-white/95">{def.name}</div>
          <p className="mt-1 text-xs leading-relaxed text-white/50">
            {def.description}
          </p>
        </div>
        {active && <Badge tone="cyan">Active</Badge>}
      </div>

      {def.type === 'lower-third' ? (
        <LowerThirdPreview def={def} status={status} />
      ) : (
        <TemplatePreview
          def={def}
          standings={standings}
          summary={summary}
          killFeed={killFeed}
          status={status}
        />
      )}

      <button
        type="button"
        disabled={busy || locked || active}
        onClick={onActivate}
        title={
          locked
            ? 'Template changes are rejected in Full Auto mode — switch to Semi-Auto or Manual first.'
            : undefined
        }
        className={`rounded-lg px-4 py-2.5 font-mono text-xs font-bold uppercase tracking-[0.15em] transition-all ${
          active
            ? 'cursor-default bg-[#00f0ff]/20 text-[#00f0ff]'
            : 'bg-[#00f0ff] text-black hover:brightness-110'
        } disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:brightness-100`}
      >
        {busy ? 'Setting…' : active ? 'Active' : 'Set Active'}
      </button>
    </div>
  );
}

function GalleryInner() {
  const { token } = useAuth();
  const { status, standings, killFeed, directorState } = useLiveSocket();
  const [state, setState] = useState<DirectorState | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setState(await getDirectorState(token));
    } catch (e) {
      setLoadError(actionErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  // WS frames are the freshest source — apply them as they arrive.
  useEffect(() => {
    if (directorState) setState(directorState);
  }, [directorState]);

  // Same analytics summary the scorebug overlay polls.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/analytics/summary`);
        if (!res.ok) return;
        const body = (await res.json()) as { data?: SummaryData };
        if (!cancelled && body.data) setSummary(body.data);
      } catch {
        /* feed may be down — previews fall back to WS-only data */
      }
    };
    poll();
    const t = setInterval(poll, SUMMARY_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  const activate = useCallback(
    async (type: GraphicType, def: TemplateDef) => {
      setBusyId(def.id);
      setActionError(null);
      try {
        const body: Partial<DirectorGraphics> = {};
        body[GRAPHIC_STATE_KEYS[type]] = def.id;
        await setGraphics(token, body);
        // Re-read the authoritative state (the WS broadcast confirms it).
        await load();
      } catch (e) {
        setActionError(actionErrorMessage(e));
      } finally {
        setBusyId(null);
      }
    },
    [token, load]
  );

  const mode = state?.mode ?? 'full_auto';
  // Unlike scene/overlay/lower-third writes (manual-only), template
  // selection is allowed in SEMI_AUTO too — only FULL_AUTO 409s.
  const locked = mode === 'full_auto';

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8 text-white">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[#00f0ff]">
            Graphics Templates
          </h1>
          <p className="text-sm text-white/50">
            Pick the on-air look for each graphic. Previews are live — they
            render the real templates with the real feed.
          </p>
        </div>
        <Link
          href="/broadcast/director"
          className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-zinc-200 transition-colors hover:border-[#00f0ff]/60 hover:text-[#00f0ff]"
        >
          ← Director console
        </Link>
      </div>

      {loading && <Loading label="Loading director state" />}

      {!loading && loadError && (
        <ErrorState message={loadError} onRetry={() => void load()} />
      )}

      {!loading && !loadError && (
        <>
          <Card
            title="Director state"
            action={
              <Badge
                tone={
                  mode === 'full_auto'
                    ? 'cyan'
                    : mode === 'semi_auto'
                      ? 'amber'
                      : 'green'
                }
              >
                {mode === 'full_auto'
                  ? 'Full Auto'
                  : mode === 'semi_auto'
                    ? 'Semi-Auto'
                    : 'Manual'}
              </Badge>
            }
          >
            {locked ? (
              <p className="text-sm text-amber-200/80">
                The director is in <strong>Full Auto</strong> — template
                changes are rejected by the backend (409) in this mode.
                Switch to Semi-Auto or Manual on the director console to
                change templates.
              </p>
            ) : (
              <p className="text-sm text-white/60">
                Template changes apply immediately to the output and all
                overlay pages. Last updated by{' '}
                <span className="text-white/80">
                  {state?.updated_by ?? '—'}
                </span>{' '}
                at{' '}
                <span className="text-white/80">
                  {formatDirectorUpdatedAt(state?.updated_at ?? '')}
                </span>
                .
              </p>
            )}
          </Card>

          {actionError && (
            <ErrorState
              message={actionError}
              onRetry={() => setActionError(null)}
            />
          )}

          {GRAPHIC_TYPES.map((type) => (
            <section key={type} aria-label={`${GRAPHIC_TYPE_LABELS[type]} templates`}>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-lg font-bold text-white/90">
                  {GRAPHIC_TYPE_LABELS[type]}
                </h2>
                <span className="font-mono text-xs text-white/40">
                  {templatesFor(type).length} templates
                </span>
              </div>
              {templatesFor(type).length === 0 ? (
                <EmptyState
                  message={`No ${GRAPHIC_TYPE_LABELS[type].toLowerCase()} templates are registered yet.`}
                />
              ) : (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {templatesFor(type).map((def) => (
                    <TemplateCard
                      key={def.id}
                      def={def}
                      type={type}
                      state={state}
                      locked={locked}
                      busy={busyId === def.id}
                      standings={standings}
                      summary={summary}
                      killFeed={killFeed}
                      status={status}
                      onActivate={() => void activate(type, def)}
                    />
                  ))}
                </div>
              )}
            </section>
          ))}
        </>
      )}
    </div>
  );
}

export default function GraphicsGalleryPage() {
  return (
    <RequireRole roles={['operator', 'admin']}>
      <GalleryInner />
    </RequireRole>
  );
}
