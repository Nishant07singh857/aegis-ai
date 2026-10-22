# DIRECTOR DECISION BLUEPRINT — External Spec vs AEGIS Reality

**Date:** 2026-10-21 · **Type:** Evaluation only — nothing built, nothing committed.
**Input:** User-pasted external design spec, "Automated AI Broadcast Director"
(16 Team Agents + 2 Macro Agents → Central AI Show Director, 3-second lookahead
buffer, Interest Score S_i, 30-minute show flow).
**Judged against:** (1) the user's standing **SPECTATOR-ONLY** directive — no game
API/telemetry exists, ever; AEGIS inputs are vision events from spectator screens,
spotter input, and the feed adapter · (2) frame-verified broadcast grammar —
`docs/VISUAL_BROADCAST_BIBLE.md` + `docs/DEEP_ANALYSIS/MASTER_PATTERNS.md`
(7 streams) · (3) `docs/SPECTATOR_PRODUCTION_RESEARCH.md` · (4) the actual code —
every "we have it" below was verified in the repo by an independent code audit,
not copied from phase summaries.

**Verdict key:** ✅ HAVE (in AEGIS, live-wired, location named) · 🔧 BUILDABLE-ADAPTED
(spectator version possible, sketch given) · 🚫 TELEMETRY-ONLY (impossible from a
spectator feed; honest alternative named or none) · ⛔ CONTRA-GRAMMAR (real
PMGC/Krafton broadcasts do not do this — doing it would make the show *worse*;
Bible rule cited).

---

## 0. The headline finding (bigger than the spec)

AEGIS today has **three director stacks, and the real vision feed is not wired to
any autonomous camera switch**:

| Stack | Where | Status |
|---|---|---|
| `DirectorService` — state store (mode / scene / overlays / `active_camera`) | `backend/app/services/director_service.py` | **LIVE** — output page renders its state. Contains **no decision loop**; it stores decisions made elsewhere. |
| `DirectorAIEngine` | `backend/app/intelligence/director_ai.py` | Wired **only to the DEMO_MODE simulator** in `main.py`. Its `SWITCH_POV` never passes `active_camera` — even in demo full-auto, the AI does not actually switch the video slot. |
| `ObserverDirector` (+ `POVSelector`, `SafetyController`, `PlaybookManager`) | `backend/app/agents/observer_agents/director_ai.py` | **Zero live callers.** `AgentOrchestrator` is never instantiated by `main.py`. The rich logic — 45 s fight hold, score-gap margins, staleness, final-zone lock, narrative continuity — is dead code in the running process. |

Consequence, stated plainly: **a real kill on a spectator screen today reaches
LiveStats, the replay buffer, the derivations and the story flagger — and no
camera switch happens.** Kill→switch on the real path is not slow; it is absent.
The spec's core demand ("16 agents feed a central director that decides") is
therefore AEGIS's #1 real gap — not because the parts don't exist (most do), but
because they were never unified into one live decision path. That unification is
Phase 25 item P0-0 (§4) and everything else in this blueprint hangs off it.

Two smaller wiring warts found by the audit, recorded for the unification work:
the playbook returns action names (`"switch_to_fight"`, `"show_overview"`) that
`_execute_playbook_action()` does not handle (they silently fall through yet still
suppress the decision loop), and `FightDetector` publishes `"fight.detected"`
while `ObserverDirector` subscribes `"fight_detected"` — the bus alias layer for
that pair was not confirmed (moot today, must be verified during unification).

---

## 1. Spec mapping — every element, judged

### 1a. Architecture

| # | Spec element | Verdict | Spectator truth / where it lives in AEGIS |
|---|---|---|---|
| A1 | 16 Team Agents, one locked per squad | 🔧 | The rig has 16 observer slots with DB slot→team attribution (`services/vision_intelligence/attribution.py` — unmapped events are **dropped, never guessed**), and per-team derivations run per bus event. **But vision *analysis* still runs on one configured slot** (SPECTATOR_COVERAGE row 2). The adaptation — parallel analysis on all 16 feeds — is research P0 and Phase 25 P0-1. |
| A2 | Per-squad micro-camera selection (agent auto-picks most active player / overhead) | 🔧 | AEGIS does not control in-game cameras — the observer device *is* the camera. `POVSelector` candidate scoring exists (fight +30, clutch +25, star/rivalry terms) but sits in the unwired stack. Spectator version: a *recommendation* layer telling the director/observer which slot+player to feature. |
| A3 | 100 ms agent loop, 100 ms JSON telemetry packets | 🚫 | No 100 ms telemetry exists from a screen. Vision runs event-driven at ~2.3 FPS on CPU (capture target 8 FPS, detectors staggered by frame-skip). Honest version — already the design: lightweight state packets **on change** over the event bus, not on a 100 ms clock. |
| A4 | Engagement states IDLE / LOOTING / ROTATING | 🔧 | Pieces exist: match phases (`services/phase_tracker.py`), per-team rotation state (`RotationTracker`), team-status transitions. A single per-team engagement-state enum fused from these is buildable. |
| A5 | PRE_COMBAT (enemy <150 m **+ line-of-sight converging**) | 🔧 (LOS part 🚫) | Line-of-sight is never rendered on a spectator screen — telemetry-only. The convergence half is real: `FightDetector` already keeps a `PotentialFight` state promoted at confidence 0.7 (weights: kill feed .40, HP drop .25, knock .20, proximity .15, alive delta .15, utility .10…). Proximity is minimap pixels today; metres are never faked without a calibrated scale (Limitations L3/L4). |
| A6 | ACTIVE_COMBAT state | ✅ | Promoted fights in `FightDetector`, consumed by derivations, story flagger and the (unwired) director logic. |
| A7 | CLUTCH_SITUATION state | ✅ | Clutch detection is live in the highlight path (verified 1vN from alive counts → score 95; unverified → 75). Clutch *locks* exist in the demo engine (live-in-demo) and `SafetyController` (unwired). |
| A8 | 2 Macro Agents (Map View, Free Cam) | 🔧 | Map view exists as the `map_cam` scene with a hard 30 s max hold enforced in 4 layers (`DirectorService.enforce_scene_holds()` + 5 s lifespan ticker + state-read enforcement + frontend re-poll). There is no autonomous "free cam agent" — and no full-map god view to feed one (L4: one feed = one player's local minimap). Macro is a *scene*, not an agent. |
| A9 | Central Director = Observer Director + Technical Director in one | 🔧 | The role is split across the three stacks in §0. Unification = Phase 25 P0-0. |

### 1b. The core mechanism

| # | Spec element | Verdict | Spectator truth |
|---|---|---|---|
| B1 | **3-second lookahead buffer** — delay PGM 3 s so the director "sees the future", switches *before* a knock | 🚫 | Physically impossible as described. The spectator feed is *itself* delayed behind the game by an unpublished amount (research §3.1: no published BGMI spectator-delay number exists; documented 3–5 min delays live at the *stream* layer, a different thing). You cannot look ahead of a delayed feed into the game's future — a buffer only delays the output further. **Honest alternative (sourced craft):** anticipation = the *10-second lead* — pre-positioning from zone × rotation tendencies × POI homework (research §1.4, ALGS-sourced), which AEGIS computes as convergence signals (A5) + Phase 24 derivations. |
| B2 | Interest Score S_i = W × (α·PreCombat + β·ActiveDamage + γ·LookaheadKnock + δ·ZonePressure) | 🔧 | Every term except one has a real spectator source: PreCombat → PotentialFight confidence (A5); ActiveDamage → taken-side HP-drop velocity, 2 s window, ≥20 HP (`FightDetector`) — *dealt* damage per player is L1-impossible and stays null; ZonePressure → per-team distance-to-edge/centre + urgency bands (`geometry.zone_distances`, Phase 24, verified). **LookaheadKnock 🚫** — future knockdowns are unknowable; the honest substitute is knock *recency* + potential-fight confidence. W → §1c/C5. Three partial scorers exist today (demo engine: lexicographic; `POVSelector` + scene score: unwired) — one unified S_i is Phase 25 work. |
| B3 | Score recomputed every 100 ms | 🚫 | Cadence fantasy. Real cadence: per bus event, at vision FPS. Decisions in AEGIS are event-driven; nothing is gained by re-scoring unchanged state 10×/s. |

### 1c. Director rules

| # | Spec element | Verdict | Evidence |
|---|---|---|---|
| C1 | Context-First: new fight → 2.5 s Free Cam establishing → hard cut to entry fragger POV | 🔧 | The *principle* (a brief establishing/panning beat punctuates POV switches) is sourced craft (research §1.2); the *2.5 s free-cam prescription* is **UNVERIFIED** — no Bible/research rule states it, and the Bible shows direct hard cuts to third-person when action starts. AEGIS today: **absent as a sequence** (fight/clutch handlers switch directly). Buildable as an establishing beat at grammar durations — noting the "free cam" would be the map/standby scene, not a camera AEGIS owns. |
| C2 | Hysteresis: min dwell **4.0 s** (2.5 s endgame) | ⛔ | **U10** (7/7, frame-timed): early 15–30 s · mid 45–90 s · late/final-4 60–120 s · final circle *stay until it ends*. The spec's dwell is 4–30× too short, and backwards: the endgame demands the *longest* holds, not the shortest. AEGIS's own constants sit between spec and grammar (45 s fight hold in the unwired stack; 10 s minimum scene in the demo engine) — the grammar numbers govern. |
| C3 | Challenger must beat current by **+25%** to interrupt | 🔧 | The margin *concept* exists — as **absolute points**, in the unwired stack: gap 40 inside the fight-hold window, default 15, clutch challenger 5, stale scene 10; switch only if `best − current ≥ min_difference`. Percent-vs-absolute is an implementation choice; port the absolute version during unification. |
| C4 | Triage: 1 fight >75 → full PGM | 🔧 | Matches single-fight commitment (U9) in spirit. Threshold mechanics differ — AEGIS scores are not the spec's normalized 0–100 (only `POVSelector` clamps 0–100). |
| C5 | Triage: 2 fights >75 → PGM 75% + **PiP 25%** | ⛔ | **U9** (7/7): "No PiP of a second live fight, no split-screen, no replay DURING a live fight — in any stream." Fight+fight PiP was never observed once in 7 streams. The grammar-true protocol for the second fight: it is *not lost* — its events land in the replay buffer, live stats and story flagger — and its replay is offered in the next lull (§2, step 5). AEGIS's PiP service additionally makes fight+fight PiP unreachable by construction (its source list deliberately excludes fight feeds) — though note honestly: there is **no active auto-suppress rule** if an operator has a non-fight PiP up when a fight starts; the exclusion is by keyspace, not by fight state. |
| C6 | Triage: 4+ spikes → **quad split** / macro free cam for 6 s | ⛔ / 🔧 | Quad split during fights: ⛔ (U9 + Bible §11.1 — split-screen exists only for tactical *analysis* segments; the only 2×2 ever observed is a **facecam grid** on map/final-circle views — which AEGIS's `MapCamView` already renders). The macro-overview half is 🔧: an elevated map beat is grammar-legal *between* fights, and India's real final-circle pattern is aerial + top-4-only panel + 2×2 facecams. |
| C7 | Post-fight: hold winner 1.5 s → check Replay Queue → **stinger** → 6 s replay | 🔧 | Post-fight replay in a lull **FITS** (U12: replays air *between* fights or during lulls, never over a live second fight). The stinger is the one legal stinger in the whole spec — research §2.3 replay packaging ("branded stinger-in → replay → stinger-out → live"); everywhere else in gameplay, stingers are ⛔ (U8). Missing in AEGIS today: the **queue and offer/take discipline itself** — the live replay service has no pending/offered/taken states and no director approval; `POST /replay/play` re-broadcasts immediately. The 1.5 s / 6 s figures are unverified specifics (India's aired replays were full fight packages; PMGC's product is a day-end reel). Buildable per research §2.3 = Phase 25 P0-2. |

### 1d. Cameras & production terms

| # | Spec element | Verdict | Evidence |
|---|---|---|---|
| D1 | PGM (Program) | ✅ | `DirectorService` state is the program truth; the output page renders it (scene + overlays + `active_camera`). |
| D2 | PVW (Preview) — pre-position next feed 2 s before the cut | 🔧 | **Absent today**: no preview field in director state, no PVW route, no preview type in the frontend; the director page is a single program console and the output renders one video slot. The semi-auto *suggestion queue* is a decision-preview analog, not a feed preview. Buildable as a true multiview (research P1 already flagged it) = Phase 25 P0-3. The "2 s before" precision is garnish — pre-positioning happens when a candidate promotes, not on a countdown. |
| D3 | POV | ✅ | Observer POV feeds are the rig's input; POV candidate scoring exists (unwired stack). |
| D4 | Free Cam / drone as a controllable camera | 🚫 | AEGIS captures screens; it does not own a camera inside the game client. `CinematicController` exists as a state model but applies no live zoom/pan to the output. Scene-level substitutes (map cam, cinematic scene types) are the honest version. |
| D5 | Follow / Trace Cam — grenade follow-cam on physics prediction | 🚫 | No projectile tracking is possible from a spectator feed (Phase 23 measured this; `docs/GRENADE_TRAJECTORY.md`). What exists: an *estimated* endpoint-arc engine, `estimated: true` structurally enforced, analyst-map rendering — which **never fires in production** because no feed pairs a named thrower position with an explosion position in one coordinate space. It fabricates nothing, and it is a graphic, not a camera. |
| D6 | PiP (as a tool) | ⛔ in fights / ✅ as non-fight inset | Live as manual layout state (`services/pip/`, `/broadcast/output-pip`) with non-fight sources only (map-cam, standings, killfeed, commentary). During live fights ⛔ per U9 — see C5. (Honest note: the output-pip page's main "output" slot is itself unwired today — it renders a "Connect output source" placeholder.) |
| D7 | Dual / Quad split | ⛔ in fights | Legal only in analysis segments (Bible §11.1) and as the facecam grid (C6). `_switch_feed()` actively refuses `FeedType.SPLIT_SCREEN`. |
| D8 | Cut vs Stinger | ✅ cuts / ⛔ stingers in gameplay | **U8** (7/7): hard cuts only; zero wipes/dissolves in any gameplay edit. AEGIS enforces this — `CinematicController.get_transition()` always returns `CUT`. Stingers survive only as break bumpers and replay packaging (C7). |
| D9 | EVS — 15 s rolling **video** buffer on all 16 feeds | 🚫 as video / 🔧 adapted | The live replay service states it in its own docstring: "this system has NO video DVR"; a "clip" is a window of structured events (`is_video: False`). What exists: a 300 s rolling **event** buffer (50 k cap) feeding highlight detection, plus an FFmpeg renderer that makes real 16:9 / 9:16 / GIF clips **only when an operator recording exists** in `RECORDINGS_DIR` (with sidecar timing — offsets are never guessed). Continuous ISO recording of all feeds is the adaptation = Phase 25 P0-2. |

### 1e. The 30-minute flow (spec beats)

| # | Spec beat | Verdict | AEGIS / grammar position |
|---|---|---|---|
| E1 | Plane path: 100% map view, flight vector, team logos on trajectories; standings sidebar | ✅ | Grammar U13 (flight-path opener, 7/7) + U1 (persistent right-side standings panel). AEGIS: `FlightPathService` (director-set, labelled `source: "director-set"` on every surface) + predicted drops from recorded history + preshow flow. Logos "on trajectories" is presentation garnish on the same data. |
| E2 | **Drop Clash Alert** (2+ teams within 300 m of the same compound) | 🔧 | Predicted drops exist; contested drops today surface only as fields in `GET /flight/predicted-drops` — **no alert, bus event or director notification exists**. Buildable on the corridor math; "300 m" needs a calibrated map scale (normalized corridor units exist; metres are never faked). Phase 25 P1-6. |
| E3 | Hot drop → lock onto contesting teams | 🔧 | Fight logic owning the early game is the design intent; the phase suggestion plan (`phase_suggestion_plan()`) exists but `propose_phase_suggestions()` has **zero callers** — not wired. |
| E4 | Peaceful loot → cycle Top-4 teams, 15 s each, for caster narrative | 🔧 | 15 s sits exactly at the grammar floor for early holds (U10: 15–30 s) — legal, if treated as a floor not a metronome. Narrative cycling is buildable on the phase-suggestion path (E3). Team profile lower-thirds ✅ exist (graphics library + storyteller). |
| E5 | Zone reveal → **force-cut to map for 8 s** | ⛔ as specified / 🔧 adapted | U11: map-cam holds are 15–30 s *between fights*; an 8 s forced cut is below the measured hold and, if it interrupts a live fight, breaks U9. Adapted version: zone reveal fires the (verified, once-per-change) zone-pull winners/losers snapshot and a map *suggestion* — taken between fights, held 15–30 s. |
| E6 | Live rotation lines on the 2D map | 🔧 | `RotationTracker` computes bearings, speed, path history per team (verified). Rendering lines on the map view is a UI build; it inherits the position-source limit (minimap pixels today; full-map lines wait on the map position producer). |
| E7 | Compound crash → cut **3 s before** impact (collision-vector prediction) | 🚫 timing / 🔧 signal | "3 s before" is the lookahead fantasy again (B1). The honest precursor signals exist — rotation tracking + potential-fight promotion + gatekeep evaluation give seconds-scale anticipation, labelled inference. |
| E8 | H2H win-probability banner on a crash | ✅ pieces / 🔧 trigger | Win-probability service (`/predictions/win-probability`, WWCD% cards), storyteller head-to-head generator, animated H2H graphics — all live. Wiring the *trigger* to a predicted crash is the buildable delta. |
| E9 | Chaos phase: PiP at score >80; projectile trace cam | ⛔ / 🚫 | C5 and D5. The simultaneity problem is real — its fix is parallel monitoring + replay recovery (F1), not a second live fight in a box. |
| E10 | Teams Alive / Players Alive counter locked to top bar | ✅ | Grammar U3/U7; alive-count reader is live and the count is plumbed to the output top bar (`GameTopBar` via the observed snapshot). |
| E11 | Phase 6+: replays **disabled** so live action is never interrupted | 🔧 | Grammar-aligned (U12 already forbids mid-fight replays; this adds belt to braces for lulls). **Absent today** — the live replay path has no phase check. Small rule once the queue exists (P0-2). |
| E12 | Live virtual points table in sidebar, updating on every elimination | ✅ (sidebar form) | India does exactly this (U1 + research §2.1: WASP3D tables updated instantly from a live DB). AEGIS: `LiveStatsService` accumulates the real kill-stream and persists provisionally (manual/post-match rows always win; `damage_dealt` deliberately never written — L1); `GET /standings/live` is labelled `live: true, provisional: true` in every payload. A *fullscreen* table interrupting late play on every elimination is **not** observed in the grammar — sidebar is the correct form. |
| E13 | Final: split-perspective + high-ground free cam + star POV; hold winner 5 s | ⛔ split / 🔧 rest | Split during the final fight ⛔ (U9). The real final-circle pattern (India Day 3) is aerial + top-4-only panel + 2×2 facecam grid — AEGIS's `MapCamView` already has that shape. The 5 s winner-hold figure is **UNVERIFIED** — the ceremony sequence (U14) runs minutes, with WWCD team stats at ~30–60 s (Bible §5). |
| E14 | WWCD graphic → MVP comparison → championship leaderboard | ✅ | WWCD ceremony, MVP/Eliminator/IGL boards, storyteller comparisons and the standings suite are all built and pushed. |

### 1f. "Beat Krafton" claims

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| F1 | **Zero killfeed casting** — switch to the sniper's scope **1.5 s before** the trigger pull | 🚫 mechanism / 🔧 goal | Nobody can switch before an event that hasn't happened (B1). The *problem* is real and named by research: simultaneity is the #1 documented observer failure (Game for Peace field study), and its structural fix is that **the killfeed renders on every feed** — parallel UI monitoring on all 16 feeds catches fights the camera never showed (research §0.6, §6-P0.2). AEGIS monitors the killfeed on the analyzed slot today; 16-feed parallel analysis is the gap (A1, P0-1). Fast promotion after the first signal — not precognition — is the honest version of this claim. |
| F2 | **Physics-verified grenade tracking** — follow-cam only if >70 damage *guaranteed* | 🚫 | No projectile, no trajectory, no damage guarantee exists in a rendered feed (L1). Alternative: utility events + labelled estimated arcs (D5). |
| F3 | **Team-comms listen-in** (10 s, profanity-filtered, casters ducked −6 dB) | 🚫 | In-game comms are not a spectator signal (limitations register; research §2.4 documents production IFB/talkback only — the crew's own channel, not the players'). AEGIS has no comms source at all (`player_comms: False` even in demo state). No honest spectator alternative exists; venue-side team comms are a rights/production matter, not a feed. |
| F4 | 16 simultaneous fan POV streams with per-team smart switching | 🔧 (deferred) | Multi-platform program streaming exists (Phase 5). Sixteen per-team streams need 16 capture+encode chains and in-client camera control for the "smart switching between its 4 players" part (🚫, D4). A streaming-infrastructure project, not a director feature — explicitly deferred in §4. |
| F5 | Synchronized audio ducking (−6 dB under comms/replay) | 🔧 | Ducking code exists — `broadcast/audio_mixer.py` (`duck_for_commentary()/duck_for_replay()`) — with **no caller in the live audio path**; the live mixer holds volumes only. Wiring it for replay/commentary moments is a small honest build (P2-8) — for AEGIS's own commentary, not for a listen-in that can't exist. The profanity filter, incidentally, is ✅ live — in the *commentary* path (`audio/profanity.py`, step 1 before TTS/display), which is where it can truthfully operate. |

**Scorecard (44 rows):** ✅ HAVE 9 · 🔧 BUILDABLE-ADAPTED 22 · 🚫 TELEMETRY-ONLY 8 ·
⛔ CONTRA-GRAMMAR 5 (two further rows, C6/E13, are ⛔/🔧 splits counted under 🔧).
The spec's instincts — anticipate, don't whiplash, never lose a fight, feed the
director structured state — are right. Its physics (lookahead, trajectories, LOS,
comms) belongs to a game-API world that does not exist for BGMI, and three of its
signature moves (fight PiP, splits, 4 s dwell) would make the show measurably
worse than the PMGC broadcasts it wants to beat.

---

## 2. Director decision walkthrough — how a switch actually happens in AEGIS today

Written in broadcast terms, because that is the language the decision lives in.
**PGM** = `DirectorService` state (scene + overlays + `active_camera`) rendered by
the output page. **The TD** = whoever writes that state. **PVW** = does not exist
(§1 D2) — the closest analog is the semi-auto suggestion queue, a preview of a
*decision*, not of a *feed*.

### Step 0 — The feed becomes events (the only inputs that exist)
A spectator screen is captured (`VisionCaptureService`, target 8 FPS, ~2.3 FPS
budget on CPU; detectors staggered — HP every 2nd frame, utility/healing every
3rd, minimap/zone/loot every 5th, emission on change only). Ten detectors turn
pixels into events: killfeed OCR + icon matching, HP bars, alive count, zone
timer, team status, minimap, utility, healing, loot. Events cross the feed
adapter onto the bus as `NormalizedEvent`s. **This — plus spotter input — is the
entire sensorium. There is no 100 ms telemetry because there is no telemetry.**

### Step 1 — Attribution before interpretation
`VisionIntelligenceService` attributes every event by observer slot → team
(DB mapping, 5 s cache). Unmapped slot or no slot → the event is **dropped and
counted, never guessed**. (Zone events are match-global and exempt.) Nothing
downstream ever sees an unattributed guess — this is the load-bearing honesty
rule the spec's clean JSON packets take for granted.

### Step 2 — The event fans out to four live consumers (none of them a camera)
1. **Fight detection** — signals accumulate into `PotentialFight`; at confidence
   0.7 it promotes to a fight (kill feed .40, HP drop .25, knock .20, proximity
   .15, alive delta .15, utility .10…).
2. **Derivations** (Phase 24, verified) — third-party (≥3 distinct killer teams
   in a 60 s cluster window, edge-triggered), fight heat (per-area, 60 s
   half-life), gatekeep (labelled inside position + fight team outside the edge
   band, else silence), rotations (bearing/speed/early-late/urgency — urgency is
   `null` when no zone timer was ever read, never estimated), splits/isolated
   players, knock sessions, zone-pull winners/losers on each new reveal.
   These are emitted as bus `intel_alert`s and REST endpoints — **and no
   component consumes `intel_alert` today.** The director never sees them.
3. **Story flagger** — qualifying moments auto-generate story graphics
   (head-to-head, comeback, zone advantage, milestones): in full_auto they air;
   in semi_auto/manual they wait in the approval flow. Graphics only — it never
   touches scene, camera or priority.
4. **Live stats** — finishes/knocks/headshots accumulate from the kill-stream
   and persist provisionally; `/standings/live` updates the sidebar (E12).

### Step 3 — So who switches the camera today? Three honest answers
- **A human operator**, through the director console, inside the mode rules:
  full_auto locks operators out of scene/camera writes (HTTP 409 — only
  `system_publish` writes); semi_auto routes AI output into a suggestion queue
  (60 s TTL, approve/reject endpoints); manual makes operator writes
  authoritative.
- **The demo simulator** (only when `DEMO_MODE=1`): fabricates a fight event
  every 3–8 s and feeds `DirectorAIEngine`, which filters confidence ≥0.85 and
  sorts lexicographically by (priority, emotion, confidence) — *not* a weighted
  sum — then emits `SWITCH_POV`, subject to a 10 s minimum scene time and an
  8-switches/minute cap (CRITICAL bypasses the 10 s rule, not the cap; an active
  clutch rejects non-CRITICAL challengers). Even here, the switch publishes a
  *scene*, never `active_camera` — the video slot itself does not move.
- **Nobody, on a real feed.** A real promoted fight produces intel, stories and
  stats — and no switch. This is the gap §0 names.

### Step 4 — The decision that exists on paper (the unwired `ObserverDirector`)
For the record, because Phase 25 ports from it: a 200 ms tick runs
context update → broadcast-state update → `SafetyController.can_switch()` gate →
playbook → decision. The decision compares the current scene's score against the
best candidate: base 50 (or fight intensity), clutch +25, priority team/player
+15 each, **narrative continuity +4 per recent appearance of the same team/fight,
capped +12**; staleness decays the current scene after 90 s (fight) / 30 s
(other) at −0.5/s. A switch needs an absolute margin — 40 inside the fight-hold
window, 15 default, 5 for a clutch challenger, 10 if the current scene is
stale. Safety adds a 3 s switch cooldown, the same 8/min cap, clutch and replay
locks, and a **final-zone hard lock** (top-3: cannot leave a final-zone scene;
top-5: 12 s stability first). Its fight "hold" check is boolean (fight still
active) rather than a true 45 s timer comparison — a wart to fix in the port,
not a behaviour to copy blindly. Note what even this scorer never uses:
**standings.** No rank, no points — the spec's W has no ancestor in the code.

### Step 5 — Two fights at once, without PiP (the grammar-true protocol)
1. **Commit** to Fight A on PGM (U9, single-fight commitment; holds per U10).
2. Fight B is **not invisible**: its kills land in the replay buffer, live
   stats, fight heat and the story flagger in real time. The casters' data
   never misses B even when the camera does.
3. When A resolves (or a genuine lull opens), B's packaged replay is **offered**
   — "replay ready" in the preview row — and the director **takes** it
   (research §2.3 discipline). Today the offer/take states don't exist
   (C7/P0-2); `POST /replay/play` fires immediately, which is exactly the
   unprofessional failure mode the discipline exists to prevent.
4. If B is *still live* when A ends, the cut goes to B — pre-positioned (P0-3),
   with an establishing beat (C1), not into a box beside A.

### Deltas vs the spec, in one table

| Dimension | Spec | AEGIS reality |
|---|---|---|
| Loop | 100 ms telemetry | Event-driven at vision FPS (~2.3 FPS CPU analysis) |
| Foresight | 3 s lookahead buffer | Convergence anticipation (PotentialFight promotion, rotation/zone derivations) + 10-second-lead craft |
| Score | One formula S_i | Three partial scorers (demo lexicographic · unwired weighted · fight-promotion weights); standings in none |
| Dwell | 4.0 s / 2.5 s | Grammar 15–120 s by phase; code constants 45 s fight hold (unwired), 10 s min scene (demo) |
| Margin | +25 % | Absolute points 40 / 15 / 5 / 10 (unwired) |
| Second fight | PiP box | Commit + buffer + offered replay (U9/U12) |
| Cameras | Owned (POV/free/trace) | Captured screens; scenes, not cameras |
| Replay | EVS video buffer | Event buffer + renderer over operator recordings; no DVR |

---

## 3. The full ~30-minute match show — the spectator-truth version

Phases follow AEGIS's own `PhaseTracker` (`plane, parachute, loot, early,
mid_game, late_game, final_circle`) over a typical 28–32 min Erangel/Miramar
match. Camera logic uses only ✅ features or items marked 🔧 with their Phase 25
ref. Holds are the grammar's (U10), not the spec's.

| Time / phase | In-game reality | AEGIS camera logic | Graphics & story triggers |
|---|---|---|---|
| Pre-show (before T-0) | Lobby, desk, anticipation | Preshow flow; director sets the flight path (labelled director-set) | Overall standings sidebar; team profiles; **predicted drops** along the corridor |
| 00:00–02:00 · **Plane** | Plane crosses; 64 players jump | 100% map view — the grammar's opener (U13). Flight vector + drop prediction overlay. Holds are scene holds, not cuts | **Drop Clash Alert** 🔧 (P1-6) when 2+ teams' predicted drops share a POI; standings sidebar persistent (U1) |
| 02:00–06:00 · **Parachute → Loot** | Hot drops clash; most teams loot quietly | Hot drop: fight logic takes PGM and *commits* (U9). Peaceful: narrative cycle across top teams at 15–30 s holds 🔧 (E4) — casters get story time, viewers get context | Team profile lower-thirds (rank, points, fragger); drop outcomes recorded for the corridor history |
| 06:00–11:30 · **Early** (zone 1–2) | First circle shifts; vehicle rotations; bridge/gatekeep setups | Zone reveal → zone-pull winners/losers computed instantly; map beat 15–30 s **between** fights 🔧 (E5), never a forced 8 s cut through a fight. Rotation intel follows high-risk rotations; gatekeep alerts arm the director *before* the push | Rotation lines on map view 🔧 (E6); zone-advantage stories auto-flagged for approval |
| 11:30–16:30 · **Mid game** (zone 3–4) | Compound crashes, utility exchanges, 4v4 breaches | Convergence does the anticipating: PotentialFight promotion + rotation-vs-compound signals pre-position the next feed (PVW 🔧 P0-3); on the switch, a brief establishing beat, then POV 🔧 (C1). Holds 45–90 s (U10) | **H2H win-probability banner** on a predicted crash (✅ pieces, 🔧 trigger — E8); fight-heat tells the director where the *next* compound is likely to go |
| 16:30–21:30 · **Mid → Late** (zone 5) | Third-party chaos; 10–14 teams in tight proximity; simultaneous fights peak | **Commit-and-queue protocol** (§2 step 5): one fight on PGM, the rest fully tracked off-camera; offered replays fill genuine lulls only. No PiP, no splits (U9) — this is where the spec's show would fall apart and the real one holds | Alive counters locked to the top bar (U3); sidebar standings update on **every** elimination from the live kill-stream (E12); third-party alerts name the vulture team before it arrives |
| 21:30–26:00 · **Late game** (zone 6–7) | Edge battles, gatekeeping, lethal blue; 5–8 teams | Title-contender and clutch weighting rises (storyline W 🔧 P1-4); holds stretch to 60–120 s; **replays suppressed** 🔧 (E11) so nothing interrupts live action; final-zone safety lock arms as teams thin | Live points race in the sidebar — rank changes visible per elimination; comeback/milestone stories armed; WWCD% cards for the contenders |
| 26:00–30:00+ · **Final circle** (zone 8–9) | Final 2–3 squads in a tiny zone | Stay until it ends (U10). Layout shifts to the real final-circle pattern: aerial/map view + top-4-only panel + 2×2 facecam grid (`MapCamView` shape), cutting to the star POV for the decisive fight. No switching away, no replays, no splits | On the final elimination: hold on the winner through the celebration — the ceremony is minutes, not 5 s (U14) |
| Post-match | WWCD | WWCD ceremony sequence | WWCD team-stats graphic (finishes + 4 player cards) → match MVP comparison → updated championship leaderboard → day reel / highlights package at day close (U12: the reel is a day-end product, not a mid-match insert) |

What this show deliberately does **not** contain, and why: no lookahead buffer
(the feed's own delay is unmeasured — §4 P2-9 measures it instead of inventing
a number), no fight PiP or splits (U9), no 4 s dwell (U10), no stingers inside
gameplay (U8), no grenade follow-cam or listen-in (no such signal exists).

---

## 4. Phase 25 build list — the adaptable gold, prioritized

Ordered by dependency: P0-0 unblocks everything beneath it.

### P0 — the director becomes real
- **P0-0 · Unify and wire the director.** One decision service consuming
  vision-intelligence output (fight promotions + `intel_alert`s — give the
  orphaned alerts their first consumer), writing through `DirectorService`
  (scene **and** `active_camera`), respecting the existing mode contract
  (full_auto acts · semi_auto suggests with the 60 s approval flow · manual
  stays authoritative). Port from `ObserverDirector`: absolute-margin switching
  (40/15/5/10), staleness (90 s fight / 30 s other), safety caps (3 s cooldown,
  8/min), final-zone hard lock, narrative continuity — fixing the two warts
  (playbook action names; make the fight-hold window a true timer) and
  verifying the `fight.detected`/`fight_detected` bus naming. *This is the
  spec's central demand, and it is AEGIS's real gap.*
- **P0-1 · Parallel killfeed/UI monitoring on all 16 feeds.** Research P0.2 /
  coverage row 2. The killfeed renders on every feed; analyzing only one slot
  is the structural cause of missed fights (the #1 documented observer
  failure). This — not precognition — is the honest "zero killfeed casting."
- **P0-2 · Replay offer/take + ISO recording.** Continuous per-feed recording
  into `RECORDINGS_DIR` (the renderer already turns real recordings into
  16:9/9:16/GIF clips and never guesses an offset). Replay queue with
  OFFERED → TAKEN states and "replay ready" surfacing; director takes in
  lulls only; stinger packaging in/out (the one legal stinger); **phase-6+
  suppression** (E11) as a queue rule.
- **P0-3 · PVW preview + true multiview director UI.** Preview tile = the
  top promoted candidate's feed, pre-positioned on promotion (P1 research
  item); PGM/PVW pair, replay A/B tiles, intel tiles (rotations / fights /
  airdrops / knocks), status row. The director finally *sees* like a TD.

### P1 — the spec's scoring soul, made honest
- **P1-4 · Storyline Multiplier W in the unified priority score.** Inputs that
  exist: live standings rank/points (live writer), storyteller narrative flags
  (rivalry, milestone, comeback), win-probability. Bounded multiplier,
  documented weights, standings entering a director score for the first time.
- **P1-5 · PRE_COMBAT as a named engagement state.** Fuse PotentialFight
  confidence + proximity/convergence + rotation intent into a per-team state
  (IDLE/LOOTING/ROTATING/PRE_COMBAT/ACTIVE_COMBAT/CLUTCH — the spec's enum,
  minus the LOS lie). Feeds S_i and PVW pre-positioning. This is the
  10-second-lead, mechanized.
- **P1-6 · Drop Clash Alert.** Promote contested predicted drops from GET
  fields to a bus alert + director notification at plane phase.
- **P1-7 · Context-first beat.** On a new-fight switch, a brief establishing
  scene before POV where the rig supports it — grammar durations, sourced
  principle, no 2.5 s dogma.

### P2 — small, honest completions
- **P2-8 · Wire the ducking.** `broadcast/audio_mixer.py` ducking into the
  live mixer for replay/commentary moments. (For AEGIS commentary — the
  listen-in it was imagined for cannot exist.)
- **P2-9 · Measure the delay.** Stopwatch on the real rig → publish the
  measured spectator delay as a sync-offset (carried from Phase 24
  follow-ups). The spec's "buffer" idea ends here: you measure the delay
  reality gives you; you don't invent foresight.

### Explicitly NOT building (with the reason on record)
Fight PiP / dual / quad splits during live play ⛔ U9 · 4 s dwell ⛔ U10 ·
forced 8 s map cuts ⛔ U11 · stingers inside gameplay ⛔ U8 · 3-second
lookahead buffer 🚫 (impossible over an already-delayed feed) · grenade
follow-cam / trajectory prediction 🚫 (no projectile signal) · team-comms
listen-in 🚫 (not a spectator signal) · per-team fan POV streams — deferred
as streaming infrastructure, not director logic.

---

*Evaluation ends. Build begins only on the user's word — Phase 25 as scoped
above, starting at P0-0.*
