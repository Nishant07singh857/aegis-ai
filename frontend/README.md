# AEGIS AI — Operator Frontend

Next.js 15 (App Router) + TypeScript (strict) + Tailwind CSS v4. Dark
esports-console operator dashboard that talks to the AEGIS AI backend REST
API. All data comes from the live backend — no mock data anywhere.

## Prerequisites

- Node 20+ and npm 10+ (this repo was developed with Node v24 / npm 10)
- The backend running at `http://localhost:8000` (see `../backend`)

## Setup

```sh
cd frontend
cp .env.example .env.local   # edit if your backend is not on localhost:8000
npm install
```

## Development

```sh
npm run dev
```

Serves the console at <http://localhost:3000> with hot reload. Pages proxy
nothing — every request goes directly to `NEXT_PUBLIC_API_URL`.

## Build

```sh
npm run build
```

`next.config.ts` sets `output: 'standalone'`, so the build also emits a
self-contained server at `.next/standalone` (plus `.next/static`). This is
what the production Docker image runs (`node .next/standalone/server.js`
after copying `.next/standalone` and `.next/static`).

## Environment variables

| Variable             | Purpose                                                                 |
| -------------------- | ----------------------------------------------------------------------- |
| `NEXT_PUBLIC_API_URL` | Base URL of the backend API (default `http://localhost:8000`).        |

**Important:** `NEXT_PUBLIC_` variables are inlined into the client bundle
at **build time**. For Docker builds, pass the value as a build-arg:

```sh
docker build --build-arg NEXT_PUBLIC_API_URL=http://backend:8000 .
```

## Project layout

```
app/
  layout.tsx        Dark shell: top bar (wordmark, nav, auth state) + main
  globals.css       Theme: near-black bg, cyan accent, red danger
  login/page.tsx    Operator login (username/password -> JWT)
lib/
  api.ts            apiFetch<T> typed HTTP client + ApiError + API_BASE
  auth.tsx          AuthProvider + useAuth (token in localStorage)
components/
  topbar.tsx        Wordmark, section nav, operator auth state
  ui.tsx            Card, Badge, Loading, ErrorState, EmptyState
```

Routes `/`, `/teams`, `/broadcast`, `/commentary`, `/admin` each live in
their own `app/<route>/page.tsx` and are wired to the real backend API.
