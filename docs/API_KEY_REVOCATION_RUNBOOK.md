# API Key Revocation Runbook — Static `ADMIN_API_KEYS` (Phase 36, N-4)

Operator procedure for revoking a static API key. There are two kinds
of credential on this server and they revoke differently — do not
confuse them:

* **Static API keys** (`ADMIN_API_KEYS` allowlist, `X-API-Key` header)
  — revoked by the procedure below.
* **JWTs** (operator login, `Authorization: Bearer`) — NOT revoked by
  this procedure. A JWT expires on its own (`ACCESS_TOKEN_EXPIRE_MINUTES`);
  a DB user's JWT is additionally killed immediately by deactivating
  the DB user (Phase 16 kill switch / Phase 32 H2 check). The
  settings-only operator credential (`ADMIN_USERNAME` /
  `ADMIN_PASSWORD`) has no DB row: disabling it means unsetting
  `ADMIN_PASSWORD`.

## What revocation does (honest semantics)

1. The key's **SHA-256 digest only** is written to the
   `revoked_api_key` table (alembic migration 018) with `revoked_at`,
   `revoked_by` and an optional `reason`. **The raw key is never
   stored, never logged, and never echoed back** in any response or
   WS frame — a digest cannot be turned back into a working key, so
   listing the table leaks nothing usable.
2. The same digest is added to the in-memory revocation registry,
   which `app/api/deps.py::_verify_api_key` — the single choke point
   for REST and WebSocket API-key auth — consults on every request.
   The key is therefore rejected (REST 401 / WS principal rejected)
   **on the very next request**, with no restart.
3. At startup the lifespan seeds the in-memory registry from the
   table, so a revoked key **stays revoked across restarts**.

Other keys and JWT auth are unaffected: revoking one static key does
not invalidate any other static key or any JWT.

## How to revoke (operator-only)

### REST (preferred)

```http
POST /api/v1/admin/api-keys/revoke
Authorization: Bearer <operator JWT>      # or X-API-Key: <another, still-valid key>
Content-Type: application/json

{"api_key": "<the raw key to revoke>", "reason": "key leaked in chat"}
```

Response (200):

```json
{
  "status": "success",
  "revoked": true,
  "was_configured": true,
  "persisted": true,
  "already_revoked": false,
  "detail": "API key revoked: rejected on the next request and persistent across restarts ..."
}
```

* `was_configured: false` — the key was not in the configured
  `ADMIN_API_KEYS` allowlist, so it was not an active key. Its digest
  is still recorded, so the key stays revoked if it is ever added to
  the allowlist later.
* `persisted: false` — **the database write failed**. The key IS
  revoked in the running process, but the revocation will NOT survive
  a restart. Treat this as an incident: fix the database, re-run the
  revoke, and (below) remove the key from `ADMIN_API_KEYS`.
* `already_revoked: true` — a row for this digest already existed.
  Re-revoking is a safe no-op.
* Missing/empty `api_key` → 400, nothing revoked. No auth → 401.

### WebSocket control channel

Send on an authenticated `/ws/live` connection (operator/admin
principal — viewers get `unauthorized`):

```json
{"action": "revoke_key", "api_key": "<the raw key to revoke>", "reason": "optional"}
```

The `action_ack` frame carries the same `revoked` /
`was_configured` / `persisted` / `already_revoked` fields. Sending
`revoke_key` with no key is an `error` ack and revokes nothing.
No frame ever contains a fabricated or real key value.

## Step 2 (still recommended): remove the key from `ADMIN_API_KEYS`

Persistent revocation survives restarts — **but a database reset
(table dropped, DB re-created, or a fresh database) would resurrect
the key**, because the revocation record lives in that database.
So, after revoking, also remove the key from the `ADMIN_API_KEYS`
environment/config source and restart at the next convenient window.
That is the only revocation that survives a DB reset. (Removing it
from config alone, without the revoke above, leaves the key valid
until the restart — do the revoke first, the config removal second.)

## Verifying a revocation

* Immediately: any request with the revoked key gets 401
  (e.g. `GET /api/v1/admin/system` with `X-API-Key: <revoked>`).
* Durably: restart the server (or watch the startup log line
  `Revoked API-key cache seeded: N digest(s) from DB`) and repeat
  the 401 check — the key must still be rejected.
* In the DB (digest only): `SELECT key_digest, revoked_at,
  revoked_by, reason FROM revoked_api_key;` — confirm the row exists
  and that no raw key value appears anywhere in it.

## Restoring a key (there is no un-revoke)

Revocation is deliberately one-way: there is no un-revoke endpoint.
To restore a key, an operator must delete its row from
`revoked_api_key` **and** restart with the key present in
`ADMIN_API_KEYS` (a restart clears the in-memory registry, and the
startup seed then finds no row). Prefer issuing a fresh key instead.

## Known limits (do not work around silently)

* The verify path is synchronous, so it reads the in-memory cache,
  not the DB, per request. A key revoked by **another process or
  instance after this process started** is not visible here until
  re-seed/restart. Single-process deployments are unaffected.
* If the DB is unreachable at startup, the seed loads nothing
  (logged as a warning) and pre-restart revocations are not enforced
  until the DB is back and the process restarts. In-memory
  revocations made during that process lifetime still apply.
* `revoke_api_key()` (sync, in-memory only) still exists in
  `app/api/deps.py` as the degraded/test primitive. It does NOT
  persist — operator tooling must call the persistent path above.
* JWTs are not covered by this runbook (see top).
