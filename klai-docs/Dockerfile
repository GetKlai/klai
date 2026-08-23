FROM node:24-alpine AS base

# Install dependencies only when needed
FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
# Ensure musl-specific native binaries are present for Alpine
RUN npm rebuild lightningcss 2>/dev/null || true

# Build
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# Production image
FROM base AS runner

# Apply security patches to base image OS packages. node:24-alpine ships
# apk packages with fixed CVEs and the Trivy gate blocks the deploy on them.
RUN apk update && apk upgrade --no-cache

WORKDIR /app
ENV NODE_ENV=production

# npm is removed, not upgraded. The runtime is `node server.js` on a Next
# standalone build — npm is never invoked here, but its bundled dependency
# tree is scanned, and that tree is where CVE-2026-14257 / CVE-2026-69152
# (brace-expansion) and CVE-2026-69192 (ip-address) live today. Pinning
# npm@11.18.0 cleared the previous round of exactly these advisories and then
# became the thing carrying the next round. Deleting what we do not run ends
# the treadmill instead of pacing it, same reasoning as the gnupg purge in
# klai-scribe/whisper-server/Dockerfile.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3010
ENV PORT=3010
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
