"""Abstract base class for all Debrid providers."""

from __future__ import annotations

import abc
import re
from typing import Any, Self

import httpx

from app.schemas import LinkType, ResolvedFile


def is_info_hash(text: str) -> bool:
    """Check if text is a 40-character hex or 32-character base32 torrent info-hash."""
    cleaned = text.strip()
    return bool(
        re.fullmatch(r"[a-fA-F0-9]{40}", cleaned) or re.fullmatch(r"[a-zA-Z2-7]{32}", cleaned)
    )


def classify_url(url: str) -> LinkType:
    """Classify whether an input URL is a magnet URI, a torrent file, or a direct/hoster URL."""
    cleaned = url.strip()
    if cleaned.lower().startswith("magnet:?") and "xt=urn:" in cleaned.lower():
        return LinkType.MAGNET
    if is_info_hash(cleaned):
        return LinkType.MAGNET
    if cleaned.lower().endswith(".torrent") or cleaned.lower().startswith("torrent:"):
        return LinkType.TORRENT
    return LinkType.HOSTER



class BaseDebridProvider(abc.ABC):
    """Abstract base class establishing the contract for Multi-Debrid providers."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._owns_client = False

    def _create_client(self) -> httpx.AsyncClient:
        """Create a new httpx.AsyncClient with appropriate environment fallbacks."""
        try:
            return httpx.AsyncClient(timeout=self.timeout)
        except httpx.InvalidURL:
            # Handle environments where NO_PROXY contains bracketed IPv6 notation (e.g. [::1])
            return httpx.AsyncClient(timeout=self.timeout, trust_env=False)

    async def __aenter__(self) -> Self:
        if self._client is None:
            self._client = self._create_client()
            self._owns_client = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client session if owned by this instance."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    @abc.abstractmethod
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue an authenticated HTTP request to the provider API."""
        raise NotImplementedError

    @abc.abstractmethod
    async def unrestrict_link(self, link: str) -> dict[str, Any]:
        """Unrestrict a file hoster link into direct CDN metadata."""
        raise NotImplementedError

    @abc.abstractmethod
    async def add_magnet(self, magnet: str) -> dict[str, Any]:
        """Add a magnet link to the provider cloud."""
        raise NotImplementedError

    @abc.abstractmethod
    async def resolve_to_direct_files(
        self,
        url: str,
        poll_interval: float = 1.0,
        max_attempts: int = 60,
    ) -> list[ResolvedFile]:
        """Resolve magnet URI or hoster link into direct CDN URLs with filename metadata."""
        raise NotImplementedError

    async def resolve_to_direct_links(
        self,
        url: str,
        poll_interval: float = 1.0,
        max_attempts: int = 60,
    ) -> list[str]:
        """Resolve either a magnet URI or hoster link into direct URLs."""
        files = await self.resolve_to_direct_files(
            url, poll_interval=poll_interval, max_attempts=max_attempts
        )
        return [f.url for f in files]

    @abc.abstractmethod
    async def test_account(self) -> dict[str, Any]:
        """Test API key validity and return provider account status."""
        raise NotImplementedError
