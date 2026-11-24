"""Post-download archive auto-extractor and split-RAR/7z cleanup utility."""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import stat
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, ClassVar

RAR4_MAGIC = b"Rar!\x1a\x07\x00"
RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"
RAR5_MAX_HEADER_SIZE = 2 * 1024 * 1024

_PART_RAR_RE = re.compile(r"^(.+)\.part(\d+)\.rar$", re.IGNORECASE)
_OLD_PART_RE = re.compile(r"^(.+)\.([r-z])(\d{2})$", re.IGNORECASE)


@dataclass(frozen=True)
class RarVolumeInfo:
    """Parsed RAR4/RAR5 archive volume metadata from binary headers."""

    version: int
    is_volume: bool
    has_next_volume: bool | None
    encrypted_headers: bool = False
    truncated: bool = False
    volume_number: int | None = None


@dataclass(frozen=True)
class ArchiveReadiness:
    """Result of checking whether an archive set is complete and safe to extract."""

    ready: bool
    primary_archive: Path | None
    volumes: list[Path]
    reason: str
    encrypted_headers: bool = False


def _read_vint(buf: bytes, offset: int) -> tuple[int, int]:
    """Decode a RAR5 variable-length integer from an in-memory buffer."""
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(buf):
            raise ValueError("Truncated RAR5 vint")
        byte = buf[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, offset
    raise ValueError("RAR5 vint exceeds 10 bytes")


def _read_vint_stream(stream: BinaryIO) -> int | None:
    """Decode a RAR5 variable-length integer directly from a binary stream."""
    value = 0
    for shift in range(0, 70, 7):
        raw = stream.read(1)
        if not raw:
            return None
        byte = raw[0]
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value
    return None


def _inspect_rar4(stream: BinaryIO, file_size: int) -> RarVolumeInfo:
    """Inspect RAR4 block headers starting immediately after the 7-byte magic."""
    is_volume = False
    saw_main = False
    saw_split_after = False
    endarc_next_volume = False

    def _truncated() -> RarVolumeInfo:
        return RarVolumeInfo(4, is_volume, True if is_volume else None, False, True)

    while (pos := stream.tell()) < file_size:
        hdr = stream.read(7)
        if len(hdr) < 7:
            return _truncated()
        _head_crc, head_type, head_flags, head_size = struct.unpack("<HBHH", hdr)
        if head_size < 7 or pos + head_size > file_size:
            return _truncated()
        remaining = stream.read(head_size - 7)
        if len(remaining) < head_size - 7:
            return _truncated()

        add_size = 0
        if head_type == 0x74 or (head_flags & 0x8000):
            if len(remaining) < 4:
                return _truncated()
            add_size = struct.unpack_from("<I", remaining, 0)[0]
            # LHD_LARGE (0x0100) stores upper 32 bits of packed data size at offset 25
            if head_type in (0x74, 0x7A) and (head_flags & 0x0100):
                if len(remaining) < 29:
                    return _truncated()
                high_pack_size = struct.unpack_from("<I", remaining, 25)[0]
                add_size |= high_pack_size << 32

        if head_type == 0x73:  # MAIN_HEAD
            saw_main = True
            is_volume = bool(head_flags & 0x0001)  # MHD_VOLUME
            if head_flags & 0x0080:  # MHD_PASSWORD (encrypted headers)
                return RarVolumeInfo(4, is_volume, None, encrypted_headers=True, truncated=False)
        elif head_type == 0x74:  # FILE_HEAD
            if head_flags & 0x0002:  # LHD_SPLIT_AFTER
                saw_split_after = True
        elif head_type == 0x7B:  # ENDARC_HEAD
            endarc_next_volume = bool(head_flags & 0x0001)  # ENDARC_NEXT_VOLUME
            break

        next_pos = pos + head_size + add_size
        if next_pos > file_size:
            return _truncated()
        stream.seek(next_pos)

    if not saw_main:
        return _truncated()
    return RarVolumeInfo(4, is_volume, endarc_next_volume or saw_split_after, False, False)


def _inspect_rar5(stream: BinaryIO, file_size: int) -> RarVolumeInfo:
    """Inspect RAR5 block headers starting immediately after the 8-byte magic."""
    is_volume = False
    saw_main = False
    saw_endarc = False
    saw_split_after = False
    endarc_next_volume = False
    volume_number: int | None = None

    def _truncated() -> RarVolumeInfo:
        return RarVolumeInfo(5, is_volume, True if is_volume else None, False, True, volume_number)

    while stream.tell() < file_size:
        if len(stream.read(4)) < 4:  # HEAD_CRC32
            return _truncated()
        head_size = _read_vint_stream(stream)
        if head_size is None or head_size == 0 or head_size > RAR5_MAX_HEADER_SIZE:
            return _truncated()
        body_start = stream.tell()
        if body_start + head_size > file_size:
            return _truncated()
        body = stream.read(head_size)
        if len(body) < head_size:
            return _truncated()

        try:
            head_type, idx = _read_vint(body, 0)
            head_flags, idx = _read_vint(body, idx)
            if head_flags & 0x0001:  # HFL_EXTRA
                _extra_size, idx = _read_vint(body, idx)
            data_size = 0
            if head_flags & 0x0002:  # HFL_DATA
                data_size, idx = _read_vint(body, idx)
            if head_flags & 0x0010:  # HFL_SPLITAFTER
                saw_split_after = True

            if head_type == 4:  # HEAD_CRYPT (encrypted headers)
                return RarVolumeInfo(5, is_volume, None, encrypted_headers=True, truncated=False, volume_number=volume_number)
            if head_type == 1:  # HEAD_MAIN
                saw_main = True
                archive_flags, idx = _read_vint(body, idx)
                is_volume = bool(archive_flags & 0x0001)  # MHFL_VOLUME
                if archive_flags & 0x0002:  # MHFL_VOLNUMBER
                    volume_number, idx = _read_vint(body, idx)
                elif is_volume:
                    volume_number = 0
            elif head_type == 5:  # HEAD_ENDARC
                saw_endarc = True
                endarc_flags, idx = _read_vint(body, idx)
                endarc_next_volume = bool(endarc_flags & 0x0001)  # EHFL_NEXTVOLUME
                break
        except ValueError:
            return _truncated()

        next_pos = body_start + head_size + data_size
        if next_pos > file_size:
            return _truncated()
        stream.seek(next_pos)

    if not saw_main or not saw_endarc:
        return _truncated()
    return RarVolumeInfo(5, is_volume, endarc_next_volume or saw_split_after, False, False, volume_number)


def _is_unsafe_archive_member_path(member_name: str, destination_dir: Path) -> bool:
    """Return True if an archive member path attempts traversal, absolute write, or null-byte injection."""
    if not member_name or "\x00" in member_name:
        return True
    normalized = member_name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return True
    if ".." in normalized.split("/"):
        return True
    dest_resolved = destination_dir.resolve()
    candidate = (dest_resolved / normalized).resolve()
    return not candidate.is_relative_to(dest_resolved)


def validate_zip_members(archive_path: Path, destination_dir: Path) -> None:
    """Reject zip archives containing path traversal entries or symlinks."""
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        return
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            if _is_unsafe_archive_member_path(info.filename, destination_dir):
                raise ValueError(f"Unsafe path traversal entry in zip archive: {info.filename}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"Unsafe symlink entry in zip archive: {info.filename}")


def validate_rar_members(archive_path: Path, destination_dir: Path) -> None:
    """Inspect RAR4/RAR5 file headers and reject traversal paths or symlink entries."""
    if not archive_path.is_file():
        return
    file_size = archive_path.stat().st_size
    with archive_path.open("rb") as stream:
        magic = stream.read(8)
        if magic == RAR5_MAGIC:
            while stream.tell() < file_size:
                if len(stream.read(4)) < 4:
                    return
                head_size = _read_vint_stream(stream)
                if head_size is None or head_size == 0 or head_size > RAR5_MAX_HEADER_SIZE:
                    return
                body_start = stream.tell()
                if body_start + head_size > file_size:
                    return
                body = stream.read(head_size)
                if len(body) < head_size:
                    return
                try:
                    head_type, idx = _read_vint(body, 0)
                    head_flags, idx = _read_vint(body, idx)
                    if head_flags & 0x0001:
                        _extra_size, idx = _read_vint(body, idx)
                    data_size = 0
                    if head_flags & 0x0002:
                        data_size, idx = _read_vint(body, idx)
                    if head_type == 4:
                        return
                    if head_type == 2:
                        file_flags, idx = _read_vint(body, idx)
                        _unpacked_size, idx = _read_vint(body, idx)
                        attributes, idx = _read_vint(body, idx)
                        if file_flags & 0x0002:
                            idx += 4
                        if file_flags & 0x0004:
                            idx += 4
                        _comp_info, idx = _read_vint(body, idx)
                        host_os, idx = _read_vint(body, idx)
                        name_len, idx = _read_vint(body, idx)
                    else:
                        head_type = -1
                except ValueError:
                    return

                if head_type == 2 and idx + name_len <= len(body):
                    name = body[idx : idx + name_len].decode("utf-8", errors="replace")
                    if _is_unsafe_archive_member_path(name, destination_dir):
                        raise ValueError(f"Unsafe path traversal entry in RAR5 archive: {name}")
                    if host_os == 1 and stat.S_ISLNK(attributes):
                        raise ValueError(f"Unsafe symlink entry in RAR5 archive: {name}")

                next_pos = body_start + head_size + data_size
                if next_pos > file_size:
                    return
                stream.seek(next_pos)
            return

        if magic[:7] == RAR4_MAGIC:
            stream.seek(7)
            while (pos := stream.tell()) < file_size:
                hdr = stream.read(7)
                if len(hdr) < 7:
                    return
                try:
                    _head_crc, head_type, head_flags, head_size = struct.unpack("<HBHH", hdr)
                except struct.error:
                    return
                if head_size < 7 or pos + head_size > file_size:
                    return
                remaining = stream.read(head_size - 7)
                if len(remaining) < head_size - 7:
                    return

                add_size = 0
                if head_type == 0x74 or (head_flags & 0x8000):
                    if len(remaining) < 4:
                        return
                    add_size = struct.unpack_from("<I", remaining, 0)[0]
                    if head_type in (0x74, 0x7A) and (head_flags & 0x0100):
                        if len(remaining) < 29:
                            return
                        high_pack_size = struct.unpack_from("<I", remaining, 25)[0]
                        add_size |= high_pack_size << 32

                if head_type == 0x73 and (head_flags & 0x0080):
                    return
                if head_type == 0x74 and len(remaining) >= 25:
                    host_os = remaining[8]
                    name_size = struct.unpack_from("<H", remaining, 19)[0]
                    attr = struct.unpack_from("<I", remaining, 21)[0]
                    name_offset = 33 if (head_flags & 0x0100) else 25
                    if len(remaining) >= name_offset + name_size:
                        raw_name = remaining[name_offset : name_offset + name_size]
                        if head_flags & 0x0200:
                            raw_name = raw_name.split(b"\x00", 1)[0]
                        name = raw_name.decode("utf-8", errors="replace")
                        if _is_unsafe_archive_member_path(name, destination_dir):
                            raise ValueError(f"Unsafe path traversal entry in RAR archive: {name}")
                        if host_os == 3 and stat.S_ISLNK(attr):
                            raise ValueError(f"Unsafe symlink entry in RAR archive: {name}")
                elif head_type == 0x7B:
                    return

                next_pos = pos + head_size + add_size
                if next_pos > file_size:
                    return
                stream.seek(next_pos)


def validate_extracted_tree(destination_dir: Path, before_entries: set[Path]) -> None:
    """Remove any newly extracted symlink that escapes destination_dir and raise ValueError."""
    dest_resolved = destination_dir.resolve()
    for p in destination_dir.rglob("*"):
        if p in before_entries:
            continue
        if p.is_symlink():
            resolved = p.resolve()
            if not resolved.is_relative_to(dest_resolved):
                p.unlink(missing_ok=True)
                raise ValueError(f"Unsafe symlink escaping target directory removed: {p.name}")


class ArchiveExtractor:
    """Handles automatic extraction of single and multi-part archives via `unrar` or `7z`."""

    SUPPORTED_EXTENSIONS: ClassVar[set[str]] = {".rar", ".zip", ".7z"}

    def __init__(self, cleanup_after_extract: bool = True, tool: str | None = None) -> None:
        self.cleanup_after_extract = cleanup_after_extract
        self._configured_tool = tool

    @property
    def tool(self) -> str:
        """Return explicitly configured extractor tool or auto-detect available binary."""
        if self._configured_tool:
            return self._configured_tool
        if shutil.which("unrar"):
            return "unrar"
        if shutil.which("7z"):
            return "7z"
        return "unrar"

    def parse_rar_volume_info(self, file_path: Path) -> tuple[str, str, int] | None:
        """Parse RAR volume naming into `(style, normalized_stem, sequence_index)`."""
        name = Path(file_path).name
        part_match = _PART_RAR_RE.match(name)
        if part_match:
            vol_num = int(part_match.group(2))
            if vol_num >= 1:
                return ("part", part_match.group(1).lower(), vol_num)
            return None

        if name.lower().endswith(".rar"):
            return ("old", name[:-4].lower(), 0)

        old_match = _OLD_PART_RE.match(name)
        if old_match:
            letter = old_match.group(2).lower()
            digits = int(old_match.group(3))
            vol_idx = 1 + (ord(letter) - ord("r")) * 100 + digits
            return ("old", old_match.group(1).lower(), vol_idx)

        return None

    def is_rar_volume(self, file_path: Path) -> bool:
        """Return True if `file_path` is any RAR volume (primary or continuation)."""
        return self.parse_rar_volume_info(file_path) is not None

    def get_archive_group_key(self, file_path: Path) -> str | None:
        """Return a stable grouping key uniting all volumes of the same archive set."""
        rar_info = self.parse_rar_volume_info(file_path)
        if rar_info:
            style, base_stem, _ = rar_info
            return f"rar:{style}:{base_stem}"

        name_lower = Path(file_path).name.lower()
        if name_lower.endswith(".zip"):
            return f"zip:{name_lower}"
        if name_lower.endswith(".7z"):
            return f"7z:{name_lower}"
        return None

    def is_primary_rar(self, file_path: Path) -> bool:
        """Return True if `file_path` is a standalone `.rar` or the first volume (`.part01.rar`)."""
        name = file_path.name.lower()
        if not name.endswith(".rar"):
            return False
        if ".part" in name:
            stem_before_rar = name[:-4]  # strip .rar
            part_idx = stem_before_rar.rfind(".part")
            part_num_str = stem_before_rar[part_idx + 5 :]
            if part_num_str.isdigit():
                return int(part_num_str) == 1
        return True

    def is_extractable_archive(self, file_path: Path) -> bool:
        """Return True if `file_path` is any primary extractable archive (.rar, .zip, .7z)."""
        name = file_path.name.lower()
        if name.endswith((".zip", ".7z")):
            return True
        return self.is_primary_rar(file_path)

    def find_related_volumes(self, archive_path: Path) -> list[Path]:
        """Identify all split archive parts in the same directory, ordered numerically."""
        path = Path(archive_path)
        parent = path.parent
        if not parent.exists():
            return [path]

        rar_info = self.parse_rar_volume_info(path)
        if rar_info is None:
            return [path]

        target_style, target_stem, _ = rar_info
        matched: list[tuple[int, str, Path]] = []
        for entry in parent.iterdir():
            if not entry.is_file():
                continue
            parsed = self.parse_rar_volume_info(entry)
            if parsed and (parsed[0], parsed[1]) == (target_style, target_stem):
                matched.append((parsed[2], entry.name.lower(), entry))

        matched.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in matched]

    def has_aria2_sidecars(self, archive_path: Path) -> bool:
        """Return True if any `.aria2` download control file exists for the same archive set."""
        path = Path(archive_path)
        parent = path.parent
        if not parent.exists():
            return False

        group_key = self.get_archive_group_key(path)
        if group_key is None:
            return False

        for entry in parent.iterdir():
            if entry.is_file() and entry.name.lower().endswith(".aria2"):
                stripped = entry.name[:-6]
                if self.get_archive_group_key(Path(stripped)) == group_key:
                    return True
        return False

    def inspect_rar_volume(self, file_path: Path) -> RarVolumeInfo | None:
        """Inspect RAR4/RAR5 headers of a volume on disk, or return None if not a RAR file."""
        path = Path(file_path)
        if not path.is_file():
            return None

        file_size = path.stat().st_size
        with path.open("rb") as stream:
            magic = stream.read(8)
            if magic == RAR5_MAGIC:
                return _inspect_rar5(stream, file_size)
            if magic[:7] == RAR4_MAGIC:
                stream.seek(7)
                return _inspect_rar4(stream, file_size)
        return None

    def check_archive_readiness(self, archive_path: Path) -> ArchiveReadiness:
        """Verify whether an archive (or multi-part RAR set) is complete and safe to extract."""
        path = Path(archive_path)
        rar_info = self.parse_rar_volume_info(path)

        if rar_info is None:
            if path.name.lower().endswith((".zip", ".7z")):
                if path.parent.exists() and self.has_aria2_sidecars(path):
                    return ArchiveReadiness(
                        False, None, [path], f"Archive download still in progress (.aria2 sidecar): {path.name}"
                    )
                return ArchiveReadiness(True, path, [path], "Ready")
            return ArchiveReadiness(False, None, [], f"Unsupported archive type: {path.name}")

        style, _, _ = rar_info
        if not path.parent.exists():
            if self.is_primary_rar(path):
                return ArchiveReadiness(True, path, [path], "Ready (unverified mocked path)")
            return ArchiveReadiness(False, None, [path], f"Waiting for primary volume before extracting {path.name}")

        if self.has_aria2_sidecars(path):
            return ArchiveReadiness(
                False, None, [], f"Archive download still in progress (.aria2 sidecar for {path.name})"
            )

        volumes = self.find_related_volumes(path)
        if not volumes:
            return ArchiveReadiness(False, None, [], f"No archive volumes found for {path.name}")

        parsed_indices = [
            parsed[2]
            for v in volumes
            if (parsed := self.parse_rar_volume_info(v)) is not None
        ]
        expected_first = 1 if style == "part" else 0
        if parsed_indices[0] != expected_first:
            return ArchiveReadiness(
                False, None, volumes, f"Waiting for primary volume (found starting at index {parsed_indices[0]})"
            )

        if parsed_indices != list(range(expected_first, expected_first + len(volumes))):
            return ArchiveReadiness(
                False, None, volumes, f"Missing intermediate RAR volume(s) in {path.parent.name}"
            )

        last_info: RarVolumeInfo | None = None
        for vol in volumes:
            info = self.inspect_rar_volume(vol)
            if info is not None and info.truncated:
                return ArchiveReadiness(False, None, volumes, f"RAR volume appears truncated: {vol.name}")
            last_info = info

        if last_info is not None and last_info.has_next_volume is True:
            return ArchiveReadiness(
                False, None, volumes, f"Waiting for remaining volume(s) after {volumes[-1].name}"
            )

        if (
            len(volumes) == 1
            and style == "old"
            and last_info is not None
            and last_info.is_volume
            and last_info.has_next_volume is not False
        ):
            return ArchiveReadiness(
                False, None, volumes, f"Waiting for .r00+ continuation volumes after {volumes[0].name}"
            )

        return ArchiveReadiness(
            True,
            volumes[0],
            volumes,
            "Ready",
            encrypted_headers=bool(last_info and last_info.encrypted_headers),
        )

    def build_extract_command(self, archive_path: Path, dest_arg: str) -> list[str]:
        """Construct the extraction command depending on active binary (unrar or 7z)."""
        archive_str = str(archive_path)
        if archive_str.startswith("-"):
            archive_str = f"./{archive_str}"
        if dest_arg.startswith("-"):
            dest_arg = f"./{dest_arg}"

        active_tool = self.tool
        if active_tool == "7z":
            # 7-Zip CLI: 7z x -y -o<dest_dir> -- <archive_path>
            return ["7z", "x", "-y", f"-o{dest_arg}", "--", archive_str]
        # Default unrar CLI: unrar x -o+ -y -- <archive_path> <dest_dir/>
        return ["unrar", "x", "-o+", "-y", "--", archive_str, dest_arg]

    async def extract_and_cleanup(self, archive_path: Path, destination_dir: Path) -> list[Path]:
        """Extract an archive into `destination_dir` and optionally delete source archive parts."""
        archive_path = Path(archive_path)
        destination_dir = Path(destination_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)

        if archive_path.suffix.lower() == ".zip":
            validate_zip_members(archive_path, destination_dir)
        elif self.is_rar_volume(archive_path):
            validate_rar_members(archive_path, destination_dir)

        before_entries = set(destination_dir.rglob("*"))
        before_files = {p for p in before_entries if p.is_file() and not p.is_symlink()}

        dest_arg = str(destination_dir).rstrip("/") + "/"
        active_tool = self.tool
        cmd = self.build_extract_command(archive_path, dest_arg)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                proc.terminate()
                await proc.wait()
            raise

        if proc.returncode != 0:
            err_msg = stderr.decode().strip() or stdout.decode().strip() or f"exit code {proc.returncode}"
            raise RuntimeError(f"{active_tool} extraction failed with code {proc.returncode}: {err_msg}")

        validate_extracted_tree(destination_dir, before_entries)

        if self.cleanup_after_extract:
            for volume in self.find_related_volumes(archive_path):
                if volume.exists():
                    volume.unlink()

        after_files = {p for p in destination_dir.rglob("*") if p.is_file() and not p.is_symlink()}
        extracted_files = [p for p in sorted(after_files - before_files, key=lambda p: str(p)) if p.is_file()]
        return extracted_files
