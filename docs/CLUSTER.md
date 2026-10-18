# AEGIS AI — Distributed Cluster

**Phase 7 / W3.** Run the backend as a real multi-node cluster: split work
across machines (API, AI workers, streamer), keep every node visible in one
live view, and degrade honestly when Redis goes down.

No paid APIs involved. No new secrets: the registry stores only operational
metadata (node id, hostname, roles, version, agent count).

---

## How it works

Every backend process registers itself on startup in Redis:

| Redis key | `aegis:nodes:{node_id}` (hash) |
|---|---|
| `hostname` | machine hostname |
| `roles` | comma-separated, from `NODE_ROLES` |
| `started_at` | process start, ISO-8601 UTC |
| `version` | `GIT_SHA` env, else `AEGIS_VERSION` env, else app `VERSION` ("1.0.0") |
| `agent_count` | agents **this** node reports as running (same source as the Control Room *Agents* card), refreshed on every beat |
| `heartbeat_ts` | epoch seconds of the last beat |

A background asyncio task heartbeats every **15s** and refreshes the key's
**45s TTL**. A dead node vanishes from the view automatically — no leader,
no manual cleanup.

**Node identity:** `node_id` is a uuid4 hex minted **per process start**
(override with `NODE_ID` for a stable id, e.g. in compose). Per-process ids
are deliberate: N uvicorn workers on one host show up as N distinct nodes
instead of clobbering one shared key.

**Status thresholds** (computed from `heartbeat_age_s = now − heartbeat_ts`):

- `healthy` — < 30s (green)
- `degraded` — 30–45s (amber)
- `stale` — > 45s (red; normally impossible because the TTL is 45s, but computed anyway)

---

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `NODE_ROLES` | `api,ai-worker` | Roles this node serves. Valid: `api`, `ai-worker`, `streamer`. Unknown values are dropped with a warning. |
| `NODE_ID` | uuid4 hex / process | Stable node identity override. |
| `REDIS_URL` | `redis://localhost:6379/0` | **Must be identical on every node** — this is what makes it one cluster. |
| `DATABASE_URL` | `postgresql+asyncpg://…` | Should also be shared (same match state everywhere). |
| `GIT_SHA` / `AEGIS_VERSION` | — | Optional; shown as the node `version`. |

---

## Run distributed — bare metal (two machines)

Both machines must reach the **same** Redis and Postgres (open the Redis
port on the Redis host, or run Redis/Postgres on Machine A and point
Machine B at them).

**Machine A — API + AI workers:**

```bash
export REDIS_URL='redis://<machine-a-ip>:6379/0'
export DATABASE_URL='postgresql+asyncpg://aegis:<password>@<machine-a-ip>:5432/aegis'
export NODE_ROLES='api,ai-worker'
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Machine B — streamer node:**

```bash
export REDIS_URL='redis://<machine-a-ip>:6379/0'
export DATABASE_URL='postgresql+asyncpg://aegis:<password>@<machine-a-ip>:5432/aegis'
export NODE_ROLES='streamer'
export NODE_ID='machine-b-streamer'   # stable identity, survives restarts
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Point the frontend at either node (`NEXT_PUBLIC_API_URL=http://<machine-a-ip>:8000`).

**Load distribution:** roles are advisory labels today — they tell the
operator *what each box is for* and gate nothing. The honest split:

- `api` nodes serve the REST/WS control plane (put a reverse proxy like the
  bundled nginx in front, round-robin across them).
- `ai-worker` nodes are where you run the heavy agent loops; add machines
  with GPUs here.
- `streamer` nodes are the encoding boxes (pair with the automated streamer
  container — see `docs/STREAMING.md` and
  `infrastructure/docker/docker-compose.streaming.yml`).

JWTs are stateless (HS256, shared `SECRET_KEY`), so any node accepts any
login — no sticky sessions needed for auth. Set the **same** `SECRET_KEY`
(and `FERNET_KEY`) on every node or logins/API keys won't validate across
machines.

---

## Run distributed — docker compose (single host, multi-node)

On top of the dev stack, this spins two extra nodes (extra API node on
`:8001`, streamer node on `:8002`), all sharing the base stack's redis +
postgres:

```bash
docker compose -f infrastructure/docker/docker-compose.dev.yml \
               -f infrastructure/docker/docker-compose.cluster.yml \
               --profile cluster up -d --build
```

The override follows the same pattern as
`docker-compose.streaming.yml`: it changes nothing in the base file, sets
explicit `NODE_ID`s per service, and wires every node to the same
`REDIS_URL`/`DATABASE_URL`.

---

## Verify

**API (any authenticated user — viewer included):**

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/token \
  -d 'username=admin&password=<your-admin-password>' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/cluster/nodes | python3 -m json.tool
```

Expected: one entry per running node, each with `status: "healthy"`,
`roles`, `agent_count`, `heartbeat_age_s`. Never any invented nodes.

**Control Room UI:** open `http://localhost:3000/control` (operator/admin)
and look at the **"Cluster — sare nodes connected rahe"** card: every
connected node listed with role badges, agent count, heartbeat age and a
health dot (green <30s, amber 30–45s, red stale/unreachable). It polls every
15s, same cadence as the backend heartbeat. A 401 on the fetch (expired JWT)
logs you out via the existing session-expiry handler.

---

## Graceful degradation (Redis down)

If Redis is unreachable — at startup or mid-run — the node **keeps
serving** and logs a clear warning:

```
Redis unreachable — node registry running STANDALONE; cluster view will show only this node
```

`GET /api/v1/cluster/nodes` then returns a single honest entry for *this*
process, flagged `"standalone": true` (amber badge in the UI). When Redis
comes back, the next heartbeat re-registers and the full cluster view
returns. Dead nodes never linger: their keys expire 45s after the last
heartbeat, and a clean shutdown deletes the key immediately.

---

## API reference

`GET /api/v1/cluster/nodes` — auth: any authenticated user. Response:

```json
[
  {
    "node_id": "9f2c…",
    "hostname": "aegis-api-1",
    "roles": ["api", "ai-worker"],
    "started_at": "2026-10-18T06:10:00+00:00",
    "version": "1.0.0",
    "heartbeat_age_s": 4.2,
    "status": "healthy",
    "agent_count": 5,
    "standalone": false
  }
]
```

401 when unauthenticated. No secrets anywhere in the payload.

---

## Files

- `backend/app/cluster/registry.py` — registration, heartbeat loop, standalone fallback
- `backend/app/api/v1/cluster/` — router + schemas (`GET /cluster/nodes`)
- `backend/app/main.py` — lifespan hooks (`node_registry.start/stop`), router mount, `_active_agent_count` provider
- `frontend/app/control/page.tsx` — `ClusterCard` (15s polling, untouched other cards)
- `infrastructure/docker/docker-compose.cluster.yml` — multi-node overlay for the dev stack
- `backend/tests/unit/test_cluster.py` — 18 tests (registry lifecycle, thresholds, standalone, API auth)
