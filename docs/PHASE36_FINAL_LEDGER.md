# Phase 36 Final Ledger — N-1 + N-4 + W2 stub + Low 10 + R-1…R-7 + feature sweep

**Base:** local HEAD `cdf4046` (Phase 35, pushed to remote `9382d015`).
**Status:** code complete, independent QA **FAIL → fix → RE-GATE PASS** (see §9).
No commit/push by the coordinator, workers, or QA — work remains in the working
tree for parent review; commit + push only while the user is present in chat,
per the standing rule. `git stash` was never run by anyone in this phase.

**User's bar for this phase (2026-11-01, direct order):** keep writing code and
testing, NO mistakes, NO feature missed, EVERY feature must work at its peak.
That bar is the reason the QA FAIL below gated completion instead of being
logged as a note.

## QA verdict chain (independent, read-only, all numbers re-derived)

- **Round 1 QA: FAIL (narrow, security-gated).** Everything functional passed
  and every worker number re-derived — except two **new credential leaks in
  W2's new revocation code** (probes P6/P7): `deps.py:239` (seed) and
  `deps.py:358` (revoke-write failure) logged raw DB exceptions with
  `exc_info=True`; fake password + full URL appeared verbatim in the
  formatted log record. Same defect class this phase exists to eliminate.
- **Fix-up:** both sites + `main.py:1448` scrubbed via W1's pattern; 2 `caplog`
  tests added to the W2 suite (16 → 18); 2 miscounted inventory rows
  corrected to QA-verified HEAD values.
- **Re-gate QA: RE-GATE PASS.** Own probes on all paths (seed, revoke,
  lifespan, IntegrityError-branch claim, caplog non-vacuity, inventory rows,
  scope check, adjacent suites) — all pass. No residual blocking defect.

## Numbers (authoritative)

- **Full backend suite (round-1 QA, `-o addopts=''`, 1018s): collect 2585,
  0 collection errors; 2521 passed / 62 failed / 3 skipped** (outcomes 2586 =
  collected + 1 — the +1 is the fakeredis module skip, SOLVED by L-9, §7).
  The 62 failures are exactly the Phase 35 inventory (e2e 13, OBS drift 19,
  dossier-PDF/fpdf 5, event-bus/Redis 1, commentary 7, observer 6, team 3,
  coordinate-mapper 3, minimap 2, player-tracker 2, fight-agent 1).
  **Zero failures in any Phase 31–36 file. Zero regressions.**
- **New Phase 36 suites (QA re-ran):** W1 scrub 20/20; W2 revocation 16/16
  (round 1) → **18/18 after fix-up** (re-gate QA re-ran); W3 pollution 4/4;
  W4 residuals 114/114; W5 low backlog 39/39. **Total new: 195 tests**
  (193 at QA's full run + 2 caplog added by the fix-up).
- **Collect honesty:** QA's full run (2585) predates the fix-up's 2 added
  tests. Expected final collect is **2587 — NOT re-run in full** after the
  fix-up (re-gate scope was the fix + adjacent suites only). State 2587 as
  expected, not measured, until the parent's post-commit full run.
- **Frontend (round-1 QA):** `npx tsc --noEmit` exit 0; `npm run build`
  exit 0 (34 routes prerendered).
- **pytest-cov: NOT installed** — the `--cov-fail-under=70` gate is NOT
  verifiable here, as in all prior phases; CI env must verify it.
- **QA adversarial probes (round 1): 60/62 pass** (only P6/P7 fail — the
  gate). Re-gate probes: all pass (see §9).

## §1 N-1 scrub adoption — PARTIAL (structural guarantee not fully achieved)

W1 applied the scrubber at every named site: all 10 raw `{e}` logging sites
in `backend/app/db/base.py` (incl. the `exc_info` traceback vector — a new
`_scrubbed_exc_info()` surrogate, since `exc_info=True` prints the ORIGINAL
message as the traceback's final line) plus the 2 raw `str(e)` payload
returns; `get_db_with_retry` now re-raises via `scrubbed_exception`;
`backend/scripts/init_db.py` (4 sites); `main.py` login helpers
`_authenticate_db_user` / `_is_deactivated_db_user`; the false module comment
now states the exact scrubbed scope. New suite: 20 tests, all pass (QA
re-ran). QA probes P1–P3 confirm surrogate tracebacks clean, benign detail
survives, retry re-raise clean with no `__cause__`/`__context__`.

**Residual (disclosed by W1, confirmed by QA P4):** `get_db`,
`get_db_context`, `init_db` still **bare-raise the raw exception**
(type-preserving; only their log lines are proven scrubbed). The lifespan boot
gate re-scrubs on raise, but any other caller sees the raw exception. Also:
`get_db_with_retry` now raises plain `Exception`, not `OperationalError` —
zero production callers (grep-verified), so no live blast radius, but a
latent type change. Pre-existing same-class sites `deps.py:505/741`
(`is_active` checks) are NOT fixed here — backlog (§10).

## §2 N-4 operator revocation path — FIXED (after re-gate)

W2 built a real operator path with persistence that survives restart:

- **Model + migration:** `RevokedApiKey` → table `revoked_api_key`
  (`key_digest` SHA-256 hex, unique + indexed; `revoked_at`, `revoked_by`,
  `reason`; digest-only, raw key never stored). Migration **018**
  (`018_revoked_api_keys`, down `017_match_stats_tables`, inspector-guarded,
  portable DDL, 017 pattern). `alembic heads` = single head `018`.
  Scratch-DB proof: stamp-at-017 → `upgrade head` creates the table with
  columns/index/unique identical to `create_all`; second upgrade no-op;
  downgrade to 017 drops it; re-upgrade recreates. Not proven on Postgres
  (none here); fresh sqlite `upgrade head` still fails at pre-existing 009.
- **API:** `revoke_api_key_persistent()` (DB row + cache; returns
  `{revoked, was_configured, persisted, already_revoked}`, never raises on DB
  failure), `seed_revoked_api_key_cache()` (startup re-seed, union-only, DB
  error → logs + returns 0), `load_revoked_api_key_digests()`,
  `is_api_key_revoked_in_db()`. Sync `revoke_api_key()` kept unchanged as the
  in-memory-only primitive (Phase 35 test still pins that semantic).
  `_verify_api_key` stays the sync choke point for REST + WS.
- **Operator paths:** REST `POST /api/v1/admin/api-keys/revoke`
  (`require_admin`; missing key → 400, unauth → 401); WS `revoke_key` now
  really revokes (acks `success` with the result fields; no key → `error`;
  never a fabricated key, never echoes the raw key). Lifespan seeds the cache
  right after `_init_database_or_fail_fast()`, failure-isolated.
- **Runbook:** new `docs/API_KEY_REVOCATION_RUNBOOK.md` (REST + WS
  procedures, field semantics, no-un-revoke policy, honest rule: removing the
  key from `ADMIN_API_KEYS` is still recommended so a DB reset cannot
  resurrect it). `SECURITY.md` untouched.
- **L-4 (in this item):** `MATCH_STATE["director_state"]` literal now
  carries `"source": "placeholder"` with an honest comment; WS connect sends
  the C6 `_demo_frame` (source `demo`) when DEMO is on, the marked literal
  otherwise. `lock_pov` real decisions still replace the whole dict.

**Proof:** new suite 18/18 (16 + 2 caplog, re-gate re-ran). QA P8: revoke →
1 DB row, digest-only (sha256 verified, raw key absent); cache clear +
re-seed (simulated restart) → still revoked; `_verify_api_key` rejects.
**Honest limits (proven, not hidden):** the verify path is sync, so it reads
the cache, not the DB, per request — a key revoked by **another
process/instance after this one started** is invisible until re-seed/restart
(single-process deployments unaffected). If the DB is unreachable at startup,
the seed loads nothing (logged warning); pre-restart revocations are
unenforced until the DB is back and the process restarts. The lifespan seed
call itself is verified by inspection only (full lifespan not executed in
tests); the seed function it calls is fully tested.

## §3 W2 stub leak + test pollution — FIXED (conftest isolation deferred)

W3 replaced the W2 file's import-time `sys.modules.setdefault` fpdf stub
(old lines 52–82) with exact reuse of the Phase 33 context manager
(`_fpdf_stub_if_absent` from `test_phase33_db_boot_fail_fast`), skip
ImportError-only, plus a `_load_main_module()` helper; unused `sys`/`types`/
`Enum` imports removed. New proof suite (4 tests): teardown on success AND
failure paths (stub torn down even when a sabotaged non-`ImportError`
propagates — never a skip), bare re-import fails honestly, both collection
orders identical. W2 suite still 17/17; Phase 33 DB-boot 14/14; W5 pollution
6/6; combined 6-file batch 80/4 forward and reversed — identical.

**Production ordering:** `fight_detector.py` `list(set)` → `sorted(...)` at
the three ledger sites (:1349, :1426, :1451) plus :1487 (`get_team_fights`
opponents, same class). No `list(set)` output sites remain in the file.
Deterministic across `PYTHONHASHSEED` 0/1/42 (byte-identical payloads).

**Bisect:** NO polluter found for `test_get_fight_detail` — it passes
standalone across seeds and in both orders; its only failure is
`test_determine_third_party_fight`, which fails **deterministically** in
every order and seed (returns `LONG_RANGE`, expects `THIRD_PARTY`) —
pre-existing production drift, not pollution. The Phase 35 test-side fix
holds; there is no polluting test/file to name.

**Conftest per-test DB isolation — DEFERRED, not half-built** (honest):
module-global `engine`/`AsyncSessionLocal` bound by reference at many import
sites, `TestClient`-against-real-app files, `./test.db`-dependent files — a
rebind mechanism chasing every consumer + per-test create_all/dispose + a
full-suite regression pass. QA note: W3's counts were loose-grep numbers
(correct tight count: 14 import statements / 8 test files, not 47 sites /
15 files) — rationale stands, numbers corrected here.

**Caveats:** dossier-PDF tests still cannot pass here (fpdf2 absent); the fix
makes their outcome order-independent (collection error both ways), not
passing. Full-suite order-independence is NOT claimed: at least 7 other files
still install import-time fpdf stubs (out of scope, untouched). Observed, not
fixed: `fight_detector.py:1089` (arbitrary team for fight-ID suffix) and
`:1115` (primary attacker/defender from `list(set)` order) — nondeterministic
assignment, not output ordering; changing it is a semantic decision (backlog).

## §4 R-1…R-7 + R-8 residuals — FIXED (all 8)

W4, new suite 114/114; adjacent 331 passed (P34 160, D-2 29, replay 13+4+23
+15, P23 23+29, P22 35), zero failures. QA re-probed every residual.

- **R-6 (Medium, LIVE) — FIXED.** `_sniper_triple`
  (`highlight_triggers.py:101–132`): no distance producer exists anywhere on
  the replay path (grep-verified: kill-feed payloads carry weapon/killer/
  victim only; zero `distance` references in `services/replay/`). Reason now
  `"3 sniper-class eliminations in {span:.1f}s"` — only the evidenced weapon
  class. Docstring: if a measured distance ever lands on this path, range may
  be claimed only for kills carrying it, never from class alone. QA P9:
  reason has no `long-range`.
- **R-7 (Medium-Low, LIVE) — FIXED.** `queue.py:171` (`_candidate_score`),
  `:721` (drop gate), `:734`. Semantics chosen + documented: **drop the
  candidate** (no defer/lowest-priority mechanism exists; drop matches the
  existing underspecified-drop pattern). Missing/zero/`"0"`/negative/
  non-numeric/NaN/inf/bool → `None` → dropped with
  `replay_candidate_dropped_no_score`, never 80. Positive finite numbers
  (incl. numeric strings) queue at their own value. Live publisher always
  sends `int(intensity)` (`live_director/service.py:2295`), so a drop means a
  broken/foreign publisher, not a real fight. QA P10 confirms. Residual nit:
  score 0.5 parses then truncates to 0 via pre-existing `int()` — same as
  before for valid scores, not a new lie (backlog).
- **R-1 (Medium) — FIXED.** `_create_comeback_clip`: `from_position` via
  `_evidence_positive_count(..., minimum=2)` (floor 2: #1 is already top, so
  it evidences no comeback). Absent/None/junk → position-less `"THE
  COMEBACK! {team}!"`, excitement base 60 (was 84 from default 8). Evidenced
  8 still scores exactly 84. QA: unevidenced title has no `#`, excitement 60.
- **R-2 (Medium) — FIXED.** `_create_final_circle_clip`: `teams_alive` via
  `_evidence_positive_count(..., minimum=1)`; the ≤2 bonus (+20) applies only
  when evidenced. Absent/None/junk → 70/GOOD (was 90/LEGENDARY from default
  2). Explicit `None` no longer raises `TypeError` on direct call (same for
  comeback `from_position=None`).
- **R-3 (Low) — FIXED.** `_EVIDENCED_WEAPON_VALUES` allowlist
  (`clip_detector.py:504`, gate `:587`): icon-matcher class ids (`ar, smg,
  sniper, dmr, pistol, "frag grenade"`) + replay-trigger spellings (`sr,
  kar98k, m24, awm, mini14, sks, mk14, grenade, frag`). **Every
  `FAMILY_TO_CLASS` key is rejected**, including `frag_grenade` in its
  underscore spelling (the class display spelling `"Frag Grenade"` is
  accepted). Unlisted strings (`M416`, `AKM`, `shotgun`) render gun-less. QA
  accepts the exclusion as deliberate and consistent with the H-1 contract
  (family ids are never evidence; the display spelling renders). Judgment
  call, recorded: the real bus weapon for a frag kill *is* `"frag_grenade"`
  (underscore) per `gated_weapon` — if one ever reaches
  `_create_long_range_clip` it now renders gun-less. Harmless (frag isn't a
  long-range weapon; grenade clips don't use `_evidence_weapon`), but a future
  gun-model producer needs an explicit allowlist addition.
- **R-4 (Low-Medium) — FIXED.** `_create_funny_clip`: caller
  `title`/`description` ignored entirely; fixed generic text. No funny-moment
  producer exists and no label vocabulary exists to gate against — documented
  that a future producer should pass an allowlisted `moment_type`, not free
  text. QA: injected caller text ignored.
- **R-5 (Low) — FIXED.** `check_weapon_highlight`: `"(kill-feed icon
  verified)"` → `"(kill-feed icon match)"` (provenance, not a verification
  claim this function performs).
- **R-8 (pre-existing multi-kill TypeError) — FIXED.**
  `kill_count`/`time_span` now go through the shared evidence helpers
  (minimum=1, existing `< 3` check preserved with its log message). String
  `"3"`/`"4.5"` parse; junk/None/bool/fractional skip cleanly.

## §5 Low 10 — FIXED (2 already present, verified; 1 accepted debt)

W5, new suite 39/39; adjacent 221 passed incl. the 39. Per-item:

- **L-1 — FIXED (ledger correction, not wiring).** Live path never receives
  a requested duration: `decide_zone_beat()` always returns fixed
  `MAP_BEAT_DURATION_S = 15.0`, so wiring `clamp_map_beat_duration()` would
  be fake. The live 30s cap is `DirectorService.enforce_scene_holds()`
  (`MAP_CAM_MAX_HOLD_SECONDS = 30.0`). Dated correction addendum §7 added to
  `docs/PHASE31_FINAL_LEDGER.md` (old text intact); overstated docstring in
  `zone_beat.py:13-21` corrected.
- **L-2 — FIXED (retraction implemented).** New
  `DirectorService.retract_suggestions(created_by, reason)`
  (`director_service.py:1048-1096`) — exact-`created_by` match, marks pending
  entries `"retracted"` (+`decided_at`/`retracted_reason`), broadcasts on
  `director.suggestion` like a rejection, keeps them in capped history.
  Deliberately NOT a learning fate (`SUGGESTION_FATES` has no "retracted"; a
  system supersede is not a human decision). Lull proposals now use
  `created_by="live_director:lull_loop"`; the abort branch
  (`live_director/service.py:1670-1700`) retracts them. `lull.py` HOOK NOTE
  updated; `docs/DIRECTOR.md` status list + lifecycle updated with
  `"retracted"`. QA P12: retract is creator-scoped (zone suggestion
  untouched), empty reason → `"superseded"`, second retract empty,
  approve-after-retract raises.
- **L-3 — FIXED (briefed scope) + tree-wide follow-up noted.** `FADE`,
  `SWIPE`, **and `CROSSFADE`** removed from `TransitionType` enum and
  `transition_rules` in `cinematic_controller.py`; presets now `CUT`;
  `BANNED_TRANSITION_NAMES/VALUES` + `BannedTransitionError`, raised by
  `apply_transition`/`apply_preset` (Phase 31 `TransitionController`
  pattern). "Never wire without calibration" guards added to
  `winner_predictor.py`, `survival_predictor.py`, `fight_predictor.py`,
  `cheating_detector.py` (citing fabricated +25/+20/+30/+25/+10 constants).
  `zone_predictor.py` already guarded — untouched. **QA finding:** fades
  survive elsewhere pre-existing (`replay_director.py` intro/outro `"fade"`,
  `montage_generator.py` fade/dissolve/wipe pool, `documentary_generator.py`
  default `"fade"`) — outside the briefed scope (cinematic_controller only);
  needs a follow-up decision if the Bible ban is meant tree-wide (backlog).
- **L-5 — ALREADY FIXED in Phase 35, verified.** Float-truth docstring is
  already in `gated_weapon` (added in `cdf4046`); W5 verified the arithmetic
  in Python and pinned it with tests. No docstring edit needed. QA P14
  confirms (`0.70−0.55` abstains, `0.80−0.65` confident).
- **L-6 — ALREADY PRESENT since Phases 26–29, verified.** The literal
  message exists in `frontend/app/broadcast/director/components/ClonePanel.tsx:164`
  ("Abhi style seekha nahi gaya — pehle kam se kam 10 faislo par director
  feedback chahiye."), shown on `coldStart` (`!profile.exists`), fed by
  `GET /director-learning/status` → `ProfileInfo.exists: bool`. No new
  surface invented; verified + tested. QA confirms the message matches the
  `DIRECTOR.md` quote exactly.
- **L-7 — FIXED (a)/(b), ACCEPTED DEBT (c).** (a) `backend/.coverage`
  (112 KB, tracked) untracked via `git rm --cached` (staged deletion; file
  stays on disk, now ignored) + `.coverage` added to `.gitignore`. (b)
  0-byte `.github/workflows/CODEOWNERS` relocated via `git mv` to
  `.github/CODEOWNERS`. (c) One-liners NOT deleted — honestly re-counted:
  **82 exactly-1-line files** (QA re-counted: exactly 82; 7 py, all
  `__init__.py` package markers — legitimate), breakdown .md 26 / .ts 15 /
  .json 10 / .py 7 / .sh 7 / .yml 4 / .conf 3 / .tf 3 / .yaml 2 / .txt 2 /
  .ini 2 / .ipynb 1. Recorded as accepted debt; census test asserts 74–90
  with the exact `.py` rule. **Push note (§11):** the CODEOWNERS rename and
  `.coverage` deletion are staged index changes; `push-files` cannot delete,
  so the parent must delete the old remote paths via `delete_file` (or leave
  the 0-byte file on remote) — do not silently drop them from the manifest.
- **L-8 — FIXED (tightened, no flip).** `backend/app/ml/trained_detector.py`
  (actual location, not `backend/app/vision/`): `_decode_rfdetr` now uses
  EXACT name match (`"dets"`/`"labels"`, case-insensitive) instead of
  substring (`"det" in n`); shape fallback replaced by
  `_match_rfdetr_pair_by_shape()` — fires only when exactly one (dets
  (B,Q,4), labels (B,Q,C′+1), same B/Q) pair is jointly consistent,
  otherwise raises instead of guessing (3-class ambiguity now raises).
  Registry untouched (`killfeed` stays `yolov8`, default input 640); no
  `best.onnx` in repo (verified — `*.onnx` search empty); flip checklist
  verified present in `docs/PHASE33_FINAL_LEDGER.md` §4 (`input_size=
  (384,384)` override). QA P13: name-less 2-class pair decodes; 3-class
  ambiguity raises `ValueError`; substring names (`"detections"`) don't match.
- **L-9 — SOLVED.** (a) `pytest-cov` is **not installed** in this env
  (`ModuleNotFoundError`; `pytest.ini` still requests `--cov-fail-under=70`,
  so the gate is unverifiable here — CI env must verify it). (b) The +1
  outcome discrepancy is **SOLVED, not unexplained**: full run 2325 passed +
  65 failed + 3 skipped = 2393 outcomes vs 2392 collected (W5's mid-flight
  run). JUnitXML diff proves the extra outcome is the module-level collection
  skip of `tests/integration/test_phase26_lifespan_monitor.py:35`
  (`pytest.importorskip("fakeredis")` — fakeredis absent here): 0 items to
  `--collect-only`, 1 "skipped" outcome to the run. Stripped node-id
  comparison confirms 2392 == 2392 otherwise. Pinned with a regression test.
  QA independently verified the same +1 on its own run (2586 outcomes vs
  2585 collected; the +1 is the fakeredis module skip in the junitxml
  skipped list).
- **L-10 — FIXED (verified counts only), with 2 rows corrected by QA.**
  W5 re-measured at HEAD `cdf4046` and corrected `docs/PROJECT_INVENTORY.md`
  (backend .py 459→461, LOC ~126,322→~128,134, graphics 16 registered + 9
  animated, feed types 15→17, docs 44→47 tracked, lib ×14→×18, tests
  2062→2585 collected) and surgically corrected five stale docs
  (`KAGGLE_TRAINING.md`, `TRAINING.md`, `ROLES.md`, `DIRECTOR.md`,
  `GRAPHICS.md`). **QA found 2 rows wrong:** py files claimed 461 "at HEAD
  cdf4046" — true HEAD is **460** (461 is the dirty-tree count); LOC claimed
  ~128,134 matches neither HEAD (**127,757**, via `git archive`) nor the final
  tree (128,595) — a mid-flight snapshot presented as a HEAD recount. The
  fix-up corrected both rows to the QA-verified HEAD values (460 / 127,757,
  dated notes; fix-up worker verified both independently before writing;
  re-gate QA verified exactly). Other recounts verified by QA: tests 2585 ✓,
  docs 47 tracked + 1 untracked runbook ✓, feed types 17 ✓, graphics 16
  registered + 9 animated + 35 files ✓, lib 18 ✓, notebooks 5 ✓, roles 6 ✓.
  Inventory test count is dated and flagged for re-measurement after the
  Phase 36 commit.

## §6 Feature-completeness sweep (W6, read-only audit)

Report: `~/workspace/drafts/aegis-phase36-feature-sweep.md` (outside the repo
tree). Method: HEAD `cdf4046` via `git archive HEAD` snapshot in /tmp; all
probes ran there; nothing written to the repo. All 33 Phase 31–35 ledger items
probed with file:line evidence: 20 phase test files **547 passed / 6 skipped /
0 failed**, rewritten P32-5 files **38/38**, own direct probes **33/35**, plus
`alembic heads` and a live import of the rate-limit rule table.

**Verdict counts: CONFIRMED 19 · ADJUSTED 14 · GAP 0 · UNPROVEN 0.**
Phase 31: 4/4; Phase 32: 5/2; Phase 33: 3/2; Phase 34: 2/0; Phase 35: 5/6.
No ledger claim was found false. Every ADJUSTED grade is a limitation the
ledger itself already disclosed (live-proof gap, admitted partial, or
uncalibrated heuristic). Top live defects confirmed at HEAD were R-1, R-2,
R-6, R-7 — all fixed in this phase (§4); QA spot-verified the sweep's
methodology and accepts 19/14/0 as reported (not fully re-derived).

**Process finding (honest):** the shared tree was being mutated by sibling
workers mid-audit; W6's early live-tree greps were contaminated and everything
was re-verified against the HEAD snapshot. Sibling work is NOT credited in the
sweep report — the fixes were re-verified against the final tree by round-1
QA instead.

## §7 N-1/N-4 carry-over status (Phase 35 partials — now closed)

- **N-1: PARTIAL → PARTIAL (improved, residual open).** Adoption is now
  complete at every named site (§1), but the structural guarantee is still
  not achieved: `get_db`/`get_db_context`/`init_db` bare-raise raw. The
  residual is a Phase 37 item, not hidden.
- **N-4: PARTIAL → FIXED.** Operator path exists, persists across restart,
  and is proven (§2). Cross-process invisibility pre-seed is the disclosed
  limit.
- **N-2/N-3 (Phase 35 fold-in):** unchanged, still fixed.
- **W2 stub (Phase 35 addendum observation):** FIXED (§3).

## §8 Margin-getter note (Phase 35 minor, folded in here)

The property getter returning a directly-written `_margin_threshold`
verbatim (clamp on read) was prescribed in Phase 35 §1. It was NOT in this
phase's brief and was NOT fixed here — recorded, not hidden. Clamp-on-read
remains a small follow-up; the label stays safe because `gated_weapon`
re-clamps.

## §9 QA FAIL → fix → RE-GATE PASS (full record)

**Round-1 FAIL (narrow, security-gated).** QA probes P6/P7: W2's new
`seed_revoked_api_key_cache` (`deps.py:239`) and `revoke_api_key_persistent`
generic-`Exception` branch (`deps.py:358`) logged raw DB exceptions with
`exc_info=True`; fake password + full URL appeared verbatim in the formatted
log record. Both fire exactly when operators read logs (every startup while
the DB is down; every revoke-write failure). W2's suite had zero `caplog`
assertions, so its own gate couldn't catch it. QA also logged as backlog
(not gating): pre-existing `deps.py:505/741` same-class sites.

**Fix-up (4 files only).** `deps.py`: imports `_scrubbed_exc_info` from
`..db.base` and `scrub_db_error_text` from `..db.error_scrub` (W1's existing
helpers reused, no new scrubber, no import cycle); both sites now log the
scrubbed surrogate with scrubbed exc_info. `main.py:1448` lifespan seed
wrapper scrubbed (already imported there). W2 suite: 2 new `caplog` tests
(`test_seed_failure_log_never_carries_db_credential`,
`test_revoke_write_failure_log_never_carries_db_credential`) asserting fake
password/URL/username appear NOWHERE in `caplog.text` (message + traceback)
and `<redacted>` is present. Inventory: 2 rows corrected (§5 L-10).
**Deviation adjudicated:** the `IntegrityError` branch has NO logger call at
all (verified) — nothing to scrub, and the worker correctly invented no new
log line (that would be new observable behaviour on every routine
re-revoke, not a fix).

**Re-gate QA: PASS.** Own probes (fake creds only): seed path — all three
fake strings absent, `<redacted>` present, returns 0; revoke path — absent,
behaviour preserved (`persisted: False`, in-memory revoked); control — the
pre-fix shape leaked all three strings, so the probes are sensitive.
Lifespan wrapper run for real (stubbed init + exploding seed): exactly one
seed line, scrubbed, no `exc_info` at all (safe; marginally less traceback
debuggability — not a defect). IntegrityError deviation ACCEPTED (claim
verified true). Caplog tests proven non-vacuous (bypassed scrubber → both
fail). Inventory rows verified exactly at cdf4046. Scope: only the 4 claimed
files modified in the fix-up window (plus ignored runtime logs); stash empty;
`test.db` absent. Adjacent suites 63 passed; W2 suite 18/18. **Caveat (not a
FAIL):** no first-QA baseline commit exists, so "only these 4 files changed
since first QA" is corroborated by markers + mtimes, not proven from git;
the staged CODEOWNERS rename + `.coverage` deletion predate the fix-up and
belong to Phase 36 L-7 (§5) — commit them together (§11).

## §10 Honest limits + backlog (not hidden)

- No real phone feed, scrim, RTMP push, 720p re-measurement, real Chromium
  capture, live-browser click-through, real Postgres, or real `best.onnx` —
  all on this VM, as before. Revocation persistence proven on sqlite only; no
  live multi-process deployment; no real operator run.
- Full suite + frontend numbers are round-1 QA's on the pre-fix-up tree;
  the fix-up added 2 tests (expected collect 2587, not re-run in full).
- **Backlog for Phase 37 / follow-ups:** N-1 bare-raise residual
  (`get_db`/`get_db_context`/`init_db`) + pre-existing `deps.py:505/741`
  exc_info sites; margin-getter clamp-on-read (§8); conftest per-test DB
  isolation (deferred with reason, §3); L-3 tree-wide fade decision
  (`replay_director.py`, `montage_generator.py`, `documentary_generator.py`);
  R-7 0.5→0 truncation nit; `fight_detector.py:1089/:1115`
  nondeterministic assignment (semantic decision); `test_determine_third_party_fight`
  deterministic drift (`LONG_RANGE` vs `THIRD_PARTY`); the 62 pre-existing/env
  failures (unchanged); 7 other import-time fpdf stubs (order-dependence
  risk, out of scope); inventory re-measurement after the Phase 36 commit;
  82 one-liner files (accepted debt, §5 L-7).
- **Explicitly NOT in this phase:** H-3 (user-only), PNG/workflows, RF-DETR
  flip (registry untouched, zero `*.onnx`), outreach (paused), scrim.

## §11 Files in this phase (40 — verify before pushing)

Modified, unstaged (28): `backend/app/agents/fight_agents/fight_detector.py`,
`backend/app/agents/highlight_agents/clip_detector.py`,
`backend/app/agents/observer_agents/cinematic_controller.py`,
`backend/app/agents/prediction_agents/fight_predictor.py`,
`backend/app/agents/prediction_agents/survival_predictor.py`,
`backend/app/agents/prediction_agents/winner_predictor.py`,
`backend/app/agents/referee_agents/cheating_detector.py`,
`backend/app/api/deps.py`, `backend/app/db/base.py`,
`backend/app/db/models/__init__.py`, `backend/app/main.py`,
`backend/app/ml/trained_detector.py`,
`backend/app/services/director_service.py`,
`backend/app/services/live_director/lull.py`,
`backend/app/services/live_director/service.py`,
`backend/app/services/live_director/zone_beat.py`,
`backend/app/services/replay/highlight_triggers.py`,
`backend/app/services/replay/queue.py`, `backend/scripts/init_db.py`,
`backend/tests/unit/test_phase35_w2_auth_controls.py`,
`backend/tests/unit/test_phase35_w4_db_scrub_migration.py`,
`docs/DIRECTOR.md`, `docs/GRAPHICS.md`, `docs/KAGGLE_TRAINING.md`,
`docs/PHASE31_FINAL_LEDGER.md`, `docs/PROJECT_INVENTORY.md`, `docs/ROLES.md`,
`docs/TRAINING.md`.
Staged (3): `.gitignore` (M), `.github/workflows/CODEOWNERS` →
`.github/CODEOWNERS` (R), `backend/.coverage` (D).
New, untracked (8): `backend/alembic/versions/018_revoked_api_keys.py`,
`backend/app/db/models/revoked_api_key.py`,
`backend/tests/unit/test_phase36_w1_scrub_adoption.py`,
`backend/tests/unit/test_phase36_w2_revocation.py`,
`backend/tests/unit/test_phase36_w3_pollution.py`,
`backend/tests/unit/test_phase36_w4_residuals.py`,
`backend/tests/unit/test_phase36_w5_low_backlog.py`,
`docs/API_KEY_REVOCATION_RUNBOOK.md`.
This ledger: `docs/PHASE36_FINAL_LEDGER.md` (new).
No deletions beyond the staged `.coverage` + CODEOWNERS move; no binaries; no
workflows added. `backend/test.db` absent; stash empty throughout (never
used). **Push note:** `push-files` cannot delete — the old remote paths
`.github/workflows/CODEOWNERS` and `backend/.coverage` need `delete_file`
(one approval-gated call each) or must be left on remote; the new
`.github/CODEOWNERS` pushes normally. Do not silently drop them from the
manifest.

## §12 Process notes (honest)

- 6 specialist workers + 1 fix-up worker + 2 QA rounds (round-1 + re-gate),
  all on the shared tree. File-level separation held except `main.py`
  (W1 login helpers, W2 WS/revoke + lifespan seed, fix-up :1448) — no
  clobbered hunks reported; every worker verified its diffs.
- Two workers independently hit the same migration-018 test-currency failure
  (`TestMigration017` asserting head 017 while 018 existed); the coordinator
  fixed it as integration (version assertions 017→018 + explanatory comment;
  file re-ran 17/17).
- No worker invented numbers: round-1 QA re-derived every claimed count
  exactly, except the two inventory rows (§5 L-10), which were miscounts
  (mid-flight snapshot presented as HEAD recount) — corrected by the fix-up
  and verified by re-gate QA. W3's conftest counts were loose-grep numbers —
  corrected here (§3).
- W6's sweep was audited against a HEAD snapshot while siblings mutated the
  tree; its contamination disclosure is honest and its verdicts were
  spot-verified, not fully re-derived.

## Addendum — post-commit inventory re-measurement (parent fix-up, 2026-11-01)

After the Phase 36 commit (9bcc587) the parent's full-suite run
(2522 passed / 63 failed / 3 skipped, collect 2587 — the ledger's expected
number, now measured) had exactly ONE failure outside the 62 pre-existing
inventory: the W5 test `test_inventory_counts_match_remeasurement`, asserting
`'49 top-level tracked'` against an inventory that still said 47. The two
new tracked docs (this ledger + `API_KEY_REVOCATION_RUNBOOK.md`) made the
count stale the moment the commit landed — the ledger §5/§10 "re-measure
after the commit" note, executed now, before the push.

Re-measured at 9bcc587 with the ledger's own methods: backend/app py files
461 (+1 `revoked_api_key.py`; `error_scrub.py` already existed at cdf4046),
backend/app LOC 128,611, tracked docs 49 (on-disk also 49), tests collected
2587, migrations 15 files (001, 005–018), tables 34 (30 explicit +
User/ApiKey/AuditLog/RevokedApiKey auto-named). `PROJECT_INVENTORY.md`
updated to these values. No production code touched by this fix-up. The W5
file re-runs green afterwards; the full suite is not re-run in full for a
docs-only change (pre-fix full run stands: 62 pre-existing failures + this
one, now fixed).
