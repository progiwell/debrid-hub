"""Tests for Real-Debrid URL classification and RealDebridClient API operations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import httpx
import pytest
from app.debrid import (
    RealDebridClient,
    RealDebridProvider,
    classify_url,
    get_debrid_provider,
    is_info_hash,
)
from app.schemas import LinkType, ResolvedFile


def test_classify_url_magnet() -> None:
    assert classify_url("magnet:?xt=urn:btih:abcdef") == LinkType.MAGNET
    assert classify_url("  MAGNET:?xt=urn:btih:12345 ") == LinkType.MAGNET
    assert classify_url("magnet:?dn=test") != LinkType.MAGNET


def test_classify_url_raw_info_hash() -> None:
    # 40-character hex info hash
    assert classify_url("4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b") == LinkType.MAGNET
    assert classify_url("  4A2B3C4D5E6F7A8B9C0D1E2F3A4B5C6D7E8F9A0B  ") == LinkType.MAGNET
    # 32-character base32 info hash
    assert classify_url("JINWZX3XQ4VMBCLY47A5G7V54Z3V7B4Q") == LinkType.MAGNET
    assert is_info_hash("4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b") is True
    assert is_info_hash("notahash") is False



def test_classify_url_hoster() -> None:
    assert classify_url("https://1fichier.com/?xyz") == LinkType.HOSTER
    assert classify_url("https://rapidgator.net/file/123") == LinkType.HOSTER


@pytest.mark.asyncio
async def test_unrestrict_link_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/rest/1.0/unrestrict/link"
        assert request.headers["authorization"] == "Bearer test_token"
        assert b"link=https%3A%2F%2F1fichier.com%2F%3Fxyz" in request.content
        return httpx.Response(200, json={"id": "abc", "download": "https://cdn.real-debrid.com/dl/file.mkv"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        result = await rd.unrestrict_link("https://1fichier.com/?xyz")
        assert result["download"] == "https://cdn.real-debrid.com/dl/file.mkv"


@pytest.mark.asyncio
async def test_unrestrict_link_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "bad_token"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="bad_token", client=mock_client)
        with pytest.raises(RuntimeError, match="Real-Debrid API error 403"):
            await rd.unrestrict_link("https://1fichier.com/?xyz")


@pytest.mark.asyncio
async def test_add_magnet_and_select_files() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/1.0/torrents/addMagnet":
            assert b"magnet%3A%3Fxt%3Durn" in request.content
            return httpx.Response(201, json={"id": "TORRENT123", "uri": "https://..."})
        elif request.url.path == "/rest/1.0/torrents/selectFiles/TORRENT123":
            assert b"files=all" in request.content
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        res = await rd.add_magnet("magnet:?xt=urn:btih:abcdef")
        assert res["id"] == "TORRENT123"

        # select_files returns None on success
        await rd.select_files("TORRENT123", "all")


@pytest.mark.asyncio
async def test_get_torrent_info() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/rest/1.0/torrents/info/TORRENT123"
        return httpx.Response(200, json={"id": "TORRENT123", "status": "downloaded", "links": ["https://rd.com/d/1"]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        info = await rd.get_torrent_info("TORRENT123")
        assert info["status"] == "downloaded"
        assert info["links"] == ["https://rd.com/d/1"]


@pytest.mark.asyncio
async def test_resolve_to_direct_links_hoster() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"download": "https://cdn.real-debrid.com/file.zip"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        links = await rd.resolve_to_direct_links("https://rapidgator.net/file/123")
        assert links == ["https://cdn.real-debrid.com/file.zip"]


@pytest.mark.asyncio
async def test_resolve_to_direct_links_magnet_success() -> None:
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        path = request.url.path
        if path == "/rest/1.0/torrents/addMagnet":
            return httpx.Response(201, json={"id": "T123"})
        if path == "/rest/1.0/torrents/selectFiles/T123":
            return httpx.Response(204)
        if path == "/rest/1.0/torrents/info/T123":
            poll_count += 1
            if poll_count < 2:
                return httpx.Response(200, json={"id": "T123", "status": "downloading", "links": []})
            return httpx.Response(
                200,
                json={
                    "id": "T123",
                    "status": "downloaded",
                    "links": ["https://real-debrid.com/d/L1", "https://real-debrid.com/d/L2"],
                },
            )
        if path == "/rest/1.0/unrestrict/link":
            body = request.read().decode()
            if "L1" in body:
                return httpx.Response(200, json={"download": "https://cdn.rd.com/direct1.iso"})
            return httpx.Response(200, json={"download": "https://cdn.rd.com/direct2.iso"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        links = await rd.resolve_to_direct_links("magnet:?xt=urn:btih:xyz", poll_interval=0.01)
        assert links == ["https://cdn.rd.com/direct1.iso", "https://cdn.rd.com/direct2.iso"]
        assert poll_count == 2


@pytest.mark.asyncio
async def test_resolve_to_direct_links_magnet_terminal_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/1.0/torrents/addMagnet":
            return httpx.Response(201, json={"id": "T123"})
        if path == "/rest/1.0/torrents/selectFiles/T123":
            return httpx.Response(204)
        if path == "/rest/1.0/torrents/info/T123":
            return httpx.Response(200, json={"id": "T123", "status": "magnet_error"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        with pytest.raises(RuntimeError, match="failed with status: magnet_error"):
            await rd.resolve_to_direct_links("magnet:?xt=urn:btih:xyz", poll_interval=0.01)


@pytest.mark.asyncio
async def test_resolve_to_direct_links_magnet_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/1.0/torrents/addMagnet":
            return httpx.Response(201, json={"id": "T123"})
        if path == "/rest/1.0/torrents/selectFiles/T123":
            return httpx.Response(204)
        if path == "/rest/1.0/torrents/info/T123":
            return httpx.Response(200, json={"id": "T123", "status": "queued"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        with pytest.raises(TimeoutError, match="did not finish downloading within timeout"):
            await rd.resolve_to_direct_links("magnet:?xt=urn:btih:xyz", poll_interval=0.01, max_attempts=2)


@pytest.mark.asyncio
async def test_real_debrid_client_default_http_request() -> None:
    """Test RealDebridClient when client is None, verifying context manager execution."""
    client = RealDebridClient(api_key="token_abc")
    mock_resp = httpx.Response(200, json={"download": "https://cdn.rd.com/test.zip"})
    mock_instance = AsyncMock()
    mock_instance.request.return_value = mock_resp
    mock_instance.__aenter__.return_value = mock_instance
    mock_instance.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_instance):
        res = await client.unrestrict_link("https://rapidgator.net/file/1")
        assert res["download"] == "https://cdn.rd.com/test.zip"
        assert mock_instance.request.call_count == 1


@pytest.mark.asyncio
async def test_real_debrid_client_async_context_manager() -> None:
    """Verify async context manager creates persistent client and closes it on exit."""
    rd = RealDebridClient(api_key="token_abc")
    assert rd._client is None
    assert rd._owns_client is False

    mock_client = AsyncMock()
    with patch.object(rd, "_create_client", return_value=mock_client):
        async with rd as client_session:
            assert client_session is rd
            assert rd._client is mock_client
            assert rd._owns_client is True

    mock_client.aclose.assert_called_once()
    assert rd._client is None
    assert rd._owns_client is False


@pytest.mark.asyncio
async def test_real_debrid_client_external_client_not_closed() -> None:
    """Verify externally supplied client is not closed when context manager exits."""
    external_client = AsyncMock()
    rd = RealDebridClient(api_key="token_abc", client=external_client)
    assert rd._owns_client is False

    async with rd as client_session:
        assert client_session is rd
        assert rd._client is external_client

    external_client.aclose.assert_not_called()
    assert rd._client is external_client


@pytest.mark.asyncio
async def test_resolve_to_direct_links_auto_reuses_session() -> None:
    """Verify resolve_to_direct_links automatically manages a single persistent session for batch calls."""
    rd = RealDebridClient(api_key="token_abc")
    mock_client = AsyncMock()
    mock_client.request.side_effect = [
        # addMagnet
        httpx.Response(201, json={"id": "T456"}),
        # selectFiles
        httpx.Response(204),
        # get_torrent_info
        httpx.Response(200, json={"id": "T456", "status": "downloaded", "links": ["https://rd.com/part1"]}),
        # unrestrict_link
        httpx.Response(200, json={"download": "https://cdn.rd.com/part1.iso"}),
    ]

    with patch.object(rd, "_create_client", return_value=mock_client):
        direct_links = await rd.resolve_to_direct_links("magnet:?xt=urn:btih:magnet456")
        assert direct_links == ["https://cdn.rd.com/part1.iso"]
        assert mock_client.request.call_count == 4
        # Client should be cleaned up after resolution completes
        mock_client.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_to_direct_files_hoster() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "download": "https://cdn.real-debrid.com/game.rar",
                "filename": "game.rar",
                "filesize": 1048576,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        files = await rd.resolve_to_direct_files("https://rapidgator.net/file/game.rar")
        assert len(files) == 1
        assert files[0] == ResolvedFile(
            url="https://cdn.real-debrid.com/game.rar",
            filename="game.rar",
            filesize=1048576,
        )


@pytest.mark.asyncio
async def test_resolve_to_direct_files_magnet() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/1.0/torrents/addMagnet":
            return httpx.Response(201, json={"id": "TMAG1"})
        if path == "/rest/1.0/torrents/selectFiles/TMAG1":
            return httpx.Response(204)
        if path == "/rest/1.0/torrents/info/TMAG1":
            return httpx.Response(
                200,
                json={
                    "id": "TMAG1",
                    "status": "downloaded",
                    "links": ["https://rd.com/f1", "https://rd.com/f2"],
                },
            )
        if path == "/rest/1.0/unrestrict/link":
            body = request.read().decode()
            if "f1" in body:
                return httpx.Response(
                    200,
                    json={
                        "download": "https://cdn.rd.com/file1.iso",
                        "filename": "file1.iso",
                        "filesize": 500,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "download": "https://cdn.rd.com/file2.iso",
                    "filename": "file2.iso",
                    "filesize": 700,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        files = await rd.resolve_to_direct_files("magnet:?xt=urn:btih:xyz123", poll_interval=0.01)
        assert len(files) == 2
        assert files[0].filename == "file1.iso"
        assert files[0].filesize == 500
        assert files[1].filename == "file2.iso"
        assert files[1].filesize == 700


def test_get_debrid_provider_factory() -> None:
    provider = get_debrid_provider("realdebrid", api_key="secret_key")
    assert isinstance(provider, RealDebridProvider)
    assert provider.api_key == "secret_key"

    # Alias / case insensitivity
    provider_rd = get_debrid_provider("RD", api_key="secret_key")
    assert isinstance(provider_rd, RealDebridProvider)

    # Unknown provider
    with pytest.raises(ValueError, match="Unsupported debrid provider"):
        get_debrid_provider("unknown_provider", api_key="test")


@pytest.mark.asyncio
async def test_real_debrid_provider_test_account() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/1.0/user"
        return httpx.Response(
            200,
            json={
                "username": "tester",
                "email": "test@example.com",
                "type": "premium",
                "expiration": "2030-01-01T00:00:00.000Z",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        provider = RealDebridProvider(api_key="valid_token", client=mock_client)
        result = await provider.test_account()
        assert result["valid"] is True
        assert result["username"] == "tester"
        assert result["type"] == "premium"
        assert result["expiration"] == "2030-01-01T00:00:00.000Z"


@pytest.mark.asyncio
async def test_resolve_to_direct_files_concurrent_unrestriction() -> None:
    links = [f"https://real-debrid.com/d/{i}" for i in range(10)]
    info_response = {
        "id": "TCONCURRENT",
        "status": "downloaded",
        "links": links,
    }

    concurrent_calls = 0
    max_concurrent_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal concurrent_calls, max_concurrent_calls
        path = request.url.path
        if path == "/rest/1.0/torrents/addMagnet":
            return httpx.Response(201, json={"id": "TCONCURRENT"})
        if path.startswith("/rest/1.0/torrents/selectFiles/"):
            return httpx.Response(204)
        if path == "/rest/1.0/torrents/info/TCONCURRENT":
            return httpx.Response(200, json=info_response)
        if path == "/rest/1.0/unrestrict/link":
            concurrent_calls += 1
            max_concurrent_calls = max(max_concurrent_calls, concurrent_calls)
            # Return link item
            req_data = dict(x.split("=") for x in request.read().decode().split("&") if "=" in x)
            link_val = unquote(req_data.get("link", ""))
            concurrent_calls -= 1
            return httpx.Response(
                200,
                json={
                    "download": f"https://cdn.rd.com/{link_val.split('/')[-1]}.bin",
                    "filename": f"file_{link_val.split('/')[-1]}.bin",
                    "filesize": 1024,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        rd = RealDebridClient(api_key="test_token", client=mock_client)
        files = await rd.resolve_to_direct_files("magnet:?xt=urn:btih:concurrent123", poll_interval=0.01)
        assert len(files) == 10
        # Verify ordering is preserved
        for i, f in enumerate(files):
            assert f.filename == f"file_{i}.bin"
            assert f.url == f"https://cdn.rd.com/{i}.bin"
        assert max_concurrent_calls <= 5

