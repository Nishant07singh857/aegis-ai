# Roles & Access Control (Phase 6)

AEGIS AI has three control-plane roles, stored on `User.role`
(`admin` | `operator` | `viewer`, default `viewer`).

## Hierarchy

`admin` ⊇ `operator` ⊇ `viewer` — an admin can do everything an operator
can, an operator everything a viewer can.

| Role     | Can do                                                                 |
|----------|------------------------------------------------------------------------|
| viewer   | Authenticated reads: `GET /api/v1/director/state`, stream status, WS feed |
| operator | Everything viewer can, plus: director console (`POST /director/*`), stream start/stop |
| admin    | Everything operator can, plus: admin-only routes (teams write, `require_admin` endpoints) |

## Dependencies (`backend/app/api/deps.py`)

- `get_current_user` — any valid JWT or `X-API-Key`. Raises **401** when missing/invalid.
- `require_role(*roles)` — dependency factory; raises **403** when the principal's role isn't in `roles`.
- `require_admin` — `require_role("admin")`.
- `require_operator` — `require_role("admin", "operator")` (admins pass too).

The role travels in the JWT `role` claim. Tokens minted before Phase 6 have
no claim and fall back to the pre-roles rule (superuser / `admin` scope →
`admin`), so old operator tokens keep working. The static `X-API-Key`
principal resolves to `admin`, preserving Phase 1 behavior.

## First admin seeding

On startup (`lifespan` in `backend/app/main.py`), if **both** env vars are
set and **no** admin user exists in the DB, one is created:

- `ADMIN_EMAIL` — email of the first admin
- `ADMIN_PASSWORD` — its password

Details:

- The password is hashed with bcrypt (`SecurityManager.hash_password`) —
  **never stored plaintext**.
- The user gets `role="admin"`, `is_superuser=True`, `is_active=True`.
- Only the fact that seeding happened is logged — **never the credentials**.
- Seeding never crashes startup: an unreachable DB or a schema that hasn't
  applied migration `005_add_user_role` yet just logs a warning and skips.
- **No hardcoded credentials anywhere** — both values come from the
  environment.

The seeded admin logs in via `POST /api/v1/auth/token` with the email (or
derived username) + password, exactly like any DB user; the returned JWT
carries `role: "admin"`.

Apply the migration before first boot with roles:

```bash
cd backend && alembic upgrade head
```

## Changing roles later

Roles live on the `user` row — update `role` directly in the DB (or via a
future admin UI). Existing JWTs keep their old role until they expire; log
out / log back in to pick up a role change.
