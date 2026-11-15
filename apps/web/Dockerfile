# syntax=docker/dockerfile:1.19
FROM oven/bun:1.4.4-alpine AS base

# Install dependencies only when needed
FROM base AS deps
RUN apk upgrade --no-cache && apk add --no-cache libc6-compat
WORKDIR /app

COPY package.json bun.lock* ./
RUN bun install --frozen-lockfile

# Rebuild the source code only when needed
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY --exclude=public/thinktogether-source.tar.gz . .

ENV NEXT_TELEMETRY_DISABLED=1

RUN rm -f .env*

# Remove Enterprise Edition folder for public/OSS builds, and drop an ee
# symlink that does not resolve inside the image (it points outside the build
# context), which would otherwise fail the build
ARG LEARNHOUSE_PUBLIC=false
RUN if [ "$LEARNHOUSE_PUBLIC" = "true" ] || { [ -L /app/ee ] && [ ! -e /app/ee ]; }; then rm -rf /app/ee; fi

# Commit sha from CI; lets Next tell deploys apart (see generateBuildId)
ARG BUILD_ID
ENV BUILD_ID=$BUILD_ID
# Allow the bundled Webpack build on hosts where Turbopack cannot finish.
ARG NEXT_BUILD_BUNDLER=turbopack
RUN --mount=type=cache,id=thinktogether-web-next-cache,sharing=locked,target=/app/.next/cache \
    if [ "$NEXT_BUILD_BUNDLER" = "webpack" ]; then NODE_OPTIONS=--max-old-space-size=3072 TT_LOW_MEMORY_BUILD=true bun run build --webpack; \
    elif [ "$NEXT_BUILD_BUNDLER" = "turbopack" ]; then bun run build; \
    else echo "Unsupported NEXT_BUILD_BUNDLER" >&2; exit 2; fi

# Production image: the Next.js standalone server runs on Bun
FROM oven/bun:1.4.4-alpine AS runner
WORKDIR /app

RUN apk upgrade --no-cache && apk add --no-cache curl

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/public ./public

RUN mkdir .next
RUN chown nextjs:nodejs .next

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

COPY --chown=nextjs:nodejs server-wrapper.js ./
RUN chmod +x server-wrapper.js

COPY --chown=nextjs:nodejs docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

RUN rm -f public/runtime-config.js && touch runtime-config.json && chown nextjs:nodejs runtime-config.json

# Corresponding source changes whenever API/worker code changes. Keep this
# artifact separate from the compiled frontend cache; ordinary upstream builds
# without a ThinkTogether bundle remain supported.
RUN --mount=type=bind,source=public,target=/source-public,ro \
    if [ -f /source-public/thinktogether-source.tar.gz ]; then \
      cp /source-public/thinktogether-source.tar.gz /app/public/thinktogether-source.tar.gz; \
      chown nextjs:nodejs /app/public/thinktogether-source.tar.gz; \
    fi

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME=0.0.0.0

ENTRYPOINT ["bun", "server-wrapper.js"]
