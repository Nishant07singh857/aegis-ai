"use client";

/**
 * AEGIS AI — AI storytelling control panel (Phase 19, W3).
 *
 * Director-console panel for the AI story graphics pipeline:
 *   - lists available stories (GET /api/v1/stories/available), grouped by
 *     story type, with thumbnails when the backend provides them
 *   - Preview: renders the story through W2's animated <StoryGraphic> in a
 *     preview pane — it never touches the broadcast output
 *   - Trigger to Output / Dismiss: POST .../trigger .../dismiss — only for
 *     director + admin
 *   - Generate: POST /api/v1/stories/generate for the current match —
 *     only for director + admin
 *   - per-story template picker (W2's 2–3 layout templates per type) +
 *     animation style picker (slide / fade / scale / none); per-tournament
 *     defaults persisted via GET/PUT /api/v1/stories/settings
 *
 * Permission gating (UI-side; the backend enforces server-side too):
 *   director | admin   — full control (trigger / dismiss / generate /
 *                        settings). Buttons are conditionally rendered —
 *                        never merely CSS-hidden.
 *   operator          — read-only preview: can view stories and use the
 *                        preview pane, but no trigger/dismiss/generate and
 *                        no settings writes.
 *   everyone else     — the panel returns null (never rendered). The
 *                        director page itself is RequireRole-gated, so this
 *                        is a defence-in-depth second check for reuse.
 *
 * Honest-data rule: when there are no stories the panel says so — no
 * placeholder stories are invented. Backend story_type ids are translated
 * to W2 renderer ids in @/lib/stories.ts; an unknown type renders an
 * honest fallback instead of a guessed graphic.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading } from "@/components/ui";
import { useRole } from "@/lib/useRole";
import { apiFetch } from "@/lib/api";
import StoryGraphic from "@/components/graphics/animated/StoryGraphic";
import type { Story as W2Story } from "@/components/graphics/animated/types";
import {
  dismissStory,
  generateStories,
  getAvailableStories,
  getStorySettings,
  isKnownStoryType,
  isStoryAnimation,
  putStorySettings,
  STORY_ANIMATIONS,
  storyTypeLabel,
  templateLabel,
  templateOptionsForStory,
  templatesForStoryType,
  triggerStory,
  type Story,
  type StoryAnimation,
  type StorySettings,
} from "@/lib/stories";

function actionErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Request failed.";
}

function storyLabel(story: Story): string {
  return storyTypeLabel(story.type);
}

function isOnAir(story: Story): boolean {
  return story.on_air === true;
}

/**
 * Adapt this panel's (already translated) story to W2's StoryGraphic input
 * ({ type, title, body, data }). lib/stories.ts translates backend shapes
 * at the client boundary, so this is a thin pass-through. Returns null
 * for story types W2 has no renderer for — the preview pane shows an
 * honest fallback instead.
 */
function toW2Story(story: Story): W2Story | null {
  if (!isKnownStoryType(story.type)) return null;
  return {
    type: story.type,
    title: story.title,
    body: story.body,
    data: story.data,
  };
}

/**
 * Resolve the current live match id for story scoping.
 * GET /api/v1/matches/live -> { data: { id } }. That endpoint is
 * DEMO_MODE-gated and 404/503s otherwise, so this returns null when no
 * live match is known. The backend REQUIRES match_id for generation, so
 * the Generate button stays disabled until a real match id is available.
 * Nothing is invented.
 */
function useLiveMatchId(token: string | null): string | null {
  const [matchId, setMatchId] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = await apiFetch<{ data?: { id?: string } }>(
          "/api/v1/matches/live",
          { token: token ?? undefined }
        );
        if (!cancelled) setMatchId(body.data?.id ?? null);
      } catch {
        if (!cancelled) setMatchId(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);
  return matchId;
}

/**
 * Preview pane: the real W2 animated story graphic (template + animation
 * style applied), wrapped with an honest "not on output" marker. This is
 * local-only — preview never touches the broadcast output.
 */
function StoryPreview({
  story,
  templateId,
  animation,
}: {
  story: Story;
  templateId?: string;
  animation: StoryAnimation;
}) {
  const w2 = toW2Story(story);
  return (
    <div className="overflow-hidden rounded-xl border border-white/15 bg-black/50">
      <div
        key={`${story.id}-${templateId ?? "default"}-${animation}`}
        className="min-h-40 p-4"
      >
        {w2 ? (
          <StoryGraphic
            story={w2}
            template={templateId}
            animationStyle={animation}
          />
        ) : (
          <div className="flex min-h-40 items-center justify-center p-6 text-center">
            <p className="text-sm text-white/50">
              No animated graphic registered for story type “{story.type}” —
              nothing rendered rather than a guessed graphic.
            </p>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-2 border-t border-white/10 px-4 py-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-amber-300/70">
          ◆ Preview — not on output
        </span>
        <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">
          {templateId ? templateLabel(templateId) : "Default template"} ·{" "}
          {animation}
        </span>
      </div>
    </div>
  );
}

export function StorytellingPanel({ token }: { token: string | null }) {
  const { role } = useRole();
  const canControl = role === "director" || role === "admin";
  const canView = canControl || role === "operator";
  const matchId = useLiveMatchId(token);

  const [stories, setStories] = useState<Story[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Per-story picker overrides (template / animation) before triggering.
  const [overrides, setOverrides] = useState<
    Record<string, { templateId?: string; animation?: StoryAnimation }>
  >({});
  // Per-tournament settings.
  const [settings, setSettings] = useState<StorySettings | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<StorySettings | null>(
    null
  );
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsSaved, setSettingsSaved] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await getAvailableStories(token, matchId);
      setStories(res.stories);
      setError(null);
      if (res.stories.length > 0) {
        setPreviewId((prev) =>
          prev && res.stories.some((s) => s.id === prev)
            ? prev
            : res.stories[0].id
        );
      } else {
        setPreviewId(null);
      }
    } catch (e) {
      setError(actionErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [token, matchId]);

  // Load story list on mount + poll every 15s.
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 15000);
    return () => clearInterval(timer);
  }, [refresh]);

  // Load per-tournament settings (visible to all panel roles; writable by
  // director/admin only).
  useEffect(() => {
    let cancelled = false;
    setSettingsLoading(true);
    getStorySettings(token)
      .then((s) => {
        if (!cancelled) {
          setSettings(s);
          setSettingsDraft(s);
          setSettingsError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setSettingsError(actionErrorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setSettingsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const storiesByType = useMemo(() => {
    const groups = new Map<string, Story[]>();
    for (const story of stories) {
      const key = story.type;
      const list = groups.get(key);
      if (list) list.push(story);
      else groups.set(key, [story]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [stories]);

  /** Story types known to the panel: from the story list plus any types
   *  already saved in settings (so defaults survive an empty list). */
  const knownTypes = useMemo(() => {
    const types = new Map<string, string>();
    for (const story of stories) types.set(story.type, storyLabel(story));
    if (settings) {
      for (const t of [
        ...Object.keys(settings.default_template),
        ...Object.keys(settings.animation_style),
      ]) {
        if (!types.has(t)) types.set(t, storyTypeLabel(t));
      }
    }
    return [...types.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [stories, settings]);

  const previewStory = stories.find((s) => s.id === previewId) ?? null;

  const run = useCallback(
    async (label: string, fn: () => Promise<void>) => {
      setBusy(label);
      setError(null);
      setNotice(null);
      try {
        await fn();
        await refresh();
      } catch (e) {
        setError(`${label}: ${actionErrorMessage(e)}`);
      } finally {
        setBusy(null);
      }
    },
    [refresh]
  );

  const storyOverride = (id: string) => overrides[id] ?? {};

  const effectiveTemplate = (story: Story): string | undefined =>
    storyOverride(story.id).templateId ??
    settings?.default_template[story.type];

  const effectiveAnimation = (story: Story): StoryAnimation => {
    const o = storyOverride(story.id).animation;
    if (o && isStoryAnimation(o)) return o;
    const s = settings?.animation_style[story.type];
    return isStoryAnimation(s) ? s : "slide";
  };

  const onTrigger = (story: Story) =>
    run("Trigger", async () => {
      await triggerStory(token, story.id, {
        template_id: effectiveTemplate(story),
        animation_style: effectiveAnimation(story),
      });
      setNotice(`“${story.title}” sent to broadcast output.`);
    });

  const onDismiss = (story: Story) =>
    run("Dismiss", async () => {
      await dismissStory(token, story.id);
      setNotice(`“${story.title}” removed from broadcast output.`);
    });

  const onGenerate = () =>
    run("Generate", async () => {
      if (!matchId) {
        setNotice("No live match id — generation needs a real match_id.");
        return;
      }
      const res = await generateStories(token, matchId);
      setNotice(
        res.generated > 0
          ? `Generated ${res.generated} ${res.generated === 1 ? "story" : "stories"}.`
          : "Generate finished — no stories came back from the backend."
      );
    });

  const onSaveSettings = () =>
    run("Save settings", async () => {
      if (!settingsDraft) return;
      const saved = await putStorySettings(token, settingsDraft);
      setSettings(saved);
      setSettingsDraft(saved);
      setSettingsSaved("Defaults saved.");
    });

  const setDraftTemplate = (type: string, templateId: string) =>
    setSettingsDraft((d) =>
      d
        ? {
            ...d,
            default_template: { ...d.default_template, [type]: templateId },
          }
        : d
    );

  const setDraftAnimation = (type: string, animation: StoryAnimation) =>
    setSettingsDraft((d) =>
      d
        ? {
            ...d,
            animation_style: { ...d.animation_style, [type]: animation },
          }
        : d
    );

  // ── Role gate: spotter / observer / viewer (and any unknown role) never
  //    see the panel. Placed after all hooks so hook order is stable.
  if (!canView) return null;

  const anyOnAir = stories.some(isOnAir);

  return (
    <Card
      title="AI Storytelling"
      action={
        <div className="flex items-center gap-2">
          {anyOnAir && <Badge tone="red">● On air</Badge>}
          {canControl ? (
            <Badge tone="cyan">Director control</Badge>
          ) : (
            <Badge tone="slate">Read-only preview</Badge>
          )}
        </div>
      }
    >
      {loading && <Loading label="Loading stories" />}
      {error && (
        <div className="mb-3">
          <ErrorState message={error} onRetry={() => setError(null)} />
        </div>
      )}
      {notice && (
        <p className="mb-3 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-sm text-emerald-200">
          {notice}
        </p>
      )}

      {!loading && !error && (
        <div className="space-y-6">
          {/* Generate — director/admin only. Conditionally rendered, never
              CSS-hidden, and the backend enforces the role server-side. */}
          {canControl && (
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy !== null || !matchId}
                onClick={onGenerate}
                title={
                  matchId
                    ? "Generate stories for the current match"
                    : "Disabled — no live match id (backend requires match_id)"
                }
                className="rounded-lg bg-violet-500/80 px-4 py-2 text-sm font-semibold text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy === "Generate" ? "Generating…" : "✨ Generate stories"}
              </button>
              {matchId ? (
                <span className="font-mono text-xs text-white/40">
                  match {matchId}
                </span>
              ) : (
                <span className="font-mono text-xs text-white/40">
                  no live match — generation disabled
                </span>
              )}
            </div>
          )}

          {/* Story list, grouped by type. */}
          {stories.length === 0 ? (
            <EmptyState
              message={
                canControl
                  ? "No stories yet — press “Generate stories” to create some for the current match."
                  : "No stories yet."
              }
            />
          ) : (
            <div className="space-y-5">
              {storiesByType.map(([type, group]) => (
                <section key={type} aria-label={storyLabel(group[0])}>
                  <h3 className="mb-2 flex items-center gap-2 text-sm font-bold uppercase tracking-[0.15em] text-white/70">
                    {storyLabel(group[0])}
                    <span className="font-mono text-xs font-normal text-white/35">
                      {group.length}
                    </span>
                  </h3>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {group.map((story) => {
                      const onAir = isOnAir(story);
                      const selected = previewId === story.id;
                      return (
                        <div
                          key={story.id}
                          className={`rounded-xl border p-3 transition-colors ${
                            selected
                              ? "border-[#00f0ff]/60 bg-[#00f0ff]/[0.06]"
                              : "border-white/10 bg-black/30"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="truncate text-sm font-semibold text-white/90">
                                {story.title}
                              </div>
                            </div>
                            {onAir ? (
                              <Badge tone="red">On air</Badge>
                            ) : (
                              <Badge tone="slate">Ready</Badge>
                            )}
                          </div>

                          {/* per-story template + animation pickers */}
                          <div className="mt-2.5 grid grid-cols-2 gap-2">
                            <label className="flex flex-col gap-1">
                              <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">
                                Template
                              </span>
                              <select
                                value={effectiveTemplate(story) ?? ""}
                                disabled={busy !== null}
                                onChange={(e) =>
                                  setOverrides((prev) => ({
                                    ...prev,
                                    [story.id]: {
                                      ...storyOverride(story.id),
                                      templateId: e.target.value || undefined,
                                    },
                                  }))
                                }
                                className="rounded-lg bg-white/10 px-2 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-[#00f0ff]/60 disabled:opacity-40"
                              >
                                <option value="" className="bg-zinc-900">
                                  Default
                                </option>
                                {templateOptionsForStory(story).map((t) => (
                                  <option
                                    key={t.id}
                                    value={t.id}
                                    className="bg-zinc-900"
                                  >
                                    {t.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="flex flex-col gap-1">
                              <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">
                                Animation
                              </span>
                              <select
                                value={effectiveAnimation(story)}
                                disabled={busy !== null}
                                onChange={(e) =>
                                  setOverrides((prev) => ({
                                    ...prev,
                                    [story.id]: {
                                      ...storyOverride(story.id),
                                      animation: e.target
                                        .value as StoryAnimation,
                                    },
                                  }))
                                }
                                className="rounded-lg bg-white/10 px-2 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-[#00f0ff]/60 disabled:opacity-40"
                              >
                                {STORY_ANIMATIONS.map((a) => (
                                  <option
                                    key={a}
                                    value={a}
                                    className="bg-zinc-900"
                                  >
                                    {a}
                                  </option>
                                ))}
                              </select>
                            </label>
                          </div>

                          {/* actions: preview for everyone who can see the
                              panel; trigger/dismiss rendered only for
                              director+admin. */}
                          <div className="mt-3 flex flex-wrap gap-2">
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={() => setPreviewId(story.id)}
                              aria-pressed={selected}
                              className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                                selected
                                  ? "border-[#00f0ff] bg-[#00f0ff]/15 text-[#00f0ff]"
                                  : "border-white/15 bg-white/5 text-white/75 hover:border-white/30"
                              } disabled:cursor-wait disabled:opacity-40`}
                            >
                              👁 Preview
                            </button>
                            {canControl && !onAir && (
                              <button
                                type="button"
                                disabled={busy !== null}
                                onClick={() => onTrigger(story)}
                                className="rounded-lg bg-[#00f0ff] px-3 py-1.5 text-xs font-bold uppercase tracking-wider text-black transition disabled:cursor-wait disabled:opacity-40"
                              >
                                {busy === "Trigger"
                                  ? "Sending…"
                                  : "▶ Trigger to output"}
                              </button>
                            )}
                            {canControl && onAir && (
                              <button
                                type="button"
                                disabled={busy !== null}
                                onClick={() => onDismiss(story)}
                                className="rounded-lg border border-red-400/50 bg-red-500/15 px-3 py-1.5 text-xs font-bold uppercase tracking-wider text-red-200 transition hover:bg-red-500/25 disabled:cursor-wait disabled:opacity-40"
                              >
                                {busy === "Dismiss"
                                  ? "Removing…"
                                  : "■ Dismiss"}
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          )}

          {/* Preview pane — the real W2 animated graphic. Local only, never
              goes to output. */}
          {previewStory && (
            <div>
              <h3 className="mb-2 text-sm font-bold uppercase tracking-[0.15em] text-white/70">
                Preview
              </h3>
              <StoryPreview
                story={previewStory}
                templateId={effectiveTemplate(previewStory)}
                animation={effectiveAnimation(previewStory)}
              />
            </div>
          )}

          {/* Per-tournament defaults. Writes rendered only for
              director/admin; operator sees the saved values read-only. */}
          <div className="rounded-xl border border-white/10 bg-black/30 p-4">
            <h3 className="mb-1 text-sm font-bold uppercase tracking-[0.15em] text-white/70">
              Tournament defaults
            </h3>
            <p className="mb-3 text-xs text-white/45">
              Default template and animation style per story type. Trigger
              uses these unless a story has its own pickers set above.
            </p>
            {settingsLoading && <Loading label="Loading settings" />}
            {settingsError && (
              <div className="mb-3">
                <ErrorState
                  message={settingsError}
                  onRetry={() => setSettingsError(null)}
                />
              </div>
            )}
            {!settingsLoading && !settingsError && settingsDraft && (
              <div className="space-y-2.5">
                {knownTypes.length === 0 && (
                  <p className="text-sm text-white/50">
                    No story types known yet — defaults appear after the
                    first generation.
                  </p>
                )}
                {knownTypes.map(([type, label]) => (
                  <div
                    key={type}
                    className="grid grid-cols-[1fr_auto] items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-2 sm:grid-cols-[1fr_1fr_1fr]"
                  >
                    <span className="text-sm font-semibold text-white/85">
                      {label}
                    </span>
                    <label className="flex items-center gap-2">
                      <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">
                        Template
                      </span>
                      <select
                        value={settingsDraft.default_template[type] ?? ""}
                        disabled={!canControl || busy !== null}
                        onChange={(e) =>
                          setDraftTemplate(type, e.target.value)
                        }
                        className="rounded-lg bg-white/10 px-2 py-1.5 text-xs text-white focus:outline-none disabled:opacity-40"
                      >
                        <option value="" className="bg-zinc-900">
                          Default
                        </option>
                        {templatesForStoryType(type).map((t) => (
                          <option
                            key={t}
                            value={t}
                            className="bg-zinc-900"
                          >
                            {templateLabel(t)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="flex items-center gap-2">
                      <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">
                        Animation
                      </span>
                      <select
                        value={
                          isStoryAnimation(
                            settingsDraft.animation_style[type]
                          )
                            ? settingsDraft.animation_style[type]
                            : "slide"
                        }
                        disabled={!canControl || busy !== null}
                        onChange={(e) =>
                          setDraftAnimation(
                            type,
                            e.target.value as StoryAnimation
                          )
                        }
                        className="rounded-lg bg-white/10 px-2 py-1.5 text-xs text-white focus:outline-none disabled:opacity-40"
                      >
                        {STORY_ANIMATIONS.map((a) => (
                          <option
                            key={a}
                            value={a}
                            className="bg-zinc-900"
                          >
                            {a}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                ))}
                <div className="flex items-center gap-3 pt-1">
                  {canControl && (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={onSaveSettings}
                      className="rounded-lg bg-emerald-500/80 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-wait disabled:opacity-50"
                    >
                      {busy === "Save settings" ? "Saving…" : "Save defaults"}
                    </button>
                  )}
                  {settingsSaved && (
                    <span className="text-xs text-emerald-300">
                      {settingsSaved}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
