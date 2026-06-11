# syntax=docker/dockerfile:1.7
#
# Frontend container for the Inspira Vite + React app.
#
# Multi-stage: stage 1 compiles the Vite bundle with npm on Node 20. Stage 2
# serves the built static assets with nginx:alpine and falls back to
# index.html for SPA routes (reactflow / client-side state keep us all in a
# single HTML entry).
#
# Build context is the REPO ROOT (not app/) so the build can reach
# ../docs/legal/*.md — LegalPage.tsx imports those via Vite's ?raw suffix:
#   docker build -f app/Dockerfile \
#     --build-arg VITE_INSPIRA_API_URL=http://localhost:4174 \
#     -t inspira-frontend .
#
# VITE_INSPIRA_API_URL is an env var Vite inlines at build time — it baked
# into the bundle, so you need a new image for every distinct backend URL.

# ---------- Stage 1: build ----------
FROM node:20-alpine AS build

WORKDIR /app

# Copy manifest + lockfile first for install-layer caching. If only source
# files change, the install layer is reused.
COPY app/package.json app/package-lock.json ./

# `npm ci` is the reproducible-install equivalent of `npm install`: it
# refuses to run if package.json and package-lock.json disagree, and it
# wipes node_modules before installing. Keeps CI and local image builds
# byte-identical.
RUN npm ci

# Now bring in the rest of the app.
COPY app/tsconfig.json app/vite.config.ts app/index.html ./
COPY app/src ./src
# Legal markdown sources live outside app/ and are imported by
# src/features/marketing/LegalPage.tsx via relative ?raw imports.
# Mirror the repo layout inside the image so those imports resolve.
COPY docs/legal /docs/legal

# Build-arg Vite reads via import.meta.env.VITE_INSPIRA_API_URL. Default
# points at the local compose backend so a no-arg build still produces a
# runnable image for localhost testing. Override in CI / prod.
ARG VITE_INSPIRA_API_URL=http://localhost:4174
ENV VITE_INSPIRA_API_URL=${VITE_INSPIRA_API_URL}

RUN npm run build


# ---------- Stage 2: serve ----------
FROM nginx:alpine AS runtime

# Replace the default server block with SPA-aware config.
RUN rm /etc/nginx/conf.d/default.conf
COPY app/nginx.conf /etc/nginx/conf.d/default.conf

# Copy the built bundle into nginx's public root.
COPY --from=build /app/dist /usr/share/nginx/html

# Run nginx as the stock `nginx` user (already present in the base image).
# We adjust ownership of the dirs nginx writes to so it can run without root
# privileges on the static assets. The base image's master still starts as
# root because binding :80 requires it, but the worker processes drop.
RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
