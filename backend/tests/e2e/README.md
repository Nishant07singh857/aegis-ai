# ⚠️ tests/e2e — SCRIPTED DEMOS, NOT END-TO-END VERIFICATION

**Nothing in this directory verifies the production pipeline.** Each file is
a *scripted demo*: it wires mocks and in-memory components together, feeds
them events it invents itself, and (where it asserts at all) asserts on the
test's own scripted state. A green run here means "the demo script ran",
never "the real broadcast/match/highlight pipeline works". Do not cite
these files as e2e coverage — see `docs/FULL_PROJECT_INSPECTION.md` §5
("All 3 e2e files are scripted theater").

Per file:

- `test_broadcast_pipeline.py` — The director "decision" under test is a
  dict literal invented inside the test itself (the code comment admits:
  "In production, this is handled by the actual agent"). OBS and the
  overlay server are mocks; only the mock-backed `SceneManager`'s scene
  bookkeeping is asserted. The dormant `app.broadcast` / `app.overlays`
  packages it exercises have zero production callers (inspection H8).
- `test_full_match_flow.py` — Phases 2–6 contain **zero assertions**; each
  phase builds a fresh `EventBus`/`AgentOrchestrator` state, so no state
  flows between phases and the "complete match flow" is fictional. Only
  phase 1 (orchestrator initialisation) and phase 7 (points/leaderboard/
  report arithmetic on hand-fed results) assert anything.
- `test_highlight_generation.py` —
  `test_detect_all_highlight_types` warns instead of failing when a
  detection is missed, and `test_content_publishing_pipeline` has **zero
  assertions** (it only prints; `ContentPublisher` never publishes anywhere
  — inspection §5). The agents it drives are dormant in production (H5).

What real verification exists lives in the unit/integration suites for
Phases 23–29 (measured vision harnesses, LiveDirector, replay contract,
16-feed monitor) — not here. Rebuilding e2e around one real pipeline path
is tracked in the inspection's prioritized fix list (§8, item 12).
