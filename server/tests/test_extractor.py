"""Tests for ArchiveExtractor detection, split-archive cleanup, and unrar/7z execution."""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.extractor import RAR4_MAGIC, RAR5_MAGIC, ArchiveExtractor


def _encode_vint(value: int) -> bytes:
    """Encode an unsigned integer into RAR5 vint format."""
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
    """Build a minimal RAR5 block with CRC placeholder, header size, body, and optional data."""
    flags = head_flags | (0x0002 if extra_data else 0)
    body = _encode_vint(head_type) + _encode_vint(flags)
    if extra_data:
        body += _encode_vint(len(extra_data))
    body += body_tail
    return b"\x00\x00\x00\x00" + _encode_vint(len(body)) + body + extra_data


def _build_rar5_volume(
    *,
    is_volume: bool = False,
    volume_number: int | None = None,
    split_after: bool = False,
    endarc_next_volume: bool = False,
    encrypted_headers: bool = False,
    include_endarc: bool = True,
) -> bytes:
    """Construct minimal valid RAR5 binary bytes for header inspection tests."""
    chunks = [RAR5_MAGIC]
    if encrypted_headers:
        chunks.append(_rar5_block(4, 0, b"\x00\x00"))
        return b"".join(chunks)

    archive_flags = (0x0001 if is_volume else 0) | (0x0002 if volume_number is not None else 0)
    main_tail = _encode_vint(archive_flags)
    if volume_number is not None:
        main_tail += _encode_vint(volume_number)
    chunks.append(_rar5_block(1, 0, main_tail))

    file_flags = 0x0010 if split_after else 0
    chunks.append(_rar5_block(2, file_flags, b"\x00\x00", extra_data=b"abc"))

    if include_endarc:
        endarc_flags = 0x0001 if endarc_next_volume else 0
        chunks.append(_rar5_block(5, 0, _encode_vint(endarc_flags)))
    return b"".join(chunks)


def _build_rar4_volume(
    *,
    is_volume: bool = False,
    split_after: bool = False,
    endarc_next_volume: bool = False,
    encrypted_headers: bool = False,
    lhd_large: bool = False,
    high_pack_size: int = 0,
    payload: bytes = b"abc",
) -> bytes:
    """Construct minimal valid RAR4 binary bytes for header inspection tests."""
    chunks = [RAR4_MAGIC]
    main_flags = (0x0001 if is_volume else 0) | (0x0080 if encrypted_headers else 0)
    chunks.append(struct.pack("<HBHH", 0, 0x73, main_flags, 13) + b"\x00" * 6)
    if encrypted_headers:
        return b"".join(chunks)

    file_flags = (0x0002 if split_after else 0) | (0x0100 if lhd_large else 0)
    remaining = bytearray(29 if lhd_large else 4)
    struct.pack_into("<I", remaining, 0, len(payload))
    if lhd_large:
        struct.pack_into("<I", remaining, 25, high_pack_size)
    file_head_size = 7 + len(remaining)
    chunks.append(struct.pack("<HBHH", 0, 0x74, file_flags, file_head_size) + bytes(remaining) + payload)
    end_flags = 0x0001 if endarc_next_volume else 0
    chunks.append(struct.pack("<HBHH", 0, 0x7B, end_flags, 7))
    return b"".join(chunks)


def test_is_primary_rar() -> None:
    extractor = ArchiveExtractor()
    assert extractor.is_primary_rar(Path("archive.rar")) is True
    assert extractor.is_primary_rar(Path("game.part1.rar")) is True
    assert extractor.is_primary_rar(Path("game.part01.rar")) is True
    assert extractor.is_primary_rar(Path("game.part001.rar")) is True
    assert extractor.is_primary_rar(Path("game.part02.rar")) is False
    assert extractor.is_primary_rar(Path("game.part10.rar")) is False
    assert extractor.is_primary_rar(Path("game.nsz")) is False


def test_is_extractable_archive() -> None:
    extractor = ArchiveExtractor()
    assert extractor.is_extractable_archive(Path("archive.rar")) is True
    assert extractor.is_extractable_archive(Path("game.part01.rar")) is True
    assert extractor.is_extractable_archive(Path("package.zip")) is True
    assert extractor.is_extractable_archive(Path("bundle.7z")) is True
    assert extractor.is_extractable_archive(Path("game.part02.rar")) is False
    assert extractor.is_extractable_archive(Path("game.nsp")) is False


def test_tool_property_detection() -> None:
    # Explicit tool takes precedence
    extractor = ArchiveExtractor(tool="7z")
    assert extractor.tool == "7z"

    # When unrar is found on PATH
    with patch("shutil.which", side_effect=lambda binary: "/usr/bin/unrar" if binary == "unrar" else None):
        ext_unrar = ArchiveExtractor()
        assert ext_unrar.tool == "unrar"

    # When unrar is missing but 7z is found on PATH
    with patch("shutil.which", side_effect=lambda binary: "/usr/bin/7z" if binary == "7z" else None):
        ext_7z = ArchiveExtractor()
        assert ext_7z.tool == "7z"

    # Fallback when neither is found
    with patch("shutil.which", return_value=None):
        ext_fallback = ArchiveExtractor()
        assert ext_fallback.tool == "unrar"


def test_find_related_volumes_multipart(tmp_path: Path) -> None:
    extractor = ArchiveExtractor()
    part1 = tmp_path / "game.part01.rar"
    part2 = tmp_path / "game.part02.rar"
    part3 = tmp_path / "game.part03.rar"
    unrelated = tmp_path / "other.part01.rar"
    for f in (part1, part2, part3, unrelated):
        f.write_text("data")

    related = extractor.find_related_volumes(part1)
    assert related == [part1, part2, part3]


def test_find_related_volumes_old_style(tmp_path: Path) -> None:
    extractor = ArchiveExtractor()
    rar = tmp_path / "movie.rar"
    r00 = tmp_path / "movie.r00"
    r01 = tmp_path / "movie.r01"
    rar.write_text("data")
    r00.write_text("data")
    r01.write_text("data")

    related = extractor.find_related_volumes(rar)
    assert sorted(related) == sorted([rar, r00, r01])


def test_find_related_volumes_zip_and_7z(tmp_path: Path) -> None:
    extractor = ArchiveExtractor()
    zip_file = tmp_path / "package.zip"
    seven_z = tmp_path / "package.7z"
    zip_file.write_text("data")
    seven_z.write_text("data")

    assert extractor.find_related_volumes(zip_file) == [zip_file]
    assert extractor.find_related_volumes(seven_z) == [seven_z]


@pytest.mark.asyncio
async def test_extract_and_cleanup_single_rar(tmp_path: Path) -> None:
    archive_dir = tmp_path / "downloads"
    archive_dir.mkdir()
    rar_file = archive_dir / "single.rar"
    rar_file.write_text("content")

    dest_dir = tmp_path / "extracted"

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"All OK", b"")

    extractor = ArchiveExtractor(cleanup_after_extract=True, tool="unrar")
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await extractor.extract_and_cleanup(rar_file, dest_dir)
        mock_exec.assert_called_once()
        assert mock_exec.call_args.kwargs.get("stdin") == asyncio.subprocess.DEVNULL
        cmd_args = mock_exec.call_args[0]
        assert cmd_args[0] == "unrar"
        assert cmd_args[1:5] == ("x", "-o+", "-y", "--")
        assert cmd_args[5] == str(rar_file)
        assert cmd_args[6].endswith("/")

        # Archive file should have been cleaned up
        assert not rar_file.exists()
        assert isinstance(result, list)


@pytest.mark.asyncio
async def test_extract_and_cleanup_with_7z(tmp_path: Path) -> None:
    archive_dir = tmp_path / "downloads"
    archive_dir.mkdir()
    archive_file = archive_dir / "game.7z"
    archive_file.write_text("content")

    dest_dir = tmp_path / "extracted"

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"Everything is Ok", b"")

    extractor = ArchiveExtractor(cleanup_after_extract=True, tool="7z")
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await extractor.extract_and_cleanup(archive_file, dest_dir)
        mock_exec.assert_called_once()
        assert mock_exec.call_args.kwargs.get("stdin") == asyncio.subprocess.DEVNULL
        cmd_args = mock_exec.call_args[0]
        assert cmd_args[0] == "7z"
        assert cmd_args[1:3] == ("x", "-y")
        assert cmd_args[3].startswith("-o")
        assert cmd_args[4] == "--"
        assert cmd_args[5] == str(archive_file)

        assert not archive_file.exists()
        assert isinstance(result, list)


@pytest.mark.asyncio
async def test_extract_and_cleanup_multipart_rar(tmp_path: Path) -> None:
    archive_dir = tmp_path / "downloads"
    archive_dir.mkdir()
    part1 = archive_dir / "game.part01.rar"
    part2 = archive_dir / "game.part02.rar"
    part1.write_text("p1")
    part2.write_text("p2")

    dest_dir = tmp_path / "extracted"

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"All OK", b"")

    extractor = ArchiveExtractor(cleanup_after_extract=True, tool="unrar")
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await extractor.extract_and_cleanup(part1, dest_dir)

        # Both multi-part volumes should have been removed
        assert not part1.exists()
        assert not part2.exists()


@pytest.mark.asyncio
async def test_extract_error_preserves_volumes(tmp_path: Path) -> None:
    archive_dir = tmp_path / "downloads"
    archive_dir.mkdir()
    rar_file = archive_dir / "corrupt.rar"
    rar_file.write_text("bad data")

    dest_dir = tmp_path / "extracted"

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate.return_value = (b"", b"CRC failed")

    extractor = ArchiveExtractor(cleanup_after_extract=True, tool="unrar")
    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(RuntimeError, match="unrar extraction failed with code 1: CRC failed"),
    ):
        await extractor.extract_and_cleanup(rar_file, dest_dir)

    # Archive parts must be preserved on failure
    assert rar_file.exists()


@pytest.mark.asyncio
async def test_extract_error_with_7z(tmp_path: Path) -> None:
    archive_dir = tmp_path / "downloads"
    archive_dir.mkdir()
    seven_z = archive_dir / "bad.7z"
    seven_z.write_text("bad data")

    dest_dir = tmp_path / "extracted"

    mock_proc = AsyncMock()
    mock_proc.returncode = 2
    mock_proc.communicate.return_value = (b"", b"Fatal error")

    extractor = ArchiveExtractor(cleanup_after_extract=True, tool="7z")
    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(RuntimeError, match="7z extraction failed with code 2: Fatal error"),
    ):
        await extractor.extract_and_cleanup(seven_z, dest_dir)

    assert seven_z.exists()


@pytest.mark.asyncio
async def test_extract_cleanup_disabled_preserves_volumes(tmp_path: Path) -> None:
    archive_dir = tmp_path / "downloads"
    archive_dir.mkdir()
    rar_file = archive_dir / "keep.rar"
    rar_file.write_text("keep this")

    dest_dir = tmp_path / "extracted"

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"All OK", b"")

    extractor = ArchiveExtractor(cleanup_after_extract=False)
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await extractor.extract_and_cleanup(rar_file, dest_dir)

    # Archive part preserved because cleanup_after_extract is False
    assert rar_file.exists()


@pytest.mark.asyncio
async def test_extract_and_cleanup_destination_diffing(tmp_path: Path) -> None:
    archive_dir = tmp_path / "downloads"
    archive_dir.mkdir()
    rar_file = archive_dir / "release.rar"
    rar_file.write_text("rar content")

    dest_dir = tmp_path / "extracted"
    dest_dir.mkdir()
    # Pre-existing file that should NOT be reported as extracted
    pre_existing = dest_dir / "old_file.txt"
    pre_existing.write_text("i was already here")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0

    async def fake_communicate():
        # Simulate extraction writing new file
        (dest_dir / "new_video.mkv").write_text("video stream")
        return (b"All OK", b"")

    mock_proc.communicate.side_effect = fake_communicate

    extractor = ArchiveExtractor(cleanup_after_extract=True, tool="unrar")
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        results = await extractor.extract_and_cleanup(rar_file, dest_dir)

    # Only newly created files should be in results
    assert len(results) == 1
    assert results[0].name == "new_video.mkv"
    assert pre_existing not in results


@pytest.mark.asyncio
async def test_extract_cancellation_terminates_proc(tmp_path: Path) -> None:
    archive_dir = tmp_path / "downloads"
    archive_dir.mkdir()
    archive = archive_dir / "large.zip"
    archive.write_text("data")

    dest_dir = tmp_path / "extracted"

    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = asyncio.CancelledError()
    mock_proc.terminate = MagicMock()
    mock_proc.wait = AsyncMock()

    extractor = ArchiveExtractor(tool="7z")
    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(asyncio.CancelledError),
    ):
        await extractor.extract_and_cleanup(archive, dest_dir)

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once()


def test_find_related_volumes_numeric_order_and_from_any_part(tmp_path: Path) -> None:
    extractor = ArchiveExtractor()
    for n in (10, 2, 1):
        (tmp_path / f"game.part{n}.rar").write_text("data")
    expected = [tmp_path / "game.part1.rar", tmp_path / "game.part2.rar", tmp_path / "game.part10.rar"]
    assert extractor.find_related_volumes(tmp_path / "game.part2.rar") == expected
    assert extractor.find_related_volumes(tmp_path / "game.part10.rar") == expected

    for ext in ("r01", "rar", "r00", "s00"):
        (tmp_path / f"movie.{ext}").write_text("data")
    assert extractor.find_related_volumes(tmp_path / "movie.r01") == [
        tmp_path / "movie.rar", tmp_path / "movie.r00", tmp_path / "movie.r01", tmp_path / "movie.s00"
    ]


def test_inspect_rar4_variants(tmp_path: Path) -> None:
    extractor = ArchiveExtractor()

    (single := tmp_path / "single.rar").write_bytes(_build_rar4_volume(is_volume=False))
    info_single = extractor.inspect_rar_volume(single)
    assert info_single is not None
    assert (info_single.version, info_single.is_volume, info_single.has_next_volume, info_single.truncated) == (4, False, False, False)

    (non_final := tmp_path / "nonfinal.part01.rar").write_bytes(_build_rar4_volume(is_volume=True, split_after=True, endarc_next_volume=True))
    info_nf = extractor.inspect_rar_volume(non_final)
    assert info_nf is not None and info_nf.is_volume is True and info_nf.has_next_volume is True and info_nf.truncated is False

    (final_vol := tmp_path / "final.part02.rar").write_bytes(_build_rar4_volume(is_volume=True, split_after=False, endarc_next_volume=False))
    info_fin = extractor.inspect_rar_volume(final_vol)
    assert info_fin is not None and info_fin.is_volume is True and info_fin.has_next_volume is False

    (large_ok := tmp_path / "large.rar").write_bytes(_build_rar4_volume(lhd_large=True, high_pack_size=0, endarc_next_volume=False))
    info_large = extractor.inspect_rar_volume(large_ok)
    assert info_large is not None and info_large.truncated is False and info_large.has_next_volume is False

    (large_trunc := tmp_path / "large_trunc.rar").write_bytes(_build_rar4_volume(lhd_large=True, high_pack_size=1))
    info_lt = extractor.inspect_rar_volume(large_trunc)
    assert info_lt is not None and info_lt.truncated is True

    (encrypted := tmp_path / "enc.rar").write_bytes(_build_rar4_volume(is_volume=True, encrypted_headers=True))
    info_enc = extractor.inspect_rar_volume(encrypted)
    assert info_enc is not None and info_enc.encrypted_headers is True and info_enc.has_next_volume is None

    (truncated := tmp_path / "trunc.rar").write_bytes(_build_rar4_volume(is_volume=True)[:-4])
    info_trunc = extractor.inspect_rar_volume(truncated)
    assert info_trunc is not None and info_trunc.truncated is True

    (dummy := tmp_path / "dummy.rar").write_text("not a rar binary")
    assert extractor.inspect_rar_volume(dummy) is None


def test_inspect_rar5_variants(tmp_path: Path) -> None:
    extractor = ArchiveExtractor()

    (non_final := tmp_path / "r5.part01.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=0, split_after=True, endarc_next_volume=True))
    info = extractor.inspect_rar_volume(non_final)
    assert info is not None
    assert (info.version, info.is_volume, info.has_next_volume, info.volume_number, info.truncated) == (5, True, True, 0, False)

    (final_vol := tmp_path / "r5.part02.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=1, endarc_next_volume=False))
    info_final = extractor.inspect_rar_volume(final_vol)
    assert info_final is not None and (info_final.has_next_volume, info_final.volume_number, info_final.truncated) == (False, 1, False)

    (enc := tmp_path / "r5_enc.rar").write_bytes(_build_rar5_volume(encrypted_headers=True))
    info_enc = extractor.inspect_rar_volume(enc)
    assert info_enc is not None and info_enc.encrypted_headers is True and info_enc.has_next_volume is None

    (missing_endarc := tmp_path / "r5_trunc.rar").write_bytes(_build_rar5_volume(is_volume=True, include_endarc=False))
    info_trunc = extractor.inspect_rar_volume(missing_endarc)
    assert info_trunc is not None and info_trunc.truncated is True


def test_check_archive_readiness_incomplete_cases(tmp_path: Path) -> None:
    extractor = ArchiveExtractor()

    (p1 := tmp_path / "set1.part01.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=0, endarc_next_volume=True))
    r1 = extractor.check_archive_readiness(p1)
    assert r1.ready is False and "remaining" in r1.reason.lower()

    (p2_only := tmp_path / "set2.part02.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=1, endarc_next_volume=False))
    r2 = extractor.check_archive_readiness(p2_only)
    assert r2.ready is False and "primary" in r2.reason.lower()

    (tmp_path / "gap.part01.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=0, endarc_next_volume=True))
    (g3 := tmp_path / "gap.part03.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=2, endarc_next_volume=False))
    r3 = extractor.check_archive_readiness(g3)
    assert r3.ready is False and "missing" in r3.reason.lower()

    (s1 := tmp_path / "aria.part01.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=0, endarc_next_volume=True))
    (tmp_path / "aria.part02.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=1, endarc_next_volume=False))
    (tmp_path / "aria.part02.rar.aria2").write_bytes(b"control")
    r4 = extractor.check_archive_readiness(s1)
    assert r4.ready is False and ".aria2" in r4.reason

    (tmp_path / "trunc.part01.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=0, endarc_next_volume=True))
    (t2 := tmp_path / "trunc.part02.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=1, endarc_next_volume=False)[:-2])
    r5 = extractor.check_archive_readiness(t2)
    assert r5.ready is False and "truncated" in r5.reason.lower()

    (old_only := tmp_path / "oldonly.rar").write_bytes(_build_rar4_volume(is_volume=True, endarc_next_volume=True))
    assert extractor.check_archive_readiness(old_only).ready is False


def test_check_archive_readiness_complete_sets(tmp_path: Path) -> None:
    extractor = ArchiveExtractor()

    (r5_1 := tmp_path / "ok5.part01.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=0, endarc_next_volume=True))
    (r5_2 := tmp_path / "ok5.part02.rar").write_bytes(_build_rar5_volume(is_volume=True, volume_number=1, endarc_next_volume=False))
    readiness_r5 = extractor.check_archive_readiness(r5_2)
    assert readiness_r5.ready is True and readiness_r5.primary_archive == r5_1 and readiness_r5.volumes == [r5_1, r5_2]

    (old_rar := tmp_path / "ok4.rar").write_bytes(_build_rar4_volume(is_volume=True, endarc_next_volume=True))
    (old_r00 := tmp_path / "ok4.r00").write_bytes(_build_rar4_volume(is_volume=True, endarc_next_volume=True))
    (old_r01 := tmp_path / "ok4.r01").write_bytes(_build_rar4_volume(is_volume=True, endarc_next_volume=False))
    readiness_old = extractor.check_archive_readiness(old_r01)
    assert readiness_old.ready is True and readiness_old.primary_archive == old_rar and readiness_old.volumes == [old_rar, old_r00, old_r01]
