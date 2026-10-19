# VISUAL BROADCAST BIBLE V2 — PUBG Mobile Tournament Broadcast (Definitive Spec)

**Date:** 2026-10-19 · **Supersedes:** `docs/VISUAL_BROADCAST_BIBLE.md` (V1)
**Analyzed from actual video frames — NOT transcript-based.**
Video: 640×360 @ ~30fps (YouTube 360p rips). Positions as % of frame (scale to 1920×1080).

## SOURCES (7 streams)

| # | Stream | Method | Depth |
|---|---|---|---|
| 1 | BMSD 2026 GF Day 1 | Frame-level + transcript | Strong |
| 2 | BMPS 2026 GF Day 1 | Frame-level | Strong |
| 3 | BMSD 2026 GF Day 2 | 4,176 keyframes + 174 sampled frames + loudness peaks | Strong |
| 4 | BMSD 2026 GF Day 3 | 5,388 keyframes + 188 sampled frames + loudness peaks | Strong |
| 5 | PMGC 2025 GF Day 1 | Frame windows + 13× 3-min transcripts | Medium-strong |
| 6 | PMGC 2025 GF Day 2 | Frame windows | Medium |
| 7 | PMGO 2025 Main Event Day 1 | Frame windows + 4× 3-min transcripts | Medium |

**BMSD Day-2/Day-3 and all global streams are new in V2.** Two corrections from V1's Day-2/3 addendum are incorporated (§8a notes). "No X observed" claims are sampling-bound unless marked (7/7).

---

## CHANGELOG V1 → V2 (what changed and why)

| # | Change | Why |
|---|---|---|
| C1 | Added 3 new camera modes: **SCOPE** (ADS view), **SPLIT_ANALYSIS** (split-screen for analysis only), **FINAL_CIRCLE** (aerial + top-4 panel + 2×2 facecams) | Discovered on BMSD Day 2 (scope, split-screen) and Day 3 (final-circle aerial) — 1/7 streams each but championship-relevant |
| C2 | Camera-hold rule refined to a **phase table** (early 15–30 s / mid 45–90 s / late 60–120 s / final circle: hold to end) | V1 said "45–90 s"; Day-2/3 fight strips show phase dependence |
| C3 | "No split-screen" → **"no split-screen during live fights"** | Day-2 split-screen exists for flight-path/rotation *analysis* segments only |
| C4 | Right-panel Day-3 claim **corrected**: layout unchanged (# \| TEAM \| match-finishes \| TOTAL PTS) on all India days; Day-3 addition is the **green smash-leader highlight**, not a layout change | Frame-verified by Analyst B (§4b, MASTER_PATTERNS) |
| C5 | Added **Smash Rule 5-beat broadcast system** (explainer, RACE TO SMASH chart, green leader highlight, smash scorebug, points-fallback ceremony) | Observed live on Day 3; the fallback path was *executed*, not theoretical |
| C6 | Added **M18 correction**: V1BA won M18 (8 finishes); Soul's title came via Smash fallback, not M18 conversion | Three web sources + internal standings arithmetic (V1BA 0→2 WWCD, SOUL 1→2 with M14) — see §12 |
| C7 | Added **global layout variants**: PMGC top-center team stat cards, PMGO WWCD% prediction cards, facecam position per event (BMSD bottom-left / BMPS+PMGC bottom-right / PMGO bottom-center-left) | V1 assumed the India layout was universal — it isn't |
| C8 | Added **match-strip checklist** (PMGC ✓ + countdown; India map strip with played-map highlights) | Same function, 7/7 — V1 only had India's version |
| C9 | Added **whole-day highlight reel** as the day-end replay product (PMGC) | Replays 0→1→2 across BMSD days; PMGC packages them at day end |
| C10 | Added **Day-3 glitch-wipe bumpers** (break segments only) + **branded segment bugs** (FINAL BLOW / DAY RECAP) | New production artifacts; editorial cuts stay hard cuts |
| C11 | Added **prize-money ceremony graphics**, trophy-lift sequence, credits, static champions end card | First money graphics in corpus (Day 3) |
| C12 | Added **measured switch rates** (Day 2: 34/hr, Day 3: 57/hr) and observer-anticipation note (PMGC near-instant vs India 2–3 s) | First hard numbers; informs AI-director tuning |
| C13 | Facecam position marked **event-variant, not universal**; facecam *sync* kept universal | BMPS and PMGC use bottom-right; V1 said bottom-left |
| C14 | Kept **"OPERATION SDC" banner** as flagged/unverified | Purpose unknown (likely sponsor activation); do not build against it |

---

## 1. GAMEPLAY OVERLAY LAYOUT (exact positions)

### 1a. Universal elements (7/7)

| Element | Position (x / y %) | Content |
|---|---|---|
| Top info strip | 0–100 / 0–4 | "Remaining N · Team N · Elims N" (left), event/BGMI logo (center), "ZONE CLOSING IN mm:ss" + location (right). Thin dark strip, always on |
| Killfeed | 0–17 / 4–12 (India 0–18 / 4–20) | 3–4 entries max, "Killer [weapon] Victim" + skull icon, dark rounded bars. New kills push from top; fade after ~5 s |
| Team standings panel (PERSISTENT) | 84–100 / 8–100 | 16 teams: rank · logo · name · finishes · alive-count · alive-player bars. Header (alive total + zone stage/timer). Footer tabs ALIVE / KNOCK / ELIM. **NEVER disappears during gameplay** |
| Minimap | 88–100 / 4–20 | Zone circle, plane path early, zone stage + timer, location label |
| Observed-player panel | 28–48 / 86–100 (India x 35–65%) | Health bar, boost, weapon + ammo, player name. PMGC/PMGO add stat ranks / AVG GAME stats |
| Match label / branding | Bottom-left (India), e.g. "GRAND FINALS DAY 3 / MATCH 18" | PMGC: "GRAND FINALS MATCH n/18" + infinix sponsor. PMGO: "MATCH n/12 – MAP" + esports logo |

### 1b. Event variants

| Element | India (BMSD) | India (BMPS) | PMGC 2025 | PMGO 2025 |
|---|---|---|---|---|
| Facecam | **Bottom-left** (8–17 / 78–96) | **Bottom-right** | **Bottom-right PiP** | Bottom-center-left PiP |
| Top-center | Empty / logo | Empty / logo | **Live team stat cards** — flag + abbr + alive bars + elims/knocks row, 3–4 contending teams | **WWCD% cards** — flag + alive bars + "WWCD – NN%", top-3 remaining teams |
| Right-panel content | match finishes + alive bars (+ live TOURNAMENT PTS in smash context) | Same as BMSD | ALIVE / PTS / FINISH, blue=alive, red=eliminated | "ELIMINATION RANKING" (match elim leaderboard) |
| Killfeed flags | None | None | Country flag per entry | Country flag per entry |
| Location label | **Purple label top-right** (e.g. "Bamboo"), updates as player moves [V2] | Not documented | Not documented | Not documented |
| Smash scorebug | Player-cam mosaic strip: "#10 58 97 … #2 62 126" (M18 only) [V2] | n/a | n/a | n/a |
| Green leader highlight | Smash-point leader's row highlighted green in right panel (Day 3) [V2] | n/a | n/a | n/a |

**Rule for AEGIS:** the *slot map* is universal; facecam position, top-center content, and right-panel column set are **event-theme parameters**, not hardcoded.

### 1c. Map cam (tactical) mode

- Fullscreen aerial with team markers (colored pills + player names; PMGC adds flags), zone circle + timer, "MATCH n – MAP" header slate.
- Minimap inset top-right with location label.
- Bottom-right: **2×2 player facecam grid** (India) / single PiP retained (PMGC).
- Right team panel persists. BMPS variant: gameplay PiP inset bottom-right during tactical overview.
- Holds: **15–30 s**, then hard cut back to action. PiP is map+gameplay only — never fight+fight.

---

## 2. CAMERA MODES (V2 — expanded)

| Mode | What | When |
|---|---|---|
| `THIRD_PERSON` | Standard observer follow | Default gameplay |
| `SCOPE` [V2] | ADS/scope view when the observed player aims (4× scope overlay) | Engagements; also the *anticipation* camera — PMGC's observer is pre-aimed as fights start |
| `MAP_CAM` | Fullscreen aerial + team markers | Between fights, rotations, match start, 15–30 s |
| `SPLIT_ANALYSIS` [V2] | Split-screen: left third-person, right aerial map | **Analysis segments only** (flight-path review, rotation review). NEVER during live fights |
| `FINAL_CIRCLE` [V2] | Top-down aerial center + **top-4-only right panel** + 2×2 facecam grid + match branding | **≤4 teams alive.** Hold until match end — no switching during the climax |
| `PLAYER_CAM` | Observed-player facecam (synced) | Persistent companion to THIRD_PERSON/SCOPE |
| `STAGE` / `DESK` / `CROWD` | Venue feeds | Breaks, celebrations, pre-show |

---

## 3. CAMERA SWITCHING PATTERNS

### 3a. Fight-hold table [V2 — refined]

| Phase | Hold time | Notes |
|---|---|---|
| Early game (0–5 min) | 15–30 s | Faster cuts, many players looting |
| Mid-game (5–25 min) | 45–90 s | Bible confirmed (V1) |
| Late game (final 4 teams) | 60–120 s | Longer holds, switch to FINAL_CIRCLE mode |
| Final circle (1v1/2v2) | **Stay until end** | No switching during the climax |

V1's "45–90 s" was correct for mid-game only; the Day-2/3 fight strips show phase dependence.

### 3b. Transition rules (7/7)

1. **All editorial transitions are HARD CUTS** — 1 frame, zero fades/wipes/dissolves in gameplay, desk, or ceremony edits.
2. Day 3 uses animated **glitch-wipe bumpers** (green/magenta chromatic aberration) **between break segments only** — these are bumper animations, not editorial transitions.
3. Facecam syncs with the observed player on every switch.
4. Map → gameplay: hard cut when a fight develops (no transition animation).
5. Player switches happen when: current fight ends (no kills 15+ s), a more important fight starts elsewhere, or the zone forces repositioning. **Never mid-fight.**

### 3c. Measured switch rates (India)

- **Day 2:** 196 switches / 5h 48m = ~34/hr (one per ~107 s)
- **Day 3:** 357 switches / 6h 15m = ~57/hr (one per ~63 s)
- The Day-3 jump is ceremony/interview churn, not faster gameplay cutting — fight holds stayed 45–120 s.

### 3d. Observer anticipation (PMGC)

- PMGC's observer acquires fights near-instantly (scope pre-aimed, vehicle chases picked up early).
- India's measured decision delay: ~2–3 s.
- **AI-director implication:** cut on fight-start triggers (killfeed burst, zone-contact, knock events) with a ≤2 s budget; use SCOPE as the anticipation camera.

---

## 4. FIGHT COVERAGE RULES (visual evidence — CONFIRMED 7/7)

### The broadcast DOES:
✅ Commit to ONE fight until it resolves (phase-hold table, §3a)
✅ Show killfeed in real-time (top-left)
✅ Keep the right team panel visible (who's alive, who died)
✅ Switch to the winner after the fight ends (2–3 s delay)
✅ Use map cam to preview the NEXT potential fight location
✅ Air replays only between/during lulls — never over a live fight

### The broadcast does NOT:
❌ PiP of a second live fight (7/7)
❌ Split-screen during live fights (7/7 — analysis segments excepted, §2)
❌ Instant replay DURING a live fight (7/7)
❌ Switch away mid-fight to show another fight (7/7)

### Multi-fight handling:
Stay on the MORE IMPORTANT fight (more kills, top teams, later zone). The other fight is missed live; it may surface as a replay package afterward (India Day 2/3) or in the day-end reel (PMGC).

---

## 5. POST-MATCH SEQUENCE (7/7)

Standard order (observed across all streams):
1. Final kill → **"FINAL BLOW"** lower-third (India Day 2/3) → winner player cams celebrating
2. "WINNER WINNER CHICKEN DINNER" fullscreen → winning-team stage celebration
3. **WWCD TEAM STATS** fullscreen: team logo + big numbers (finishes, damage) + 4 player cards (photo, finishes, damage, assists)
4. Winner interview (India) / on-stage interview (PMGC, with translator)
5. **TOP 4 TEAMS** graphic (PMGC: player photos + elims + **travel distance KM**)
6. Standings (India: per-match + overall with ▲▼ arrows after EVERY match; PMGC: less frequent full tables)
7. Break: countdown + match-strip checklist

**Turnaround:** PMGC ~2–4 min (celebration → Top-4 → checklist → next flight path). India 10–16 min (interview → countdown → desk → standings → map standings).

### Championship exception [V2]
When the title is decided without a WWCD conversion (Smash fallback), the broadcast **skips the WWCD graphic entirely** and cuts from the final circle straight to trophy/celebration. (Day 3 M18 — the VOD jumps from M18 endgame to Soul's trophy lift.)

---

## 6. BREAK SEGMENTS

| Element | India | PMGC |
|---|---|---|
| Countdown | "SHOW RESUMES IN mm:ss" + game-imagery background | Countdown + sponsor ads |
| Strip | **Map strip**: RONDO / ERANGEL×3 / MIRAMAR×2 with played maps highlighted | **Match checklist**: ✓ on completed matches, WWCD chicken icons, "NEXT MATCH IN mm:ss" |
| Desk | 3 casters, sponsor lower-thirds, standings discussion | Short, narrative-driven (heavy pre-show instead) |
| Extras | Winner strip, RACE TO SMASH chart (Day 3), fan cams | — |

**Function is universal (7/7):** orient the viewer on match count, what's next, and when. Skins differ.

---

## 7. PRE-SHOW (event-variant)

| | India (BMSD) | PMGC 2025 | PMGO 2025 |
|---|---|---|---|
| Length | ~20–42 min | **~135 min entertainment show** | Themed opener, studio scale |
| Content | Countdown → TEAMS PLAYING → desk → (Day 1/3: ceremony, walkouts, trophy showcase) → Day-recap: standings, SPOTLIGHT, MVP/IGL boards, caster predictions | Countdown + sponsor ads → human-interest featurettes → desk (predictions, backstage stories) → concerts → 16 team walkouts w/ national flags → cinematic player films → **Creative Vision Awards ceremony** → 2026 season roadmap → caster handoff | Theatrical opener (train-station journey motif) → Smash Rule explainer → $500K pool → amateur-to-pro story ("90,000 amateurs, 4,600 matches") |

**Pattern:** ceremonial spend is concentrated on Day 1 and the final day; middle days run the "pure broadcast" template. Budget sets the ceiling — the *grammar* below the ceremony is identical everywhere.

---

## 8. SMASH RULE BROADCAST SYSTEM (BMSD Day 3 — frame-verified) [V2]

Verbatim from the Day-3 fullscreen explainer (@ t=2100):
> **THE SMASH RULE**
> • Match Point Target: After Match 12, the 1st-place team's score + 10 points sets the Target.
> • To Win: From Match 13 onward, a team must: Reach the Match Point Target. Win a Chicken Dinner in a later match.
> • Important: Reaching the Target and winning the Chicken Dinner must happen in separate matches.
> • No Winner: If no team completes both conditions by Match 18, the team with the most overall points wins.

**The 5 broadcast beats (all observed):**
1. Fullscreen explainer in pre-show.
2. **RACE TO SMASH** bar chart in breaks ("SMASH POINT – 126", "REQUIRED POINTS", top-8 teams, qualified teams green).
3. **Green highlight** on the smash-point leader's row in the persistent right panel.
4. **Smash scorebug** strip over the player-cam mosaic ("#10 58 97 … #2 62 126").
5. **Fallback path executed live:** SOUL reached the target (126) after M14 but no match-point team won M15–M18 (K9 / WELT / V1BA / V1BA) → title decided by "No Winner" rule → **SOUL 155, champions**. The broadcast skipped the M18 WWCD graphic and went straight to trophy/celebration.

**Observed smash point:** 126 (= Day-2 leader 8BIT's 116 + 10).

---

## 9. AWARDS & CEREMONY GRAPHICS [V2]

- **Award boards (pre-show / breaks):** GOOGLE PLAY MVP, BEST IGL, ELIMINATOR, FINALS MVP (rating ~1.0–3.2 scale + finishes + damage + survival + knocks), GRENADIER top-5, per-match MVP, TOP 5 PLAYERS, TEAM SPOTLIGHT (map + team stats + 4 player minis).
- **Prize-money cards (Day 3):** CHAMPIONS ₹30,00,000 · RUNNER-UP ₹15,00,000 · GOOGLE PLAY MVP ₹2,00,000 · BEST CLUTCH ₹50,000.
- **Championship sequence:** trophy lift → stage celebration → "READY FOR GLOBAL — SOUL" (international-slot graphic) → awards → DAY RECAP → credits ("LEAGUE OPERATIONS") → static "CHAMPIONS — SOUL — ₹30,00,000" end card.
- **Branded segment bugs:** "FINAL BLOW" (red, clutch moments) · "DAY RECAP" (red) · standard "GRAND FINALS DAY X / MATCH Y".

---

## 10. CASTER RHYTHM (summary)

- **India (Hinglish):** fast, emotive; storyline per table; gap highlights ("sirf 4-finish ka gap"); every segment ends on the next match's flight path; points math on air.
- **PMGC (English, John Allen + Blake; desk Max + Seven):** 160–227 wpm (fastest on desk); tactical vocabulary (utility economy, third-party theory, zone-end prediction); roster-move gossip; national-pride framing; MVP race built live ("heading for a Doc MVP"); playful banter during flight phase.
- **PMGO (English):** analytical, rotation/zone-shift focused, least banter; ~180–200 wpm in fights.
- **Universal (7/7):** points math on air, narrative arcs carried across the day (rookie/grudge/MVP-race), excitement peaks cluster at final circles and match ends.

---

## 11. IMPLICATIONS FOR AEGIS (updated from V1)

### Must match (universals, 7/7):
1. Right-side team panel: ALWAYS visible during gameplay (x 84–100%, y 8–100%)
2. Killfeed: top-left, 3–4 entries, ~5 s fade
3. Hard cuts only for all editorial transitions
4. Phase-hold table: 15–30 / 45–90 / 60–120 / hold-to-end — never switch mid-fight
5. Facecam syncs with observed player
6. Map cam: 15–30 s max, then back to action
7. WWCD ritual after every match (with the championship-fallback exception)
8. Breaks: countdown + match-strip checklist with played-map highlights

### Adopt from global (AEGIS gaps — see AEGIS_GAP_AUDIT.md):
1. **WWCD% live prediction cards** (PMGO) — top-center, the #1 world-#1 feature
2. **Top-center live team stat cards** (PMGC) — fills India's empty slot; share the slot with #1 as a mode
3. **Smash Rule 5-beat system + fallback path** — championship-correctness
4. Match-strip checklist + next-match countdown
5. Whole-day highlight reel as the day-end package
6. Tight (~2–4 min) inter-match turnaround as the default rundown
7. Final-circle aerial mode (≤4 teams: aerial + top-4 panel + 2×2 facecams)
8. Observer anticipation tuning (≤2 s fight acquisition; SCOPE as anticipation camera)
9. Travel distance (KM) stat
10. Branded segment bugs (FINAL BLOW / DAY RECAP), location labels, AVG GAME stats on player panel, prize-money ceremony package, flags for multi-region events

### Policy refinements:
- **PiP:** map+gameplay only, never fight+fight (7/7 confirmed)
- **Split-screen:** allowed for analysis segments only (flight-path/rotation review), never live fights
- **Animated bumpers** (incl. glitch-wipes) are permitted *between break segments*; gameplay/desk/ceremony cuts stay hard
- **Facecam position and top-center content are event-theme parameters**, not hardcoded

---

## 12. RESOLVED FACTS (do not re-litigate)

- **M18 winner: Version1 x Blue Army** (Miramar, 8 finishes) — NOT Soul. Three independent web sources + internal standings arithmetic (V1BA 0→2 WWCD via M17+M18; SOUL 1→2 via M14) are conclusive. Soul's championship came from the Smash fallback (most points, 155), the first observed live execution of a "no conversion" title.
- **Day-3 right panel:** same layout as Day 2 (# | TEAM | match-finishes | TOTAL PTS); the green smash-leader highlight is the only addition.
- **Day-2 standings:** the broadcast graphic (8BIT 116 … GENS 28) is ground truth over the talkesport Day-2 table.
- **M15 K9 points: UNRESOLVED** (Analyst A: 18 pts from 8 finishes; sportsdunia: 23 pts). Flagged, not asserted.
- **BMPS ≈ BMSD production tier:** same vendor grammar, hard cuts, 10+1/finish scoring; differences are packaging/branding only.
- **"OPERATION SDC" banner:** purpose unverified — flagged, do not build against it.

---

## Honest Limits
- 360p YouTube rips throughout; overlay positions ±2%; fine text partially illegible.
- 7 streams (4 India frame-level, 3 global sampled). PMGC Day 3 (Smash day) and semis/last-chance VODs were never analyzed.
- No Hindi transcript for BMSD Day 2/3 — commentary notes there are audio-energy based only.
- Switch logs at keyframe granularity (±4–5 s); sub-keyframe cuts are missed.
- Single-stream (1/7) findings — Smash beats, final-circle layout, WWCD%, scope view, split-screen analysis, location labels, prize graphics — are real observations but not universals.
