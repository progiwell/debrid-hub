"""Aria2c multi-threaded download runner for server storage."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "ftp", "ftps"})


def _validate_download_url(url: str) -> str:
    """Validate that a download URL uses a safe network scheme and cannot inject CLI flags."""
    cleaned = (url or "").strip()
    if not cleaned or cleaned.startswith("-") or any(ch in cleaned for ch in ("\x00", "\r", "\n")):
        raise ValueError(f"Unsafe or unsupported download URL: {url!r}")
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise ValueError(f"Unsafe or unsupported download URL: {url!r}")
    return cleaned


def _sanitize_filename(filename: str | None) -> str | None:
    """Strip path traversal, control bytes, and leading hyphens from output filenames."""
    if not filename:
        return None
    cleaned = filename.replace("\x00", "").replace("\r", "").replace("\n", "")
    name = Path(cleaned).name
    if name in ("", ".", ".."):
        return None
    if name.startswith("-"):
        name = f"_{name}"
    return name


class Aria2Downloader:
    """Wrapper around `aria2c` CLI / process execution for parallel chunked downloads."""

    def __init__(
        self,
        download_dir: str = "/downloads",
        max_connections: int = 16,
        split: int = 16,
    ) -> None:
        self.download_dir = download_dir
        self.max_connections = max_connections
        self.split = split

    def build_command(
        self,
        url: str,
        output_dir: str | None = None,
        filename: str | None = None,
    ) -> list[str]:
        """Construct the aria2c CLI command list for a single CDN download."""
        safe_url = _validate_download_url(url)
        target_dir = output_dir or self.download_dir
        if target_dir.startswith("-"):
            target_dir = f"./{target_dir}"

        cmd = [
            "aria2c",
            f"--max-connection-per-server={self.max_connections}",
            f"--split={self.split}",
            "--min-split-size=1M",
            "--continue=true",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--summary-interval=1",
            "--dir",
            target_dir,
        ]
        safe_filename = _sanitize_filename(filename)
        if safe_filename:
            cmd.extend(["--out", safe_filename])
        cmd.extend(["--", safe_url])
        return cmd

    async def download(
        self,
        url: str,
        output_dir: str | None = None,
        filename: str | None = None,
        on_progress: Callable[..., Any] | None = None,
    ) -> str:
        """Execute aria2c to download the unrestricted CDN URL to the target directory."""
        safe_url = _validate_download_url(url)
        target_dir = output_dir or self.download_dir
        os.makedirs(target_dir, exist_ok=True)

        target_filename = _sanitize_filename(filename)
        if not target_filename:
            extracted_name = Path(urlparse(safe_url).path).name
            if extracted_name:
                target_filename = _sanitize_filename(unquote(extracted_name))

        cmd = self.build_command(safe_url, output_dir=target_dir, filename=target_filename)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_text = ""
        stderr_text = ""
        try:
            if on_progress and proc.stdout is not None:
                stdout_lines: list[str] = []
                while True:
                    line_bytes = await proc.stdout.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode(errors="replace")
                    stdout_lines.append(line)
                    match = re.search(r"\((\d{1,3}(?:\.\d+)?)%\)", line)
                    if match:
                        with contextlib.suppress(Exception):
                            val = float(match.group(1))
                            speed_match = re.search(r"DL:([^\s\]]+)", line)
                            speed_str: str | None = None
                            if speed_match:
                                raw_speed = speed_match.group(1).strip()
                                speed_str = raw_speed if raw_speed.endswith("/s") else f"{raw_speed}/s"
                            try:
                                cb_res = on_progress(val, speed_str)
                            except TypeError:
                                cb_res = on_progress(val)
                            if asyncio.iscoroutine(cb_res):
                                await cb_res
                stdout_text = "".join(stdout_lines)
                if proc.stderr is not None:
                    stderr_bytes = await proc.stderr.read()
                    stderr_text = stderr_bytes.decode(errors="replace")
                await proc.wait()
            else:
                stdout_bytes, stderr_bytes = await proc.communicate()
                stdout_text = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
                stderr_text = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                proc.terminate()
                await proc.wait()
            raise

        if proc.returncode != 0:
            err_msg = stderr_text.strip() or stdout_text.strip() or f"exit code {proc.returncode}"
            raise RuntimeError(f"aria2c download failed with code {proc.returncode}: {err_msg}")

        return os.path.join(target_dir, target_filename) if target_filename else target_dir
