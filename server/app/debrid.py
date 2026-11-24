"""Real-Debrid REST API client and URL classification (backwards-compatibility module)."""

from __future__ import annotations

from app.debrid import (
    BaseDebridProvider,
    RealDebridClient,
    RealDebridProvider,
    classify_url,
    get_debrid_provider,
    is_info_hash,
)

__all__ = [
    "BaseDebridProvider",
    "RealDebridClient",
    "RealDebridProvider",
    "classify_url",
    "get_debrid_provider",
    "is_info_hash",
]
