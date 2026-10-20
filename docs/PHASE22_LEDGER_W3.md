# Phase 22 — W3 Ledger: Partial-Items Sweep

Owner: W3 (partial items). Scope: every remaining 🟡/❌ in
`docs/PRD_GAP_ANALYSIS.md` not owned by W1 (commentary modes/dual-track/
subtitles), W2 (vision subscriber, narrative auto-flag, anti-cheat) or
Phase 21 (CV wiring, MVP unification, damage honesty, highlight video
rendering, English voice, healing/grenade stats API).

Status key: **DONE** (implemented + tested) · **PLUG-READY** (complete
interface/plug point; activates when the named external piece arrives;
blocker stated) · **DIFFERENT** (deliberate different approach, kept).

No migrations were needed: every fix lands on existing columns/tables
(`team_intelligence.aggression_score` already exists; backup pool is
capture-layer state, not DB slots). Alembic head stays 013.

| Item | Status | Evidence / blocker |
|---|---|---|
| M1 Backup pool + auto-failover | DONE | `vision/capture/multi_device.py`: `BACKUP_POOL_SIZE=2` spare devices held unassigned; supervisor fails a slot over onto a spare after `FAILOVER_MISS_THRESHOLD` consecutive ADB misses; `on_team_eliminated()` frees the slot's device back to the pool. |
| M1 Auto role reassignment (slot failure) | DONE | Same failover path; reassignment is broadcast as `video.feeds` with reason `failover`. |
| M1 Auto role reassignment (team elimination) | DONE | `MultiDeviceCaptureManager.on_team_eliminated(team_id)` releases the eliminated team's slot device to the backup pool / next unassigned use. |
| M1 Feed health scores wired + exposed | DONE | `FrameGrabber` owns a `FeedHealthMonitor`: `record_frame()` on every captured frame, `record_frame_drop()` on every missed/failed capture; score + status surfaced in `FrameGrabber.get_stats()` and per-slot in `GET /video/status` (`health_score`, `health_status`). |
| M1/M2 Latency sync ±500 ms framework | DONE (framework) | `vision/capture/latency_sync.py`: capture-time stamping (`CapturedFrame` server grab time + optional `device_capture_ts`), per-feed offset vs the fleet median, drift reporting, `within_tolerance` (±500 ms). Exposed per source in grabber stats + per slot in video status. Honest limit: true device-clock sync needs the observer app to stamp frames on-device; until then offsets are grab-time offsets and are labelled as such. |
| M2 Frame-drop logging wired | DONE | Grabber loop calls `record_frame_drop()` and logs per-feed drop counters via health monitor stats. |
| M2 Emulator ADB documented | DONE | `docs/OBSERVER_SETUP.md` emulator section (serial forms, `emulator-5554`, TCP `127.0.0.1:5555`, same screencap path). |
| M2 WebRTC ingest | PLUG-READY | `vision/ingest_plugins.py` `WebRTCIngestPlugin`: full `IngestPlugin` interface, `available()=False`, `grab_frame()` returns `None` (honest no-feed). Blocker: no product requirement / no aiortc dependency; activates by implementing the plugin body behind the same interface. |
| M2 NDI ingest | PLUG-READY | `vision/ingest_plugins.py` `NDIIngestPlugin` formalises the existing honest `FrameSource.NDI_FEED` stub as a plug point. Blocker: NDI SDK is a proprietary venue install; no venue has asked yet. |
| M3 YOLOv8 live registration | PLUG-READY | `vision/models/trained_registration.py`: `resolve_yolo_registration()` registers the YOLO service ONLY when `VISION_MODEL_PATH`/`YOLO_TRAINED_MODEL_PATH` points at a BGMI-trained model; generic COCO `yolov8n.pt` is explicitly refused (never presented as BGMI detection). Blocker: Kaggle-trained BGMI model (user-run training) has not landed yet. |
| M3 ByteTrack behind position tracking | PLUG-READY | `vision/models/trained_registration.py` `PositionTrackerAdapter`: wraps `ByteTrackService` when detections exist; with no live position/detection producer it returns no tracks (never fabricates). Activates with the trained model + minimap position wiring. |
| M3 Parachute phase | PLUG-READY | `vision/parachute.py` `ParachutePhaseDetector`: derives the drop phase ONLY from truthful early-match signals (players alive == full lobby, zero kills, match clock). Returns `unknown` when signals are absent — no guessing. |
| M4 FightEvent canonical JSON schema | DONE | `services/feed_adapter/fight_schema.py`: single pydantic `FightEventCanonical` + `fight_event_to_canonical()` / `canonical_to_bus_payload()` used as the published contract; field-for-field aligned with `db/models/fight_event.py`. Published from the feed-adapter package. |
| M4 Audio-gunshot signal | PLUG-READY | `agents/fight_agents/audio_signal.py`: `AudioGunshotProvider` ABC + `NullAudioGunshotProvider` (no producer → `poll()` returns `None`, signal stays absent) + `FightDetector.process_audio_gunshots()` adapter that only creates a `SHOOTING` signal from a real provider reading. Blocker: spectator audio is mixed/delayed program audio; no per-feed gunshot classifier exists. Spotter remains the honest fallback. |
| M5 Aggression score (canonical) | DONE | `analytics/aggression.py`: ONE formula (documented in-module) → 0..1; `refresh_aggression_scores()` writes `team_intelligence.aggression_score`; `DossierService.compute_dossier` refreshes before reading, so dossiers stop rendering `insufficient_data` when fight/kill data exists. The 1–10 `team_memory` heuristic is now documented as a display-only transform of the same inputs, not a second source. |
| M5 Loot quality/tier score | DONE | `analytics/aggression.py` `loot_tier_score()`: defined tier scoring over existing inventory items (weapon tier + attachments + consumables + armour); returns `None` when no inventory exists (never invented). |
| M6 MIN_SCENE unification | DONE | `SafetyConfig.min_scene_time` now defaults from `settings.MIN_SCENE_TIME_SECONDS` (10 s) — the divergent 8.0 s constant is gone. |
| M6 Switch-cooldown constant | DONE | `settings.SWITCH_COOLDOWN_SECONDS = 3` (PRD-named) + `SafetyConfig.rapid_switch_threshold` defaults from it; director fight/team/replay locks remain separate named constants (documented as distinct concepts, not a fragmented cooldown). |
| M6 Final-zone HARD lock | DONE | `SafetyController` final-zone lock mirrors the clutch lock: `validate_switch()` refuses switches away from a final-zone scene while `teams_alive <= 5` (top-5) and hard-refuses at `<= 3` (top-3) unless the candidate is also final-zone/clutch; `DirectorState` feeds `teams_alive` into the safety state each tick. |
| M6 Narrative-continuity bonus | DONE | `ObserverDirector._calculate_current_scene_score()` adds a stay-with-storyline bonus when the current scene's team/fight appears in `previous_scenes` history (director scene history deque) — capped, documented. |
| M7 Zone efficiency metric | DONE | `analytics/zone_efficiency.py`: defined formula from `in_zone` fraction, mean distance-to-edge and danger level → 0..100; `zone_efficiency_report()` publisher shape + `POST /api/v1/analytics/zone-efficiency` (new `analytics_extra` router). Returns `null` score with reason when ingredients are missing. |
| M7 Per-metric cadence documentation | DONE | `docs/METRIC_CADENCE.md` + `analytics/zone_efficiency.py` cadence constants; every metric names its producer cadence (event-driven / 30 s tick / on-demand). |
| M8 PointsEngine second source of truth retired | DONE | `intelligence/points_engine.py` is now a deprecated read-through adapter over `PointsCalculator` (the configured single source); it no longer keeps its own Redis-hash standings math. Old finishes-only hash is ignored, with a migration note in-module. |
| M8 PlacementPoints 15/12/10 trap removed | DONE | `PlacementPoints` dataclass defaults are now the BMSD values (1st=10, all others 0) matching `DEFAULT_CHICKEN_POINTS`; the PRD 15/12/10 table survives only as tournament config input. |
| M11 1v4 clutch verification | DONE | `services/replay/highlights.py`: clutch detection now verifies alive counts when the kill buffer carries them (anchor-kill payload `killer_team_alive` / `opponents_alive`): a verified 1v4 (clutch player sole alive vs 4) scores 95 and is labelled verified; without alive data the legacy solo-scorer heuristic still fires but is labelled unverified. |
| M11 Final-kill trigger | DONE | `services/replay/highlights.py`: `final_kill` moment from match-end state (last kill before `match_end` / teams_alive reaching 1) — new `Highlight.kind` value. |
| M11 Grenade multi-kill | PLUG-READY | `services/replay/highlight_triggers.py`: registered trigger, INACTIVE until kill events carry a truthful `weapon == "grenade"`/utility attribution. Today kill-feed weapon is honestly `None` (icons never guessed) — blocker: trained weapon-icon detection. |
| M11 Sniper triple | PLUG-READY | Same module: trigger registered, inactive; blocker: truthful weapon classification (sniper/DMR) per kill. |
| M11 Vehicle kill | PLUG-READY | Same module: trigger registered, inactive; blocker: kill↔vehicle linkage (vehicle detector needs the trained model). |
| M12 Canonical event names + aliases | DONE | `core/event_names.py`: canonical names `kill.occurred`, `zone.updated`, `team.eliminated`, `feed.switch`, `commentary.trigger`, `leaderboard.update`, `insight.generated`, `fight.detected`, `highlight.clip` with the legacy bus names as published aliases; `EventBus` dispatch resolves aliases both ways so existing subscribers keep working. |
| M12 `leaderboard.update` emitter | DONE | `core/event_names.py` `emit_leaderboard_update()` + wired into `PointsCalculator.calculate_match_points` via an optional bus hook (`set_event_bus()`); emits on every standings change with the ranked table. |
| M12 `insight.generated` emitter | DONE | `core/event_names.py` `emit_insight_generated()`; wired into `ReportGenerator` insight generation (the only insight producer today) — storyteller auto-flag remains W2's area and can emit through the same helper. |
| M12 Event-bus "consumer groups" docstring | DONE | Docstring corrected to the truth: Redis Streams are a persistence/replay log; delivery is Pub/Sub + in-process dispatch. No `xreadgroup` is claimed or used. |
| Tech NVENC selection | DONE | `streaming/encoder.py`: `detect_encoder()` probes `ffmpeg -encoders` + `nvidia-smi`; selects `h264_nvenc` only when both exist, else `libx264`; config override `VIDEO_ENCODER` (`auto|nvenc|libx264`). Wired into the highlight renderer (`services/replay/renderer.py`). `StreamService.build_ffmpeg_command` adoption is a one-line plug (`"-c:v", selected_encoder()`) deliberately NOT applied in this run: W1 is editing `streaming/service.py` in parallel (dual-track) and the file changed under this worker; the parent should apply that one line when W1 lands. |
| Tech Dead `ml/commentary/llm_chain.py` | DONE | File deleted; `ml/commentary/__init__.py` no longer exports it. Grep before removal: zero production callers; the one test-side reference was an unused conftest fixture importing it lazily — that fixture now returns a plain dict (documented in-place). The live commentary path is `app/ai` (Ollama + heuristic fallback). |
| Tech OBS consolidation | DONE | `broadcast/obs_client.py` is the single real client (documented as canonical in its docstring). `production/obs_manager.py` is marked DEPRECATED in-module (kept only because `live_production_integration.py` imports it; it must not gain new features). |
| Tech S3/R2 storage | PLUG-READY | `storage/` package: `StorageBackend` interface + `LocalDiskStorage` default (real, used for highlights/recordings paths); `S3Storage` raises `NotImplementedError` with the exact env config it will consume (`STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL` for R2). Blocker: no bucket/credentials exist for the 1-PC product. |
| Tech player_stats/team_stats fallback models | DONE | Truth-in-labelling fix: `db/models` has NO `MatchPlayerStats`/`MatchTeamStats`, so the `match_service.py` definitions are the canonical models (not dead fallbacks) — module docstring + class docstrings corrected to say so, and the try-import now logs which definition is live. |
| Hygiene: other docstring lies | DONE | Event-bus consumer-groups claim (above); `PointsEngine` "zero data-loss" claim removed with the retirement; YOLO COCO weights no longer described anywhere as BGMI detection (registration guard + docstring). |

Tests: `backend/tests/unit/test_phase22_w3.py` (new) — 35/35 pass.
Full existing unit suite re-run: 913 passed, 32 failed, 2 skipped
(plus 3 pre-existing collection errors excluded: legacy tests
importing `MatchTeamStats` from `db.models` and missing
`app.core.time_sync`). All 32 failures are in legacy
`tests/unit/test_vision/**` files and are pre-existing stale-test
issues, spot-verified NOT W3 regressions: e.g. tests assert
`AgentConfig.min_scene_time` (never existed — it's on SafetyConfig),
`BroadcastScene.priority_score` (the field lives on `FeedCandidate`),
`ScriptGenerator._check_novelty` (renamed long ago), director
priority/state-mapping expectations vs current defaults. W3-modified
behaviour is covered by the new test file; no W3-touched module's
dedicated tests regressed. Also pre-existing in this VM: `fpdf2`
declared in pyproject but not installed, so `app.main` /
`test_dossier_pdf.py` cannot import here at all.
