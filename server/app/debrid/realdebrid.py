"""Real-Debrid REST API provider implementation."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.debrid.base import BaseDebridProvider, classify_url
from app.schemas import LinkType, ResolvedFile


class RealDebridProvider(BaseDebridProvider):
    """Client for interacting with Real-Debrid REST API v1.0."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.real-debrid.com/rest/1.0",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout, client=client)

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue an authenticated HTTP request to the Real-Debrid REST API."""
        if not self.api_key:
            raise ValueError("Real-Debrid API key is required but was not provided.")

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        if self._client is not None:
            response = await self._client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=data,
            )
        else:
            async with self._create_client() as direct_client:
                response = await direct_client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    data=data,
                )

        if response.is_error:
            raise RuntimeError(
                f"Real-Debrid API error {response.status_code} ({method} {endpoint}): {response.text}"
            )
        return response

    async def unrestrict_link(self, link: str) -> dict[str, Any]:
        """Unrestrict a hoster or Real-Debrid torrent link into a direct CDN URL."""
        response = await self._request("POST", "/unrestrict/link", data={"link": link})
        return response.json()

    async def add_magnet(self, magnet: str) -> dict[str, Any]:
        """Add a magnet link to Real-Debrid torrents."""
        response = await self._request("POST", "/torrents/addMagnet", data={"magnet": magnet})
        return response.json()

    async def select_files(self, torrent_id: str, files: str = "all") -> None:
        """Select files within a Real-Debrid torrent to start caching/downloading."""
        await self._request(
            "POST", f"/torrents/selectFiles/{torrent_id}", data={"files": files}
        )

    async def get_torrent_info(self, torrent_id: str) -> dict[str, Any]:
        """Retrieve current status and generated links for a Real-Debrid torrent."""
        response = await self._request("GET", f"/torrents/info/{torrent_id}")
        return response.json()

    async def resolve_to_direct_files(
        self,
        url: str,
        poll_interval: float = 1.0,
        max_attempts: int = 60,
    ) -> list[ResolvedFile]:
        """Resolve magnet URI or hoster link into direct CDN URLs with filename metadata."""
        if self._client is None:
            async with self:
                return await self.resolve_to_direct_files(
                    url, poll_interval=poll_interval, max_attempts=max_attempts
                )

        link_type = classify_url(url)
        if link_type == LinkType.HOSTER:
            result = await self.unrestrict_link(url)
            download_url = result.get("download")
            if not download_url:
                raise RuntimeError(f"No download URL in Real-Debrid response: {result}")
            filename = result.get("filename")
            filesize = result.get("filesize")
            return [ResolvedFile(url=download_url, filename=filename, filesize=filesize)]

        # Magnet flow
        magnet_res = await self.add_magnet(url)
        torrent_id = magnet_res.get("id")
        if not torrent_id:
            raise RuntimeError(
                f"No torrent ID returned by Real-Debrid addMagnet: {magnet_res}"
            )

        await self.select_files(torrent_id, files="all")

        terminal_error_states = {"magnet_error", "error", "virus", "dead"}
        info: dict[str, Any] = {}

        for _ in range(max_attempts):
            info = await self.get_torrent_info(torrent_id)
            status = info.get("status")
            if status == "downloaded":
                break
            if status in terminal_error_states:
                raise RuntimeError(f"Torrent {torrent_id} failed with status: {status}")
            await asyncio.sleep(poll_interval)
        else:
            raise TimeoutError(
                f"Torrent {torrent_id} did not finish downloading within timeout (last status: {info.get('status')})"
            )

        links = info.get("links", [])
        semaphore = asyncio.Semaphore(5)

        async def _unrestrict_link_item(item_link: str) -> ResolvedFile | None:
            async with semaphore:
                unrestricted = await self.unrestrict_link(item_link)
                dl = unrestricted.get("download")
                if dl:
                    return ResolvedFile(
                        url=dl,
                        filename=unrestricted.get("filename"),
                        filesize=unrestricted.get("filesize"),
                    )
                return None

        tasks = [_unrestrict_link_item(link) for link in links]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def test_account(self) -> dict[str, Any]:
        """Validate Real-Debrid API token and return account info."""
        response = await self._request("GET", "/user")
        data = response.json()
        return {
            "valid": True,
            "provider": "realdebrid",
            "username": data.get("username", "unknown"),
            "email": data.get("email"),
            "type": data.get("type", "unknown"),
            "expiration": data.get("expiration"),
        }


# Backwards compatibility alias
RealDebridClient = RealDebridProvider
