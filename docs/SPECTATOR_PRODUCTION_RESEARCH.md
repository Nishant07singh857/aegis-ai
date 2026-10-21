# SPECTATOR PRODUCTION RESEARCH — How World-Class Broadcasts Produce from Spectator Mode, and What AEGIS Must Learn

**Date:** 2026-10-20 · **Mission:** producer-craft research — how real Krafton India (BMSD/BMPS/BGIS) and global (PMGC/PMGO) productions actually run observers, produce standings/graphics/replays, and live inside spectator mode's hard limits.
**Companion docs (not repeated here):** `VISUAL_BROADCAST_BIBLE.md` (frame-measured overlay/camera grammar) · `DEEP_ANALYSIS/MASTER_PATTERNS.md` + `india_vs_global.md` (format comparison) · `SPECTATOR_COVERAGE.md` (Phase 24 code-verified signal matrix + limitations register L1–L12) · `PROJECT_INVENTORY.md` (audited build state).

**Sourcing key — every claim is one of:**
- **[SOURCED]** — published source, URL in §8. Vendor case studies are marked *(vendor claim)*.
- **[INFERRED]** — reasonable industry inference from sourced facts; labelled, never presented as fact.
- **[OURS–VOD]** — from our own frame/transcript analysis of 7 VODs (BMSD GF D1–3, BMPS GF D1, PMGC GF D1–2, PMGO D1), 360p rips.
- **[OURS–CODE]** — from our code-verified audit of the AEGIS repo (`SPECTATOR_COVERAGE.md`, `PROJECT_INVENTORY.md`).

---

## 0. TOP FINDINGS (executive summary)

1. **Nobody publishes observer headcounts — the role is real, the numbers are not.** The official PUBG Mobile Competition Rulebook v1.7.0 (Jan 2026) contains **no observer staffing clause and no spectator-delay clause at all** [SOURCED — two independent full-text searches]. The best hard benchmark is a comparable BR truck: ALGS runs **5 live observers, 8 observer POVs, 2 producers/directors, ~20 screens** for a 60-player lobby [SOURCED]. Any exact "PMGC uses N observers" claim found online is invented.
2. **Top-tier standings are not typed — they are an API render.** NODWIN's PMPL South Asia / PMIS productions used WASP3D graphics wired **directly to the game backend API**; the post-match table is "instant" because the leaderboard is maintained live in a database all match [SOURCED, vendor case studies]. That API is a **publisher-granted privilege** (approved organisers may request "live match data API integration" + a gated esports spectator client, Nov 2025) [SOURCED] — everyone else, including AEGIS, is spectator-only by default.
3. **There are THREE different "spectator" things, and confusing them breaks architectures.** (i) Consumer follow-spectate — **server-filtered: a spectator only receives data for players visible to the followed host** (Tencent anti-cheat design) [SOURCED]; (ii) the gated esports spectator client (undocumented UI, organiser-only) [SOURCED]; (iii) third-party HUD layers (LHM.gg) on top of a feed [SOURCED]. No consumer path gives a god-view map.
4. **No published BGMI spectator-delay number exists — anywhere.** Delay in PUBG Mobile documentation lives at the *stream* layer: 3–5 min recommended for player POV streams; a 2026 Asia Stars rulebook revision imposed 5+ min after a stream-sniping scandal [SOURCED]. The "~30 s–1 min spectator delay" AEGIS has assumed is an **operational observation, not a spec** — it must be stopwatch-measured on AEGIS's own rig.
5. **Observer craft is measured in lead time, not reaction time.** "Being there with a virtual camera **ten seconds before** you're needed" [SOURCED, ALGS]. Anticipation is built from zone pulls vs known team rotation tendencies, POI homework, and 3-team proximity clusters — exactly the inputs a spectator-only AI can also compute.
6. **The #1 documented observer failure is simultaneity, not skill.** A field study of a Game for Peace (Chinese PUBG Mobile) streaming team formalised five recurring failures, #1 = *"the observers miss in-game firing"*; plus duplicated coverage of the same player and ignored director instructions [SOURCED]. The mitigation is structural: multiple observers with pre-assigned coverage + replay recovery — and, for an AI, **monitoring the UI layer (killfeed) on every feed in parallel**, because the killfeed reports fights the camera never showed.
7. **Compression kills small UI by sampling first, contrast second.** A ~20 px kill-feed icon at 1080p is ~6–7 px at 360p — 4–8× below Tesseract's optimal glyph size before artifacts are even counted [derived from SOURCED bitrates + OCR guidance]. **4:2:0 chroma subsampling destroys colour-dominant thin elements (team colours, HP bars, red markers) before luminance text** [SOURCED]. Conclusion: reading a public stream/VOD is damage control; the pipeline must tap the **local observer device at native resolution, pre-OBS, pre-YouTube** — which also removes the entire broadcast-delay layer (finding 4).
8. **Enhancement: super-resolution is the only technique with large measured OCR gains; the rest are A/B-test knobs.** GAN SR before OCR: Tesseract word accuracy **0.10% → 85.96%** on 75-dpi scans (bicubic alone: 64.77%) [SOURCED, document scans — direction transfers, deltas won't]. CLAHE/gamma have **no measured game-UI gain** and one controlled human study found no improvement [SOURCED]. Training detectors **on compressed examples** beat inference-time cleaning (+~1% F1; mAP 33.3%→34.0%) [SOURCED].
9. **Replay professionalism = continuous recording + offer/take discipline, not fast clipping.** EVS/vMix productions ISO-record every feed continuously, auto-mark events (5/10/20 s presets), the replay op *offers* "replay ready" into Preview, the director *takes* it with branded stinger-in → slow-mo → stinger-out; a second replay is prepared **in parallel** [SOURCED]. Tencent built the same idea into the observing program itself (replay/time-flow function) [SOURCED].
10. **Published "standard" scoring ≠ what BMSD 2026 actually used.** Generic guides publish placement points 10/6/5/4/3/2/1/1 + 1/finish [SOURCED]; our frame-verified BMSD 2026 GF tables ran **WWCD = 10, 1/finish, no other placement points** [OURS–VOD]. Broadcast graphics are ground truth; guides are secondary. Scoring must stay per-tournament config (AEGIS already does this [OURS–CODE]).

---

## 1. OBSERVER CRAFT

### 1.1 Staffing — what is actually documented

- **The role and its management are documented; headcounts are not.** James Yang, Tencent's Director of PUBG Mobile Esports: *"You can think of an observer as a cameraman. There are 64 players in a match, and a map is wide and large… all big organizers working with us have good observer teams usually managed by a director."* [SOURCED]
- **PUBG Mobile has "no proper observer mode"** — David Neichel (ESL/EFG production leadership): in PUBG Mobile/Fortnite *"we have a team of observers whose job is to be like camera men or women inside the game. The supervisor for the in-game observer team has a very crucial mission, to always anticipate where the action will take place."* [SOURCED]
- **Rulebooks don't staff it.** Rulebook v1.7.0: no observer clause [SOURCED]. A national-championship ruleset (PMNC Hungary 2026, §11.2) only gates *access*: organiser-appointed observers/commentators may enter the spectator section [SOURCED].
- **Production-company structure (PMGO/PMNC producer, Wiser Esports):** "Broadcast Observer(s)" (plural) sit alongside a separate Show Director, Producer, Graphics Operator, Replay Operator, Technical Director; plural observer stations + **spare observer stations** are budgeted as contingency [SOURCED].
- **Benchmark numbers (Apex/ALGS, 60 players — NOT PUBG Mobile):** 5 observers live, 8 POVs monitored, 2 producers/directors, ~20 screens; 9 observers rostered across a multi-day LAN [SOURCED]. Generic career-source norm: 2–3 observers at smaller events [SOURCED, low authority].
- **[INFERRED] PMGC-tier PUBG Mobile staffing ≈ 4–8 live observers + 1 observer lead**, by interpolation (64-player lobbies, plural stations + spares confirmed at PMGO level). **[INFERRED] Coverage is partitioned by map region / team cluster**, the only structure consistent with the ALGS dispatch model and the Observer Lead's documented duty to "assign coverage responsibilities" [SOURCED for the duty].

### 1.2 Coordination — how the truck actually runs

- **Split authority [SOURCED, ALGS]:** the producer "makes the big picture calls, picking out storylines"; the game director watches every observer POV and dispatches observers to map areas/teams; a second director shares load.
- **Hybrid top-down + bottom-up [SOURCED]:** *"Does the director call for what shots they want, or do the observers tell the director what they have?… it's the best of both worlds… now you have 12 sets of eyeballs instead of one."* Observers are dispatched pre-emptively on reads ("NRG will probably head south — an observer is sent that way just in case") and **call outcomes** ("E36 should win now" → director cuts on the call). Observers also answer caster queries relayed through the truck.
- **Comms discipline tightens with fight density [SOURCED]:** when three fights break simultaneously the director triages and picks one. Standing protocol: pre-match coverage clarification + comms test; in-match proactive call-outs, clean handoffs, replay-opportunity alerts [SOURCED, production guide].
- **Cut grammar [SOURCED]:** every POV switch is punctuated with a brief establishing/panning shot ("if only for a split second"); POV cuts are used deliberately to expose HP/ammo state at decisive moments.
- **PUBG Mobile LAN comms standard [SOURCED, Wiser]:** all talent on IFB/talkback; director can address each position individually or as a group; referees coordinate match-start timing with the show director. Their cautionary tale: casters *not* on talkback talked through a called break — "your director loses all real-time control of the show."
- **[INFERRED for BGMI specifically]:** no public source documents a distinct "spotter" job title in PUBG Mobile productions — the documented titles are observer, observer lead/supervisor, in-game director, show director, replay operator. The spotter function exists as the observers' bottom-up call-out channel. (AEGIS's human spotter is therefore an *addition* to the classic structure, not a copy of it — a backup channel, which matches how AEGIS already positions it [OURS–CODE].)

### 1.3 The spectator client — three different things

1. **Consumer follow-spectate** (what a normal account gets): spectate friends/crew, view player info, post-death opponent spectate; room-mode toggle even shows projectile/bullet trajectories [SOURCED]. **Critically, it is visibility-filtered by design**: Tencent's system has the server transmit a player's data to spectators *only if that player is visible to the followed host* [SOURCED]. There is no full-map god view.
2. **The gated esports spectator client**: since Nov 2025, *approved* organisers receive "a spectator client for broadcasts," Room Cards, anti-cheat/technical assistance, and may separately request live match data API integration [SOURCED]. Its internal UI/camera-mode list is **not publicly documented anywhere** — a hard research gap, closable only via the organiser programme or a practitioner.
3. **The observing program + HUD layer**: Yang's replay/time-flow upgrade lives in Tencent's observing program (*"a very powerful function to control the time-flow to see all players' moves and actions"*) [SOURCED]. LHM.gg documents what productions extract from a PUBG Mobile feed: real-time scoreboard (rankings, deaths/kills/assists), All Players List, Observed Player Panel (nickname/avatar or player-cam, health, kills, inventory), Killfeed (causer, victim, weapon), Team Details & Comparison, Player Summary [SOURCED].
- **What tournament spectator UIs show (PUBG PC, METU thesis on PGC 2021 — PC, flagged):** minimap, match info, persistent scores panel; on POV: team panel (names/health/alive/vehicle) + selected-player panel (armour, HP, ammo, weapons, kills); in free-cam, **all players carry nametags — a spectator-interface-specific feature** [SOURCED]. PC custom-match observing also documents an **auto-combat-spectate (Ctrl+Z)** that auto-spectates combat by priority and a hotkey to jump to the most recent attacker (Ctrl+X) [SOURCED, PC only — do not assume Mobile parity].
- **Spectator-client fragility is documented**: spectator lag artefacts (burst fire rendering as single shots, floating characters/vehicles), difficult free-cam control (Korean league, via NamuWiki — fan wiki, flagged) [SOURCED, secondary]. Productions budget spare observer stations for a reason [SOURCED, Wiser].

### 1.4 Anticipation — the actual skill

- *"Observing is all about knowing where fights are going to break out and being there with a virtual camera ten seconds before you're needed"* [SOURCED, ALGS]. The failure mode it replaced: following a big-name team "just looting" while action happened elsewhere.
- The inputs are homework, not reflexes [SOURCED]: **each team's rotation tendencies × this lobby's zone pull**, POI knowledge, which teams push vs sit back, fan-favourite/storyline priority. Cameras are pre-positioned on predicted rotation paths and parked on 3-team compounds "in case."
- Same formulation from a PUBG Mobile producer: *"A skilled observer knows where the action is before it happens, can anticipate rotations, and knows when to cut between fights… one of the highest-impact investments you can make"* [SOURCED, Wiser].
- Korean-league practice: observers display the POV of players *likely to make super plays* a few seconds in advance; missed moments are recovered as replays from individual screens — achieved, per the wiki, by "thoroughly overworking the observers" [SOURCED, fan wiki, flagged].
- Named failure modes in production guides [SOURCED]: over-switching, tunnel vision, panic-chasing action, ignoring set-up/aftermath. [OURS–VOD corroboration: our measured India decision delay ~2–3 s vs PMGC's near-instant fight acquisition is the same metric — arrival lead time — seen from the viewer's side.]

---

## 2. PRODUCER CRAFT

### 2.1 Live standings — the API reality

- **Top-tier India was API-driven [SOURCED, vendor case studies]:** NODWIN's PMPL South Asia S2 and PMIS 2020 used WASP3D "Smart Live eSports Graphics" with *"direct API connections to the game's backend"* — templates designed once, *"automatically populated with live data… Every kill, every leaderboard update, and every team elimination was reflected instantly on screen… without any human delay."* PMPL SA Finals added API-driven in-studio AR (floor/ceiling/3D-map) graphics fed the same way.
- **The plumbing pattern [SOURCED]:** graphics templates wired to a **SQL database**; database linked to the game **API**; a **Data Feeder application** between them; event-based auto-triggering of context graphics; web control app, single-click take; vendor's stated result: **graphics manpower reduced to 1 operator**. Generic explainer confirms the 3-stage shape: capture (APIs/telemetry/tools) → map fields to visuals → system "continuously listens," graphics update "without requiring manual input."
- **Why post-match tables appear "within seconds" [INFERRED from SOURCED pipeline]:** they are not produced at match end. The leaderboard is live in the DB throughout; the full-screen table is a render + a take. Perceived speed is continuity, not fast typing.
- **Verification [research gap]:** no public primary source names a stats-verification team or sign-off step at Krafton India/NODWIN. The WASP3D evidence implies verification is pushed *upstream* (if the backend is wrong, the graphic is wrong) plus the operator's preview-before-take (§2.2/2.3). **[INFERRED] Lower-tier reality** (the world AEGIS sells into): an admin enters placement + finishes once into a sheet/tool; formulas compute totals; the sheet feeds overlays. The error-prone step is finish-count transcription — which is precisely the step top tier automated away.
- **Scoring config warning [SOURCED vs OURS–VOD]:** published guides give placement 10/6/5/4/3/2/1/1 + 1/finish (WWCD 8 finishes = 18 pts), plus BGMS house variants (Powerplay double finishes in zone 1, Impact Player double finishes, Bounty +10) [SOURCED]. But BMSD 2026 GF's own tables ran WWCD=10 + 1/finish only [OURS–VOD, frame-verified arithmetic]. **Lesson (also our M18 lesson, §4): on-air graphics are ground truth; guide articles are secondary sources.** Scoring must be per-tournament configuration, verified against the event's own rulebook/graphics — never hard-coded from a guide.

### 2.2 The director's multiview

Documented elements (exact NODWIN/Krafton tile arrangement is unpublished — arrangement is [INFERRED] from gallery practice [SOURCED, Broadcast Bridge]):

1. **PROGRAM** (large) — on air now. 2. **PREVIEW** (large) — next source staged. Under them, 5–6 "next thing" monitors in the director/PA/vision-mixer eye line.
3. **Observer feeds** — main observer, dedicated map cam, POV/top-team cams (BGMS S4 product: 1 Main + 1 Map + 4 Top Team Cams [SOURCED earlier research]).
4. **Player/face cams, caster desk, crowd/stage cams.**
5. **Replay channels** — first-class tiles (switcher multiview specs list 2 GFX + 2 DDR/replay channels alongside inputs, with audio PPM overlay) [SOURCED].
6. **GFX preview** — the graphic *as it will render*, before take. This preview is the human verification step that replaces a stats desk.
7. **Clocks/status row** — match/zone timers, countdowns, stream health, audio meters, run-of-show position.
Gallery scale: 5–20 people, functions doubling on smaller shows; 2–3 replay seats on big shows (one serving the mixer live, others building packages); "most productions don't need more than eight channels of replay" [SOURCED, Broadcast Bridge].

### 2.3 Replay & graphics triggering — moment → on air

1. **Continuous capture:** every observer feed ISO-recorded (EVS LSM / vMix Replay's 8-channel continuous recording with 2 playback channels) [SOURCED]. Recording never starts/stops.
2. **Marking:** on the call (observer/spotter/director), the replay op marks In/Out on the fly; auto-mark presets create e.g. "last 10 seconds" as an event in one button press (5/10/20 s presets), each event storing its default angle + a text label [SOURCED, vMix docs + studio walkthroughs]. Jog/shuttle re-trim if the mark missed.
3. **Offer, don't take:** the replay op offers the cued clip ("replay ready") into the preview row; **the director takes it**. The replay op never self-takes at broadcast grade [SOURCED practice pattern]. EVS operators can *simultaneously prepare a second/third replay* (with telestration) while the first airs [SOURCED].
4. **Take grammar:** branded **stinger-in** → replay (speed continuously variable 0–100%, slow-mo on the key play; transition duration can scale with speed) → **stinger-out** → live. In/out stingers are different assets; the stinger is a viewer signal: "this is past / this is live" [SOURCED mechanics; semantics INFERRED].
5. **Placement [INFERRED for BGMI from format + SOURCED workflow]:** replays cluster at fight-end, WWCD, and the post-match window — almost never mid-fight (you'd miss the next elimination). On-air replays come from ISO recordings of observer feeds, not from scrubbing the in-game client — the client's time-flow function is the fallback/analysis tool and the missed-moment recovery source [cf. SOURCED Yang + NamuWiki].
6. **Graphics track:** always-on data layers (killfeed, observed-player panel, alive counts) vs **operator-taken scenes** (scoreboards, comparisons, story graphics). Data-driven graphics can be event-auto-triggered; the human decision becomes *whether to take*, not *building it* [SOURCED, WASP3D + LHM pattern].

### 2.4 Comms & cueing

- **Vocabulary [SOURCED]:** *Ready/Standby* = warning that a source is next to air (no reply expected); *Take* = go to air immediately; transition calls (cut/dissolve) ride with the take. **Talkback** = off-air control-room→talent channel; **IFB** = program audio in the caster's ear that the producer can interrupt mid-sentence ("wrap up, standings in 10") without the audience hearing. The PA gives countdowns ("On air in 60… 30… 10"); the director gives executes.
- **Esports practice [SOURCED]:** casters follow producer cues for breaks/analyst handoffs/interviews (vidermedia); remote productions implement IFB + reverse talkback in software (casters can answer the director off-air; mic kill buttons) [SOURCED, TVU case].
- **Scale [SOURCED, Riedel]:** Esports World Cup: 341 wireless beltpacks, 330 smart panels, 8 stages, 35 languages; even a single-title LAN (The International) ran 90 beltpacks. Every role group gets its own partyline; the director can reach any group or individual.
- **India gap [research gap]:** no BTS source documents NODWIN/Krafton India's internal cue words or countdown practice. [INFERRED]: BGMS airs on Star Sports (standard TV gallery practice is structurally required for ad-break IFB cueing); at the grassroots base (AEGIS's market), comms collapse to a Discord/WhatsApp call with the same *words* but one person producing + observing + GFX — the coordination load AEGIS automation removes.

---

## 3. SPECTATOR-MODE HARD LIMITS IN PRACTICE

### 3.1 Delay — two different delays, only one is AEGIS's problem

| Layer | Value | Status |
|---|---|---|
| PUBG Mobile rulebook (v1.7.0) spectator/broadcast delay clause | **None exists** | [SOURCED] |
| In-client BGMI spectator delay (custom rooms) | **No published number; no documented configurable setting** | [SOURCED absence] |
| Player POV stream recommendation | **3–5 min** delay | [SOURCED, tournament regulations doc] |
| Asia Stars 2026 revised rulebook (post-scandal) | **5+ min mandatory** stream delay + info ban | [SOURCED, secondary journalism — flagged] |
| Cross-title: LoL spectator mode | **3 min** (anti-ghosting, built-in) | [SOURCED] |
| Cross-title: ESL CS player POV streams | **+240 s** mandatory | [SOURCED] |
| YouTube Live platform latency (Normal/Low/Ultra-Low) | **15–60 s / 5–15 s / 2–5 s** | [SOURCED, vendor guide] |

**The distinction that matters:** broadcast/stream delay (minutes, + platform seconds) applies **only if the pipeline reads the public stream**. A vision system tapping the **local observer device** (pre-OBS, pre-YouTube) inherits none of it. Whatever in-client/queue latency remains on the local device is an empirical property of AEGIS's own rig — **it must be stopwatch-measured** (kill on player device vs appearance on observer device, same LAN) and then treated as a known sync offset. AEGIS's inherited "~30 s–1 min" figure is [OURS] venue experience, not a spec — and §6 makes measuring it a P0 task. Note the production upside: human broadcasts insert replays/graphics *inside* their delay buffer; a known, fixed delay is headroom for OCR verification and replay trimming, not just a defect (§6).

### 3.2 Compression vs legibility — the sampling floor

- **Bitrates [SOURCED, YouTube guidance]:** 360p ≈ 1 Mbps; 720p ≈ 5 Mbps; 1080p30 ≈ 8 Mbps (upload floors — delivered renditions are lower after re-encode). Twitch live ≈ 6 Mbps cap at 1080p60 for most channels.
- **The arithmetic [derived]:** downscale is linear (360/1080 = 0.33×): a ~20 px weapon icon → **~6–7 px**; a ~14–16 px name → **~5 px**. Tesseract works best with capitals **~20–40 px tall** [SOURCED]. At 360p, kill-feed text sits 4–8× below optimal size *before* compression artifacts. **No preprocessing recovers unsampled information** — 360p VOD analysis was adequate for layout grammar (Bible) and is inadequate as a vision-pipeline input or training ideal.
- **Chroma [SOURCED]:** delivery is 4:2:0 — one chroma sample per 2×2 block. Luminance-dominant elements (white text) survive; **chroma-dominant thin elements (team-coloured entries, HP bars, red-zone markers) smear first** — a measured JPEG analogue found red-text edge error stays above the "clearly visible" threshold even at quality 0.99, and only 4:4:4 fixes it. Expect **colour-based classification to fail before shape-based detection** on any compressed feed.
- **Detector degradation [SOURCED, measured]:** YOLOv5 F1 is stable through H.264 CRF 37, then deviates markedly at CRF 47; small objects fail first (at CRF 47, 90% of misses fall within a minimum-size curve). Frame-to-frame detector consistency on real video is only 83.2–97.1%; **unsharp masking improved consistency up to +5.1% with no accuracy loss**; **retraining on compressed imagery: up to +1% F1**; training at matched compression recovered mAP 33.3% → 34.0% on harder-compressed test data.

### 3.3 What gets missed — documented

- **Game for Peace streaming-team field study [SOURCED]** — five typical "jarring observations": **(1) observers miss in-game firing; (2) two observers track the same player (duplicated coverage); (3) observers ignore director instructions; (4) wrong camera perspective of the player; (5) director's vague descriptions confuse observers.** Teams often work ad hoc without a designed system; audiences notice.
- **Structural cause [SOURCED]:** "because multiple events occur simultaneously across the map, human observers may miss important events" (auto-spectating research); SVG summit: battle royale is *"more like golf — you're covering Hole 17, then you're covering Hole 14"* because "countless scrums occur in different places simultaneously"; and *"a statistician can't keep up with it"* — the explicit motivation for automated graphics.
- **Documented mitigations [SOURCED]:** multiple observers with pre-assigned storylines/coverage; **replay recovery** of missed moments from individual screens; and the **killfeed/scoreboard as the camera-independent factual backstop** — casters report what the feed confirms when the camera missed it.
- **The AI-specific asymmetry:** a single-output-watching AI inherits the attention bottleneck; a system monitoring the **UI layer on every observer feed in parallel** does not — the killfeed reports eliminations from fights no camera showed, including third parties and simultaneous fights on opposite map sides. Its honest blind spots are different: damage-only fights with no elimination, positioning/third-party intent *before* the first knock, entries scrolled out before capture, and anything visible only in a POV nobody spectates. [OURS–CODE corroboration: AEGIS's killfeed is COVERED end-to-end, but vision analysis runs on **one** configured slot only — the parallel-monitoring advantage is designed, not yet realised.]

---

## 4. WHAT SEPARATES WORLD-BEST — FROM THE PRODUCER'S CHAIR

(The format-level comparison — WWCD% cards, top-center stats, turnaround times, pre-show spectacle — is in `india_vs_global.md` and is not repeated. This section is only the *production-craft* layer beneath it.)

| Craft dimension | World-best practice | Indian production (as documented) | Separator |
|---|---|---|---|
| Fight acquisition | ~10 s anticipation lead; cameras pre-positioned on reads [SOURCED] | Competent but ~2–3 s decision delay [OURS–VOD] | Homework systems (rotation tendencies × zone), not reflexes |
| Observer teamwork | Formalised: lead assigns coverage, standardised call-outs, hybrid top-down/bottom-up [SOURCED] | Not publicly documented; Game for Peace study shows even top CN teams fail ad hoc [SOURCED] | A *designed* observing system vs talented individuals |
| Data posture | API-first: DB-maintained standings, event-auto-triggered graphics, 1 GFX operator [SOURCED] | Top tier (NODWIN) is also API-first via WASP3D [SOURCED]; the long tail of Indian TOs is manual sheets [INFERRED] | The gap is not India-vs-world; it is **API-haves vs have-nots** — and the have-nots are AEGIS's market |
| Replay | Continuous ISO + parallel prep + offer/take discipline [SOURCED] | Day-1 BMSD aired **no replay package at all** [OURS–VOD]; D2/D3 ramped 1→2 | Replay treated as a staffed system, not an afterthought |
| Turnaround | ~2–4 min match-to-match (PMGC) [OURS–VOD] | 10–16 min desk-heavy breaks [OURS–VOD] | Rundown discipline; the data is ready either way |
| Verification culture | Preview-before-take; broadcast graphics as ground truth [SOURCED pattern] | Transparency under failure (voided matches announced openly) [OURS–VOD] | Both are trust systems — AEGIS must implement both (§6) |
| Desk identity | Spectacle + analysis desk (global) | **Data desk**: standings after every match, 4 leaderboard products [OURS–VOD] | India's edge is real: a data desk is *software-shaped* — an automated producer can run India's desk and global's predictive layer simultaneously |

**Producer's-chair summary:** the universal grammar is identical everywhere (§MASTER_PATTERNS). What separates the best is (a) anticipation infrastructure, (b) data plumbing that makes the desk a *choice* layer instead of a *typing* layer, and (c) systems (ISO replay, comms matrix, spare stations) that make misses recoverable instead of fatal. All three are engineering problems, not talent problems — which is precisely why an automated producer can plausibly reach them.

---

## 5. IMAGE ENHANCEMENT — MEASURED vs PLAUSIBLE

| Technique | Evidence | Verdict for AEGIS |
|---|---|---|
| **Capture at native res, locally** | §3.2 arithmetic; Tesseract sizing guidance [SOURCED] | The single highest-payoff "technique." Everything below is damage control for not doing this. AEGIS already captures via device (adb/scrcpy) [OURS–CODE] — keep it sacred: never let a YouTube/VOD path become a pipeline input. |
| **Upscale crops before OCR** | Tesseract docs (≥300 dpi guidance, `--psm 7` for single lines, disable dictionaries + whitelist for non-dictionary strings like player names) [SOURCED]; practitioner rule: capitals ~20–40 px | Proven practice. Bicubic alone rescues near-zero low-res OCR (0.10%→64.77% word acc in the SR study's control arm) [SOURCED]. |
| **Learned super-resolution (crop-only)** | Lat & Jawahar, ICPR 2018 (documents): SR **85.96%** vs bicubic 64.77% vs low-res 0.10% word acc (Tesseract, 75-dpi set); +~4.7 pts on cleaner sets where bicubic added nothing [SOURCED]. Real-ESRGAN feasibility: ~22 FPS FP32 at 640×448 on RTX 3090 (TensorRT port benchmark, flagged); compact SRVGGNetCompact x4v3 is 14–27× faster than x4plus at equal/better PSNR (dev benchmark, flagged) [SOURCED, flagged] | Real but bounded: **crop-only, compact model, asynchronous** (killfeed crops, off the critical path). Full-frame SR at match rate is not realistic. Budget a *smaller* gain on game UI than on documents until measured on real BGMI crops. |
| **Temporal multi-frame fusion / voting** | Multi-image SR principle is sound (multiple sub-pixel-shifted samples constrain detail no single frame holds); averaging helps only when the scene is static/aligned [SOURCED]. **No published measurement on game UI.** | High-value bet *for the killfeed specifically*: entries persist ~5 s, static and aligned — the ideal fusion case. Cheapest form = **majority-vote OCR/detections across frames** (no SR model). Must be validated on labelled real frames before trusting the gain. |
| **Train on compressed/dull data** | Retraining on compressed imagery: +~1% F1; matched-compression training recovered mAP under heavier compression [SOURCED, measured] | Best-supported *training-side* fix; likely explains synthetic-90% → real-world drop better than any inference filter. AEGIS's 44k-frame real-VOD dataset is already this shape [OURS] — extend with native-res local captures when the rig exists. |
| **Unsharp masking** | Detector consistency **+up to 5.1%**, no accuracy loss [SOURCED, measured] | Cheap, low-risk; A/B-gate per element. |
| **CLAHE / gamma / contrast stretch** | OpenCV CLAHE mechanics documented (tiles 8×8, clipLimit ~2.0, L-channel only) [SOURCED]; **zero measured game-UI OCR/detection delta found**; one controlled human study (1,997 teledermoscopy cases): CLAHE+gamma gave **no accuracy improvement** and slightly *worse* specificity [SOURCED, human task — flagged] | Mechanistically plausible for dull icons, **unproven**. Ship only behind a measured evaluation on real BGMI crops, per element (may help HP bars, hurt icons). Never budget an assumed gain. |
| **Deblocking / denoising filters** | Standard CV mitigations; standard warning that aggressive filtering softens edges [SOURCED, general] | The measured literature points the other way: fix the *training data*, not the input. Low priority. |
| **Pipeline shape (gate → spend)** | Student kill-feed/scoreboard project on a real stream: ROI-crop → frame-difference gate → template gate eliminated **99.3% of frames** before OCR/detector stage; OCR for names, **detector (fine-tuned YOLO) for icons, never OCR** [SOURCED, un-peer-reviewed — cite architecture + gating numbers, not quality] | Adopt the shape: it is what makes SR/voting affordable, and it formalises "icons are shapes, names are text." |

---

## 6. IMPLICATIONS FOR AEGIS — CONCRETE, BUILDABLE, PRIORITIZED

Every item is tied to spectator-mode inputs only. "Already" = verified in [OURS–CODE] audits; "Gap" = named absence.

### P0 — do first (correctness + the missed-fight problem)

1. **Measure the real spectator delay on the actual rig; publish it inside the system as the sync offset.** Stopwatch protocol: kill on a player device vs its appearance on the observer device (same LAN), repeated across slots; store per-slot offsets. *Why: no published number exists (§3.1); every sync claim AEGIS makes is currently ungrounded.* Then use the fixed delay as production headroom (finish OCR verification, trim replays, render-check graphics inside it) — the way human broadcasts use theirs (§2.3).
2. **Run UI-layer detection on ALL 16 observer feeds in parallel, not one slot.** Killfeed / alive-count / zone-timer readers are cheap; run them per-feed. Expensive detectors stay on the on-air feed. *Already: readers exist and are wired — on `vision0` only [OURS–CODE, SPECTATOR_COVERAGE rows 2/4/7]. Gap: 15 feeds are view-only.* This is the single change that structurally removes observer-failure #1 (missed firing/simultaneous fights, §3.3): the same killfeed is rendered on every screen, so cross-feed redundancy + voting comes free.
3. **Fix the killfeed reader against real BGMI geometry and finish the text path properly.** Default region must be **top-LEFT** (current default is top-RIGHT with an action-word parser the real icon-based feed doesn't match — SPECTATOR_COVERAGE discrepancy D9 🔴). Tesseract config per official guidance: upscale crops so capitals land ~20–40 px, `--psm 7`, dictionaries off + character whitelist for player names. Weapon/headshot stay **icon-detector** problems (Phase 23, in flight) — never OCR.
4. **Add multi-frame majority voting before any killfeed event is emitted.** Entries persist ~5 s; require the same parsed entry in ≥2 of 3 consecutive sampled frames (the AliveCountReader already does 2-frame confirmation — generalise the pattern). Zero model cost; directly attacks frame-to-frame detector inconsistency (83–97% [SOURCED]).
5. **Maintain standings LIVE from the vision/feed kill stream; make the post-match table a render, not a computation.** *Gap: the live standings writer on the vision path does not exist — post-match `record_match_results` does; live frames ride the demo/simulator path [OURS–CODE row 20].* This copies the WASP3D pattern (§2.1) with a vision feeder: event → DB → template, updated per kill, so errors surface during the match. Keep scoring per-tournament config and **verify the preset against the event's own graphics/rulebook** (§2.1 scoring warning).
6. **Continuous ISO recording of every observer feed + auto-marked replay events.** Record all feeds continuously (rolling buffer); on detected big moments (multi-kill, clutch, WWCD, final kill) auto-create replay events with 5/10/20 s windows, default angle + label, status "replay ready." *Already: event replay + highlight renderer exist; recordings happen only if an operator records [OURS–CODE].* This is the documented recovery path for every missed fight (§1.4, §2.3) and feeds the day reel with real clips.

### P1 — next (producer-craft parity)

7. **Give the director UI a true multiview shape** (§2.2): Program + Preview large; observer-feed tiles; a dedicated **GFX Preview tile**; **Replay A/B ready tiles**; status row (zone timer, match clock, countdown to next segment, feed health). *Already: director page + story preview exist piecemeal [OURS–CODE].*
8. **Preview-verify gate in full-auto mode.** Before any full-screen graphic is taken, AEGIS renders it and sanity-checks its numbers against its own DB (the software substitute for both the API's upstream correctness and the human operator's preview, §2.1–2.2). Mismatch → hold + flag, never air. In semi-auto the same render is the director's Preview.
9. **Encode the cueing discipline in software** (§2.4): director UI speaks gallery language — states **"Standby — standings," "Replay ready," "GFX ready"**; PA-style countdowns to breaks/next match; an IFB-equivalent cue channel to casters (earpiece/overlay cue: "standings in 10"); reverse channel for casters/spotters to flag a missed kill (feeds item 6's recovery). Semi-auto suggestions must arrive as *standby* (staged in Preview), never surprise takes — AEGIS's suggest-only semi-auto already honours this; extend the vocabulary to all suggestion types.
10. **Director anticipation layer (the 10-second rule).** Pre-positioning suggestions from zone pull × per-team rotation tendencies × 3-team proximity clusters — i.e., Phase 24's W2 derivations (rotation intelligence, third-party alerts, fight-heat) wired as *camera pre-positioning*, with a measured target: fight acquisition ≤2 s after first knock (vs India's ~2–3 s human delay [OURS–VOD]; PMGC ≈ instant). Observer agents emit **standardised short call-outs** (team, POI name, "fight in ~10 s," predicted winner); when 3+ fights run simultaneously the director consumes call-outs, not raw screens (§1.2).
11. **Feed-health monitoring + hot-spare failover for vision sources.** Spectator clients have documented lag/render artefacts and disconnects (§1.3); productions budget spare stations. *Already: device backup pool/failover exists (Phase 22) [OURS–CODE]. Gap: per-feed vision-health scoring (frozen frame, OCR silence, artefact detection) driving automatic analysis-source failover + graceful degradation to map-view + killfeed-only mode.*
12. **Complete the cheap HUD-reader set so phase logic runs on real feeds:** match-clock reader, flight-path/plane capture, next-zone source, revive/bleed-out derivations (knock timestamps → knock→alive transition = revive event feeding the MVP revives term). *Gaps: SPECTATOR_COVERAGE rows 9/10/16/17/19.* Each is spectator-visible; none needs telemetry.

### P2 — measured-enhancement programme (ship only what measures better)

13. **Crop-only compact super-resolution, asynchronous, A/B-gated.** SRVGGNetCompact-class x4 model on gated killfeed icon/name crops only, off the critical path (§5 feasibility). Ship only if it beats the current pipeline on **labelled real BGMI crops** by a pre-registered margin; documents' +21-pt gains will not transfer intact.
14. **CLAHE (L-channel, clipLimit ≈ 2.0, 8×8 tiles) + unsharp masking as per-element configurable pre-processors**, each behind the same real-crop evaluation (unsharp has one measured +5.1% consistency result in its favour; CLAHE has none and one negative human study — §5). Per-element verdicts expected: they may help HP bars and hurt icons, or vice versa.
15. **Keep training data honest and compressed-real.** Fine-tune detectors on real, dull, compressed frames (the 44k-frame VOD dataset's shape is right [OURS]); add native-res local captures from the real rig when available; never train on clean synthetic only (synthetic-90% → real drop is the documented pattern, §3.2/§5).
16. **Keep the data layer API-shaped.** Internal architecture stays event-source → DB → templated graphics with event auto-triggering (the WASP3D shape, §2.1) with today's source = vision/OCR/spotter. If a future organiser obtains Krafton's gated spectator client / live-data API (§1.3), only the feeder swaps — graphics, multiview, replay, cueing unchanged. Also add the **broadcast-graphics-as-ground-truth check**: when an official table is on screen in any feed, OCR it and reconcile against AEGIS's DB (the M18 lesson: our own analyst misread a championship from celebration footage; the broadcast's numbers were right).

---

## 7. HARD LIMITS — DESIGN AROUND THESE, DO NOT PROMISE PAST THEM

1. **No documented BGMI spectator delay exists.** Any in-client/queue latency is unavoidable when reading spectator screens; it is currently *unmeasured on our own rig* (P0-1). The broadcast-layer delays (3–5 min POV guidance, 5+ min post-scandal revisions, +2–60 s platform latency) are avoidable **only** by tapping local devices pre-OBS — a design property AEGIS has and must never give up.
2. **Consumer spectate is visibility-filtered by the server.** A normal account receives data only for players its followed host can see [SOURCED]. Full-map state must be *fused* from multiple feeds; any design assuming a god-view from one consumer feed is architecturally wrong. The gated esports client may lift this — but it is organiser-granted, undocumented, and not AEGIS's to promise.
3. **The sampling floor is physics.** At 360p a kill-feed icon is ~7 px; at any bitrate, 4:2:0 chroma smears colour-dominant UI first. Pipelines fed on public streams/VODs have a hard accuracy ceiling that no enhancement roadmap removes — enhancement gains are real but bounded (§5).
4. **The attention bottleneck is structural, for humans and single-camera AI alike.** Simultaneous fights on opposite map sides *will* be missed by any one view (§3.3). Parallel UI monitoring + replay recovery mitigate; nothing eliminates. Damage-only fights (no elimination) and pre-knock positioning intent stay invisible to killfeed-based detection.
5. **Top-tier "instant" is an API property, not a craft property.** AEGIS's honest latency claim is bounded by spectator delay + vision latency: *"correct within the delay buffer,"* never *"instant."* Marketing must not claim the API tier's numbers.
6. **Observer headcounts and the esports client's internals are not public knowledge.** Rulebooks are silent; vendors don't publish PCR layouts for BGMI. AEGIS planning must use benchmark ranges (ALGS-class) and its own measurements — and must never quote an invented "Krafton uses N observers / X seconds delay" figure to a client.
7. **The full spectator-signal limits register already exists** — exact damage attribution, enemy inventories/throwable counts, in-game comms, marker→team identity on the raw minimap, off-feed audio, and 9 more — in `SPECTATOR_COVERAGE.md` §3 (L1–L12), each with its standing workaround. This research confirms those limits from the production side and adds items 1–6 above; it does not soften any of them.
8. **Enhancement evidence is borrowed.** The strong SR numbers are document scans; the CLAHE study is dermatology; the detector studies are surveillance/driving footage. Direction and mechanism transfer to game UI; **magnitudes do not**. Every enhancement in §6-P2 is therefore gated on AEGIS's own labelled real-frame evaluation — an unmeasured gain is an unclaimed gain.

---

## 8. SOURCES

**Observer craft & the spectator client**
- James Yang (Tencent, Director of PUBG Mobile Esports) interview — observer as cameraman, director-managed teams, replay/time-flow upgrade: https://www.esportstalk.com/blog/pubg-mobile-esports-director-breaks-down-5-million-program/
- David Neichel (ESL/EFG) — "no proper observer mode," supervisor anticipation mission: https://iesf.org/david-neichel/
- PUBG Mobile Competition Rulebook v1.7.0 — full text; no observer or delay clauses: https://esports.pubgmobile.com/static/doc/Competition_Rulebook_PUBGM_-_Version_1.7.0.pdf
- PMNC Hungary 2026 ruleset §11.2 (observer access gating): https://hunesz.hu/documents/PMNC2026_HUNGARY_AUTUMN_SEASON_GAME_SPECIFIC_RULESET.pdf
- Wiser Esports (PMGO/PMNC producer) — role roster incl. plural Broadcast Observers, spare stations, IFB practice: https://wiseresports.com/blog/organize-lan-tournament
- ALGS Championship behind-the-scenes — 5 observers, 8 POVs, 2 directors, ~20 screens, dispatch/call-out model, 10-second rule: https://www.thegamer.com/algs-championship-behind-the-scenes-how-an-esports-tournament-gets-made/ · roster of 9 observers: https://esports-news.co.uk/2023/01/30/algs-london-broadcast-talent-apex/
- Esports observer guide (role taxonomy, protocol, failure modes): https://esportsproduction.quest/esports-observer-guide
- Tencent spectator visibility filtering (server sends only host-visible players): https://telecomtalk.info/pubg-mobile-spectator-mode-cheating-measures/283126/
- Gated esports spectator client + requestable live-data API (Nov 2025 community system): https://nordot.app/1358135216574136356 · https://www.invenglobal.com/articles/19839/pubg-mobile-eases-path-for-community-run-esports-tournaments-with-streamlined-system
- METU thesis on PGC 2021 spectator interface (PC — flagged): https://avesis.metu.edu.tr/file?id=606de1de-3412-424a-87cb-0d378530a2c5
- PUBG PC observer hotkeys / auto-combat-spectate (PC — flagged): https://www.invenglobal.com/articles/19241/all-pubg-replay-controls-full-guide
- NamuWiki PUBG observing synthesis (fan wiki — flagged): https://en.namu.wiki/w/PUBG:%20BATTLEGROUNDS/e%EC%8A%A4%ED%8F%AC%EC%B8%A0

**Producer craft**
- WASP3D × NODWIN — PMPL South Asia (API-driven graphics): https://wasp3d.com/wasp3d-customers/pubg-mobile-pro-league-2020/ · PMIS 2020: https://wasp3d.com/wasp3d-customers/pubg-mobile-india-series-2020 · PMPL SA Finals AR: https://wasp3d.com/wasp3d-customers/pmpl-south-asia-finals-event · Red Bull M.E.O. (SQL/API/Data-Feeder plumbing, 1-operator): https://wasp3d.com/wasp3d-customers/wasp3d-x-estaz-esports-in-red-bull-m-e-o-season-5 · pipeline explainer: https://wasp3d.com/blogs/how-to-add-in-game-stats-data-to-esports-livestream-graphics
- LHM.gg PUBG Mobile HUD documentation: https://lhm.gg/blog/best-tools-for-pubg-mobile-spectating-observing-and-hud-management · Lexogrine/LHM success story (vendor claims flagged): https://lexogrine.com/success-stories/lhm
- Flowics data connectors (PUBG/Apex): https://kitplus.com/news/flowics-adds-new-data-connectors-for-easy-integration-of-live-external-data-into-broadcast-graphics/7747
- Scoring system guides (generic standard + BGMS variants): https://www.talkesport.com/guides/bgmi-tournament-points-system-smash-rule-explained/ · https://www.sportsadda.asia/esports/news-esports/bgmi-points-table-system-2024-battlegrounds-mobile-india-esports/ · BGMS S4: https://www.mykhel.com/more-sports/nodwin-gaming-jio-star-unveil-oneplus-android-bgms-season-4-011-381285.html
- Broadcast Bridge — gallery/monitor-wall/replay-seat practice: https://www.thebroadcastbridge.com/content/entry/20993/live-sports-production-control-room-teams-workflow
- SVG Esports Production Summit — observer paradigm, statistician quote: https://www.sportsvideo.org/2019/12/11/svg-esports-production-summit-a-closer-look-at-the-technology-behind-the-show/ · BR "golf" panel: https://www.svgeurope.org/blog/headlines/esports-production-summit-the-key-factors-defining-the-breakout-era-for-live-esports-production/
- vMix Replay (continuous recording, auto-mark presets, stingers, speeds): https://news.vmix.com/help28/vMixUserGuide.pdf · https://www.vmix.com/help28/GettingStarted1.html
- EVS Epsio/LSM parallel replay preparation: https://www.svgeurope.org/blog/news-roundup/evs-epsio-family-expands-with-added-live-graphic-tool/
- Gallery terminology (Ready/Take/talkback/IFB): https://classicteleproductions.com/2023/02/02/terminology-2/ · https://en.wikipedia.org/wiki/Interruptible_foldback · https://sites.google.com/bathspa.ac.uk/tv-technical-training/tvstudio/tv-gallery/talkback
- Riedel comms at scale (EWC/The International): https://www.svgeurope.org/blog/news-roundup/ibc2026-riedel-technology-powers-record-breaking-esports-world-cup-production-in-paris/
- Esports production roles (vidermedia): https://vidermedia.com/esports-production-101/ · TVU cloud production (Arena Breakout): https://www.tvunetworks.com/story/arena-breakout/

**Delay, compression, enhancement, misses**
- PUBG Mobile tournament regulations (3–5 min POV delay recommendation): https://www.scribd.com/document/707323232/Regulations-of-PUBG-MOBILE
- Asia Stars 2026 stream-sniping scandal / 5+ min revision (secondary journalism — flagged): https://agmazon.com/blog/articles/writing/202609/pubg-asia-stars-2026-himass-tanvuu-timeline-en.html
- LoL spectator 3-min delay: https://www.surrenderat20.net/2012/04/full-spectator-mode-included-in-next.html · ESL CS POV +240 s: https://esl.com/cs/community-broadcast-guidelines/ · YouTube latency modes: https://datavideo.zendesk.com/hc/en-us/articles/360058612633-YouTube-Live-Latency-Settings-Guide
- Compression vs detection (YOLOv5 CRF sweep): https://arxiv.org/pdf/2211.05805 · detector consistency + unsharp + compressed retraining: https://sugaku.net/oa/W4312344003/ · matched-compression training mAP: https://arxiv.org/html/2403.16338v1
- Chroma subsampling vs small coloured text: https://www.dscexpo.com/en/news/industry-news/100.html · measured red-text/JPEG analogue: https://dev.to/iterandum/why-red-text-blurs-in-canvas-jpeg-exports-and-where-444-kicks-in-4hcp
- Tesseract quality guidance: https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html
- SR-for-OCR measured results (Lat & Jawahar, ICPR 2018): https://github.com/watersink/icpr2018_ocr_papers/raw/refs/heads/master/Enhancing%20OCR%20Accuracy%20with%20Super%20Resolution.pdf
- Real-ESRGAN feasibility (flagged benchmarks): https://deepwiki.com/yester31/Real_ESRGAN_TRT/9-performance-and-optimization · compact-model comparison: https://dev.to/sharksevenro/fp16-quantization-made-our-in-browser-real-esrgan-48x-faster-and-nearly-destroyed-our-inpainting-52m1
- CLAHE (OpenCV docs) + negative controlled study: https://docs.opencv.org/4.13.0/d5/daf/tutorial_py_histogram_equalization.html · http://link.springer.com/article/10.1007/s00403-025-04482-7
- Game for Peace streaming-team field study (five observer failures): https://fetcher.alphaxiv.org/v2/pdf/2208.07529v1.pdf · automatic-spectating research (simultaneity): https://www.sciencedirect.com/science/article/abs/pii/S0957417422019972
- Kill-feed/scoreboard pipeline architecture (un-peer-reviewed — flagged): https://dev.to/satviksingh192/what-if-the-scoreboard-had-no-delay-3gi2

**Ours:** 7-VOD frame/transcript analysis (`DEEP_ANALYSIS/`, `VISUAL_BROADCAST_BIBLE.md`); code-verified audits (`SPECTATOR_COVERAGE.md`, `PROJECT_INVENTORY.md`, Phase 22 ledger).

*— End of research. Nothing in this document authorises a build; §6 is the prioritised proposal list for the user's approval. —*
