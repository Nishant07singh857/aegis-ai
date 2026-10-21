# Phase 24 — Spectator Director Brain: Final Ledger

**Date:** 2026-10-20 · **Gate:** W5 independent QA / security (did not build any of this code)
**Gate verdict: SIGN-OFF YES-WITH-NOTES** — see §6 for the exact notes. No code was edited by W5; the only W5 edits are this ledger and the status-label updates in `docs/SIGNAL_REGISTRY.md` Layer 3.

---

## 1. Verification evidence (totals)

| Check | Result |
|---|---|
| Phase 24 test files (`test_vision_intelligence_derivations`, `test_phase24_director_coverage`, `test_poi_database`, `test_live_stats`) | **128/128 passed** |
| Related suites (vision-intelligence, director ×4, match_state, match_player_utility, phase22_w3, smash_rule, grenade_trajectory, utility_detector_world) | **250/250 passed** |
| Full backend suite | Multiple full runs landed between 33 and 65 failed (heavily environment/order-sensitive scaffold suites — see §5); per-file HEAD comparison over **every** failing file, e2e included, proves **zero new failures** |
| Frontend `tsc --noEmit` | **exit 0** |
| Frontend production build (`next build`) | **exit 0** |
| W5 no-fake probes (throwaway, in /tmp, not in repo) | **59/60 first-pass; 60/60 after correcting one probe's own arithmetic** (see §3 note on probe g4) |
| W5 security probes | **32/33 first-pass; the 1 "failure" resolved as by-design** (see §4) |
| POI database | **127 POIs / 5 maps verified by direct load** (erangel 53, miramar 26, sanhok 22, livik 13, rondo 13) |

---

## 2. Per-signal table — all 30 SPECTATOR_COVERAGE rows, status AFTER Phase 24

| # | Signal | Status after P24 | Closing evidence |
|---|---|---|---|
| 1 | Full-map team positions | IMPROVED-still-partial | Derivations now consume attributed positions (zone distances, rotations, gatekeep); still one player's local minimap, anonymous markers, no full-map reader, no marker→team identity (L3/L4 stand) |
| 2 | Per-player POV availability (16 slots) | IMPROVED-still-partial (unchanged by P24) | Capture/switching unchanged; vision *analysis* still runs on one configured slot — 16-feed parallel analysis remains a follow-up |
| 3 | HP bars + HP-drop velocity | IMPROVED-still-partial | HP now reaches the broadcast output: `observed{hp,boost}` snapshot → `useObserved` → `PlayerPanel`/`GameTopBar` (frontend diff verified). Still slot-level, no named-player attribution |
| 4 | Kill feed — killer/victim names | DONE | Was already COVERED pre-P24; unchanged and still covered |
| 5 | Kill feed — weapon icons | IMPROVED-still-partial | Not a P24 deliverable — Phase 23 icon work is in flight concurrently (see §5 boundary audit); spotter manual weapon entry remains the only live source |
| 6 | Knock vs finish; headshot icon | IMPROVED-still-partial | Headshot now has a real producer: spotter headshot toggle → `payload["headshot"]` in the feed bus payload → live-stats `headshots` (probe h3). Icon recognition still Phase 23 |
| 7 | Alive count (HUD) | IMPROVED-still-partial | Alive count plumbed to the output top bar via `useObserved` → `GameTopBar aliveCount` prop (DirectorOutputView diff verified) |
| 8 | Team status panel | IMPROVED-still-partial | Same 4-slot capture, but team-status transitions now also drive knock sessions + revive inference; all-16-teams panel capture still does not exist |
| 9 | Zone geometry + timer/phase | IMPROVED-still-partial | Per-team distance-to-edge/centre now computed live (`geometry.zone_distances`); zone clock derives countdown from real timer reads (null when never read — probe f). Next-zone circle still has no source |
| 10 | Flight path + plane position | DONE (as designed) | Closed by the director-set flight-path service (`services/flight_path.py`, `source: "director-set"` on every surface — probe-verified in the POST response) + PhaseTracker as first live caller of `ParachutePhaseDetector`. Detection is deliberately not claimed |
| 11 | Drop locations | IMPROVED-still-partial | Predicted drops from recorded history over the flight corridor (P16) landed; a *live* drop producer (parachute-phase → recorded drop) still does not exist — drops remain manual-ingest |
| 12 | Airdrops | IMPROVED-still-partial | AirdropTracker over confirmed ground crates + spotter airdrop button (D1 closed); contested flag probe-verified (g). Minimap `air_drops` icon field is still never populated |
| 13 | Vehicles | IMPROVED-still-partial | Vehicle-rotation inference (marker count + sustained centroid speed, labelled inferred) landed (P11); no type/occupants/ownership claims |
| 14 | Fights | IMPROVED-still-partial | Fight forecasting added on top of the existing inference: third-party, heat, gatekeep (probes b/c). Muzzle-flash and audio legs still do not exist (L7) |
| 15 | Utility visuals | IMPROVED-still-partial | Spotter smoke tag added (D7 half closed); the detector rewrite in the tree is Phase 23 work (see §5). Thrower attribution remains impossible from the effect alone |
| 16 | Knocked states / duration / bleed-out | IMPROVED-still-partial | Knock sessions now timestamp duration per slot and killfeed-named victim, with bleed-out urgency (labelled heuristic). Still slot-level, not per-named-player from vision |
| 17 | Revives | DONE (as labelled inference) | Revive inference live: KNOCKED→ALIVE → `revive_event` (`inferred: true`) + `TeamTracker.record_revive()` has its first live caller (probe d, spy-verified). KNOCKED→DEAD yields `bled_out`, never a revive |
| 18 | Player status icons | IMPROVED-still-partial | Parachuting is now phase-inferred via the PhaseTracker; healing/boost unchanged (already captured); swimming/driving still have no detector |
| 19 | Match clock / phase timing | DONE | `MatchClock` derives feed-elapsed time from bus `match_start`/`match_end`, monotonic, freezes at match end, `None` before start (probe: `/flight/clock` returns `elapsed_s: null`, `state: not_started`). Payload labels the basis `feed_elapsed_spectator_delayed` |
| 20 | Standings / points derived live | DONE (live writer; post-match remains authoritative) | `services/live_stats.py` accumulates the real stream and flushes provisionally through `MatchService.record_player_stats`; `GET /standings/live` is labelled `live: true, provisional: true`; placement withheld until the elimination order truly determines it (probes h7–h10); manual/final rows win (probes h11–h17) |
| 21 | Positions → distance / bearing | IMPROVED-still-partial | Bearing now computed for tracked teams (`geometry.bearing_degrees`, in the rotations snapshot). Distances still minimap pixels unless a scale is calibrated — metres never faked |
| 22 | Zone-relative position per team | IMPROVED-still-partial | Signed distance-to-edge + distance-to-centre per positioned team, zone-pull winners/losers on each new reveal (probe e). All-teams coverage extends only as far as attributed/labelled positions exist |
| 23 | Boost bar / healing state (player panel) | IMPROVED-still-partial | Boost now has a consumer (observed snapshot) and a display path (PlayerPanel via `useObserved`); still per screen slot, unattributed to a named player |
| 24 | Compass / spectated-player identity | DOCUMENTED-LIMIT | No compass reader exists and none was built; identity still resolves slot→team only. The spectated *player* is not identifiable from the spectator feed beyond the slot (see L3) |
| 25 | Team logos/colours on map markers | DOCUMENTED-LIMIT | Marker→team identity is fundamentally unavailable from anonymous minimap markers (L3); map-cam marker colour remains a presentation hash, honestly labelled |
| 26 | Damage — per-player damage dealt | DOCUMENTED-LIMIT | L1 stands. Live stats writer **never** writes `damage_dealt` (probe h4: the key is absent from every flushed row; probe h5: snapshot carries `null`). Taken-side HP-drop tracking unchanged |
| 27 | Loot / death crates | IMPROVED-still-partial | Loot stream now also feeds the airdrop tracker; who looted a crate and its contents remain unclaimed (L2) |
| 28 | Flare gun use | IMPROVED-still-partial | Spotter flare button + additive `FLARE_USED` feed event type landed (D5 closed on the human-tag side); no vision flare detector exists |
| 29 | Audio cues | DOCUMENTED-LIMIT | L7 stands: mixed/delayed program audio only; `NullAudioGunshotProvider` remains the only provider; no claim made |
| 30 | Observed player's weapon + ammo | IMPROVED-still-partial (unchanged by P24) | Still no HUD weapon/ammo reader; killfeed weapon icons are Phase 23 scope. PlayerPanel still renders "—" honestly |

**Roll-up after Phase 24:** DONE 5 (rows 4, 10, 17, 19, 20) · IMPROVED-still-partial 21 · DOCUMENTED-LIMIT 4 (rows 24, 25, 26, 29). Zero rows regressed.

---

## 3. Per-derivation verdicts (W2 / W3 / W6)

Probe key: W5 throwaway probes in `/tmp/probe24.py`, `/tmp/probe24_g.py`, `/tmp/probe24_h.py`, `/tmp/probe24_sec.py`, driving the real services with synthetic bus/event inputs.

| Derivation | Verdict | Probe / suite evidence |
|---|---|---|
| Zone distance-to-edge / centre (`geometry.zone_distances`, `zone_state`) | VERIFIED | Suite 128; probes e (pull geometry), f (distances feed urgency inputs) |
| Zone-pull winners/losers (`ZoneAnalyticsState._snapshot_zone_pull`) | VERIFIED | Probe e1–e4: snapshot exactly once per geometry change; repeated identical zone events produce **no** new snapshot and **no** duplicate alert |
| Rotation intel — bearing / speed / path (`RotationTracker`) | VERIFIED | Suite; probe f (rotation event emitted, position history tracked) |
| Rotation start classification early/late (`classify_rotation_start`) | VERIFIED | Probe f3: classification is `null` when phase timing is unknown (never guessed); suite covers early/late with real phase timing |
| Rotation urgency (`urgency_band`) | VERIFIED | Probe f1/f2: urgency **and** `time_to_zone_close_s` are `null` when no zone timer was ever seen |
| Third-party alert (`FightForecaster.record_combat` / `check_active_fights`) | VERIFIED | Probe b1–b3: 2 distinct killer teams → nothing; 3rd distinct team → fires once; same signature does not re-fire |
| Fight heat (`heat_snapshot`) | VERIFIED | Suite (decay/window cases in the 128); no-input → empty snapshot (probe a-family via `intel_fights` shape in suite) |
| Gatekeep alert (`evaluate_gatekeep`) | VERIFIED | Probe c1–c4: no fire without a labelled outside position, no fire when the fight team is inside, no fire outside the edge band; positive control fires with `inferred: true` |
| Team shape — split / isolated (`compute_team_shape`) | VERIFIED | Suite; probe i4; split/isolated alerts are edge-triggered in `service.py` |
| Airdrop tracker (`AirdropTracker`) | VERIFIED-WITH-NOTES | Probes g1–g6 + corrected re-run: contested flips only on same-slot combat within 60 s. **Note:** the window is symmetric — combat up to 60 s *before* a crate is recorded also marks it contested at birth (`_contested` checks prior combat). That matches the documented "same-slot combat within 60 s" semantics; flagged here so the semantics are on record, not as a defect |
| Knock sessions (`KnockSessionTracker`) | VERIFIED | Probe d (sessions open/close on real transitions only; a slot first *seen* knocked opens no session) |
| Revive inference + `record_revive` wiring | VERIFIED | Probe d1–d6: exactly one `revive_event` on KNOCKED→ALIVE with `inferred: true, source: team_status_transition`; spy confirms `record_revive` is actually invoked; KNOCKED→DEAD → `bled_out`, zero revive events, zero `record_revive` calls |
| Bleed-out urgency | VERIFIED | Suite; emitted payload carries `inferred: true`, `label: heuristic`, thresholds — never a countdown |
| Vehicle-rotation inference (`rotation_mode`) | VERIFIED | Suite; snapshot carries `rotation_mode_inferred: true` + source string |
| Status snapshot `observed{...}` | VERIFIED | Probe a6: all-null on an empty stream; suite covers populated values; frontend consumes exactly this shape |
| Match clock (`services/match_clock.py`) | VERIFIED-WITH-NOTES | Suite + security probe (pre-start `elapsed_s: null`). **Note is the delay wording, §6 note 1** — the clock mechanics and payload labelling are correct |
| Phase tracker (`services/phase_tracker.py`) | VERIFIED | Suite (director coverage file); `vision/parachute.py` unmodified — confirmed in the boundary audit (§5) |
| Phase-aware director suggestions (`director_service.phase_suggestion_plan`) | VERIFIED | Suite; suggestions name their phase; mode gates (D13) unchanged |
| Flight-path service (`services/flight_path.py`) | VERIFIED | Security probes: POST gated, response snapshot carries `source: "director-set"`, `is_set: true`; unknown POI names store null coordinates, never invented ones (code-read) |
| Predicted drops (corridor math) | VERIFIED | Suite (poi + director coverage files); no path / no history → honest empty list |
| POI naming (P0) | VERIFIED | Probe i1–i3: normalized position at Pochinki → `"Pochinki"`; minimap-pixel space → `None`; undeclared space → `None` (no wrong name is ever produced) |
| Live stats writer (`services/live_stats.py`) | VERIFIED | Probes h1–h17: dedupe by source id (same event twice counts once); spotter `headshot: true` traced accumulator → flushed row `headshots ≥ 1`; **no flushed row ever contains a `damage_dealt` key**; manual/foreign rows are never overwritten or deleted (flusher yields, deletes only its own ids) |
| Live standings (`GET /standings/live`) | VERIFIED | Probes h6–h10: payload carries `live: true, provisional: true`; placement is `null` + placement points 0 mid-match and even after `match_end` when the elimination order is incomplete; complete order resolves placements with the survivor at #1 |
| Spotter completions (airdrop / flare / smoke buttons, headshot toggle) | VERIFIED | Frontend diff read (spotter page: `airdrop_spotted`, `flare_used`, smoke via `utility`, headshot toggle → `payload: {headshot}`); schemas diff is additive-only (§5) |
| FeedEventType additions (15 → 17) | VERIFIED | `AIRDROP_SPOTTED`, `FLARE_USED` appended; every pre-existing type keeps its value; bus map extended additively (§5) |

**Empty-stream guarantee (probe a1–a9):** with zero events, the vision-intelligence service emits zero intel alerts of every kind, zero airdrops, zero knock sessions, empty rotations, `zone_pull` last = `None`, observed snapshot all-null; the live-stats service produces empty standings, zero player rows and **zero flusher calls**.

**Honesty labels in emitted payloads (not just docstrings) — spot-checked in probe output:** revive `inferred: true` ✔ · gatekeep `inferred: true` ✔ · bleed-out `label: heuristic` ✔ · rotation classification `classification_label: "heuristic"` when classified ✔ · vehicle mode `rotation_mode_inferred: true` ✔ · standings `provisional: true` ✔ · flight path `source: "director-set"` ✔ · clock `clock_basis: "feed_elapsed_spectator_delayed"` ✔.

---

## 4. Security

- **401 without auth** on every new endpoint, verified with both no-token and garbage-token requests: 5 intel GETs (`/vision-intelligence/intel/{rotations,fights,airdrops,knocks,zones}`), flight GETs (`/flight/path`, `/flight/clock`, `/flight/phase`, `/flight/predicted-drops`), POI GETs (`/poi`, `/poi/nearest`), `GET /standings/live`, and `POST /flight/path`.
- **Role gate on `POST /flight/path`:** viewer → 403, observer → 403, operator → 200. **Note:** `director` and `spotter` are also admitted — that is the pre-existing shared `require_operator` gate (`BROADCAST_CONTROL_ROLES = admin/operator/director/spotter` in `app/api/deps.py`, **untouched by the Phase 24 diff**; the same gate protects feed ingest and stream start). Not a Phase 24 loosening; recorded here because the gate's name undersells its membership.
- **No new secrets:** `git diff` scanned for key/token/password assignment patterns — zero hits in Phase 24 additions.
- **No `shell=True`, no string-built SQL** in any new Phase 24 module; the flusher's deletes/selects are bound-parameter SQLAlchemy over its own tracked row ids; the predicted-drops read is a plain `select(DropEvent)`.

---

## 5. Boundary audit (Phase 23 concurrency) + full-suite classification

**`backend/app/vision/**` changes in the working tree** (Phase 24 was forbidden from editing this tree):

| File | Change | Judgement |
|---|---|---|
| `vision/detectors/utility_detector.py` | 736-line rewrite, header: "Phase 23, W3 rewrite" — world-side utility detection recalibrated on real VOD frames | **Phase 23 scope** (utility icon/phenomena recognition) |
| `vision/runtime.py` | +6 lines: `stun` dedup window + `"source": "world_detector"` tag, commented "Phase 23 (W3)" | **Phase 23 scope** |
| `vision/icons/` (new) | Weapon/utility icon template PNGs | **Phase 23 scope** (icon recognition) |
| `services/grenade_trajectory/` (new) + `tests/unit/test_grenade_trajectory.py` + `docs/GRENADE_TRAJECTORY.md` | Estimated grenade arcs, labelled `estimated: true` | **Phase 23 scope** (named in the Phase 24 task brief as P23's) |
| `agents/team_agents/utility_tracker.py` (+11) | `STUN` utility type, commented "Phase 23 (W3)" | **Phase 23 scope** |
| `analysis/router.py` (+57) | `GET /analysis/grenade-trajectories`, commented "Phase 23, W4" | **Phase 23 scope** |

**No Phase 24 work leaked into `vision/`:** `vision/parachute.py` is unmodified (PhaseTracker is its first live *caller*, from `services/`), and no derivation/POI/live-stats code lives under `vision/`. Phase 23's own gate must certify those files — W5 only classifies them here.

**`feed_adapter/schemas.py` diff: additive-only — CONFIRMED.** Two enum members appended (`AIRDROP_SPOTTED`, `FLARE_USED`), two bus-map entries added, one payload key added (`headshot`, defaulting `False` from the payload). No existing type, value, or mapping changed or removed.

**Full-suite failure classification:** full-run failures were localised to 16 files (19× OBS-integration, 5× dossier-PDF `fpdf2`-missing, 1× event-bus Redis, scaffold-era `test_vision/**` + vision detector tests, 1× `test_ml/test_vision_wiring`, and 13× scaffold-era e2e: broadcast pipeline / full-match flow / highlight generation). W5 re-ran exactly those files in a clean HEAD worktree and in the working tree: **non-e2e files — HEAD = 51 failing/error lines, worktree = 46; e2e + fight-agent files — HEAD = 15 FAILED, worktree = 14 FAILED; in both comparisons zero failures exist only in the worktree** (HEAD-only extras: 3 Piper voice-synthesizer tests that fail in the bare worktree because voice files live in the main tree, log noise, and one flaky scaffold fight-agent test that fails at HEAD and passed in the worktree). Every working-tree failure is therefore in the known pre-existing baseline (cf. PHASE22_FINAL_LEDGER: scaffold-era vision tests, `fpdf2` not installed, no Redis server). Run-to-run count wobble (33 → 51 → 65 failed across full runs, the last with e2e included: 65 failed / 1143 passed / 8 errors) comes from these same environment/order-sensitive scaffold suites, not from Phase 24. The collection errors (8–12 depending on run) also match the known legacy baseline (missing `httpx_ws`, scaffold imports of long-moved modules).

---

## 6. Gate verdict

### SIGN-OFF: **YES-WITH-NOTES**

Phase 24 does what it claims, the no-fake guarantees hold under adversarial probing, and there are zero regressions. The notes are wording/labelling and documentation-debt items — none blocks sign-off, all are owner-tagged in §7.

1. **Delay wording overstated in one place (owner: W3).** `backend/app/services/match_clock.py` docstring states "BGMI spectator mode runs ~30-60 s behind the real game" as an "operational fact". The Spectator Production Research established there is **no published spec number** — the figure is a venue observation only. The emitted payload is honest (`clock_basis: feed_elapsed_spectator_delayed`, no number), so this is a docstring fix: reword to "observed at venues at roughly 30–60 s; no published spec exists — measure on the real rig". The same figure is repeated in `docs/SIGNAL_REGISTRY.md` (L11 pointer) and `docs/SPECTATOR_COVERAGE.md` §3 L11 — both predate/are outside W5's edit scope; flagged in §7.
2. **`require_operator` admits spotter/director on `POST /flight/path`** — by design of the pre-existing shared gate (see §4), recorded so nobody reads the gate name as operator-only.
3. **Airdrop contested window is symmetric** (combat ≤60 s *before* a crate record also contests it) — matches the documented semantics; on record in §3.
4. **Registry gap (owner: W4/registry owner):** the W6 live-stats writer / live-standings service has **no row** in `docs/SIGNAL_REGISTRY.md` (Layer 1 row 20 / D9 still describe the pre-P24 state). Adding a row was beyond W5's allowed edit (status labels only); §7 carries it as a follow-up.
5. **Concurrent-tree caveat:** Phase 23 files are landing in the same working tree during this gate. This sign-off covers Phase 24 scope only; Phase 23's changes (utility detector rewrite, icons, grenade trajectory) need their own gate.

---

## 7. Follow-ups (owner-tagged)

1. **D9 killfeed region/parser calibration — owner: Phase 23.** Killfeed default region is still top-RIGHT in code vs the real top-LEFT icon-based feed; parser anchors on action words real BGMI doesn't render as text. Unchanged by Phase 24; still the top vision-accuracy risk.
2. **Normalized-map position source for POI naming — owner: vision team.** POI naming works (probe i1) but only fires when a source supplies `map_normalized` positions; the live minimap stream is pixel-space and correctly yields no name (probe i2). A full-map/map-cam position source is the missing producer.
3. **16-feed parallel vision analysis — owner: vision team.** Detectors still run on one configured slot (coverage row 2); the other 15 feeds are view-only.
4. **Spectator-delay measurement on the real rig — owner: user/ops.** No published spec exists; measure with a stopwatch on the actual rig and publish the figure as the sync offset. Until then, no delay number should be stated as fact anywhere (see §6 note 1; `match_clock.py` docstring reword — owner: W3).
5. **`docs/SPECTATOR_COVERAGE.md` §3 + §1 text updates — owner: W1/coverage-doc owner (W5 may not edit it).** After Phase 24 these are less true or stale:
   - **L12** — stats now have a live writer for kills/knocks/headshots/times_revived (damage dealt and revives *performed* remain manual/null; the limitation should be narrowed, not deleted).
   - **L11** — delay figure should be labelled venue observation, no published spec (research correction).
   - **Row 3 / 7 / 23** — HP, alive count and boost now reach the broadcast output via the observed snapshot (the "NO on broadcast output" / "PlayerPanel shows —" text is stale).
   - **Row 6** — headshot now has a spotter producer (icon recognition still P23).
   - **Row 10** — flight path exists as director-set data (not detection); `ParachutePhaseDetector` now has a live caller.
   - **Row 12** — spotter airdrop tag now exists; airdrop tracker landed.
   - **Row 16** — knock duration/bleed-out tracking now exists per slot.
   - **Row 17** — revive inference + `record_revive` caller now exist (D2 in §4 of that doc is closed on the inference side).
   - **Row 19** — match clock now has a live source (feed-elapsed).
   - **Row 20** — live standings writer now exists (provisional; post-match remains authoritative).
   - **Row 21** — bearing is now computed for tracked teams.
   - **Row 22** — distance-to-edge/centre now computed in the live path for positioned teams.
   - **Row 28** — spotter flare tag now exists (D5 closed on the human-tag side).
   - §4 discrepancies **D1, D2, D5** should be marked closed (spotter tags + inference landed); **D7**'s spotter-smoke half is closed.
6. **Registry row for the W6 live-stats/standings writer — owner: W4/registry owner** (see §6 note 4). W5 updated all Layer-3 P-row status labels + real module/event names in `docs/SIGNAL_REGISTRY.md`; totals line updated ("P0–P16 landed, W5-verified").
7. **Live drop producer — owner: future phase.** Predicted drops exist, but nothing yet records a drop automatically from the parachute phase (row 11 remains manual-ingest).

*End of ledger. Probes were throwaway (/tmp) and are not in the repo; no code file was modified by W5.*
