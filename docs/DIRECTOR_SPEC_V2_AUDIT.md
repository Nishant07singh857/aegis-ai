# DIRECTOR SPEC v2 AUDIT — External Spec vs AEGIS Reality (Phase 25–29 ke baad)

**Date:** 2026-10-22 · **Type:** Evaluation only — kuch build nahi hua, kuch commit nahi hua.
**Input:** User ki laayi hui doosri external director spec (`~/workspace/drafts/director-spec-v2-user-paste-2026-10-22.md`) — Drop/Rotation/Fight terms, 4-Step Scene Rule, 4 decision rules, 5-if-else cheat-sheet.
**Judged against:** (1) standing **SPECTATOR-ONLY** directive — koi game API/telemetry exist hi nahi karta; inputs = 16 observer-phone screens ke vision events + spotter · (2) frame-verified grammar — `docs/VISUAL_BROADCAST_BIBLE.md` (U9: fights ke beech NO PiP/split · U10: holds early 15–30s / mid 45–90s / late 60–120s, final circle no switching · U12: replay kabhi mid-fight nahi · map cam 15–30s) · (3) asal code — har verdict 4 independent code-audits ke file:line saboot par hai (Phase 30, W1–W4), phase summaries par nahi.
**Format note:** User ke nirdesh par ye doc **kaam ki cheezon se shuru** hota hai. Poora element-by-element lekha-jokha Appendix A me hai — har faisla wahan traceable hai.

**Score (30 elements):** ✅ HAVE 6 · 🔧 BUILDABLE-ADAPTED 14 · 🚫 TELEMETRY-ONLY 5 · ⛔ CONTRA-GRAMMAR 5
(v1 audit ke 44 rows se alag elementization hai — v2 spec ki granularity alag hai, counts seedhe compare mat karo.)

---

## 1. KAAM KI CHEEZEIN — sirf ye banane layak hai (= prioritized build list)

Har item: spec ka wo hissa jo **aaj sach me missing hai** aur spectator-only me imaandaari se ban sakta hai. Order = impact/effort.

### 1. Dormant producers ki wiring — drop-clash alert, phase suggestions, PRE_COMBAT intakes
**Abhi haal:** Teen features ke consumer (LiveDirector) ready aur tested hain, lekin unke producers ke **production callers zero** hain — `FlightPathService.check_drop_clashes`, `DirectorService.propose_phase_suggestions`, `LiveDirector.note_rotation()/note_potential_fight()` sirf tests me chalte hain. Matlab drop-clash alert, phase-wise director salah aur rotation-based anticipation asal app me **kabhi fire hi nahi hote**.
**Adapted roop:** Flight API / match lifecycle / VI rotation path se ye callers jod do — koi nayi science nahi, sirf taar jodna hai.
**Kyun kaam ka:** Bina naya kuch invent kiye spec ke teen hisse (Drop Clash, phase flow, anticipation) ek saath zinda ho jayenge. **Effort: LOW–MED.**

### 2. Clutch ko zinda karo + solo-player lock (Spec Rule 1 + Cheat 4 ka asal roop)
**Abhi haal:** LiveDirector me clutch ka pura mechanism hai (+25 bonus, margin 5, CLUTCH state) — lekin `FightDetector` **`is_clutch` kabhi set hi nahi karta** (uska clutch check literally khaali `pass` hai), to live path par clutch **kabhi flag hi nahi hota**. Aur incumbent clutch ko koi protection bhi nahi — margin 5 clutch ko camera *chheenene* ka sabse sasta rasta banata hai, lock nahi.
**Adapted roop:** VI me lone-survivor derivation (team alive == 1 + fight active / nearest enemy ≤ 60px) → `is_clutch` asal payload par; incumbent clutch par final-zone-jaisa block rule; Phase 29 pilot me solo player par lock rule (R3b). Spec ka "40m" number **px me rahega** — metres sirf calibrated scale (`AEGIS_VISION_MINIMAP_PX_PER_METER`, default unset) ho tabhi.
**Kyun kaam ka:** Spec ka sabse bada rule (Risk of Elimination) kagaz par pura hai, taar kata hai — ye wahi taar hai. **Effort: MED.**

### 3. Championship close-gap uplift (Spec Rule 3 ka honest roop)
**Abhi haal:** Rank uplifts (rank1 +0.10, rank2 +0.06) aur match-point/Smash awareness (+0.10) live hain — lekin #1–#2 ke beech ka **points gap kabhi compute hi nahi hota**; spec ka 2x, W cap 1.35 ke design-usool se mana hai (neeche reject #6).
**Adapted roop:** Standings table scoring se pehle maujood hai — gap ≤ 5 pts ho to dono top-2 teams ko W ke andar +0.05; aur "drop everything" ki jagah championship-fight challenger ko maujooda clutch-margin (5) wala rasta. Cap kabhi nahi tootega, storyline kabhi switch manufacture nahi karegi.
**Kyun kaam ka:** Finals-day ki sabse badi kahani (5-point race) ko director aaj pehchanta hi nahi; design audit me ready-made hai. **Effort: LOW.**

### 4. Zone reveal ka on-screen jawab (Spec "Hard Shift / 8-Second Rule" + Cheat 1 ka grammar-true roop)
**Abhi haal:** Naya circle aane par poori chain wired hai (geometry change → winners/losers → `zone_pull` alert → director) — lekin on-air asar sirf andar ke +10 score points hai. **Screen par kuch nahi hota.**
**Adapted roop:** Koi promoted fight na chal rahi ho aur final circle na ho → maujooda context-beat machinery se **15s map_cam beat** (Bible ki 15–30s range ke andar, 30s cap code me enforced hai); fight chal rahi ho → sirf winners/losers graphic, cut kabhi nahi. Spec ka "slam within 30m" exception naapa hi nahi ja sakta — uski jagah "koi promoted fight active nahi" wali shart.
**Kyun kaam ka:** Har match me 6–9 baar aane wala caster-context moment aaj invisible hai. **Effort: LOW–MED.**

### 5. Payoff pack — Elimination banner + winner preference (Scene Rule Step 4 ka asal roop)
**Abhi haal:** `team.eliminated` event aur lower-third template plumbing maujood hai, lekin **"TEAM X ELIMINATED" banner template hai hi nahi**; aur fight resolve hone par (aaj 25s inactivity ka andaz — koi `fight_ended` producer nahi) jeetne wali team ko next-incumbent preference nahi milti.
**Adapted roop:** Elimination banner template (event → lower-third plumbing par) + resolve par winning team ko agle faisle me preference. Bible ka 2–3s winner-switch tabhi sach hoga jab fight-end ka asal signal bane — ye usi disha ka pehla, imaandaar kadam hai.
**Kyun kaam ka:** Squad wipe broadcast ka sabse bada graphic pal hota hai — hum use aaj bina banner ke guzaar dete hain. **Effort: LOW–MED.**

### 6. Slam anticipation (Spec "Slam / Blind Crash" ka asal roop)
**Abhi haal:** Vehicle-rotation ke signals bante hain (rotation start event, bearing, speed px/s, `rotation_into_occupied_poi` → PRE_COMBAT) — lekin rotation LiveDirector ke consumed intel kinds me **hai hi nahi**, aur item 1 wala `note_rotation` caller bhi missing hai.
**Adapted roop:** Rotation intel ko director ke intel path me jodo → PRE_COMBAT par **PVW pre-position** (preview hamesha taiyaar; PGM anticipation par kabhi blind-cut nahi hoga). Crash rukte hi fight promotion → aam hard cut usi team ki feed par; pilot ka fighting-teammate rule "star assaulter" ka roster-saboot wala badal hai. "3 sec pehle overhead free cam" nahi banega — free cam exist hi nahi karta (reject #10), aur koi measured lead-time bhi nahi hai (TTL lifetimes ko anticipation mat samjho).
**Kyun kaam ka:** Compound crashes mid-game ke sabse bade pal hain; wahan "pehle se taiyaar" hona hi director ki asal kala hai. **Effort: MED** (item 1 ke baad).

### 7. Lull loop — grammar-true (Spec Rule 4 ka pehla hissa)
**Abhi haal:** Shaant phases me LiveDirector idle rehta hai ("no candidate, no decision, nothing invented") — casters ko points-table discussion ke liye koi loop nahi milta. Phase-suggestion plan coded hai par caller-less hai (item 1).
**Adapted roop:** Loot/early phases me **15–30s legs** — map → standings → top team POV — semi_auto suggestions ke roop me. Spec ke 12–15s legs Bible ki map-cam floor (15s) se neeche hain, isliye adapted roop 15–30s rakhta hai.
**Kyun kaam ka:** Show kabhi khaali nahi lagta, casters ko unki baat ka saaman milta hai — bina grammar tode. **Effort: LOW–MED** (item 1 ke baad).

### 8. Center-hold derivation (Spec "Center / God Compound")
**Abhi haal:** Aisi koi derivation hai hi nahi — jabki saare primitives maujood hain: zone-centre distance (px), favoured-team (sabse zyada markers andar), POI `type="compound"` (26/127), RotationTracker se stationarity.
**Adapted roop:** Naya `center_hold` intel alert (andar + centre band + stationary dwell + compound POI) → maujooda pattern se zone-pressure/precombat weight + semi_auto suggestion us team ki feed dikhane ko. Isi ka bhai choke-point hai — lekin bridge POI data sirf **2 hai, sirf Erangel**; pehle data bharo, phir derivation.
**Kyun kaam ka:** "Yahan baad me crash hoga" wali jagah ko director pehle se pehchane — anticipation ki ek aur parat, saste me. **Effort: MED.**

---

## 2. PEHLE SE HAI (confirmations — vistaar nahi)

**6 pure HAVE:** Entry Knock (knock par knocker ki feed/POV — pilot R3/R4 se) · Trade Knock (hold + margin + cooldown se structural) · Third-Party (detection + LiveDirector consumer live, +20 precombat) · Scene Step 3 hard-cut climax · Rule 1 ka scoring mechanism (caveat item 2 me) · Rule 2 (phase-timer hold 30/45/60s + margin 40, dusri fight ki replay-queue handoff, stinger packaging contract — asset abhi payload-level hai, verified render nahi).
Iske alawa: context-first 3s map beat (sirf anticipation cuts par — jaanboojh kar), WWCD ceremony, match-point/Smash awareness, gatekeep alert, replay take ka fight-time hard block — sab live hain (Appendix A).

---

## 3. REJECT LIST (ek line each — in par waqt nahi lagana)

1. **PiP/split during fights** (Rule 1 ki PiP, Heal Battle PiP, Cheat 5) — ⛔ U9: 7/7 streams me kabhi nahi; dusri fight box me nahi, replay queue me jati hai (code me bhi fight-feed PiP keyspace se unreachable hai).
2. **Scene Rule ke durations live direction ke roop me** (3s + 2s + 4–8s + 2s) — ⛔ U10: asal holds 45–120s hain; ye poora package ek **highlight cut** hai, fight coverage nahi (vishaal vishleshan §4-Q1).
3. **"4-Second Hold" naam/number** — ⛔ asal hold pehle se zyada majboot hai: phase-timer 30/45/60s + hold-margin 40.
4. **Hard Shift ka unconditional 8s forced map cut** — ⛔ beech-fight cut U9 ke khilaf, aur 8s Bible ki 15–30s map range se neeche. (Grammar-true roop kaam #4.)
5. **Reset ke beech 5–8s ke liye replay/dusri fight par dip** — ⛔ U12: replay kabhi live fight ke upar nahi; replay take code me bhi fight ke samay hard-blocked hai. Reset abhi bhi ek *live* fight hai.
6. **Rule 3 ka 2x multiplier + "drop everything"** — ⛔ AEGIS ke apne design-usool ke khilaf: W cap 1.35, base 0 ⇒ score 0 — storyline kabhi switch manufacture nahi kar sakti. (Honest roop kaam #3.)
7. **Cheat 5 ka ">85 score" threshold** — ⛔/be-maani: AEGIS ka score 0–100 scale hai hi nahi (theoretical max ≈ 277); faisle absolute thresholds par nahi, margins (40/15/10/5) par hote hain.
8. **Listen-In — jeetne wali team ki in-game voice chat** — 🚫 spectator feed me dusri team ki voice hoti hi nahi; system me koi comms source hai hi nahi (v1 F3 ka dohraav).
9. **Nade Trace/Follow Cam + "grenade cooked" trigger + predicted damage > 80** (Cooking, Cheat 3) — 🚫 spectator screen par projectile track nahi hota (1–2 frames, occlusion, no depth); cooked state ka koi signal nahi; predicted grenade damage naam ki cheez code me hai hi nahi; explosion/stun detection measured gates par default OFF hai.
10. **Drone/Free Cam + X-Ray team outlines** (Drop Clash, Choke, Third-Party, Scene Step 1) — 🚫 consumer spectator server-filtered hai, god view nahi; AEGIS ke paas movable camera hi nahi; X-Ray outlines production layer ka render hain, observer client ke nahi. (Free cam — §4-Q2.)
11. **Player Facecam Overlay** (Scene Step 4) — 🚫 is rig me koi facecam feed source hai hi nahi (UI imaandaari se "FACECAM — NO FEED" placeholder dikhata hai); ye venue hardware/ingest ka kaam hai, director feature nahi.
12. **Vehicle Grab race + Cheat 2 ke numbers (60 km/h, 80m, arrival se 3 sec pehle)** — 🚫 vehicle data sirf marker **count** hai; speed px/s hai — km/h calibrated scale bina banta nahi (scale default unset, rig par kabhi measure nahi hua); "enemy compound distance" naam ka concept code me nahi (enemy markers anonymous hain); koi arrival predictor nahi.
13. **Gatekeeping par bahar wali team ke "First-Aids remaining" dikhana** — 🚫 dusri team ka inventory spectator screen par kabhi render nahi hota; jo hai wo heals-*used* count hai (wo bhi bina naam ke) — remaining dikhana fabrication hota. (Gatekeep alert khud HAVE hai.)
14. **Entry Knock ka "scope kholte hi" camera** — 🚫 koi ADS/scope signal spectator se nahi milta; knock ke baad knocker par hona pehle se hai (HAVE).

---

## 4. KHAAS SAWAAL — compact jawab

**Q1. Scene Rule ke durations (3s + 2s + 4–8s + 2s) — highlight cut hai ya poori fight coverage?**
Highlight cut hai. Poora package 10.5–15s ka hai — Bible ke naape hue **akeli map-cam leg (15–30s)** se bhi chhota; asal fight coverage: ek fight par 60–120s, ek player par 45–90s (§3 measured), winner switch fight ke 2–3s baad (§4). Spec ka 4–8s "climax" asal mid-game hold ka lagbhag daswa hissa hai. Live direction ke roop me har step contra hai; **replay/clip template** ke roop me yehi structure AEGIS ke replay queue + renderer par thik baithta hai — iski sahi jagah wahi hai.

**Q2. "Free Cam / Drone Cam" — consumer spectator me hota bhi hai? Phase 29 pilot use chala sakta hai?**
Repo me **koi saboot nahi** ki hota hai; saboot ulta hai — production research: consumer follow-spectate server-filtered hai, "no full-map god view"; gated esports client organiser-only hai aur uski camera-mode list kahin documented nahi. Phase 29 pilot sirf follow-spectate ke andar **spectated player** badal sakta hai (roster tap + nameplate verify) — free camera ghumana uske paas hai hi nahi. Pakka jawab sirf organiser programme / scrim par milega — **guess nahi karenge**.

**Q3. Slam pre-positioning (crash se 3 sec pehle) — Phase 24/25 signals se kitna mumkin?**
Signal aadhar hai: rotation start event, bearing, speed (px/s), `rotation_into_occupied_poi` → PRE_COMBAT, inferred vehicle mode. Do bandishen: (a) `note_rotation` ka production caller hi nahi (kaam #1/#6); (b) **koi measured lead-time exist nahi karta** — PRE_COMBAT TTL 20s signal ki *lifetime* hai, "3 sec pehle" jaisa daava nahi. Asal roop: PVW pre-position + promotion par hard cut. "3 sec pehle overhead" telemetry-kalpana hai.

**Q4. Lull loop (12–15s map→leaderboard→POV) ka adapted roop?**
12–15s legs Bible ke map-cam floor (15s) se neeche hain. Adapted: loot/early phases, **15–30s legs**, semi_auto suggestions (map → standings → top-team POV), 30s map cap enforced. Phase-suggestion plan coded hai — caller judte hi (kaam #1) iska aadha hissa wahi se aata hai; baaki timed cycle + star-POV leg kaam #7 hai.

**Q5. "Risk of Elimination" — Interest Score + clutch margin se kitna pehle se hai, kya missing hai?**
Mechanism hai: clutch +25, clutch-challenger margin 5, rank W factors, final-zone locks. Missing teen cheezein: (a) **`is_clutch` live path par kabhi set hi nahi hota** — mechanism dormant hai; (b) koi **elimination-risk term** nahi (last-player-alive / teams-alive score me kahin nahi; rank sirf multiplier hai, base 0 ⇒ 0); (c) precedence ulta hai — fight-hold window ke andar clutch challenger ko bhi margin **40** chahiye, 5 nahi; aur incumbent clutch ko koi lock nahi. Yani spec ka sabse bada rule aadha bana, aadha kata — poora fix kaam #2 hai.

---

## Appendix A — Poora element-by-element lekha-jokha (30 elements)

Evidence sankshipt hai; poori file:line jaankari Phase 30 worker reports (W1–W4) me hai. Paths `backend/app/` ke relative hain jab tak alag na likha ho.

### Drop (4)
| # | Element | Verdict | Saboot / adapted note |
|---|---|---|---|
| D1 | Flight Path → Map View jab tak pehli team jump na kare | 🔧 | Flight path director-typed hai, vision source nahi (`services/flight_path.py:1-12`); "plane phase" elapsed-clock heuristic hai (`vision/parachute.py`), first-jump event exist nahi karta. Adapted: PhaseTracker → phase plan wiring (caller missing — kaam #1); map 30s cap enforced. |
| D2 | Drop Clash → Red Alert + High Free Cam | 🔧 | Detector coded + imaandaar (`flight_path.py:285-367`, normalized coords, metre nahi); consumer watch-only (+10 pts, `live_director/config.py:96-100`); **producer ka koi production caller nahi**. Free-cam half 🚫. Adapted: caller wiring + observed clash par map_cam (kaam #1). |
| D3 | Split Drop → isolated player ke POV par cut | 🔧 | Split/isolated live, **sirf minimap px** me (spread >80px, isolated >60px — `vision_intelligence/geometry.py`); director ko team-level precombat (+8) milta hai. Player-level cut identity-blocked: marker anonymous hai, named isolated player kabhi claim nahi hota (SIGNAL_REGISTRY P6). Adapted: isolated + nearest-enemy join → team weight (kaam #1 se juda). |
| D4 | Vehicle Grab → Follow Cam | 🚫 | Vehicle data sirf **count** hai (`vision/detectors/minimap_reader.py:160`); position/type/occupants kabhi track nahi hote; do identified players ka "race" pehchanna namumkin; Follow Cam scene hai hi nahi. |

### Rotation (5)
| # | Element | Verdict | Saboot / adapted note |
|---|---|---|---|
| R1 | Hard Shift → 8-Second Rule, turant Map View | ⛔ (roop 🔧 kaam #4) | Trigger asal hai: `zone_pull` winners/losers (`vision_intelligence/zone_state.py`) → director sirf +10 pts. Unconditional beech-fight cut U9 ke khilaf; 8s, Bible ki 15–30s map range se neeche. |
| R2 | Scout / Ahead Man → scout ke POV par raho | 🔧 | Scout concept live stack me nahi; RotationTracker sirf team **centroid** (bearing/speed px/s) rakhta hai; lead marker anonymous hai. Adapted: rotation par us team ki **feed** preference-hold; andar pilot ka fight-lock. |
| R3 | Choke Point / Bridge Camp → Drone view | 🔧 | Drone 🚫 (koi movable camera nahi); choke derivation bhi nahi — gatekeep POI-free geometry hai (15px edge band). POI `type="bridge"` sirf 2/127. Adapted: stationary + POI + incoming rotation → `choke_point` alert (kaam #8 ka bhai, data pehle). |
| R4 | Gatekeeping → wide angle + First-Aids remaining | 🚫 (alert ✅) | Gatekeep detection live hai (`derivations.py` FightForecaster, `inferred:true`), director consume karta hai. Lekin inventory/backpack ka koi reader nahi — spectator screen par dusri team ke heals dikhte hi nahi. |
| R5 | Center / God Compound → holder ka setup dikhao | 🔧 | Derivation nahi hai; primitives saare hain (centre distance px, favoured team, compound POIs, stationarity) → kaam #8. |

### Fight (8)
| # | Element | Verdict | Saboot / adapted note |
|---|---|---|---|
| F1 | Entry Knock → scope kholne wale ke POV par raho | ✅ (note ke saath) | Knock live trigger hai (FightDetector definitive signal; LiveDirector heat); pilot killer-naam → roster index karke usi ki feed/POV par le jata/hai rokta hai (R3/R4). Scope-open anticipation 🚫 (koi ADS producer nahi). |
| F2 | Cooking / Pre-Nade → Nade Trace Cam | 🚫 | Cooking detector exist nahi karta; explosion + stun detection default OFF (measured gates); follow-cam spectator se bhautik roop se namumkin (GRENADE_TRAJECTORY: 1–2 frames, no depth, thrower↔explosion pairing zero records production me). |
| F3 | Slam / Blind Crash → 3s pehle Free Cam, rukte hi Star Assaulter POV | 🔧 | Slam detector nahi; free cam nahi; "star assaulter" sirf legacy dead code me hai. Signals: rotation_mode="vehicle" (inferred) + occupied-POI PRE_COMBAT. Adapted = kaam #6. |
| F4 | Trade Knock → camera switch mat karo | ✅ | Explicit rule nahi, asar structural hai: trade knock incumbent fight ki activity refresh karta hai, challenger ko hold-margin 40 paar karna padta hai, 3s cooldown spec ke 2s window ko cover karta hai; pilot R4 fight-lock POV par. |
| F5 | Third-Party / Pinch → High-Angle Free Cam | ✅ (camera roop 🔧) | Detection end-to-end live (≥3 killer teams cluster → alert → LiveDirector +20 precombat, PVW pre-position). High-angle cam 🚫 — grammar-true commit hi asal roop hai, jo pehle se hota hai. |
| F6 | Reset → 5–8s ke liye dusri fight / Instant Replay | ⛔ (legal roop ✅) | Smoke live hai, revive inference hai — lekin reset abhi bhi live fight hai: Bible §4 replay/switch mid-fight mana; `ReplayQueue.can_take_now` fight-on-PGM par hard block (fail-closed). Resolve hote hi gate khulta hai — wahi legal roop, pehle se hai. |
| F7 | Clutch 1vN → 100% lock lone survivor POV par | 🔧 | Mechanism hai, **producer dead hai, lock hai hi nahi** (W2/W4 dono ki independent finding). Adapted = kaam #2. |
| F8 | Heal Battle → PiP split screen | ⛔ (detection 🔧) | PiP form U9 se mana aur code me unreachable (PiP SOURCES me fight feed hai hi nahi). Detection aadhi live hai (HealingDetector edges → VI counts) lekin heal-battle composite (heal + zone ke bahar + late phase) nahi banta; full-screen candidate ke roop me ban sakta hai — priority kaam list se neeche. |

### Scene Rule (4 steps)
| # | Element | Verdict | Saboot / adapted note |
|---|---|---|---|
| S1 | Establishing 2.5–3s: Drone + X-Ray outlines | 🔧 (drone/X-Ray 🚫) | 3s map_cam context beat hai (`live_director/config.py:113-115`) — lekin sirf anticipation cuts par; hot fight beat ko abort karta hai, har fight par nahi lagta. Drone feed rig me hai hi nahi (16 phone MJPEG); X-Ray renderer kahin nahi. |
| S2 | Trigger 2s: Follow Cam / Nade Trace | 🔧 (nade trace 🚫) | Follow-cam mode nahi; signals (ROTATING, rotation-into-occupied-POI → PRE_COMBAT) hain. Adapted build-up = PVW pre-position + 3s map beat. Nade trace F2 jaisa 🚫. |
| S3 | Climax 4–8s: entry fragger POV hard cut | ✅ (duration ⛔) | Hard cut hi ekmatra transition hai live path par; selection team→slot hai, andar ka player pilot chunta hai (entry-fragger ki pehchaan wala signal nahi). 4–8s duration U10 ke khilaf — holds 30/45/60s code me enforced. |
| S4 | Payoff 2s: winner POV + facecam + elimination banner | 🔧 (facecam 🚫) | Winner-switch/payoff timer nahi hai; fight resolve 25s idle inference se hota hai. Banner: event + plumbing hai, template nahi → kaam #5. Facecam: koi feed source nahi, UI "NO FEED" placeholder dikhata hai. (WWCD ceremony alag se ✅.) |

### Decision Rules (4)
| # | Element | Verdict | Saboot / adapted note |
|---|---|---|---|
| DR1 | Risk of Elimination pehle; clutch PGM, slam PiP me | ✅ mechanism (PiP ⛔) | Score: base = precombat(≤30) + combat(≤50 heat) + zone(≤20) + clutch 25 + priority(≤30), × W ≤1.35; margins 40/15/10/5. Caveats: hold window me clutch challenger ko bhi 40 chahiye; elimination-risk term nahi; `is_clutch` producer dead (kaam #2). PiP half reject #1. |
| DR2 | Don't Break the Story ("4-Second Hold") + EVS queue + stinger 5s replay | ✅ (naam/number ⛔) | Hold: phase-timer + margin (C(i) W3). Queue: dusri fight `replay.candidate` → READY ≤2, TTL 180s, take fight ke time blocked. Stinger: packaging contract hai (`replay/queue.py` `stinger_in/out`) — lekin asset/frontend handler grep me nahi mila, verified render nahi; 5s duration ka koi rule nahi (window renderer ke pre/post-roll se banta hai). Isolated single knock queue me nahi jata (qualifying kinds + min score 60 chahiye). |
| DR3 | Tournament Context: #1/#2 gap ≤5 → 2x priority; #1 ki chhoti fight par sab chhodo | ⛔ letter (spirit ~60% ✅) | 2x cap se structurally namumkin (W ≤1.35, `storyline.py` min() se truncate). Gap compute nahi hota. Rank + match-point uplifts live hain. Adapted = kaam #3. |
| DR4 | Caster Sync: lull loop 12–15s + Listen-In 8s | 🔧 (listen-in 🚫) | Timed lull cycler nahi hai; phase suggestions (semi_auto) + map 30s enforcement hai. Adapted loop = kaam #7. Listen-in reject #8. |

### Cheat-Sheet (5)
| # | If-Else | Verdict | Saboot / adapted note |
|---|---|---|---|
| C1 | New circle → forced Map 7s (slam <30m exception) | 🔧 | Event ✅ (zone_pull); forced map ❌ ("this service never selects map_cam autonomously" — director grammar docstring). 7s scene ke roop me Bible range se neeche; beat ke roop me mumkin. Exception ka 30m naapa nahi ja sakta. Adapted = kaam #4. |
| C2 | Speed >60 km/h + compound <80m → overhead cam, 3s pehle | 🚫 | Speed sirf px/s (km/h conversion calibrated scale par, default unset); compound-distance concept nahi; arrival predictor nahi; free cam nahi. Naapne layak: bearing, speed_px_s, rotation_mode (inferred), zone-edge ETA. |
| C3 | Grenade cooked + predicted damage >80 → follow cam | 🚫 | Reject #9 dekho. `predicted_damage` live grenade ke liye code me hai hi nahi; utility damage field default 0 emit hota hai. |
| C4 | Alive==1 + enemies <40m ≥2 → solo POV lock | 🔧 | Solo state observable hai (VI `_team_alive`, pilot roster status); 40m px hai, calibrated bina metres nahi; per-radius enemy count nahi banta (nearest distance + kul marker count banta hai); lock rule hai hi nahi. Adapted = kaam #2. |
| C5 | Two fights >85 → 75/25 PiP split | ⛔ | Reject #1 + #7. AEGIS ka anti-panic design pehle se hai: incumbent hold + haarne wali fight replay queue me. |

---

## Appendix B — Chaukane wale findings (audit ke side-products)

1. **Clutch teen audits me dead paya gaya.** W2, W3, W4 — teeno ne independently pakda: `is_clutch` ke saare `True` assignments legacy/dead stacks me hain; live producer use kabhi set nahi karta. Director ka clutch bonus/margin/state aur replay ka `kind="clutch"` rasta — sab dormant hain, "finished feature" padhne ke bawjood.
2. **Dormant-producer pattern sirf clutch tak nahi.** Drop-clash checker, phase suggestions, PRE_COMBAT ke rotation/potential-fight intakes — chaar jagah consumer ready, caller zero. Phase 24/25 ki kai "live" kahaniyan aakhri taar par ruki hain. Kaam #1 + #2 isi pattern ka ilaaj hai.
3. **Do "fight over" ghadiyan.** FightDetector 8s inactivity par resolve karta hai, LiveDirector 25s par — beech ke ~17s director fight ko live manta hai (holds lagte hain, replay gate band rehta hai) jab detector use khatam maan chuka hai. Payoff timing (kaam #5) ke saath iska milaan bhi hona chahiye.
4. **ULTA margin.** Spec clutch ko lock chahta hai; AEGIS ka margin 5 clutch ko sabse sasta *challenger* banata hai. Lock ek nayi block rule hai, tuning change nahi.
5. **`TransitionController` dead code me forbidden inventory.** FADE/SWIPE/SLIDE/STINGER types defined hain, live caller koi nahi; uski ek Bible-aware method har moment ke liye Cut hardcode karti hai. Galti se wire ho jaye to Bible-mana transitions ek config door hain — sambhal kar rakho.
6. **Zone-pull ki jeet-haar director ke liye ek jaisi hai.** Winners aur losers dono ko same +10 milta hai — `zone_state` ki rich ranking camera faisle se pehle hi flatten ho jati hai. Kaam #4 ke saath iska farak bhi sudhar sakta hai.

---

*Evaluation ends. Build sirf user ke shabd par — upar ki 8 kaam ki cheezein hi agle phases ke candidates hain; reject list band hai jab tak user khud Bible/design-usool badalne ka faisla na kare.*
