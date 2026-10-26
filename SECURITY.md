# SECURITY.md — AEGIS AI

Secrets policy, incident history, and rotation runbook for this repository.

> **Standing rule:** no real secret may ever be committed to this repo.
> Local dev secrets live in `backend/.env.development` (gitignored), prod
> secrets live in the deployment environment / secret manager — never in
> `backend/.env.production` as a committed file (also gitignored).
> CORRECTION (Phase 33): despite the gitignore entries, env files ARE
> tracked from the initial commit — `git ls-files` shows
> `backend/.env.development`, `backend/.env.production`,
> `backend/.env.example`, and `frontend/.env.example` as committed.
> Only `backend/.env.example` is claimed placeholder-only; the
> `.env.development` / `.env.production` history is the incident in §1,
> whose purge is still pending a user decision (§3).

---

## 1. What was exposed

`backend/.env.development` (65 lines) was committed to this **public**
repository in the very first commit ("Initial commit without video data",
author "AI Agent", 2026-05-27) and contained real-looking credentials:

| Variable | What it protects | Assessed |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string (host, user, **password**, db) | real-looking |
| `OBS_PASSWORD` | OBS WebSocket password (broadcast control) | real-looking |
| `OPENAI_API_KEY` | OpenAI API key | real-looking, correct key format |
| `GEMINI_API_KEY` | Google Gemini API key | real-looking, correct key format |
| `ELEVENLABS_API_KEY` | ElevenLabs API key | real-looking, correct key format |

Values are never reproduced here or anywhere in the repo. The file has been
sanitized in the working tree: all five values were replaced with
`REPLACE_ME__ROTATE_IMMEDIATELY` (the `DATABASE_URL` keeps its structural
shape with a fake password). **Assume every one of the five is compromised**
— anyone who cloned the repo while they were present has them.

Also note: `backend/alembic.ini` and `backend/app/config.py` contain
hardcoded **dev-grade** database credentials (`aegis:aegis123@localhost`).
These are low-risk local-dev defaults, but any deployment that inherits them
must override `DATABASE_URL`.

## 2. Rotation steps (do these NOW, in this order)

### 2.1 Database password (`DATABASE_URL`)
1. On the database host, change the password for the `aegis` role:
   `ALTER ROLE aegis WITH PASSWORD '<new-strong-password>';`
2. Update `DATABASE_URL` in your **local, gitignored**
   `backend/.env.development` and in the production secret store.
3. Restart the app and verify connectivity; revoke old sessions if the DB
   supports it.

### 2.2 OBS password (`OBS_PASSWORD`)
1. In OBS → Tools → WebSocket Server Settings, set a new password
   (or disable auth only on a trusted local network — not recommended).
2. Update `OBS_PASSWORD` in your gitignored env file / secret store.
3. Restart the broadcast controller and confirm the WS handshake succeeds.

### 2.3 OpenAI API key (`OPENAI_API_KEY`)
1. Go to https://platform.openai.com/api-keys → **Revoke** the exposed key,
   then **Create new secret key**.
2. Put the new key in your gitignored env file / secret manager.
3. Check usage/billing for the old key for unexpected spend, then delete it.

### 2.4 Gemini API key (`GEMINI_API_KEY`)
1. Go to https://aistudio.google.com/app/apikey → **Delete** the exposed key,
   then create a replacement. (Or via Google Cloud Console → APIs & Services
   → Credentials.)
2. Update your gitignored env file / secret manager; restrict the new key
   (API restrictions + application restrictions) if possible.

### 2.5 ElevenLabs API key (`ELEVENLABS_API_KEY`)
1. Go to https://elevenlabs.io → Profile / API keys → **revoke** the exposed
   key and generate a new one.
2. Update your gitignored env file / secret manager.

## 3. Purging the secrets from git history

Replacing the values in the working tree is **not enough**: the real values
remain in git history (the single initial commit) and in any existing clone,
fork, or GitHub cache. Purge them with one of the following.

⚠️ **DO NOT run these without explicit approval** — rewriting history changes
commit hashes and requires every collaborator to re-clone. Coordinate first.

### Option A — `git filter-repo` (recommended, faster)

```bash
# Install: pip install git-filter-repo  (or via your package manager)
cd /path/to/aegis-ai

# 1. Back up first
git clone --mirror . /tmp/aegis-ai-backup.git

# 2. Remove the secret-bearing file from ALL history
git filter-repo --path backend/.env.development --invert-paths

# 3. Inspect the result, then force-push (single remote, coordinated)
git log --oneline | head
git push origin --force --all
git push origin --force --tags
```

### Option B — `git filter-branch` (built-in, slower)

```bash
cd /path/to/aegis-ai
git clone --mirror . /tmp/aegis-ai-backup.git

git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch backend/.env.development' \
  --prune-empty --tag-name-filter cat -- --all

rm -rf .git/refs/original/
git reflog expire --expire=now --all
git gc --prune=now --aggressive

git push origin --force --all
git push origin --force --tags
```

After either option:
1. Rotate the keys **again** if they were not rotated before the purge
   (assume exposure until rotation is confirmed).
2. Ask GitHub Support to purge cached views if the repo is public
   (secret scanning / "remove sensitive data" request).
3. Tell every collaborator to **re-clone** — old clones still contain the
   secrets.

## 4. New secrets policy (enforced going forward)

1. **Never commit real secrets.** `backend/.env.development`,
   `backend/.env.production`, and `.env` are gitignored (see `.gitignore`) —
   but gitignore does not untrack files already committed, so the §1 files
   remain tracked until the §3 purge is executed (user decision).
2. **`.env.example` files are the only env files that should be committed** — placeholders only.
   CI/review should reject any real-looking value in them. (The previous
   wording here — "`.env.example` is the only committed env file" — was
   false: see the header correction.)
3. **New settings that hold secrets** (`ADMIN_PASSWORD`, `ADMIN_API_KEYS`,
   `FERNET_KEY`, `SECRET_KEY`) must come from environment / secret manager.
   `FERNET_KEY` is required in production — the server refuses to boot
   without it, because an ephemeral key would silently destroy encrypted data
   on every restart.
4. **Rotate on suspected exposure.** Treat any committed secret as
   compromised: rotate first (Section 2), purge history second (Section 3).
5. **Production boot requirements** (to be completed): refuse to start in
   production with the default `SECRET_KEY` or default/empty
   `ADMIN_PASSWORD`.

## 5. Auth model (Phase 1 description — SUPERSEDED in part, see note)

> CORRECTION (Phase 33): the sentence below claiming "There is no `User`
> table yet" is stale since Phase 13/16. A `User` table DOES exist
> (`backend/app/db/models/user.py:14`, `class User`), and REST + WebSocket
> auth now checks the DB user's live `is_active` flag on every request /
> action frame (`backend/app/api/deps.py`, Phase 32 kill-switch). The
> settings-based operator account remains only as a non-DB fallback
> (`deps.py` notes that account is NOT a DB user). The Phase 1 mechanism
> described below is therefore history, not the current auth model;
> consult `deps.py` for the endpoint map.

There is no `User` table yet, so protected endpoints authenticate against
settings (see `backend/app/api/deps.py` for the full endpoint map):

- `POST /api/v1/auth/token` with `ADMIN_USERNAME`/`ADMIN_PASSWORD` →
  JWT (`Authorization: Bearer <token>`), HS256, 30-minute expiry.
- `X-API-Key` header with a key from `ADMIN_API_KEYS` (static allowlist,
  dev/demo grade — a hashed DB store replaces this in Phase 2).
- WebSocket control channel (`/ws/live`): pass `?token=<jwt-or-api-key>`.
  Passive subscriptions stay public; every mutating action requires auth.

Public read endpoints (match state, standings, kill feed, health) remain open
for the demo dashboard; all mutating, admin, and broadcast-control endpoints
require auth.
