# Director Service (Phase 7)

The director service (`backend/app/services/director_service.py`) is the
**single source of truth for what's on the broadcast output page**. The
operator console writes to it, the AI writes to it in full-auto mode, the
AI *proposes suggestions* to it in semi-auto mode, and the output page
renders it.

## State

```jsonc
{
  "mode": "full_auto",               // "full_auto" | "semi_auto" | "manual"
  "scene": "main",                   // "main" | "standings" | "killfeed_focus" | "commentary"
  "overlays": {"scorebug": true, "killfeed": true},
  "lower_third": {"visible": false, "title": "", "subtitle": ""},
  "updated_by": "operator",          // username, or "director_ai:<team>" / "system"
  "updated_at": "2026-10-18T00:00:00+00:00"
}
```

## Modes

- **FULL_AUTO** (default): only the system — the match simulator /
  Director AI in `app/main.py` — may write, via `system_publish()`. The
  AI acts directly: it never creates suggestions in this mode. Operator
  scene/overlay/lower-third writes are **rejected**.
- **SEMI_AUTO**: the AI **never writes and never acts**. It may propose
  *suggestions* via `propose_suggestion()`; each proposal lands in a
  pending queue that an operator approves or rejects through the API. No
  suggestion ever auto-applies. Operator direct writes are still
  rejected in this mode — the approval queue is the operator's control
  surface here.
- **MANUAL**: operator API writes are authoritative. `system_publish()`
  and `propose_suggestion()` are both **no-ops**, so the AI is fully
  silent.
- Transitions are **explicit**, via `POST /director/mode`. The legacy
  Phase 6 value `"auto"` is accepted and **normalised to `"full_auto"`**
  — the string `"auto"` is never stored.

## Suggestions (SEMI_AUTO only)

A suggestion is:

```jsonc
{
  "id": "9f3c…",                     // uuid4 hex
  "type": "scene",                   // "scene" | "overlay" | "lower_third"
  "payload": {"scene": "killfeed_focus"},
                                     // or {"overlay": "scorebug", "visible": false}
                                     // or {"visible": true, "title": "…", "subtitle": "…"}
  "reason": "Fight detected — X knocks Y; killfeed focus shows the action.",
  "confidence": 0.8,                 // 0.0–1.0
  "created_at": "2026-10-18T00:00:00+00:00",
  "expires_at": "2026-10-18T00:01:00+00:00",  // default: created_at + 60s
  "status": "pending"                // "pending" | "applied" | "rejected" | "expired"
}
```

Lifecycle:

1. **Propose** — only while the mode is `semi_auto` (a silent no-op in
   `full_auto` and `manual`). Duplicates of a still-pending identical
   suggestion and suggestions that would be a no-op against the current
   state are skipped, so an idle AI leaves the queue **genuinely empty**
   — nothing is fabricated.
2. **Approve** (`POST /director/suggestions/{id}/approve`, operator+) —
   the payload is applied **exactly as if the operator had issued it
   manually** (same mutation path as the operator commands), then the
   suggestion is marked `applied` and removed from the queue. Rejected
   with 409 in `full_auto` (the AI is driving there — switch modes
   first).
3. **Reject** (`POST /director/suggestions/{id}/reject`, operator+) —
   marked `rejected`, logged (decided_by/decided_at), dropped from the
   queue.
4. **Expiry** — dropped silently. Pruned lazily on every read and on
   every propose (the janitor); decided/expired records are kept in a
   capped in-memory history for audit.

Current genuine suggestion sources (all derived from real match events,
never invented): a fight event from the match simulator proposes a
`killfeed_focus` scene suggestion, with confidence derived from the
Director AI's event priority (CRITICAL 0.9 / HIGH 0.75 / MEDIUM 0.6).

## API (`/api/v1/director`, tag `Director`)

| Method | Path                              | Auth            | Notes                              |
|--------|-----------------------------------|-----------------|------------------------------------|
| GET    | `/state`                          | any logged-in   | viewers included                   |
| GET    | `/suggestions`                    | operator+       | pending AI proposals               |
| POST   | `/mode`                           | operator+       | `{"mode": "full_auto"\|"semi_auto"\|"manual"}`; legacy `"auto"` → `"full_auto"` |
| POST   | `/scene`                          | operator+       | manual only                        |
| POST   | `/overlays`                       | operator+       | manual only, partial update        |
| POST   | `/lower-third`                    | operator+       | manual only                        |
| POST   | `/suggestions/{id}/approve`       | operator+       | applies payload like a manual write; 404 unknown/decided/expired; 409 in full_auto |
| POST   | `/suggestions/{id}/reject`        | operator+       | marks rejected, logged, dropped; 404 unknown/decided/expired |

## Status-code choices

- **422** — invalid values (e.g. `{"mode": "turbo"}`). Pydantic rejects
  these with a clear message.
- **404** — a suggestion id that is unknown, already decided, or expired.
- **409 Conflict** — a *valid* operator write while the director is not
  in **MANUAL** mode, or a valid suggestion approval while the director
  is in **FULL_AUTO** mode. 409 was chosen over 422 because the request
  itself is well-formed; it conflicts with the current director mode.
- **401** unauthenticated, **403** insufficient role (viewer hitting a
  POST endpoint or GET /suggestions).

## WebSocket

Every director change publishes on the existing `/ws/live` broadcast path:

```jsonc
{"type": "director.state", "data": { /* full director state */ }}
```

Every suggestion event (created / approved / rejected) publishes:

```jsonc
{"type": "director.suggestion", "data": { /* suggestion, incl. final status */ }}
```

The service holds listener callbacks; `app/main.py` registers
`ConnectionManager.broadcast` at startup (no circular import). New WS
connections also receive the current state in the initial burst. The
director console seeds its suggestion queue from `GET /suggestions` and
reconciles on reconnect (a missed frame never leaves a stale queue).

## FULL_AUTO behaviour (honest, no fake content)

When the Director AI approves a POV switch, the simulator calls
`system_publish(scene="main", updated_by="director_ai:<team_id>")`. The
main program feed therefore follows the AI's camera decisions; the legacy
`director_state` / `scene_switch` WS messages still carry the POV detail.

In SEMI_AUTO the same simulator tick proposes a `killfeed_focus`
suggestion instead of writing, and only when the fight event is real.

Deliberately **not** invented:

- The `standings`, `killfeed_focus`, and `commentary` scenes carry no
  fabricated AI content — in full-auto the AI only ever publishes
  `scene="main"`; the other scenes are operator-selected (manual) or
  AI-proposed (semi-auto) on genuine events.
- `system_publish` broadcasts only on a real visible change (no WS spam
  from no-op simulator ticks).
- Suggestions are never fabricated: duplicates and no-ops are skipped,
  so the queue is empty when the AI has nothing genuine to propose.
