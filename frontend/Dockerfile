# syntax=docker/dockerfile:1
# =============================================================================
# AEGIS AI — frontend production image (multi-stage)
#
# IMPORTANT ASSUMPTION: frontend/ is a Next.js App Router application built
# with `output: 'standalone'` in next.config.js, so `npm run build` produces
# .next/standalone/server.js. If the sibling frontend build does not enable
# standalone output, the runner stage below must change to `npm start`.
#
# Build context is the REPO ROOT:
#   docker build -f frontend/Dockerfile \
#       --build-arg NEXT_PUBLIC_API_URL=https://api.example.com \
#       -t aegis-frontend:prod .
# or via infrastructure/docker/docker-compose.prod.yml
#
# - node:20-alpine pinned throughout (no :latest)
# - Runtime runs as non-root user `nextjs` (uid 10001)
# - Healthcheck uses busybox `wget`, which ships in node:20-alpine
# =============================================================================

ARG NODE_IMAGE=node:20-alpine

# ------------------------------------------------------------------ deps -----
FROM ${NODE_IMAGE} AS deps

WORKDIR /app

# Lockfile-only copy for dependency layer caching. If the project uses a
# different lockfile, update this line accordingly.
COPY frontend/package.json frontend/package-lock.json* ./

RUN npm ci

# ---------------------------------------------------------------- builder -----
FROM ${NODE_IMAGE} AS builder

ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./

# Baked into the client bundle at build time; overridable per environment.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN npm run build

# ----------------------------------------------------------------- runner -----
FROM ${NODE_IMAGE} AS runner

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

RUN addgroup --system --gid 10001 nextjs \
    && adduser --system --uid 10001 --ingroup nextjs nextjs

COPY --from=builder --chown=nextjs:nextjs /app/public ./public
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:3000/ > /dev/null || exit 1

CMD ["node", "server.js"]
