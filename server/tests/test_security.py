"""Application security tests: archive slip, symlink escape, arg injection, and route auth."""

from __future__ import annotations

import stat
import struct
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from app.config import Settings, get_settings
from app.db import JobStore, reset_job_store
from app.downloader import Aria2Downloader
from app.extractor import RAR4_MAGIC, RAR5_MAGIC, ArchiveExtractor
from app.main import JOBS, app
from app.schemas import DownloadJob, JobStatus, LinkType
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def _encode_vint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _rar5_block(head_type: int, head_flags: int, body_tail: bytes, extra_data: bytes = b"") -> bytes:
    flags = head_flags | (0x0002 if extra_data else 0)
    body = _encode_vint(head_type) + _encode_vint(flags)
    if extra_data:
        body += _encode_vint(len(extra_data))
    body += body_tail
    return b"\x00\x00\x00\x00" + _encode_vint(len(body)) + body + extra_data


def _craft_rar4_with_member(member_name: str, *, host_os: int = 3, attr: int = 0o100644) -> bytes:
    chunks = [RAR4_MAGIC]
    chunks.append(struct.pack("<HBHH", 0, 0x73, 0, 13) + b"\x00" * 6)
    name_bytes = member_name.encode("utf-8")
    payload = b"abc"
    remaining = bytearray(25)
    struct.pack_into("<II", remaining, 0, len(payload), len(payload))
    remaining[8] = host_os
    remaining[17] = 29
    remaining[18] = 0x30
    struct.pack_into("<H", remaining, 19, len(name_bytes))
    struct.pack_into("<I", remaining, 21, attr)
    file_body = bytes(remaining) + name_bytes
    chunks.append(struct.pack("<HBHH", 0, 0x74, 0, 7 + len(file_body)) + file_body + payload)
    chunks.append(struct.pack("<HBHH", 0, 0x7B, 0, 7))
    return b"".join(chunks)


def _craft_rar5_with_member(member_name: str, *, host_os: int = 1, attr: int = 0o100644) -> bytes:
    chunks = [RAR5_MAGIC, _rar5_block(1, 0, _encode_vint(0))]
    name_bytes = member_name.encode("utf-8")
    body_tail = (
        _encode_vint(0)  # file_flags
        + _encode_vint(3)  # unpacked_size
        + _encode_vint(attr)  # attributes
        + _encode_vint(0)  # comp_info
        + _encode_vint(host_os)  # host_os
        + _encode_vint(len(name_bytes))
        + name_bytes
    )
    chunks.append(_rar5_block(2, 0, body_tail, extra_data=b"abc"))
    chunks.append(_rar5_block(5, 0, _encode_vint(0)))
    return b"".join(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("malicious_member", ["../../evil.sh", "..\\..\\evil.sh", "/etc/passwd", "sub/../../escape.txt"])
async def test_zip_slip_blocked_before_subprocess(tmp_path: Path, malicious_member: str) -> None:
    archive_path = tmp_path / "slip.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr(malicious_member, "pwned")

    dest_dir = tmp_path / "extract_target"
    extractor = ArchiveExtractor(tool="7z")
    with (
        patch("asyncio.create_subprocess_exec") as mock_exec,
        pytest.raises(ValueError, match="Unsafe path traversal entry in zip archive"),
    ):
        await extractor.extract_and_cleanup(archive_path, dest_dir)

    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_zip_symlink_entry_blocked_before_subprocess(tmp_path: Path) -> None:
    archive_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        info = zipfile.ZipInfo("escape_link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")

    dest_dir = tmp_path / "extract_target"
    extractor = ArchiveExtractor(tool="7z")
    with (
        patch("asyncio.create_subprocess_exec") as mock_exec,
        pytest.raises(ValueError, match="Unsafe symlink entry in zip archive"),
    ):
        await extractor.extract_and_cleanup(archive_path, dest_dir)

    mock_exec.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("malicious_member", ["../../evil.sh", "..\\..\\evil.sh", "/etc/cron.d/evil"])
async def test_rar4_and_rar5_slip_blocked_before_subprocess(tmp_path: Path, malicious_member: str) -> None:
    dest_dir = tmp_path / "extract_target"
    extractor = ArchiveExtractor(tool="unrar")

    for idx, payload in enumerate(
        (_craft_rar4_with_member(malicious_member), _craft_rar5_with_member(malicious_member))
    ):
        rar_path = tmp_path / f"slip_{idx}.rar"
        rar_path.write_bytes(payload)
        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            pytest.raises(ValueError, match="Unsafe path traversal entry in RAR"),
        ):
            await extractor.extract_and_cleanup(rar_path, dest_dir)
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_rar_symlink_entry_blocked_before_subprocess(tmp_path: Path) -> None:
    dest_dir = tmp_path / "extract_target"
    extractor = ArchiveExtractor(tool="unrar")

    rar4_sym = tmp_path / "sym4.rar"
    rar4_sym.write_bytes(_craft_rar4_with_member("bad_link", host_os=3, attr=stat.S_IFLNK | 0o777))
    with (
        patch("asyncio.create_subprocess_exec") as mock_exec,
        pytest.raises(ValueError, match="Unsafe symlink entry in RAR archive"),
    ):
        await extractor.extract_and_cleanup(rar4_sym, dest_dir)
    mock_exec.assert_not_called()

    rar5_sym = tmp_path / "sym5.rar"
    rar5_sym.write_bytes(_craft_rar5_with_member("bad_link", host_os=1, attr=stat.S_IFLNK | 0o777))
    with (
        patch("asyncio.create_subprocess_exec") as mock_exec,
        pytest.raises(ValueError, match="Unsafe symlink entry in RAR5 archive"),
    ):
        await extractor.extract_and_cleanup(rar5_sym, dest_dir)
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_post_extraction_escaping_symlink_removed_and_rejected(tmp_path: Path) -> None:
    host_secret = tmp_path / "host_secret.txt"
    host_secret.write_text("top-secret")

    archive_path = tmp_path / "clean.rar"
    archive_path.write_bytes(_craft_rar5_with_member("legit.txt"))
    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir()

    mock_proc = AsyncMock()
    mock_proc.returncode = 0

    async def fake_communicate():
        (dest_dir / "escape_link").symlink_to(host_secret)
        return (b"All OK", b"")

    mock_proc.communicate.side_effect = fake_communicate

    extractor = ArchiveExtractor(cleanup_after_extract=True, tool="unrar")
    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(ValueError, match="Unsafe symlink escaping target directory removed"),
    ):
        await extractor.extract_and_cleanup(archive_path, dest_dir)

    assert not (dest_dir / "escape_link").exists()
    assert not (dest_dir / "escape_link").is_symlink()
    assert host_secret.read_text() == "top-secret"
    assert archive_path.exists()


@pytest.mark.parametrize(
    "malicious_url",
    [
        "--on-download-complete=/bin/sh",
        "-x 16 https://example.com/file.zip",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://example.com/file.zip\n--on-download-complete=/bin/sh",
        "",
    ],
)
def test_aria2_rejects_unsafe_urls(malicious_url: str) -> None:
    downloader = Aria2Downloader(download_dir="/downloads")
    with pytest.raises(ValueError, match="Unsafe or unsupported download URL"):
        downloader.build_command(malicious_url)


def test_aria2_prevents_filename_and_url_flag_injection() -> None:
    downloader = Aria2Downloader(download_dir="/downloads")
    cmd = downloader.build_command(
        "https://cdn.example.com/file.bin",
        filename="--on-download-complete=evil.sh",
    )
    out_idx = cmd.index("--out")
    assert not cmd[out_idx + 1].startswith("-")
    assert cmd[-2] == "--"
    assert cmd[-1] == "https://cdn.example.com/file.bin"


def test_extractor_prevents_flag_injection_for_unrar_and_7z() -> None:
    unrar_cmd = ArchiveExtractor(tool="unrar").build_extract_command(
        Path("--on-download-complete=evil.rar"),
        "/downloads/",
    )
    assert "--" in unrar_cmd
    dash_idx = unrar_cmd.index("--")
    assert unrar_cmd[dash_idx + 1].startswith("./--")

    seven_z_cmd = ArchiveExtractor(tool="7z").build_extract_command(
        Path("-evil.7z"),
        "/downloads/",
    )
    assert "--" in seven_z_cmd
    dash_idx_7z = seven_z_cmd.index("--")
    assert seven_z_cmd[dash_idx_7z + 1].startswith("./-")


def test_all_non_public_routes_enforce_auth_when_api_key_configured(tmp_path: Path) -> None:
    store = JobStore(db_path=tmp_path / "auth_introspection.db")
    reset_job_store(store)
    JOBS.clear()
    custom_settings = Settings(api_auth_key="test-secret-auth-key")
    app.dependency_overrides[get_settings] = lambda: custom_settings

    public_routes = {
        ("GET", "/health"),
        ("GET", "/"),
        ("GET", "/settings"),
        ("GET", "/dashboard"),
        ("POST", "/api/v2/auth/login"),
        ("POST", "/api/v2/auth/logout"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
    }

    sample_torrent = b"d4:infod4:name4:test12:piece lengthi16384e6:pieces20:12345678901234567890ee"
    client = TestClient(app)

    def iter_api_routes(routes: list, prefix: str = "") -> list[tuple[str, set[str]]]:
        found: list[tuple[str, set[str]]] = []
        for r in routes:
            if isinstance(r, APIRoute):
                found.append((f"{prefix}{r.path}", set(r.methods or set())))
            elif hasattr(r, "original_router"):
                inc_prefix = getattr(getattr(r, "include_context", None), "prefix", "")
                found.extend(iter_api_routes(r.original_router.routes, f"{prefix}{inc_prefix}"))
        return found

    try:
        checked_routes = 0
        for route_path, methods in iter_api_routes(app.routes):
            for method in sorted(methods - {"HEAD", "OPTIONS"}):
                if (method, route_path) in public_routes:
                    continue

                path = route_path.replace("{job_id}", "test-job-id")
                kwargs: dict = {}
                if route_path == "/api/download":
                    kwargs["json"] = {"url": "https://example.com/file.zip"}
                elif route_path == "/api/settings":
                    if method == "PUT":
                        kwargs["json"] = {}
                elif route_path == "/api/settings/test-debrid":
                    kwargs["json"] = {}
                elif route_path == "/api/upload/torrent":
                    kwargs["files"] = {"file": ("test.torrent", sample_torrent, "application/x-bittorrent")}
                elif route_path in ("/api/v2/torrents/delete", "/api/v2/torrents/setCategory"):
                    kwargs["data"] = {"hashes": "abc"}
                elif route_path in ("/api/v2/torrents/properties", "/api/v2/torrents/files"):
                    kwargs["params"] = {"hash": "abc"}

                resp = client.request(method, path, **kwargs)
                assert resp.status_code in (401, 403), (
                    f"Route {method} {route_path} returned {resp.status_code} without auth!"
                )
                checked_routes += 1

        assert checked_routes >= 24
    finally:
        app.dependency_overrides.clear()
        store.close()
        reset_job_store(None)
        JOBS.clear()


def test_qb_delete_files_blocks_path_traversal(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("must-survive")

    store = JobStore(db_path=tmp_path / "qb_del.db")
    reset_job_store(store)
    JOBS.clear()
    job = DownloadJob(
        job_id="job-traversal",
        source_url="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        link_type=LinkType.MAGNET,
        status=JobStatus.COMPLETED,
        output_dir=str(downloads),
        filenames=["../outside.txt"],
        info_hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    store.upsert_job(job)

    try:
        client = TestClient(app)
        res = client.post(
            "/api/v2/torrents/delete",
            data={"hashes": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "deleteFiles": "true"},
        )
        assert res.status_code == 200
        assert outside_file.exists()
        assert outside_file.read_text() == "must-survive"
    finally:
        store.close()
        reset_job_store(None)
        JOBS.clear()
