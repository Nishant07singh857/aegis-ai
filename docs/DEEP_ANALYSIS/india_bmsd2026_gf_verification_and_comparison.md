# VERIFICATION & COMPARISON — BMSD 2026 GF Day 2 / Day 3

**Analyst:** STREAM ANALYST B (India)
**Date:** 2026-10-19
**Purpose:** Independent frame-verified check of Analyst A's `india_bmsd2026_gf_day2.md` / `india_bmsd2026_gf_day3.md` (written in parallel, same session), plus the cross-tournament / cross-day comparison from the original mission. **This file corrects — it does not duplicate — the two per-stream docs.**
**Method:** 146 frames @ 5-min intervals across both streams + 10 targeted frames (640×360), web cross-reference (talkesport / sportsdunia / sportsdigest). No transcript (Hindi feed; YouTube subtitle fetch blocked; local transcription infeasible on shared 2-CPU box).

---

## 1. Assignment status

| Requested stream | State |
|---|---|
| `bmsd2026_semis_day1/2/3.mp4` | **NOT READY** — `.part` files stalled since 07:04 UTC (578/501/504 MB, zero growth over 90+ min). Not analyzed. |
| `bmsd2026_lastchance_day2.mp4` | **NOT READY** — `.part` stalled at 989 MB since 08:35 UTC. Not analyzed. |
| `bmsd2026_gf_day1.mp4` | Ready; previously analyzed from transcript (memory). Used as baseline. |
| `bmps2026_gf_day1.mp4` | Ready; analyzed by me pre-restart (`india_bmps2026_gf_day1.md`). Used for BMPS-vs-BMSD. |
| `bmsd2026_gf_day2.mp4` | Ready — **already covered by Analyst A** (deep doc, 196-switch camera log). Independently verified below. |
| `bmsd2026_gf_day3.mp4` | Ready — **already covered by Analyst A**. Independently verified below; **2 corrections**. |

The official Krafton India Esports playlist (`PLBXXigHs5dr8`) confirms the pending streams exist as **[HINDI] Semi-Finals Day 1–4** VODs — note **4** semi-final days, not 3.

---

## 2. Verification results

### 2a. Confirmed correct (frame-verified by me)
- **Day-2 final standings** — my `OVERALL STANDINGS` frame (@ t=19500) matches Analyst A's table exactly: 8BIT 116 (3/73/43), K9 100 (1/64/36), BLEG 99 (2/60/39), SOUL 88 (1/58/30), WELT 85 (1/51/34), OR 85 (1/60/25), NINZ 77, GDR 73, TAG 70, MYTH 69, WF 67, RNTX 67, V1BA 55, NBE 47, GODL 45, GENS 28.
- **Day-3 final standings** — my `POST MATCH 18` frame (@ t=17400) matches exactly, incl. Soul's team card (damage 22698, knocks 95, avg survival 21:30): SOUL 155, GDR 141, 8BIT 140, K9 135, BLEG 133, OR 123, V1BA 122, WELT 120, NINZ 113, TAG 98, RNTX 89, MYTH 88, WF 85, NBE 83, GODL 68, GENS 67.
- **Day-2 match winners** (OR/GDR/RNTX/8BIT/BLEG/SOUL) — consistent with my break winners-strip frame (@ t=12000: M7→OR, M8→GDR, M9→RNTX, M10→8BIT) and sportsdunia.
- **Day-3 M13–M17 winners** (GDR/SOUL/K9/WELT/V1BA) — consistent with my break-strip frame (@ t=12300) and all three web sources.
- **Smash point 126** (= 8BIT's 116 + 10) and the **Smash Rule explainer** — my frame (@ t=2100, Day 3) captured the full text (see §4).
- **Prize graphics**: CHAMPIONS SOUL ₹30,00,000 (static end card to VOD end, frames @ t=21000/21900/22500); trophy lift (@ t=18300); stage celebration (@ t=19200).
- **Hard cuts only**; overlay layout identical across days; map rotation Rondo→Erangel×3→Miramar×2 all three days.
- **Day-2 talkesport table is wrong** (see my note in the original mission draft): the broadcast graphic is ground truth. Analyst A used the graphic — correct.

### 2b. CORRECTION 1 — Match 18 winner (material)
Analyst A's Day-3 doc (§1 table, §2 seg 12, §5 F6, §7) states **"SOUL wins → champions"** and *"SOUL converted match point by winning Match 18."*
**This is incorrect.** Three independent sources agree **Version1 x Blue Army won Match 18** (Miramar, 8 finishes):
- talkesport winner article: *"Version1 x Blue Army won Match 18, and SouL's lead held to the end"* (match table: M18 → Blue Army, 8 elims).
- sportsdunia Day-3 live: *"Blue Army finished the tournament with a win. Soul wins BMSD 2026 at Hyderabad."*
- sportsdigest: *"Blue Army won the Chicken Dinner in that match, but Team Soul's total points and Smash status secured the overall BMSD 2026 title."*

**What actually happened:** Soul reached Match Point (126) but could not convert in M17 (Blue Army denied them — my frame @ t=14500 shows V1BA celebrating with "WINNER" behind). **No match-point-eligible team won a WWCD on Day 3**, so the title was decided by the **Smash Rule fallback**: most total points after 18 matches → **Soul 155**. The broadcast skipped the M18 WWCD graphic and cut straight to the championship (my frames: M18 endgame @ t=16800 → trophy presentation @ t=17100), which likely caused the misread — an understandable error, since the celebration footage is all Soul.

**Implication for AEGIS:** the smash-rule state machine needs the *fallback* path as a first-class broadcast beat ("no conversion → points decide"), not just the conversion path.

### 2c. CORRECTION 2 — Day-3 right-panel layout (minor)
Analyst A (§4, §7.7) claims the Day-3 right panel *"shows live TOURNAMENT POINTS (not finishes)"* — i.e., a changed layout vs Day 2.
**Frame evidence shows the same two-column layout on both days:** `# | TEAM | match-finishes | TOTAL PTS`.
- Day 2 @ t=5400 (M8): "BLEG 0 74" → 60 + 14 (M7) = 74 total ✓; first number = M8 finishes so far.
- Day 3 @ t=6000 (M14): "8BIT 0 117" → 116 + 1 = 117 ✓; first number = M14 finishes so far.
- Day 3 @ t=8100 (M15): "SOUL 0 127" → 88 + 39 (M13+M14) = 127 ✓.
The genuine Day-3 addition is the **green highlight on the smash-point leader's row** (Analyst A observed "1 SOUL 127" green @ ~02:15 — consistent), not a layout change.

### 2d. Unresolved flag
- **M15 K9 points:** Analyst A logs 8 finishes (= 18 pts); sportsdunia says *"K9 earned 23 points in this match."* 10 + 8 = 18 ≠ 23. One of the two is off (possibly K9 had 13 finishes, or sportsdunia conflated). Not resolvable from my frames — flagged, not asserted.

---

## 3. Comparison: BMSD GF across days, and BMPS vs BMSD

### 3a. Day 1 → Day 2 → Day 3 (same package, escalating narrative systems)

| Element | Day 1 | Day 2 | Day 3 |
|---|---|---|---|
| Preshow | Bumper → Hot Seat → desk → opener → **16 team walkouts** | 25-min countdown → desk (no ceremony) | 20-min countdown → trophy showcase → desk |
| Overlay/HUD | Base package | Identical | Identical + **green smash-leader highlight** |
| Mid-day break | Short (stats → desk) | **"SHOW RESUMES IN" + winners strip** | Same + RACE TO SMASH chart |
| Narrative system | Cumulative points | Cumulative + "moving day" framing | **Smash Rule** (explainer, bar chart, scorebug, fallback) |
| Award boards | MVP/Eliminator/Best IGL/Finals MVP | FINALS MVP + **GOOGLE PLAY MVP** | GRENADIER contenders + prize-money cards |
| Closing | Standings + montage | MOMENTS → MVP → DAY RECAP → standings → thanks loop | Trophy lift → celebration → awards → DAY RECAP → **25-min static CHAMPIONS card** |
| Replays | None observed | 1 ("REPLAY" @ 00:54, per Analyst A) | 2 (M17 fight, M18 vehicle-stunt loop) |
| Production spend | Ceremony (one-time) | Narrative packaging | Championship ceremony |

**Verdict:** one stable broadcast package across 3 days; each day adds exactly one narrative layer (Day 2: recap systems; Day 3: smash-rule + prize systems). Ceremonial spend is Day-1/ Day-3-only.

### 3b. BMPS 2026 GF vs BMSD 2026 GF (different tournaments, same vendor grammar)

| Element | BMPS GF Day 1 | BMSD GF (all days) | Verdict |
|---|---|---|---|
| Overlay positions | KF TL, right panel, facecam BR, top bar | **Identical** | Same vendor |
| Transitions | Hard cuts | Hard cuts | Same |
| PiP grammar | Map + gameplay only | Map + gameplay; Day-3 adds crowd PiP in endgame | Same +1 |
| Persistent bug | "M{n} – {MAP}" | "GRAND FINALS DAY n / MATCH n" | Cosmetic |
| Preshow | Fan-zone packages, "TEAM'S JOURNEY" per-team road-to-GF | Countdown + desk + standings/map strip | **Different packaging** |
| Ceremony | Opening ceremony + 16 walkouts | Day 1 only (same pattern) | Same |
| Scoring | 10 + 1/finish, WWCD column | 10 + 1/finish, WWCD column | Identical |
| Unique systems | Team's Journey, "FORGING THE FUTURE" branding | Smash Rule (Day 3), "FOR THE THRONE" branding | Each has one |
| Commentary | English-dominant + Hinglish | **Hindi** (analyzed feeds) | Different feeds |

**No evidence of a cheaper production anywhere.** BMPS is not "smaller" than BMSD — both are full-venue Krafton productions with the same observer/HUD grammar; they differ only in packaging and branding.

### 3c. Semis / Last Chance — what can be said without the VODs
Nothing first-hand (downloads stalled). Indirect facts worth recording for the follow-up analysis:
- Semis fed **8 of 16** GF slots; Last Chance fed **4** (Myth, Welt, Genesis, Wyld Fangs); Qualifiers Group A fed 4 (Meta Ninza, 8Bit, RNTX, BLEG). — talkesport
- Defending champions **Orangutan were eliminated before the GF** (didn't survive semis/last chance). — talkesport
- 4 semi-final days exist (playlist), vs 3 covered by the download list — the Day-4 semis VOD was never queued.
- **Prediction for the eventual analysis:** check whether semis/lastchance use the same overlay package (likely yes — same vendor) with reduced ceremony (likely — Day-2 pattern suggests ceremony is GF-Day-1-only). Verify, don't assume.

---

## 4. Smash Rule broadcast system (frame-verified reference for AEGIS)

From the Day-3 fullscreen explainer (@ t=2100), transcribed verbatim:
> **THE SMASH RULE**
> • Match Point Target: After Match 12, the 1st-place team's score + 10 points sets the Target.
> • To Win: From Match 13 onward, a team must: Reach the Match Point Target. Win a Chicken Dinner in a later match.
> • Important: Reaching the Target and winning the Chicken Dinner must happen in separate matches.
> • No Winner: If no team completes both conditions by Match 18, the team with the most overall points wins.

Broadcast implementation observed: (1) fullscreen explainer in preshow; (2) **RACE TO SMASH** bar chart ("SMASH POINT – 126", "REQUIRED POINTS") in breaks; (3) green highlight on the smash leader's row in the persistent right panel; (4) smash scorebug strip over the player-cam mosaic in M18; (5) **fallback path executed live** — no WWCD graphic for M18, straight to trophy/celebration when points decide. AEGIS's smash-rule module needs all five beats, including the fallback.

---

## 5. Files
- This doc: `docs/DEEP_ANALYSIS/india_bmsd2026_gf_verification_and_comparison.md`
- Frames: `data/analysis/india_streams/analyst_b/frames/bmsd_gf2_*.jpg` (15) and `bmsd_gf3_*.jpg` (18) — key evidence frames archived alongside Analyst A's set
- Per-stream deep docs (Analyst A, this session): `india_bmsd2026_gf_day2.md`, `india_bmsd2026_gf_day3.md` — read with §2b/§2c corrections
- BMPS doc (mine, pre-restart): `india_bmps2026_gf_day1.md`

## Pending
- Semis Day 1–3 + Last Chance Day 2: downloads stalled (`.part` files not growing since 07:04/08:35 UTC). Re-queue or resume before analysis.
- No Hindi transcript exists for any analyzed stream — commentary-pattern analysis remains structural-only until transcription is feasible.
