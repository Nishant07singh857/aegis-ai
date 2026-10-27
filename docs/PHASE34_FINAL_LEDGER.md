# Phase 34 Final Ledger — Code Verification HIGH Backlog (H-1 + H-2)

**Base:** local HEAD `7972857` (Phase 33, QA PASS-WITH-NOTES; remote main `1be10e25`).
**Status:** code complete, independent QA **PASS-WITH-NOTES**. No commit, no push by the
coordinator, workers, or QA — work remains in the working tree for parent review; commit +
push only while the user is present in chat, per the standing rule.
**Scope:** ONLY the two High items from the Code Verification Report
(`~/workspace/drafts/aegis-code-verification-2026-10-27.md`, defects F1/F2). H-3 and all
Medium/Low backlog items are explicitly NOT in this phase (§8).

## 1. H-1 — `_evidence_weapon` dict leak (FIXED)

**Defect (verification F1):** `clip_detector.py` dict-unwrap keys included `weapon_family`, so
`{"weapon": {"weapon_family": "ar_a"}}` rendered `"ar_a"` as a gun name
(`"hits 320m ar_a shot!"`), contradicting the docstring's claim that a family id alone
never renders. Top-level `{"weapon_family": "ar_a"}` already returned `None` correctly.

**Fix (one hunk family, `clip_detector.py` evidence helpers):** `weapon_family` removed from
the unwrap key list entirely — it now appears in the file only in comments/docstring
explaining the exclusion. Unwrap keys are `("name", "label", "weapon", "weapon_class")`,
applied at up to 3 dict-nesting levels; a dict carrying ONLY `weapon_family` (at any depth,
including under `weapon`/`weapon_name`/`weapon_class`) returns `None`. An unwrapped value
that is still a dict, or a non-string with no string `weapon` attribute, returns `None` —
never guessed. First non-`None` key wins at each level (prior precedence preserved);
top-level key order `weapon` → `weapon_name` → `weapon_class` unchanged; sentinel tokens
and the `unknown_*` prefix still mean no evidence; the value is returned verbatim
(stripped); no class↔gun mapping was added. Docstring updated to state the any-depth
exclusion truthfully. `_evidence_distance` and `_create_long_range_clip` rendering were
not changed (already fail-closed in Phase 33); `_evidence_distance` now delegates to the
shared positive-number helper with identical key list and semantics (D-2 suite confirms).

**Proof:** the verification-report probe now yields `None` and renders gun-less. QA re-probed
14 family-only shapes (every depth, under every top-level key), name+family precedence,
15 sentinels, and junk/object-attr shapes — all `None` or the correct evidenced name.
Depth-4 nesting returns `None` (documented 3-iteration bound; no realistic producer nests
deeper). One safe-direction quirk: a family-only dict in `weapon` shadows a valid
`weapon_name` (first-key-wins, pre-existing) → `None`, never a leak.

## 2. H-2 — clip defaults (FIXED)

**Defect (verification F2):** four clip creators rendered invented numbers from defaults
reachable via `detect_clip` dispatch: grenade `victims=1, distance=0` → `"KOBE! … 1-man
grenade!"`; headshot `headshots=2` → `"2 headshots!"`; clutch `enemy_count=4` →
`"1v4"` (+ `win_probability=0.1` → `"defies 10% odds"`, unconditional `"#1v4"`
hashtag); squad-wipe `time_span=15` → `"in 15.0s"` (+ `placement=16` → `"#16 place"`).

**Fix (D-2 pattern, ONE shared evidence contract — no second path):** new helpers
`_evidence_number` (first non-`None` key; bools rejected; plain numeric strings parsed;
NaN/inf, non-numeric strings, containers, missing keys → `None`), `_evidence_positive_number`
(> 0), `_evidence_positive_count` (whole number ≥ a per-call floor), `_evidence_probability`
(`0 <= v < 1`). Each creator now renders WITHOUT the number/name when evidence is absent,
and excitement gets ZERO contribution from the missing value:

- **Grenade:** victims via count helper (floor 1), distance via `_evidence_distance`.
  Evidenced → `"{n}-man grenade"`; else plain `"KOBE! {player} with the grenade!"`.
  `needs_slow_motion` only on evidenced victims ≥ 3. Unevidenced excitement base 45.
- **Headshot:** count via count helper (floor 1). Evidenced → `"{n} headshots"`; else
  count-less `"ONE TAPS! {player}!"`. Unevidenced excitement base 40.
- **Clutch:** enemy_count via count helper (floor 2 — a 1v1 is not a clutch claim);
  win_probability via probability helper. `"1v{n}"` only when count evidenced; odds phrase
  only when probability evidenced; `"#1v4"` hashtag only when evidenced count is exactly 4.
  Unevidenced excitement base 80.
- **Squad-wipe:** time_span (positive number) and placement (count, floor 1) both gated.
  Four-way rendering (both / time-only / placement-only / neither); `duration_seconds =
  time_span + 8` only when evidenced, else a neutral fixed `8.0` clip window (same fixed
  window grenade clips use — a window length, never a rendered claim); no duration or
  placement phrase when unevidenced. Unevidenced excitement base 65.

**Explicit-`None` handling (sweep finding F-7, in scope because it is the live shape):**
`handle_event` builds evidence keys present-as-`None` on the commentary path; at HEAD the
old squad-wipe/grenade arithmetic raised `TypeError` on explicit `None`, swallowed by a
bare `except: pass` (no clip, no log, no publish). The helpers skip `None` values
(first non-`None` key wins; junk does not block fall-through to a later key), so no
creator raises on explicit `None` anymore. QA probed all 9 `clip_event_type`s ×
{keys-`None`, keys-omitted} on the fixed tree: no exception, exact publish counts,
no invented strings in any stored/published clip.

**Untouched by design:** `_create_multi_kill_clip` (already skip-when-insufficient),
`_create_long_range_clip`, `_create_final_circle_clip`, `_create_comeback_clip`,
`_create_funny_clip`, `detect_clip`, `_process_clip`, `handle_event`,
`check_weapon_highlight` (its `victims=1` is the single frag event itself, count never
displayed; `distance=0` is now correctly no-evidence — QA agreed). The `"(kill-feed icon
verified)"` description wording in `check_weapon_highlight` is a residual wording gap
(R-5): the function checks no source/confidence itself, though upstream gating is real.

## 3. Evidence-helper contract (as implemented)

| Helper | Rule |
|---|---|
| `_evidence_number(data, keys)` | First non-`None` key; bools rejected; plain numeric strings parsed (stripped); NaN/inf, non-numeric strings, containers, missing → `None`. Range/floor checks are the caller's job. |
| `_evidence_positive_number` | `_evidence_number` + `> 0`. Used for `time_span` and (via delegation) `distance`. |
| `_evidence_positive_count(data, key, minimum)` | `_evidence_number` + whole (`is_integer()`) + `>= minimum`. Floors: victims/headshots/placement 1; clutch enemy_count 2. |
| `_evidence_probability` | `_evidence_number` + `0 <= v < 1`. Exactly `1.0` is no evidence; `0` is evidenced. |
| `_evidence_weapon` / `_evidence_distance` | Unchanged key lists and semantics; distance delegates to the positive-number helper. |

**Judgment calls (all agreed by QA):** (a) plain numeric strings count as evidence for
counts/probabilities/time_span (mirrors the Phase 33 distance contract; pinned in tests);
(b) clutch floor 2; (c) squad-wipe unevidenced `duration_seconds=8.0` fixed window
(description carries no duration; the alternative, 0, would break `_process_clip`
thumbnail math); (d) probability 0 evidenced, 1.0 not; (e) `check_weapon_highlight`
untouched for the reasons above.

## 4. QA verdict: PASS-WITH-NOTES

Independent QA (read-only; own probes under `/tmp`; no file modified; never ran
`git stash`) reproduced every worker claim — no worker number was copied.

- **New suite** `backend/tests/unit/test_phase34_h1_h2_clip_evidence.py`: **160 passed**
  (full `_evidence_weapon` adversarial matrix; per-creator evidenced/partial/absent/junk
  matrices incl. bools, 0, negatives, NaN/inf, fractional counts, dicts/lists; exact
  excitement values proving zero default contribution; live-path pin that
  `check_weapon_highlight` frag events still re-title with excitement 60).
- **Phase 33 D-2 suite** (unmodified): **29 passed** — long-range matrix + `#Sniper`
  rule intact, H-1 fix included.
- **Adjacent suites** (`test_phase23_w2.py` 23 + `test_integrity_dormant_layer.py` 25):
  passed as part of a combined **237 passed** run (160+29+23+25).
- **Full backend suite** (`-o addopts=''`; pytest-cov NOT installed, stated plainly):
  collect **2281** = Phase 33's 2121 + exactly 160 new; **2216 passed / 63 failed /
  3 skipped**. Fail count identical to the Phase 33 ledger (63); pass delta exactly +160;
  zero failures in any Phase 34/D-2/Phase 23/dormant suite. The clip-touching e2e file
  fails at Redis `connect()` before any clip code runs (environment, not a regression).
  Honest limit: HEAD full suite was not re-run (stash forbidden), so per-test identity of
  the 63 vs Phase 33's 63 is inferred from count + area + sampled causes, not proven
  test-by-test. Outcome-sum quirk: 2282 outcomes vs 2281 collected (same +1 artifact as
  Phase 33), reported verbatim, unexplained.
- **Frontend:** `npx tsc --noEmit` exit 0; `npm run build` exit 0.
- **QA own probes:** 282 round-1 checks (8 initial failures all triaged as probe errors
  or one pre-existing defect, re-probed clean) + 30/30 round-2 = **312 passing
  assertions, 0 gating defects.** No H-1/H-2 leak on any probed path, no crash on any
  in-scope `handle_event` path, no D-2 regression, no attributable full-suite regression.
- **Security review:** diff contains no secret-shaped strings, no paid-API references,
  no director-grammar change; every added title/description fragment is
  evidence-conditional; the only fixed numbers in added code are neutral bases/windows
  already present in the file.

## 5. Sweep findings (read-only audit, HEAD archive) — what the fix sits on

- **ClipDetector chain is DORMANT in production:** `AgentOrchestrator` is never started
  by app code (zero `main.py` callers; only `backend/scripts/smoke_agents.py`). "Reachable"
  in this ledger means dispatch-reachable when the orchestrator runs or via direct
  `detect_clip` calls — no viewer renders a `ClipDetector` clip today. The live viewer
  highlight path is the separate replay-service scorer, which never uses `ClipDetector`.
- **`highlight.clip_ready` publishes** exactly 7 keys on the commentary path
  (`clip_id, category, quality, title, excitement_score, fight_id, match_id`) and 9 on
  the kill/knock path (plus raw `weapon` + `trigger`). No literal subscriber and no
  frontend consumer exist today, but bus aliasing (`highlight.clip` ↔
  `highlight.clip_ready` ↔ `highlight_moment`) means 5 `highlight_moment` subscribers
  would receive publications if the orchestrator ran. None is live today because the
  chain is dormant. `_process_clip` re-derives platform scores/thumbnail from
  excitement/duration, so invented scores used to propagate second-order — fixed at the
  creators by this phase.
- **No second weapon-extraction path renders:** `weapon_family` travels only as a sibling
  payload key; `gated_weapon` maps family→class as a label gate, not a display name;
  frontend renders the gated class string. The multi-feed monitor fallback passes a
  foreign detector's `weapon` verbatim and ungated (dormant risk, already M-2 backlog;
  no foreign detector wired).
- **Process note (honest):** the sweep worker's first probe run executed the sibling
  implementer's already-fixed code on the shared tree; those results were discarded and
  every probe was re-run against a `git archive HEAD` extraction. No files were harmed;
  final tree state below is the implementer's change only.

## 6. Files changed (verify before pushing)

Modified (1): `backend/app/agents/highlight_agents/clip_detector.py` (+257/−68).
New (1): `backend/tests/unit/test_phase34_h1_h2_clip_evidence.py` (657 lines).
This ledger: `docs/PHASE34_FINAL_LEDGER.md` (new; not counted in the QA diff stat above
— QA verified the tree before the ledger was written).
No deletions, no binaries, no workflows. `backend/test.db` absent (QA deleted its own
run artifact). Stash empty throughout (never used).

## 7. Honest limits

No real phone feed, no scrim, no RTMP push, no 720p re-measurement, no real Chromium
capture, no live-browser click-through, no real Postgres, no real `best.onnx` — all on
this VM, as before. pytest-cov gate NOT re-verified (not installed here). The fixes are
proven on dispatch/direct-call paths and the `handle_event` FakeBus path; the dormant
chain means no viewer-facing behaviour changes until/unless the orchestrator is wired —
at which point these gates are what stand between an unevidenced event and a viewer.

## 8. Explicitly NOT in this phase (backlog, not hidden)

- **H-3 (user-only, never touched):** git-history secret blob, tracked `.env` files,
  5-secret rotation, history purge. No `.env` file was read and no history was browsed.
- **Medium (11) + Low (10) verification backlog:** untouched, including margin-knob
  clamp, monitor-fallback gating, lockout dead code, API-key revocation, DB-error scrub,
  rotation enrichment, `resolve_winner`, create_all-only tables, test pollution fixes,
  docs re-issue.
- **Residual leaks found by QA/sweep during this phase (do NOT auto-start):**
  - **R-1 (Medium)** `_create_comeback_clip` `from_position=8` default still renders
    `"rises from #8!"`, exc 84. Reachable via `detect_clip` + commentary path.
  - **R-2 (Medium)** `_create_final_circle_clip` `teams_alive=2` default still inflates
    excitement to 90 (LEGENDARY), no number in text; explicit `None` raises `TypeError`
    on direct call (masked by `handle_event`'s bare `except: pass`). Same `None`
    fragility in comeback (`from_position=None`).
  - **R-3 (Low)** No allowlist in `_evidence_weapon`: `weapon="ar_a"` renders verbatim.
    No real producer found (all real weapon values are gated class strings or
    `None→"Unknown"`; commentary producer publishes no weapon) — defense-in-depth only.
  - **R-4 (Low-Medium)** `_create_funny_clip` caller-trusted title/description
    passthrough verbatim; not reachable via `handle_event` (title not forwarded).
  - **R-5 (Low)** `check_weapon_highlight` `"(kill-feed icon verified)"` wording asserts
    a check the function does not perform; upstream gating is real in practice.
  - **R-6 (Medium, LIVE)** Replay scorer `_sniper_triple` reason `"3 long-range
    eliminations"` checks only weapon class, never distance
    (`services/replay/highlight_triggers.py:120`; live via lifespan replay stack).
  - **R-7 (Medium-Low, LIVE)** Replay queue `score=int(payload.get("score") or 80)`
    (`services/replay/queue.py:689`); missing/0 score becomes 80 for underspecified
    candidates (kind + window still required).
  - **Pre-existing (for the record):** `_create_multi_kill_clip` with string keys
    raises `TypeError` (byte-identical at HEAD; swallowed by `handle_event`).
    Behaviour-preserving was required here; one-line hardening belongs to a future pass.
- RF-DETR flip, PNG/workflows upload, outreach, scrim: nothing done, as briefed.

## 9. Recommended next step for parent

1. Review the tree + this ledger.
2. Only while the user is present in chat: commit Phase 34 per-file (the 3 files in §6:
   `clip_detector.py`, the new test file, this ledger), push via the GitHub App
   connector, verify remote HEAD + spot-checks, then append the sync-record entry
   (update only after the push).
3. Do NOT auto-start the residual backlog (R-1…R-7) or the Medium/Low backlog without
   fresh user/parent direction — none is authorized by this task. If the parent wants
   R-1/R-2 folded in, they are the same fail-closed pattern and a small follow-up; R-6/R-7
   are live-path items and deserve their own decision.
