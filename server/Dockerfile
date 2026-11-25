# Stage 1: Build Frontend Static Assets (TypeScript + Tailwind CSS)
FROM --platform=$BUILDPLATFORM node:22-alpine AS ui-builder

WORKDIR /src
COPY package.json package-lock.json tsconfig.json ./
COPY scripts/ ./scripts/
COPY extension/src/types.ts ./extension/src/types.ts
COPY server/app/static/src/ ./server/app/static/src/
COPY server/app/templates/settings.html ./server/app/templates/settings.html

RUN npm ci && npm run build:server

# Stage 2: Lean Python + Aria2c + Unrar Runtime
FROM python:3.11-alpine

LABEL maintainer="Progiwell"
LABEL description="Lean Real-Debrid Server Daemon for Debrid Hub (FastAPI + Aria2c + Unrar/7-Zip)"
LABEL org.opencontainers.image.source="https://github.com/progiwell/debrid-hub"
LABEL org.opencontainers.image.licenses="GPL-3.0-or-later"

# Install lightweight runtime utilities: aria2c, 7zip, and official unrar
RUN apk upgrade --no-cache \
    && apk add --no-cache \
    aria2 \
    7zip \
    ca-certificates \
    tzdata \
    libstdc++ \
    && apk add --no-cache --virtual .build-deps build-base wget \
    && wget -qO- https://www.rarlab.com/rar/unrarsrc-7.1.5.tar.gz | tar -xzv -C /tmp \
    && make -C /tmp/unrar -f makefile -j$(nproc) \
    && install -m 755 /tmp/unrar/unrar /usr/bin/unrar \
    && rm -rf /tmp/unrar \
    && apk del .build-deps

WORKDIR /app

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y setuptools wheel

COPY server/app/ ./app/
COPY --from=ui-builder /src/server/app/static/dashboard.js ./app/static/dashboard.js
COPY --from=ui-builder /src/server/app/static/dashboard.css ./app/static/dashboard.css
COPY server/docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Create default download and configuration directories
RUN mkdir -p /downloads /config

ARG GIT_COMMIT=dev

ENV PYTHONUNBUFFERED=1 \
    DOWNLOAD_DIR=/downloads \
    CONFIG_FILE=/config/settings.json \
    HOST=0.0.0.0 \
    PORT=8080 \
    UMASK=002 \
    GIT_COMMIT=${GIT_COMMIT}

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "5"]
