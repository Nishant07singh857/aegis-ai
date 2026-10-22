# Observer Auto-Pilot (Phase 29)

Per-feed POV pilot: inside ONE observer feed (one slot, one team), which
player should the spectator camera be on — and when it must move, to
whom, and why.

Pipeline shape:

| Stage | Owner | Artifact |
|---|---|---|
| Screen → state | W1 | `backend/app/services/observer_pilot/types.py` + `state.py` — `FeedPOVState` per feed |
| State → decision | W2 (this doc's rules contract) | `backend/app/services/observer_pilot/rules.py` — `POVPilotRules.decide()` |
| Decision → execution | W3/W4 | ADB tap executor + pilot service wiring (monitor events → `PilotSignals`, mode policy below) |

The rules engine is **pure deterministic logic**: no vision, no ADB, no
clock reads. Every timestamp is an input (`PilotSignals.now`,
`last_tap_at`, `last_fight_signal_at`), the engine is stateless, and
every decision carries a human-readable `reason` plus an `evidence`
dict so a camera move can always be audited after the fact. A no-action
outcome is equally explainable via `PilotNote` /
`explain_no_action()` — a still camera is never silent about why.

---

## 1. Shared contract (exact)

Defined by W1 in `backend/app/services/observer_pilot/types.py`
(`POVDecision` is defined in `rules.py` — W1's types module does not
carry it; the field shape below is the brief contract and must not
drift):

```python
class POVPlayerStatus(str, Enum): ALIVE / KNOCKED / DEAD / UNKNOWN

@dataclass PlayerPOV:
    roster_index: int            # 0..3
    player_id: Optional[str]
    name: Optional[str]
    status: POVPlayerStatus
    hp_fraction: Optional[float]
    hp_velocity: Optional[float] # negative = HP falling
    is_spectated: bool

@dataclass FeedPOVState:
    slot_number: int
    source_id: str
    team_id: Optional[str]
    observed_at: float
    spectated_index: Optional[int]   # None = UNKNOWN
    players: Tuple[PlayerPOV, ...]
    identity_known: bool

@dataclass POVDecision:            # rules.py
    slot_number: int
    source_id: str
    from_index: Optional[int]
    to_index: int
    rule: str                      # e.g. "R1_SPECTATED_DEAD"
    reason: str                    # plain English, names included
    evidence: Dict[str, Any]
    decided_at: float
```

`PilotSignals` (rules.py, filled by W4 per call):

```python
killfeed_involvements: List[Tuple[roster_index: int, kind: str, at: float]]
                       # kind in "killer" | "victim" | "knocked"; THIS team's events only
fight_active_on_feed: bool   # team-level fight flag (LiveDirector engagement)
phase: str                   # LiveDirector phase vocabulary (see §5)
now: float                   # injected clock for this evaluation
```

Engine entry points (W4 uses these):

```python
POVPilotRules(config: PilotConfig = PilotConfig())
  .decide(state, signals, last_tap_at, last_fight_signal_at) -> Optional[POVDecision]
  .decide_with_note(...) -> (Optional[POVDecision], Optional[PilotNote])
  .explain_no_action(...) -> str
  .effective_dwell_s(phase) -> float
```

---

## 2. Configuration — `PilotConfig` (one place, nothing scattered)

| Field | Default | Meaning |
|---|---|---|
| `dwell_s` | 9.0 | Minimum seconds between two pilot taps on the same feed |
| `hp_drop_velocity` | -0.05 | HP velocity at/below which a player counts as fighting (fraction/sec) |
| `quiet_window_s` | 6.0 | A fight signal is "fresh" inside this window; no fresh signal + no HP drop = quiet |
| `return_primary_delay_s` | 10.0 | Whole team quiet this long before R5 may return to primary |
| `final_zone_dwell_multiplier` | 2.0 | Dwell multiplier in the final zone |
| `primary_roster_index` | 0 | Primary player (default: first in roster order — the IGL slot) |

These are editorial tunables, not measurements; no accuracy claim
attaches to any of them.

**"Fighting" definition** (used by R2/R3/R4): a player is fighting iff
(a) they have a killfeed involvement with `now - at <= quiet_window_s`,
OR (b) `hp_velocity is not None and hp_velocity <= hp_drop_velocity`.
A `hp_velocity` of `None` means that signal is **absent** — it is never
treated as zero and never guessed.

**Dwell gate**: every rule except R1 requires
`now - last_tap_at >= effective_dwell`, where
`effective_dwell = dwell_s * final_zone_dwell_multiplier` in the final
zone, else `dwell_s`. `last_tap_at=None` (no tap yet) always passes.
A dwell block produces no decision; `decide_with_note()` returns a
`PilotNote` with `blocked_rule` set and the elapsed/required numbers in
its evidence, so W4's status surface can show exactly why the camera
did not move.

---

## 3. Rules — evaluation order R0, R1, R4, R2, R3, R5 (first match wins)

The listing order in the phase brief is R0–R5; the **evaluation** order
moves R4 ahead of R2/R3 for one reason: if the spectated player is
themselves fighting, the camera must never move, and that check has to
run before any "teammate is fighting" rule reads the spectated player's
quietness the wrong way round. R1 stays ahead of R4 because a dead
camera is useless even mid-fight.

| Rule | Trigger | Action | Evidence required | Dwell |
|---|---|---|---|---|
| **R0 NO-EVIDENCE** | `identity_known=False`, OR `spectated_index=None`, OR spectated player missing from the player list, OR spectated status `UNKNOWN` | No decision (`None` + note), always | — (absence of evidence IS the trigger) | n/a |
| **R1 SPECTATED_DEAD** | Spectated player `DEAD` | Move to next `ALIVE` teammate in roster order after the current index, wrap-around. No alive teammate → no decision | Spectated status `DEAD`; target status `ALIVE` | **BYPASSED** — a dead camera carries no information; the bypass is stated in the reason + `evidence.dwell_bypassed=True` |
| **R4 FIGHT_LOCK** | Spectated player `ALIVE` and fighting (definition above) | No decision — camera never leaves a player who is in the fight | Fresh killfeed involvement and/or HP velocity for the spectated player | n/a (it is a lock, not a move) |
| **R2 SPECTATED_KNOCKED** | Spectated player `KNOCKED` and at least one teammate is `ALIVE` + fighting | Move to the strongest-signal fighting teammate (§4) | Target `ALIVE` + fighting; spectated `KNOCKED` | Applies |
| **R3 TEAMMATE_IN_FIGHT** | Spectated player `ALIVE` and quiet (no fresh fight signal inside `quiet_window_s`, no HP drop), and a non-spectated teammate is `ALIVE` + fighting | Move to the strongest-signal fighting teammate (§4) | Target `ALIVE` + fighting; spectated quiet (guaranteed structurally: R4 already returned otherwise) | Applies |
| **R5 RETURN_PRIMARY** | Spectated is not the primary, primary is `ALIVE`, and the whole team has had no fight signal for **strictly more than** `return_primary_delay_s` (no one fighting, no fresh killfeed, `fight_active_on_feed=False`) | Move back to `primary_roster_index` | `last_fight_signal_at` merged with all killfeed timestamps in the call; if no fight signal was ever recorded, the team counts as long-quiet | Applies |

Candidate-side honesty (all rules): a target is only ever an `ALIVE`
player. `UNKNOWN`, `KNOCKED` and `DEAD` teammates are never selected —
status unknown means no action on that player, exactly as R0 states for
the spectated player.

Notes on specific rules:

- **R4 is ALIVE-only, deliberately.** A knocked player typically carries
  a fresh `victim` killfeed entry; counting that as "fighting" would
  lock the camera onto a knocked player and R2 could never fire. A
  knocked spectated player is handled by R2 (fighting teammate exists)
  or falls through to R5 (team long quiet → return to primary).
- **R5 timing boundary is strict**: at exactly
  `quiet_for == return_primary_delay_s` the rule does NOT fire; it
  fires only once the quiet period exceeds the delay.
- **Every `POVDecision`** carries `rule` (the stable `R#_NAME` string),
  a plain-English `reason` naming both players (`"Spectated player
  GodL (idx 1) is DEAD; moving to next alive teammate Nova (idx 2)…"` —
  a player with no known name is labelled `player at roster index N`,
  never given an invented name), and an `evidence` dict with the
  statuses, HP velocities, killfeed involvements and dwell values the
  decision actually used.

---

## 4. Evidence priority (multi-teammate fights — R2/R3 target choice)

When several teammates are fighting, the target is chosen
deterministically:

1. **Freshest knocked/killer killfeed involvement** — the most recent
   `("knocked" | "killer")` involvement inside the quiet window wins.
   Any kind counts as fight *involvement* (that is the fighting
   definition), but this priority tier ranks the kinds that mark a
   player as driving the fight.
2. **Most negative `hp_velocity`** — fastest HP loss. A missing
   velocity (`None`) ranks below any measured velocity on this tier.
3. **Lowest `roster_index`** — exact tie-break, so the same inputs
   always produce the same camera.

**Proximity is NOT a tier.** There is no per-player proximity evidence
available from the spectator screen, so it is never estimated, faked,
or used as a tie-break. If a future vision stage produces a real
per-player proximity signal, it may be inserted as a tier only with a
measured source named in this doc.

---

## 5. Director-grammar consistency (Phases 25/27)

The pilot works **inside** one feed; the LiveDirector
(`backend/app/services/live_director/`) works **between** feeds. They
share one grammar instinct — do not thrash during a fight — at two
scales:

- **Fight-hold**: the director holds a program cut for
  `min_fight_hold_s(phase)` — 30 s (plane/parachute/loot/early), 45 s
  (mid_game), 60 s (late_game/final_circle), and in `final_circle` a
  fight locks the program feed until it resolves. The pilot's analogue
  is R4 (never move off a fighting player at all) plus the dwell gate
  between taps.
- **Final zone**: the director's holds lengthen as the phase advances;
  the pilot mirrors this with `final_zone_dwell_multiplier` (dwell
  doubles in the final zone instead of staying flat early-game).
- **Phase vocabulary**: `PilotSignals.phase` uses the
  PhaseTracker/LiveDirector names — `plane`, `parachute`, `loot`,
  `early`, `mid_game`, `late_game`, `final_circle`. The Phase 29
  brief's spelling `final_zone` names the same phase and is accepted
  as an alias by `is_final_zone()`; `final_circle` is canonical.
- **No manufactured switches**: just as the director's storyline
  multiplier can never create a switch from a zero fight-signal base,
  the pilot never moves a camera without per-player evidence — R0 is
  the pilot's version of that honesty rule.

---

## 6. Mode policy — W4's execution contract

The rules engine only **decides**. Whether a decision is executed,
proposed, or merely observed is decided by the pilot service (W4)
from the director mode (`DirectorService` mode:
`full_auto | semi_auto | manual | clone`) and the pilot kill-switch.
This section is the contract W4 implements; the rules engine itself is
mode-agnostic and must stay that way.

| Mode | Pilot behaviour |
|---|---|
| `full_auto` | `POVDecision`s **auto-execute** (tap is performed, `last_tap_at` updated, decision logged) |
| `clone` | Same as `full_auto` — decisions auto-execute. (The learned-profile distinction of Phase 27 applies to the LiveDirector's knobs; the pilot's config is not a learnable knob surface unless a later phase explicitly registers it.) While clone is **grabbed** by a human, the pilot behaves as `manual` (below) until release — a grabbed clone is human-driven, exactly as in the LiveDirector contract |
| `semi_auto` | A decision becomes a **pilot suggestion**; it executes only on the director's one-tap approval. An unapproved suggestion expires without executing and never updates `last_tap_at` |
| `manual` | Pilot is **shadow-only**: decisions/notes are computed and shown on the status surface, but no suggestion is offered for execution and no tap is ever performed |
| Kill-switch OFF (any mode) | **No tap in any mode.** The engine may still be evaluated for status/shadow display, but execution and suggestions are both suppressed |

Invariants W4 must preserve:

- Only an **executed** tap updates `last_tap_at` (suggestions and
  shadow decisions must not feed the dwell gate as if they were taps).
- `last_fight_signal_at` is fed from real monitor/director events for
  the feed's team (kill/knock/fight activity), never synthesised.
- Every executed tap is logged with the full `POVDecision`
  (rule, reason, evidence) — the audit trail is the decision object
  itself, not a paraphrase.
- The kill-switch check happens at execution time, not decision time,
  so flipping it mid-suggestion is safe.

---

## 8. Integration (W4) — service, journal, API, frontend

### Service lifecycle

`ObserverPilotService`
(`backend/app/services/observer_pilot/service.py`) is built in the
app lifespan (`backend/app/main.py`), AFTER the Phase 26 multi-feed
monitor, failure-isolated like every other lifespan service: with it
down, directing and monitoring are unchanged and the pilot API
reports `running: false`. Startup order inside the lifespan:

1. ONE `CachedRosterProvider` is created and refreshed.
2. It is threaded into `build_multi_feed_monitor(roster_provider=…)`
   so the per-feed POV trackers bind identity, AND into the pilot
   service, whose loop drives its refresh.
3. The monitor's `pov_state` is injected into the LiveDirector as a
   read-only `pov_state_provider` (status + decision-log colour —
   scoring is never touched).
4. `build_observer_pilot(monitor, bus, roster_provider,
   multi_device_manager)` assembles W3's executor (below) and the
   service subscribes to the bus kill/knock topics and starts its
   ~1 s tick loop. Shutdown stops the pilot before the monitor.

**Loop cadence — why ~1 s:** the monitor samples each feed on its
own cadence (a full 16-feed cycle is seconds-scale, Phase 26/28), so
polling POV state faster would only re-evaluate identical states;
the service additionally skips any state whose `observed_at` it has
already evaluated. The tick also drives the roster refresh check
and suggestion expiry.

**Executor assembly (W4 owns it — W3's executor has no builder):**
`POVTapExecutor(adb=ADBController(), config=ExecutorConfig(),
control=PilotControl(), device_resolver=device_resolver_from_multi_device_manager(multi_device_manager),
frame_provider_for=<the monitor feeds' own frame providers>,
spectated_reader=<POVReader highlight read>)`. Without a capture
manager the resolver defaults to W3's `no_device_resolver`, so every
tap honestly ends `no_device`. The frame provider returns the
grabber's latest frame per call — verification polls therefore see
new frames only as fast as the capture layer produces them.

**Signals per evaluation:** killfeed involvements from the bus
(kill/knock records — killer/victim NAMES mapped to roster indices
through each feed's own roster via `match_name_to_roster`; unmatched
names are dropped and counted, never guessed), `fight_active_on_feed`
from the LiveDirector engagement snapshot (team state
ACTIVE_COMBAT/CLUTCH; absent provider → False), `phase` from the
LiveDirector/PhaseTracker (absent → `"unknown"`, treated as
non-final). `last_tap_at` updates ONLY when `TapResult.taps_sent >
0` — verified and failed_stay both sent a real tap; blocked /
no_device / no_frame sent nothing. (Contract note: W3's executor is
**async** — `execute`/`execute_target` are coroutines returning a
`TapResult` whose outcome text field is `reason`, and
`PilotControl` flips via `set_enabled()`. The service awaits
awaitable results, so sync fakes and the real executor both work.)

### Roster provider

`CachedRosterProvider`
(`backend/app/services/observer_pilot/roster.py`): the trackers need
a SYNC callable, the DB is async — so an async `refresh()` loads
`observer_slots` (slot → team_id) + `players` (team roster) into a
plain dict cache and `__call__(slot)` only reads the dict. Loaded at
pilot/monitor build, refreshed every 30 s by the pilot loop. A slot
with no team, or a team with no players, is absent from the cache →
provider returns None → identity stays UNKNOWN. Roster order is
`players.created_at, id` (insertion order; the players table has no
roster-order column). Display name = IGN when set, else name (the
spectator nameplate shows the IGN).

### POV journal (durable)

Table `pov_pilot_decisions` (migration `016_pov_pilot`, model in
`services/observer_pilot/models.py`, writer `pov_journal.POVJournal`
— the DecisionJournal pattern: sync `record()` that never raises,
injectable session factory, drop counters instead of exceptions).
NOT the director-learning tables: `DirectorDecision` has no
slot/source/from-to/verification fields and mixing POV rows in would
pollute the Phase 27 journal stats. Fields: slot, source_id,
team_id, from/to index, rule, reason, evidence JSON, mode, fate
(`executed|suggested|approved|rejected|expired|shadow|override|
failed`), suggestion_id, verification (TapResult status verbatim),
taps_sent, decided_by, timestamps. Every executed tap, suggestion +
fate, shadow decision, override and failure lands here — this is
the Phase 27-style learning loop's future data source.

### API — `/api/v1/observer-pilot` (operator+ on every route)

| Route | What |
|---|---|
| `GET /status` | Per-feed POV (name/index/statuses), current behaviour per mode policy, pending suggestion, last decision + PilotNote ("kyun nahi badla"), kill-switch state, calibration flags |
| `POST /kill-switch` `{enabled}` | Flip the PilotControl (OFF blocks every tap incl. overrides) |
| `POST /suggestions/{id}/approve` | Execute the pending decision now (kill-switch re-checked at execution); 404 unknown/decided/expired, 409 kill-switch OFF |
| `POST /suggestions/{id}/reject` | Fate journaled, never executed; 404 as above |
| `POST /override` `{slot_number, roster_index}` | Manual tap via `execute_target`, journaled `manual_override`; 409 kill-switch OFF |
| `GET /decisions?slot=&limit=` | Recent journal rows from the DB, newest first |

Writes when the service is not running → 503 (honest), never a
fabricated success. Director `/state` also carries `pov_feeds`
(per-feed spectated name/index + identity_known) from the
LiveDirector's POV provider, and decision-log entries carry
`pov_on_fight` (null when the POV is unknown; read-only colour,
never a scoring input).

### Frontend

`POVPilotPanel.tsx` on the director page (DirectorLearningPanel
pattern): per-feed POV summary, pending suggestions with
approve/reject, kill-switch toggle, 4 player chips per feed for
manual override, recent journal decisions (rule + reason), and the
calibration honesty badge. `Multiview.tsx` PGM/PVW tile headers show
the spectated player's name via a POV marker (`POV unknown` when
identity is unknown); when the spectated index changes the marker
flashes with the last reason as its tooltip. Both poll the pilot
status (5 s) and keep last-known state on a failed poll.

### Mode policy — implementation notes (§6 realised)

- Behaviour resolution is one function
  (`ObserverPilotService.behaviour_for`): kill-switch OFF →
  `off_kill_switch` (decisions still evaluated + shadow-journaled,
  suggestions suppressed); manual or clone-grabbed → `shadow`;
  semi_auto → `suggest`; full_auto / clone-not-grabbed →
  `auto_execute`. Unknown modes degrade to shadow.
- Semi suggestions: one pending per feed, same-target dedupe (the
  existing id is kept, evidence refreshed, no duplicate journal
  row), TTL 20 s (`PilotServiceConfig.suggestion_ttl_s`), expiry
  journaled. A different-target decision supersedes (old one
  journaled expired).
- The kill-switch is checked TWICE: in the service before any
  execution path, and inside the executor as its first step — so
  flipping it mid-suggestion is safe (approve re-checks and returns
  `blocked_kill_switch` → API 409, suggestion stays pending).

### Failure cooldown & DEAD confirmation (post-QA fixes, D-2 / D-1)

Two service-layer gates (both knobs on `PilotServiceConfig`; the
rules engine and the dwell semantics of §2/§6 are unchanged):

- **Failure cooldown (D-2).** An execution that ends
  `failed_stay` starts a per-feed cooldown
  (`failed_retry_cooldown_s`, default 30 s). While it runs, NEW
  decisions on that feed are neither executed nor suggested —
  each hold is journaled (`suppressed_failed_cooldown` in the
  row's reason/evidence, with the remaining cooldown; the journal
  `fate` is `suppressed` because the fate column is String(16))
  and shown on the status surface (`failed_cooldown_active /
  _until / _remaining_s`). A `verified` execution clears the
  cooldown; after it expires, one new retry cycle is allowed. A
  manual override is never blocked by the cooldown, but an
  override's own `failed_stay` restarts it. Without this gate a
  broken calibration made the pilot re-poke a live phone on
  every fresh state (QA repro: 2 taps at t=1000, +2 at t=1020).
- **DEAD confirmation (D-1, pilot-side mitigation).** The
  inherited `TeamStatusReader` can read a low-HP ALIVE card as
  DEAD (measured boundary: HP ≈0.15–0.39 read DEAD, 0.45 ALIVE);
  fixing the reader is out of scope here. Mitigation: an
  `R1_SPECTATED_DEAD` action (execute or suggest) requires the
  spectated player's DEAD reading in `dead_confirm_evals`
  (default 2) **consecutive** evaluations of that feed. The
  first reading holds the action — journal fate
  `dead_unconfirmed`, status shows `dead_confirm_count /
  dead_unconfirmed` — and an ALIVE/KNOCKED reading resets the
  streak. The state is never relabelled: the player still shows
  as DEAD, only unconfirmed. KNOCKED readings and R2/R3/R5 are
  not gated (the defect is DEAD-only).

### Scrim calibration procedure (per-device tap points)

The built-in tap points are UNCALIBRATED estimates (centres of the
four team-bar cards) — `TapCalibration.resolve()` reports
`calibrated=False` for them and every TapResult carries that flag.
Before a real tournament, per device:

1. Connect the observer phone via ADB; note its serial
   (`adb devices`) and its spectator-screen resolution.
2. In a scrim lobby, open the spectator view so the team bar /
   player list is visible. For each roster index 0..3, find the
   on-screen point that switches the POV to that player (tap the
   player's card/name in the spectator player list) and record it
   as screen FRACTIONS: `fx = x_px / width`, `fy = y_px / height`.
   Verify by tapping: the spectated nameplate must change to that
   player. Repeat per phone model/resolution in the rig.
3. Write the calibration JSON and point the backend at it with
   `AEGIS_POV_CALIBRATION_PATH`:
   ```json
   {
     "profiles": {
       "2400x1080": [[fx0, fy0], [fx1, fy1], [fx2, fy2], [fx3, fy3]]
     },
     "per_serial": {
       "<adb-serial>": [[fx0, fy0], [fx1, fy1], [fx2, fy2], [fx3, fy3]]
     },
     "calibrated_profiles": ["2400x1080", "<adb-serial>"]
   }
   ```
   `per_serial` beats the resolution profile for that phone. Only
   list a key in `calibrated_profiles` when its points were REALLY
   measured on that rig — that list is the honesty ledger behind
   the `calibrated` flag (status API + panel badge read it).
4. Restart the backend (calibration loads at executor build), open
   the director console: the panel's UNCALIBRATED badge must be
   gone for the calibrated profiles. Run one scrim match with the
   pilot in semi_auto first — approve a few suggestions and confirm
   the journal shows `verified` — before trusting full_auto.

## 7. What the pilot deliberately does NOT do

- No proximity-based targeting (no such evidence exists — §4).
- No action on unknown identity/state (R0), ever.
- No estimation of missing HP velocity or killfeed data — absent
  signals are absent, and the rules degrade to no-action rather than
  to a guess.
- No mode handling, no ADB, no persistence inside the rules engine —
  history arrives as call inputs; execution belongs to W4.
