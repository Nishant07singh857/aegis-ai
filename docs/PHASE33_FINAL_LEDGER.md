# Phase 33 Final Ledger — Post-Phase-32 Backlog

**Base:** local HEAD `877cc7a` (Phase 32, QA RE-GATE PASS; remote main `dd46d87b` per parent).
**Status:** code complete, independent QA **PASS-WITH-NOTES**. **No commit, no push by the team.**
Parent commits (per-file) and pushes only while the user is present in chat.
**Brief:** `~/workspace/drafts/aegis-phase33-backlog-brief.md` (order followed exactly).
**Standing rules kept:** spectator-mode only, zero API spend, Broadcast Bible grammar,
no invented numbers. Anything unmeasured is listed as a limit below, not hidden.

## QA verdict

Independent QA/security reviewer (read-only; probes only under `/tmp`; no production
or test file modified; never ran `git stash`): **PASS-WITH-NOTES**.
Every worker claim was re-run independently — no worker number was copied.
No worker claim failed re-verification; no `file:line` defect to name.

- Phase 33 suites re-run by QA: D-2 29 pass, POV 12 pass / 1 skip, DB boot 14 pass,
  RF-DETR 16 pass, doc batch re-read pass.
- Targeted regressions (workers): clip 72 pass / 5 pre-existing skip; POV 256 pass /
  1 skip; DB 10/10 + 6 pass / 1 pre-existing skip; ML dir 31 pass; margin gate 25/25.
- Frontend (QA): `npx tsc --noEmit` exit 0; `npm run build` exit 0.
- Full backend suite (QA, `-o addopts=''` throughout; pytest-cov missing here, so the
  cov gate is **NOT re-verified**, same as Phases 31/32): authoritative Run 3 =
  **2056 passed / 63 failed / 3 skipped**. All 63 are pre-existing/env, zero in any
  Phase 33 file: e2e 13, OBS 19, dossier_pdf 5 (missing fpdf), event_bus 1 (no redis),
  commentary 7, fight_agent 2, observer 6, team 3, coordinate_mapper 3, minimap 2,
  player_tracker 2. Two disputed tests were proven flaky/env at clean HEAD, not
  regressions (see §6). Collect 2121 = 2062 (Phase 32) + 59 new Phase 33 tests exactly.

## 1. D-2 clip_detector — FIRST, LIVE (fixed)

**Defect was live, not dormant.** `_create_long_range_clip` defaulted to
`weapon="Sniper"`, `distance=200`, and the commentary path built its clip event
without weapon/distance keys, so every such clip shipped an invented
"200m Sniper shot!" to viewers. Caller trace: only production caller of
`detect_clip("long_range_kill", …)` is `ClipDetector.handle_event`, fed by
`fight.detected` (long_range) → hype → `commentary.line` (carrying clip_event_type
but no weapon/distance). Other long-range paths audited and already evidence-gated
(sniper-triple checks, hype distance 0 → fail-closed below 150 m, replay-director
camera angles only) — no change needed there.

**Fix** (`backend/app/agents/highlight_agents/clip_detector.py`, +173/−16):
defaults removed entirely. Evidence helpers `_evidence_weapon` / `_evidence_distance`
are fail-closed: weapon from `weapon`/`weapon_name`/`weapon_class` (string,
IconMatch-shaped dict, or object with `.weapon`); distance from
`distance`/`distance_m`/`distance_meters`/`distance_metres` (finite, positive,
real numbers or plain numeric strings only). Sentinels (`Unknown`/`None`/
`unknown_*`), bools, 0/negative, NaN/inf, non-numeric strings → no evidence.
A `weapon_family` id alone (e.g. `ar_a`) is never rendered as a gun name.
Rendering matrix: weapon+distance → `"hits 320m AWM shot!"`; distance-only →
`"hits 320m shot!"`; weapon-only → `"long-range AWM shot!"`; neither →
`"long-range shot!"` (no gun, no number). Excitement gets no distance contribution
without evidence (base 40, +15 only on literal bool `headshot=True`).
`#Sniper` appears only when the evidenced weapon is exactly the `"sniper"` class.
`handle_event` now forwards evidence keys when the commentary event carries them.
Signature unchanged (`data, context`).

**Proof:** new suite `test_phase33_d2_clip_detector.py` 29/29 (QA re-run 29/29;
QA's own probe: no-evidence → `"INCREDIBLE! Scout lands a long-range shot!"`,
hashtags `[#LongRange, #BGMI]` only, excitement 40; AWM+320 → `"hits 320m AWM shot!"`,
no `#Sniper`; class `sniper`+250 → `#Sniper` added). E2E highlight file: same
4 failures with and without the change (websocket/SSL harness error, pre-existing).

**Honest limit:** no live viewer render on this VM (guarantee is at
title/description/payload level). No measured-distance producer exists yet for this
path — until an upstream producer forwards real kill-feed evidence, live long-range
clips render gun-less/distance-less by design (truthful, but plain). Wiring a real
evidence producer is follow-up work, not part of this fix. `_create_grenade_clip`'s
`victims=1` default is a separate function — flagged for a future worker, not fixed
here.

## 2. POV identity single-signal — VERIFIED FIRST, THEN FIXED (K-H5)

**Located path (re-inspection citation accurate, and the only confirming path):**
`backend/app/services/observer_pilot/state.py` `_resolve_identity()` returned
`highlight, True, "highlight"` on highlight-only and `ocr_index, True, "ocr"` on
OCR-only. This feeds rules R0–R5, the dead-streak, the director panel, and
LiveDirector POV — a single-signal read could previously drive a tap decision.

**Checked and left untouched (consumers, not confirmers):** `pov_reader.py` (returns
raw signals separately, never confirms); `rules.py` (gates on `identity_known`);
`service.py` dead-streak (gates on `identity_known`); LiveDirector `pov_summary` /
`_pov_on_fight` (read-only, gated); `multi_feed_monitor.py` (reports only);
`frontend/lib/observer-pilot.ts` (null unless known); deprecated `pov_selector.py`
(not on the live path).

**Fix** (`state.py` only, plus contract comments in `types.py`): the only
confirming case is now both signals present and agreeing → `(index, True, "both")`.
Exactly-one-signal, neither, unmatchable OCR text, and out-of-roster highlight all
return `(None, False, "none")`. Disagreement behaviour unchanged (logged + UNKNOWN).
Roster binding, HP velocity, statuses, and the no-roster → unknown rule unchanged.
`identity_source` is now documented as `"both" | "none"` only.

**Proof:** old single-signal test (which asserted the buggy behaviour) replaced by a
full-matrix test — highlight-only, OCR-only, both-agree, disagreement, neither,
unmatchable OCR ("Zulu"), out-of-roster highlight — W1 file 12 pass / 1 skip
(QA re-run identical; QA probe of `POVStateTracker` matches the matrix exactly).
Regressions: 256 pass / 1 skip across Phase 29 W1–W4, fixes, HP bands,
multi-feed monitor, and Phase 31 clutch/payoff suites.

**Coordinator decision (recorded, not snuck in):** tap verification
(`service.py:_spectated_reader`) returns only a highlight index — it is a
*position* check ("highlight read back as target"), not identity confirmation:
it never sets `identity_known`, names no player, drives no rule. Making it require
OCR agreement would need slot+roster plumbing through the executor's
`frame -> int` reader contract and would fail taps whenever OCR is unavailable
(Tesseract is not installed in this VM; nameplate region is uncalibrated).
Accepted as documented (scope note added in code); a slot+roster-aware verifier is
separate future work requiring parent direction. **Consequence:** feeds where OCR
is unavailable now report identity UNKNOWN and the pilot takes no POV action on
them — the intended Phase 29 contract, but OCR availability/calibration is now
load-bearing for POV actions. Synthetic frames only; no real phone/720p proof.

## 3. DB boot failure — LOUD FAIL-FAST (K-H6)

**Defect:** `main.py` lifespan wrapped `init_db()` in try/warn ("continuing with
in-memory state"), so an app configured with a dead Postgres silently booted
in-memory and could lose data without telling anyone.

**Fix** (`main.py`, `config.py`, `db/base.py`): new `_init_database_or_fail_fast()`,
called **unwrapped** by `lifespan()`; the old try/warn block is deleted. The
distinction lives in exactly one place, `Settings.database_configured`
(empty/whitespace-only = unconfigured, anything else = configured; no new operator
flag, no guessing):
- DATABASE_URL set + `init_db` fails → `RuntimeError`, boot aborts before the
  event bus/monitor/services start. The URL itself is never logged (it embeds the
  DB password); only the configured fact and the driver error are logged.
- DATABASE_URL unset/blank in non-production → intentional dev/test no-DB mode:
  `init_db` is **not called at all** (tests prove this with a would-raise stub),
  boot continues without persistence, and the log says so plainly.
- Production + unset is unchanged: Settings validation already refuses it first.

**Necessary related fix found while implementing:** the documented dev path
("empty = in-memory mode") could never actually boot — `create_async_engine("")`
raised at import, before the lifespan was reached. `base.py` now builds a
single-connection in-memory sqlite placeholder engine for empty URLs in
non-production (production still raises as defence-in-depth). The placeholder is
an import-time shim only; the lifespan deliberately skips `init_db` on it. The
configured-URL engine path is behaviour-preserving (pool sizes, postgres connect
args, listeners extracted unchanged into `_register_engine_listeners()`).

**Proof:** new suite `test_phase33_db_boot_fail_fast.py` 14/14 (QA re-run 14/14;
QA probe: unset/blank → boot continues with `init_db` provably not called;
configured-dead URL with stubbed raising `init_db` → `RuntimeError("Refusing to
boot…")` with password absent from message and log; production+unset →
`ValidationError` refusing boot). Regressions: credentials-auth 10/10,
Phase 26 lifespan/monitor 6 pass / 1 pre-existing skip (fakeredis missing).

**Honest limits:** no real Postgres in this VM — dead DB simulated via stubbed
`init_db` raising and real `init_db` against a refused port (127.0.0.1:59999);
hung-host timeout and auth-failure cases were not directly probed (same fail-fast
path, same exception surface). No full TestClient dead-DB boot run (needs
Redis/fakeredis, missing here). Later DB death mid-run is out of scope (boot fix
only; per-request failures remain per-request failures). The new tests install a
minimal `fpdf` stub only when real fpdf2 is absent (pre-existing VM gap).

## 4. RF-DETR decode — GATED, PENDING ON best.onnx (K-H7)

**Gap evidence (verified pre-change):** `best.onnx` absent — `git ls-files` for
`*.onnx` empty; only unrelated voice/venv `.onnx` files exist elsewhere; zero
`rfdetr` hits in `backend/app`/`backend/tests`. In `trained_detector.py`:
`decode_mode` had no `"rfdetr"` option (L109: `"yolov5" | "yolov8" | "direct"`);
`_decode_outputs` fell everything else through to the yolov5 anchor-head decode,
so an RF-DETR `dets`/`labels` pair would have been mis-decoded as yolov5 heads
(garbage boxes, not silence); `_preprocess` was plain `/255` only (no ImageNet
norm, which RF-DETR exports require); `_ensure_session` never recorded output
names, so a two-output graph could not be matched by name. `registry.py`
`killfeed` entry was `"yolov8"`, and `get_detector` could not even forward
`normalize_imagenet`/`input_size`.

**Patch** (`trained_detector.py` +121/−3, `registry.py` +13/−0, new test file
341 lines): `decode_mode` gains `"rfdetr"`; `normalize_imagenet` config;
ImageNet branch in `_preprocess` (mean `[0.485, 0.456, 0.406]`, std
`[0.229, 0.224, 0.225]`); `_ensure_session` records `_output_names`; new
`_decode_rfdetr` + `_session_output_names` helper (name match on `dets`/`labels`,
shape fallback `(∗,∗,4)` = dets, clear `ValueError` if the pair is missing);
`rfdetr` branch in `_decode_outputs` feeding the existing shared threshold + NMS +
letterbox-undo path. One deliberate deviation from the draft patch: ranking uses
full descending `argsort` instead of `argpartition(-Q*C')` (edge-case hazard when
`Q*C'` equals the flat length); semantics identical, documented in the docstring.
`get_detector` now forwards `normalize_imagenet`/`input_size` generically.

**Gating (the point of this item):** the `killfeed` registry entry was **NOT
flipped** — it stays `"yolov8"`. Flipping it now would present an unverified
model as live and break the YOLO26 fallback. The only way to reach the new code
is an explicit `decode_mode="rfdetr"` + `normalize_imagenet=True` +
`input_size=(384,384)` override (proven working by test, selected by no default,
env var, or registry entry today). No fake model file created; no Kaggle token
asked for (and none is needed for the future steps below).

**Proof:** new suite `test_trained_detector_rfdetr.py` 16/16 (QA re-run 16/16):
sigmoid values, bg-slot dropped (never a class id), cxcywh→xyxy at 384, score-desc
global ranking, one query scoring high on both classes yields two boxes,
out-of-order named outputs, shape fallback, missing-output `ValueError`,
letterbox-undo, threshold filtering, exact ImageNet values on a solid-red frame,
default `/255` path unchanged, mock-session end-to-end (4 boxes, normalised
`(1,3,384,384)` tensor, letterbox-undo mapping), and gating tests (defaults
unchanged; registry killfeed still yolov8; no-model → `None` → heuristic fallback).
Full `tests/unit/test_ml/` dir: 31 pass. **YOLO26 fallback proven:** a synthetic
tensor in yolov8 single-output layout `(B, 4+C, N)` through the existing
unmodified `_decode_yolov8` yields `killfeed_panel` (0.88) and `killfeed_entry`
(0.92) — decode compatibility only, not YOLO26 quality, no real YOLO26 file used.
**YOLO26 remains the decodable fallback today.**

**What is still needed the day best.onnx arrives (minutes of work, for a future
worker):** (1) place the real v3-notebook export where `VISION_MODEL_PATH`
points (e.g. `backend/models/best.onnx`); do **not** commit it to git without a
separate decision (binary weight file); no Kaggle token needed — the file is
handed over by the user. (2) Shape smoke test FIRST with onnxruntime: inputs
expect `(1,3,384,384)`-ish, outputs `dets (1,Q,4)`, `labels (1,Q,3)` — if resolution,
names, or class count differ, stop and adjust config, do not force the decode.
(3) Live trial via explicit override only on one real BGMI frame (registry
`get_detector("killfeed", decode_mode="rfdetr", normalize_imagenet=True,
input_size=(384,384))`, assert boxes land in the killfeed region and the hybrid
path still yields named kill events). Only after that passes, flip the registry
`killfeed` entry and re-run the full suite. **Still honestly unproven after this
patch:** any real-model accuracy (no test-set mAP claim exists), the
letterbox-vs-plain-resize distribution shift (hybrid OCR fallback covers misses),
and any 720p re-measurement. The real `best.onnx` graph has never been seen —
step (2) is a real gate, not a formality.

## 5. Low doc/comment batch

Only 6 files touched; **no logic changed anywhere** (QA re-read every diff hunk):
- `icon_matcher.py` `match()` docstring now describes the Phase 32 gate accurately
  (delegates to `match_candidates(top_n=1)`; weapon set only on identified family
  + score ≥ threshold + margin ≥ 0.15 boundary-inclusive; margin failure →
  `weapon=None` + `weapon_abstained=True`, distinct from low-score/`unknown_*`
  nulls; raw family/score/second_score still reported). Margin-gate suite 25/25
  proves no logic changed.
- `multi_feed_monitor.py` `_entry_to_event` fallback comment corrected: it does
  **NOT** re-gate — it passes `entry.weapon` verbatim and only mirrors a pre-set
  abstention flag. A foreign detector whose weapon is not margin-gated would leak
  ungated labels through this path. **Future work (not done here, comment-only
  per brief):** gate the fallback before any foreign detector is wired. None is
  wired in production today, so there is no live bypass. Production
  `KillFeedDetector` has its own `_entry_to_event` (killfeed.py:968).
- `PROJECT_INVENTORY.md`: headline table re-measured at HEAD `877cc7a` (backend/app
  459 py files / ~126,322 LOC; frontend TS/TSX ~31,430 LOC; endpoints 211 =
  209 HTTP + 2 WS from 212 decorator matches minus 1 docstring false positive;
  tables 33 = 30 explicit `__tablename__` + 3 auto-named; migrations 13
  (001, 005–016); top-level docs 44; routes 34; components 55; tests collected
  2062, 0 errors). §1/§5/§7/§9 detail flagged as the 2026-10-20 snapshot unless
  individually corrected; streaming rows corrected to K-C2 truth (shell streamer
  official, API 410 Gone, no real RTMP ever demonstrated); §11.1 corrected
  (`record_player_stats` HAS a live caller — `LiveStatsService` at
  live_stats.py:351, Phase 24 W6; remaining limit narrowed to `damage_dealt`
  still never written); §11.5 corrected (spotter headshot toggle exists at
  spotter/page.tsx:432, Phase 24; spotter/manual is a trusted-human producer,
  only VISION headshot still lacks a reliable producer at 7.7% precision).
  QA reconciled the endpoint/table counts independently (212 decorator matches →
  211 endpoints ✓; 30 explicit `__tablename__` + 3 auto-named = 33 ✓, the 31st
  grep hit being the `__tablename__` helper def in base.py, not a table);
  working-tree LOC 126,763 vs doc's 126,322 at HEAD = Phase 33 additions, as the
  doc itself scopes. Note: the re-inspection's "210 endpoints" figure used a
  different counting method — this ledger follows QA's reconciled 211.
- `README.md`: NOT rewritten wholesale — scaffold-placeholder banner added, plus
  only verified-false lines corrected (top-level `overlays/` is empty, overlay
  routes live under `frontend/app/overlays`; simulator runs only with
  `DEMO_MODE=1`, default OFF; no `use-aegis-ws.ts` — the WS hook is
  `frontend/lib/useLiveSocket.ts`).
- `SECURITY.md`: only verified-false lines corrected (tracked `.env*` files are
  exactly `backend/.env.development`, `backend/.env.production`,
  `backend/.env.example`, `frontend/.env.example` — gitignore does not untrack,
  purge still pending user decision; `User` table exists at
  `db/models/user.py:14` and auth checks the DB `is_active` flag, Phase 1
  settings-only description marked superseded, settings operator account noted as
  non-DB fallback only). SECURITY §1 "sanitized in the working tree" was NOT
  verified (verifying means reading a secret-bearing file — refused) and is left
  as-is.
- `frontend/app/watch/page.tsx`: header comment only — status always reports
  `"deprecated"`, so `streamIsRunning` can never return true and the page always
  shows honest offline standby even while the shell streamer runs; shell-streamer
  liveness is unreported anywhere; a future out-of-band health signal is needed.
  K-C2 decision untouched; no API-status resurrection.

**Deliberately left alone (with reasons):** `IconMatcher` class-docstring
`threshold` param (incomplete, not false); `_entry_to_event` docstring ("same
fields" — accurate); inventory §1 per-module breakdown (flagged as snapshot;
recomputing 20+ module counts is a re-audit, out of scope); inventory §4/§8/§9/§11
sections not re-measured and not asserted false; README remainder (Poetry/ports/
uvicorn — not verifiably false); /watch standby UI copy + LIVE badge JSX
(user-visible copy, not a comment — **parent decision**: out-of-band health signal
vs. copy change); other stale docs (ROLES, DIRECTOR, GRAPHICS, TRAINING,
KAGGLE_TRAINING, BGMI_TERMS_AZ, BETTER_THAN_KRAFTON, PRD_GAP_ANALYSIS, gap audits,
SPECTATOR_COVERAGE, orchestrator docstrings) — outside the brief's named scope,
flagged for a future batch.

## 6. Process incident — shared-tree stash (reconciled, read this before committing)

Worker 1 ran `git stash -u` on the shared tree to prove e2e failures were
pre-existing. The pop conflicted (concurrent edits to `icon_matcher.py` and
`PROJECT_INVENTORY.md` appeared mid-run); the worker restored only its own two
files. **Result:** completed Workers 2 and 4 files temporarily vanished from the
tree (showing unmodified), their work surviving only in `stash@{0}`.
**Coordinator reconciliation (verified, before QA):** diff proved the stash held
exactly the W2/W4 work (registry +13, trained_detector +124, observer_pilot
service/state/types, POV test +55) and that all other overlapping files were
identical between stash and working tree except PROJECT_INVENTORY (Worker 5's
newer version, correctly kept). Only the 6 W2/W4 files were restored from the
stash, then unstaged; their suites re-ran green (rfdetr + POV: 28 pass, 1 skip).
Running workers were warned never to stash again (W3 complied — used diff/show
only; W5 finished before the warning could be delivered). One W3 early config
edit also vanished mid-task and was re-applied and re-verified by that worker;
QA confirmed the config change present in the final diff by re-reading it.
**For the parent:** `stash@{0}` is still intact and was never popped/dropped
after the incident. **Do NOT drop it until after the commit below is verified
to contain all 19 files** — after that it is redundant (a snapshot of work that
is then committed) and may be dropped. Commit per-file (see §8), never a blind
whole-tree commit, because W5 also warned that parallel edits in one turn lost
writes (tool reported "updated" but re-reads showed original text; that worker
redid edits sequentially and verified each via re-read + diff — QA's re-read of
the W5 diff is the backstop for that warning).

## 7. Flaky / env instability found by QA (not Phase 33 regressions)

- `test_fight_agent.py::TestFightDetector::test_get_fight_detail` asserts team
  order `['team_001','team_002']` but gets `['team_002','team_001']` (dict/set
  iteration order). Proven flaky at HEAD: 5× in the Phase 33 tree = 3 fail / 2 pass;
  5× in a clean HEAD worktree = 3 fail / 2 pass. Identical behaviour; Phase 33
  touches nothing in fight_agent.
- A second process installed `/usr/bin/tesseract` mid-QA (absent at QA start,
  present later) — this flipped the killfeed frag test (fails without tesseract,
  passes with it) and moved skips 9→3 across runs. QA proved the frag failure
  fails identically at clean HEAD under tesseract-absent conditions, so it is
  pre-existing/env, not Phase 33. Consequence: Phase 32's exact baseline
  ("62 fails, 0 in `test_killfeed_phase23.py`") is **not reproducible on this VM**
  — the Phase 32 QA environment evidently differed. The controlled HEAD-vs-tree
  comparison, not the ledger number, is the basis for the no-regression call.
- One `/tmp` wipe mid-QA destroyed an in-progress run's log; runs were re-done
  with logs in the workspace (then deleted). QA's Run 2 log omitted FAILED names
  (a flag/output quirk); Run 3 supplies the complete FAILED list used above.
- QA artifact note: a 0-byte `backend/test.db` appeared mid-QA (conftest's
  `DATABASE_URL=sqlite+aiosqlite:///./test.db`) and was deleted by QA after the
  suites finished — **do not commit it if it reappears** (verified absent at
  ledger time).

## 8. Files for the parent's commit (19 files — verify before pushing)

Modified (16):
- `backend/app/agents/highlight_agents/clip_detector.py`
- `backend/app/config.py`
- `backend/app/db/base.py`
- `backend/app/main.py`
- `backend/app/ml/registry.py`
- `backend/app/ml/trained_detector.py`
- `backend/app/services/observer_pilot/service.py`
- `backend/app/services/observer_pilot/state.py`
- `backend/app/services/observer_pilot/types.py`
- `backend/app/vision/detectors/icon_matcher.py`
- `backend/app/vision/multi_feed_monitor.py`
- `backend/tests/unit/test_phase29_w1_pov_reader.py`
- `README.md`
- `SECURITY.md`
- `docs/PROJECT_INVENTORY.md`
- `frontend/app/watch/page.tsx`

New (3):
- `backend/tests/unit/test_ml/test_trained_detector_rfdetr.py`
- `backend/tests/unit/test_phase33_d2_clip_detector.py`
- `backend/tests/unit/test_phase33_db_boot_fail_fast.py`

Plus this ledger (`docs/PHASE33_FINAL_LEDGER.md`) — 20 files total in the commit
if the ledger is included, as with Phases 31/32. Tracked diff stat: 725 insertions,
131 deletions (excludes untracked test files). No deletions, no binaries,
no workflows in this change. HEAD before commit: `877cc7a`; tree otherwise clean
apart from the files above; stash list: `stash@{0}` intact (see §6).

## 9. Explicitly NOT in this phase (backlog, not hidden)

- `.env` untrack + git-history purge + rotation of the 5 exposed secrets —
  user decision, user hands only; team never touched them.
- 84 PNG icons + 4 workflow files — manual-upload kit already with the user;
  the push path cannot carry binaries/workflows.
- Outreach — paused; nothing sent to anyone.
- full_auto ELIMINATED banner path, zone winners/losers template,
  normalized-coordinate position source, clutch stickiness, lull retraction —
  not touched.
- POV tap-verification slot+roster-aware rework — separate future work (see §2
  coordinator decision); tap verification stays highlight-only position check.
- Multi-feed fallback gating — future work before any foreign detector is wired
  (see §5).
- RF-DETR registry flip — gated on the real `best.onnx` smoke test (see §4).
- Real evidence producer for long-range clips — follow-up (see §1).
- `_create_grenade_clip` `victims=1` default — flagged, separate function,
  future worker.
- /watch user-visible standby copy + LIVE badge JSX — parent decision (§5).
- Remaining re-inspection backlog carried forward, untouched here: POV tap-verifier
  rework (above), DB death mid-run (per-request failures, §3), stale docs beyond
  the named scope (§5), lockout dead code, `start_scrim` `LOBBY-{random}`
  invention, infra placeholders, Kaggle dataset/notebook inconsistencies.

## 10. Honest limits (standing, same as Phases 31/32)

No real phone feed, no scrim, no RTMP push, no 720p re-measurement, no real
Chromium capture, no live-browser click-through, no real Postgres, no real
`best.onnx` graph seen, no real broadcast render check — all on this VM.
pytest-cov gate not re-verified (not installed here). Production-measure items
remain for the rig/scrim track, not for code phases.
