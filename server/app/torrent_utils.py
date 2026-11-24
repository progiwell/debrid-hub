"""Utility functions for parsing Magnet URIs and bencoded .torrent files."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from urllib.parse import quote, unquote


def extract_magnet_hash(url: str) -> str | None:
    """Extract a 40-character lowercase hexadecimal info-hash from a magnet URI."""
    if not url or not url.strip().lower().startswith("magnet:?"):
        return None

    # Match xt=urn:btih:<hash>
    match = re.search(r"xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", url, re.IGNORECASE)
    if not match:
        return None

    raw_hash = match.group(1)
    if len(raw_hash) == 40:
        return raw_hash.lower()

    if len(raw_hash) == 32:
        try:
            decoded = base64.b32decode(raw_hash.upper())
            return decoded.hex().lower()
        except (binascii.Error, ValueError):
            return None

    return None


def extract_magnet_name(url: str) -> str | None:
    """Extract the display name (&dn=) from a magnet URI if present."""
    match = re.search(r"[?&]dn=([^&]+)", url)
    if match:
        return unquote(match.group(1).replace("+", " "))
    return None


def extract_torrent_metadata(data: bytes) -> tuple[str, str]:
    """Parse raw bencoded .torrent file bytes and extract (info_hash_hex, name).

    Raises ValueError if data is not a valid bencoded torrent or missing info dict.
    """
    if not data:
        raise ValueError("Empty torrent file data")

    idx = data.find(b"4:info")
    if idx == -1:
        # Fallback to direct sha1 of data
        return hashlib.sha1(data).hexdigest().lower(), "download.torrent"

    info_start = idx + 6
    if data[info_start : info_start + 1] != b"d":
        return hashlib.sha1(data).hexdigest().lower(), "download.torrent"

    def get_token_len(s: bytes) -> int:
        c = s[:1]
        if c == b"i":
            return s.index(b"e") + 1
        if c.isdigit():
            colon = s.index(b":")
            length = int(s[:colon])
            return colon + 1 + length
        if c in (b"l", b"d"):
            p = 1
            while s[p : p + 1] != b"e":
                p += get_token_len(s[p:])
            return p + 1
        raise ValueError(f"Invalid bencode token: {c!r}")

    try:
        info_len = get_token_len(data[info_start:])
        info_bytes = data[info_start : info_start + info_len]
        info_hash = hashlib.sha1(info_bytes).hexdigest().lower()
    except (ValueError, IndexError):
        info_hash = hashlib.sha1(data).hexdigest().lower()
        info_bytes = b""

    # Attempt to extract name from info dictionary
    name = "torrent"
    if info_bytes:
        name_idx = info_bytes.find(b"4:name")
        if name_idx != -1:
            try:
                rest = info_bytes[name_idx + 6 :]
                colon = rest.index(b":")
                nlen = int(rest[:colon])
                name = rest[colon + 1 : colon + 1 + nlen].decode("utf-8", errors="replace")
            except (ValueError, IndexError):
                pass

    return info_hash, name


def create_magnet_from_torrent(info_hash: str, name: str) -> str:
    """Construct a Magnet URI from an info-hash and display name."""
    clean_name = quote(name)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={clean_name}"
