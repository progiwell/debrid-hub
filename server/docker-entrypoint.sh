#!/bin/sh
# ==============================================================================
# Docker entrypoint wrapper for Debrid Hub
# ==============================================================================
# Sets the default UMASK for shared folder permissions and handles SIGTERM
# and SIGINT cleanly so that graceful container stops return exit code 0,
# preventing false-positive container manager abnormal exit alerts.
# ==============================================================================
set -e

# Configure default UMASK for shared folder permissions (default 002)
umask "${UMASK:-002}"

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

"$@" &
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
