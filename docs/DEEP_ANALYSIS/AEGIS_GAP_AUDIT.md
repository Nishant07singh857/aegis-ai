# AEGIS GAP AUDIT — Broadcast Capability vs Observed Best

**Date:** 2026-10-19
**Analyst:** SYNTHESIS ANALYST
**Evidence base:** 7 streams (see `MASTER_PATTERNS.md`); AEGIS state from repo inspection (`backend/app/services/match_point/`, `frontend/components/graphics-staging/`, Phase 7/9–11/13/14/19 docs in memory).

**Severity scale:** CRITICAL = championship-correctness or world-#1 claim breaks without it · MAJOR = visible gap vs Krafton/PMGC a paying organizer would notice · MINOR = polish/optionality.

---

## WHERE AEGIS ALREADY MATCHES OR EXCEEDS (honest baseline)

| Capability | AEGIS state | Observed best |
|---|---|---|
| Krafton-format standings + ▲▼ movement arrows after every match | Phase 9 templates ✓ | India-only; PMGC does this less often — **AEGIS exceeds** |
| Four leaderboards (MVP / Eliminator / Best IGL / Finals MVP) | Phase 9 templates ✓ | India-only — **AEGIS exceeds** |
| Team spotlight stat drops | Phase 9 templates ✓ | India ("K9: 7002 damage, 31 knocks") — matches |
| Auto replays + highlights | Phases 9–11 ✓ | India D2/D3 green "REPLAY" lower-thirds; PMGC day-end reel — matches mechanism, not *placement* (see GAP-4) |
| Right panel persistent, killfeed TL, hard cuts, facecam sync, 45–90 s holds, map cam | Bible + camera-bible unit tests (`test_director_camera_bible.py`) ✓ | Universal grammar 7/7 — matches |
| Match-point badge | `matchpoint-badge.tsx` + `services/match_point/` ✓ | Partial — see GAP-3 |
| Animated graphics library (10 templates), 3-mode director, permissioned director panel, storytelling section | Phases 7/19 ✓ | No equivalent observed in any stream — **AEGIS exceeds** |

The gaps below are what remains.

---

## GAP LIST (priority order)

### GAP-1 · WWCD% live prediction cards (PMGO) — CRITICAL 🔴

- **They do:** PMGO top-center shows 3 live cards — flag + alive bars + **"WWCD – 37%"** win-probability for the top-3 remaining teams, updated through the match. The single most differentiating data feature found in 7 streams. (1/7 — but uncontested as "world #1" signature.)
- **AEGIS does:** no win-probability product. Heuristic engine + Phase 13/14 tracking data exist, but nothing surfaces prediction.
- **Fix:** new `wwcd_probability` service — honest heuristic model (alive count, elims, zone position/edge distance, recent form), displayed as top-center cards with model confidence band. Must show "—" when inputs are stale (honest-missing-data rule from Phase 19), never fake numbers. Frontend: `frontend/components/graphics-staging/wwcd-probability-cards.tsx`.
- **Build first.** This is the headline of the "better than Krafton" story.

### GAP-2 · Top-center live team stat cards (PMGC) — CRITICAL 🔴

- **They do:** PMGC top-center shows 3–4 cards — flag + team abbr + alive-player bars + live stat row (elims / knocks). Fills the exact slot India leaves empty. (2/7)
- **AEGIS does:** nothing in top-center during gameplay (matches India's empty slot — the *weaker* of the two).
- **Fix:** `top-center-team-cards.tsx` — 4 contending teams (leader + top threats), live elims/knocks, auto-promo/demote as fights resolve. Reuse `team-panels.tsx` data feeds.
- **Note:** GAP-1 and GAP-2 share the slot — design the top-center as a *mode*: stat cards by default, probability cards during final circles / director-triggered. Never both stacked.

### GAP-3 · Smash Rule: the fallback path + 5 broadcast beats — CRITICAL 🔴

- **They do (BMSD Day 3, observed live):** (1) fullscreen SMASH RULE explainer in pre-show; (2) RACE TO SMASH bar chart in breaks ("SMASH POINT – 126", "REQUIRED POINTS", qualified teams green); (3) green highlight on the smash-point leader's row in the persistent right panel; (4) smash scorebug strip over the player-cam mosaic ("#10 58 97 … #2 62 126"); (5) **fallback path executed live:** when no match-point team converted by M18, the broadcast *skipped the WWCD graphic entirely* and cut straight to the trophy/celebration — champion decided by points (SOUL 155).
- **AEGIS does:** `services/match_point/` + `matchpoint-badge.tsx` — target tracking exists; the *fallback state machine* and beats 2–5 do not.
- **Fix (specific):** extend the match-point service with the full state machine from the verbatim Day-3 explainer (target = 1st-place score after M12 + 10; reach + win must happen in *separate* matches; "no winner → most overall points wins"). Build all five graphics/beats, and — critically — the **no-conversion ceremony variant**: when the match winner is not the champion and the fallback fires, suppress the WWCD graphic and jump to the championship package. Without this, AEGIS would tell the wrong story (exactly the error Analyst A made: "SOUL won M18 → champions").
- **Priority:** this is championship-correctness. A wrong champion narrative is the worst possible broadcast bug.

### GAP-4 · Whole-day highlight reel (PMGC) — MAJOR 🟠

- **They do:** PMGC closes with a multi-minute "WHOLE DAY HIGHLIGHT" frag montage (HIGHLIGHT bug) — the replay *product* is a day-end package.
- **AEGIS does:** replay + auto-highlights machinery (Phases 9–11) but no day-end placement — highlights are scattered/mid-match inserts.
- **Fix:** "AEGIS DAY HIGHLIGHT" closing package in the director rundown — auto-assembled from the day's top frags (reuse Phase 9–11 highlight scoring), with HIGHLIGHT bug + branded segment bug. Cheap: it's placement, not new tech.

### GAP-5 · Match-strip checklist + countdown (PMGC/India) — MAJOR 🟠

- **They do:** every break orients the viewer — PMGC: bottom strip with ✓ on completed matches + "NEXT MATCH IN mm:ss"; India: "SHOW RESUMES IN mm:ss" + map strip (RONDO / ERANGEL×3 / MIRAMAR×2) with played maps highlighted.
- **AEGIS does:** countdown exists; the persistent match-strip with checkmarks + played-map highlights does not.
- **Fix:** `match-strip.tsx` — bottom break strip: per-match ✓/chicken icon + next-match countdown + played-map highlight. Trivial build, high orientation value across 6 matches.

### GAP-6 · Tighter inter-match turnaround (PMGC) — MAJOR 🟠

- **They do:** PMGC turns around in **~2–4 min**: celebration → Top-4 graphic → checklist → next flight path. India's 10–16 min interview/desk/standings breaks bleed momentum.
- **AEGIS does:** no enforced turnaround discipline in the director rundown.
- **Fix:** ship a PMGC-mode rundown template in the director — target ≤4 min match-to-match (celebration → top-4 → strip → flight path), with India's long-break template as the alternative. This is a *rundown config*, not code. Make the tight template the default.

### GAP-7 · Final-circle aerial layout (India Day 3) — MAJOR 🟠

- **They do:** when ≤4 teams remain — top-down aerial center, right panel collapses to **top-4 teams only**, 2×2 facecam grid bottom-right, match branding bottom-left. (1/7 — but it's the championship-deciding look.)
- **AEGIS does:** no final-circle mode; mid-game layout persists.
- **Fix:** new director camera mode `FINAL_CIRCLE`: aerial observer + top-4 panel variant + 2×2 facecams. Trigger rule: ≤4 teams alive. This is the single highest-leverage *visual* addition for the "world best" claim.

### GAP-8 · Observer anticipation tuning (PMGC) — MAJOR 🟠

- **They do:** PMGC's observer is *on* fights as they start — scope cams pre-aimed, vehicle chases picked up early. India's measured decision delay is ~2–3 s vs PMGC's near-instant.
- **AEGIS does:** AI director exists, but no explicit fight-start trigger tuning.
- **Fix:** director trigger rules — cut on fight-start signals (killfeed burst, zone-contact events, knock events) with a ≤2 s budget; scope-view as the *anticipation* camera (see GAP-10) rather than arriving after the first knock.

### GAP-9 · Travel distance (KM) stat (PMGC) — MINOR 🟡

- **They do:** Top-4 graphic carries "TRAVEL DIST — 24.2 KM" per team. Distinctive, cheap.
- **AEGIS does:** zone/position tracking exists (Phase 13/14); no travel-distance stat.
- **Fix:** accumulate per-team path length from position pings; surface on Top-4/team graphics. ~1 day of work.

### GAP-10 · Scope-view camera mode (India Day 2) — MINOR 🟡

- **They do:** when the observed player aims (ADS), broadcast shows the scope view (4× scope overlay) — a distinct camera mode.
- **AEGIS does:** not in the director camera grammar.
- **Fix:** add `SCOPE` camera mode to the director; pair with GAP-8 (scope = anticipation camera).

### GAP-11 · Split-screen tactical analysis rule refinement — MINOR 🟡

- **They do (Day 2):** split-screen (left third-person, right aerial map) used for flight-path/rotation review — **never during live fights**. Refines the Bible's "no split-screen" to "no split-screen during live fights."
- **AEGIS does:** `pip-layout.tsx` (map+gameplay) exists; the *policy* needs the refinement.
- **Fix:** update the camera-bible rule + unit test: split-screen allowed only for analysis segments (flight path, rotation review); hard prohibition stands during live fights. Add a `SPLIT_ANALYSIS` mode to the director.

### GAP-12 · Branded segment bugs (India Day 2/3) — MINOR 🟡

- **They do:** bottom-left branding swaps by segment — "FINAL BLOW" (red, clutch moments), "DAY RECAP" (red), standard "GRAND FINALS DAY X / MATCH Y".
- **AEGIS does:** segment branding exists in the graphics library but not as a swappable bug system.
- **Fix:** segment-bug component driven by director state (final-blow auto-triggers on clutch/final-kill events).

### GAP-13 · Location labels (India Day 2) — MINOR 🟡

- **They do:** purple top-right label ("Bamboo") during gameplay, updates as the player moves.
- **AEGIS does:** map-name in top bar; no live location label.
- **Fix:** small overlay fed from zone/location tracking. Trivial.

### GAP-14 · AVG GAME stats on player panel (PMGO) — MINOR 🟡

- **They do:** PMGO's bottom-center player panel shows AVG GAME stats (0.3, 67.0, 33.7) alongside name/health/weapons.
- **AEGIS does:** player panel has stat ranks (BMSD style).
- **Fix:** add AVG GAME row to `player-panels.tsx` from tournament master data. Trivial.

### GAP-15 · Prize-money ceremony graphics (India Day 3) — MAJOR-later 🟠

- **They do:** full awards ceremony with money graphics — CHAMPIONS ₹30,00,000 / RUNNER-UP ₹15,00,000 / MVP ₹2,00,000 / BEST CLUTCH ₹50,000, trophy lift, credits ("LEAGUE OPERATIONS"), 25-min static champions end card.
- **AEGIS does:** WWCD ceremony exists; no prize-money award cards, no credits template, no static-champions end card.
- **Fix:** ceremony package driven by tournament config (prize pool from Phase 13 master data): award cards + trophy-lift sequence + credits + static end card. Schedule after GAPs 1–8.

### GAP-16 · National flags for multi-region events (PMGC/PMGO) — MINOR, later 🟡

- **They do:** flags on every team card, map tag, and killfeed entry.
- **AEGIS does:** no flag assets (all-Indian events so far).
- **Fix:** add flag field to team master data + flag rendering on team cards/tags/killfeed. Do when AEGIS runs its first multi-region event.

### GAP-17 · Right-panel tournament-PTS mode + smash-leader highlight — folded into GAP-3

- **They do (Day 3):** right panel shows live tournament points with green highlight on the smash-point leader's row (layout itself unchanged — see MASTER_PATTERNS §4b).
- **AEGIS does:** right panel exists; needs the points-mode toggle + leader highlight.
- **Fix:** implement with GAP-3's beat (3).

---

## EXPLICITLY CONFIRMED (not gaps)

- **No PiP during fights — CONFIRMED 7/7.** Keep `pip-layout.tsx` restricted to map+gameplay only; never fight+fight. The Bible rule stands, refined by GAP-11.
- **45–90 s camera holds — CONFIRMED, refined:** early 15–30 s · mid 45–90 s · late/final-4 60–120 s · final circle: stay until it ends. Update the camera-bible unit test thresholds to the phase table.
- **Hard cuts only — CONFIRMED 7/7.** Day 3's glitch-wipes are bumper animations between break segments, not editorial transitions. AEGIS may use animated bumpers in breaks; gameplay/desk cuts stay hard.

## NOT RECOMMENDED (observed but not worth copying)

- **Mid-match sponsor overlays** (Google Play Points QR during live M12) — sponsor-ops intrusion; not a broadcast-quality feature. Skip.
- **"OPERATION SDC" banner** — purpose unverified (possibly sponsor activation). Skip until identified.
- **25-min frozen end card** — VOD tail artifact more than a feature; the ceremony + highlight reel matter, the freeze doesn't.
- **In-game interactive tie-ins** (PMGC Champion Prediction with redeem codes) — needs publisher cooperation; aspirational, not buildable from the broadcast side alone.
- **135-min variety-show pre-show** — budget/talent problem, not an AEGIS software gap. Note for the user's event planning, not the backlog.

---

## PRIORITY BUILD ORDER

| Order | Gap | Severity | Effort |
|---|---|---|---|
| 1 | GAP-1 WWCD% prediction cards | CRITICAL | Medium (model + component) |
| 2 | GAP-2 Top-center team stat cards | CRITICAL | Small (component) |
| 3 | GAP-3 Smash 5-beat system + fallback | CRITICAL | Medium (state machine + 4 graphics + ceremony variant) |
| 4 | GAP-5 Match-strip checklist + countdown | MAJOR | Trivial |
| 5 | GAP-4 Whole-day highlight reel placement | MAJOR | Small (rundown placement) |
| 6 | GAP-6 Tight-turnaround rundown template | MAJOR | Trivial (config) |
| 7 | GAP-7 Final-circle aerial layout | MAJOR | Medium (camera mode + panel variant) |
| 8 | GAP-8 Observer anticipation triggers | MAJOR | Medium (director tuning) |
| 9 | GAP-9 / 10 / 11 / 12 / 13 / 14 | MINOR | Small each |
| 10 | GAP-15 Prize ceremony package | MAJOR-later | Medium |
| 11 | GAP-16 Flags | MINOR, later | Small |

**Top 5 by priority:** (1) WWCD% live prediction cards · (2) Top-center live team stat cards · (3) Smash Rule 5-beat system + fallback path · (4) Match-strip checklist + countdown · (5) Whole-day highlight reel placement.
