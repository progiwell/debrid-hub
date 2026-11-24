#!/usr/bin/env bash
# ==============================================================================
# Debrid Hub - Clean Deployment & Upgrade Script
# ==============================================================================
# Gracefully stops the container before pulling and recreating to ensure an
# exit code of 0. This prevents Docker host monitors and UI managers from
# emitting false-positive abnormal container exit alerts.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

SERVICE_NAME="debrid-hub"

# Resolve Docker Compose CLI (support 'docker compose' or legacy 'docker-compose')
if docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    DOCKER_COMPOSE=(docker-compose)
else
    echo "[ERROR] Neither 'docker compose' nor 'docker-compose' was found on PATH." >&2
    exit 1
fi

echo "========================================================"
echo " Starting Debrid Hub Graceful Deployment"
echo "========================================================"

# Step 1: Graceful stop
echo "[1/3] Gracefully stopping ${SERVICE_NAME} container..."
"${DOCKER_COMPOSE[@]}" stop "${SERVICE_NAME}" || true

# Step 2: Pull latest image
echo "[2/3] Pulling latest image..."
"${DOCKER_COMPOSE[@]}" pull "${SERVICE_NAME}"

# Step 3: Recreate container
echo "[3/3] Launching updated container in detached mode..."
"${DOCKER_COMPOSE[@]}" up -d "${SERVICE_NAME}"

echo "========================================================"
echo " Deployment completed successfully!"
echo " Container status:"
echo "========================================================"
"${DOCKER_COMPOSE[@]}" ps
