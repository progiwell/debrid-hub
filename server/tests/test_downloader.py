"""Tests for Aria2Downloader command generation and execution."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.downloader import Aria2Downloader


def test_build_command_defaults() -> None:
    downloader = Aria2Downloader(download_dir="/downloads", max_connections=16, split=16)
    cmd = downloader.build_command("https://cdn.real-debrid.com/file.nsz")
    assert cmd[0] == "aria2c"
    assert "--max-connection-per-server=16" in cmd
    assert "--split=16" in cmd
    assert "--dir" in cmd
    assert "/downloads" in cmd
    assert cmd[-1] == "https://cdn.real-debrid.com/file.nsz"


def test_build_command_custom_filename() -> None:
    downloader = Aria2Downloader(download_dir="/downloads")
    cmd = downloader.build_command(
        "https://cdn.real-debrid.com/file.nsz",
        output_dir="/downloads/switch",
        filename="game.nsz",
    )
    assert "/downloads/switch" in cmd
    assert "--out" in cmd
    assert "game.nsz" in cmd


@pytest.mark.asyncio
async def test_download_success_with_derived_filename(tmp_path) -> None:
    target_dir = str(tmp_path / "downloads")
    downloader = Aria2Downloader(download_dir=target_dir)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"download completed", b"")

    url = "https://cdn.real-debrid.com/d/abc12345/Super%20Game%20(USA).zip"
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result_path = await downloader.download(url)

        assert os.path.exists(target_dir)
        expected_path = os.path.join(target_dir, "Super Game (USA).zip")
        assert result_path == expected_path

        mock_exec.assert_called_once()
        cmd_args = mock_exec.call_args[0]
        assert cmd_args[0] == "aria2c"
        assert "--out" in cmd_args
        assert "Super Game (USA).zip" in cmd_args
        assert cmd_args[-1] == url


@pytest.mark.asyncio
async def test_download_success_with_explicit_filename(tmp_path) -> None:
    target_dir = str(tmp_path / "custom")
    downloader = Aria2Downloader(download_dir="/downloads")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"ok", b"")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result_path = await downloader.download(
            "https://cdn.real-debrid.com/file.mkv",
            output_dir=target_dir,
            filename="custom_name.mkv",
        )
        assert result_path == os.path.join(target_dir, "custom_name.mkv")
        assert os.path.exists(target_dir)


@pytest.mark.asyncio
async def test_download_failure_raises_runtime_error(tmp_path) -> None:
    downloader = Aria2Downloader(download_dir=str(tmp_path))

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate.return_value = (b"", b"Resource not found (404)")

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(RuntimeError, match="aria2c download failed with code 1: Resource not found"),
    ):
        await downloader.download("https://cdn.real-debrid.com/missing.iso")


def test_build_command_sanitizes_path_traversal() -> None:
    downloader = Aria2Downloader(download_dir="/downloads")
    cmd = downloader.build_command(
        "https://cdn.real-debrid.com/file.zip",
        filename="../../../etc/cron.d/evil.sh",
    )
    assert "--out" in cmd
    idx = cmd.index("--out")
    assert cmd[idx + 1] == "evil.sh"


@pytest.mark.asyncio
async def test_download_sanitizes_path_traversal(tmp_path) -> None:
    target_dir = str(tmp_path / "downloads")
    downloader = Aria2Downloader(download_dir=target_dir)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"ok", b"")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result_path = await downloader.download(
            "https://cdn.real-debrid.com/payload.sh",
            output_dir=target_dir,
            filename="../../etc/cron.d/evil.sh",
        )
        assert result_path == os.path.join(target_dir, "evil.sh")
        cmd_args = mock_exec.call_args[0]
        assert "--out" in cmd_args
        idx = cmd_args.index("--out")
        assert cmd_args[idx + 1] == "evil.sh"


@pytest.mark.asyncio
async def test_download_cancellation_terminates_proc(tmp_path) -> None:
    downloader = Aria2Downloader(download_dir=str(tmp_path))

    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = asyncio.CancelledError()
    mock_proc.terminate = MagicMock()
    mock_proc.wait = AsyncMock()

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(asyncio.CancelledError),
    ):
        await downloader.download("https://cdn.real-debrid.com/file.iso")

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once()


@pytest.mark.asyncio
async def test_download_streams_progress_lines(tmp_path) -> None:
    downloader = Aria2Downloader(download_dir=str(tmp_path))

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock()

    # Simulate aria2c stdout stream with progress lines
    stream_lines = [
        b"[#1 1.0MiB/10MiB(10%) CN:16 DL:1.0MiB]\n",
        b"[#1 5.0MiB/10MiB(50%) CN:16 DL:2.0MiB]\n",
        b"[#1 10MiB/10MiB(100%) CN:16 DL:1.5MiB]\n",
        b"",
    ]
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline.side_effect = stream_lines
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read.return_value = b""

    reported_progress: list[float] = []

    def on_progress(pct: float) -> None:
        reported_progress.append(pct)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await downloader.download(
            "https://cdn.real-debrid.com/movie.mkv",
            on_progress=on_progress,
        )

    assert reported_progress == [10.0, 50.0, 100.0]


@pytest.mark.asyncio
async def test_download_streams_progress_lines_with_speed(tmp_path) -> None:
    downloader = Aria2Downloader(download_dir=str(tmp_path))

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock()

    stream_lines = [
        b"[#1 1.0MiB/10MiB(10%) CN:16 DL:1.2MiB]\n",
        b"[#1 5.0MiB/10MiB(50%) CN:16 DL:5.8MiB/s]\n",
        b"",
    ]
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline.side_effect = stream_lines
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read.return_value = b""

    reported_tuples: list[tuple[float, str | None]] = []

    def on_progress(pct: float, speed: str | None = None) -> None:
        reported_tuples.append((pct, speed))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await downloader.download(
            "https://cdn.real-debrid.com/speed_test.mkv",
            on_progress=on_progress,
        )

    assert reported_tuples == [(10.0, "1.2MiB/s"), (50.0, "5.8MiB/s")]

