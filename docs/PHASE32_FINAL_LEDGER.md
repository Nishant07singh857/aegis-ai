# Phase 32 Final Ledger — LIVE-PROOF FIXES

**Base:** local HEAD `d4c309f` (Phase 31), remote main `3619a47d` at task start.
**Status:** QA gate PASS-WITH-NOTES → gating defect D-1 fixed → independent RE-GATE PASS.
**Commit/push:** NONE made by this phase. Work is in the working tree for parent review;
commit + push only while the user is present in chat, per standing rule.
**Constraints honoured:** spectator-mode only, zero API spend, Broadcast Bible grammar
(no PiP/split during fights, hard cuts, holds, final-circle no switching, no mid-fight
replays), no invented numbers, no fake "done".

## 1. K-C1 — Headless capture credential (Critical, FIRST) — LIVE

**Problem (re-inspection):** feed-auth (H3) broke headless capture. Game video is a bare
`<img>` that cannot send headers, and no signed-URL/cookie/proxy mechanism existed →
`/broadcast/output` got 401 / NO FEED.

**Fix:** short-lived HMAC-signed feed token in the query string (`?feed_token=`).
Format `ft1.<b64url JSON>.<b64url HMAC-SHA256>`, payload `{scope:"video-feed", exp,
nonce}`. TTL default 600s, enforced 60–3600s. Signing key = existing SECRET_KEY
(no new secret, no DB row). Deliberately **feed-wide, not slot-scoped**: the director
switches slots mid-broadcast and the `<img>` src follows `active_camera`; a slot-scoped
token would 401 on every camera switch. Scope is still feed-only: accepted on ONE
route, resolves to a viewer principal, never an operator.

**Files:** NEW `backend/app/api/v1/video/feed_token.py` (`mint_feed_token`,
`verify_feed_token`, viewer-only principal, `get_feed_principal` dependency);
`backend/app/api/v1/video/router.py` (docstring documents the implemented mechanism,
`POST /video/feed-token` operator-only mint, feed route uses `get_feed_principal`;
full Bearer/X-API-Key auth unchanged); `frontend/components/broadcast/game-video.tsx`,
`frontend/components/DirectorOutputView.tsx`, `frontend/app/broadcast/output/page.tsx`
(propagate `?feed_token=` into the `<img>` src, stale "intentionally unauthenticated"
comment replaced); `infrastructure/docker/streamer-entrypoint.sh` (precedence
FEED_TOKEN > mint via backend, 600s TTL, pipefail-safe; **fail-fast with
instructions** when neither is set on a `/broadcast/output` URL — never a silent
NO FEED capture; overlay-only URLs skip with a note; director log line strips the
query so the token is never logged); `infrastructure/docker/docker-compose.streaming.yml`,
`docs/STREAMING.md` (§0b).

**Proof:** unauth feed 401 (H3 intact); minted token opens slots 1 AND 2; tampered /
expired tokens 401; bad TTL 422; mint unauth 401; token presented as Bearer on the
mint endpoint 401 (not a general credential). Entrypoint (stub binaries): no
credential → exit 1 fail-fast before Xvfb runs; overlay-only URL reaches Xvfb;
FEED_TOKEN set passes and the log masks the query.

**Honest limits:** no real Chromium boot on this VM (code-path + stub-argv proof only);
real capture against live backend+frontend on the production rig still required.
Token is a bearer until expiry: a leaked token views the feed for the rest of its
short TTL (mitigations: 600s default, operator-only minting, no-store, no logging —
not elimination). No revocation list; remedy is TTL expiry or SECRET_KEY rotation
(which also invalidates all JWTs — operator decision). Token appears in the streamer
container's Chromium argv (container-local `ps` visibility only). Default HTTP access
logs (uvicorn/reverse proxy) log full request paths including `?feed_token=` — deployment
should suppress query-string access logging (QA note D-4).

## 2. K-C2 — Streaming topology (Critical) — DECIDED AND IMPLEMENTED

**Decision: shell streamer is the official topology**
(`Dockerfile.streamer` + entrypoint + compose streaming overlay). The API path was
NOT given binaries in the backend image — stuffing a display stack into the
multi-worker API server is the wrong topology, so no half-done second path exists.

**Files:** `backend/app/api/v1/stream/router.py` (start/stop return **410 Gone** with
streamer instructions; status returns `state="deprecated"`, never an idle/running claim
about a streamer this process cannot see; auth still gates first — unauth → 401, not
410); `backend/app/streaming/service.py` (deprecation banner; retained only as a
library for pure command builders + unit tests; zero `get_stream_service` callers
outside `app/streaming/`); `frontend/components/stream-control.tsx` (deprecated notice
replaces start/stop controls); `docs/STREAMING.md` (§0, decision + do-not-rewire
warning).

**Proof:** stream start unauth 401, start operator 410, stop 410, status operator
`state="deprecated"`. StreamService unreachable from any route.

**Honest limits:** no real RTMP push proven (no keys/ingest from this VM); first real
RTMP push must be proven on the rig before any "streaming works" claim.
`/watch` page: `streamIsRunning` requires `state=="running"`, so with K-C2 it always
shows the honest offline standby even while the shell streamer runs (QA note D-5) —
shell-streamer liveness is unreported anywhere; needs a future out-of-band health
signal, not an API status resurrection.

## 3. WS kill-switch hole — CLOSED

**Problem:** `authenticate_websocket` had no `is_active` check, and `websocket_live`
froze the token at connect, so a deactivated JWT worked until expiry.

**Fix:** `backend/app/api/deps.py` — `authenticate_websocket` is now **async** with the
same kill-switch check as REST (`authenticate_checked` pattern): JWT principal with
DB `is_active` False or missing row → reject; DB lookup failure → fail-closed.
Operator principal (`user_id="operator"`, no DB row by design) and X-API-Key path are
exempt, exactly as on REST. `backend/app/main.py` — `websocket_live` no longer freezes
the token at connect; it stores `raw_token` and **re-authenticates on every action
frame**. Passive subscription stays public; `/ws/monitor` has no control actions, so
no change there. REST kill-switch untouched and re-proven.

**Proof:** new `backend/tests/unit/test_phase32_ws_dossier_auth.py` (5 tests, incl. a
live TestClient socket: active `start_scrim` → `success`, mid-connection deactivate →
next action `unauthorized` without reconnect; viewer/no-token → `unauthorized`;
ghost user / DB failure → None; operator / API key pass). Combined regression 25
claimed by worker, 15 in QA's natural bundles (see §9 note on unreproduced counts).

**Honest limits:** per-action revalidation costs one DB query per action (same as REST
per-request; control actions are rare). Frontend PDF download browser click-through
not verified here (tsc only).

## 4. Dossier policy — AUTH APPLIED (parent decision)

**Decision:** dossier GETs are NO LONGER public. Team-intel PDFs are not public.

**Fix:** `backend/app/dossiers/router.py` — all 4 GETs (`/dossiers/fights`,
`/dossiers/dossier`, match PDF, team PDF) carry `dependencies=[Depends(require_operator)]`,
exactly the POST ingest gate (unauth 401, viewer 403, operator/director/spotter/admin
200). Router docstring and `backend/app/dossiers/__init__.py` stale "reads are public /
read-only" intent updated; no other "dossier public" claim remains in the repo (only a
`pdf.py` "public generators" comment meaning exported functions, unrelated to auth).
Frontend: JSON endpoints already sent tokens and showed honest 401/403 errors (no
change needed); the two PDF links (plain `<a href>`, cannot send headers) were switched
to authenticated blob downloads with "sign in again as operator or admin" error lines
(`frontend/app/analyst/team/[name]/page.tsx`, `frontend/app/analyst/match/[id]/page.tsx`;
both pages already `RequireRole ["operator","admin"]` gated, so no gate mismatch).

**Consequence (by design, not a bug):** authenticated viewers can no longer read
dossiers (previously public). Restoring viewer read would need a policy change +
`get_current_user` gate — a small code change, but a policy decision first.

**Proof:** dossier GETs unauth `[401,401,401,401]`; admin API key `[200,200,200,200]`;
viewer JWT `[403,403,403,403]`; deactivated operator GET → 401. Frontend `tsc` clean.

## 5. Broken test files — REWRITTEN, NOT DELETED (K-H8 collection block gone)

**`test_tournament_service.py` → rewritten** against real
`app.api.v1.tournament.service.TournamentService` (constructor `(db=None, repo=None)`,
plain-string statuses, no enum), using the proven `test_master_data.py` fake-repository
pattern (no Postgres/Redis/network). 19 tests: create + auto-slug, explicit slug,
duplicate slug → 409, no-DB write → 503, get found/missing (`None`), list + pagination,
no-DB reads honest-empty, get-cache, update/patch (+missing → `None`), activate
single-active invariant (+missing → `None`), soft-delete (+missing → `False`).
Deliberately NOT recreated: `register_team`, `unregister_team`, standings/stats getters,
`start/complete/cancel_tournament`, hard-delete — those methods do not exist on the
real service. Two wrong old expectations corrected (missing → `None`, not raise;
delete is always soft-delete).

**`test_time_sync.py` → rewritten, without claiming an NTP feature.**
Repo-wide grep: zero production hits for `app.core.time_sync` / `TimeSyncManager` /
NTP — that module/feature never existed. The real successor is
`app.vision.capture.latency_sync.LatencySyncTracker` (Phase 22/W3: multi-feed grab-time
offsets vs fleet median, ±500ms tolerance, drift slope; its docstring states it does
NOT re-time frames). The file now tests that, with the full history in its header.
Dropped without fake mapping: NTP calibration, device-clock correction, RTT
compensation, corrected-timestamp ordering, sync loop. 19 deterministic tests
(explicit `capture_ts`, no wall-clock/network): empty report, tolerance 500.0, unknown
feed → `None`, auto-register, lone-feed offset 0, symmetric ±200ms, boundary 500ms →
within / 600ms → out-of-tolerance, alignment summary, custom tolerance 100ms, offset
basis, unregister, sample count, reset, drift `None` (<3 samples / zero span), steady
drift 0.0, lagging drift > 0.

**Proof:** baseline both files → collection error, 0 collected. After: collect exit 0,
38 tests (19+19), 38 passed. Full-suite collect exit 0, 0 errors (see §8 for totals).

**Ledger note:** NTP-style device-clock correction was never built; only grab-time
measurement (`LatencySyncTracker`) exists — correction is still a gap.

## 6. Weapon margin gate + abstention — LIVE (owner decision: abstention ON)

**Fix:** gate at the single choke point — `IconMatcher.match_candidates` via pure
`gated_weapon()`, `MARGIN_THRESHOLD = 0.15` (boundary inclusive). Margin < 0.15 →
weapon UNKNOWN/abstain (never a wrong name); ≥ 0.15 → confident label.
`KillFeedEntry.weapon_margin / weapon_abstained` carried through killfeed
(`_finish_layout_entry`, `_entry_to_event`: abstained → bus payload `"Unknown"`) and
multi-feed monitor fallback passthrough. Gate drops the label only — the entry
(names/knock) still ships. Also fixed a real bug found on the way: every candidate
previously shared `ranked[1]` as its second score; now the top candidate's runner-up
is correct, so a runner-up can never be promoted (its margin is negative).

**Bypass audit (QA-verified):** killfeed label path has no live bypass
(`IconMatch.weapon` gated → `KillFeedEntry.weapon` → `NormalizedEvent.weapon` → bus
`"Unknown"` when abstained; `check_weapon_highlight` refuses `None`/`"Unknown"`).
`KnockDetector`/`WeaponDetector` (OCR-text weapon path) are never instantiated in
production — dead code, not a bypass. `fight_detector`'s `kill_confirmed` handler has
no production publisher. `engagement_analyzer` weapon stats only consume
caller-supplied events, not killfeed icons. `weapon_detector.py` (HUD OCR) is not wired
to the live event path — if ever wired, the gate does not automatically apply to it
(noted, not silently assumed).

**Tests:** new `backend/tests/unit/test_phase32_margin_gate_knock_hint.py` 25/25
(0.14 → UNKNOWN, 0.15 boundary → confident, 0.0/unknown → UNKNOWN, confusable-table
probe **zero wrong confident labels**, stubbed-slide matcher probes, killfeed wiring +
payload, knock hint cases). `test_killfeed_phase23.py` 29/29 after 3 gate-changed
expectations (QA re-measured the template margins itself — ar_a 0.095, ar_b 0.060,
sniper_bolt 0.113 — and confirmed these are legitimate gate consequences, not weakened
asserts: identification still correct, runner-up within 0.15, new asserts explicitly
check `weapon is None` + `weapon_abstained is True`).

**Coverage-vs-precision trade-off (honest):** even exact template pastes abstain at
family level (ar_a 0.095, ar_b 0.060, sniper_bolt 0.113) because the runner-up
cross-matches too closely — consistent with the diagnostic (24/357 ≈ 6.7% coverage
at 79% precision, vs ungated ~48.2%). Coverage drop is expected; the gate was NOT
loosened to buy coverage. Class-level margin (ar_a vs ar_b both "ar") is a future
owner decision, not a silent change. 360p physics floor unchanged; 720p re-measure
still pending; no real-frame measurement in this phase (probes synthetic/stubbed).

## 7. Knock hint-labelling — LIVE

**Fix:** `backend/app/services/live_stats.py` — `PlayerAccum.knocks_verified`,
`_knock_trusted()` (mirror of the headshot C2 gate: `knock_confirmed=True` or source
spotter/manual/observer), `_on_knock` verified/unverified split +
`stats.knocks_unverified` counter, `player_snapshot` gains `knocks` (total) +
`knocks_verified` + `knocks_unverified` + `knocks_hint` + `knocks_status`
(`hint`/`verified`/`none`), `live_standings` gains match-level
`knocks_hint / knocks_verified / knocks_unverified` alongside the provisional flag.
Points remain finishes-only.

**Verified:** no `knock_confirmed` producer exists in production code (only the gate
references it), so on a pure-vision stream 100% of knocks are unverified today; the
verified branch is forward-compatible, not merged. Flush rows contain no `knocks`
field; MVP `combat_score`/`overall_score` never include knocks (carried in the result
object only, unscored); knock-only team scores 0 points. No shared schema change
(trust derives from existing bus `source`/`knock_confirmed` fields).

**Proof:** knock tests in the new suite (uncorroborated → hint, trusted → verified,
spotter-source → verified, mixed → hint, standings hint + knock-only team 0 points,
flush knock-free + kills=0).

## 8. QA gate — PASS-WITH-NOTES → D-1 fixed → RE-GATE PASS

**Independent QA (read-only, /tmp probes only):** 41/41 probes pass on real code paths
(feed token, stream 410/401/deprecated, margin boundary cases, knock splits, WS live
socket with mid-connection deactivation, dossier 401/403/200, entrypoint stub tests).
Frontend: `npx tsc --noEmit` exit 0; `npm run build` exit 0 (full route table incl.
`/analyst/match/[id]`, `/analyst/team/[name]`, `/broadcast/output`, `/watch`).
Security: no new secret/token/key strings in the diff; no paid API; no
PiP/split-during-fight in new code; no new unauthenticated route (feed-token path is
fail-closed, 401 without any credential, route-scoped).

**QA full suite (quiet tree):** 1991 passed / 69 failed / 3 skipped, collect 2062,
0 collection errors. Controlled HEAD-worktree baseline: 1908/67/9. Exact failure diff:
only 7 NEW failures, all in `test_phase23_w2.py` (stale ungated asserts) — defect
**D-1, gating, test currency only (not a product defect)**.

**D-1 fix (test-only, no production change):** updated ONLY
`backend/tests/unit/test_phase23_w2.py` (58 insertions / 15 deletions, 9 hunks, 7
test functions, asserts + comments only). Before: 7 failed / 16 passed, every failure
verified from output to be a gate consequence (correct family, label withheld). After:
23 passed in that file, 29 killfeed_phase23, 25 margin-gate suite (combined 54;
all three 77/77). Deliberate nuance, measured: in
`test_template_vs_itself_high_confidence`, frag_grenade (margin 0.164) genuinely
clears the gate and kept its old labelling asserts, while ar_a (0.107) and
sniper_bolt (0.092) converted to abstention; the strip-pipeline frag measurement
(0.138, abstains) is a different input (entry strip vs bar canvas) and both behaviours
are correct for their inputs.

**RE-GATE (independent, read-only):** **PASS.** Diff audit by mtime confirms the fix
file is the only post-QA change (12:13:43 vs 11:33–11:37 for all other Phase 32 files);
production `icon_matcher.py` still `MARGIN_THRESHOLD = 0.15`. Targeted re-runs confirmed
exactly (23 / 29 / 25). Frag split-out re-measured independently (ar_a 0.1065 abstained,
frag_grenade 0.1642 labelled, sniper_bolt 0.0915 abstained) — legitimate, not a
weakened assert. **Full suite after fix: 1998 passed / 62 failed / 3 skipped, collect
2062** — exactly +7 / −7 vs QA, collect unchanged, 0 failures in `test_phase23_w2.py`,
any `test_phase32_*` file, or `test_killfeed_phase23.py`. Remaining 62: e2e 13, OBS
integration 19, dossier_pdf 5, commentary 7, observer 6, team 3, coordinate_mapper 3,
minimap 2, player_tracker 2, event_bus (redis) 1, fight_agent 1 — all pre-existing/env.
One counting quirk in both runs: outcome sum 2063 vs 2062 collected (unexplained,
identical before/after, not a new failure).

**Not re-verified (same as Phase 31):** pytest-cov gate (pytest-cov not installed here;
all runs used `-o addopts=""`); no real RTMP push; no real Chromium boot; no
live-browser click-through (subagent limitation, no website visit claimed).

**Worker-count corrections (QA, not rounded off):** Worker 1 "56 combined" reproduced
exactly (11 + 35 + 10). Worker 2 "25 combined" NOT reproduced as stated (atomic 5
verified; natural bundles give 15; `test_dossier_pdf` cannot even collect here — fpdf
not installed). Worker 4 "140 targeted" NOT reproduced as stated (bundle composition
unspecified; closest verifiable set 109, all passing). Worker 4 "25 new" reproduced
exactly. Worker 3 "38 both files" reproduced exactly; its "2021 collect" NOT reproduced
(quiet-tree collect is 2062, 0 errors — 2021 matches neither `tests/` nor `tests/unit`
scope). Worker 3's 1947/72 full run ran during concurrent sibling edits and was
correctly disregarded.

## 9. Files changed (working tree, uncommitted)

Modified (24): `backend/app/api/deps.py`, `backend/app/api/v1/stream/router.py`,
`backend/app/api/v1/video/router.py`, `backend/app/dossiers/__init__.py`,
`backend/app/dossiers/router.py`, `backend/app/main.py`,
`backend/app/services/live_stats.py`, `backend/app/streaming/service.py`,
`backend/app/vision/detectors/icon_matcher.py`,
`backend/app/vision/detectors/killfeed.py`,
`backend/app/vision/multi_feed_monitor.py`,
`backend/tests/unit/test_phase23_w2.py`,
`backend/tests/unit/test_vision/test_agents/test_services/test_tournament_service.py`,
`backend/tests/unit/test_vision/test_agents/test_utils/test_time_sync.py`,
`backend/tests/unit/test_vision/test_killfeed_phase23.py`, `docs/STREAMING.md`,
`frontend/app/analyst/match/[id]/page.tsx`, `frontend/app/analyst/team/[name]/page.tsx`,
`frontend/app/broadcast/output/page.tsx`, `frontend/components/DirectorOutputView.tsx`,
`frontend/components/broadcast/game-video.tsx`, `frontend/components/stream-control.tsx`,
`infrastructure/docker/docker-compose.streaming.yml`,
`infrastructure/docker/streamer-entrypoint.sh`.
New (5, incl. this ledger): `backend/app/api/v1/video/feed_token.py`,
`backend/tests/unit/test_phase32_feed_token_stream.py`,
`backend/tests/unit/test_phase32_margin_gate_knock_hint.py`,
`backend/tests/unit/test_phase32_ws_dossier_auth.py`, `docs/PHASE32_FINAL_LEDGER.md`.
Diff stat (tracked files): 24 files, 1250 insertions, 876 deletions (excludes untracked).
Stray untracked 0-byte `backend/test.db` (appeared during Phase 32 sessions, no test
owns it) was deleted by the coordinator before this ledger was finalised — it must
not be committed. Stash list empty. HEAD still `d4c309f`.

## 10. Backlog — NOT in this phase, NOT hidden

- **D-2 (Medium, pre-existing):** `backend/app/agents/highlight_agents/clip_detector.py`
  `_create_long_range_clip` defaults `weapon="Sniper"`, `distance=200`. QA verdict:
  spirit violation only, NOT a real bypass of the killfeed label path (never reads
  `IconMatch`/`KillFeedEntry`), but live-reachable via commentary and ships an
  unevidenced gun name + distance (`"200m Sniper shot!"` with zero evidence).
  Follow-up: require weapon/distance evidence or render gun-less.
- **D-3 (Low):** multi-feed monitor fallback comment overclaims gating for foreign
  detectors — no live bypass today (no foreign detector in production); gate the
  fallback or correct the comment before any foreign detector is wired.
- **D-5 (Low):** `/watch` liveness (see §2) — future out-of-band health signal.
- **D-6 (Low):** `IconMatcher.match()` docstring still describes pre-gate behaviour;
  code is correct via `match_candidates`.
- **Re-inspection items NOT in Phase 32:** POV identity single-signal (K-H5), DB boot
  failure swallow (K-H6), RF-DETR decode gap + `best.onnx` absent (K-H7), tracked
  `.env` files + committed dev-phrase SECRET_KEY + history purge (K-H9, user decision),
  Kaggle dataset/notebook inconsistencies (K-H11), stale docs beyond STREAMING.md
  (README, PROJECT_INVENTORY, SECURITY.md), account lockout dead code, `start_scrim`
  WS `LOBBY-{random}` invention, infra placeholders (nginx/terraform/ansible).
- **Production-measure NOT done (VM limits):** no real phone feed, no scrim, no RTMP,
  no 720p re-measure, no real Chromium capture, no live-browser click-through.
  Dwell/centre-band heuristics from Phase 31 remain uncalibrated.
- **Known flaky (pre-existing, unrelated):** `test_frag_grenade_knock_entry` failed once
  on clean HEAD, then passed repeatedly; `test_phase23_w2.py` outcome-sum quirk (§8).
