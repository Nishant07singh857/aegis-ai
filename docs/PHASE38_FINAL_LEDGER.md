# Phase 38 Final Ledger — Gate Fixes (C-1, H-2, M-1, M-3, M-4, M-5, M-7)

**Base:** local HEAD `8f3d33a` (Phase 37 commit, QA RE-GATE PASS-WITH-NOTES) =
remote `773b3bbc`.
**Date:** 2026-11-04. **Pattern:** coordinator + specialist workers (W1, W2, W3, W4,
W5) + independent QA/security reviewer (sign-off gates completion) + fix-up worker
(D-1 qualifier + this ledger).
**Source:** Final Verification Gate report
`~/workspace/drafts/aegis-final-verification-2026-11-04.md` — 1 Critical + 4 High +
14 Medium + 26 Low. Phase 38 scope: **C-1, H-2, M-1, M-3, M-4, M-5, M-7**.
**Not in scope:** H-1 (user-only secrets), H-3/H-4 (dormant-agent honesty,
docs-only follow-up), N-5 (not reproduced in gate run), DoD 1–6 user/live-proof
items, PNG/workflows, Kaggle `best.onnx`, scrim date.

## 0. Verdict

- Full suite (coordinator-run, this tree, `python3`, `-o addopts=''`, dossier
  ignored, log `~/workspace/aegis-phase38-full-suite.log`): **40 failed / 2641
  passed / 3 skipped, 1609.63 s (26:49), exit 1.** Gate baseline (frozen HEAD):
  52 / 2579 / 9. Diff (`comm` on both FAILED lists): **ZERO new failures**;
  12 gate failures now pass (§8). Arithmetic exact: +62 passed = 44 new Phase 38
  tests + 12 fixed + 6 previously-skipped now running/passing (9 → 3 skipped;
  the six are inferred from count arithmetic — the run used `-q` without `-rs`,
  so individual test IDs are not recoverable from the logs).
  Remaining 40 failures are the documented pre-existing/env set (§8).
- Frontend: **tsc 0 errors, build exit 0.**
- QA verdict: **FINAL PASS-WITH-NOTES (round 2, §9)** — round 1 was provisional
  PASS-WITH-NOTES (full-suite numbers had not landed); round 2 confirmed the
  suite numbers from the logs (§8), established the 6-e2e cause (C-1 fallback,
  §8), and re-checked the 3 new test files 44/44 in the current tree. Per-item:
  C-1 CONFIRMED, H-2 CONFIRMED, M-1 CONFIRMED, M-3 CONFIRMED, M-4 CONFIRMED,
  M-5 CONFIRMED, M-7 ADJUSTED (D-1 fixed), Scope ADJUSTED, Regression CONFIRMED
  on the suite numbers. Defects D-1 (fixed by the fix-up, §7), D-2 (recorded,
  §7), D-3 (closed by this file), D-4 (observation, strengthen later — not a
  Phase 38 fix). Round 2 found 6 minor ledger defects (L-1…L-6, numbers/wording
  only, §9) — all corrected in this file by the coordinator; no code re-gate
  required.

## 1. C-1 Redis-down live spine silently dead — FIXED (W1, real in-memory fallback)

The gate's Critical: Redis down → live spine silently dead, app still shows
"ready"; the "in-memory fallback" log line was false. The fix is a **real**
in-memory fallback, verified sound: **no Redis pub/sub consumer exists anywhere;
every subscribe is in-process; Redis `xadd` is persistence/replay-only.**

- **`backend/app/core/event_bus.py` (+309/−64):** `connect()` enters
  `_enter_memory_mode()` after 5 retries instead of raising; new `_transport`
  (`"redis"` | `"memory"`); `_start_background_tasks()` guarded (fixes the old
  duplicate-task bug on re-connect); `_try_redis_reconnect()` single-attempt
  probe per 10 s health tick, upgrades back to Redis; `publish()` skips Redis
  `xadd`/`publish` in memory mode, dispatch/history/queue unchanged;
  `publish_batch()` memory branch + not-running guard; `disconnect()` resets
  transport; `health_check()` "healthy" only on Redis, memory mode → "degraded"
  + `"mode"` field; `get_stats()` gains `"mode"`.
- **`backend/app/main.py`:** the false `"continuing with in-memory broadcasting"`
  log line DELETED; the except branch now logs an honest `logger.error`;
  `_system_status_payload()` adds `"event_bus"` (`connected` | `memory` |
  `disconnected`) and `"live_spine"` (`ready` | `degraded` | `down`).
- **Measured on this VM (no Redis server):** before — `connect()` raises after 5
  retries (~15 s), Redis-down boot leaves all 6 spine services `None`; after —
  `connect()` returns, `transport=memory`; Redis-down TestClient boot **17.0 s /
  25.3 s / 16.8 s** (3 runs; 15 s is the real retry backoff); all 6 spine
  services built (`live_director`, `vision_intelligence`, `multi_feed_monitor`,
  `observer_pilot`, `outcome_labeler`, `rotation_bridge`); `GET
  /api/v1/system/status` → `event_bus: memory`, `live_spine: degraded`;
  publish → subscriber delivered (`event_id` returned, `events_published=1`,
  `dropped=0`); recovery probe upgrades memory → redis (asserted on fake
  client).
- **Tests:** new `backend/tests/unit/test_phase38_w1_c1_redis_fallback.py`,
  **15/15 pass** (3 consecutive runs; lifespan test run 12× in isolation,
  12/12 passes — one test, twelve runs).
  Affected suites **84 passed**. `test_event_bus.py::test_connect` FAILED at
  HEAD, now passes via fallback; **no test edited.**
- **Residuals:**
  1. Intermittent TestClient boot+shutdown hang observed 3× by W1, never
     reproduced in isolation (QA also 3/3 clean, ~33–35 s each,
     `PYTHONFAULTHANDLER=1`) — suspected pipe/SIGPIPE interaction, **not
     established**.
  2. Lifespan shutdown never stops `multi_feed_monitor` / `observer_pilot`
     (pre-existing; `stop()` methods exist, not called).
  3. Memory mode loses Redis Stream persistence/replay-from-stream +
     cross-process broadcast (replay still works from in-memory history; no
     multi-process deployment exists).
  4. Recovery probe is a single ping per 10 s tick; `connect()` inside the probe
     can take ~15 s of the health-loop task.
  5. Real-Redis behaviour covered with fake client only (no Redis server on
     this VM).

## 2. H-2 inventory stale + guard test FAILS — FIXED (W2, doc-only correction)

- **Re-measured at HEAD `8f3d33a`:** py **461**, LOC **129,085**, docs **50**,
  tests **2639** collected (0 errors with `tests/unit/test_dossier_pdf.py`
  ignored; 1 collection error without — fpdf absent), endpoints **212** (210
  HTTP + 2 WS; grep gives 213, the 213th is a docstring false positive at
  `db/base.py:643`), migrations **15** (001, 005–018), tables **34**, frontend
  routes **34**, tsc **0**, build exit **0**.
- `docs/PROJECT_INVENTORY.md` corrected (**26 insertions, 13 deletions**); guard
  test `test_inventory_counts_match_remeasurement` was 1 FAILED before, full W5
  file **39/39 pass** after; **no test file edited.**
- **Note recorded:** Phase 37 ledger §8 ("VERIFIED UNCHANGED") was measured at
  `941a13b`, not at the Phase 37 commit — the same failure mode as the Phase 36
  fix-up (deltas +1 doc, +474 LOC, +52 tests, +1 endpoint). The Phase 37 ledger
  itself is untouched (historical record).
- **Residuals:** per-module endpoint table in §1 still sums to the 2026-10-20
  snapshot (169), stated in the doc; fpdf uninstalled so dossier-PDF tests are
  uncollectable here (pre-existing env limit).

## 3. M-1 on-air feed watched by the monitor — FIXED (W3, live exclusion)

Live exclusion, not build-time-only (`_on_air_slot` starts `None` at boot; the
director moves it via `set_on_air`).

- **`backend/app/vision/multi_feed_monitor.py` (74 lines):**
  `on_air_slot_provider` in `__init__`, `_current_on_air_slot()` (best-effort —
  raising/invalid provider → `None`), `excluded_slots` property (static ∪ live
  on-air), `_slot_is_excluded()`; `add_feed` rejects a live on-air slot;
  `sync_from_grabber` skips adopting it and drops an already-adopted feed if
  on-air moves onto it mid-match, re-adopts once on-air moves away; builder
  derives the provider from `multi_device_manager.get_on_air`.
- **`backend/app/main.py:1843` comment only** — lifespan already passes the
  manager. Dedup untouched.
- **Residuals:** provider raises/absent → fails OPEN (watches everything, static
  set still applies), documented; exclusion is enforced at adoption/sync, not
  instantly.

## 4. M-4 `start_scrim` lobby id — FIXED (W3, honest placeholder)

- **`backend/app/main.py:648–665` — "honest placeholder" branch.** Fail-closed
  would have broken the Phase 32 kill-switch test, which calls `start_scrim`
  with no lobby id and expects success; **no caller passes a lobby id**.
  Caller-supplied `lobby_id` is used verbatim; absent/blank/non-string →
  `lobby_id None` + `"lobby_id_unset": true` in `scrim_state`; **no
  `LOBBY-<digits>` is ever produced.**
- **Residual:** `start_scrim` still returns success with an unset lobby.

## 5. M-5 unevidenced clip quality labels — FIXED (W3)

- **`backend/app/agents/highlight_agents/clip_detector.py` (22 lines):**
  `quality_label = f"{quality.value.upper()}!"`; all four description branches
  use it (unevidenced clutch 80 → `EXCELLENT!`; LEGENDARY only with evidenced
  `enemy_count >= 4` → 95); `replay_value=100` → `replay_value=excitement`.
  Clip chain stays dormant, no wiring changed.
- **Residual:** other creators' hardcoded replay values (`:869`, `:954`,
  `:1043`) out of scope.
- **W3 tests:** `backend/tests/unit/test_phase38_w3_m1_m4_m5.py`, **20/20
  pass**; non-vacuity **19 failed / 1 passed** (evidenced-1v4 positive control)
  on HEAD-restored files; affected suites **408 passed** + Phase 32 WS auth &
  Phase 26 lifespan **5 passed / 1 skipped** (fakeredis absent, pre-existing).

## 6. M-3 raw credential-shaped exceptions in event bus / async utils — FIXED (W1 + W4)

- **`event_bus.py` 4 sites** (`:334` connect, `:620` publish, `:843` subscriber
  dispatch, `:920` queue loop) scrubbed via the Phase 37 `_scrubbed_exc_info()`
  surrogate (lazy-delegated to `app.db.base`) + a **5th site beyond the brief**
  (`_persist_event` raw `str(e)`, a DB error by construction) now
  `_scrubbed_text(e)`; **zero `exc_info=True` calls remain (AST-verified)** —
  the two remaining `exc_info=True` strings in the file are docstring/comment
  mentions only (fix-up verified).
- **`backend/app/utils/async_utils.py:216`** (was `:180` at HEAD) scrubbed (module-local
  `_scrubbed_exc_info` mirror — local mirror deliberate: `error_scrub` is
  stdlib-only, importing `app.db.base` would drag SQLAlchemy into leaf utils).
- **Sweep found + scrubbed 4 more `traceback.format_exc()` sites** in
  `backend/app/agents/base_agent.py` (`:343` start failure, `:708`/`:713`
  `_handle_tick_error`, `:767` `_attempt_recovery`) via `_scrub_error_text()`,
  incl. scrubbing text stored in `metrics` / `last_error` / `_error_history`
  (surfaces via `get_status()`). `deps.py` / `base.py` verified still clean
  (13 grep-counted `_scrubbed_exc_info` references: 5 in `deps.py`, 8 in
  `base.py`).
- **W4 tests:** `backend/tests/unit/test_phase38_w4_m3_scrub.py`, **9/9 pass**;
  non-vacuity **6/9 fail** on un-fixed HEAD (throwaway worktree); affected
  suites **12 failed / 290 passed vs HEAD 15 failed / 287 passed — zero
  regressions**, all failures pre-existing (commentary-agent 6, team_agent 1,
  observer-agent 4); 3 extra HEAD failures (VoiceSynthesizer ×3,
  `test_event_bus::test_connect`) are order/env-flaky.
- **Residuals:** observed-but-untouched bare-`{e}` log sites
  (`feed_health_monitor.py` `last_error`, `multi_feed_monitor.py:1629`,
  `match_clock.py:100`, `phase_tracker.py:174`) — a full `{e}`-in-log sweep is a
  separate, larger job; `create_task_safe` has zero callers (dormant channel,
  scrubbed anyway).

## 7. M-7 scrim checklist refresh — DONE (W5) + D-1 qualifier (fix-up)

- **NEW file** `~/workspace/drafts/aegis-scrim-test-checklist-2026-11-04.md`
  (Phase 37 edition, base HEAD `8f3d33a` = remote `773b3bbc`, marked CURRENT;
  old 2026-11-01 file untouched by W5, md5 `497909f7d25ce45b6752da0c5039e559`
  before and after). **21 changed lines (17 added / 4 removed)** vs Phase 36 edition: headline warning
  corrected; new "Badla kya" section (FINAL_CIRCLE/MATCH_END reachable,
  THIRD_PARTY on 3-team fights, `full_dead_count` → `is_eliminated()` can be
  True, replay cut-only scoped, R-7 reframed as documented behaviour,
  "not scrim-visible" note); test script item 17 + success-criteria + gotchas
  lines added. All claims verified vs `docs/PHASE37_FINAL_LEDGER.md` + code.
  All standing scrim guidance kept verbatim.
- **D-1 qualifier — added by the fix-up (this phase, Job 1).** QA found
  checklist claims 2 (FINAL_CIRCLE/MATCH_END now reachable) and 5 (replay
  transitions cut-only) describe dormant/superseded code as scrim-visible.
  The fix-up **verified this independently against the code** before editing:
  - The F4 branch-order fix lives in `ObserverDirector._update_broadcast_state`
    (`backend/app/agents/observer_agents/director_ai.py:940` area). `ObserverDirector`
    is **never constructed in production** — the only `ObserverDirector(...)`
    call sites are tests (`test_observer_agent.py`, `test_director_camera_bible.py`,
    `test_phase22_w3.py`, `conftest.py` fixture). The orchestrator deliberately
    never constructs it (`orchestrator.py:144`, `observer_director = None`,
    docstring: superseded by `app/services/live_director`, "the live director is
    the one and only director"); no orchestrator is instantiated from `main.py`
    at all. `BroadcastState` is referenced **nowhere else** in `backend/app`
    outside `director_ai.py`. The live director (`services/live_director/`)
    has its own phase model (`"final_circle"` as a PhaseTracker phase string)
    and no broadcast-state concept.
  - `ReplayDirector` is constructed **only** in the dormant orchestrator
    (`orchestrator.py:471`); the live replay path (`app/services/replay/` —
    `ReplayService` + queue, wired from `main.py`) contains **no transition
    concept** (the only "transition" hit in `services/replay/` is a docstring
    word in `queue.py:920`).
  - One qualifier paragraph was added to the new checklist in the "Badla kya"
    section, immediately after claim 5, covering claims 2 and 5: both are
    code-level Phase 37 behaviours in superseded/dormant paths, **not directly
    observable on the live scrim path**; test-script item 17 and the
    success-criteria FINAL_CIRCLE / MATCH_END / replay-cut lines inherit the
    same caveat; the live director's own behaviour (phase lock, WWCD path)
    should be checked instead of `BroadcastState` values. **Neither claim was
    deleted; no observability was invented.** The old 2026-11-01 checklist
    file was not touched by the fix-up.
- **D-2 note (old checklist banner):** the old 2026-11-01 file later gained a
  SUPERSEDED banner (mtime 2026-11-04 09:42:58 UTC, md5 now
  `b37599fb7e76b0e9162ee76c33f07338` — fix-up re-verified this md5) — added by
  an **unknown party during QA, NOT by any Phase 38 worker or QA**; body
  intact, banner accurate; attribution unestablished (**most likely the parent
  agent — recorded as such, honestly**).

## 8. Full suite + frontend — coordinator run (this tree)

- Command: `cd backend && python3 -m pytest tests
  --ignore=tests/unit/test_dossier_pdf.py -o addopts='' -q -p no:cacheprovider`.
  Log: `~/workspace/aegis-phase38-full-suite.log`.
- **Result: 40 failed / 2641 passed / 3 skipped, 1609.63 s (26:49), exit 1.**
  Gate baseline (frozen HEAD): 52 / 2579 / 9.
- **Diff (`comm` on both FAILED lists): ZERO new failures; 12 gate failures
  now pass** — `test_event_bus::test_connect` (C-1), inventory guard test (H-2),
  4 voices-artifact (`test_shipped_voices_dir_matches_disk` + 3×
  VoiceSynthesizer — gate ran in a bare worktree without untracked
  `.local-voices/`), 6× e2e (`test_detect_fight_to_scene_switch`,
  `test_phase_2_match_start`, `test_phase_3_early_game_fights`,
  `test_phase_4_mid_game`, `test_phase_5_late_game`,
  `test_phase_6_final_circle` — **cause ESTABLISHED by QA round 2 (§9): the
  C-1 fallback** — the gate log shows `test_phase_2_match_start` failing at
  `event_bus.connect()` → `redis.ping()` → `ConnectionError`, and round 2's
  probe in a throwaway unfixed-HEAD worktree failed all 6 e2e tests with the
  identical Redis error while the fixed tree passes `test_phase_2_match_start`
  (20.17 s). Caveat: the 6 e2e tests contain zero assertions, so passing proves
  only that `connect()` no longer raises.
- **Arithmetic:** +62 passed = 44 new Phase 38 tests + 12 fixed + 6
  previously-skipped now running/passing (9 → 3 skipped; the six are inferred
  from count arithmetic — the run used `-q` without `-rs`, so individual test
  IDs are not recoverable from the logs).
- **Remaining 40 failures are the documented pre-existing/env set:** e2e 7,
  OBS 19, commentary 6, observer 4, team 1, minimap 2, player-tracker 1.
  (Fix-up verified the count and breakdown from the log: 40 `^FAILED` lines,
  same composition.)
- Frontend: **tsc 0 errors, build exit 0.**

## 9. Independent QA / security review — FINAL PASS-WITH-NOTES (rounds 1 + 2)

Report: `~/workspace/drafts/aegis-phase38-qa-report.md` (QA run ~09:35–10:00 UTC /
~15:05–15:30 IST). Method: brief + gate report read; every fix verified with
the reviewer's own probes (21-check probe script `/tmp/qa_p38_probes.py`, kept
out of the repo) plus re-runs of the team's tests and directly affected suites.
Non-vacuity: the 44 new Phase 38 tests copied into a throwaway HEAD worktree —
**31 failed, 9 errors, 4 passed** (vs 44/44 fixed); own probe script 21/21
pass. W1 hang not reproduced by QA (3/3 clean, ~33–35 s each). **Full-suite
numbers had not landed when it finished — verdict provisional.**

- **Verdict: provisional PASS-WITH-NOTES.** Per-item: C-1 CONFIRMED, H-2
  CONFIRMED, M-1 CONFIRMED, M-3 CONFIRMED, M-4 CONFIRMED, M-5 CONFIRMED, M-7
  ADJUSTED (D-1), Scope ADJUSTED, Regression PARTIAL (pending suite).
- **Defects:**
  - **D-1 — checklist claims 2+5 — FIXED by the fix-up (§7).**
  - **D-2 — old checklist banner attribution — recorded above (§7).**
  - **D-3 — this ledger did not exist at QA time — this file closes it.**
  - **D-4 — H-2 guard test doesn't assert headline LOC/tests/endpoints
    numbers — recommend strengthening later; not a Phase 38 fix.**
- **What QA did not verify:** full backend suite (now landed, §8); frontend
  tsc/build (no frontend files in the diff); live Redis (no server on this VM);
  no real phone feed / scrim / 720p; Kaggle training state (out of scope).
- **Round 2 (final)** — report `~/workspace/drafts/aegis-phase38-qa-report-round2.md`:
  **FINAL VERDICT: PASS-WITH-NOTES.** Confirmed the suite numbers from the logs
  (§8: 40/2641/3, zero new failures, 12 fixed, arithmetic exact); **established
  the 6-e2e cause as the C-1 fallback** (gate log shows `test_phase_2_match_start`
  failing at `connect()` → `redis.ping()` → `ConnectionError`; round-2 probe in a
  throwaway unfixed-HEAD worktree failed all 6 with the identical Redis error,
  fixed tree passes `test_phase_2_match_start` in 20.17 s; caveat — the 6 e2e
  tests contain zero assertions, so passing proves only that `connect()` no
  longer raises); D-1 fix CONFIRMED (qualifier present and accurate, no claim
  deleted); re-checked the 3 new test files **44/44** in the current tree and the
  diff still exactly the expected set. Round 2 found 6 minor ledger defects
  (L-1…L-6, numbers/wording only) — L-1 event_bus diff `+309/−64` (not
  `~+190/−60`), L-2 async_utils scrub site `:216` (not `:180`), L-3 checklist
  `21 changed lines (17 added / 4 removed)` (not `18-line diff`), L-4 the six
  skipped→passing tests are inferred from arithmetic, not named/recoverable,
  L-5 lifespan test is one test run 12× (not 12 tests), L-6 `13` grep-counted
  `_scrubbed_exc_info` references (not `12 usages`) — all corrected in this file
  by the coordinator; **no code re-gate required.** D-2 (banner attribution) and
  D-4 (guard-test weakness) remain carried notes.

## 10. Files changed (total)

7 modified + 3 new test files (coordinator `git status` at HEAD `8f3d33a`;
fix-up verified the same set; the ledger itself is an additional new file;
the new checklist draft lives outside the repo):

- Production: `app/agents/base_agent.py`, `app/agents/highlight_agents/clip_detector.py`,
  `app/core/event_bus.py`, `app/main.py`, `app/utils/async_utils.py`,
  `app/vision/multi_feed_monitor.py` (6 files)
- Docs: `docs/PROJECT_INVENTORY.md` (1 file)
- New tests: `tests/unit/test_phase38_w1_c1_redis_fallback.py` (15 tests),
  `tests/unit/test_phase38_w3_m1_m4_m5.py` (20 tests),
  `tests/unit/test_phase38_w4_m3_scrub.py` (9 tests)
- New ledger: `docs/PHASE38_FINAL_LEDGER.md` (this file)

New tests total: **+44** (W1 15 + W3 20 + W4 9). No test file edited.

## 11. Backlog — what remains open (with reasons)

1. **QA round 2 — DONE (§9):** 6-e2e cause established (C-1 fallback); final
   verdict PASS-WITH-NOTES; L-1…L-6 corrected in this ledger.
2. **D-4:** strengthen `test_inventory_counts_match_remeasurement` to assert
   headline LOC (129,085), tests (2639), endpoints (212) — later phase.
3. **C-1 residuals (§1):** TestClient boot/shutdown hang (mechanism not
   established); lifespan shutdown never stops monitor/pilot (pre-existing);
   memory-mode loss of Redis Stream persistence/replay + cross-process
   broadcast; recovery probe ~15 s inside the health loop; real-Redis upgrade
   unproven on a live server.
4. **M-3 residuals (§6):** bare-`{e}` log sites left untouched (full sweep is a
   separate, larger job); `create_task_safe` has zero callers.
5. **M-1/M-4/M-5 residuals (§3–§5):** fail-open provider; exclusion at
   adoption/sync only; `start_scrim` success with unset lobby; hardcoded replay
   values at `:869`/`:954`/`:1043` out of scope.
6. **H-2 residuals (§2):** per-module endpoint table still sums to the
   2026-10-20 snapshot (169), stated in the doc; dossier-PDF tests
   uncollectable without fpdf2 (pre-existing env limit).
7. **40 pre-existing/env failures (§8)** — left with per-area reasons; not
   Phase 38's to fix wholesale.
8. **Out of scope, untouched:** H-1 (user-only secrets), H-3/H-4
   (dormant-agent honesty, docs-only follow-up), N-5 (not reproduced in gate
   run), DoD 1–6 user/live-proof items, PNG/workflows, Kaggle `best.onnx`,
   scrim date.

## 12. DoD 7 statement

DoD 7 moves from PARTIAL to **documented-clean on the H-2 point** (the 1 NEW
failure from Phase 37's commit is green) **AND the suite improved 52 → 40
failed with zero new failures**; remaining 40 are the documented
pre-existing/env set (§8).
