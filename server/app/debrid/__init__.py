"""Multi-Debrid provider package and factory."""

from __future__ import annotations

from typing import Any

from app.debrid.base import BaseDebridProvider, classify_url, is_info_hash
from app.debrid.realdebrid import RealDebridClient, RealDebridProvider

SUPPORTED_PROVIDERS: list[str] = ["realdebrid", "torbox", "alldebrid"]


def get_debrid_provider(
    provider_name: str,
    api_key: str,
    base_url: str | None = None,
    **kwargs: Any,
) -> BaseDebridProvider:
    """Instantiate and return the appropriate Debrid provider."""
    normalized = provider_name.strip().lower()
    if normalized in ("realdebrid", "real-debrid", "rd"):
        if base_url:
            return RealDebridProvider(api_key=api_key, base_url=base_url, **kwargs)
        return RealDebridProvider(api_key=api_key, **kwargs)

    raise ValueError(
        f"Unsupported debrid provider: '{provider_name}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
    )


__all__ = [
    "SUPPORTED_PROVIDERS",
    "BaseDebridProvider",
    "RealDebridClient",
    "RealDebridProvider",
    "classify_url",
    "get_debrid_provider",
    "is_info_hash",
]
