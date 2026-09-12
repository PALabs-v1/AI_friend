# STAGE 1: Builder
FROM node:22-alpine AS builder

# Check https://github.com/nodejs/docker-node/tree/b4117f9333da4138b03a546ec926ef50a31506c3#nodealpine to understand why libc6-compat might be needed.
RUN apk add --no-cache libc6-compat
WORKDIR /app

# Install dependencies first for caching
COPY package*.json ./
RUN npm ci

# Copy code and build
COPY . .

# Disable telemetry during build
ENV NEXT_TELEMETRY_DISABLED=1

# NEXT_PUBLIC_* vars are inlined into the JS bundle at build time, not read
# at container start -- LIVEKIT_URL and BACKEND_ACCESS_KEY need the same
# treatment NEXT_PUBLIC_BACKEND_URL already got, or every deployment but the
# unauthenticated localhost default silently ships a frontend pointed at the
# wrong LiveKit URL / missing the access key `useWebRTCVoice.js` and
# `lib/api.js` both need against a non-loopback backend (roadmap Phase 5.4).
ARG NEXT_PUBLIC_BACKEND_URL
ARG NEXT_PUBLIC_LIVEKIT_URL
ARG NEXT_PUBLIC_BACKEND_ACCESS_KEY
ENV NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL
ENV NEXT_PUBLIC_LIVEKIT_URL=$NEXT_PUBLIC_LIVEKIT_URL
ENV NEXT_PUBLIC_BACKEND_ACCESS_KEY=$NEXT_PUBLIC_BACKEND_ACCESS_KEY

RUN npm run build

# STAGE 2: Runtime
FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Create non-root user
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nextjs -u 1001

# Copy only necessary artifacts from builder
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# Metadata
LABEL org.opencontainers.image.source="https://github.com/PALabs-v1/AI_friend"
LABEL org.opencontainers.image.description="AI Friend Frontend - Real-time Voice Interface"
LABEL org.opencontainers.image.version="2.3.0"

CMD ["node", "server.js"]
