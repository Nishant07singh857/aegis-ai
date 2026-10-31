# Phase 35 Final Ledger — Code Verification MEDIUM Backlog (11 items)

**Base:** local HEAD `428a4af` (Phase 34, QA PASS-WITH-NOTES; remote main `3b5a0ff5`).
**Status:** code complete, independent QA **PASS-WITH-NOTES** (4 notes, none gating production behaviour; items 4, 5, 9, 10 graded PARTIAL — see §8). No commit/push by the team; work remains in the working tree for parent review. `git stash` was never run by any worker or QA.

## QA verdict (independent, read-only, all numbers re-derived)

- **New Phase 35 suites (QA re-ran):** W1 margin/fallback 59 pass; W2 auth controls 17 pass; W3 rotation/payoff 12 pass; W4 scrub/migration 17 pass; W5 pollution proof 6 pass. **Total new: 111 tests exactly** (collect 2392 = 2281 Phase-34 ledger + 111).
- **Full backend suite (QA, `-o addopts=''`, pytest-cov NOT installed so the cov gate is NOT verifiable here): 2328 passed / 62 failed / 3 skipped**, collect 2392 (outcomes total 2393, the same unexplained +1 discrepancy noted in prior verification; immaterial). All 62 failures are pre-existing/env areas (e2e 13, OBS drift 19, dossier-PDF/fpdf 5, event-bus/Redis 1, commentary 7, observer 6, team 3, coordinate-mapper 3, minimap 2, player-tracker 2, fight-agent 1 deterministic standalone failure in `test_determine_third_party_fight` — pre-existing drift, `fight_detector.py` untouched by Phase 35). **Zero failures in any Phase 29/31/32/33/34/35 file.** Regression subsets: 478 passed across Phase 29/31/32/33/34 files; RF-DETR suite passes within its batch.
- **Frontend (QA):** `npx tsc --noEmit` exit 0; `npm run build` exit 0 (35 static pages).
- **Security review:** PASS-WITH-NOTES — no `.env`/history touched, no secret values in the diff (shape-only scan), no paid-API references, no new fabrication path, no PiP-during-fight change, no director-grammar change. Residuals are Notes N-1 and N-4 below; neither is a demonstrated live leak/bypass.

## §1 Margin-knob clamp — COMPLETE

`IconMatcher` threshold now clamps to [0.15, 1.0] on construction and mutation; NaN/non-numeric → 0.15; ±inf → ceiling/floor; always finite. QA probes: `margin_threshold=-1.0/0.0/0.149/0.05` with margin 0.10 → `None` in all cases. Float fail-closed behaviour preserved (`0.70−0.55` abstains, `0.80−0.65` confident). Minor note: the property getter returns a directly-written `_margin_threshold` verbatim, so a private-attribute write could desync the abstention flag (the label stays safe because `gated_weapon` re-clamps). Prescription: clamp on read too.

## §2 Monitor-fallback gating — COMPLETE

Fallback foreign-detector weapons now pass through the Phase 32 `gated_weapon`/abstention contract. QA's 18 own cases: confusable margin, no-margin evidence, model-name, family-id, dict weapon, flag mismatch, NaN/inf/negative margin — all → `weapon=None, weapon_abstained=True`; clean gated passes ship with `abstained=False`. Flag and label agree on every claimed-label path; the claimed family survives only as diagnostic `icon_family`.

## §3 Account lockout — COMPLETE

Lockout is real: 5 consecutive failed passwords → `locked_until = now + 900s`; attempts while locked do not extend the lock; after expiry a correct password works and one typo restarts at count 1; DB failure → deny without crash. Stated limitation (in code): the settings-only operator credential has no `User` row, so its mitigation remains the 5/min rate limit; `UserService.authenticate` has no lockout side effects but zero production callers (dormant, not a bypass).

## §4 API-key revocation — PARTIAL (do not call complete)

The mechanism works: `revoke_api_key()` records only the SHA-256 digest in a thread-locked in-memory set; `_verify_api_key` — the single choke point for REST and WebSocket — rejects revoked keys on the next request; other keys and JWT auth unaffected; no raw key stored or logged. **But the operator has no working revocation path:** zero production callers of `revoke_api_key()`, and the WS `revoke_key` handler still reports restart-only. The registry is in-memory only: a restart resurrects a revoked key unless it is also removed from `ADMIN_API_KEYS`. Prescription (N-4): wire an operator-only REST/WS action to `revoke_api_key()`, fix the `revoke_key` message, document "revoke in-process AND remove from config" in the runbook. A DB/file-backed registry is a separate phase with a migration/runbook decision.

## §5 DB-error scrub — PARTIAL

The scrubber (`backend/app/db/error_scrub.py`, stdlib-only) is correct on all QA cases: URL userinfo, `DATABASE_URL=` assignment, password/secret/token kv and quoted forms, Bearer/JWT, `sk-`/`ghp_`/`AKIA` shapes, verbatim known secrets; innocent text preserved; idempotent; never raises; surrogate-exception tracebacks leak-free. The Phase 33 boot site is fixed and proven. **But the structural guarantee is not achieved** (N-1): `backend/app/db/base.py` has zero scrub calls (raw `{e}` logging at 10+ sites, several with `exc_info=True`; `get_db_with_retry` re-raises the raw exception), `backend/scripts/init_db.py` logs raw errors, and the login helpers in `main.py` log raw DB exceptions — which also falsifies the module comment claiming every DB exception passes through the scrubber. Prescription: apply the scrubber at all 10+ sites, re-raise via `scrubbed_exception`, scrub the script + login helpers, or correct the comments to state the exact scrubbed scope.

## §6 Rotation enrichment — COMPLETE

Wired end-to-end: bus `rotation_event` → translator (enrichment gate: non-finite bearing/speed, negative speed dropped, never passed as measured) → bridge forwards all fields → `note_rotation(rotation_mode, bearing_deg, speed_px_s, occupancy_basis)` → `EngagementTracker` stores `rotation_detail` in the engagement snapshot. QA end-to-end probe: every field intact. **Audit-only, never scored:** mode alone never creates PRE_COMBAT, bearing/speed never affect scoring, `_derive` never reads `rotation_detail`; detail cleared on `rotation_ended` and past the 20s TTL; no physical units anywhere (px/s, screen degrees only).

## §7 `resolve_winner` dead code — COMPLETE, decision ACCEPTED

Decision: keep the builders, fix the stale docstring — do NOT wire, do NOT delete. QA verified: zero production callers outside `payoff.py`; stale "called from `_prune`" claims gone; `_team_alive_at_resolve` appears only in HOOK STATUS text stating it was never built. Wiring **would** fabricate: QA proved synthesised `{teamA:1, teamB:0}` → winner `teamA`, and single-team `{teamA:1}` → winner where `_prune`'s wipe-inference correctly withholds. The builders are marked TESTED-BUT-UNWIRED with a prohibition on synthetic counts, and are the correct exact-count path if a real per-team alive-count source ever lands. `_prune` untouched.

## §8 Migration 017 — COMPLETE

Chain verified: `001→005→…→016→017`, `alembic heads` = single head `017_match_stats_tables`. Schema matches the models exactly: QA's own scratch-DB proof (under `/tmp`, never in the tree) — stamp-at-016 → `upgrade head` creates both tables with PRAGMA columns/indexes/FKs identical to `create_all`; second upgrade no-op; downgrade to 016 drops both; re-upgrade recreates; create_all-first DB upgrades cleanly via the inspector guard. Honest limit: a literal fresh `alembic upgrade head` on sqlite fails at **pre-existing** migration 009 (`ALTER TABLE players ALTER COLUMN team_id DROP NOT NULL` — sqlite syntax error, chain stops at 008), exactly as stated in the migration header; no Postgres exists here to prove the full chain there, and 017 uses only portable DDL.

## §9 Test pollution — PARTIAL

Verified: Phase 33 stub teardown restores `sys.modules` exactly and evicts stub-bound modules (bare re-import then fails honestly); both narrowed skip sites are `ImportError`-only; fixture-level DB isolation proven (committed write invisible to the next test; same-slug fixture reusable); `test_get_fight_detail` fixed test-side (`sorted` assertion, equally strong). **Known partials confirmed:** dossier-PDF tests are unpassable without real fpdf2 (standalone collection error; in-suite 5 failures when another file's import-time stub is collected — outcome is collection-order-dependent); production `get_fight` ordering is still nondeterministic (`list(set)` at `fight_detector.py:1349,1426,1451` — production-code follow-up needed, `sorted(...)` precedent exists in the same file); suite-wide DB isolation not done (conftest docstring states this honestly); the Phase 26 narrowed branch never executes here (fakeredis absent). **New Phase 35 leak (N-2):** the W4 test file installs the fpdf stub with no teardown and a blanket `except Exception → skip` — reuse the Phase 33 context manager and narrow the skip. Order-independence: affected subsets in both orders give identical outcomes.

## §10 Docs re-issue — PARTIAL

SPECTATOR_COVERAGE substantially correct: QA recounted all 30 matrix rows — **COVERED 1 / PARTIAL 25 / GAP 4 / INCOMING 0**, matching the printed roll-up; the 61 signal-registry note is arithmetically exact (Layer-1 30 + Layer-2 D1–D14 + Layer-3 P0–P16, counted row by row); `FeedEventType` = 17 exactly; row-21 wording matches the final tree. **But STREAMING.md contains a false claim (N-3):** §3/§7 say `detect_encoder` is "used by the highlight renderer and the deprecated StreamService" — the sole caller is `replay/renderer.py:398`; `streaming/service.py` never imports `encoder.py` and hardcodes `libx264`. Prescription: delete "and the deprecated StreamService" in both places. The NVENC-probing and video-only/`-an` substance is correct. Minor: carried-forward stale line refs (`killfeed.py:884`→986; grenade-trajectory `main.py:1522`→~1579).

## §11 Rate limits — COMPLETE

QA ASGI probes with Redis down (forced fallback): feed-token mint 30×200 then 429; rule table verified (auth 5, feed-token 30, video/director/stream/users 30–60, dossier ingest 60, feed/admin GET 120, WS handshakes 60 — handshakes only, never messages); UI-polled GETs and unlisted writes deliberately unthrottled (documented, not an oversight); Redis-down still limits via the in-memory limiter; limiter failure → HTTP 429 with `Retry-After: 60` and WS close 1011, while unthrottled paths still return 200. K-C1 mint happy path intact at sane rates.

## Notes carried forward (QA prescriptions, NOT fixed in this phase)

- **N-1** Scrub adoption incomplete (item 5) — apply the scrubber in `db/base.py` (10+ sites), `scripts/init_db.py`, login helpers; or correct the scope comments.
- **N-2** W4 test file reintroduces the stub leak (item 9, test-only) — reuse the Phase 33 context manager, narrow the skip.
- **N-3** STREAMING.md false caller claim (item 10, doc-only) — delete "and the deprecated StreamService" in §3/§7.
- **N-4** No operator revocation path (item 4) — wire an operator-only action to `revoke_api_key()`; fix the `revoke_key` message; document in-process + config removal.
- Minor: margin-getter private-attribute bypass (clamp on read); SPECTATOR_COVERAGE stale line refs; production `get_fight` `list(set)` ordering; `test_determine_third_party_fight` deterministic standalone failure (pre-existing drift, untouched).

## Explicitly NOT in this phase (backlog, not hidden)

H-3 (git-history secret blob, tracked `.env`, rotation, purge — user-only; never touched); Low backlog (10); residuals R-1…R-7 (Phase 34 — R-6/R-7 are live replay-path items needing their own decision); deferred builds (position source, full_auto banner, zone template, clutch stickiness, lull retraction, tap-verifier rework, infra); RF-DETR flip (registry untouched, zero `*.onnx` tracked); PNG/workflows; outreach (paused); scrim. Full suite and frontend numbers above are QA's own runs on the final merged tree.

## Standing limits

No real phone feed, scrim, RTMP push, 720p re-measurement, Chromium capture, live-browser click-through, real Postgres, or real `best.onnx` — all on this VM, as before. pytest-cov gate NOT verifiable here (not installed; verify in CI). Dossier-PDF tests cannot pass without real fpdf2. A literal fresh sqlite `alembic upgrade head` fails at pre-existing migration 009. Revocation is in-memory only.

## Files in this phase (22 — verify before pushing)

Modified (15): `backend/app/api/deps.py`, `backend/app/core/rate_limit_middleware.py`, `backend/app/main.py`, `backend/app/services/live_director/engagement.py`, `backend/app/services/live_director/payoff.py`, `backend/app/services/live_director/rotation_intake.py`, `backend/app/services/live_director/service.py`, `backend/app/vision/detectors/icon_matcher.py`, `backend/app/vision/multi_feed_monitor.py`, `backend/tests/conftest.py`, `backend/tests/integration/test_phase26_lifespan_monitor.py`, `backend/tests/unit/test_phase33_db_boot_fail_fast.py`, `backend/tests/unit/test_vision/test_agents/test_fight_agent.py`, `docs/SPECTATOR_COVERAGE.md`, `docs/STREAMING.md`.
New (7): `backend/alembic/versions/017_match_stats_tables.py`, `backend/app/db/error_scrub.py`, `backend/tests/unit/test_phase35_w1_margin_fallback.py`, `backend/tests/unit/test_phase35_w2_auth_controls.py`, `backend/tests/unit/test_phase35_w3_rotation_payoff.py`, `backend/tests/unit/test_phase35_w4_db_scrub_migration.py`, `backend/tests/unit/test_phase35_w5_pollution_proof.py`.
No deletions, no binaries, no workflows.

## Addendum — QA notes N-2 + N-3 folded in (pre-commit follow-up)

Small follow-up after the QA verdict above, before commit. Original
sections and numbers above were NOT rewritten; this addendum
supersedes only the N-2/N-3 "not fixed" lines in "Notes carried
forward". N-1 and N-4 remain carried forward to Phase 36, unchanged.

### What changed (3 files, no production code)

1. **N-2, test-only** — `backend/tests/unit/test_phase35_w4_db_scrub_migration.py`:
   the local no-teardown `_install_fpdf_stub_if_absent()` is gone.
   The file now **imports** `_fpdf_stub_if_absent` from the Phase 33
   DB-boot suite (`test_phase33_db_boot_fail_fast`, bare-name import —
   the established pattern in this suite, cf.
   `test_team_status_hp_bands` importing from
   `test_phase29_w1_pov_reader`), so the stub gets the exact Phase 33
   teardown (sys.modules restored, stub-bound `app.main` /
   `app.dossiers.*` evicted). `_load_main_module()` skips ONLY on
   `ImportError`; any other import failure propagates and fails
   loudly. No assert was removed and no test weakened (all Section B
   test bodies untouched; 72 asserts in the file).
2. **N-3, doc-only** — `docs/STREAMING.md` §3 + §7: the false
   "used by the highlight renderer and the deprecated StreamService"
   claim is removed in both places. Verified by grep before editing:
   the sole production caller of `detect_encoder` is
   `backend/app/services/replay/renderer.py:398`;
   `backend/app/streaming/service.py` never imports `encoder.py` and
   hardcodes `libx264`.
3. **Stale line refs** — the two ledger-named refs live in
   `docs/SPECTATOR_COVERAGE.md`, not in `STREAMING.md` (which carries
   no line refs at all): `killfeed.py:884` → `killfeed.py:986` (Phase 32
   comment block) and `main.py:1522-1528` → `main.py:1579-1597`
   (grenade-trajectory lifespan block), both re-verified in code.
   Other refs in that doc were not re-verified (out of scope).

### Re-run numbers (this follow-up; `python3 -m pytest`, `-o addopts=''`)

- Targeted (W4 + Phase 33 DB-boot + W1/W2/W3/W5 + leak probe, one
  process): 125 passed, 1 probe failure — investigated, see below.
- Isolation: W4 + probe 18 passed; Phase 33 + probe 15 passed;
  reverse order (W4 last) 109 passed including the probe.
- Narrow-skip probe (standalone script, /tmp only): a sabotaged
  non-`ImportError` failure importing `app.main` propagates as
  `RuntimeError` (never a skip), and the stub is torn down on both
  the failure and success paths.
- Full suite: **2328 passed / 62 failed / 3 skipped** — identical to
  QA's pre-follow-up numbers; all 62 failures in the pre-existing/env
  areas named above. Touched-area batch (Phase 35 suites + Phase 33
  DB-boot + Phase 34 + Phase 23 + Phase 29 POV + Phase 26 lifespan):
  **350 passed / 1 skipped / 0 failed**.

### Observed, NOT fixed (out of this follow-up's scope)

The combined-run probe failure is a **pre-existing leak in the W2
file** (`test_phase35_w2_auth_controls.py:52-82`): it installs its
own fpdf stub at import time via `sys.modules.setdefault`, with no
teardown — the import-time stub behind the dossier-PDF
order-dependence noted in §9. It was left untouched: N-2's scope was
the W4 file only, and no other file was to be changed. Recommended
follow-up (Phase 36 / N-1 batch): apply the same Phase 33
context-manager reuse to W2.

### Follow-up hygiene

No commit/push by this follow-up; `git stash` never run; H-3 /
`.env` / history untouched; N-1/N-4 untouched; no file outside the
3 named above was modified. Probe scripts live under `/tmp`, not in
the tree. File count for this phase is unchanged (no new files).
