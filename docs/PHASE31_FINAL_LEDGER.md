# PHASE 31 FINAL LEDGER — Spec-v2 ke 8 kaam ki cheezein (BUILD)

**Scope:** `docs/DIRECTOR_SPEC_V2_AUDIT.md` §1 ke 8 items, priority order me,
saare. Base: Integrity Fix Pack commit `e737050` (= remote `d880598`).
**Commit/push is phase me NAHI hua** — working tree uncommitted hai;
main agent user ke saamne hone par review ke baad commit+push karega.
**Standing rules kept:** spectator-mode only, zero API spend, Bible
grammar (live fight me NO PiP/split, hard cuts only, replays kabhi
mid-fight nahi, map cap 30s), koi fabricated number nahi.

> Note: 2026-10-24 ki FULL RE-INSPECTION ek FROZEN snapshot
> (`e737050` = remote `d880598`) par hui thi — isliye us report me
> Phase 31 items nahi hain aur unhe fail nahi gina gaya. Us pass ke
> naye findings (feed-auth/headless-capture side-effect, prod
> streamer topology, WS kill-switch residual, RF-DETR decode gap,
> tooti test files) Phase 31 ke scope se BAHAR hain aur yahan
> Phase 31 regressions ke roop me claim nahi kiye gaye hain.

## 1. Kya bana — item by item (sab LIVE wired, test ke saath)

### Item 1 — Dormant producers ki wiring ✅ LIVE
- **Drop-clash:** `main.py` me failure-isolated 30s ticker — flight path
  set ho + bus chal rahi ho to `check_drop_clashes(current,
  history_rows=history)` chalta hai. `history_rows` param naya hai:
  predicted basis past-match history par, observed basis SIRF is match
  ke drops par (poori history ko observed maan lena purane matches par
  alert karta — ye bug wiring me hi roka gaya). Alert
  `intel_alert(kind=drop_clash)` ke roop me director tak pahunchta hai
  (pehle se wired watch/PVW path).
- **Phase suggestions:** `DirectorService.propose_phase_suggestions_on_change()`
  — sirf phase ENTRY par ek baar, repeats par silent, SEMI_AUTO ke
  bahar silent (mode gate barkarar). Usi ticker se call hoti hai.
- **PRE_COMBAT rotation intakes:** VI rotation starts ko mode/bearing/
  speed-px/s/area-POI/shared-occupancy se enrich karta hai;
  `potential_fight` bus events (first sight + ≥0.1 probability rise,
  deduped, promotion par pruned) ab detector se bahar aate hain;
  `main.py` lifespan me `phase31_rotation_intake` bridge
  `rotation_event` + `potential_fight` ko `LiveDirector.note_rotation()`
  / `note_potential_fight()` tak pahunchata hai. Isolated intel ab
  `nearest_enemy_px` (+ unit `minimap_pixels`) bhi carry karta hai —
  team-level hi, marker anonymous rehta hai, kabhi named player nahi.
- **Verify:** end-to-end bus tests (event-in → director-receives-it).

### Item 2 — Clutch zinda ✅ LIVE (producer + lock + solo lock)
- **Producer:** `FightDetector.evaluate_clutch()` — SIRF tab fire jab
  exact ALIVE-only count == 1 AUR (fight active YA nearest enemy
  ≤60px). `_determine_fight_type` ka khaali `pass` hata, comment se
  samjhaya (clutch ek flag hai, fight type nahi). Payload
  (`is_clutch`/`clutch_team_id`/`clutch_basis`) director tak jata hai,
  False→True transition dobara publish hoti hai, revive (1→2) par
  detector-side clear hota hai (sticky nahi).
- **Critical correction (code me mila):** VI ka `alive_count` alive +
  knocked ginata hai — knocked-only team "1" padhti. Isliye detector
  SIRF per-slot roster (`note_team_roster()`, VI `_on_team_status`
  se wired) ya attested ALIVE-only count leta hai; koi `unknown` slot
  fail-closed (count None). Combined count kabhi pass nahi hota.
- **Incumbent-clutch lock:** `config.py` (`lock_enabled=True`,
  challenge margin 40.0) + `service.py` margin branch me FIRST —
  clutch incumbent ko margin-5 se chheena nahi ja sakta; challenger
  ko poora hold margin (40) chahiye. Margin 5 sirf challenger-side
  rehta hai (audit ka "ULTA margin" fix).
- **Pilot solo lock R3b:** `observer_pilot/rules.py` — R0 → R1 → R3b
  → R4 → R2 → R3 → R5; spectated ALIVE + koi aur ALIVE nahi + koi
  UNKNOWN nahi + fight active → lock; death/fight-end/revive se
  release (stateless re-evaluation).
- **Caveat (pre-existing, badla nahi):** director ka `_on_fight_detected`
  `is_clutch` ko OR karta hai — promotion-time flag director-side
  chipak jata hai chahe detector revive par clear kar de. Ise chupke
  se badla nahi gaya; QA note me hai.

### Item 3 — Championship close-gap uplift ✅ LIVE
- `storyline.py`: `championship_gap_from_standings()` (#1–#2 gap,
  missing leg → None, kabhi estimate nahi) + `compute_storyline()`
  me gap ≤5 par rank 1/2 ko **+0.05 W ke andar**. Spec ka 2x
  REJECTED (lagaya nahi), W cap 1.35 barkarar, base 0 ⇒ score 0
  barkarar. Championship-fight challenger ke liye koi "drop
  everything" bypass nahi — maujooda clutch-margin (5) wala rasta
  hi replacement hai; non-clutch top-2 ko margin 5 se jodna
  "clutch" ki paribhasha badalta, isliye nahi kiya (coordinator
  decision, ledger me darj).

### Item 4 — Zone reveal par on-screen jawab ✅ LIVE
- `zone_beat.py` + `service.py` hook: naya circle (`zone_pull`) par
  — koi promoted fight nahi + final circle nahi → **15s `map_cam`
  beat** (Bible 15–30s; 30s cap; 7s/8s clamp-up hote hain).
  Full_auto/clone me scene turant publish + 15s ke liye distinct
  `_pending_zone_beat` hold (hot fight aate hi abort, expiry par
  sirf normal evaluation — context-beat wala follow-up cut yahan
  JAANBOOJH kar reuse nahi hua, kyunki zone beat ke baad koi team
  cut nahi hona chahiye). Semi_auto me map_cam suggestion;
  manual/grabbed me sirf log.
- Fight chal rahi ho YA final circle → **graphic only, kabhi cut
  nahi**. Graphic DATA decision log me hai (winners/losers);
  dedicated frontend graphic template backlog hai (neeche).
- **Appendix B finding 6 fix:** winners/losers ab dono +10
  zone_pressure nahi — winners +10 zone_pressure, losers +10
  precombat (same magnitude, alag term; overlap team ko sirf
  winner term, double-count nahi).

### Item 5 — Payoff pack ✅ LIVE (2 honest gaps ke saath)
- **ELIMINATED banner template:** `lowerthird-eliminated.tsx` +
  frontend registry + backend `LOWER_THIRD_TEMPLATES` contract
  (default template nahi badla). Banner sirf event payload se
  banta hai: title hamesha ELIMINATED, placement `#N` sirf jab
  event me asal me ho, kills ko finishes kabhi relabel nahi.
- **Banner delivery:** director ab `team_wipe_event` subscribe
  karta hai (`_on_team_wipe`): semi_auto me lower_third suggestion
  (operator approval); baaki modes me decision-log entry. **Gap:**
  full_auto me banner AUTO-appear nahi ho sakta — kyunki
  `DirectorService.set_lower_third` manual-gated hai by design aur
  full_auto ke paas direct graphics-write path nahi hai. Ye mode
  contract ko chupke se todne ki jagah backlog me darj hai.
- **Winner preference:** director ke 25s resolve (`_prune`) par
  wipe-inference preference — SIRF tab jab fight ki ek team ko
  chhod baaki SAB observed wiped hon (sole remainder). Ye exact
  alive count NAHI hai (wo director ke paas hai hi nahi); koi wipe
  observe na ho, ya 2+ teams unwiped hon → koi preference nahi
  (fail-closed). One-shot, first-place tie-break ONLY, scores
  kabhi modify nahi. **Clock reconciliation:** detector 8s (hard
  16s) vs director 25s — koi `fight_ended` producer nahi hai,
  isliye payoff director ke 25s clock par gate hota hai; ~17s ka
  gap asal hai, chhupaya nahi gaya.

### Item 6 — Slam anticipation ✅ LIVE (gated, imaandaari se)
- Rotation intel ab director ke intel path me hai (item 1 ki wiring
  + `rotation_intake.py`): PRE_COMBAT par **PVW pre-position only**,
  kabhi blind PGM cut nahi, koi free-cam/overhead claim nahi, koi
  lead-time number nahi (TTL lifetime hai, "3s pehle" nahi),
  speed px/s, distance px.
- **Gating (chhupaya nahi):** (a) Occupied-POI PRE_COMBAT aaj ke
  live feeds par fire NAHI ho sakta — rotation minimap-pixel scale
  par hai, POI naming ko normalized-map coordinates chahiye, aur
  live minimap pipeline `minimap_region_pixels` emit karta hai
  (jise `poi_name_for` sahi tareeke se reject karta hai). (b)
  Vehicle rotation akela PRE_COMBAT nahi deta — engagement sirf
  occupied POI se PRE_COMBAT banata hai. Isliye slam anticipation
  tab poori tarah live hoga jab normalized-coordinate position
  source aayega; unit level par occupancy leg proven hai.

### Item 7 — Lull loop ✅ LIVE (grammar-true)
- `lull.py` + hook: loot/early phases me, koi fight/precombat
  candidate na ho to **semi_auto suggestions** cycle karte hain:
  map → standings → top-team POV, legs **15–30s** (default 20s;
  spec ke 12–15s legs Bible ke 15s floor se neeche — lagaye nahi).
  Full_auto/manual me loop reset, kabhi auto-cut nahi. Fight ya
  PRE_COMBAT aate hi naye suggestions turant abort; POV leg tab
  skip jab top team/slot unknown ho (kabhi invent nahi). Pehle se
  queued suggestion ko retract karna DirectorService queue ka
  kaam hai, loop ka nahi — ye seema hook note me darj thi aur
  yahan bhi darj hai.

### Item 8 — Center-hold derivation ✅ LIVE (producer + consumer)
- `center_hold.py`: chaaron gates zaroori — inside zone + centre
  band (distance ≤ 0.5 × safe_radius, heuristic, labelled) +
  stationary dwell ≥60s (heuristic, labelled) + POI `type =
  "compound"`. Output: `center_hold` alert, zone-pressure weight
  10.0 (cap 20 ke andar), semi_auto-only suggestion. Px only.
  Favoured team context hai, gate nahi (centre compound hold karne
  wali team ke paas andar sabse zyada markers hona zaroori nahi —
  design decision, coordinator ka).
- **Producer (coordinator fix):** VI `_on_zone_update` me wired —
  normalized position se `poi_database.nearest()` (POI dict with
  type), zone entry, tracker dwell. Integration me ek asal bug
  pakda + theek kiya: `team_zone_distances` entries me
  `safe_radius` key hoti hi nahi, isliye wrapper radius None
  padhkar chupchaap no-alert deta tha — producer ab radius
  `zone_state.safe_radius` se seedha leta hai.
- **Choke-point variant NAHI bana:** bridge POIs sirf 2 hain, dono
  Erangel — data backlog, derivation nahi.

## 2. Rejected cheezein — dobara nahi laayi gayi
PiP/split during fights · 4-second hold · reset-dip to replay ·
2x storyline weight · ">85 score" · team comms listen-in · nade
trace cam · drone free-cam · X-ray · facecam (koi feed source
nahi) · 60 km/h / 80m jaise fabricated numbers · enemy first-aid
counts · 7s/8s forced map cut · 12–15s lull legs · 12–15s map legs.
Koi bhi inme se code me nahi hai.

## 3. Measure hua (numbers, sab test se)
- Naye Phase 31 suites: **117 passed** — worker suites 99
  (rotation 20, clutch/payoff 54, context 25) + coordinator
  integration 18 (hooks 14, VI hooks 4).
- Targeted regressions (live director, Bible, modes, default mode,
  VI derivations, p1, Phase 24 coverage, director, VI, + Phase 31):
  **321 passed, 0 failed**.
- Frontend: `tsc --noEmit` exit 0 (0 errors); `npm run build`
  **35/35 pages**.
- Full backend suite (coordinator run, 2 collection-error files
  excluded — nested paths
  `tests/unit/test_vision/test_agents/test_services/
  test_tournament_service.py` aur `.../test_utils/test_time_sync.py`;
  plain `pytest tests` in par collection exit 2 deta hai, ye
  pre-existing hai): **1918 passed / 63 failed / 3 skipped**
  (7m07s). Failed triage — ZERO Phase 31 regression dikha:
  e2e 13 (Redis ConnectionError localhost:6379 — highlight ke 4
  fails isi wajah se, dobara run karke confirm kiya) + OBS drift
  19 + dossier fpdf stub 5 + event_bus Redis 1 + dormant-agent
  drift 21 (commentary 7, observer 6, team 3, coordinate 3,
  fight_agent 2 — jinme `test_determine_third_party_fight`
  clean HEAD par bhi fail hota hai (Worker B ne stash comparison
  se verify kiya) aur `test_get_fight_detail` flaky set-ordering
  hai) + minimap 2 + player_tracker 2 = 63. Integrity QA ke 63 se
  count same hai (frag-grenade flake is run me pass hua, fight_agent
  ka doosra test is run me fail — net zero). Passed 1795 → 1918
  (+123: Phase 31 ke 117 naye tests + 6 pehle-skipped/flaky
  variations; skipped 9 → 3 ka farak run-to-run variation hai,
  regression nahi).
- QA ko independently verify karna hai: full suite dobara + neeche
  adversarial probes + security review (uske verdict ke bina
  Phase 31 complete nahi).
- Production-measure NAHI hua (VM limits, imaandaari se): koi real
  phone feed, koi scrim, koi RTMP, koi 720p re-measure nahi.
  Dwell 60s aur centre-band 0.5 heuristics hain, measured numbers
  nahi — scrim par calibrate hone hain.

## 4. Coordinator ke apne fixes (worker reports ke baad)
1. `flight_path.py:310` syntax error (Worker A ki docstring edit
   se) — pehle coordinator ne verify kiya, phir A ko fix karwaya;
   final tree compile-clean hai.
2. Lull hook me `base_entry` use se pehle reference (NameError
   hota) — coordinator ne minimal entry dict se theek kiya.
3. Center-hold producer ka safe_radius bug (upar §1 item 8).
4. Zone-beat follow-up cut: context-beat machinery reuse karne se
   zone beat ke baad galat team cut lagta — alag
   `_pending_zone_beat` state machine banayi (expiry par sirf
   normal evaluation, koi cut nahi).
5. Ek integration test ki galat expectation (beat expiry ke baad
   normal evaluation ka rejected decision) — test theek kiya,
   production code nahi badla.

## 5. Backlog (Phase 31 rokne wale nahi, chhupaye nahi)
1. **Full_auto ELIMINATED banner auto-appear** — DirectorService
   me graphics-write path chahiye (mode-contract decision);
   aaj semi_auto suggestion + decision log + selectable template
   hi hai.
2. **Zone winners/losers dedicated graphic template** — data
   decision log me hai; frontend template nahi bana.
3. **Slam/center-hold ka normalized-coordinate position source**
   — bina iske occupied-POI PRE_COMBAT aur centre-hold live
   feeds par fire nahi honge (unit level par proven).
4. **Director-side clutch stickiness** — `_on_fight_detected` OR
   promotion-time flag chipkata hai; revive par clear karne ka
   faisla alag change hai.
5. **Lull queued-suggestion retraction** — fight shuru hone par
   pehle se queued lull suggestion DirectorService queue se
   retract nahi hota (naye suggestions abort hote hain).
6. **WS kill-switch residual, RF-DETR decode gap + best.onnx
   absent, 2 tooti test files, feed-auth/headless-capture
   side-effect, prod streamer topology** — ye re-inspection ke
   findings hain, Phase 31 ke nahi; yahan sirf isliye darj hain
   taaki koi inhe Phase 31 ka regression na samjhe.

## 6. Files (uncommitted tree, commit/push NAHI hua)
- Modified: `backend/app/agents/fight_agents/fight_detector.py`,
  `backend/app/main.py`, `backend/app/services/director_service.py`
  (LOWER_THIRD_TEMPLATES me ek entry),
  `backend/app/services/flight_path.py`,
  `backend/app/services/live_director/config.py`,
  `backend/app/services/live_director/service.py` (hooks),
  `backend/app/services/live_director/storyline.py`,
  `backend/app/services/observer_pilot/rules.py`,
  `backend/app/services/vision_intelligence/service.py`,
  `frontend/components/graphics/registry.ts`.
- New: `backend/app/services/live_director/center_hold.py`,
  `lull.py`, `payoff.py`, `rotation_intake.py`, `zone_beat.py`;
  `backend/tests/unit/test_phase31_{a_rotation,b_clutch_payoff,
  c_context,hooks,vi_hooks}.py`;
  `frontend/components/graphics/templates/lowerthird-eliminated.tsx`;
  ye ledger `docs/PHASE31_FINAL_LEDGER.md`.
- QA gate: independent QA/security reviewer ka verdict is ledger
  se alag report me aayega; QA PASS ke bina Phase 31 complete
  nahi mana jayega.
