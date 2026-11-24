"""Unit and integration tests for qBittorrent Web API v2 emulation layer."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from app.config import Settings
from app.db import JobStore, reset_job_store
from app.main import JOBS, app
from app.schemas import DownloadJob, JobStatus, LinkType
from app.torrent_utils import (
    create_magnet_from_torrent,
    extract_magnet_hash,
    extract_magnet_name,
    extract_torrent_metadata,
)
from fastapi.testclient import TestClient


@pytest.fixture
def clean_job_store(tmp_path: Path):
    """Fixture providing an isolated in-memory or temporary SQLite store."""
    db_file = tmp_path / "test_qb_jobs.db"
    store = JobStore(db_path=db_file)
    reset_job_store(store)
    JOBS.clear()
    yield store
    store.close()
    reset_job_store(None)
    JOBS.clear()


@pytest.fixture
def client(clean_job_store):
    """Test client for FastAPI app."""
    return TestClient(app)


def test_torrent_utils_magnet():
    """Verify magnet hash extraction and name decoding."""
    # 40-char hex
    url1 = "magnet:?xt=urn:btih:61bc4c810d29759ad0034a70be6550b07042a59a&dn=Sample+Movie"
    assert extract_magnet_hash(url1) == "61bc4c810d29759ad0034a70be6550b07042a59a"
    assert extract_magnet_name(url1) == "Sample Movie"

    # 32-char base32
    # 20 bytes -> base32 encoded
    raw_bytes = bytes.fromhex("61bc4c810d29759ad0034a70be6550b07042a59a")
    b32 = base64.b32encode(raw_bytes).decode("ascii")
    url2 = f"magnet:?xt=urn:btih:{b32}&dn=Base32Movie"
    assert extract_magnet_hash(url2) == "61bc4c810d29759ad0034a70be6550b07042a59a"
    assert extract_magnet_name(url2) == "Base32Movie"

    # Non-magnet
    assert extract_magnet_hash("https://example.com/file.zip") is None


def test_torrent_utils_bdecode():
    """Verify bencoded torrent info-hash and name extraction."""
    sample_torrent = b"d4:infod4:name12:Ubuntu.Linux12:piece lengthi16384e6:pieces20:12345678901234567890ee"
    info_hash, name = extract_torrent_metadata(sample_torrent)
    assert len(info_hash) == 40
    assert name == "Ubuntu.Linux"

    magnet = create_magnet_from_torrent(info_hash, name)
    assert magnet.startswith(f"magnet:?xt=urn:btih:{info_hash}")
    assert "Ubuntu.Linux" in magnet


def test_qb_login_and_logout(client: TestClient):
    """Test login and logout endpoints with default settings (no auth key)."""
    res = client.post("/api/v2/auth/login", data={"username": "admin", "password": "adminadmin"})
    assert res.status_code == 200
    assert res.text == "Ok."
    assert "SID" in res.cookies

    res_logout = client.post("/api/v2/auth/logout")
    assert res_logout.status_code == 200
    assert res_logout.text == "Ok."


def test_qb_login_with_auth_protection(client: TestClient):
    """Test login behavior when API_AUTH_KEY is configured."""
    from app.config import get_settings

    custom_settings = Settings(api_auth_key="secret123")
    app.dependency_overrides[get_settings] = lambda: custom_settings
    try:
        # Failed login
        fail_res = client.post("/api/v2/auth/login", data={"username": "admin", "password": "wrongpassword"})
        assert fail_res.status_code == 200
        assert fail_res.text == "Fails."

        # Successful login with matching password
        ok_res = client.post("/api/v2/auth/login", data={"username": "admin", "password": "secret123"})
        assert ok_res.status_code == 200
        assert ok_res.text == "Ok."
        assert ok_res.cookies.get("SID") == "debrid-hub-session"

        # Verify both new and legacy SID cookies authenticate requests
        client.cookies.set("SID", "debrid-hub-session")
        assert client.get("/api/v2/torrents/info").status_code == 200
        client.cookies.set("SID", "debrid-nas-auth-token")
        assert client.get("/api/v2/torrents/info").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_qb_app_version_and_preferences(client: TestClient):
    """Test app version, webapiVersion, preferences, and defaultSavePath."""
    v_res = client.get("/api/v2/app/version")
    assert v_res.status_code == 200
    assert "v4." in v_res.text

    api_res = client.get("/api/v2/app/webapiVersion")
    assert api_res.status_code == 200
    assert "2." in api_res.text

    pref_res = client.get("/api/v2/app/preferences")
    assert pref_res.status_code == 200
    prefs = pref_res.json()
    assert "save_path" in prefs

    path_res = client.get("/api/v2/app/defaultSavePath")
    assert path_res.status_code == 200
    assert len(path_res.text) > 0


def test_qb_torrents_add_magnet(client: TestClient, clean_job_store: JobStore):
    """Test adding magnet links via /api/v2/torrents/add as sent by Sonarr."""
    with patch("app.main.process_download_job", new_callable=AsyncMock):
        magnet = "magnet:?xt=urn:btih:61bc4c810d29759ad0034a70be6550b07042a59a&dn=Severance.S02E01"
        res = client.post(
            "/api/v2/torrents/add",
            data={
                "urls": magnet,
                "category": "tv-sonarr",
                "savepath": "/downloads/tv",
            },
        )
        assert res.status_code == 200
        assert res.text == "Ok."

        jobs = clean_job_store.list_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.category == "tv-sonarr"
        assert job.source == "sonarr"
        assert job.info_hash == "61bc4c810d29759ad0034a70be6550b07042a59a"
        assert job.link_type == LinkType.MAGNET
        assert "Severance" in job.filenames[0]


def test_qb_torrents_add_torrent_file(client: TestClient, clean_job_store: JobStore):
    """Test uploading a .torrent file via /api/v2/torrents/add as sent by Radarr."""
    with patch("app.main.process_download_job", new_callable=AsyncMock):
        torrent_bytes = b"d4:infod4:name17:Dune.Part.Two.mkv12:piece lengthi16384e6:pieces20:12345678901234567890ee"
        files = {"torrents": ("dune.torrent", torrent_bytes, "application/x-bittorrent")}
        res = client.post(
            "/api/v2/torrents/add",
            data={
                "category": "radarr",
                "savepath": "/downloads/movies",
            },
            files=files,
        )
        assert res.status_code == 200
        assert res.text == "Ok."

        jobs = clean_job_store.list_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.category == "radarr"
        assert job.source == "radarr"
        assert job.link_type == LinkType.TORRENT
        assert job.filenames == ["Dune.Part.Two.mkv"]


def test_qb_torrents_info_and_filters(client: TestClient, clean_job_store: JobStore):
    """Test mapping and filtering jobs in /api/v2/torrents/info."""
    job1 = DownloadJob(
        job_id="job-1",
        source_url="magnet:?xt=urn:btih:1111111111111111111111111111111111111111&dn=Show1",
        link_type=LinkType.MAGNET,
        status=JobStatus.DOWNLOADING,
        output_dir="/downloads/tv",
        filenames=["Show1.mkv"],
        progress_percent=55.0,
        category="tv",
        source="sonarr",
        info_hash="1111111111111111111111111111111111111111",
    )
    job2 = DownloadJob(
        job_id="job-2",
        source_url="magnet:?xt=urn:btih:2222222222222222222222222222222222222222&dn=Movie1",
        link_type=LinkType.MAGNET,
        status=JobStatus.COMPLETED,
        output_dir="/downloads/movies",
        filenames=["Movie1.mkv"],
        progress_percent=100.0,
        category="movies",
        source="radarr",
        info_hash="2222222222222222222222222222222222222222",
    )
    clean_job_store.upsert_job(job1)
    clean_job_store.upsert_job(job2)

    # 1. Query all
    res = client.get("/api/v2/torrents/info")
    assert res.status_code == 200
    torrents = res.json()
    assert len(torrents) == 2

    # Verify field mapping
    t1 = next(t for t in torrents if t["hash"] == "1111111111111111111111111111111111111111")
    assert t1["state"] == "downloading"
    assert t1["progress"] == 0.55
    assert t1["category"] == "tv"
    assert t1["tags"] == "sonarr"
    assert t1["name"] == "Show1.mkv"

    t2 = next(t for t in torrents if t["hash"] == "2222222222222222222222222222222222222222")
    assert t2["state"] == "pausedUP"  # Completed state for Sonarr
    assert t2["progress"] == 1.0

    # 2. Filter by category
    res_tv = client.get("/api/v2/torrents/info?category=tv")
    assert res_tv.status_code == 200
    tv_torrents = res_tv.json()
    assert len(tv_torrents) == 1
    assert tv_torrents[0]["hash"] == "1111111111111111111111111111111111111111"

    # 3. Filter by hash
    res_hash = client.get("/api/v2/torrents/info?hashes=2222222222222222222222222222222222222222")
    assert res_hash.status_code == 200
    hash_torrents = res_hash.json()
    assert len(hash_torrents) == 1
    assert hash_torrents[0]["name"] == "Movie1.mkv"


def test_qb_torrents_delete(client: TestClient, clean_job_store: JobStore):
    """Test deleting torrents via /api/v2/torrents/delete."""
    job = DownloadJob(
        job_id="job-del-1",
        source_url="magnet:?xt=urn:btih:3333333333333333333333333333333333333333",
        link_type=LinkType.MAGNET,
        status=JobStatus.COMPLETED,
        output_dir="/downloads",
        info_hash="3333333333333333333333333333333333333333",
    )
    clean_job_store.upsert_job(job)

    assert clean_job_store.get_job_by_hash("3333333333333333333333333333333333333333") is not None

    del_res = client.post(
        "/api/v2/torrents/delete",
        data={"hashes": "3333333333333333333333333333333333333333", "deleteFiles": "false"},
    )
    assert del_res.status_code == 200
    assert del_res.text == "Ok."

    assert clean_job_store.get_job_by_hash("3333333333333333333333333333333333333333") is None


def test_qb_torrents_properties_and_files(client: TestClient, clean_job_store: JobStore):
    """Test /api/v2/torrents/properties and /api/v2/torrents/files endpoints."""
    job = DownloadJob(
        job_id="job-prop-1",
        source_url="magnet:?xt=urn:btih:4444444444444444444444444444444444444444",
        link_type=LinkType.MAGNET,
        status=JobStatus.COMPLETED,
        output_dir="/downloads/tv",
        filenames=["Episode1.mkv"],
        progress_percent=100.0,
        info_hash="4444444444444444444444444444444444444444",
    )
    clean_job_store.upsert_job(job)

    prop_res = client.get("/api/v2/torrents/properties?hash=4444444444444444444444444444444444444444")
    assert prop_res.status_code == 200
    props = prop_res.json()
    assert props["save_path"] == "/downloads/tv"
    assert props["pieces_have"] == 100

    files_res = client.get("/api/v2/torrents/files?hash=4444444444444444444444444444444444444444")
    assert files_res.status_code == 200
    files = files_res.json()
    assert len(files) == 1
    assert files[0]["name"] == "Episode1.mkv"
    assert files[0]["progress"] == 1.0
