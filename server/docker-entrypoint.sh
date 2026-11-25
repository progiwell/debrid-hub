#!/bin/sh
# ==============================================================================
# Docker entrypoint wrapper for Debrid Hub
# ==============================================================================
# Configures UMASK, prepares non-root PUID/PGID ownership on /config and
# /downloads, drops root privileges via su-exec, and forwards SIGTERM/SIGINT
# cleanly so graceful container stops return exit code 0.
# ==============================================================================
set -e

# Configure default UMASK for shared folder permissions (default 002)
umask "${UMASK:-002}"

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

case "$PUID" in
    ""|*[!0-9]*)
        echo "[ERROR] Invalid PUID '${PUID}': must be a positive integer." >&2
        exit 1
        ;;
esac
if [ "$PUID" -eq 0 ]; then
    echo "[ERROR] Refusing to run with root PUID=0." >&2
    exit 1
fi

case "$PGID" in
    ""|*[!0-9]*)
        echo "[ERROR] Invalid PGID '${PGID}': must be a positive integer." >&2
        exit 1
        ;;
esac
if [ "$PGID" -eq 0 ]; then
    echo "[ERROR] Refusing to run with root PGID=0." >&2
    exit 1
fi

use_su_exec=0
if [ "$(id -u)" -eq 0 ]; then
    if ! command -v su-exec >/dev/null 2>&1; then
        echo "[ERROR] su-exec binary is required to drop root privileges." >&2
        exit 1
    fi
    CONFIG_DIR="$(dirname "${CONFIG_FILE:-/config/settings.json}")"
    DOWNLOAD_TARGET="${DOWNLOAD_DIR:-/downloads}"
    mkdir -p "$CONFIG_DIR" "$DOWNLOAD_TARGET" 2>/dev/null || true
    chown -R "${PUID}:${PGID}" "$CONFIG_DIR" 2>/dev/null || true
    chown "${PUID}:${PGID}" "$DOWNLOAD_TARGET" 2>/dev/null || true
    export HOME="/tmp"
    use_su_exec=1
fi

child_pid=0

term_handler() {
    if [ "$child_pid" -ne 0 ]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        # Bounded wait (default up to 7s, configurable via SHUTDOWN_TIMEOUT_SECONDS) ensures entrypoint exits with code 0 before Docker's 10s SIGKILL cutoff
        timeout_sec="${SHUTDOWN_TIMEOUT_SECONDS:-7}"
        max_iterations=$((timeout_sec * 10))
        wait_count=0
        while kill -0 "$child_pid" 2>/dev/null && [ "$wait_count" -lt "$max_iterations" ]; do
            sleep 0.1
            wait_count=$((wait_count + 1))
        done
        if kill -0 "$child_pid" 2>/dev/null; then
            kill -KILL "$child_pid" 2>/dev/null || true
        fi
        wait "$child_pid" 2>/dev/null || true
    fi
    exit 0
}

trap 'term_handler' TERM INT

if [ "$use_su_exec" -eq 1 ]; then
    su-exec "${PUID}:${PGID}" "$@" &
else
    "$@" &
fi
child_pid=$!

set +e
wait "$child_pid"
exit_code=$?
set -e

# If killed by SIGTERM (143 = 128+15) or SIGINT (130 = 128+2), treat as clean shutdown (exit code 0)
if [ "$exit_code" -eq 143 ] || [ "$exit_code" -eq 130 ]; then
    exit 0
fi

exit "$exit_code"
