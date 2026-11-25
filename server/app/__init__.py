"""Lean Server Docker Daemon for Real-Debrid downloads."""

from __future__ import annotations

import os
import subprocess

__version__ = "0.2.0"


def _resolve_commit() -> str:
    commit = os.getenv("GIT_COMMIT", "").strip()
    if commit and commit != "dev":
        return commit[:7]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(__file__),
        )
        return out.decode().strip()
    except (OSError, subprocess.SubprocessError):
        return commit or "dev"


__commit__ = _resolve_commit()

