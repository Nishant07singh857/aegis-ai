# AEGIS AI — Project Inventory (code-verified)

**Date:** 2026-10-20 · **Method:** every number below was counted from the code itself
(grep / find / pytest --collect-only), not copied from phase reports.
Where a count could not be verified exactly, that is stated.
**Headline recount (Phase 33 low batch, 2026-10-26, HEAD `877cc7a`):**
the headline table below was re-measured at that HEAD; per-module
detail in §1/§5/§7/§9 is the 2026-10-20 snapshot unless a line below
was individually corrected and says so.

## Headline counts

| Metric | Count | How verified |
|---|---|---|
| Backend Python files (`backend/app/`) | 459 | `find` |
| Backend LOC (`backend/app/**/*.py`) | ~126,322 | `wc -l` |
| Frontend TS/TSX LOC (app+components+lib) | ~31,430 | `wc -l` |
| API endpoints | **211** (209 HTTP + 2 WebSocket) | 212 decorator matches; 1 is a false positive inside `db/base.py` text |
| Agent classes (BaseAgent subclasses) | **41** in 9 families | grep `class …(BaseAgent)` per family dir |
| DB tables | **33** (30 explicit `__tablename__` + User/ApiKey/AuditLog auto-named) | grep across `backend/app` |
| Alembic migrations | **13 files** (001, 005–016) | `ls backend/alembic/versions/` |
| Vision detectors on disk | 16 modules; **10 wired live** | `vision/runtime.py` imports + docstring |
| Frontend page routes | **34** | `find app -name page.tsx` |
| Frontend component files | 55 | `find components -name '*.tsx'` |
| Graphics templates | **17 templates + 7 animated story graphics** | `components/graphics/**` |
| Backend tests | **2062 collected** (pytest --collect-only; 0 collection errors) | pytest + grep |
| Docs | 44 top-level `docs/*.md` | `ls docs/` |
| Piper voices installed | 2 (Hindi `hi_IN-pratham-medium`, English `en_US-amy-medium`) | `.local-voices/` |
| Commentary modes | 5 | `audio/commentary_modes.py` |
| Feed event types (NormalizedEvent) | 15 | `feed_adapter/schemas.py` |
| Anti-cheat checks registered | 14 (**3 live**, 11 disabled-with-reason) | `vision_intelligence/anti_cheat.py` |
| Training notebooks | 5 | `ls notebooks/` |

---

## 1. API surface — 169 endpoints by module (2026-10-20 snapshot; the
headline total is now 211 — per-module counts below were NOT re-measured
in the Phase 33 recount and no longer sum to the headline)

Route decorators counted per file (`@router.*` + `@app.*`):

| Module | Endpoints | Notes |
|---|---|---|
| `main.py` (app-level) | 28 | incl. **2 WebSockets** (live feed + director/state fan-out), player-stats, matches, standings reads |
| `api/v1/audio` | 20 | commentary synth, modes, mixer, voices, subtitles |
| `api/v1/replay` | 11 | marks, clips, play, highlight render/download |
| `api/v1/director` | 10 | mode set, camera, scene, overlays, suggestions approve/reject |
| `api/v1/tournament` | 8 | tournament CRUD + active-tournament invariant |
| `api/v1/video` | 7 | MJPEG game-feed layers, honest NO FEED / SIMULATED states |
| `api/v1/stories` | 7 | story generate/trigger, settings |
| `api/v1/rundown` | 7 | producer run-of-show |
| `api/v1/players` | 7 | player CRUD |
| `api/v1/match_point` | 7 | match-point / Smash status |
| `api/v1/users` | 6 | user CRUD, bulk-observers, tournament kill-switch |
| `api/v1/teams` | 6 | team CRUD |
| `analysis` (router) | 6 | team analysis, dossiers data |
| `dossiers` (router) | 5 | PDF dossier generation/download |
| `api/v1/feed` | 5 | feed ingest, vision start/stop |
| `api/v1/zone` | 4 | zone state/timer |
| `api/v1/preshow` | 4 | pre-show flow control |
| `api/v1/observers` | 4 | observer-slot ↔ team mapping |
| `api/v1/ceremony` | 4 | WWCD ceremony control |
| `api/v1/anti_cheat` | 4 | flags list, review confirm/dismiss |
| `api/v1/stream` | 3 | stream start/stop/status — Phase 32 K-C2: start/stop now 410 Gone, status reports `"deprecated"`; the shell streamer is the official path (see `docs/STREAMING.md` §0) |
| `api/v1/pip` | 2 | map-cam / PiP layout |
| `api/v1/predictions` | 1 | `GET /predictions/win-probability` |
| `api/v1/cluster` | 1 | `GET /cluster/nodes` |
| `api/v1/vision_intelligence` | 1 | subscriber status |
| `api/v1/analytics_extra` | 1 | zone-efficiency etc. |

Example routes: `POST /api/v1/feed/ingest`, `GET /api/v1/matches/{id}/player-stats`,
`POST /api/v1/users/bulk-observers`, `GET /api/v1/predictions/win-probability`,
`POST /api/v1/feed/vision/start`.

## 2. Agents — 41 BaseAgent subclasses, 9 families

| Family | n | Classes |
|---|---|---|
| Observer | 5 | ObserverDirector, POVSelector, SafetyController, CinematicController, PlaybookManager |
| Fight | 4 | FightDetector, ClutchDetector, EngagementAnalyzer, IntensityScorer |
| Commentary | 6 | ScriptGenerator, EmotionEngine, HypeDetector, CasterMemory, LanguageManager, VoiceSynthesizer |
| Highlight | 4 | ClipDetector, MontageGenerator, ReplayDirector, ContentPublisher |
| Prediction | 4 | WinnerPredictor, SurvivalPredictor, ZonePredictor, FightPredictor |
| Analytics | 5 | MVPCalculator, PlayerProfiler, ScoutingReporter, PerformanceTracker, TeamChemistryAnalyzer |
| Team | 5 | TeamTracker, RotationAnalyzer, PositioningAnalyzer, UtilityTracker, TeamMemory |
| Narrative | 5 | StorytellerAgent, StorylineBuilder, RivalryTracker, ComebackDetector, DocumentaryGenerator |
| Referee | 3 | CheatingDetector, DisputeResolver, RuleEnforcer |

Plus: `AgentOrchestrator`, `BaseAgent`, and the Day-Reel builder
(`agents/highlight/day_reel.py` — DayReel/DayReelSegment/DayReelMoment).
Total classes of all kinds under `agents/` = 227 (includes dataclasses/enums/configs).
ML support modules (`backend/app/ml/`): ModelRegistry + fallback chain, datasets
(recorder, auto-labeler), training pipelines (killfeed_detection, fight_detection),
player_profiling (Elo-style skill rating, k-means clustering, playstyle classifier),
prediction models, `vision_wiring.py` (trained-model → detector bridge).

## 3. Services (`backend/app/services/` + platform modules)

- **director_service** — 3-mode broadcast director (state, scenes, suggestions, holds)
- **match_service** — matches, results, per-player stats recording, canonical MVP persistence
- **feed_adapter** — NormalizedEvent contract, ingest service, simulator, vision adapter
- **replay/** — event buffer, highlights detector, renderer (video clips), replay service (event replay)
- **vision_intelligence/** — bus subscriber (attribution, fight feeding), anti-cheat, story auto-flagger, zone state
- **match_point/** — match-point engine + Smash Rule (5-beat progression + verified fallback)
- **zone/** — zone phase/timer service
- **preshow/**, **ceremony/** — pre-show flow, WWCD ceremony state
- **rundown/** — producer run-of-show
- **pip/** — map-cam / PiP layout state
- **observer_mapping/** — observer slot ↔ team assignment
- **video_feed_service** — MJPEG layers for output page (honest feed states)
- **streaming/** (`app/streaming`) — Xvfb+Chromium+FFmpeg pipeline, encoder, destinations (YouTube/Twitch/Facebook/custom RTMP). Phase 32 K-C2 correction: the API-driven path is deprecated (410 Gone); the module is retained only as a library for pure command builders + unit tests, and the shell streamer is the official topology (see `docs/STREAMING.md` §0).
- **broadcast/** — OBS client, scene/source managers, transition controller, recording manager, audio mixer
- **production/** — fail-safe controller, emergency scenes, OBS health monitor, production guard/metrics
- **cluster/** — Redis node registry (advisory/monitoring)
- **storage/** — storage interface (local default; S3/R2 plug-ready)

## 4. Vision pipeline

Capture: ADB screencap loop, scrcpy capture, PC (mss) capture, multi-device manager
(16 observer devices), frame grabber, feed-health monitor, latency-sync stamps,
resolution scaler.

Detectors on disk (16 modules in `vision/detectors/`). **Wired live in `runtime.py` (10):**
KillFeedDetector (OCR), HUD readers ×3 (AliveCount, ZoneTimer, TeamStatus), HPBarDetector,
UtilityDetector (smoke/grenade), MinimapReader, ZoneDetector (minimap geometry),
HealingDetector, LootDetector.
**NOT wired (deliberate, documented in runtime.py):** VehicleDetector, WeaponDetector,
KnockDetector (all need PaddleOCR/ultralytics, not installed — wiring them would emit
untruthful data), PlayerTracker (needs trained model), legacy AliveCounter + old
kill_feed_ocr module (superseded by the HUD reader / KillFeedDetector).
Inference: model loader, GPU manager, batch processor, worker; trained-model path via
`VISION_MODEL_PATH` (RF-DETR/YOLO ONNX — model itself still in Kaggle training).

## 5. Database — 33 tables, 13 migrations

Tables: teams, players, tournaments, user (auto-named), api_key, audit_log, matches,
match_events, kill_events, fight_events, match_player_stats, match_team_stats,
predictions, highlights, commentary, broadcast_sessions, event_store, observer_slots,
stories, story_settings, team_intelligence, rotation_events, drop_events, zone_phases,
utility_events, anti_cheat_flags, director_decisions, director_decision_labels,
director_learning_state, director_learning_adjustments, director_suggestion_fates,
director_shadow_divergences, pov_pilot_decisions.

| Migration | Added |
|---|---|
| 001 | Initial schema — 17 tables |
| 005 | User role column (RBAC) |
| 006 | drop_events, zone_phases (analysis tracking) |
| 007 | event_store + indexes on event tables |
| 008 | observer_slots (observer↔team mapping) |
| 009 | Master-data fields (teams region/active, players, tournament config) |
| 010 | User tournament/team scoping |
| 011 | stories, story_settings (AI Storyteller) |
| 012 | Player utility stats — healing, grenades_used |
| 013 | MVP unification — mvp_score/is_mvp, damage_dealt nullable (honesty) |
| 014 | anti_cheat_flags (review workflow) |
| 015 | Director learning loop — decision journal, suggestion fates, shadow divergences |
| 016 | Observer auto-pilot — POV pilot decisions |

(Numbers 002–004 do not exist as separate files; the chain is linear, single head 016.)

## 6. Audio / commentary

- 5 modes (`audio/commentary_modes.py`): Hindi Main, English Main, Bilingual Blend,
  Silent (text-only), Human Assist (AI suggests, human speaks) — real behaviour
  differences (pacing, cooldowns, content mix), director-selectable, per-match persisted.
- Piper TTS offline: Hindi voice + English voice installed; language-routed synthesis.
- Dual-track: per-language FIFOs → program MKV with 2 AAC tracks (VOD deliverable;
  live RTMP carries 1 track — platform limit, documented).
- Pieces: mixer, commentary_feed, game_audio route, subtitles (WebVTT + CC toggle),
  profanity filter (EN/Hindi/Hinglish), fallback pack (40 pre-rendered WAVs).
- Warm-synth latency ~519 ms median (voice cached + boot warm-up); end-to-end 1.3–5 s.

## 7. Frontend — 34 page routes

- **Public/viewer:** `/` (home), `/login`, `/watch`, `/live`, `/teams`
- **Broadcast:** `/broadcast` (hub), `/broadcast/director`, `/broadcast/output`,
  `/broadcast/output-pip`, `/broadcast/graphics`, `/broadcast/highlights`,
  `/broadcast/day-reel`, `/broadcast/preshow`, `/broadcast/ceremony`
- **Overlays (OBS sources):** `/overlays/scorebug`, `/overlays/killfeed`,
  `/overlays/standings`, `/overlays/lower-third`
- **Control room:** `/control`, `/control/rundown`, `/control/observers`,
  `/control/match-strip`, `/control/anti-cheat`
- **Role pages:** `/spotter` (mobile), `/observer`, `/commentary`
- **Admin:** `/admin`, `/admin/teams`, `/admin/players`, `/admin/tournament`, `/admin/users`
- **Analyst:** `/analyst`, `/analyst/match/[id]`, `/analyst/team/[name]`

Components (55 files, Phase 33 recount; subgroup counts below are the 2026-10-20 snapshot): broadcast ×4 (game-video, game-overlays, map-cam-view,
top-center-stats), live ×7 (DirectorPanel, KillFeed, StandingsTable, MatchHeader,
AgentChatter, SystemHealth, ConnectionBanner), graphics-staging ×10 (map-cam, PiP
layout, player/team panels, preshow view+controls, WWCD ceremony, zone timer,
matchpoint badge), graphics ×26, plus lib ×14 modules (api, auth, director, graphics,
stories, socket/commentary/stats/zone hooks).

## 8. Graphics library — 26 files

**Templates (17):** standings ×7 (table, cards, MVP board, Eliminator race, Best IGL,
Finals MVP, team spotlight — with ▲▼ movement), scorebug ×3 (classic, modern, minimal),
lower-third ×3 (banner, rise, slide) + standby + player board, killfeed ×2 (stack, ticker).
**Animated story graphics (9 files):** WinProbBar, H2HCards, MilestoneAlert, FormGuide,
ZoneAdvantage, WWCD cards, StoryGraphic shell, TemplatePicker, animation-style system
(slide/fade/scale, 2–3 templates per story type).

## 9. Tests & docs & infra

- **Tests:** Phase 33 recount at HEAD `877cc7a`: `pytest --collect-only` = **2062 tests collected, 0 collection errors** (run with `-o addopts=""` because the system Python lacks pytest-cov, which `tests/pytest.ini` requests). The 2026-10-20 snapshot below it (947 collected, 12 errors) is superseded.
- **Docs (44 top-level `docs/*.md`, Phase 33 recount):** VISUAL_BROADCAST_BIBLE, PRD_GAP_ANALYSIS, PHASE22_FINAL_LEDGER,
  BETTER_THAN_KRAFTON, BGMI_TERMS_AZ, BGMI_EXPERT_PROMPTS, AI_STORYTELLER,
  ANIMATED_GRAPHICS, GRAPHICS, DIRECTOR, ROLES, CLUSTER, STREAMING, AUDIO, VISION,
  VISION_MODEL, GAME_FEED, OBSERVER_SETUP, ANALYSIS_PRODUCT, KAGGLE_TRAINING, TRAINING,
  TRAINING_BEST_PRACTICES, DATA_COLLECTION, METRIC_CADENCE, VISUAL_BIBLE_CHANGELOG,
  gap_audit_w1–w4 + `docs/DEEP_ANALYSIS/` (11 files: master patterns, gap audit, Bible v2,
  7 per-stream reports) + `docs/architecture/` (9 numbered docs + subfolders).
- **Infra:** 5 Dockerfiles (backend, backend-dev, frontend, frontend-dev, streamer);
  5 compose files (dev, prod, staging, streaming, cluster — prod = postgres + redis +
  backend + frontend); 4 CI workflows exist **locally only** (.github/workflows:
  ci-backend, ci-frontend, docker-build, migrate-check — GitHub App cannot write that
  path; still awaiting user PAT/manual upload). MIT LICENSE, SECURITY.md present.
- **Notebooks (5):** RF-DETR-nano (primary), YOLO26-nano (backup), parallel "best"
  variants ×2, Qwen3-VL director — + dataset upload script.

---

## 10. Feature ledger (feature → sub-features built)

### Auth & Users — BUILT
JWT login + API keys; 6 roles (admin/operator/director/spotter/observer/viewer);
role-home redirects; tournament+team scoping (observers 403 on other teams);
bulk creation of 16 observers with printable credential sheet; tournament kill-switch;
bcrypt password hashing.

### Tournament Master Data — BUILT
Teams CRUD (region, active flag), players CRUD (role: IGL/assault/support/sniper),
tournament CRUD with exactly-one-active invariant; scoring reads the active
tournament's config; DEMO_MODE gate (default OFF) — demo fallback data killed.

### Vision — BUILT (detectors) / PLUG-READY (trained model)
ADB/scrcpy/PC capture, 16-device manager, 10 detectors live (see §4), per-detector
frame-skip intervals, feed-health scores, latency-sync stamps; trained ONNX model
slot ready (Kaggle training pending — user-run).

### Fight Detection — BUILT
FightDetector with HP-velocity window (2 s sliding, ≥20 HP drop), 50 m proximity
(calibrated scale only), kill/knock clustering, squad-size delta, down-count;
intensity scoring, clutch detection (verified against real alive counts),
engagement analysis.

### Director — BUILT (see USER-ASKED §2 for the wiring nuance)
3 modes (full_auto / semi_auto / manual); 5 scenes; suggestion queue
(propose → approve/reject); map-cam 30 s max hold enforced by a 5 s ticker;
45 s fight hold + 10 s minimum scene time in the DirectorAI engine; graphics
template + animation-style control; mobile-responsive console.

### Overlays / Graphics — BUILT
4 OBS overlay routes; 17 templates + animated story graphics (§8); Visual Bible
layout geometry at 1920×1080 (killfeed top-left, standings right rail, facecam
bottom-left, player panel bottom-centre); hard cuts only; no PiP during fights.

### Standings suite — BUILT
Per-match + overall tables after every match; BMSD 2026 scoring (chicken = 10,
1 finish = 1 pt, per-tournament configurable); ▲▼ movement; 4 leaderboards
(MVP, Eliminator, Best IGL, Finals MVP) + team spotlight stats.

### Commentary / Audio — BUILT
5 modes, dual-language Piper voices, dual-track VOD mux, subtitles, profanity
filter, fallback WAV pack, audio mixer card, game-audio route (plug: physical
audio path is venue-side).

### Storytelling — BUILT
7 generators (head-to-head, win probability, player comparison, team form, zone
advantage, milestone, comeback); animated cricket-style graphics; director
permission gate (operator preview-only, server-side 403); auto-flagging
(full_auto fires; semi/manual suggest-only; debounce 20 s/120 s/300 s);
missing data → "—", never invented.

### Predictions — BUILT
Live WWCD% cards (top-5, 30 s poll, honest null → "—"); win-probability endpoint;
Smash Rule engine (config target, 5-beat progression, M18-verified fallback:
no conversion → most total points wins); match-point indicator; zone-advantage.

### Highlights / Replays — BUILT (event replay) / renderer BUILT, source-dependent
Event replay (marks, clips, WS re-broadcast — data replay, not video); highlight
detection (multi-kill, squad wipe, verified clutch, final-kill); day reel
(24 segments / 5 min cap); video renderer cuts 16:9 MP4 + 9:16 MP4 + 3 s GIF from
recordings when a source recording exists, else honest `no_source`.

### Streaming — BUILT via shell streamer (never live-tested); API path DEPRECATED
Phase 32 K-C2 correction: the official topology is the shell streamer
(Director output page → Xvfb + headless Chromium → FFmpeg x264 → tee →
multi-RTMP, YouTube/Twitch/Facebook/custom; see `docs/STREAMING.md` §0).
The older API-driven path (`POST /api/v1/stream/start`, `app.streaming`
spawning the display stack from the API process) is deprecated and
disabled — start/stop return 410 Gone, status reports `"deprecated"`.
No real RTMP push has ever been demonstrated from the dev VM, so
"streaming works" is still unproven until the first real push on the
production rig.

### Team Analysis — BUILT
Rotation/drop/zone tracking, fight log, team dossiers, analyst dashboard,
PDF reports (fpdf2), zone-efficiency metric, aggression score (unified),
heatmap/comparative/trend generators.

### Anti-Cheat — PARTIAL BY DESIGN
14 registered checks: 3 LIVE (impossible position jump, kill-feed rate anomaly,
alive-count inconsistency) + review UI (`/control/anti-cheat`, admin/director
gated, flags never auto-ban); 11 disabled with documented reasons (need telemetry
a spectator feed cannot produce).

### Cluster — BUILT (advisory)
Redis node registry, heartbeat, `GET /cluster/nodes`, role-based node docs
(Master/Agents/Vision/Stream/Edge). Monitoring/advisory only — no auto-scheduling.

### Spotter — BUILT
Mobile-first manual logging (`/spotter`, 360 px, large touch targets):
kill/knock toggle, team/player pickers, submits into the same feed pipeline.

### Feed Adapter — BUILT
NormalizedEvent contract (15 event types), `POST /feed/ingest`, vision adapter,
match simulator for demos/tests, per-source killfeed region config requirement
documented (real BGMI killfeed is top-LEFT, "killer [weapon icon] victim" format).

---

## 11. USER-ASKED ITEMS — honest verdicts

### 1. Damage — ⚠️ PARTIAL: HP-drop tracking BUILT & WIRED; per-player `damage_dealt` persistence NOT wired to a live producer
- **Real HP-drop detection exists and is live:** `FightDetector` uses an HP-velocity
  window (sliding ~2 s, minimum 20 HP drop) as a fight signal
  (`fight_agents/fight_detector.py`). The vision-intelligence subscriber consumes
  `hp_update` bus events and calls `fight_detector.process_hp_change(...)` with
  old/new HP, and `team_tracker.update_player_hp(...)` — TeamTracker's
  damage_taken derives from those drops (`services/vision_intelligence/service.py`).
- **The DB/API side is built honestly:** `MatchPlayerStats.damage_dealt` is nullable
  (migration 013); `GET /api/v1/matches/{id}/player-stats` serialises the persisted
  value directly — `null` ("—") when no real damage was recorded, never `kills×120`
  (that fabrication was removed in Phase 21).
- **The gap, CORRECTED Phase 33 (was: "no live caller"):** `MatchService.record_player_stats(...)`
  now HAS a live caller — `LiveStatsService` (`backend/app/services/live_stats.py:351`,
  Phase 24 W6) subscribes to the event bus itself and flushes accumulated
  per-player stats through it. The remaining honest limit is narrower:
  `damage_dealt` is still NEVER written from a spectator feed (HP drops
  measure damage *taken*, never damage *dealt* by an attacker), so
  `live_stats.py` deliberately omits it and the API still renders `null`
  ("—") for damage per player.
- **Verdict (corrected):** fight-signal damage = BUILT & WIRED. Per-player
  stats persistence = WIRED via the live-stats service. Per-player
  `damage_dealt` itself = still honestly NULL — no dealt-damage producer
  exists from spectator mode.

### 2. Camera switching — ✅ BUILT & WIRED (one nuance)
- **Director service** (`services/director_service.py`, 855 lines): 3 modes —
  FULL_AUTO (system publishes), SEMI_AUTO (AI proposes, human approves), MANUAL
  (operator only). 5 scenes: `main, standings, killfeed_focus, commentary, map_cam`.
  Suggestion queue with approve/reject + dedupe + expiry.
- **Holds/cooldowns (Visual Bible):** map-cam **30 s maximum hold**, enforced in
  every mode by `enforce_scene_holds()`, ticked every 5 s from main.py's lifespan;
  re-publishing the same scene never extends a hold. DirectorAI engine
  (`agents/observer_agents/director_ai.py`): **FIGHT_HOLD_MIN_SECONDS = 45** fight
  lock, 10 s minimum scene time (switch cooldown), priority-scored POV selection.
- **Triggers:** kill/knock events → BroadcastEvent (priority CRITICAL/HIGH/MEDIUM by
  fight score) → `director_engine.process_tick` → SWITCH_POV decision →
  `director_service.system_publish` (full_auto) or a `killfeed_focus` suggestion
  (semi_auto).
- **Nuance (honest):** the `process_tick` call site lives in main.py's built-in
  match-event path (the simulator/demo event source). The decision engine, service,
  holds and mode gates are all real and unit-tested; with a real feed, events must
  arrive through that same main.py event path for auto-switching to fire — the
  vision-intelligence subscriber feeds the *fight detector*, and does not itself
  call `process_tick`.

### 3. Cinematic shots — ⚠️ EXISTS BUT NOT WIRED into the live output
- **Exists:** `CinematicController` agent (observer family) — TransitionType
  (zoom_in, zoom_out, fade, cut…), CameraMovement (slow_pan, zoom_in/out…),
  CinematicEffect (slow_motion, depth-of-field…), camera state (zoom level, target),
  presets and multi-step cinematic sequences. It is instantiated by the
  AgentOrchestrator and the observer DirectorAI.
- **Not wired:** the live broadcast path (main.py + director_service + output page)
  never calls it; the output page applies no cinematic zoom/pan/slow-mo treatment
  to the video. It is a decision/state model, not video processing — observer feeds
  are phone screens, so a true cinematic zoom would need a video-side transform
  that does not exist in the output chain.
- **What IS live:** broadcast *segments* with a cinematic role — pre-show flow
  (`/broadcast/preshow`, preshow service) and the WWCD winner ceremony
  (`/broadcast/ceremony`, ceremony service, wwcd-ceremony component).
- **Verdict:** cinematic camera *logic* EXISTS BUT NOT WIRED; cinematic *segments*
  (pre-show, ceremony) BUILT.

### 4. Sniper shots — ❌ NOT PRESENT as detection; ⚠️ PLUG-READY highlight trigger; ✅ sniper as a stats label
- **Weapon detection:** `WeaponDetector` exists (`vision/detectors/weapon_detector.py`)
  but is **NOT wired** — confirmed in `runtime.py`: it needs PaddleOCR (not
  installed; production OCR is Tesseract) and is deliberately excluded. The
  kill-feed OCR does not extract weapons from real BGMI frames (the weapon is an
  icon, not text); the feed schema has a `weapon` field that stays `"Unknown"`
  unless an external feed or manual entry supplies it.
- **Sniper highlight:** `sniper_triple` trigger is registered in
  `services/replay/highlight_triggers.py` behind the trigger registry — it only
  activates for event sets that actually carry sniper-class weapon data
  (`trigger_registry()` reports it inactive otherwise). PLUG-READY.
- **Sniper as analysis:** BUILT — playstyle classifier has `SNIPER_SPECIALIST`
  (engagement distance + headshot % + aggression), MVP awards include
  `BEST_SNIPER`, EngagementAnalyzer maps AWM/M24/Kar98k → WeaponCategory.SNIPER.
  These classify *players*, they do not detect sniper *shots*.
- **Verdict:** no sniper-shot detection exists today. Anyone claiming otherwise
  would be faking it — the honest path is weapon data from a real feed.

### 5. Knock + headshot — knock ✅ BUILT & WIRED; headshot ⚠️ EXISTS BUT NOT WIRED (no reliable live source)
- **Knock — BUILT & WIRED, three sources:** (a) kill-feed OCR parses action words
  (`knocked/downed/knock` → knock, `eliminated/killed/finished` → kill) in
  `vision/detectors/killfeed.py`; (b) feed adapter has a first-class `KNOCK`
  event type → `knock_event` on the bus; (c) Spotter UI has a kill/knock toggle
  that ingests knocks manually. A separate PaddleOCR `KnockDetector` module
  exists but is not wired (redundant with the OCR path).
- **Headshot — the honest picture:** the kill-feed OCR entry model has a
  `headshot` flag, set only when the OCR text literally contains "headshot" or
  "hs" (`killfeed.py:369`). BGMI's killfeed marks headshots with an **icon, not
  text** — so on real footage this flag will almost never fire. The DB has a
  `headshots` column, the canonical MVP formula includes `headshots × 2`, and
  agents (EngagementAnalyzer, HypeDetector — incl. a >100 m headshot hype moment)
  consume headshot flags *when present*. CORRECTION (Phase 33 — the
  2026-10-20 claim it replaces was wrong): the Spotter UI DOES have a
  headshot toggle (Phase 24, `frontend/app/spotter/page.tsx:432`), and
  a spotter/manual headshot is a trusted-human producer — the live-stats
  writer source-gates headshots so only trusted-human provenance
  (`headshot_confirmed`, spotter/manual/observer source) increments the
  persisted counter (`backend/app/services/live_stats.py` header). What
  still has no reliable producer is VISION headshot: the killfeed icon
  flag measures 7.7% precision (Phase 23) and is banned from downstream
  consumption, so in the pure vision path the MVP headshot term is
  effectively always 0 — honestly 0, not faked.
- **Verdict (corrected):** knock = BUILT & WIRED. Headshot = schema/formula/
  consumers exist + a MANUAL (spotter) producer exists; a reliable VISION
  producer does not → EXISTS, MANUALLY PRODUCIBLE, NOT VISION-WIRED.

### 6. Replay of knock / headshot / big moments — big moments ✅ BUILT; knock/headshot-specific replay ❌ NOT PRESENT
- **Replay system:** event replay — marks + clips cut from the rolling event
  buffer and re-broadcast over WebSocket (`replay.play`) so the output page
  re-renders the moment as data. It is explicitly **not** video replay
  (`is_video=False`); video clips come from the renderer (below).
- **Big-moment triggers (BUILT, `services/replay/highlights.py`):**
  multi-kill (≥3 kills by one player in 10 s), squad wipe (≥4 kills vs one team
  in 30 s, score 90), **verified clutch** (≥2 kills in 10 s while last alive —
  verified against real alive counts), final-kill. Plus the whole-day reel
  (24 segments / 5 min cap).
- **Video rendering (BUILT, source-dependent):** `HighlightRenderer` cuts
  16:9 MP4 + 9:16 MP4 + 3 s GIF around a moment **if** a recording exists in
  RECORDINGS_DIR; otherwise the moment is honestly marked `no_source` — no fake clip.
- **Knock/headshot replays:** there is **no dedicated trigger** for a knock or a
  headshot. A knock that occurs inside a multi-kill/clutch window is part of that
  clip's event window, but a standalone knock or headshot never triggers a replay
  or highlight by itself. Grenade-multi-kill / sniper-triple / vehicle-kill
  triggers are registered PLUG-READY (need truthful weapon data — see §4).
- **Verdict:** big-moment replay = BUILT. Knock/headshot replay = NOT PRESENT
  as its own trigger.

---

## Honest leftovers found during this audit
- `production/` and `broadcast/` contain a second, older OBS-control stack
  (scene managers, replay controllers ×2) alongside the live streaming service —
  real code, but the live path uses `streaming/service.py` + director output page.
- `intelligence/director_ai.py` is a second DirectorAI implementation; the wired
  one is `agents/observer_agents/director_ai.py` (instantiated in main.py).
- 12 pytest collection errors in legacy scaffold-era test files (pre-existing).
- The 4 CI workflow files exist only locally (GitHub App cannot push
  `.github/workflows/`).
