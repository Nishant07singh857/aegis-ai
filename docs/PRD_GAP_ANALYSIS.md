# PRD v2.0 Gap Analysis — AEGIS AI: PRD vs Actually Built

**Date:** 2026-10-20 · **Method:** 4 independent auditors read the **actual codebase** (`backend/app/**`, `frontend/**`, migrations, compose files) and classified every PRD item against real code with file-path evidence. Docs were not trusted. Worker detail files: `docs/gap_audit_w1.md` (M1–M4), `docs/gap_audit_w2.md` (M5–M8), `docs/gap_audit_w3.md` (M9–M13), `docs/gap_audit_w4.md` (tech stack / agents / DB / exceeds-PRD).

**Status key:** ✅ BUILT (works as specified) · 🟡 PARTIAL (exists but differs/limited/unwired) · ❌ MISSING (not built) · 🔄 DIFFERENT-APPROACH (deliberately built differently — usually the user's ZERO-API-SPEND decision or a correction learned from real Krafton broadcasts; **not** a failure).

---

## Executive Summary

**165 PRD items audited:**

| Status | Count | Share |
|---|---|---|
| ✅ BUILT | 55 | 33% |
| 🟡 PARTIAL | 67 | 41% |
| ❌ MISSING | 30 | 18% |
| 🔄 DIFFERENT-APPROACH (deliberate) | 13 | 8% |

**Per area:**

| Area | ✅ | 🟡 | ❌ | 🔄 | Items |
|---|---|---|---|---|---|
| M1–M4 (Spectator / Ingest / CV / Fight) | 9 | 23 | 12 | 2 | 46 |
| M5–M8 (Tracking / Director / Analytics / Leaderboard) | 17 | 12 | 1 | 3 | 33 |
| M9–M13 (Insights / MVP / Highlights / Bus / Commentary) | 8 | 19 | 12 | 4 | 43 |
| Tech stack / Agents / DB | 21 | 13 | 5 | 4 | 43 |

**One-line verdict:** The product's *core loop* (capture → detect → direct → broadcast) is real and in places exceeds the PRD, but the PRD's *breadth* claims are only ~⅓ fully true: a large middle band is "real code, not wired into the live path" (CV detectors, secondary analytics, anti-cheat), and three PRD pillars are genuinely absent — **rendered highlight video export, bilingual commentary modes/dual-track audio, and the 24 non-team spectator devices**. The architecture claims (40 microservices, Kafka, TimescaleDB) do not hold — it is a well-built monolith with an in-process agent library on Redis, which at this scale is arguably the right call (see §Deliberate Differences).

---

## Deliberate Differences — NOT failures (read before judging any 🔄)

1. **ZERO API SPEND (user's explicit policy).** The PRD assumes Claude/GPT-4o (scripts/insights), Azure TTS, and ElevenLabs. The user forbade all paid APIs. The project substitutes **Ollama + honest heuristic fallbacks** for LLMs and **Piper TTS (offline, free)** for voices, with paid providers gated behind `ENABLE_PAID_LLM=true`. Every 🔄 in M9/M13 and the tech stack traces to this decision. Consequence to note honestly: quality ceiling of scripts/voices is lower than the PRD's paid stack, and commentary latency is seconds, not the PRD's <600 ms.
2. **16 devices, not 40.** BGMI has 16 teams; the project built 16 team-observer slots (the PRD's most important class) with DB-driven team labels, auto-reconnect, and on-air selection. The PRD's other 24 devices (Map ×4, Final Zone ×2, Hotspot ×10, Backup ×8) do not exist as devices — Map/Final-Zone survive only as logical feed types. Judgement: the 16 cover the *spirit* for a 1-PC product; the missing **Backup** pool and **Hotspot** dynamic feeds are the only parts with real broadcast value (see Top-10 gaps).
3. **Points formula: the project is MORE correct than the PRD.** PRD's 15/12/10/… placement table is a generic format. The project scores with the **real BMSD 2026 system** (chicken dinner = 10, no other placement points, 1/finish) measured from actual Krafton broadcasts — and it is **configurable per tournament** (`tournaments.placement_points` JSONB + `points_per_kill`), so the PRD table can still be entered as config for any tournament that uses it.
4. **Kafka: absent, and it does not matter at this scale.** One broadcast, 16 phones, one server (optional small cluster sharing one Redis). The Redis event bus (Streams + Pub/Sub + DLQ + replay, 990 lines) covers the PRD's topic model. Kafka would add ops cost with no benefit until multi-tournament concurrency exists.
5. **TimescaleDB: absent, low impact.** Plain PostgreSQL 16 with ordinary tables + indexes handles current volumes. Revisit only if per-second HP/position time-series are actually persisted at scale (today they are not — see M7).
6. **Agents are a library, not microservices.** 41 real `BaseAgent` subclasses (~27.6k lines) run in-process under one orchestrator in one FastAPI app. The count claim holds; the deployment claim does not. At single-tournament scale this is simpler and cheaper to run; it only becomes a scaling ceiling if many tournaments must run concurrently on one install.
7. **OCR is Tesseract, not PaddleOCR.** Deliberate, documented in `detectors/killfeed.py`; also free/local/offline. A complete PaddleOCR wrapper exists but is not wired into the live pipeline.

---

## M1 — Spectator Management System

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| 40-device fleet (total) | ❌ | Hard-capped at 16: `vision/capture/multi_device.py` (`MAX_OBSERVER_SLOTS=16`), `device_manager.py` (`max_devices=16`), `db/models/observer_slot.py` (slots 1..16) | Critical (vs PRD text) | Deliberate 16-team scope — see §Deliberate Differences. Do not chase 40; add Backup pool only (below). |
| Team Observer ×16 | ✅ | `MultiDeviceCaptureManager` (DB-driven team labels, thumbnails, on-air select); `api/v1/observers/router.py`; unit-tested vs fake ADB | None | — |
| Map Observer ×4 | ❌ | Only logical label `FeedType.MAP_OVERVIEW` in `agents/observer_agents/pov_selector.py` | Major | Map view can reuse the map-cam feature (built, Phase 10/18); dedicated devices unnecessary at 1-PC scale. |
| Final Zone ×2 | ❌ | Only logical label `FeedType.FINAL_ZONE` in `pov_selector.py` | Major | Same as Map — logical role already drives director priority. |
| Hotspot ×10 | ❌ | No hotspot device role anywhere | Major | Real value if added later as *dynamic re-tasking* of existing slots, not 10 extra phones. |
| Backup ×8 | ❌ | No spare/reserve pool; dead slot just reports `disconnected`/`no_feed` | Major | Small version worth building: 1–2 spare devices + auto-failover onto a failed slot. |
| Auto-reconnect | ✅ | `frame_grabber.py` (capped exponential backoff, retries forever, LIVE/NO_FEED/RECONNECTING states); supervisor loop in `multi_device.py` | None | — |
| Latency sync ±500 ms | ❌ | Frames stamped with server grab-time (`time.time()`); no device-clock timestamps, drift measurement, or alignment buffer. `feed_health_monitor.py` only *penalises* >500 ms in a score | Critical | Add capture-time stamping + per-feed offset tracking before claiming multi-feed sync. |
| Feed health scores | 🟡 | Real scorers: `feed_health_monitor.py` (0–100, FPS/latency/drops), `device_manager.py` health model — but `record_frame()`/`record_frame_drop()` have **zero call sites**; live status exposes counts/age, never the score | Major | Wire the two record calls into the grabber loop — code exists, ~an afternoon of work. |
| Auto role reassignment | 🟡 | Manual only: `PUT /observers/{slot}/assign`; eliminated teams skipped for broadcast selection in `pov_selector.py` | Major | Add auto-trigger on slot failure (with Backup pool) and on team elimination. |

## M2 — Video Ingest System

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| OBS ingest | 🔄 | OBS Virtual Camera as generic `cv2.VideoCapture` (`vision/ingestion_manager.py`); OBS WebSocket client is output/director-side (`broadcast/obs_client.py`, hand-rolled v5 protocol) | Minor | Acceptable — OBS is the output tool in this product, not an ingest source. |
| RTMP ingest | ✅ | `frame_grabber.py` (`FrameSource.RTMP_STREAM`), `ingestion_manager.py` (`CAP_FFMPEG`) | None | — |
| WebRTC ingest | ❌ | No `webrtc`/`aiortc` code or dependency | Major | Only needed if browser-based spectators become a requirement. |
| NDI ingest | ❌ | `FrameSource.NDI_FEED` raises "not supported in this build" — honest stub, returns no-feed (never fakes frames) | Major | NDI matters for pro venue setups; defer until a paying venue asks. |
| Emulator ADB capture | 🟡 | Works implicitly via ADB (`adb_controller.py` discover/connect any serial incl. emulators); no emulator-specific management | Minor | Document emulator use; no code needed. |
| ADB phone capture (screencap/scrcpy) | ✅ | `frame_grabber.py` (`adb exec-out screencap`, scrcpy backend), `scrcpy_capture.py` — ~5–10 FPS/device | None | — |
| 1080p pipeline | 🟡 | Sources/program feed full-res, but CV preprocess is **960×540** @ ~8–10 FPS (`vision/capture/config.py`) | Major | Fine for current detectors; raising CV resolution is a GPU/CPU budget decision, not a bug. |
| Timestamp sync | ❌ | Same root gap as M1 latency sync — server grab-time only | Major | See M1 latency sync. |
| Frame-drop logging | 🟡 | Counters in `ingestion_manager.py` and grabber stats; `FeedHealthMonitor.record_frame_drop()` uncalled; no per-feed drop log in the main path | Minor | Wire with the health-score fix. |

## M3 — Computer Vision Engine

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| YOLOv8 | 🟡 | Real wrapper `vision/models/yolo_service.py` + `yolov8n.pt` (generic COCO weights), but **not registered in the live runtime**; only trained-model path live is the kill-feed ONNX hybrid (`ml/vision_wiring.py`) | Major | Wire once a BGMI-trained model exists (Kaggle RF-DETR training in progress); COCO weights can't read BGMI UI. |
| PaddleOCR | 🔄 | Production OCR is **Tesseract** (`detectors/killfeed.py`, `detectors/hud.py` `TesseractEngine` + template-digit fallback); complete `PaddleOCRService` wrapper unused live | None | Deliberate substitution (§Deliberate Differences #7). |
| ByteTrack | 🟡 | Real service `vision/models/bytetrack_service.py` (boxmot + IOU fallback; boxmot not in pyproject) — nothing in the live path calls it; `PlayerTracker` does its own distance matching | Major | Only matters once position tracking is live; pair with minimap wiring. |
| OpenCV | ✅ | Core dependency, used across all detectors/capture | None | — |
| Kill feed | ✅ | `detectors/killfeed.py` (Tesseract OCR, name parsing, 30 s dedup → NormalizedEvent), wired in `vision/runtime.py`, tested; measured ~90% on synthetic frames | None | Real-BGMI calibration still pending (needs real footage). |
| HP bars | 🟡 | Real, tested detector `detectors/hp_bar_detector.py` — **not registered in `runtime.py`**; live HP-adjacent signal is only coarse team-card status | Major | **Wire it** — unlocks HP-drop velocity (M4) and honest damage (M7). Top-10 gap. |
| Team names | 🟡 | Player names parsed from kill feed; team names from DB master data (more reliable by design) | Minor | Accept. |
| Smoke detection | 🟡 | Real heuristic `detectors/utility_detector.py` (`_detect_smoke`) — not wired; output never reaches fight detector/bus | Major | Wire with HP bars (same runtime registration task). |
| Frag grenades | 🟡 | Explosion-flash detection in `utility_detector.py` — not wired; detects flashes, not trajectory | Major | Same wiring task. |
| Vehicles | 🟡 | `detectors/vehicle_detector.py` (COCO-based; BGMI type table is metadata the model can't produce) — not wired | Major | Needs trained model to be truthful; defer to model roadmap. |
| Alive count | ✅ | `hud.py` `AliveCountReader` (Tesseract + template fallback, multi-frame confirmation), live in `runtime.py`, tested (28/30 measured, zero wrong reads) | None | — |
| Zone circle | 🟡 | `detectors/zone_detector.py` (circle geometry) not wired; live zone signal is only the timer OCR (`ZoneTimerReader`) | Major | Wire geometry if zone-position features (M9 zone pressure) are prioritised. |
| Minimap | 🟡 | Complete reader `detectors/minimap_reader.py` (positions/zone/vehicles/airdrops) — production never calls it | Major | Wiring it is the key that unlocks proximity/rotation truthfulness. |
| Parachute phase | ❌ | Zero occurrences of "parachute" in `backend/app/` | Major | Nice-to-have for drop analysis (Phase 11 dossiers use spotter/feed data instead). |
| Weapons | 🟡 | HUD weapon reader exists (PaddleOCR-based, unwired); kill-feed weapon honestly `None` (icons never guessed) | Major | Blocked on trained icon detection; keep honest `None` until then. |
| Helmets/vests | 🟡 | No dedicated detector; only HP detector's armor-level colour guess (and that detector is unwired) | Major | Low broadcast value; defer. |

**M3 pattern to understand:** the CV engine is *wide but shallow in production* — `vision/runtime.py` runs only **4 detectors live** (kill feed, alive count, zone timer, team status). The rest is real Gen-1 code awaiting wiring + a trained BGMI model.

## M4 — Fight Detection Engine

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| HP drop velocity | 🟡 | `fight_agents/fight_detector.py` `process_hp_change()` — single-event threshold (damage ≥20); declared 2.0 s window never used, **no velocity math**; no live HP producer (HP detector unwired) | Major | Fix with M3 HP wiring: sliding-window HP/sec. |
| Kill feed activity | ✅ | `process_kill_feed()` (weight 0.40), full CV → bus → detector chain via `kill_confirmed` | None | — |
| Audio gunshots | ❌ | `FightSignal.SHOOTING` enum exists (weight 0.05) with **no producer and no callers**; audio module does mixing, not gunshot analysis | Major | Genuinely hard (spectator audio is mixed/delayed); spotter remains the honest fallback. |
| Grenade explosions | 🟡 | Combined `process_utility()` API (grenade value 80 on enemy hit) — nothing calls it automatically | Major | Wire from `utility_detector` (M3 task). |
| Smoke deployment | 🟡 | Same `process_utility()` (smoke, confidence 0.4) — no automatic producer | Major | Same wiring task. |
| Proximity <50 m | 🟡 | `process_proximity()` real, but threshold is **150 m**, not <50 m; no live position producer (minimap unwired) | Minor | Tighten to 50 m once positions are live; at 150 m it fires for mere co-location. |
| Down count | 🟡 | Only the `is_knock` flag inside the kill-feed signal; no independent down-count signal | Major | Derive from team-status reader + HP detector once wired. |
| Squad size delta | ❌ | No such signal anywhere; alive-count changes never converted into a fight signal | Major | Cheap to add once alive-count events are consumed by the fight detector. |
| Intensity 0–100 | ✅ | Implemented twice: raw intensity (`fight_detector.py`, capped 100) + broadcast-priority scorer (`intensity_scorer.py`); persisted as `FightEvent.intensity_score` | None | — |
| FightEvent JSON schema | 🟡 | Shape split across DB model (`db/models/fight_event.py`), bus payload, and serializer dicts that don't fully agree | Minor | Publish one canonical schema in the feed-adapter contract. |

## M5 — Team Tracking Agents

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| 16 agents as FastAPI microservices | 🟡 | 4 in-process agents per team (`TeamTracker` + `RotationAnalyzer` + `UtilityTracker` + `PositioningAnalyzer`) spawned by `agents/orchestrator.py`, `MAX_TEAM_AGENTS=16` (`config.py:131`), inside ONE FastAPI app | Medium | Functionally complete; architecture differs (§Deliberate Differences #6). |
| Rotation tracking | ✅ | `team_agents/rotation_analyzer.py` (path, strategy classification, vehicle events, prediction); DB `rotation_event.py` | None | — |
| Alive count | ✅ | `team_tracker.py` team state + vision counter + `core/match_state.py` | None | — |
| Utility usage | ✅ | `utility_tracker.py` + `team_tracker.py` counters; DB `utility_event.py` | None | (Publishing gap is in M7.) |
| Aggression index | 🟡 | Live heuristic in `team_memory.py` (1–10) vs DB `team_intelligence.aggression_score` (0–1) that **nothing writes** — dossiers render "insufficient_data" | Medium | One canonical computation writing the DB field. |
| Positioning | ✅ | `positioning_analyzer.py` (941 lines: cover, high ground, exposure, formation, zone) | None | — |
| Engagement history | ✅ | `engagement_analyzer.py` + head-to-head memory in `team_memory.py` | None | — |
| Loot assessment | 🟡 | Loot *detection* (`loot_detector.py`) + equipment inventory exist; no per-team loot quality/tier **score** | Medium | Small scoring layer on existing inventory data. |
| Win probability (Bayesian) | 🔄 | Transparent heuristic normalisation (`prediction_agents/win_probability.py`: alive×3 + kills×2 + in-zone×5 + form + H2H, 30 s cadence, honest `None`/"—") — **not Bayesian** (no priors/posterior) | Low | Heuristic is honest and explainable; call it what it is. Bayesian upgrade is a research task, not a fix. |

## M6 — Observer Director AI

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| Min scene 10 s | ✅ | `config.py:150` `MIN_SCENE_TIME_SECONDS=10`, enforced in `director_ai.py`. Caveat: `safety_controller.py` has a divergent 8.0 s constant | Low | Unify the two constants. |
| Cooldown 3 s | 🟡 | No 3 s cooldown exists; behaviour fragmented across 2 s rapid-switch / 6 s fight-lock / 10 s team-cooldown / 15 s replay constants | Medium | Name one switch-cooldown constant; values are defensible, naming is not. |
| Clutch priority (1v2/1v3/1v4) | ✅ | `clutch_detector.py` (SOLO_VS_DUO/TRIO/SQUAD) → `_switch_to_clutch` priority 100, immediate, bypasses the 45 s fight-hold | None | True override, matches PRD intent exactly. |
| Final zone lock (top-5/top-3) | 🟡 | Priority *scoring* exists (`pov_selector.py`: overview 80 if ≤3 teams, FINAL_ZONE 75 if ≤5; longer holds) but **no hard lock** forbidding a switch away | Medium | Add a hard lock mirroring the clutch lock for the final circle. |
| Narrative continuity | 🟡 | `narrative_score` is 10% of candidate scoring; storyline engine exists separately; director records scene history but doesn't use it in decisions | Low | Add a stay-with-storyline bonus using the existing history deque. |
| Star player focus | ✅ | `pov_selector.py` star candidates (40 + rating×5, first-person if >7); director `priority_players` +15 | None | — |
| `should_switch()` logic | 🔄 | No single named function; equivalent stricter pipeline: `director_ai._make_director_decision` + `SafetyController.can_switch()` + `IntensityScorer.compare_fights()` → `(should_switch, reason)` | Low | Behaviour ≥ PRD; only the named entry point is missing. |
| 3 modes (beyond PRD) | ✅ | `services/director_service.py`: `full_auto` / `semi_auto` (suggestion approve/reject queue, 60 s TTL) / `manual` | None | **Exceeds PRD** — see Exceeds section. |
| Fight holds (Bible) | ✅ | Named constants from measured Krafton behaviour: `FIGHT_HOLD_MIN_SECONDS=45`, staleness 90 s, switch gap 40; map-cam max 30 s enforced | None | **Exceeds PRD** (PRD has no such measured discipline). |

## M7 — Live Analytics Engine

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| Kills | ✅ | Event-driven at agent, service/DB, and vision layers (`team_tracker.py`, `match_service.py`, `core/match_state.py`) | None | — |
| Damage | 🟡 | Honestly computed from HP drops in agents (`team_tracker.update_player_hp`: `damage = old_hp − new_hp`), **but** the HTTP analytics summary estimates `kills×120` (and demo MVP `kills×100`) in `main.py` — contained only by the DEMO_MODE gate | **High** | Route the API to the agent-computed damage; delete the estimate paths. |
| Healing | 🟡 | Recorded in agents + vision detector exists, but `MatchPlayerStats` has **no healing field** — never persisted or published | Medium | Add field + API surface (small fix, counters exist). |
| Grenades used | 🟡 | Counted in-memory per team (`utility_tracker.py`), absent from stats model/API — never published | Medium | Same small fix as healing. |
| Survival time | ✅ | Persisted per player (`survived_time_seconds`), aggregated by `performance_tracker.py` | None | — |
| Revives | ✅ | `record_revive` → persisted `revives_performed` | None | — |
| Aggression score | 🟡 | Two disconnected implementations (see M5) | Medium | See M5. |
| Zone efficiency | ❌ | Metric never computed; only raw ingredients (`in_zone`, distances, danger level in rotation/positioning analyzers) | Medium | Compute from existing ingredients — a defined formula + publisher. |
| Rotation speed | ✅ | `RotationPath.average_speed/max_speed/total_distance` per team | Low | — |
| Update frequencies | 🟡 | Explicit for overlay *delivery* (`overlays/data_pusher.py`: 33 ms–1 s channels); analytics computations themselves are event/tick-driven with no per-metric cadence contract | Medium | Document cadence per metric; low user impact today. |

## M8 — Live Leaderboard System

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| PRD points formula (15/12/10… + 1/kill) | 🔄 | Real **BMSD 2026** scoring: chicken = 10, no other placement points, 1/finish (`analytics/points_calculator.py`, `config_source="defaults (Krafton BMSD)"`) | Low | **Project is more correct than PRD** (§Deliberate Differences #3). Cleanup: legacy `PlacementPoints` dataclass with 15/12/10 defaults still sits unused in the same file — remove to avoid a trap. |
| Configurable from tournament config | ✅ | `configure_from_active_tournament()` reads `tournaments.points_per_kill` + `placement_points` JSONB; match recording scores through it | None | PRD's table is enterable as config. |
| Redis channel | 🟡 | Redis **hash** `aegis:standings` (`intelligence/points_engine.py`, finishes-only, no placement points) — a **second source of truth** beside the configured calculator; real delivery is WebSocket `/ws/live` + `DataPusher` SCOREBOARD channel; no dedicated pub/sub topic | Medium | Retire or subordinate `PointsEngine`; publish standings changes on one channel. |
| Lower-third overlay | ✅ | `overlays/lower-third` page + 3 templates; director-controlled state (manual-gated, semi-auto approvable) | None | — |
| Standings + movement arrows | ✅ | `leaderboard_generator.py` (overall/kills/damage/survival/MVP boards, `rank_change`, tiebreaker chain); 7 frontend standings templates incl. Krafton boards | None | **Exceeds PRD** (see Exceeds section). |

## M9 — AI Insights Engine

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| Fight prediction | ✅ | `prediction_agents/fight_predictor.py` (672 lines, 6 weighted factors, confidence levels), wired in orchestrator | Low | Heuristic (no trained weights yet — Kaggle training pending). |
| Zone pressure | 🟡 | No `zone_pressure` metric exists; zone position is only a *factor* inside survival/win-probability predictors + storyteller zone story | Medium | Surface a per-team zone-pressure score (ingredients exist). |
| Rotation prediction | 🟡 | `rotation_analyzer.py` linear extrapolation from last 5 points; not zone-aware; no broadcast/API surface | Medium | Couple with `zone_predictor` output; publish to director. |
| Utility advantage | ✅ | `fight_predictor.py:394` (nades×5, smokes×2, molotovs×3, 15% weight) + full `utility_tracker.py` | Low | — |
| Clutch probability | ✅ | `clutch_detector.py` (826 lines; 1v1–2v4 types, base probs, real-time updates) | Low | — |
| Team comparison | ✅ | `analytics/comparative_analysis.py` + storyteller H2H/player-comparison + animated `H2HCards.tsx` | Low | Not a standalone REST endpoint (via stories API) — acceptable. |
| Narrative flagging | 🟡 | 7 story generators are real (`storyteller.py`) but **director must manually trigger**; nothing auto-flags a storyline moment into the live broadcast | Medium | Add auto-flagging: generators already detect conditions; emit flags, let director approve (fits semi-auto mode). |
| Anti-cheat flags | 🟡 | Real detector `referee_agents/cheating_detector.py` (541 lines, 12 types, human-review design) — but **feed-dead**: needs aim/movement telemetry no spectator feed provides; no review UI | High | Either define a vision-derivable subset (impossible positions from minimap) + review UI, or scope it honestly as post-match/manual. |
| LLM = Claude API | 🔄 | Ollama default + heuristic fallback with honest `source` labelling (`app/ai/factory.py`); paid providers opt-in only. Legacy `ml/commentary/llm_chain.py` (gpt-4 references) is **dead code** with zero callers | Info | Zero-spend decision. Hygiene: delete/quarantine `llm_chain.py`. |

## M10 — MVP Engine

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| MVP engine exists | ✅ | `analytics_agents/mvp_calculator.py` (549 lines, 8 factors, placement multiplier, category awards), wired in orchestrator | Low | — |
| PRD formula (kills×5, dmg×0.05, revives×3, survival×0.02, clutches×10, headshots×2) | ❌ | Calculator uses **different weights** (kill 10, damage 0.01, revive 8, clutch attempt 10 + win 30, headshot 5, + multipliers + 0–100 normalisation). Worse, **three conflicting definitions** coexist: calculator vs demo endpoint `(kills×10)+50` (`main.py`) vs `match_service.py` taking `mvp_score` as an external passthrough (never computed). Calculator output feeds **neither DB nor overlay** | **High** | Pick ONE canonical formula (PRD's or the calculator's — either is defensible), one producer, wire it calculator → DB → overlay. Top-10 gap. |
| Animated overlay leaderboard | 🟡 | Board is real and animated (`standings-mvp.tsx`, staggered slide-in) but **sorts by finishes** and shows a feed-provided rating — not the MVP engine's score | Medium | Fixed by the same wiring as above. |

## M11 — Highlight Generation System

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| 1v4 clutch | 🟡 | Wired detector approximates a solo clutch (≥2 kills/10 s, only scorer on team) without verifying 1v4 alive counts; richer `ClipDetector` category is passive/unfed | Medium | Add alive-count verification (alive data exists). |
| Squad wipe (30 s window) | ✅ | `services/replay/highlights.py`: ≥4 kills vs same victim team within exactly 30 s, score 90; API + highlights page | Low | Matches PRD exactly. |
| Grenade multi-kill | 🟡 | Category exists in passive `ClipDetector`; the wired detector never inspects weapon/utility fields, so it never fires | Medium | Wire once weapon data is truthful (M3 dependency). |
| Sniper triple | ❌ | No trigger combines sniper/DMR + 3 kills; multi-kill detection is weapon-agnostic | Medium | Same weapon-data dependency. |
| Vehicle kill | ❌ | No highlight trigger references vehicle kills | Medium | Needs kill-feed/vehicle linkage. |
| Final kill | ❌ | No final-kill trigger (only a `FINAL_CIRCLE` *moment* category and an MVP `last_kill` bonus) | Medium | Easy once match-end state is consumed by the detector. |
| Export 16:9 | 🟡 | Exists only as metadata labels (`content_publisher.py`); **no video file is ever produced** | **High** | See export pipeline row. |
| Export 9:16 vertical | 🟡 | `"9:16"` labels only; no crop/scale render anywhere | **High** | Same. |
| Export 3 s GIF | ❌ | Zero GIF code in backend/frontend | Medium | Trivial once video clips exist (one FFmpeg flag). |
| FFmpeg export pipeline | 🟡 | FFmpeg is real for streaming/audio (`utils/video_utils.py`, `streaming/service.py`) but **nothing in the highlight path calls it**; replay is explicitly *event* replay (`Clip.is_video=False`, honestly labelled) | **High** | **The module's biggest gap:** highlights are scored windows, never rendered clips. Build the render step (moments → FFmpeg cut → 16:9 + 9:16 + GIF). Top-10 gap #1 — directly monetisable (Highlight API, creator Reels/Shorts). |

## M12 — Event Bus

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| Kafka + Redis | 🔄 | Redis only: `core/event_bus.py` (990 lines — Streams, Pub/Sub, DLQ+retry, replay, priorities). No Kafka anywhere | Info | Right call at this scale (§Deliberate Differences #4). Caveat: docstring advertises "consumer groups" but no `xreadgroup` exists — fix docstring or add groups. |
| `fight.detected` | ✅ | Published `fight_detector.py:648`; subscribed by hype detector | Low | Exact name match. |
| `kill.occurred` | 🟡 | Equivalent named `kill_event`/`knock_event` | Low | Naming drift only. |
| `zone.updated` | 🟡 | Three names across layers: `zone.tick` (WS), `zone_event` (bus), `zone_change` (DB/vision) | Low | Pick one canonical bus name. |
| `team.eliminated` | 🟡 | Equivalent `team_wipe_event` / `TEAM_WIPE` enums | Low | Naming drift. |
| `feed.switch` | 🟡 | Equivalents `director.decision`, `broadcast_command`, `director.state` | Low | Naming drift. |
| `commentary.trigger` | 🟡 | Output topic `commentary.line` exists; trigger side is `hype.spike` + internal 22-value `CommentaryTrigger` enum (direction inverted vs PRD, functionally covered) | Medium | Formalise the topic name if PRD parity matters. |
| `highlight.clip` | 🟡 | Equivalent `highlight.clip_ready` (carries clip_id/window) | Low | Close enough. |
| `leaderboard.update` | ❌ | No topic, no emitter; leaderboard computed in-memory, pushed via overlay channels/REST | Medium | Emit on standings change so subscribers stop polling. |
| `insight.generated` | ❌ | No topic and no insight-event service; only post-match template strings in `report_generator.py` | Medium | Tied to M9 narrative auto-flagging. |

## M13 — Bilingual Commentary Engine

| PRD item | Status | What's actually in the project (evidence) | Severity | Recommendation |
|---|---|---|---|---|
| LLM script generation (Claude/GPT-4o) | 🔄 | `script_generator.py` (964 lines, 9 prompt templates + few-shot per style) via Ollama with honest provenance labels; heuristic template fallback | Info | Zero-spend decision. |
| TTS (Azure Hindi + ElevenLabs English) | 🔄 | **Piper** offline, verified end-to-end (`voice_synthesizer.py`, `docs/AUDIO.md`: real voice, −3.0 dB peak); only voice installed: `hi_IN-pratham-medium.onnx`; paid engines intentionally not wired | Info | Zero-spend decision. |
| English commentary (real) | 🟡 | Text side bilingual-ready (English templates/profiles/few-shot); **no English voice model installed** — English lines would be spoken by the Hindi voice with a Hindi accent | Medium | Cheap fix: install a free Piper English voice (`en_US`/`en_IN`) and route by language. |
| Hinglish mode / personality | 🟡 | Hinglish templates + `hinglish_caster` profile exist; no mode system to select it (see modes row) | Medium | Delivered with the modes fix. |
| Dual-track audio mixer | ❌ | Single output: mixer → PCM FIFO → FFmpeg `amix` → **one** AAC track; no per-language tracks for viewer selection | High | Real dual-track needs FFmpeg multi-map + player support; medium effort, high PRD visibility. |
| Subtitle overlay | ❌ | No caption track/WebVTT/SRT anywhere; commentary text appears only as lower-third graphics | Medium | Commentary lines + timestamps already exist — a WebVTT sidecar generator is a small build. |
| Latency <600 ms | ❌ | Measured reality: cold model load ~10 s; warm synthesis seconds; compute 50–100 s on the contended VM (`docs/AUDIO.md`). Sub-second is unachievable with the current path | High | Mitigation = predictive pre-synthesis of likely lines (not built). Set honest expectations; human casters carry live emotion in the product's hybrid model anyway. |
| Profanity filter | ❌ | Zero filtering code in commentary path | Low | Low practical risk (curated templates/LLM), one regex pass would close it. |
| Fallback cached audio | 🟡 | Synthesis LRU cache exists (100 entries); when TTS is down it is honestly disabled (`synthesized=False`) — no pre-recorded generic-line failover library | Low | Pre-record ~20 generic lines with Piper as a failover pack. |
| 5 modes (Hindi Main / English Main / Bilingual Blend / Silent / Human Assist) | ❌ | **No mode concept anywhere** — no enum, no director-UI selector, no API; only a master mute | High | **Major PRD feature entirely absent.** Build as director-controlled setting (fits the existing mode/permission architecture). Top-10 gap. |
| Caster personality profiles | 🟡 | 3 profiles exist (`hindi_caster_1`, `english_analyst`, `hinglish_caster`) with pitch/speed/emotion tuning, but `engine=NONE` defaults, placeholder voice_ids, PRD ids (`hype_hindi`/`analyst_english`) absent, no per-language assignment logic | Medium | Wire profiles to the Piper voices when modes land. |
| Event→commentary mapping | ✅ | 22-value `CommentaryTrigger` enum + per-trigger templates, priorities, cooldowns; single entry point `generate_commentary(trigger, context)` | Low | Real and comprehensive — matches PRD §13.4's intent. |

## Tech Stack

| PRD item | Status | Reality (evidence) | Note |
|---|---|---|---|
| FastAPI | ✅ | Single monolithic app (`main.py`, 22 routers + 2 websockets) | — |
| Kafka | ❌ | Zero references anywhere | See §Deliberate Differences #4 — no loss at this scale. |
| Redis | ✅ | Event bus, cache, cluster registry; graceful no-op fallback | — |
| Next.js | ✅ | 15.5 / React 19 / TS 5.9 / Tailwind 4, App Router | — |
| Claude/GPT-4o | 🔄 | Ollama default + heuristic fallback (honest labelling); paid opt-in only | Zero-spend decision. |
| YOLOv8 | ✅ | `ultralytics` wrapper + `yolov8n.pt`; live inference via ONNX path | — |
| PaddleOCR | 🔄 | Complete wrapper exists; **production OCR is Tesseract** (documented choice) | Zero-cost local, deliberate. |
| ByteTrack | 🟡 | Real wrapper (boxmot not in pyproject → fallback tracker runs); nothing live calls it | Wire with position tracking. |
| OpenCV | ✅ | Core dependency, pervasive | — |
| PyTorch | ✅ | Optional extra; trainers/RL present | — |
| Azure TTS / ElevenLabs | 🔄 | Replaced by local **Piper** (verified end-to-end); paid engines intentionally not wired | Zero-spend decision. |
| OBS + obs-websocket v5 | 🟡 | Hand-rolled v5 protocol client in `broadcast/obs_client.py` (port 4455, SHA256 auth) — real; but declared `obsws-python` is never imported, and a second `production/` OBS stack is a mock stub | Consolidate to the real client; drop the mock. |
| FFmpeg + NVENC | 🟡 | FFmpeg real (streaming/audio); **NVENC not wired** — actual encode is software libx264 | Wire NVENC for GPU encode when GPU hardware is present. |
| PostgreSQL + TimescaleDB | 🟡 | PostgreSQL 16 ✅ real (20 models, migrations 001, 005–011); **TimescaleDB ❌** (no extension, no hypertables) | Plain PG is fine until per-second time-series are persisted. |
| Docker | ✅ | Full dev/prod/staging/streaming/cluster compose topology + streamer/edge images | — |
| Socket.io | 🔄 | Absent; raw FastAPI/native WebSockets everywhere (reconnect hand-rolled in `useLiveSocket.ts`) | Fine at this scale. |
| Cloudflare R2 / S3 | ❌ | Local disk only (`DATA_DIR`/`HIGHLIGHTS_DIR`); no S3 client anywhere | Needed before any multi-venue SaaS — but not for 1-PC product. |

## Agent Architecture

| PRD claim | Status | Reality (evidence) |
|---|---|---|
| ~40 agents as FastAPI microservices | ❌ | **41 `BaseAgent` subclasses** (~27.6 k lines) run **in-process** under one `AgentOrchestrator` in one FastAPI app — one Dockerfile, zero per-agent services |
| ~40 agent classes exist | ✅ | 41 subclasses across 9 categories (analytics 5, commentary 6, team 5, observer 5, narrative 5, fight 4, prediction 4, highlight 4, referee 3) |
| Agents are stateless | ❌ | Explicitly stateful: per-instance queues, tactical memory (928-line `team_memory.py`), circuit breakers, lifecycle states |
| State in Redis | 🟡 | Transport + cache + cluster in Redis; agent/match state in process memory |
| Team ×16 / Fight ×6 / Stats ×4 / Prediction ×4 / Zone ×3 / Director ×2 / Commentary ×2 / MVP ×2 / Master ×1 | 🟡 mixed | Counts land close as *classes/instances* (Team ×16 as runtime instances of one class; Prediction exactly 4; Fight 4 of 6; Zone 1+service; Director split across agent+module+service; Commentary 6 vs 3; MVP 1 vs 2; Master exactly 1); Overlay ×2 and Coach ×2 categories **do not exist as agent classes** |

## DB Tables

9 of 12 PRD tables exist (`matches`, `teams`, `players`, `fight_events`, `kill_events`, `highlights`, `predictions`, `commentary`, `utility_events`); `player_stats`/`team_stats` are **computed at query time with dead fallback models** (🟡), and **`leaderboard` is a derived in-memory view, never a table** (❌ — defensible). Ten extra tables exist beyond the PRD (tournaments, users, observer_slots, stories, event_store, etc.).

---

## TOP 10 GENUINE GAPS — ranked by product + revenue impact

1. **Highlight video export pipeline (M11)** — Highlights are *scored windows*, never rendered clips. No 16:9, no 9:16, no GIF; FFmpeg exists but nothing in the highlight path calls it. The PRD's Highlight API (₹0.50–2/clip) and the creator Reels/Shorts market **cannot be served** until this is built. Detection already works — rendering is the missing step.
2. **Commentary modes + dual-track audio (M13)** — The PRD's key differentiator (bilingual AI commentary) is currently single-mode, single-track: no 5-mode selector, no dual language tracks, seconds of latency vs the PRD's <600 ms. Modes as a director setting + a free English Piper voice would close most of it; true dual-track is medium effort.
3. **Wire the orphaned CV detectors (M3)** — HP bars, minimap, zone circle, smoke, grenades, vehicles, weapons are all *real code* that the live runtime never calls. One registration task in `vision/runtime.py` (plus the trained-model wiring in progress) unlocks: HP-drop velocity (M4), honest damage (M7), live positions (proximity truthfulness), zone geometry (M9).
4. **MVP formula chaos (M10)** — Three conflicting definitions coexist (calculator vs demo endpoint vs passthrough); the calculator's output feeds neither DB nor overlay, and the animated board sorts by finishes. Pick one formula, one producer, wire calculator → DB → overlay.
5. **Damage estimation in the API (M7)** — The agent layer computes damage honestly from HP drops, but the HTTP analytics summary serves `kills×120`. Route the API to the real computation (unblocks honest dashboards).
6. **Latency/timestamp sync (M1/M2)** — The only quantitative spec with zero implementation. Capture-time stamps + per-feed offsets are required before any claim of synchronised multi-feed broadcast.
7. **Healing + grenade stats never published (M7)** — Counters exist in agents; adding fields to `MatchPlayerStats` + the API is a small fix with visible dashboard payoff.
8. **Narrative auto-flagging (M9)** — 7 story generators are real but only fire on manual director trigger; nothing auto-flags storyline moments into the live broadcast. Fits the semi-auto approval model perfectly.
9. **Anti-cheat review surface (M9)** — Detector is code-complete but feed-dead (no telemetry source, no UI). Define a vision-derivable subset (impossible positions from minimap) + a review UI; tournament integrity is a paying-organizer feature.
10. **English voice (M13)** — English text pipeline is bilingual-ready; no English voice model is installed, so English would be spoken by the Hindi voice. One free Piper English voice + language routing closes it.

Honourable mentions (smaller): zone-efficiency metric (compute from existing ingredients); `leaderboard.update` / `insight.generated` topics; Kafka consumer-group docstring mismatch; final-zone hard lock; 3 s cooldown constant; stale `PlacementPoints` defaults; dead `llm_chain.py` (Anthropic/OpenAI references); `PointsEngine` vs `PointsCalculator` dual source of truth.

---

## Where the Project EXCEEDS the PRD (built, never asked for)

16 feature areas exist that the PRD never mentions (verified by grep — none of these terms appear in the PRD):

1. **Phase 19 — AI Storytelling section**: 7 story generators + director-permission-gated animated graphics (templates + animation-style switcher). The PRD's narrative ambition is one line; this is a shipped product.
2. **Phase 20 — WWCD% live prediction cards** (PMGO-style top-5) and **Smash Rule engine** (5-beat progression + verified fallback) — prediction features the PRD never specified.
3. **Phase 20 — Match-strip checklist + whole-day highlight reel** — tournament-day operations tooling beyond the PRD.
4. **Krafton-format standings suite + Visual Broadcast Bible**: MVP / Eliminator / Best IGL / Finals MVP / spotlight templates with ▲▼ movement arrows, built from measured analysis of 7 real Krafton/global streams. The PRD says "live overlays" once; this is a broadcast-grade graphics system.
5. **Tournament-scoped user management**: 6 roles, bulk 16-observer creation with printable credentials, tournament kill-switch.
6. **Spotter UI** (human kill/zone logger — the hybrid human-backup seat) and **observer-team mapping console**.
7. **Feed-adapter plug-in** (normalized-event contract — the real-game-feed path the PRD assumed but couldn't build).
8. **Team-analysis product + PDF dossiers** (rotation/drop/zone tracking, analyst dashboard, fpdf2 reports).
9. **3-mode director** (full_auto / semi_auto with suggestion queue / manual) with permission model.
10. **Distributed node cluster** (Redis node registry, multi-backend heartbeats).
11. **Automated multi-platform streaming service** (Chromium → FFmpeg → YouTube/Twitch/Facebook) — PRD mentions OBS only.
12. **19-file graphics template library** (scorebug/lower-third/killfeed/standings variants).
13. **Broadcast show-flow pack** (pre-show, run-of-show, award ceremony, map-cam/PiP, viewer page).
14. **Scrim Analytics Tool** (the PRD's sixth vision bullet — present as `/spotter` + analysis product + Discord/Sheets export path).

**Net assessment:** against PRD v2.0 as a *spec document*, the project is ~⅓ fully built, ~⅖ partially built, ~⅕ missing, with ~8% deliberately different (mostly zero-spend and corrections from real broadcast research). Against the PRD's *business intent* (autonomous BGMI production that sells to organizers), the misses that matter most are: rendered highlight export, commentary modes/dual-track, unwired CV detectors, and MVP/data consistency — all covered in the Top 10 above.

---

*Evidence basis: `docs/gap_audit_w1.md` (46 items, M1–M4) · `docs/gap_audit_w2.md` (33 items, M5–M8) · `docs/gap_audit_w3.md` (43 items, M9–M13) · `docs/gap_audit_w4.md` (43 items, tech stack / agents / DB / exceeds-PRD). All audits read actual code with file-path evidence; no invented features.*
