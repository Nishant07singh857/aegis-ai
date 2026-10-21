# Weapon & Utility Recognition — Phase 23 (W5 Measured)

**Status: W5 independent QA measurement, 2026-10-21.** Every number in
this document was measured by W5 (who built none of this) on the
held-out evaluation set only. Nothing here is a builder claim.

**The acceptance bar vs the measurement, up front.** The user's bar
was: *every gun icon, every utility, dull icons too.* The system does
**not** meet that bar at 360p, and this document publishes the real
numbers instead of rewording them:

- Weapon **class** accuracy on classes that have templates:
  **48.2 %** overall — **39.4 % on dull icons**, 65.3 % on clean icons.
- Weapon **family** accuracy (ar_a vs ar_b etc.): **43.7 %** overall —
  **34.3 % on dull icons**, 62.0 % on clean icons.
- Two classes are effectively broken: **AR recall is 25.8 %** (most AR
  icons are labelled SMG) and **DMR recall is 0 %** (n=3).
- Classes with **no template** are confidently mislabelled **88.5 %**
  of the time (23/26) — the worst failure mode, present in production.
- **Headshot** marker detection is not usable: **precision 7.7 %**.
- Frag grenade is perfect (100 % / 100 %, n=34) and sniper is solid
  (100 % precision, 62.5 % recall, n=8). Knock detection is usable
  with caveats (precision 61.2 %, recall 90.8 %).

---

## 1. System overview

### 1.1 Killfeed layout (W1, verified)

The broadcast killfeed renders **top-left**, below the spectated
team's roster block — the pre-Phase-23 detector scanned the top-right
(minimap/standings) and could never see an entry. Production scan
region is now `(0.0, 0.24, 0.34, 0.52)` of the frame
(`backend/app/vision/detectors/killfeed.py`). One entry is:

```
[killer logo] killer_name [WEAPON ICON] [marker slot] [victim logo] victim_name
```

There are no action words. Knock = crawling-figure glyph in the
marker slot; headshot = white ring/crosshair glyph; no marker = plain
elimination. Full geometry: `data/phase23_work/LAYOUT_REPORT.md`.

### 1.2 Icon template library (W1)

`backend/app/vision/icons/` — **28 raw native 360p crops**
(`manifest.json`), harvested **only** from BMSD GF Day 1/2 + PMGC GF
Day 1: 7 identified weapon families (ar_a ×3, ar_b ×4, smg_compact
×2, sniper_bolt ×1, dmr_long ×1, frag_grenade ×2, pistol_t ×1),
2 marker families (marker_knock ×4, marker_headshot ×3), and
5 unresolved `unknown_*` families (dome, wheel, diagonal, burst,
shotgunlike) that compete in matching but are never emitted as a
weapon. No enhancement was applied to templates; the library itself
is dull (mean luminance 47.6–116.1).

### 1.3 Matcher (W2)

`backend/app/vision/detectors/icon_matcher.py` — multi-scale
normalised cross-correlation (`cv2.matchTemplate`, TM_CCOEFF_NORMED),
scales (0.85, 1.0, 1.15), 4× cubic upscale, shipped defaults
**threshold 0.55, marker threshold 0.50, preprocess "none"**.
Emission rule: the best-scoring family wins; a weapon is emitted only
if that family is identified **and** score ≥ threshold; an
`unknown_*` win or a low score yields `weapon = None` (honest
unknown). Output is **class-level only** (`ar`, `smg`, `sniper`,
`dmr`, `frag_grenade`, `pistol`) with payload
`weapon_confidence` / `weapon_family` / `weapon_granularity="class"`.
Specific gun models are never emitted.

The layout parser (`killfeed.py::_parse_entry_strip`) walks the top-4
matcher candidates, rejects ones whose location is structurally
impossible against OCR word boxes, derives the marker slot from the
accepted icon location (bounded by the first victim-name OCR word),
calls `match_markers` on that slot, and splits killer/victim names
around the slots. Events with `weapon = None` still flow as
`NormalizedEvent`s (verified in W2 suites and in §4).

### 1.4 Preprocessing modes

`icon_preprocess ∈ {none, bin, clahe, histeq}` is applied identically
to templates (at load) and query crops. A/B results in §2.4: **no
mode decisively helps dull icons**; the shipped default stays
`"none"`.

### 1.5 Event flow & highlight triggers

Parsed entries become `NormalizedEvent`s (kill/knock, headshot flag,
class-level weapon). Weapon data feeds the live highlight paths:
`clip_detector.check_weapon_highlight` (frag_grenade → grenade clip,
sniper path) and `services/replay/highlight_triggers.py` (grenade
multi-kill, sniper triple) — both consume the class ids exactly as
emitted. Consequence of §2: grenade highlights rest on the one
perfect class; sniper highlights on a high-precision class; any
future trigger keyed on `ar`/`smg`/`dmr` inherits the confusion
measured below (AR↔SMG especially) and should not be built on this
data yet.

### 1.6 World-side utility (W3)

`utility_detector.py` was rewritten after a real-footage audit showed
the old smoke/fire paths fired on HUD elements. Now: **smoke** and
**molotov** are live behind measured gates (texture, world-band,
flame-core, flicker/persistence, colour-scene gate; smoke confidence
capped ≤ 0.78; events carry no thrower — team attribution happens via
observer slot downstream). **Grenade-explosion detection is DISABLED
by default** (`enable_explosion_detection=False`): on real footage
its only triggers co-located with molotov fire, so it double-counted.
**Stun detection is implemented but gated OFF**
(`enable_stun_detection=False`): no confirmed real stun exists in 7
VODs to validate against, while broadcast white transitions mimic
it. W5 verified both defaults in code and by direct instantiation.
Stats flow via `UtilityTracker` (the W2-era stun enum gap is fixed:
`UtilityType.STUN` exists and is counted).

**Honest W3 limit:** there is no labelled world-side held-out set, so
smoke/molotov **recall is unproven** — the gates were tuned for
precision on audited footage (false negatives preferred). No accuracy
number for world utility is claimed anywhere in this document.

### 1.7 Grenade trajectory (W4)

`backend/app/services/grenade_trajectory/` — an endpoint-based
**estimated-arc** pipeline: given a thrower position and an explosion
position in one shared space, it renders a dashed arc that is
**always labelled ESTIMATED** (analyst map shows the word ESTIMATED as
visible text, not just a tooltip). The store enforces the label
structurally — W5 verified directly that a record without
`estimated: True` (or with `estimated: False`) is refused with
`ValueError`, and that a well-formed labelled record is accepted.
API: `GET /api/v1/analysis/grenade-trajectories`. Docs:
`docs/GRENADE_TRAJECTORY.md`.

**Honest W4 status: the pipeline never fires today.** No live feed
produces a thrower position and an explosion position in the same
coordinate space, so the bus consumer has nothing to pair. It is a
correct, labelled, tested pipeline awaiting a position producer —
not a live feature.

---

## 2. Held-out accuracy — the core measurement

### 2.1 Method (W5)

- Set: `data/phase23_work/heldout/` — **465 whole-entry crops**,
  labels in `labels.csv` (ground-truth family, quality tag, per-entry
  knock/headshot truth). VODs: BMSD GF Day 3, BMPS GF Day 1, PMGC GF
  Day 2, PMGO Day 1 — **none used for templates or tuning**.
  Quality: **320 dull / 145 clean** (deliberately pessimistic OR rule,
  see LAYOUT_REPORT §6).
- Weapon: production `IconMatcher.match()` on each whole-entry crop,
  exactly as shipped (per preprocess mode). Raw best family, score,
  and emitted weapon/family recorded per entry
  (`data/phase23_work/w5_weapon_results.csv`, harness
  `w5_eval.py` / `w5_analyze.py`, summary `w5_summary.json`).
- Markers: production `match_markers()` on the marker slot
  constructed exactly as the layout parser constructs it (icon
  location from the first candidate ≥ threshold; slot = icon right
  edge +1k … +36k, k = strip-height/24; the parser's additional
  OCR-victim bound can only shrink the slot further)
  (`w5_marker_results.csv`).
- End-to-end cross-check: the full production
  `KillFeedDetector._parse_entry_strip` on all 465 strips
  (`w5_parser_results.csv`).
- Denominators: *identified* classes (template + identified) n=357
  (ar_b 182, smg_compact 73, ar_a 57, frag_grenade 34, sniper_bolt 8,
  dmr_long 3). *Unseen* n=108: templated `unknown_*` truth 82,
  no-template classes 26 (lmg_box 14, bullpup 5, unknown_lmgT 4,
  vehicle 3). Pistol has a template but **zero** held-out instances.

### 2.2 Headline results (shipped config: none / 0.55 / 0.50)

| Metric | Overall | Dull (n=236 id.) | Clean (n=121 id.) |
|---|---|---|---|
| Emitted **class** accuracy (identified classes) | **48.2 %** | **39.4 %** | 65.3 % |
| Emitted **family** accuracy (identified classes) | **43.7 %** | **34.3 %** | 62.0 % |
| Raw top-1 family (best match, templated truths, n=439) | 52.6 % | 48.7 % | 62.0 % |
| Emission coverage on identified classes | 99.4 % | — | — |

Coverage of 99.4 % is itself a finding: at threshold 0.55 the
matcher almost never abstains on identified classes — it labels
nearly everything, and roughly half the labels are wrong.

### 2.3 Per-class precision / recall (emitted labels, mode "none")

Family level:

| Family | n true | n predicted | Precision | Recall |
|---|---|---|---|---|
| ar_a | 57 | 18 | 16.7 % | **5.3 %** |
| ar_b | 182 | 48 | 91.7 % | **24.2 %** |
| smg_compact | 73 | 242 | **28.9 %** | 95.9 % |
| sniper_bolt | 8 | 5 | 100 % | 62.5 % |
| dmr_long | 3 | 0 | — | **0 %** |
| frag_grenade | 34 | 34 | **100 %** | **100 %** |
| pistol_t | 0 | 34 | **0 %** (all 34 are false) | — |

Class level (what users see):

| Class | n true | n predicted | Precision | Recall |
|---|---|---|---|---|
| ar (ar_a+ar_b) | 244 | 66 | 95.5 % | **25.8 %** |
| smg | 73 | 242 | **28.9 %** | 95.9 % |
| sniper | 8 | 5 | 100 % | 62.5 % |
| dmr | 3 | 0 | — | **0 %** |
| frag_grenade | 34 | 34 | **100 %** | **100 %** |
| pistol | 0 | 34 | **0 %** | — |

Dominant confusions (≥3 occurrences): ar_b→smg **119**,
ar_a→smg **42**, ar_b→ar_a 13, ar_a→pistol 9, ar_b→pistol 5,
sniper→smg 3. The mechanism is visible in the scores: the small
smg_compact template (25×8 px) and the tiny pistol_t template
(15×8 px) match as **sub-windows inside larger rifle glyphs** at
NCC 0.85–0.99. Mean best score is 0.953 when right and 0.926 when
wrong — **the NCC score does not separate right from wrong answers**
on whole-entry crops, which is why §2.6 finds no usable threshold.

What works: when the matcher says **AR, it is usually right**
(95.5 % precision) — it just rarely says it. **Frag grenade** is a
distinctive round glyph and is perfect. **Sniper** is precise when
found. What fails: **AR recall, all of DMR, SMG precision, and every
pistol prediction** (34 emitted, 0 possible true pistols in the set —
pistol as currently templated is a false-positive generator against
AR and LMG glyphs).

### 2.4 Preprocess A/B (the dull-icon question)

Emitted accuracy on identified classes + unseen behaviour, per mode:

| Mode | Family acc | Class acc | Dull class acc | Clean class acc | Coverage | Unseen abstention |
|---|---|---|---|---|---|---|
| **none (shipped)** | **43.7 %** | 48.2 % | 39.4 % | 65.3 % | 99.4 % | 75.9 % |
| clahe | 39.8 % | 49.0 % | 39.8 % | 66.9 % | 98.6 % | 75.9 % |
| histeq | 41.7 % | **56.9 %** | **50.0 %** | 70.2 % | 92.4 % | **58.3 %** |
| bin | 32.8 % | 35.3 % | **26.3 %** | 52.9 % | 73.9 % | 90.7 % |

**Verdict: preprocessing does not decisively help dull icons.**

- `bin` — the shape-only hypothesis — is the **worst** mode on dull
  icons (26.3 % class), refuting the idea that binarisation rescues
  dull glyphs at 360p.
- `clahe` is a wash vs `none` everywhere.
- `histeq` genuinely raises class accuracy (+8.7 pts overall,
  +10.6 pts dull) — but it **nearly doubles confident wrong labels
  on unseen classes** (false-label rate 24.1 % → 41.7 %) and lowers
  family accuracy. Under the project's honesty rules (a confident
  wrong label is the worst failure), that trade is rejected.
- W5 therefore made **no change** to the shipped default (`none`),
  which also matches W2's independent harvest-VOD tuning.

### 2.5 Unseen classes — abstention vs false labels

Correct behaviour = abstain (`weapon = None`).

| Group | n | Abstained | Falsely labelled | False labels |
|---|---|---|---|---|
| Templated `unknown_*` truth (dome 63, wheel 10, burst 5, shotgunlike 3, diagonal 1) | 82 | **96.3 %** (79) | 3.7 % (3) | smg 1, ar 1, pistol 1 |
| **No-template classes** (lmg_box 14, bullpup 5, unknown_lmgT 4, vehicle 3) | 26 | **11.5 %** (3) | **88.5 %** (23) | pistol 18, smg 5 |
| All unseen | 108 | 75.9 % | 24.1 % | — |

Per no-template class: lmg_box → pistol **14/14**, unknown_lmgT →
pistol **4/4**, bullpup → smg **5/5**, vehicle → abstained 3/3.
The `unknown_*` competition mechanism works well where an unknown
template exists (unknown_dome wins its own entries 63/63); it cannot
help classes that have neither an identified nor an unknown
template — those glyphs' sub-parts match pistol/smg templates at
high NCC and are emitted confidently. **This is the single most
important defect found by W5**: roughly 1 in 4 unseen-class entries
— and 9 in 10 entries of classes with no template at all — receive a
confident, wrong weapon label in production today.

### 2.6 Threshold calibration (W5 decision: NO change)

- **icon_threshold:** sweeping the emission threshold on recorded
  scores changes *nothing* between 0.40 and 0.85 (family 43.7 % /
  class 48.2 % / coverage 99.4 % throughout). The minimum best-match
  score across all 465 entries is **0.719** — some template always
  correlates ≥ 0.72 somewhere in a whole entry, so the threshold is
  effectively inoperative as an abstention gate. At 0.90 class
  accuracy *falls* (45.4 %); at 0.95 coverage collapses to 63 % and
  class accuracy to 35.0 %. No threshold fixes the SMG/pistol
  sub-window problem. **0.55 kept.**
- **marker_threshold:** a subset sweep on recorded gated marker
  results (approximation — suppression not re-run) suggests knock
  precision could rise 61 % → 86 % at threshold 0.80 with recall
  91 % → 81 %, but the knob is **shared** with headshot (whose
  recall would fall 44 % → 25 % while precision stays ≈ 12 %), and
  the full-parser cross-check (§2.8) shows different absolute
  numbers than the slot replica. Not decisive on this evidence.
  **0.50 kept.** Recommended future production change (outside W5's
  calibration mandate): *separate* per-marker thresholds, tuned on
  the full parser.
- **icon_preprocess:** kept at `none` (§2.4).
- Diagnostic for future work (not an allowed W5 knob): a
  score-**margin** gate (best − second ≥ 0.15) yields 79 % class
  precision — on only 24/357 entries. Margin, template-size
  normalisation, and slot-tight crops (instead of whole-entry
  sliding) are the promising directions; all are model changes, not
  calibration.

### 2.7 Markers: knock & headshot (mode "none", threshold 0.50)

Slot-replica measurement over all 465 entries:

| Marker | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|
| Knock (all) | 216 | 137 | 22 | 90 | **61.2 %** | **90.8 %** |
| Knock (dull) | 130 | 106 | 14 | 70 | 55.1 % | 90.3 % |
| Knock (clean) | 86 | 31 | 8 | 20 | 73.5 % | 91.5 % |
| Headshot (all) | 16 | 192 | 20 | 237 | **7.7 %** | 44.4 % |
| Headshot (dull) | 10 | 133 | 12 | 165 | 7.0 % | 45.5 % |
| Headshot (clean) | 6 | 59 | 8 | 72 | 9.2 % | 42.9 % |

Knock: high recall, mediocre precision — about 2 in 5 "knock" flags
are wrong (victim logos and name fragments in the slot match the
crawling-figure template despite the scale/saturation gates).
Usable as a *hint*, not as ground truth for standings without the
Phase-24 revive/bleed-out corroboration.

Headshot: **not usable at 360p.** 192 false positives against 16
true ones; no marker threshold rescues it (best swept precision
14.7 % at 0.85, recall 13.9 %). The ring glyph cross-matches logos
and letter strokes everywhere. Any consumer should treat the
headshot flag as noise until a ≥720p or trained-model path exists;
Phase 24's spotter HEADSHOT toggle remains the only trustworthy
headshot producer.

### 2.8 End-to-end parser cross-check

Full production `_parse_entry_strip` on the same 465 strips:
**only 85/465 (18.3 %) parse at all** — the binding constraint
end-to-end is OCR name legibility at 360p (word-confidence gates
correctly refuse to guess names), not the icon matcher. On the 85
parsed entries the numbers are consistent with the matcher-level
measurement, not better: weapon class accuracy 50.8 % (identified
n=65), family 47.7 %, knock P 50.8 % / R 78.9 %, headshot P 12.1 % /
R 44.4 %. Production reality is therefore *lower-throughput and
similarly accurate* to §2.2 — the matcher numbers are not an
artefact of W5's harness.

---

## 3. Honest limits

1. **Resolution physics.** At 360p a weapon glyph is ~25–52 px wide
   but only **~7–10 px tall** (the "~7 px wide" shorthand in earlier
   briefs refers to this glyph height / sub-sampled width at lower
   renditions). Silhouette *families* are the ceiling; **model-level
   ID (M416 vs Beryl vs AKM) is physically impossible** at this
   resolution and is never attempted. Even family level is only
   ~44–53 % here.
2. **Name OCR is unreadable at 360p for most entries** — the parser
   emits nothing for 81.7 % of real held-out entries rather than
   guessing (correct behaviour, real cost).
3. **Production ≥720p is unmeasured.** All templates and all numbers
   here are 360p. Higher-resolution capture should help
   substantially (glyphs 2–4× larger), but that is a hypothesis, not
   a result — it needs its own held-out set before any claim.
4. **Headshot at 360p is noise** (§2.7). **DMR is unmeasured-broken**
   (n=3, recall 0). **Pistol predictions are all false** on this set
   (n=0 true). Small-n classes (sniper 8, dmr 3) carry wide
   uncertainty.
5. **Unseen classes get confident wrong labels** (§2.5) — the
   abstention mechanism only works where an `unknown_*` template
   exists to win.
6. **Molotov/smoke recall unproven** (no labelled world set);
   **stun and grenade-explosion are gated OFF** (§1.6) — utility
   coverage today = smoke + molotov (precision-gated) + frag-grenade
   killfeed icons. Smoke/molotov/stun *as killfeed causes of death*
   were never confirmed as distinct glyphs in any VOD; the dome
   glyph (63 held-out instances) may be the smoke canister and
   remains honestly `unknown_dome`.
7. **Grenade trajectory never fires today** (§1.7) — no paired
   position producer exists.
8. Held-out quality tags are a deliberately pessimistic operational
   definition (LAYOUT_REPORT §6); "dull" is an upper-bound bucket,
   which makes the dull numbers conservative, not flattering.

## 4. Regression & spot-check evidence (W5)

- W2 suites: `test_phase23_w2.py` (23) + `test_killfeed_phase23.py`
  (29) = **52/52 pass** — matches the W2 claim exactly. Includes
  `weapon=None` event flow and unknown-glyph events.
- W3 suites: `test_utility_detector_world.py` (16) +
  `test_utility_detector.py` (8) = **24/24 pass**; gating defaults
  verified in code *and* by instantiation (explosion False, stun
  False).
- W4 suite: `test_grenade_trajectory.py` = **31/31 pass** — matches
  the W4 claim; store enforcement re-verified independently (§1.7).
- Killfeed legacy suites: 19/19 pass.
- Full backend suite: **1197 passed, 63 failed, 8 collection
  errors**. Classification (verified by re-running the failing files
  against a clean HEAD worktree, pre-Phase-23 commit 6af3a5f):
  **zero Phase-23-caused failures.** All 13 e2e failures are
  identical at HEAD (Redis absent + constructor drift in untouched
  modules); the 44 unit/integration failures in the checked subset
  are a strict subset of the 48 at HEAD (4 since fixed); dossier
  (5) = missing `fpdf` in this environment; event-bus (1) = Redis
  absent; collection errors = `fpdf` / `httpx_ws` / missing legacy
  modules — all pre-existing or environmental. (The repo
  `pytest.ini` needs `-o addopts=""` here because the cov plugin is
  absent — environmental.)
- Frontend: `tsc --noEmit` clean, `next build` green.
- Hygiene: nothing was committed by any Phase-23 workstream (HEAD
  unchanged); Phase-23 diff scanned — **no secrets**; held-out data
  hashes identical before/after W5 measurement (read-only use):
  `labels.csv` sha256 `47fb4f6f…`, held-out tree hash unchanged.

## 5. The flywheel — from templates to a trained model

Template matching at 360p has hit its measured ceiling (~48 % class
accuracy, threshold-inoperative, sub-window confusions). The exit is
the one the data was built for:

1. **Today's labels are training data.** The harvest labels
  (Day 1/2 + PMGC 1) and this held-out set (465 entries, 15 classes,
  quality tags, knock/headshot truth) are exactly the labelled corpus
  a multi-class icon classifier needs. The matcher's *raw top-1 +
  margin* can auto-label new VOD entries for human spot-verification
  (margin ≥ 0.15 slice is 79 % precise — a useful pre-labeller, not a
  labeller).
2. **A trained classifier fixes the structural flaws**: it scores the
  whole glyph at once (no sub-window wins), learns smg-vs-AR from
  hundreds of examples instead of 2 templates, and can output a
  calibrated "unknown" via softmax margin — replacing NCC template
  matching behind the same `IconMatcher` interface and event
  contract.
3. **The discovery queue already exists.** `unknown_*` glyphs are
  the labelled unknowns: dome (63 held-out instances — harvest a
  corroborated identity), wheel, burst, shotgunlike, diagonal. The
  no-template classes measured here — **bullpup, lmg_box, vehicle,
  unknown_lmgT** — need templates/examples from *newly designated
  harvest footage* (never from the held-out set, or the eval is
  contaminated) — and §2.5 is the before/after measurement to beat.
4. **Re-measure, don't re-word.** Any successor (classifier, 720p
  templates, per-marker thresholds, margin gate) must be scored on
  this same held-out set — dull split first — before its numbers
  replace the ones in §2.

---

*W5 harness + raw results: `data/phase23_work/w5_eval.py`,
`w5_analyze.py`, `w5_weapon_results.csv`, `w5_marker_results.csv`,
`w5_parser_results.csv`, `w5_summary.json`. No production code was
changed by W5: the held-out evidence supported no calibration change
(§2.4–§2.6).*
