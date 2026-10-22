# Director Learning Loop (Phase 27)

Plain-language summary: the LiveDirector keeps a journal of every
decision it makes, what humans did with its suggestions, and how its
switches turned out. A small **rule-tuning engine** reads that
evidence and proposes bounded changes to a fixed set of numbers
("knobs"). A human can apply, reject, or roll back every change.
Nothing here is machine learning: there is no model, no training,
no neural network — only fixed statistical rules over counted
outcomes, with the numbers shown in every proposal.

## How the loop works

1. **Journal (W1).** Every LiveDirector decision is persisted
   (`director_decisions`), together with suggestion fates
   (approved / rejected / expired / modified) and manual-mode shadow
   divergences.
2. **Labels (W2).** Past switches are labelled from later evidence:
   `fizzle`, `premature_exit`, `good_call`, `missed_fight`.
3. **Engine (W3, this document).** `LearningEngine.run_analysis()`
   groups decisions into buckets keyed by
   `(phase, challenger engagement_state, rule_family)` — rule family
   is the decision rule with its trailing number stripped, e.g.
   `margin_default_15` → `margin_default` — and computes, per bucket:
   suggestion count and approval rate, and switch counts with
   fizzle / premature-exit / good-call rates. Five fixed rules
   (below) turn those rates into proposals.
4. **Application.** Proposals land in
   `director_learning_adjustments`. Only `applied` rows affect
   anything, and — since the clone-mode addition — they affect
   **only clone mode** (see the mode × profile table).

## Knobs and bounds

Exactly these eleven numbers are learnable. Defaults are identical
to the LiveDirector constants / `LiveDirectorConfig` defaults they
replace, so with no applied adjustment the director behaves exactly
as before.

| Knob | Default | Min | Max | Step | Unit |
|---|---|---|---|---|---|
| `margin_default` | 15 | 8 | 30 | 1 | points |
| `margin_fight_hold` | 40 | 25 | 60 | 1 | points |
| `margin_stale_incumbent` | 10 | 5 | 25 | 1 | points |
| `margin_clutch` | 5 | 2 | 15 | 1 | points |
| `storyline_rank1_uplift` | 0.10 | 0 | 0.20 | 0.02 | multiplier uplift |
| `storyline_rank2_uplift` | 0.06 | 0 | 0.15 | 0.02 | multiplier uplift |
| `storyline_match_point_uplift` | 0.10 | 0 | 0.20 | 0.02 | multiplier uplift |
| `storyline_hot_streak_uplift` | 0.08 | 0 | 0.16 | 0.02 | multiplier uplift |
| `engagement_precombat_points` | 15 | 8 | 25 | 1 | points |
| `precombat_confidence_enter` | 0.50 | 0.35 | 0.70 | 0.05 | confidence |
| `drop_clash_watch_points` | 10 | 5 | 20 | 1 | points |

Values are clamped to these bounds; a knob already at its bound
produces no proposal.

## The five rules

Minimum samples `N` defaults to 10 and is configurable on the
engine. "Decided" suggestions are fates approved / rejected /
modified (an expired suggestion is nobody's decision).

- **R1** — bucket approval rate < 0.40 with decided ≥ N →
  `margin_default` **+3**. (Humans keep rejecting these challengers;
  make the director ask for a bigger margin.)
- **R2** — bucket switch fizzle rate > 0.35 with switches ≥ N →
  if at least half the fizzled switches had a positive precombat
  term in the challenger breakdown, `engagement_precombat_points`
  **−2**; otherwise `margin_default` **+2**.
- **R3** — bucket premature-exit rate > 0.30 with switches ≥ N →
  `margin_fight_hold` **+4**.
- **R4** — ≥ 5 `missed_fight` labels (window: **all history** —
  missed fights are rare, and the cooldown guard below still bounds
  re-proposals) tied to decisions whose rule was `margin_stale_*`
  or whose incumbent breakdown shows staleness →
  `margin_stale_incumbent` **−2**.
- **R5** — decided suggestions with `storyline_bonus > 0` rejected
  or modified at a rate > 0.60 with n ≥ 8 →
  `storyline_rank1_uplift` **−0.02**.

Guards on every rule:

- At most **one pending (`proposed`) row per knob**.
- **Sample cooldown:** after a knob is applied, it is not
  re-proposed until at least N *new* decided/labelled samples exist
  in the justifying bucket beyond the sample watermark stored in
  the applied row's evidence.
- Every proposal records knob, bucket, before (= current effective
  learned value), after (= clamped stepped value), a plain-language
  reason containing the numbers, and evidence JSON
  `{rule, n, rates, decision/label id samples, watermark}`.

## Learning modes

Stored in the single-row `director_learning_state` (default
`suggest`):

| Mode | `run_analysis()` behaviour |
|---|---|
| `off` | Creates nothing. The provider serves defaults everywhere — applied rows stay in the table but are dormant, never deleted. |
| `suggest` | New rows are written `status='proposed'`; a human applies or rejects each one. |
| `auto` | New rows are written already `status='applied'` (`applied_by='learning_engine'`) and the provider refreshes immediately. |

## Director-Style ("Clone") mode

`clone` is a fourth LiveDirector mode. It is the same director,
firing autonomously like `full_auto`, but its learnable knobs
resolve from the **learned profile** instead of the factory
defaults. The factory director never changes: `full_auto`,
`semi_auto`, and the manual shadow always resolve factory defaults,
even when applied adjustments exist in the database.

| Director mode | Knob profile that drives | AI writes? |
|---|---|---|
| `full_auto` | Factory defaults — always | Yes, `system_publish` |
| `semi_auto` | Factory defaults — always | Suggestions only |
| `manual` | Factory defaults (shadow evaluation) | Never |
| `clone`, not grabbed | **Learned profile** (defaults when learning is `off` or no profile exists) | Yes, `system_publish` |
| `clone`, grabbed | Factory defaults (shadow evaluation) | Never |

**Cold start, honestly:** a learned profile "exists" only when at
least one adjustment row is `applied`. With none, clone scoring is
the defaults by construction — there is no special case.

### Grab and release inside clone

- `grab_clone_control(updated_by)` — clone-only, instant. While
  grabbed, the director behaves **exactly like manual**: no publish,
  no suggestion, no preview. It keeps evaluating and journal-logs
  shadow entries with rule `clone_grabbed_no_write`. The human
  drives with `set_camera` / `set_scene` — the manual gate allows
  those writes only in manual mode or in a grabbed clone.
  `system_publish` no-ops while grabbed.
- `release_clone_control(updated_by, reason)` — `reason` is
  `explicit` (API-driven) or `inactivity`. Inactivity means no human
  output write since the grab (or since the last human write —
  human writes while grabbed reset the timer, observed through the
  DirectorService output-write sink). The check rides the
  LiveDirector evaluation tick (no new loop) after
  `clone_grab_inactivity_s` seconds (default 120, a
  `LiveDirectorConfig` ops field — **not** a learnable knob). On
  release, clone resumes firing with the learned profile in the
  same evaluation.
- Leaving clone mode drops the grab.

### Transition journaling

Mode changes, grabs, and releases (explicit and inactivity) are
journal-logged as synthetic entries via the W1 journal:
`outcome='mode_transition'`, `trigger` one of
`mode_change` / `grab` / `release`, a rule naming the transition
(e.g. `mode_full_auto_to_clone`, `clone_grabbed`,
`clone_released_inactivity`), and a reason naming from/to and the
actor. The labeler ignores non-decision outcomes by design.

### Where the values are read (seams)

- **Margins** are resolved through the knob provider on every
  evaluation in `LiveDirector._evaluate` (rule names in the log are
  unchanged; `margin_required` carries the actual value used).
- **Config weights** (storyline uplifts, precombat points,
  drop-clash points) are applied as a per-evaluation overlay copy of
  `LiveDirectorConfig` that `_score_team` reads; factory modes use
  the config object untouched, so custom configs are preserved.
- **`precombat_confidence_enter`** is read by the engagement
  tracker at intake, so `LiveDirector.note_potential_fight` syncs
  the tracker's field to the current mode's effective value first.
- **Shadow ladder:** `shadow.full_auto_margin_required` resolves
  its 5/10/15 ladder through the provider with `learned=False` —
  the factory profile — so shadow comparisons never move.

## API surface (Phase 27, W4)

Two routers, both mounted under `/api/v1`:

**Director router additions** (`/api/v1/director`, operator+ like the
other director writes):

| Endpoint | What it does |
|---|---|
| `POST /director/clone/grab` | Grab manual control inside CLONE mode. Outside clone mode the service refuses and the API returns **409** with the service's own message. Idempotent while already grabbed. |
| `POST /director/clone/release` | Release a grab (reason `explicit`). When nothing is grabbed this is an honest no-op: it returns the current state unchanged (`clone_grabbed` stays `false`) rather than pretending a release happened. |
| `POST /director/mode` | Now also accepts `"clone"` (the API schema's mode list matches the service's). |
| `GET /director/state` | The state now also carries `clone_grabbed`, `clone_grabbed_by`, `clone_grabbed_at`. |

**Learning router** (`/api/v1/director-learning`):

| Endpoint | Auth | What it does |
|---|---|---|
| `GET /status` | operator / director / admin | One honest snapshot: `journal_stats` (decisions by mode/outcome, fates by fate, divergences by kind, label total), the learning mode, a `profile` block, the in-process journal's own status (including its drop counters; `null` when no journal runs in this process), and every knob with its default, learned-effective and factory-effective values. |
| `GET /proposals` | operator / director / admin | Pending (`proposed`) adjustments, newest first. |
| `POST /proposals/{id}/approve` | director / admin | Applies the proposal (the actor is recorded). Unknown id → **404**; already decided → **409**. |
| `POST /proposals/{id}/reject` | director / admin | Rejects the proposal (actor recorded). Same 404/409 rules. |
| `GET /adjustments?status=&knob=` | operator / director / admin | The full append-only adjustment trail, filterable. |
| `POST /adjustments/{id}/rollback` | director / admin | Rolls an applied adjustment back exactly and returns the **compensating row**. Unknown id → 404; not currently applied → 409. |
| `GET /mode` | operator / director / admin | Current learning mode. |
| `PUT /mode` | director / admin | Set the learning mode (`off` / `suggest` / `auto`; anything else → **422**). |
| `POST /analyze` | director / admin | Runs one analysis pass on demand and returns the rows it created (empty when the mode is `off` or no rule has enough samples). |

There is no unauthenticated access anywhere on the learning router
(401), and viewer / spotter / observer get **403 on every route,
reads included** — the evidence names who decided what, so it is
broadcast-staff data, not audience data.

The `profile` block in `GET /status` is the clone cold-start source
of truth:

- `exists` — at least one adjustment row is `applied`.
- `contributors` — `[{username, decided_count}]` grouped from the
  suggestion fates' `decided_by` (expiry rows have no decider and
  never appear; a name shows up only when that person actually
  decided fates).
- `learned_from` — `{labeled_decisions, decided_fates, divergences}`:
  label rows, human-decided fates (approved + rejected + modified;
  expiries are nobody's decision), and shadow divergences. All raw
  counts from the tables — nothing derived for show.

## Director console UI (Phase 27, W4)

The director console (`/broadcast/director`) gained a fourth mode
button, **Director Style — Clone**, and two panels:

**Clone panel** (under the mode selector, visible to everyone who can
open the console). It reads the learning status API plus the live
director state and shows:

- who is driving: the AI, or "Manual grab — *username*" with the
  grab time when a human holds control;
- the profile: contributor badges (name · decided count) and the
  learned-from counts when a profile exists;
- **Grab control** / **Release** buttons wired to the endpoints
  above. Grab is disabled outside clone mode with the reason on the
  button — the panel never fakes a grab;
- the **cold-start state**: when no profile exists the panel says,
  in plain words, "Abhi style seekha nahi gaya — pehle kam se kam 10
  faislo par director feedback chahiye", followed by the *real*
  current counts (decided fates, labeled decisions, divergences)
  from the API. Clone then drives on default settings, exactly as
  the engine does;
- the **learning-off state**: when the learning mode is `off`, a
  note that clone will drive on default settings and the applied
  adjustments are dormant, not deleted.

While a clone is grabbed, the console's scene / camera / overlay /
lower-third controls enable (the backend's manual gate accepts a
grabbed clone exactly like manual), and the multiview labels the
mode "Clone (grabbed)" and allows taking the PVW preview.

**Director Learning panel** (director/admin only — it renders
nothing for other roles, and the API enforces the same split):

- a stats header: total journal decisions, labeled count, and the
  suggestion approval rate (approved ÷ decided, both from the API);
- pending proposals with the engine's own plain-language reason
  (numbers included), the knob's before → after values, and the
  bucket context, with Approve / Reject buttons;
- "Seekha hua" — the applied adjustments as plain lesson lines,
  each with a Rollback button;
- the learning-mode toggle OFF / SUGGEST / AUTO with a one-line
  honest explainer each ("SUGGEST: badlav pehle tumhari manzoori
  mangega"), plus an on-demand "Abhi analyze karo" button;
- no ML jargon and no confidence theatre: counts and values only.

## Apply / reject / rollback

- `apply_adjustment(id, applied_by)` — `proposed` → `applied`;
  provider refreshes.
- `reject_adjustment(id, decided_by)` — `proposed` → `rejected`
  (the actor is recorded).
- `rollback_adjustment(id, by)` — the target row becomes
  `rolled_back` **and** a compensating row is inserted
  (`status='applied'`, `parent_id` = target, `after_value` = the
  target's `before_value`), so the effective value returns
  **exactly** to the pre-adjustment value. If a newer applied row
  exists for the same knob, a plain rollback is **refused** (API:
  409) — it would otherwise silently override the newer
  adjustment. The caller either unwinds newest-first, or passes
  `cascade` (API: `?cascade=true`), which rolls back the target
  **and** every newer applied row for that knob together — all
  marked `rolled_back`, all cited in the compensating row's
  evidence — so any prior state stays reachable and the applied
  chain can never silently disagree with the value in effect. The
  audit trail is
  append-only: historical rows are never deleted, and no row is
  mutated beyond its status transition.

## What this does NOT do

- **No neural training, no ML model, no "AI learning" in the
  statistical-model sense.** It is fixed rules over counted
  outcomes, and the code says so.
- **No grammar-floor changes.** Min fight holds, the 3 s switch
  cooldown, the 8 switches/minute cap, the storyline multiplier cap
  (1.35), final-zone locks and stability, staleness windows/decay,
  all score caps, and the context-beat settings are *not in the
  registry*, and attempting to register or adjust a forbidden name
  raises in code.
- **No learning without samples.** Every rule needs its minimum
  sample count in its bucket; small samples produce no proposal,
  never a guess. An empty labels table (W2 not yet run) means the
  label-driven rules simply do not fire.
- **No silent application in `suggest` mode, and nothing at all in
  `off`.** Applied rows are dormant — not deleted — while off.
- **No factory drift.** Learning can only ever change how *clone*
  mode scores. `full_auto` is the factory director forever.
