# syntax=docker.io/docker/dockerfile:1

FROM node:20-alpine AS base

# Certificado corporativo Enagás/Netskope (proxy SSL)
# El archivo se pasa desde el contexto adicional repo_root (raiz del monorepo)
# En dev: additional_contexts.repo_root = .. (docker-compose.dev.yml)
# En CI:  --build-context repo_root=.
COPY --from=repo_root netskope-ca-bundle.crt /tmp/corp-root.crt
# Inyectar el cert corporativo en el bundle del sistema SIN necesitar red ni apk.
# Alpine tiene /etc/ssl/certs/ca-certificates.crt en el base image de node:20-alpine.
# Append directo → disponible para todo proceso de red posterior (apk, pip, node...).
RUN cat /tmp/corp-root.crt >> /etc/ssl/certs/ca-certificates.crt && \
    mkdir -p /usr/local/share/ca-certificates && \
    cp /tmp/corp-root.crt /usr/local/share/ca-certificates/corp-root.crt
# NODE_EXTRA_CA_CERTS debe apuntar SOLO al cert corporativo adicional.
# Node.js lo suma al bundle Mozilla integrado — no sustituye el bundle completo.
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/corp-root.crt

# Install dependencies only when needed
FROM base AS deps
# Check https://github.com/nodejs/docker-node/tree/b4117f9333da4138b03a546ec926ef50a31506c3#nodealpine to understand why libc6-compat might be needed.
RUN apk add --no-cache libc6-compat
WORKDIR /app

# Install pnpm
RUN corepack enable pnpm

# Copy package files
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* .npmrc* ./

# First copy the config file and content to avoid fumadocs-mdx postinstall error
COPY source.config.ts ./
COPY content ./content

# Install dependencies.
# --no-frozen-lockfile: el pnpm-lock.yaml se generó en Windows; los binarios
# nativos de @vercel/turbopack-next son distintos por plataforma y pnpm no
# puede instalarlos desde un lockfile de otra plataforma con --frozen-lockfile.
RUN pnpm i --no-frozen-lockfile


# Rebuild the source code only when needed
FROM base AS builder
WORKDIR /app

# libc6-compat es necesario para cargar los binarios nativos (.node) de
# @vercel/turbopack-next que vienen en node_modules copiados del stage deps.
# Sin esto, Node no puede cargar el módulo y next build falla con Module not found.
RUN apk add --no-cache libc6-compat git

# Enable pnpm
RUN corepack enable pnpm

# Build with placeholder values for NEXT_PUBLIC_* variables.
# These are replaced at container startup by docker-entrypoint.js
# with real values from the container's environment variables.
ARG NEXT_PUBLIC_FASTAPI_BACKEND_URL=__NEXT_PUBLIC_FASTAPI_BACKEND_URL__
ARG NEXT_PUBLIC_FASTAPI_BACKEND_AUTH_TYPE=__NEXT_PUBLIC_FASTAPI_BACKEND_AUTH_TYPE__
ARG NEXT_PUBLIC_ETL_SERVICE=__NEXT_PUBLIC_ETL_SERVICE__
ARG NEXT_PUBLIC_ZERO_CACHE_URL=__NEXT_PUBLIC_ZERO_CACHE_URL__
ARG NEXT_PUBLIC_DEPLOYMENT_MODE=__NEXT_PUBLIC_DEPLOYMENT_MODE__

ENV NEXT_PUBLIC_FASTAPI_BACKEND_URL=$NEXT_PUBLIC_FASTAPI_BACKEND_URL
ENV NEXT_PUBLIC_FASTAPI_BACKEND_AUTH_TYPE=$NEXT_PUBLIC_FASTAPI_BACKEND_AUTH_TYPE
ENV NEXT_PUBLIC_ETL_SERVICE=$NEXT_PUBLIC_ETL_SERVICE
ENV NEXT_PUBLIC_ZERO_CACHE_URL=$NEXT_PUBLIC_ZERO_CACHE_URL
ENV NEXT_PUBLIC_DEPLOYMENT_MODE=$NEXT_PUBLIC_DEPLOYMENT_MODE

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Next.js collects completely anonymous telemetry data about general usage.
ENV NEXT_TELEMETRY_DISABLED=1

# NODE_EXTRA_CA_CERTS: para el runtime Node.js (webpack, etc.)
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/corp-root.crt
# NEXT_TURBOPACK_EXPERIMENTAL_USE_SYSTEM_TLS_CERTS: Turbopack tiene su propia
# pila TLS y no usa NODE_EXTRA_CA_CERTS. Esta variable le dice que use el bundle
# del sistema (/etc/ssl/certs/ca-certificates.crt) donde ya inyectamos el cert
# corporativo de Netskope en el stage base.
ENV NEXT_TURBOPACK_EXPERIMENTAL_USE_SYSTEM_TLS_CERTS=1

# Next.js 16 usa Turbopack por defecto para `next build`.
# @vercel/turbopack-next no resuelve sus módulos internos en Alpine musl.
# Forzamos webpack con --no-turbopack.
RUN pnpm exec next build --no-turbopack

# Production image, copy all the files and run next
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
# Uncomment the following line in case you want to disable telemetry during runtime.
# ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public

# Automatically leverage output traces to reduce image size
# https://nextjs.org/docs/advanced-features/output-file-tracing
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone/app/ ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Entrypoint scripts for runtime env var substitution
COPY --chown=nextjs:nodejs docker-entrypoint.js ./docker-entrypoint.js
COPY --chown=nextjs:nodejs --chmod=755 docker-entrypoint.sh ./docker-entrypoint.sh

USER nextjs

EXPOSE 3000

ENV PORT=3000

# server.js is created by next build from the standalone output
# https://nextjs.org/docs/pages/api-reference/config/next-config-js/output
ENV HOSTNAME="0.0.0.0"
ENTRYPOINT ["/bin/sh", "./docker-entrypoint.sh"]