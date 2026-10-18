# Director Service (Phase 6)

The director service (`backend/app/services/director_service.py`) is the
**single source of truth for what's on the broadcast output page**. The
operator console writes to it, the AI writes to it in auto mode, and the
output page renders it.

## State

```jsonc
{
  "mode": "auto",                    // "auto" | "manual"
  "scene": "main",                   // "main" | "standings" | "killfeed_focus" | "commentary"
  "overlays": {"scorebug": true, "killfeed": true},
  "lower_third": {"visible": false, "title": "", "subtitle": ""},
  "updated_by": "operator",          // username, or "director_ai:<team>" / "system"
  "updated_at": "2026-10-18T00:00:00+00:00"
}
```

## Modes

- **AUTO** (default): only the system — the match simulator / Director AI
  in `app/main.py` — may write, via `system_publish()`. Operator
  scene/overlay/lower-third writes are **rejected**.
- **MANUAL**: operator API writes are authoritative. `system_publish()` is
  a **no-op**, so the simulator/AI can never override the operator.
- Transitions auto↔manual are **explicit**, via `POST /director/mode`.

## API (`/api/v1/director`, tag `Director`)

| Method | Path              | Auth            | Notes                              |
|--------|-------------------|-----------------|------------------------------------|
| GET    | `/state`          | any logged-in   | viewers included                   |
| POST   | `/mode`           | operator+       | `{"mode": "auto"\|"manual"}`       |
| POST   | `/scene`          | operator+       | manual only                        |
| POST   | `/overlays`       | operator+       | manual only, partial update        |
| POST   | `/lower-third`    | operator+       | manual only                        |

## Status-code choices

- **422** — invalid values (e.g. `{"mode": "turbo"}`). Pydantic enums
  reject these with a clear message.
- **409 Conflict** — a *valid* operator write while the director is in
  **AUTO** mode. 409 was chosen over 422 because the request itself is
  well-formed; it conflicts with the current director mode. The response
  tells the caller to flip to manual first:
  `POST /api/v1/director/mode {"mode": "manual"}`.
- **401** unauthenticated, **403** insufficient role (viewer hitting a
  POST endpoint).

## WebSocket

Every director change publishes on the existing `/ws/live` broadcast path:

```jsonc
{"type": "director.state", "data": { /* full director state */ }}
```

The service holds listener callbacks; `app/main.py` registers
`ConnectionManager.broadcast` at startup (no circular import). New WS
connections also receive the current state in the initial burst.

## AUTO-mode behavior (honest, no fake content)

When the Director AI approves a POV switch, the simulator calls
`system_publish(scene="main", updated_by="director_ai:<team_id>")`. The
main program feed therefore follows the AI's camera decisions; the legacy
`director_state` / `scene_switch` WS messages still carry the POV detail.

Deliberately **not** invented:

- The `standings`, `killfeed_focus`, and `commentary` scenes are
  operator-selected in MANUAL mode. The AI does not fabricate scene
  content for them — their data stays empty and the frontend shows its
  honest standby state until an operator drives them.
- `system_publish` broadcasts only on a real visible change (no WS spam
  from no-op simulator ticks).
