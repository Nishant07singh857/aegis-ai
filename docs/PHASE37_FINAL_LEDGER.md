# Phase 37 Final Ledger — Backlog Closure (N-1, conftest isolation, fpdf stubs, cheap singles)

**Base:** local HEAD `941a13b` (Phase 36 commit `9bcc587` + fix-up `941a13b`).
**Remote:** main `c89be8b7` verified via `list_commits` before firing (Phase 36 push
`484e83d2` + delete commits `e23f927a` + `c89be8b7`). No other writer.
**Date:** 2026-11-03. **Pattern:** coordinator + 6 specialist workers + W3-fix worker +
independent QA/security reviewer (sign-off gates completion).
**Scope:** brief `~/workspace/drafts/aegis-phase37-backlog-brief.md` items 1–10, in order.
**Not in scope:** H-3 secrets/git history (user only), 84 PNGs + 4 workflows (user upload),
RF-DETR registry flip (needs real `best.onnx`), outreach (paused), scrim/live proof.

## 0. Verdict

- Full suite (coordinator, after last fix — THIRD run, post W1-fix2): **2586 passed /
  47 failed / 3 skipped, 0 errors, 1618.82 s (26:58)** — log
  `~/workspace/aegis-phase37-full-suite.log`. Failed list identical 47/47 to QA
  round 2's independent run (diffed). All 47 are the known pre-existing inventory
  (§11) — zero failures in any Phase 31–37 file, zero in db/deps files, zero NEW
  failures. (Second run: 2574 / 47 / 3 in 1684.84 s; +12 passed = the 12 new
  W1-fix2 tests, all green. First run: 2556 / 47 / 3 in 1388.05 s.)
- Frontend (re-run after the fix): **tsc 0 errors, build 35/35 routes** (measured
  2026-11-03 ~23:15 IST, `npx tsc --noEmit` exit 0; `npm run build` OK; 35 app
  pages counted in `.next/server/app`).
- QA verdict: **FAIL (round 1, §14) → fixed → FAIL (round 2, §14b) → fixed →
  PASS-WITH-NOTES (round 3b, §14c)** — D1r/D2r verified fixed by the reviewer's
  own probes (non-vacuity proven by plugin neuter); full suite arbitrated to the
  QA3 numbers (48 failed — run3 does not reproduce, see §11); new note N-5
  (post-reboot SQLite "disk I/O error" bands, 122–129 setup errors in the two
  round-3b runs, environmental, follow-up open).

## 1. N-1 bare-raise residual — FIXED (W1)

The structural guarantee is now in place: no caller of `get_db`, `get_db_context`,
`init_db`, or `get_db_with_retry` can receive an exception whose message, `str()`,
`args` / `statement` / `params` / `orig`, or `__cause__` / `__context__` chain can carry
a DB credential — while SQLAlchemy exception types are preserved.

- **New helper** `backend/app/db/base.py`: `_scrubbed_surrogate(exc)` (+ `_scrub_arg`,
  `_scrub_params_value`), the raise-site counterpart to Phase 36's `_scrubbed_exc_info`.
  DBAPIError subclasses are rebuilt from structured `.statement` / `.params` / `.orig`
  (leaf-scrubbed params, scrubbed `orig` surrogate, `hide_parameters` carried over).
  Probed on SQLAlchemy 2.1.5: `exc.args` is a *single formatted string*, NOT
  `(statement, params, orig)` — args-based rebuild would misplace values, so the
  structured attributes are the only correct source. Other `SQLAlchemyError`
  (incl. `DisconnectionError`, which in SQLAlchemy 2.x extends `SQLAlchemyError`
  directly) rebuilt as `type(exc)(*scrubbed_args)`. Any other exception / rebuild
  failure falls back to the plain scrubbed `Exception` from `error_scrub`. Guarantee
  is "never a raw credential", not "always the same type". Original exception never
  mutated. All re-raises use `from None`.
- **Raise sites** (`base.py`): `get_db` and `get_db_context` — `SQLAlchemyError` →
  scrubbed surrogate (same type); generic-`Exception` branch stays a **bare re-raise
  by design** (converting caller exceptions would turn a 404 into a 500 — proven by
  identity tests). `init_db` — `SQLAlchemyError` → same-type surrogate; non-SQLAlchemy
  → plain scrubbed `Exception` (both callers catch generic `Exception`).
- **`get_db_with_retry` type — RESTORED, not kept plain.** Evidence: repo-wide grep
  for `get_db_with_retry` in `app/` returns only the definition (`base.py:560`) —
  zero production callers, re-verified. Exhaustion re-raises `OperationalError` /
  `DisconnectionError`; non-retryable path re-raises any `SQLAlchemyError` same-type.
  Rationale (in code + guard test): Phase 36's collapse was safe only because there
  were zero callers; a future caller catching `OperationalError` would have silently
  stopped matching. Guard test `test_no_production_callers_guard` fails loudly if a
  caller ever appears.
- **`deps.py` sites:** brief cited `:505`/`:741`; verified actual pre-edit lines were
  **515 and 751** (`authenticate_checked` and `authenticate_websocket` is_active
  checks, raw `exc_info=True`). Both now bind `except Exception as e`, log a scrubbed
  message + `_scrubbed_exc_info(e)`, fail closed unchanged. No remaining raw
  `exc_info=True` in `deps.py` or `base.py` (coordinator grep-verified).
- **Tests:** new `tests/unit/test_phase37_w1_n1_scrubbed_reraise.py` (25 tests) +
  updated `tests/unit/test_phase36_w1_scrub_adoption.py` (3 expectation/docstring
  updates for the intended behaviour change). Suites: **45 passed** (new + updated);
  adjacent DB/boot/revocation suites **70 passed**; known-inventory areas
  311 passed / 14 failed (all pre-existing).
- **Files changed:** `backend/app/db/base.py` (+205 lines), `backend/app/api/deps.py`
  (2 sites), `backend/tests/unit/test_phase36_w1_scrub_adoption.py`,
  `backend/tests/unit/test_phase37_w1_n1_scrubbed_reraise.py` (new).
- **Honest limits / boundary:** (a) redaction covers credential patterns + the
  configured URL verbatim — a hypothetical driver message containing *only* the bare
  password with no key/URL context matches no pattern and is outside the scrubber's
  contract (no known driver shape produces it); verbatim-path test included.
  (b) An endpoint that itself formats a raw DB exception into its own exception is
  that endpoint's bug and cannot be scrubbed here without breaking types.
  (c) Same-class raw `exc_info=True` sites OUTSIDE this scope, flagged not fixed:
  `backend/app/core/event_bus.py` (Redis errors, 4 sites) and
  `backend/app/utils/async_utils.py:180` (generic task failures) — backlog (§13).

### QA FAIL → fix (W1-fix, 2026-11-03 ~21:55 IST)

Independent QA (see §14) FAILED Phase 37 on three defects in this section. All
three + two notes were fixed by W1-fix; the fix itself went further than QA's
prescribed sketch:

- **D1 — `__context__` leak (MEDIUM) — FIXED.** `raise X from None` inside `except`
  clears `__cause__` but still sets `__context__` to the raw exception — 11 of 12
  QA probe cases leaked. QA's sketch (compute in `except`, raise after the block)
  works for 4 of 6 raise paths but NOT the two `athrow` paths: during unwinding,
  `sys.exception()` is still the raw exception at the raise point after the block
  exits, so a plain `raise` re-attaches it. New helper `_raise_clean_surrogate()`
  (`base.py:147`): raise → catch the surrogate itself → clear `__context__` → bare
  re-raise (a bare re-raise does not touch `__context__`, proven in all four
  shapes). Applied at all 5 surrogate raise sites (grep-verified helper
  calls: `:695, :737, :792, :806, :867`); the other 2 raises in these
  functions are the deliberate bare re-raises of caller exceptions
  (`:691` get_db, `:733` get_db_context — by design, see §1 above).
  (Correction, W1-fix2: the earlier text said "7 raise sites" with
  stale line numbers — that count conflated the 5 helper calls with
  the 2 bare re-raises; see QA §14b.) The exhaustion raise (`:806`)
  was probe-verified already clean pre-fix (outside any `except`)
  and was routed through the helper anyway so all surrogate raises
  take one path. All stale "`from None` suppresses the
  context" comments corrected. `deps.py` (`:521`, `:761`): no fix needed — both
  sites only log and `return None`; the scrubbed `exc_info` surrogate is
  constructed, never raised, so its `__context__` is `None` by construction.
- **D2 — opaque bind params (LOW-MEDIUM) — FIXED.** `_scrub_params_value` now
  applies `_scrub_arg`'s str-comparison rule to non-`str` leaves: a value is kept
  only when its `str()` survives scrubbing unchanged, else replaced by the scrubbed
  string. Clean values keep exact type **and identity** (pinned by test).
  *Residual (beyond the prescribed fix, for QA to call):* an object with a clean
  `__str__` but a secret-bearing `__repr__` would still leak via SQLAlchemy's
  `repr()` rendering.
- **D3 — bare-password params (LOW) — FIXED.** New `is_credential_key()` in
  `error_scrub.py` (same alternation as `_CREDENTIAL_KV_RE`, trailing `\b`
  mirroring its separator rule — `password_hash` is NOT a key, `DB_PWD` /
  `client_secret` are). Dict values under credential keys are redacted wholesale
  to `"<redacted>"`, at any nesting depth.
- **N-2 — FIXED:** `db_session` docstring corrected (gap is closed by the W3
  autouse fixture). **N-1 — FIXED:** one line in the W3 fixture docstring records
  the 32-table scope (`Base` alone 32 tables, 34 after importing
  `app.analysis.models`; `drop_events`/`zone_phases` not created; the referencing
  test builds them as in-memory list objects only).
- **Tests — 18 new** in `test_phase37_w1_n1_scrubbed_reraise.py` (25 → 43): §H (8)
  `__context__ is None` on every re-raise path incl. the faithful FastAPI shape
  and the exhaustion path; §I (4) full chain-walk tests incl. chained-original and
  lifespan-gate end-to-end; §J (3) opaque-param incl. nested containers + identity;
  §K (3) credential-keyed params incl. the disclosed boundary (bare password under
  non-credential key `x` survives — pinned as documented boundary (a)). 2 stale
  assertions updated (intended behaviour change): they asserted
  `__suppress_context__ is True`, which encoded the old `from None` mechanism.
- **Non-vacuity:** pre-fix run — new tests **14 failed / 28 passed** (exactly the
  designed defect-detecting set); pre-fix probe — 4 of the 5 surrogate raise
  sites leaked via `__context__` (the exhaustion raise was already clean,
  being outside any `except` block), D2/D3 reproduced. (Correction, W1-fix2:
  the earlier text said "6 of 7 raise sites" — same loose "7" count as
  above: 5 helper calls + 2 bare re-raises. The "4 of 5" is derived, not
  re-probed: the exhaustion site is clean per the ledger's own adjacent
  sentence and QA §14b, and the other 4 raised `from None` inside `except`
  blocks, which by Python semantics sets `__context__` — consistent with
  QA round-1's systematic "11 of 12 cases leak" finding.) Neuter-helper
  check: with the helper reduced to a plain raise, exactly the 2
  `athrow`-shape tests fail. (Caveat: a `/tmp` wipe between calls destroyed
  the pre-fix backup copies, so no literal restore-from-scratch-copy check;
  the pre-fix run on the true pre-fix code stands as proof.)
- **Suites:** new file **43 passed**; scrub + adjacent 7-file set **134 passed**
  (1 failure found and fixed en route — the stale adoption-file assertion);
  extra DB-adjacent batch **69 passed**. `py_compile` clean on all touched files.
  Full suite not run by the worker — coordinator's re-run is §11 (second run).
- **Files changed (W1-fix):** `backend/app/db/base.py`, `backend/app/db/error_scrub.py`,
  `backend/tests/unit/test_phase37_w1_n1_scrubbed_reraise.py`,
  `backend/tests/unit/test_phase36_w1_scrub_adoption.py`,
  `backend/tests/conftest.py`.

### Re-gate FAIL → fix (W1-fix2, 2026-11-03 ~23:10 IST)

Round-2 QA (§14b) FAILED on two further holes in the same scrub code. Both fixed:

- **D1r — clean-`__str__` / secret-`__repr__` leak (LOW-MEDIUM) — FIXED.** Reproduced
  first (scratch probe, since deleted): the object was kept by identity and the
  full fake URL appeared in `str(surrogate)` and `str(surrogate.params)` on both
  channels. Fix: new shared helper `_scrub_opaque_leaf()` — a non-`str` leaf is
  kept (type + identity) ONLY when **both** `str()` and `repr()` survive scrubbing
  unchanged, otherwise replaced by the scrubbed `str()` form; a `repr()` that
  raises fails closed to the scrubbed string. Used by **both**
  `_scrub_params_value` and `_scrub_arg`. `_scrub_arg`'s docstring now describes
  the both-check rule it actually enforces (it previously claimed repr safety
  while checking `str()` only). The existing §J identity test still passes
  unmodified — clean objects keep identity, as QA predicted.
- **D2r — credential-keyed value nested ≥5 deep (LOW) — FIXED.** Reproduced first:
  depths 1–4 clean, 5+ leaked the bare password via the stringified dict. Fix: at
  the `_depth >= 4` cap, credential-keyed values are redacted **before**
  stringifying via new `_redact_credential_keys_deep()` — iterative (explicit
  stack, no recursion, so the cap's stack-exhaustion protection is not re-opened),
  cycle-safe (cyclic containers cut to `"<redacted>"`). The ledger's "redacted at
  any nesting depth" claim now actually holds: probe-verified clean at depths 5–8,
  plus a 5000-deep nesting probe (no leak, no `RecursionError`) and a cyclic-dict
  probe.
- **Ledger correction — DONE:** "7 raise sites" → **5 helper calls**
  (grep-verified) **+ 2 deliberate bare re-raises**. The "6 of 7 raise sites
  leaked" non-vacuity line was corrected to "4 of the 5 surrogate raise sites
  leaked" — flagged in the ledger itself as *derived, not re-probed* (the
  exhaustion site is clean per the adjacent sentence + QA §14b, and the other 4
  raised `from None` inside `except` blocks, which by Python semantics sets
  `__context__`). §14b was deliberately left untouched — it is QA's own record.
- **Tests — 12 new** (file 43 → **55 collected**; 49 `def test_` lines, the rest
  via parametrization): `TestOpaqueParamReprScrub` (3 — params channel,
  `_scrub_arg` channel, nested; all use a **divergent** repr, unlike the existing
  §J test that pins `__repr__ = __str__`) and `TestCredentialKeyedDeepNesting` (9
  — parametrised depths 1–4 control + depths 5–8 defect, plus a list-at-the-cap
  case). **Non-vacuity:** pre-fix `base.py` restored from a sha256-verified scratch
  copy → new tests **8 failed / 4 passed** (exactly the designed defect set);
  fixed file restored byte-identical → **12 passed**.
- **Suites:** new file **55 passed**; 7-file scrub + adjacent set **151 passed**
  (139 + 12 new, exact); extra DB-adjacent batch **163 passed**.
  `py_compile` clean. Full suite not run by the worker — coordinator's re-run is
  §11 (third run).
- **Files changed (W1-fix2):** `backend/app/db/base.py`,
  `backend/tests/unit/test_phase37_w1_n1_scrubbed_reraise.py`,
  `docs/PHASE37_FINAL_LEDGER.md` (§1 count correction only).

## 2. Margin-getter clamp-on-read — FIXED (W2)

- `backend/app/vision/detectors/icon_matcher.py`: getter (line 239–241) now returns
  `clamp_margin_threshold(getattr(self, "_margin_threshold", MARGIN_THRESHOLD))`
  instead of `float(...)` verbatim. Same bounds the setter enforces (`[0.15, 1.0]`,
  `MARGIN_THRESHOLD = 0.15`, `MARGIN_CEILING = 1.0`). Side benefit: a junk direct
  write now reports the floor instead of raising `ValueError` from `float()` —
  fail-closed. Setter, `__init__`, `gated_weapon` untouched.
- **Tests:** 2 new tests in `TestIconMatcherKnobClamp`
  (`test_direct_private_write_is_clamped_on_read`,
  `test_direct_private_write_of_junk_fails_closed_on_read`). Margin suites before:
  **84 passed**; after: **86 passed** (exactly +2, all 84 pre-existing assertions
  identical — no label change). Adjacent vision suites **70 passed**.
  Non-vacuity verified: old getter returns `-1.0` verbatim and `float("junk")`
  raises `ValueError` (new tests fail/error on old code).
- **Files changed:** `backend/app/vision/detectors/icon_matcher.py`,
  `backend/tests/unit/test_phase35_w1_margin_fallback.py`.

## 3. Conftest per-test DB isolation — IMPLEMENTED, not deferred (W3)

- **Verdict: YES.** Autouse fixture `_isolated_db_per_test` in
  `backend/tests/conftest.py` (+165 lines incl. design docstring): per-test FILE DB
  `tmp_path/isolated.db` with `NullPool` (never `:memory:` — TestClient lifespan runs
  in the portal thread's own loop), `create_all` over the 32-table metadata via
  `asyncio.run`, JSONB→JSON swap (existing idempotent pattern),
  `_register_engine_listeners` for parity, `monkeypatch` rebind of the 5 names,
  teardown `asyncio.run(dispose)` + `unlink`. No template copy (per-test `create_all`
  reads live metadata).
- **Blast-radius correction — the ledger's "tight count: 14 import statements / 8 test
  files" does NOT reproduce.** Strict grep finds **zero** test files importing
  `engine` or `AsyncSessionLocal` from `app.db.*` at module level. True picture:
  production rebind set is exactly **5 names in 3 modules** (`app.db.base.engine`,
  `app.db.base.AsyncSessionLocal`, `app.db.session.engine`,
  `app.db.session.AsyncSessionLocal`, `app.api.deps.AsyncSessionLocal`); every other
  production consumer resolves lazily; zero module-level by-value captures in
  production. Test-side: 6 files patch the factory themselves, 15 TestClient files
  (never module-level), 15 files build their own engines (already isolated), no test
  reads the global `engine` or depends on `./test.db` contents.
- **Files changed:** `backend/tests/conftest.py` (fixture),
  `backend/tests/integration/test_api/test_tournament_endpoints.py` — 3 tests
  rewritten + 1 new + docstring (the other ~180 lines of its 238-line diff are W5's
  fpdf conversion). The 3 tests asserted the old premise (reads 503 because shared
  `./test.db` was never initialised — ASGITransport never runs lifespan); they now
  assert the honest live contract (list 200 `{"data": []}`, unknown id 404, no-active
  404), and the 503 contract is preserved by new `test_list_tournaments_dead_db_503`
  (sessions from a no-tables `:memory:` engine via `dependency_overrides`). Only file
  in the suite asserting "Database unavailable" (grep-verified).
- **Proof runs (all `pytest -o addopts='' -q -p no:cacheprovider`):** smoke 38 passed;
  batch 1 (16 rebind-sensitive files) **285 passed**; batch 2 (16 TestClient/real-app
  files) **300 passed, 1 skipped**; batch 3 (14 DB-adjacent/service files)
  **238 passed, 1 skipped**; final check **61 passed**. **Unique total: 861 passed,
  2 skipped, 0 failed across 48 files.** Logs:
  `~/workspace/drafts/aegis-phase37-w3-logs/` (`batch2.log`, `batch3.log`,
  `broad_unit.log` partial).
- **COST — W3's claim FALSIFIED, then fixed (W3-fix).** The ledger/docstring claim
  "create_all+dispose ~0.12–0.21 s/test" is FALSE. Measured by the coordinator:
  fixture setup **~6 s per test** (25-test slice: 148.82 s for 25 passed; slowest
  setups 6.02–7.16 s; user+sys only ~15 s of 151 s wall — the time is blocked, not
  CPU). Root cause proven: `create_all` on a file SQLite DB costs 5.69–6.86 s
  because each DDL statement fsyncs on this VM's home disk (~85 ms per fsync; raw
  sqlite3 probe: single-transaction 0.145 s vs autocommit 5.475 s; tmpfs 0.003 s /
  0.016 s). The full suite at that tax was heading for ~3.5 h (13% after 30 min)
  and was killed. **Fix (W3-fix, test-only):** `PRAGMA synchronous=OFF` via a
  connect listener on the fixture's TEST engine only (`conftest.py:264–266`).
  Probe: plain 6.003 s → synchronous=OFF 0.137 s (journal_mode=MEMORY + OFF 0.115 s
  — skipped, marginal gain, extra semantic surface). After fix, the same 25-test
  slice: **25 passed in 8.23 s** (W3-fix) / **8.56 s** (coordinator re-run) — ~18×
  faster; slowest setups now 0.15 s. No-behaviour-change proof: tournament+W1+W2
  **55 passed**, DB-sensitive batch **179 passed** (total 259 with the slice, 0
  failed). Durability on OS crash is irrelevant for throwaway per-test DBs; in-test
  data visibility is identical. No production code touched.
- **Incidents (both fixed):** (a) first fixture version never deleted per-test DBs —
  filled the 512 MB tmpfs (1320 files / 489 MB), breaking concurrent runs (exit 120);
  fixed with teardown + setup-failure `unlink`; proven 0 `isolated.db` files left
  behind. Any full-suite run started before that fix is contaminated and invalid.
  (b) One nondeterministic `PytestUnhandledThreadExceptionWarning` ("Event loop is
  closed", aiosqlite worker thread) appeared once, did not reproduce in 4 repeat
  A/B runs — warning-only, zero test impact.
- **Honest limits:** full-suite tax with the pragma is NOT separately measured — the
  coordinator's final full run (§11) measures it. `ServiceCache` has a Redis tier;
  Redis is down here, so no cross-test caching occurs; if Redis were up, a cached
  tournament list could leak across tests (pre-existing design, untouched).

## 4. L-3 tree-wide fade decision — PROPOSAL (parent decides, not implemented)

Evidence (W2): the Bible's hard-cut rule (§3 "CAMERA SWITCHING PATTERNS", §9 "Must
match", §11) is observed on **live gameplay camera switches** in Krafton VODs
(BMSD/BMPS 2026 GF). §5 post-match and §6 break segments record only segment
*order* — no transition types are ever observed there. **Zero Bible evidence about
fades in post-produced packages either way.** Both existing bans are framed as
*camera/on-air* scope (`cinematic_controller.py:24–57`,
`transition_controller.py:7–10`). Blast-radius fact: no consumer anywhere reads any
of the three files' `.transition` fields (grep-verified — values only serialized
into dicts, e.g. `replay_director.py:306,346`); today the fades are inert data, not
on-air behaviour.

| File | Sites | Nature |
|---|---|---|
| `replay_director.py` | `:55` field comment lists fade/dissolve as legal; `:195` intro `"fade"`; `:221` action shots `"dissolve" if i > 0 else "cut"`; `:235` outro `"fade"` | Grey zone / closest to live — orchestrator-registered, builds replay sequences shown on air via the replay queue |
| `montage_generator.py` | `:96` dead `self.transitions` pool (never read); `:164` first segment `"fade"`; `:261–273` `_select_transition` with `random.choice` (nondeterministic, separate smell) | Post-produced highlight reels, not live switching |
| `documentary_generator.py` | `:57` dataclass default `"fade"`; `:266` `"fade_in"`; `:320` `"fade_out"` | Scripted narrative package. Note: `"fade_in"`/`"fade_out"` are in NEITHER banned set (both ban `"fade"` only) |

**Recommended option (parent decides): SCOPED, not tree-wide.**
1. Convert `replay_director.py` only: `:195` → `"cut"`, `:221` → `"cut"`,
   `:235` → `"cut"`; fix the `:55` comment to `# cut only (Bible: hard cuts on air)`;
   add a test asserting no created `ReplayShot` carries a non-`"cut"` transition.
2. Leave `montage_generator.py` and `documentary_generator.py` as deliberate
   differences — record: *"The Bible hard-cut rule (§3, §9) governs live camera
   switching observed in Krafton broadcasts. Post-produced packages — highlight
   montages and documentary scripts — are outside that observed evidence; their
   fade/dissolve transitions are a deliberate difference, not a ban violation. If a
   montage/documentary segment is ever aired as a live camera switch, it must go
   through the banned-transition guard first."*
3. Optional follow-ups (separate decisions): delete/wire the dead `:96` pool;
   `_select_transition`'s `random.choice` makes output nondeterministic.
**Alternative if the parent wants tree-wide:** convert all three files to `"cut"`
and extend one banned set to include `"fade_in"`/`"fade_out"`/`"dissolve"`/`"wipe"`.
Cost: invents a rule the Bible never observed for post-produced packages.

## 5. R-7 0.5→0 truncation nit — PROPOSAL (parent decides, not implemented)

Exact site: `backend/app/services/replay/queue.py:197`, `return int(value)` at the end
of `_candidate_score` (lines 171–197). The docstring says zero means "no score was
measured", yet any positive sub-1.0 score truncates to exactly that 0. Measured
(run 2026-11-03): `0.5 → 0`, `0.9 → 0`, `1.5 → 1`, `59.9 → 59`, `60.5 → 60`,
`"0.5" → 0`, `0.0 → None`, `1.0 → 1`. Existing test pins truncation for ≥1 values
only (`test_phase36_w4_residuals.py:517`: `_candidate_score(72.7) == 72`); no test
pins 0.5. Why it matters so little: the live publisher always sends an int
(`live_director/service.py:2337` `"score": int(fight["intensity"])`) — a 0.5 can
only arrive from a foreign/broken publisher (and the publisher itself truncates
first: `int(0.5)` = 0 at publish → `_candidate_score(0)` → `None` → dropped, so
the live path never even reaches the queue as 0.5). A 0-scored entry queues at
rock-bottom priority and is first victim of the bounded-queue policy — no wrong
label, no crash.

**Options:** (a) **Round** — `:197` → `round(value)` (0.5→1, 72.7→73): fixes the
self-contradiction but changes the pinned behaviour and alters ordering for every
fractional score — a real semantic change. (b) **Keep** — leave `int()`: zero
churn; the contradiction stays but is unreachable from the live publisher and
harmless. (c) **Document (W2's recommendation)** — leave `int()`, add one docstring
line: *"Sub-1.0 positive scores truncate to 0 and queue at lowest priority; only
the live publisher's int scores are expected — a foreign fractional score is not
treated as scoreless."* Plus a pinning test `_candidate_score(0.5) == 0`. Behaviour
becomes a documented, tested contract; no priority semantics change.

## 6. `fight_detector.py:1089` / `:1115` nondeterministic assignment — PROPOSAL (parent decides, not implemented)

Evidence (coordinator, read 2026-11-03): `teams_involved` is `Set[str]`
(`fight_detector.py:313, 384`). `:1089`:
`active_fight_id = f"fight_{int(time.time())}_{list(potential.teams_involved)[0][:8]}"`
— the fight-ID suffix is an arbitrary team (hash order). `:1115–1117`:
`teams = list(potential.teams_involved); primary_attacker = teams[0];
primary_defender = teams[1]` — arbitrary. The codebase's own convention elsewhere
is `sorted(...)` (e.g. `:893`, `:919`). The fields ARE serialized into the
`fight.detected` payload LiveDirector consumes (`:1469–1470`), but grep finds
**zero production readers** of `primary_attacker` / `primary_defender` anywhere in
`app/` (only test constructions) — today the assignment affects nothing downstream
except the stored/published values themselves. Uniqueness of the fight ID is
preserved by the timestamp; same-second + same-fragment collision is pre-existing
and unaffected by ordering.

**Options:** (A) **Sort (recommended)** — `sorted(potential.teams_involved)[0]` /
`[1]`, 2 lines + tests, with a code comment stating the order is alphabetical,
NOT the tactical aggressor (no signal data at promotion time says who attacked
first). Makes the label deterministic, reproducible, testable; costs nothing
semantically today since no consumer reads it. (B) **Derive tactically** — attacker
= source team of the first damaging signal (HP drop / knock) in the fight: real
semantic work, needs first-signal state retained at promotion time (not retained
today) — a later phase if a consumer ever needs tactical truth. (C) **Leave +
document** — record that the fields are hash-order arbitrary today and no consumer
reads them; revisit when a consumer appears.

## 7. `test_determine_third_party_fight` drift — FIXED (W4)

**Verdict: production was wrong, the test expectation was right.** Fix by reordering
checks in `_determine_fight_type`, not by touching the expectation.

- Evidence: (1) Enum semantics — `THIRD_PARTY = "third_party" # Third team joins`
  is defined purely by a third team joining, orthogonal to range; `LONG_RANGE =
  "long_range" # 100-300m sniping` is defined by distance. The test scenario
  (3 teams) is definitionally third-party; LONG_RANGE fired only on the *absence*
  of a PROXIMITY signal — absence of evidence. (2) Dead-branch proof — as ordered,
  THIRD_PARTY could only fire for a 3-team fight with no HP drop and no knock with
  `signal_count < 5`, a combination that doesn't occur in practice; every real
  third-party fight (docs/BGMI_TERMS_AZ.md: "2 teams fighting + 3rd team kills
  within 30 sec") carries HP drops and knocks, so it was always mislabelled
  LONG_RANGE or AMBUSH. (3) Membership already means engagement —
  `teams_involved` is populated only from combat-signal source/target team IDs
  (`:976–979`, `:996–999`), matching the Phase 24 derivation (≥3 distinct killer
  teams). (4) Old-code simulation (HEAD version extracted via `git show HEAD:...`):
  original test → `long_range` (fails), new pin test → `close_quarters` (would
  fail), guard test → `long_range` (unchanged).
- Fix: moved the `len(teams_involved) >= 3 → THIRD_PARTY` check from last to
  second (right after the utility check), byte-identical check, position only,
  with an in-code comment recording rationale + evidence. Utility
  (`utility_used >= 3`) still outranks third-party — no test or doc disambiguates a
  3-team utility-heavy fight; preserved deliberately, documented in the comment.
- Determinism proof (before fix, `pytest -p no:randomly -p no:cacheprovider
  -o addopts=""`): single test FAILED (`LONG_RANGE == THIRD_PARTY`,
  `test_fight_agent.py:304`); whole file 1 failed / 25 passed; dir run 20 failed /
  270 passed (incl. this test).
- After fix: `test_fight_agent.py` **28 passed** (was 25+1 failed, +2 new pinning
  tests: third-party beats close-quarters signals; 2-team fight stays LONG_RANGE);
  related suites (live_director, derivations) **61 passed**; whole
  `test_vision/test_agents/` dir run twice: **11 failed / 281 passed** — all 11 in
  untouched files (commentary 6, observer 4, team 1), pre-existing.
- Caveat: the first dir run showed 20 failures vs 11 later — the 8 flipped tests
  sit in files concurrently modified by sibling workers (mid-edit during that run).
- **Files changed:** `backend/app/agents/fight_agents/fight_detector.py`,
  `backend/tests/unit/test_vision/test_agents/test_fight_agent.py`.

## 8. Inventory re-measurement — VERIFIED UNCHANGED (W6)

Brief item 8 was already done in fix-up `941a13b`; verified, not redone blindly.
`git diff --stat 9bcc587 941a13b` shows only 2 already-tracked doc files changed, so
counts should be unchanged — confirmed by measurement at **941a13b** (all via
`git ls-tree` / `git archive` snapshot, immune to the dirty working tree):

| Metric | Fix-up @9bcc587 | Measured @941a13b |
|---|---|---|
| backend/app py files | 461 | **461** |
| backend/app LOC | 128,611 | **128,611** |
| Docs (top-level tracked) | 49 | **49** |
| Tests collected | 2587 | **2587** (in the 941a13b snapshot, 0 collection errors) |
| Migrations | 15 (001, 005–018) | **15** |
| DB tables | 34 (30 explicit + 4 auto-named) | **34** |

**Conclusion: `docs/PROJECT_INVENTORY.md` needs no correction.** No doc edits made.
(Note: the count 2587 was measured at 941a13b WITH the import-time fpdf stubs
still present, so it includes the 12 dossier-PDF tests collected via the leaked
stub. After W5's conversion (§9) those 12 are honestly uncollectable without
fpdf2 — see §11 note.)

## 9. 7 other import-time fpdf stubs — DONE, real count 9 (W5)

- **Enumeration: real count is 9, not 7.** Every test file installing an
  import-time fpdf stub (`sys.modules.setdefault("fpdf", …)` at module level, no
  teardown): `test_main_reliability.py`, `test_demo_mode_gating.py`,
  `test_mvp_unification.py`, `test_integrity_operator_state.py`,
  `test_phase32_ws_dossier_auth.py` (unit) + `test_analytics_endpoints.py`,
  `test_match_endpoints.py`, `test_tournament_endpoints.py`,
  `test_live_events.py` (integration). Correctly NOT counted:
  `test_phase33_db_boot_fail_fast.py` (already the context-manager pattern),
  `test_phase35_w2_auth_controls.py` (converted in Phase 36),
  `test_phase26_lifespan_monitor.py` (no stub — skips on `ImportError`). Only
  `backend/app/dossiers/pdf.py` imports fpdf in production code, under
  `app.dossiers.*`, which the Phase 33 eviction covers.
- **Conversion:** exact Phase 33 treatment (`_fpdf_stub_if_absent`: stub only inside
  the context, `sys.modules` restored exactly on exit, stub-bound `app.main` /
  `app.dossiers.*` evicted; skip is `ImportError`-only). Unit files import the helper
  from `test_phase33_db_boot_fail_fast`; integration files carry a local verbatim
  copy (pytest only puts each test file's own directory on `sys.path`; a
  cross-directory import hack would be fragile, and `conftest.py` is W3's).
  `grep setdefault("fpdf"` across `backend/tests` → **zero** (one hit remains only
  inside a conftest *comment*). No `def test_` removed (HEAD vs worktree counts
  identical in all 9 files).
- **Proof — forward/reversed:** pre-change baseline proved the order-dependence was
  real (forward: 146 passed / 5 failed via leaked stub; reversed: collection ERROR,
  0 tests run). Post-change, 9 files only: forward **140 passed**, reversed
  **140 passed — identical**. Fresh-interpreter teardown probe 9/9 OK
  (`W5_TEARDOWN_ALL_OK (9/9)`). Dossier file alone: `ModuleNotFoundError: fpdf` —
  honest failure, both orders. (139 → 140: W3 rewrote the tournament file after the
  conversion, adding `test_list_tournaments_dead_db_503`.)
- **Note:** the conftest docstring flag (W5: ":203 still says the 9 stub files are
  untouched") was already resolved by W3 — the docstring now states no stub files
  remain (coordinator verified).
- **Files changed:** the 9 files above. No `conftest.py` edit, no full-suite run.

## 10. 62 pre-existing/env failures — 9 fixed, rest left with reasons (W6)

**6 production fixes, 9 tests fixed, no test edited** (all production bugs with
evidence in the code itself; final proof run on the 9 fixed tests: **9 passed**):

| Fix | What | Test(s) fixed |
|---|---|---|
| F1 | `coordinate_mapper.distance_world()` returns `np.sqrt(...)`, so `is_in_safe_zone` returned `numpy.bool_`, failing `assert True is True` + breaking JSON serialisation. Fix: `return bool(... <= zone_radius)`. | 3 (`is_in_safe_zone` inside/outside/on_edge) |
| F2 | `full_dead_count` never written anywhere (grep: zero writers), so `is_eliminated()` could never be True. Fix: `self.team.full_dead_count = dead` (DEAD = cannot be revived). Also unblocks the ELIMINATED branch of `_detect_team_activity`, which was dead. | 1 (`test_team_eliminated`) |
| F3 | `reset_for_new_match` captured the roster AFTER replacing the tracker (`old_players = self.team.players` read the brand-new empty roster). Fix: capture `list(self.team.players)` before the assignment. | 1 (`test_reset_for_new_match`) |
| F4 | `_update_broadcast_state` branch order made FINAL_CIRCLE unreachable: LATE_GAME (`teams <= 5, zone >= 5`) preceded FINAL_CIRCLE (`zone >= 7, teams <= 3`) and MATCH_END (`teams <= 1`), swallowing both. Fix: EARLY → MATCH_END → FINAL_CIRCLE → MID → LATE. | 2 (final_circle, match_end) |
| F5 | Player-tracker lost-track off-by-one: `frames_lost > max_lost_frames` removed a track only after 31 lost frames, contradicting the field comment "Frames before removing track" (30). Fix: `>=`. **Weakest fix — rests on the field comment; flagged for QA.** | 1 (`test_update_tracks_lost`) |
| F6 | `generate_commentary` raised `KeyError: 'intensity'` on a realistic kill context; the method's docstring promises `None`-on-failure, not an exception. The hype-spike handler already defaults intensity to 50 — same default applied at the common path. | 1 (`test_generate_kill_commentary`) |

**Regression checks:** observer + commentary files 130 passed / 10 failed (the 10
are the intentional leaves below); director-adjacent files **140 passed**;
team/vision-intelligence-adjacent **67 passed**; minimap file still fails exactly
its 2 pre-existing tests (unchanged).

**Remaining failures by area — 53 by W6's count (62 − 9); 52 after W4's §7 fix:**

| Area | Remaining | Why left |
|---|---|---|
| e2e | 13 | Environment-gated — not touched per brief |
| OBS | 19 | Environment-gated — not touched per brief |
| dossier-PDF / fpdf | 5 | fpdf2 absent in this env — not touched (see also §11: 12 dossier tests now honestly uncollectable) |
| event-bus / Redis | 1 | No Redis in this env — not touched |
| commentary | 6 | Test drift, production not wrong: `_check_novelty`/`_recent_commentary` no longer exist (2); `HypeCategory.CLUTCH_WIN`/`KILL_HYPE` renamed/never existed (3); `on_clutch_start` tension +30 → 40 vs test's >50 is tuning with no evidence (1) |
| observer | 4 | `test_initialization` priority 60 vs 100 (semantic, no evidence); `test_switch_to_clutch` expects `BroadcastScene.priority_score`, which the model never had; `test_select_best_candidate` passes a bare `Mock()` as director_state (test fixture); `SafetyController.test_initialization` expects `config.min_scene_time`, which `AgentConfig` never had |
| team | 1 | `test_detect_activity_fighting` sets a phantom `team.is_fighting` attribute (no such field on `TeamStateTracker`); `_detect_team_activity` derives FIGHTING from `under_attack`/knocks, never from a flag |
| coordinate-mapper | 0 | All 3 fixed |
| minimap | 2 | `get_detection_regions`: region `y == 0.7` exactly vs test's `> 0.7` (boundary fudge, no evidence); `minimap_to_world`: production deliberately scales px→world metres (8000 m map), test's "uncalibrated → identity" expectation contradicts the method's design |
| player-tracker | 1 | `test_update_tracks_existing` expects top-left (105, 205); production consistently uses detection centre (130, 255) for matching, velocity, history and new tracks — test is stale |
| fight-agent | 1 → **0** | `test_determine_third_party_fight` — fixed in §7 (W4) |

**Files changed (W6):** `backend/app/vision/post_processors/coordinate_mapper.py`,
`backend/app/agents/team_agents/team_tracker.py`,
`backend/app/agents/observer_agents/director_ai.py`,
`backend/app/vision/detectors/player_tracker.py`,
`backend/app/agents/commentary_agents/script_generator.py`.

## 11. Full suite + frontend — coordinator run (after last fix) — TBD

- Command: `cd backend && TMPDIR=/home/hatch/tmp-phase37-fullsuite python3 -m pytest
  tests --ignore=tests/unit/test_dossier_pdf.py -o addopts='' -q -p no:cacheprovider`.
  Log: `~/workspace/aegis-phase37-full-suite.log`.
- **Result (measured 2026-11-03, detached run, PID 6444): 2556 passed / 47 failed /
  3 skipped, 0 errors, 1388.05 s (23:08), EXIT:1.** Log:
  `~/workspace/aegis-phase37-full-suite.log` (since overwritten by the second run;
  the first run's FAILED list was diffed identical 47/47 against QA's independent
  log `~/workspace/aegis-phase37-qa-full-suite.log` before overwrite).
- **SECOND RUN (after W1-fix, detached PID 4068, same command/flags): 2574 passed /
  47 failed / 3 skipped, 0 errors, 1684.84 s (28:04), EXIT:1.** Failed list
  diffed IDENTICAL 47/47 against QA's independent run. Passed +18 vs the first
  run = exactly the 18 new W1-fix tests, all green. Slower wall time (28:04 vs
  23:08) is the frontend tsc+build running concurrently on the 2-core VM, not a
  suite regression. `backend/test.db` absent after the run.
- **THIRD RUN (after W1-fix2, detached PID 25248, same command/flags): 2586 passed /
  47 failed / 3 skipped, 0 errors, 1618.82 s (26:58), EXIT:1.** Failed list
  diffed IDENTICAL 47/47 against QA round 2's independent log
  (`~/workspace/aegis-phase37-qa2-full-suite.log`). Passed +12 vs the second run
  = exactly the 12 new W1-fix2 tests, all green. `backend/test.db` absent after
  the run.

### QA3 orphaned run — DISCREPANCY (measured, under investigation, NOT a Phase 37 regression on current evidence)

Round-3 QA died mid-run (no verdict), but its detached full-suite run survived
and finished: **48 failed / 2579 passed / 9 skipped, 0 errors, 1299.68 s
(21:39)**, log `/home/hatch/aegis-phase37-qa3-full-suite.log` (home root, not
`~/workspace/`). Same total outcomes as the third run (2636). FAILED list =
known 47 + **one extra: `test_frag_grenade_knock_entry`
(`test_killfeed_phase23.py::TestLayoutParserRealCrops`)**. Skips: 9 vs 3 — the
extra skips are all OCR/tesseract-dependent (5× `TestLayoutParse` in
`test_phase23_w2.py`, 1× pov_reader tesseract, 1× vision_integration tesseract;
the `test_killfeed_degrades_honestly_without_tesseract` test flipped the other
way: skipped in run3, passed in QA3).

**What the coordinator verified (2026-11-04 ~00:15 IST):**
- The extra failure is `layout_ocr_failed — "tesseract is not installed"`:
  `_parse_entry_strip` returns None when OCR raises. The test fails
  deterministically standalone (3/3 runs). **Tesseract is absent on this VM
  (known since Phase 29); no `tesseract` binary exists anywhere on the
  filesystem (full `find /`); no test installs, fakes, mocks, or PATH-manipulates
  one (grep-verified); collection order is deterministic (no random plugin,
  pytest.ini checked); `ocr_available()` freshly probes
  `pytesseract.get_tesseract_version()` per detector instance.**
- **Not a Phase 37 regression on current evidence:** no Phase 37 change touches
  the OCR/tesseract/vision path (the only vision change is the margin-getter
  clamp-on-read, which cannot affect `ocr_available()`); the standalone failure
  is the pre-existing missing-tesseract environmental gap; and the OCR tests
  PASSED in five full-suite runs (coordinator run1/run2/run3, QA round 1, QA
  round 2) and skipped/failed in exactly one (QA3 orphaned).
- **Mechanism UNEXPLAINED after direct investigation:** what made
  `get_tesseract_version()` succeed during the five passing runs is not
  established — no binary, no shim, no mock, no PATH write found. The dot-map
  proof is mechanical (QA3 dots at collect positions #850–854 are `s`, run3's
  are `.`; the `test_killfeed_degrades_honestly_without_tesseract` flip at
  #2507 corroborates the tesseract-availability difference).
- **Disposition:** recorded here as an open investigation item for round-3b QA,
  whose own independent full-suite run arbitrates: if it matches run3
  (47/2586/3), QA3 was the outlier; if it matches QA3 (48/2579/9), a systematic
  dependence exists and must be chased. Either way it does NOT block the N-1
  re-gate verdict, which concerns the scrub guarantee only — but a FAIL on this
  item alone would be recorded honestly if round-3b reproduces it.
- **Failure inventory — all 47 verified against the known pre-existing list
  (coordinator `grep ^FAILED` on the log):** e2e 13 (`test_broadcast_pipeline` 3,
  `test_full_match_flow` 6, `test_highlight_generation` 4), OBS 19 (all in
  `test_obs_integration.py`), commentary 6, observer 4, team 1, minimap 2,
  player-tracker 1, event-bus/Redis 1. Zero failures in any Phase 31–37 file, zero
  in db/deps files, zero NEW failures. The dossier-PDF 5 from the Phase 36
  inventory are absent because the file is `--ignore`d (honest collection error
  without fpdf2 — see dossier note below).
- **Reconciliation vs Phase 36 (2522 passed / 63 failed / 3 skipped, collect 2587)
  — exact:** passed +34 = +30 new tests + 1 inventory test (fixed in 941a13b)
  + 10 fixed (W6 9 + W4 1) − 7 dossier-passing tests excluded with the file;
  failed −16 = −1 inventory − 10 fixed − 5 dossier-failing tests excluded.
  Outcomes 2606 vs 2588 (+18 = +30 new − 12 dossier). The same +1
  outcomes-vs-collect discrepancy seen in Phase 36 (2588 outcomes vs 2587
  collect) is present here too (2606 outcomes); it is pre-existing and unchanged
  by Phase 37, not a new artifact.
- **Dossier-PDF note:** `tests/unit/test_dossier_pdf.py` is `--ignore`d because, after
  W5 removed the import-time fpdf stubs, it honestly fails collection with
  `ModuleNotFoundError: fpdf` (fpdf2 absent in this VM) — previously it was only
  collectable via the leaked stub. Its 12 tests are therefore NOT in the collect
  count; this is the designed outcome, not a regression, and is recorded here
  rather than hidden.
- **Suite-run incidents (honest record):** two tracked full-suite sessions were
  SIGTERMed (172 s, 303 s) and a third ran 958 s before the VM/service restarted
  (~20:03 IST, `uptime` showed 2 min after reboot) — the restarts, not pytest,
  killed them. The run was re-launched detached (`setsid`/`nohup`) post-reboot.
  The first detached run crawled (~13% after 30 min) and was killed: the W3
  fixture's ~6 s/test fsync tax (§3). After the W3-fix pragma the diagnostic slice
  runs in ~8.5 s (was 148.82 s) and the suite was re-launched; §11 numbers above
  are from that re-run.
- Frontend: **tsc 0 errors, build 35/35 routes** (measured 2026-11-03).

## 12. Files changed (total)

23 modified + 1 new test file (coordinator `git status` after W1-fix; the ledger
itself is the second untracked file; `backend/test.db` absent — deleted, must
never be committed):

- Production: `app/agents/commentary_agents/script_generator.py`,
  `app/agents/fight_agents/fight_detector.py`,
  `app/agents/observer_agents/director_ai.py`,
  `app/agents/team_agents/team_tracker.py`, `app/api/deps.py`, `app/db/base.py`,
  `app/db/error_scrub.py` (W1-fix: `is_credential_key`),
  `app/vision/detectors/icon_matcher.py`, `app/vision/detectors/player_tracker.py`,
  `app/vision/post_processors/coordinate_mapper.py` (10 files)
- Tests: `tests/conftest.py`, `tests/integration/test_api/test_analytics_endpoints.py`,
  `tests/integration/test_api/test_match_endpoints.py`,
  `tests/integration/test_api/test_tournament_endpoints.py`,
  `tests/integration/test_websocket/test_live_events.py`,
  `tests/unit/test_demo_mode_gating.py`,
  `tests/unit/test_integrity_operator_state.py`,
  `tests/unit/test_main_reliability.py`, `tests/unit/test_mvp_unification.py`,
  `tests/unit/test_phase32_ws_dossier_auth.py`,
  `tests/unit/test_phase35_w1_margin_fallback.py`,
  `tests/unit/test_phase36_w1_scrub_adoption.py`,
  `tests/unit/test_vision/test_agents/test_fight_agent.py` (13 files)
- New: `tests/unit/test_phase37_w1_n1_scrubbed_reraise.py` (55 tests)

New tests total: **+60** (W1 25 + W1-fix 18 + W1-fix2 12, W2 2, W3 1, W4 2).

## 13. Backlog — what remains open (with reasons)

1. **Parent decisions (proposals in §4/§5/§6, not decided here):** L-3 fade scope
   (recommend SCOPED — convert `replay_director.py` only); R-7 truncation (recommend
   (c) Document); fight_detector `:1089`/`:1115` nondeterminism (recommend (A) sort
   + document as alphabetical-not-tactical).
2. **Raw `exc_info=True` sites outside W1's scope:** `app/core/event_bus.py`
   (Redis errors, 4 sites), `app/utils/async_utils.py:180` (generic task failures).
   Same class as N-1; not fixed in this phase.
3. **52 pre-existing/env failures** (§10 table) — left with per-item reasons; not
   Phase 37's to fix wholesale.
4. **N-1 boundary:** a driver message containing *only* the bare password with no
   key/URL context matches no scrub pattern (no known driver shape produces it).
5. **Dossier-PDF 12 tests** uncollectable without fpdf2 (honest collection error
   now; needs fpdf2 installed or a real PDF environment to pass).
6. **W6 F5** (`>` → `>=` lost-track boundary) rests on the field comment's
   semantics — flagged for QA to confirm or document differently.
7. **W3 fixture tax is fixed but the suite-time claim needs the final run** (§11);
   if Redis were up, `ServiceCache` could leak a cached tournament list across
   tests (pre-existing design, untouched).
8. **Out of scope, unchanged:** H-3 secrets/git history, 84 PNGs + 4 workflows,
   RF-DETR registry flip, outreach (paused), scrim/live proof.
9. **N-5 (NEW, open — §14c):** post-reboot SQLite `disk I/O error` bands in the
   W3 fixture's `create_all` — 122 errors (round-3b run 1) / 129 errors (run 2),
   in moving contiguous bands (replay/storyteller/smash/POI files), all affected
   tests pass standalone (200/200), no resource exhaustion, mechanism NOT
   established. Zero in any Phase 31–37/db/deps file; changes no FAILED list;
   does not block the re-gate verdict. Follow-up: `strace` on a failing window,
   or a test-only retry-on-IOERR in fixture setup.

## 14. Independent QA / security review — **FAIL** (reviewer, 2026-11-04)

**Verdict: FAIL.** The suite, the frontend, the inventory, the W3/W4/W6
work and the §4–§6 proposals all verify — but the phase's headline
security item (N-1, W1) does **not** deliver its stated guarantee: the
scrubbed surrogate still carries the raw credential-bearing exception
on its `__context__` at every raise site (D1, systematic), and two
further holes in the surrogate's own `str()`/`params` falsify the
"no credential in str()/args/statement/params/orig" claim (D2, D3).
The fixes are cheap (see each defect); QA fixed nothing — the
coordinator decides fix vs backlog and re-gates.

### Independent re-runs (measured by the reviewer, own TMPDIR)

- Full suite, exact coordinator command, fresh TMPDIR under
  `/home/hatch` (not `/tmp`): **2556 passed / 47 failed / 3 skipped,
  0 errors, 1382.94 s (23:02)** — matches the ledger's 2556/47/3
  exactly (1388.05 s coordinator run; 5 s is run variance). Log:
  `~/workspace/aegis-phase37-qa-full-suite.log`. FAILED list
  **identical 47/47** to the coordinator's log: e2e 13, OBS 19,
  commentary 6, observer 4, team 1, minimap 2, player-tracker 1,
  event-bus 1. Zero failures in any Phase 31–37 file, zero in db/deps
  files. `backend/test.db` absent after the run; 0 `isolated.db`
  files left in `/tmp`.
- Frontend: `npx tsc --noEmit` exit 0 (0 errors); `npm run build`
  exit 0; **35 routes** (35 `page.js` under `.next/server/app`) ✓.
- Collect: current tree (dossier ignored) **2605** vs 2606 outcomes —
  the same pre-existing +1 discrepancy as Phase 36 (2588 vs 2587),
  unchanged by Phase 37. Reconciliation arithmetic exact: passed
  +34 = +30 new + 1 inventory + 10 fixed − 7 dossier excluded;
  failed −16 = −1 inventory − 10 fixed − 5 dossier excluded.

### Re-derived numbers (all reproduce)

- New tests **+30**: W1 25 (`test_phase37_w1_n1_scrubbed_reraise.py`,
  counted), W2 2, W3 1 net (tournament file 12 → 13 `def test_`,
  3 rewritten + 1 new), W4 2. W5: all 9 files' `def test_` counts
  identical HEAD vs worktree except tournament +1 ✓; zero
  `sys.modules.setdefault("fpdf", …)` remains outside the conftest
  comment (`:208`) ✓.
- Margin suites: fallback 61 + knock-hint 25 = **86 passed** (was 84)
  ✓, no label change (all 84 pre-existing assertions identical).
  W1 suites (new + updated adoption): **45 passed** ✓. Targeted
  (W1 + revocation + fight-agent + tournament): **108 passed** ✓.
- §8 inventory at **941a13b** (own `git worktree`, restored after):
  py **461**, LOC **128,611**, docs **49** `.md`, tests **2587**
  collected (0 errors), migrations **15**, tables **34** — all exact.
  Caveat: 34 tables needs the full model import (incl.
  `app.analysis.models`); `app.db.models` alone registers **32** —
  the same 32 the W3 fixture creates (see note N-1).
- W3 blast radius: zero test files import `engine`/`AsyncSessionLocal`
  from `app.db.*` at module level (one hit is a string literal in
  `test_phase35_w4_db_scrub_migration.py:390`); 5 rebind names in 3
  modules confirmed in `conftest.py:295–299` ✓. Fixture setup timed
  by reviewer: 25-test slice 25 passed in 9.70 s, slowest setups
  ~0.18–0.20 s (claim ~0.14 s confirmed in substance; 6 s tax gone).
  `PRAGMA synchronous=OFF` exists only in `tests/conftest.py:264–266`,
  on the fixture's test engine ✓. Teardown disposes + unlinks (incl.
  setup-failure path) ✓.
- W4 non-vacuity, proven on HEAD code (941a13b worktree, new test
  file copied over): original test FAILED, new pin test
  `test_third_party_beats_close_quarters_signals` FAILED, guard test
  `test_two_team_fight_stays_long_range` passed ✓. Reorder is
  byte-identical check moved after utility; enum semantics, dead-branch
  reasoning, `teams_involved` population (`:976–979`, `:996–999`) and
  the BGMI_TERMS_AZ.md:471–474 quote all verified verbatim.
- W2 non-vacuity: HEAD getter is `float(getattr(…))` (returns `-1.0`
  verbatim, raises on junk) ✓.
- W6: F1 (bool coercion), F3 (roster captured before replace), F4
  (MATCH_END → FINAL_CIRCLE → MID → LATE; no swallow), F6 (intensity
  default 50, context wins when present) all sound on the code. F2
  sound: `full_dead_count` comment "Cannot be revived" matches the
  state machine — KNOCKED is the revivable state, DEAD is terminal
  (`team_tracker.py:174`, `:240–245`, `:677`). F5 (`>` → `>=`) is the
  weakest, as flagged: it rests on the literal reading of the
  `max_lost_frames = 30  # Frames before removing track` comment
  (`player_tracker.py:89`); the comment is genuinely ambiguous
  (tolerate-30 vs remove-at-30) — the test now pins the boundary, so
  this is accepted as a pinned judgment call, not a proven fact.
- Proposals §§4–6 — evidence all verified accurate, recommendations
  sound: L-3 Bible §3:94 ("All transitions are HARD CUTS"), §9:212
  "Must match", §11:232, §5/§6 order-only; replay `:55/:195/:221/:235`,
  montage `:96` (dead pool — zero readers)/`:164/:261–273`
  (`random.choice`), documentary `:57/:266/:320` (`"fade_in"`/
  `"fade_out"` in neither banned set); no consumer reads the three
  files' `.transition` fields except serialization (`:306/:346`).
  **SCOPED** (convert `replay_director.py` only) is the right call.
  R-7: `queue.py:197` `int(value)` + docstring contradiction + live
  publisher `int(fight["intensity"])` + pin test at
  `test_phase36_w4_residuals.py:517` (≥1 only) all confirmed;
  option **(c) Document** is sound. §6: `:1089`/`:1115–1117`
  hash-order assignment, `Set[str]` at `:313`/`:384`,
  serialization `:1469–1470`, zero production readers of
  `primary_attacker`/`primary_defender` — all confirmed; option
  **(A) Sort + document as alphabetical-not-tactical** is sound.

### DEFECTS (not fixed by QA; coordinator decides)

**D1 — MEDIUM — the scrubbed surrogate's `__context__` IS the raw
credential-bearing exception (systematic, all raise sites).**
`backend/app/db/base.py:499` (`get_db`), `:544`
(`get_db_context`), `:604`/`:605`/`:619` (`get_db_with_retry`),
`:673`/`:674` (`init_db`). The ledger §1 guarantees "no caller …
can receive an exception whose … `__cause__` / `__context__` chain
can carry a DB credential". False: `raise X from None` inside an
`except` block sets `X.__cause__ = None` and `__suppress_context__ =
True` but **still sets `X.__context__` to the raw original** —
verified empirically (pre-clearing `__context__ = None` before the
raise does not survive the raise). Reviewer's independent probe
(scratch, deleted after; credential-bearing `OperationalError` /
`IntegrityError` / `DisconnectionError` / non-SQLAlchemy at every
raise site, asserting over `str`/`args`/`statement`/`params`/`orig`/
`__cause__`/`__context__` and captured log records): **11 of 12
non-boundary cases leak via `__context__`** — the full original
(URL + password in statement/params/orig) is reachable as
`surrogate.__context__` (and `surrogate.__context__.__cause__` when
the original was itself chained). W1's own tests assert
`__cause__ is None` (6 sites) and never assert `__context__` —
that is why this shipped. Mitigating, honestly: traceback/log
rendering suppresses the context (`__suppress_context__`), and the
probe found **zero leaks in any captured log record** — exposure
needs a consumer that walks `__context__` programmatically
(error-tracking SDKs that serialize exception chains, chain-walking
diagnostics). Cheap fix, proven by the reviewer in a scratch
snippet: compute the surrogate inside the `except` block but
**raise it after the block exits** (no active exception →
`__context__` stays `None`); then add tests asserting
`surrogate.__context__ is None` and no secret in a full chain walk.

**D2 — LOW-MEDIUM — opaque-object bind params leak the URL into the
surrogate's own `str()`.** `backend/app/db/base.py:60–91`
(`_scrub_params_value`): non-`str` param values are returned
**untouched**, while SQLAlchemy renders `params` via `repr()` in
`str(exc)`. Probe: `OperationalError("SELECT 1", {"conn": <object
whose repr echoes the DATABASE_URL>}, …)` → `str(surrogate)` and
`surrogate.params` contain the full `postgresql+asyncpg://user:
password@host` URL. The log line is clean only because
`scrub_db_error_text` re-scrubs the rendered string; the surrogate
object itself is not clean. Note the asymmetry: `_scrub_arg`
(`:94–116`) already does the right thing — replace a value whose
`str()` changes under scrubbing — but `_scrub_params_value` does
not. Precondition is contrived (a bind param that is an object
whose `repr` echoes the DB URL; string params containing the URL
ARE scrubbed), but the fix is a 3-line alignment with `_scrub_arg`.
Add an opaque-`repr` param probe test.

**D3 — LOW — bare-password param values survive leaf-scrubbing
(dict-key context is discarded).** Same function
(`base.py:60–91`): keys and values are scrubbed independently, so
`{"pwd": "<bare password>"}` / `{"x": "<bare password>"}` keep the
password — probe cases 1–2 show it in `surrogate.params` and in
`str(surrogate)` via the rendered `[parameters: …]` block. This is
the same bare-password class as the ledger's honestly disclosed
boundary (a) for driver *messages*, but the boundary text does not
cover structured params, where the key (`pwd`/`password`) IS the
context the scrubber ignores. Cheap fix: in `_scrub_params_value`,
redact a dict value wholesale when its key matches a credential-key
pattern (same pattern family as `_CREDENTIAL_KV_RE`). Precondition
(binding the DB password itself as a query param) is contrived.

**Boundary (a) confirmed honest, not a defect:** a
bare-password-only `orig` (`Exception("<password>")`, no key/URL
context) leaks through exactly as the ledger discloses — probe
reproduced it. The ledger's disclosure is accurate.

### What held (for the record)

- Log side fully clean in every probe case (`log_leaks=[]`): message
  + `_scrubbed_exc_info` surrogate traceback carry no secret.
- `deps.py` is_active sites (`:515`, `:751` pre-edit; now `:521`,
  `:761`): probe drove `authenticate_checked` /
  `authenticate_websocket` end-to-end through a raising
  `AsyncSessionLocal` — both fail closed (`None`) with clean logs ✓.
  No raw `exc_info=True` remains in `deps.py` or `base.py` (only
  comment mentions) ✓. `get_db_with_retry` has zero production
  callers (repo-wide grep: definition only, `base.py:560`) ✓, and the
  restored `OperationalError`/`DisconnectionError` type was confirmed
  by the probe (`type=OperationalError` on exhaustion).
- No accidental changes: full diff is the 22 files + 1 new test file
  of §12; no debug/`print`/`breakpoint` leftovers; no unrelated edits
  spotted.

### Notes (not defects)

- **N-1 (W3 fixture scope):** the fixture creates the **32**-table
  metadata — `drop_events`/`zone_phases` (`app.analysis.models`)
  are not created. No test needs them through the fixture (both
  referencing tests use in-memory objects only), but the fixture's
  "32-table" scope vs the inventory's 34 tables should be recorded
  in one place, not left for the next reader to reconcile.
- **N-2 (stale docstring):** `tests/conftest.py:76–84` (`db_session`
  docstring) still says the shared-`./test.db` gap "remains open" —
  false since the W3 autouse fixture closed it; contradicts the W3
  header directly below it.
- **N-3 (F5):** see above — weakest fix, accepted as pinned
  judgment call.
- **N-4 (§13 item 2) stands:** raw `exc_info=True` sites outside
  W1's scope remain in `app/core/event_bus.py` (4 sites) and
  `app/utils/async_utils.py:180` — not re-verified line-by-line,
  carried from the ledger.

---
*Phase 37: backlog closure. No new features. Every number in this ledger was
measured (commands quoted in the worker reports and §11); where a prior claim
did not reproduce (W3's 0.12–0.21 s, the "14 imports / 8 files" tight count, the
"7 fpdf stubs" count), the ledger records the measured correction.*

## 14b. Independent QA / security review — **FAIL** (re-gate, round 2, 2026-11-04)

**Verdict: FAIL.** D1 is genuinely fixed and verified — every raise site now
hands the caller a surrogate with `__context__ is None` and a clean full chain
walk, and the 18 new tests are real and non-vacuous. D3 is fixed at nesting
depths 1–4. But the D2 fix is incomplete in exactly the channel it defends:
SQLAlchemy renders `params`/`args` via `repr()`, and the scrubber checks only
`str()` — an object with a clean `__str__` but a secret-bearing `__repr__`
leaks the full DATABASE_URL into the surrogate's own `str()` (D1r, same class
and severity as round-1 D2, which the fix worker explicitly left "for QA to
call"). The same gap sits in `_scrub_arg`, whose docstring claims repr safety
it does not deliver. A second, narrower hole: a credential-keyed value nested
5+ deep leaks the bare password through the depth cap (D2r). Both fixes are
cheap (see each defect); QA fixed nothing — the coordinator decides fix vs
backlog and re-gates. The suite, the frontend, the inventory, the W3/W4/W6
work and the §4–§6 proposals all still verify.

### Independent re-runs (measured by the reviewer, own TMPDIR)

- Full suite, exact coordinator command, fresh TMPDIR
  `/home/hatch/tmp-phase37-qa2`: **2574 passed / 47 failed / 3 skipped,
  0 errors, 1664.63 s (27:44)** — matches the ledger's 2574/47/3 exactly
  (1684.84 s coordinator run; 20 s is run variance). Log:
  `~/workspace/aegis-phase37-qa2-full-suite.log`. FAILED list **identical
  47/47** to the coordinator's log (diffed): e2e 13, OBS 19, commentary 6,
  observer 4, team 1, minimap 2, player-tracker 1, event-bus 1. Zero
  failures in any Phase 31–37 file, zero in db/deps files, zero NEW
  failures. `backend/test.db` absent after the run; 0 `isolated.db`
  files left behind.
- Frontend: `npx tsc --noEmit` exit 0 (0 errors); `npm run build` exit 0;
  **35 routes** (35 `page.js` under `.next/server/app`) ✓.
- Scrub + adjacent 7-file set (new 43-test file + adoption + db-boot +
  db-scrub-migration + revocation + auth-controls + credentials-auth):
  **139 passed, 0 failed** (ledger's 134 was a slightly narrower file
  set; all green). The 4-file core subset alone: **94 passed**.

### D1 — VERIFIED FIXED (independent probe, scratch file, deleted after)

Reviewer's own probe drove credential-bearing `OperationalError` /
`IntegrityError` / `DisconnectionError` / non-SQLAlchemy exceptions at
**all 8 scrubbed re-raise paths** — get_db commit, get_db endpoint-error
via the faithful `@asynccontextmanager` athrow shape (FastAPI), get_db_context
athrow, get_db_with_retry exhaustion, retry non-retryable SQLAlchemy,
retry non-retryable non-SQLAlchemy, init_db SQLAlchemy, init_db
non-SQLAlchemy — plus a chained-original case: **all 9 pass**
(`__context__ is None`, `__cause__ is None`, full recursive
`__cause__`/`__context__` chain walk finds no credential in `str()` /
`args` / `statement` / `params` / `orig` of any node, formatted
traceback clean, types preserved, original exception untouched).
`_raise_clean_surrogate()` (`base.py:147`) is on **all 5 surrogate raise
sites** (grep: `:589` get_db, `:631` get_db_context, `:686` retry
non-retryable, `:700` retry exhaustion, `:761` init_db); no other raise
of a surrogate exists in `base.py` — the remaining raises are the two
deliberate bare re-raises of caller exceptions (`:585`, `:627`, by
design), the degenerate `raise last_exception` (`:692`, max_retries=0 →
TypeError, pre-existing), `create_engine`'s `RuntimeError` (`:418`) and
the repository's `HTTPException` (`:911`). Precision note: the ledger's
"7 raise sites" counts the two bare re-raises (or compute+raise line
pairs) alongside the 5 helper calls — coverage is complete, the count
is loose. `deps.py` sites only log and `return None`; no raw
`exc_info=True` remains in `deps.py` or `base.py` (only a docstring
mention, `base.py:52`) ✓.
- **18 new tests confirmed**: §H 8 + §I 4 + §J 3 + §K 3 = 18; file total
  43 = 25 + 18 ✓. All 43 pass in the suite run.
- **Non-vacuity proven by the reviewer, not taken from the ledger:**
  with `_raise_clean_surrogate` monkeypatched to a plain `raise
  surrogate` (neutered in the probe only, tree untouched), both
  athrow-shape probes FAIL exactly as designed (`__context__ is raw`) —
  the two athrow tests detect the defect they guard. (The fix worker's
  recorded pre-fix run — 14 failed / 28 passed — is consistent with
  this; the reviewer did not rely on it alone.)
- **2 stale assertions**: updated as claimed and justified — new file
  `:391–392` and adoption file `:333–339` now assert
  `__context__ is None` with comments explaining the old
  `__suppress_context__ is True` assertion encoded the `from None`
  mechanism the fix removes. (The remaining `__suppress_context__ is
  True` assertion at new file `:270` is NOT stale: that test raises the
  surrogate itself with `from None` and asserts that direct raise's
  semantics — legitimate.)

### D2 — FIXED for `str()`, residual REAL (defect D1r below)

- Opaque param whose `str()` echoes the URL is replaced by its scrubbed
  string; clean values keep exact type and identity (probe + §J tests) ✓.
- **Residual decision: NEEDS FIXING, not a documented boundary.** The
  fix checks `str()` to defend a channel SQLAlchemy renders with
  `repr()` — the wrong stringification for the channel. An object with a
  clean `__str__` and a secret-bearing `__repr__` is kept untouched
  (`params['conn'] is raw.params['conn']`), and `str(surrogate)` /
  `str(surrogate.params)` then carry the full URL. This is the same
  defect class and precondition shape as round-1 D2 (a bind-param
  object whose repr echoes the URL), one channel narrower — and round 1
  FAILED on that class at LOW-MEDIUM. Waving it through as a boundary
  would leave a known leak in the exact "never a raw credential"
  guarantee this phase exists to establish, when the fix is a few
  lines (check `repr()` as well as `str()`). Precondition remains
  contrived (no production code binds such an object today), hence
  LOW-MEDIUM, not higher.

### D3 — FIXED depths 1–4, leaks at depth ≥5 (defect D2r below)

- `is_credential_key()` verified by probe (not trusted from the
  ledger): `DB_PWD` ✓, `client_secret` ✓, `password`/`passwd`/`secret`/
  `token`/`api_key`/`my_password` ✓, `password_hash` ✗ (kept verbatim),
  `x` ✗, non-str key ✗. Wholesale redaction verified at nesting depths
  1–4 (`{'pwd': '<redacted>'}` at every level) ✓.
- At depth 5–6 the bare password **leaks**: the `_depth >= 4` cap
  stringifies the dict, and `{'pwd': 'fakepass-TEST-ONLY'}` matches no
  scrub pattern (the KV regex needs `key=value`/`key: value` with no
  quote between key and colon). The ledger's "redacted wholesale at any
  nesting depth" is therefore false at depth ≥5. Precondition is doubly
  contrived (a bind param nested 5+ deep AND carrying the bare DB
  password under a credential key — real bind params are flat dicts),
  hence LOW, but the claim as written is false and the fix is cheap
  (redact credential keys before stringifying at the cap, or extend
  the cap handling).

### N-2 / N-1 notes — VERIFIED FIXED

- **N-2:** `tests/conftest.py:76–84` (`db_session` docstring) now states
  the gap "is now closed suite-wide by the Phase 37 W3 autouse
  fixture" — the stale "remains open" text is gone ✓.
- **N-1:** the 32-table scope line is present (`tests/conftest.py:
  217–220`: "`Base` alone 32 tables, 34 after importing
  `app.analysis.models`; `drop_events`/`zone_phases` not created").
  **Re-measured by the reviewer:** `Base` alone **32** tables;
  after importing `app.analysis.models` **34** (incl. `drop_events`,
  `zone_phases`) ✓.

### Regression / scope — VERIFIED

- `git status` / `git diff --stat` show exactly the §12 file list
  (23 modified + 1 new test file + the ledger itself, untracked);
  the fix touched only `base.py`, `error_scrub.py`, `conftest.py`
  docstrings and the two test files. No other behaviour changed; no
  debug/`print`/`breakpoint` leftovers; reviewer's scratch probe file
  deleted after its run (confirmed absent).

### DEFECTS (not fixed by QA; coordinator decides)

**D1r — LOW-MEDIUM — clean-`__str__` / secret-`__repr__` opaque param
leaks the URL into the surrogate's own `str()`.**
`backend/app/db/base.py:60–91` (`_scrub_params_value`) and `:94–116`
(`_scrub_arg`): both apply the str-comparison rule only — a value is
kept when its `str()` survives scrubbing unchanged. SQLAlchemy renders
`params`/`args` via `repr()` in `str(exc)`, so an object whose
`__str__` is clean but whose `__repr__` echoes the DATABASE_URL is
kept (identity preserved) and the URL appears in `str(surrogate)` and
`str(surrogate.params)` / `str(surrogate.args)`. Reviewer probe
(fake placeholders only): `str(surrogate) leaks URL: True`,
`str(params) leaks URL: True`, `params['conn'] is original object:
True`, `repr(params['conn'])` = the full
`postgresql+asyncpg://fakeuser:fakepass-TEST-ONLY@fakehost.invalid:5432/fakedb`;
same result on the `_scrub_arg` channel (`str(args)` leaks,
`args[1] is original object: True`). The `_scrub_arg` docstring
("no secret can hide behind `str()` / `repr()` of an opaque object")
over-claims: it holds for `str()` only. The §J test pins
`__repr__ = __str__`, so it cannot catch this channel. Cheap fix:
keep a non-`str` leaf only when BOTH its `str()` and its `repr()`
survive scrubbing unchanged (in both functions); the existing §J
identity test still passes under that rule (its clean object's default
`repr` is clean). Add a probe test with clean `__str__` / secret
`__repr__`.

**D2r — LOW — credential-keyed dict value nested ≥5 deep leaks the
bare password.** `backend/app/db/base.py:60–91`: the `_depth >= 4`
cap stringifies the value, and the stringified dict
`{'pwd': '<password>'}` matches no scrub pattern. Reviewer probe:
depths 1–4 clean (`'<redacted>'` at every level); depth 5 and 6 leak
(`params={'inner': … "{'pwd': 'fakepass-TEST-ONLY'}"}`). Cheap fix:
redact credential-keyed values before stringifying at the cap (or
handle quoted-dict forms in the string scrubber); correct the ledger's
"at any nesting depth" to the cap actually enforced, and pin a
depth-5 test either way.

### Notes (not defects)

- **Ledger count precision:** §1's "7 raise sites" for
  `_raise_clean_surrogate` is loose — grep finds 5 helper call sites;
  the other two are the deliberate bare re-raises. Coverage is
  complete; correct the count, not the code.
- **Boundary (a) unchanged:** bare password under a non-credential key
  (`x`) still survives in params, as pinned by the §K test — honestly
  disclosed, not a defect.
- **N-4 (§13 item 2) stands:** raw `exc_info=True` sites outside W1's
  scope remain in `app/core/event_bus.py` (4 sites) and
  `app/utils/async_utils.py:180` — carried from the ledger, not
  re-verified line-by-line.

## 14c. Independent QA / security review — **PASS-WITH-NOTES** (re-gate, round 3b, 2026-11-04)

**Verdict: PASS-WITH-NOTES.** D1r and D2r are genuinely fixed and verified by
the reviewer's own probes (not taken from the ledger), D1 holds with no
regression, the frontend and the regression suites are green, and the tree
is exactly the §12 file list. The QA3 discrepancy is resolved (see below):
both of the reviewer's independent full-suite runs reproduce the orphaned
QA3 run, not the coordinator's third run — the OCR tests now skip/fail
because the tesseract binary is gone from this VM, which is an
environmental fact, not a Phase 37 regression. The "WITH-NOTES" is one
item: both full-suite runs carry ~120–129 setup errors from a
post-reboot SQLite "disk I/O error" phenomenon in the W3 fixture's
`create_all` (bands move between runs, all affected tests pass
standalone, mechanism not established — see note N-5). It touches no
Phase 31–37, db, or deps test and changes no FAILED list, so it does
not block the N-1 re-gate — but a full suite that cannot go clean in
this environment is not a clean PASS, and the phenomenon needs a
follow-up. Rounds 1–2 verdicts (§14, §14b) stand as history.

### D1r — VERIFIED FIXED (reviewer's own probe, scratch file, deleted after)

Reviewer's probe (40 checks total across D1r/D2r/D1, all pass, fake
placeholders only) drove an object with a clean `__str__` but a
secret-bearing `__repr__` through both channels:

- **Params channel:** `OperationalError("SELECT 1", {"conn": leaky, ...})` →
  no fake URL/password in `str(surrogate)`, `str(surrogate.params)`,
  `str(surrogate.args)`; the object is replaced by its scrubbed `str()`
  form (`"clean dbapi conn object"`), not kept; clean `int`/`str` values
  keep type. **Nested:** the same object inside a list param is replaced
  leaf-wise, no leak.
- **`_scrub_arg` channel:** `_scrub_arg(leaky)` replaces it; a plain
  `SQLAlchemyError("msg", leaky)` surrogate has no leak in `str()` /
  `str(args)` and `args[1]` is the scrubbed string.
- **Clean objects keep type + identity** on both channels
  (`_scrub_params_value`, `_scrub_arg`, `_scrub_opaque_leaf` all return
  the identical object) — the §J identity test still passes unmodified,
  as QA round 2 predicted.
- **Edge the fix worker disclosed:** a `repr()` that raises fails closed
  to the scrubbed string (no crash, no leak, via both the helper directly
  and the params channel); a `str()` that raises fails closed to
  `"<redacted db error>"`.
- **Shared helper confirmed:** `_scrub_opaque_leaf()` (`base.py:139`) is
  called by both `_scrub_params_value` (`:64`, `:136`) and `_scrub_arg`
  (`:230`, `:250`); `_scrub_arg`'s docstring now states the both-check
  rule (it previously claimed repr safety while checking `str()` only).
- **New tests exist and are non-vacuous:** `TestOpaqueParamReprScrub`
  (test file `:1156`, 3 tests, all use a DIVERGENT repr — unlike the §J
  test that pins `__repr__ = __str__`), 12/12 new tests pass un-neutered.
  Non-vacuity proven by the reviewer with a pytest-plugin neuter (tree
  untouched): with `_scrub_opaque_leaf` replaced by the TRUE round-2
  rule (keep when `str()` survives scrubbing, repr unchecked), **all 3
  D1r tests fail**; with `_redact_credential_keys_deep` replaced by
  identity, **5 of 9 D2r tests fail** (depths 5–8 + list-at-cap; the 4
  depth 1–4 controls pass). (First neuter draft always returned the
  scrubbed string — itself safe, so it proved nothing; corrected to the
  true old rule, then all 3 failed. The fix worker's recorded pre-fix
  run — 8 failed / 4 passed — is consistent with this; the reviewer did
  not rely on it alone.)

### D2r — VERIFIED FIXED (same probe)

- Credential-keyed values at nesting depths **5, 6, 7, 8** are clean
  through both `_scrub_params_value` directly and the full surrogate
  (bare password appears NOWHERE in `str(surrogate)` /
  `str(surrogate.params)`); the cap string carries `"'pwd': '<redacted>'"`.
- **5000-deep nesting:** no leak, no `RecursionError` escaped.
  **Cyclic dict:** terminates, no leak.
- **Fix confirmed in code:** `_redact_credential_keys_deep()`
  (`base.py:170`) is iterative (explicit stack, zero recursive self-calls
  by source inspection) and the cap redacts BEFORE stringifying
  (`str(_redact_credential_keys_deep(value))`, `:112`). The ledger's
  "redacted at any nesting depth" claim now holds as written.
- **New tests:** `TestCredentialKeyedDeepNesting` (test file `:1347`, 9
  tests: depths 1–4 control + 5–8 defect + list-at-cap), all pass;
  non-vacuity shown above.

### D1 (round-1 fix) — VERIFIED, NO REGRESSION

- Reviewer's probe drove **3 shapes**: get_db commit-failure,
  get_db athrow shape (faithful `@asynccontextmanager` wrap, error
  thrown into the generator at the yield point), get_db_context athrow
  shape — all hand the caller a surrogate with `__context__ is None`,
  `__cause__ is None`, a full recursive `__cause__`/`__context__` chain
  walk finding no fake credential in `str()` / `args` / `statement` /
  `params` / `orig` of any node, and type preserved (`OperationalError`).
- **Raise-site count confirmed by grep (ledger's corrected count is
  exact): 5 helper calls** — `:695` get_db, `:737` get_db_context,
  `:792` retry non-retryable, `:806` retry exhaustion, `:867` init_db —
  **+ 2 deliberate bare re-raises** — `:691` get_db generic branch,
  `:733` get_db_context generic branch (caller exceptions, by design).
  (`:278` is the helper's own internal bare re-raise; `:798` is the
  pre-existing degenerate `raise last_exception`.)

### Independent re-runs (measured by the reviewer, own TMPDIRs)

- **Full suite #1**, exact coordinator command, fresh TMPDIR
  `/home/hatch/tmp-phase37-qa3b`: **48 failed / 2457 passed / 9 skipped,
  11 warnings, 122 errors, 1524.79 s (25:24), EXIT:1.** Log:
  `/home/hatch/aegis-phase37-qa3b-full-suite.log`.
- **Full suite #2 (clean re-run)**, same command, fresh exclusive TMPDIR
  `/home/hatch/tmp-phase37-qa3b2`, no concurrent pytest from the reviewer:
  **48 failed / 2451 passed / 8 skipped, 10 warnings, 129 errors,
  1330.75 s (22:10), EXIT:1.** Log:
  `/home/hatch/aegis-phase37-qa3b2-full-suite.log`.
- **FAILED lists: IDENTICAL 48/48 to the orphaned QA3 log in BOTH runs**
  (diffed); both differ from the coordinator's third run by exactly the
  one extra failure `test_frag_grenade_knock_entry`. The 47 known
  pre-existing failures are unchanged in both runs (e2e 13, OBS 19,
  commentary 6, observer 4, team 1, minimap 2, player-tracker 1,
  event-bus 1). **Zero failures and zero errors in any Phase 31–37 file,
  zero in db/deps/scrub files, in either run.** `backend/test.db`
  absent after both runs; 0 `isolated.db` files left behind.
- The 9-vs-8 skip difference between the two runs is reconciled: in
  run #2 the pov_reader tesseract test errored at fixture setup (it is
  one of the 129 errors) instead of skipping, so it is counted as an
  error, not a skip (9 − 1 = 8).
- **Frontend:** `npx tsc --noEmit` exit 0 (0 errors); `npm run build`
  exit 0; **35 routes** (35 `page.js` under `.next/server/app`) ✓.
- **Regression:** scrub + adjacent 7-file set (55-test file + adoption +
  db-boot + db-scrub-migration + revocation + auth-controls +
  credentials-auth): **151 passed** ✓ (matches the ledger's 151).
  55-test file collects **55** ✓.
- **Scope:** `git status` / `git diff --stat` show exactly the §12 list
  (23 modified + 1 new test file + the ledger itself); the W1-fix2 work
  touched only `base.py`, the test file, and the ledger on top of it. No
  debug/`print`/`breakpoint` leftovers in the `base.py` diff.

### QA3 discrepancy — RESOLVED (mechanism FOUND)

The orphaned QA3 run (48 failed / 2579 passed / 9 skipped) **reproduces**;
the coordinator's third run (47 / 2586 / 3) does **not** reproduce in
this environment any more. The mechanism, established by the reviewer
(the coordinator had ruled out binary/shim/mock/PATH/collection-order;
the reviewer found what it missed — the reboot):

- **Timing:** run3's log finished 2026-11-03 23:37 UTC; the VM booted
  ~23:46 UTC (`uptime` 39 min at 00:25 UTC); QA3's run started ~23:49
  UTC (1299.68 s, finished 00:10 UTC). The reboot sits exactly between
  the two runs. All five passing runs (coordinator run1/run2/run3, QA
  round 1, QA round 2) ran pre-reboot; QA3 and both round-3b runs ran
  post-reboot.
- **Ephemerality:** `/` is an overlay with
  `upperdir=/run/hatch/overlay/upper`, and `/run` is tmpfs — **every
  write outside `/home` is reboot-ephemeral**, while `/home` (separate
  persistent volume `/dev/mapper/rv`) survives. A system-level tesseract
  install (`/usr/bin` or `/usr/local/bin`, e.g. via apt at some earlier
  point) therefore vanished at the reboot; nothing in the repo ever
  provided it. No `tesseract` binary exists now (`which`, `ls`
  `/usr/bin` / `/usr/local/bin` / `/opt/*/bin`, repo-wide `find`, and
  `pytesseract.get_tesseract_version()` → `TesseractNotFoundError`, all
  re-verified by the reviewer).
- **Standalone reproduction (reviewer, current env):** 5×
  `TestLayoutParse` SKIP ("tesseract unavailable"),
  `test_frag_grenade_knock_entry` FAILS, `test_killfeed_degrades_honestly_without_tesseract` PASSES — exactly the QA3 pattern, not
  run3's. `ocr_available()` (`killfeed.py:349`) can only return True
  via a real `tesseract --version` subprocess (`pytesseract` 0.3.13,
  `tesseract_cmd = 'tesseract'`, live `os.environ` PATH); no test
  patches it, no test sets it True (tests only ever set
  `_tesseract_ok = False`).
- **Residual uncertainty, stated honestly:** the install record itself
  does not survive (apt logs live on `/` too, and `/var/log/apt` is
  empty now), so "tesseract was apt-installed pre-reboot" is inferred
  from the timing + overlay ephemerality + absence of any in-repo
  provider — not directly evidenced. The disposition does not depend
  on that inference: whatever provided it is gone, and no Phase 37
  change touches the OCR path.
- **Disposition:** the discrepancy does NOT affect the N-1 re-gate
  verdict — it concerns the OCR test path only. If tesseract is ever
  needed again, it must be re-installed (and will be lost again on the
  next VM replacement unless installed into `/home` or the image).

### Notes (not defects)

- **N-5 (NEW, open): the post-reboot SQLite "disk I/O error" band.**
  Both round-3b full runs carry setup errors the five pre-reboot runs
  never had: run #1 **122 errors**, run #2 **129 errors**, every one
  `sqlite3.OperationalError: disk I/O error` at the W3 autouse
  fixture's `create_all` (`tests/conftest.py:271–274`). The bands MOVE
  between runs (run #1: one band, progress chars 1841–1962,
  test_poi_database → test_streaming; run #2: two bands, 924–1042 and
  2102–2111, test_phase27 / test_phase29 / commentary files) — so it
  is time-windowed, not file-specific. Ruled out by the reviewer:
  resource exhaustion (live check during a band: pytest process 21
  FDs, 325 MB RSS, 5 GB RAM free, 128 GB disk free, `/tmp` 1%);
  pytest tmp-cleanup deleting in-use dirs (lock-guarded, session dirs
  only — source-read in `_pytest/pathlib.py`); the fixture itself (a
  300-iteration fixture-faithful `create_all` loop passes 300/300;
  the 8 band files pass 200/200 standalone). Mechanism NOT established
  — candidates are a periodic runtime/VM job interfering with disk I/O
  or a transient host-volume fault; neither is provable from here.
  Impact contained: zero scrub/DB/deps errors in either run, FAILED
  lists identical to QA3, and the band files all pass standalone — so
  this is an environment reliability item, not a Phase 37 code defect,
  and not a gate blocker. Follow-up: re-run the suite with the fixture
  under `strace -f -e trace=%file` on a failing window, or check host
  disk health; if it persists, the suite needs a retry-on-IOERR in the
  fixture setup (a test-only change, not decided here).
- **Boundary (a) unchanged:** bare password under a non-credential key
  (`x`) still survives in params, as pinned by the §K test — honestly
  disclosed, not a defect.
- **N-4 (§13 item 2) stands:** raw `exc_info=True` sites outside W1's
  scope remain in `app/core/event_bus.py` (4 sites) and
  `app/utils/async_utils.py:180` — carried from the ledger, not
  re-verified line-by-line.
- **§13 items 1–8** (parent decisions, dossier-PDF, W6 F5, Redis leak
  caveat, out-of-scope items) are unchanged by this re-gate.

## 14d. Post-QA parent-decision implementation (fix-up worker, 2026-11-04)

**Status: parent-verified only — QA did NOT re-gate this fix-up.** The parent
accepted all three §4/§5/§6 decisions; the fix-up worker implemented exactly
the ledger's specifications, no more. No commit, no push, no stash.

- **L-3 SCOPED (§4):** `backend/app/agents/highlight_agents/replay_director.py`
  only — `:195` → `"cut"`, `:221` → `"cut"`, `:235` → `"cut"`; `:55` comment
  now `# cut only (Bible: hard cuts on air)`. `montage_generator.py` and
  `documentary_generator.py` UNTOUCHED. Deliberate-difference wording
  recorded (ledger §4, verbatim): *"The Bible hard-cut rule (§3, §9)
  governs live camera switching observed in Krafton broadcasts.
  Post-produced packages — highlight montages and documentary scripts —
  are outside that observed evidence; their fade/dissolve transitions are
  a deliberate difference, not a ban violation. If a montage/documentary
  segment is ever aired as a live camera switch, it must go through the
  banned-transition guard first."* New test file
  `tests/unit/test_phase37_fixup_l3_replay_transitions.py` (2 tests: all
  created shots `"cut"` across intro/action/outro; default transition
  `"cut"`). Path note: the implementation brief cited
  `backend/app/services/replay/replay_director.py`, which does not exist;
  the actual file is `backend/app/agents/highlight_agents/replay_director.py`
  and the ledger's line numbers matched it exactly.
- **R-7 (c) Document (§5):** `int()` at `backend/app/services/replay/queue.py:197`
  UNCHANGED. The `_candidate_score` docstring gains the §5 line: "Sub-1.0
  positive scores truncate to 0 and queue at lowest priority; only the
  live publisher's int scores are expected — a foreign fractional score
  is not treated as scoreless." Pinning test added to
  `test_r7_candidate_score_helper_unit`: `_candidate_score(0.5) == 0`.
- **fight_detector (A) Sort (§6):** `sorted(potential.teams_involved)[0]` at
  `:1089` (fight-ID suffix) and `sorted(...)` at `:1115` (attacker/defender),
  with the comment stating the order is alphabetical, NOT the tactical
  aggressor (no signal data at promotion time says who attacked first).
  Two determinism tests in `test_fight_agent.py` (same input → same
  suffix/attacker/defender; alphabetical pin).
- **§12 staleness corrected:** `test_phase37_w1_n1_scrubbed_reraise.py` is
  **55 tests**, not 25 (collect-verified: 55 collected, 49 `def test_` +
  parametrization). The §12 "new tests total +60" is unchanged — it counts
  increments (25 + 18 + 12 + 2 + 1 + 2).
- **Verification (measured, no full suite):** affected files only —
  L-3 file **2 passed**, `test_phase36_w4_residuals.py` **114 passed**,
  `test_fight_agent.py` **30 passed** (28 pre-existing + 2 new); combined
  run **146 passed, 0 failed** (`pytest -o addopts='' -q -p
  no:cacheprovider`). Non-vacuity: the two new fight tests pass under
  PYTHONHASHSEED 0/1/42 (2 passed each); old `list(set)[0]` returns
  mid_team/zeta_team, never alpha_team, on those seeds — old code fails
  the alphabetical pin. HEAD `replay_director.py` had fade/dissolve/fade
  at :195/:221/:235 (`git show HEAD:`) — the new L-3 test fails on HEAD.
  `py_compile` clean on all 6 touched files. `montage_generator.py` /
  `documentary_generator.py` confirmed untouched (absent from `git status`).
- **Fix-up files changed (additional to §12):** production
  `app/agents/highlight_agents/replay_director.py`,
  `app/services/replay/queue.py`, `app/agents/fight_agents/fight_detector.py`;
  tests `tests/unit/test_phase37_fixup_l3_replay_transitions.py` (new),
  `tests/unit/test_phase36_w4_residuals.py`,
  `tests/unit/test_vision/test_agents/test_fight_agent.py`; this ledger (§12
  count line + this section).
