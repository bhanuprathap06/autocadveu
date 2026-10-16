# syntax=docker/dockerfile:1.7
# AutoForge web application (Next.js standalone server).
ARG NODE_IMAGE=mirror.gcr.io/library/node:22-bookworm-slim

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# Corporate TLS proxy support: optional CA passed as a BuildKit secret (not kept in the image).
RUN --mount=type=secret,id=extra_ca,required=false \
    NODE_EXTRA_CA_CERTS=$([ -s /run/secrets/extra_ca ] && echo /run/secrets/extra_ca) \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --no-audit --no-fund

FROM deps AS build
COPY . .
# Server-side rewrite target for /api/v1 (used when the reverse proxy does not route /api itself) and the optional
# public WebSocket origin. Both are compiled into the build.
ARG AF_API_URL=http://api:8000
ARG NEXT_PUBLIC_WS_URL=
ENV AF_API_URL=${AF_API_URL} NEXT_PUBLIC_WS_URL=${NEXT_PUBLIC_WS_URL} NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 CMD node -e "fetch('http://127.0.0.1:3000/status').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
