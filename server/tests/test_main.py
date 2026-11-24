"""Unit and integration tests for FastAPI application and background pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

import pytest
from app.config import Settings, get_settings
from app.db import get_job_store
from app.events import EVENT_BUS
from app.main import (
    ACTIVE_TASKS,
    ARCHIVE_LOCKS,
    EXTRACTED_ARCHIVE_SIGNATURES,
    JOBS,
    app,
    get_download_semaphore,
    get_effective_rd_key,
    process_download_job,
    sanitize_subfolder,
)
from app.schemas import DownloadJob, JobStatus, LinkType, ResolvedFile
from fastapi.testclient import TestClient

client = TestClient(app)


def setup_function() -> None:
    JOBS.clear()
    ACTIVE_TASKS.clear()
    ARCHIVE_LOCKS.clear()
    EXTRACTED_ARCHIVE_SIGNATURES.clear()
    get_job_store().clear_jobs(clear_all=True)
    import app.main as main_module
    main_module.DOWNLOAD_SEMAPHORE = None
    EVENT_BUS._subscribers.clear()
    EVENT_BUS._last_progress_broadcast.clear()
    main_module.app.dependency_overrides.clear()


def test_health_check() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.2.0"
    assert "commit" in data


def test_create_download_magnet() -> None:
    response = client.post(
        "/api/download",
        json={"url": "magnet:?xt=urn:btih:1234567890abcdef", "apiKey": "test_key"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["link_type"] == "magnet"
    assert data["status"] == "queued"
    assert "job_id" in data


def test_create_download_magnet_with_dn_populates_filenames() -> None:
    response = client.post(
        "/api/download",
        json={"url": "magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678&dn=Tailored+Realities+Sanderson"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    status_resp = client.get(f"/api/status/{job_id}")
    assert status_resp.status_code == 200
    job_data = status_resp.json()
    assert job_data["filenames"] == ["Tailored Realities Sanderson"]


def test_create_download_raw_info_hash() -> None:
    response = client.post(
        "/api/download",
        json={"url": "4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["link_type"] == "magnet"
    job_id = data["job_id"]

    status_resp = client.get(f"/api/status/{job_id}")
    assert status_resp.status_code == 200
    job_data = status_resp.json()
    assert job_data["source_url"] == "magnet:?xt=urn:btih:4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
    assert job_data["info_hash"] == "4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"


def test_list_jobs() -> None:
    client.post("/api/download", json={"url": "https://1fichier.com/?abcdef123"})
    response = client.get("/api/status")
    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["link_type"] == "hoster"


def test_get_job_status_found_and_not_found() -> None:
    # 404 for unknown job
    resp_404 = client.get("/api/status/unknown-uuid")
    assert resp_404.status_code == 404

    # 200 for created job
    with patch("app.main.process_download_job"):
        create_resp = client.post("/api/download", json={"url": "https://rapidgator.net/file/123"})
        job_id = create_resp.json()["job_id"]

        resp_200 = client.get(f"/api/status/{job_id}")
        assert resp_200.status_code == 200
        job_data = resp_200.json()
        assert job_data["job_id"] == job_id
        assert job_data["status"] == "queued"
        assert "extracted_files" in job_data


def test_delete_job_by_id() -> None:
    job = DownloadJob(
        job_id="job-to-delete",
        source_url="https://1fichier.com/?del1",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.COMPLETED,
    )
    JOBS["job-to-delete"] = job

    # 404 on non-existent job
    resp_404 = client.delete("/api/status/unknown-job")
    assert resp_404.status_code == 404

    # 200 on existing job
    resp_200 = client.delete("/api/status/job-to-delete")
    assert resp_200.status_code == 200
    assert resp_200.json() == {"message": "Job deleted", "job_id": "job-to-delete"}
    assert "job-to-delete" not in JOBS


def test_clear_jobs_default_terminal() -> None:
    JOBS["job-completed"] = DownloadJob(
        job_id="job-completed",
        source_url="https://1fichier.com/?1",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.COMPLETED,
    )
    JOBS["job-failed"] = DownloadJob(
        job_id="job-failed",
        source_url="https://1fichier.com/?2",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.FAILED,
    )
    JOBS["job-downloading"] = DownloadJob(
        job_id="job-downloading",
        source_url="https://1fichier.com/?3",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.DOWNLOADING,
    )
    JOBS["job-queued"] = DownloadJob(
        job_id="job-queued",
        source_url="https://1fichier.com/?4",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.QUEUED,
    )

    resp = client.delete("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 2
    assert data["remaining"] == 2
    assert "job-completed" not in JOBS
    assert "job-failed" not in JOBS
    assert "job-downloading" in JOBS
    assert "job-queued" in JOBS


def test_clear_jobs_with_status_filter() -> None:
    JOBS["job-failed1"] = DownloadJob(
        job_id="job-failed1",
        source_url="https://1fichier.com/?1",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.FAILED,
    )
    JOBS["job-completed"] = DownloadJob(
        job_id="job-completed",
        source_url="https://1fichier.com/?2",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.COMPLETED,
    )

    # Filter to only delete failed
    resp = client.delete("/api/status?status=failed")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1, "remaining": 1}
    assert "job-failed1" not in JOBS
    assert "job-completed" in JOBS


def test_auth_protection() -> None:
    mock_settings = Settings(api_auth_key="secret123", rd_api_key="rd_token")
    app.dependency_overrides[get_settings] = lambda: mock_settings
    try:
        # Missing auth
        res_missing = client.post("/api/download", json={"url": "https://1fichier.com/?123"})
        assert res_missing.status_code == 401

        # Wrong auth
        res_wrong = client.post(
            "/api/download",
            json={"url": "https://1fichier.com/?123", "apiKey": "wrong_key"},
        )
        assert res_wrong.status_code == 401

        # Matching payload apiKey
        res_payload_auth = client.post(
            "/api/download",
            json={"url": "https://1fichier.com/?123", "apiKey": "secret123"},
        )
        assert res_payload_auth.status_code == 202

        # Matching X-API-Key header
        res_header_auth = client.post(
            "/api/download",
            headers={"X-API-Key": "secret123"},
            json={"url": "https://1fichier.com/?123"},
        )
        assert res_header_auth.status_code == 202

        # GET endpoints auth protection
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/status", headers={"X-API-Key": "secret123"}).status_code == 200
        assert client.get("/api/status/some-id").status_code == 401
        assert client.get("/api/status/some-id", headers={"X-API-Key": "secret123"}).status_code == 404

        # DELETE endpoints auth protection
        assert client.delete("/api/status/some-id").status_code == 401
        assert client.delete("/api/status/some-id", headers={"X-API-Key": "secret123"}).status_code == 404
        assert client.delete("/api/status").status_code == 401
        assert client.delete("/api/status", headers={"X-API-Key": "secret123"}).status_code == 200

        # Settings endpoints auth protection
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/settings", headers={"X-API-Key": "secret123"}).status_code == 200
        assert client.put("/api/settings", json={"debrid_provider": "realdebrid"}).status_code == 401
        assert client.put("/api/settings", headers={"X-API-Key": "secret123"}, json={"debrid_provider": "realdebrid"}).status_code == 200
        assert client.post("/api/settings/test-debrid").status_code == 401
        assert client.post("/api/settings/test-debrid", headers={"X-API-Key": "secret123"}).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_sanitize_subfolder() -> None:
    base = "/downloads"
    assert sanitize_subfolder(base, None) == "/downloads"
    assert sanitize_subfolder(base, "") == "/downloads"
    assert sanitize_subfolder(base, "switch") == "/downloads/switch"
    assert sanitize_subfolder(base, "switch/games") == "/downloads/switch/games"
    assert sanitize_subfolder(base, "../../etc/passwd") == "/downloads/etc/passwd"
    assert sanitize_subfolder(base, "../../../") == "/downloads"


def test_get_effective_rd_key() -> None:
    settings = Settings(api_auth_key="nas_auth", rd_api_key="default_rd")
    # Payload key used as auth -> falls back to settings.rd_api_key
    assert get_effective_rd_key("nas_auth", settings) == "default_rd"
    # Specific payload key override
    assert get_effective_rd_key("custom_rd_key", settings) == "custom_rd_key"
    # No payload key -> falls back to settings.rd_api_key
    assert get_effective_rd_key(None, settings) == "default_rd"


@pytest.mark.asyncio
async def test_concurrency_semaphore_limits_parallel_execution() -> None:
    sem = get_download_semaphore(1)
    assert sem._value <= 1


@pytest.mark.asyncio
async def test_process_download_job_success_without_extract() -> None:
    job = DownloadJob(
        job_id="job-1",
        source_url="https://rapidgator.net/file/123",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
    )
    JOBS["job-1"] = job

    settings = Settings(auto_extract=False, rd_api_key="test_rd_key")

    with (
        patch("app.main.RealDebridClient.resolve_to_direct_files", new_callable=AsyncMock) as mock_resolve,
        patch("app.main.Aria2Downloader.download", new_callable=AsyncMock) as mock_download,
    ):
        mock_resolve.return_value = [
            ResolvedFile(url="https://cdn.rd.com/file1.iso", filename="file1.iso", filesize=1024)
        ]

        def verify_filenames_during_download(*args: object, **kwargs: object) -> str:
            # Assert filenames is already populated with the resolved file names before download finishes
            assert job.filenames == ["file1.iso"]
            assert job.status == JobStatus.DOWNLOADING
            return "/downloads/file1.iso"

        mock_download.side_effect = verify_filenames_during_download

        await process_download_job("job-1", "test_rd_key", settings)

        assert job.status == JobStatus.COMPLETED
        assert job.resolved_urls == ["https://cdn.rd.com/file1.iso"]
        assert job.filenames == ["file1.iso"]
        assert job.total_files == 1
        assert job.completed_files == 1
        assert job.progress_percent == 100.0
        assert job.error is None
        mock_resolve.assert_called_once_with("https://rapidgator.net/file/123")
        mock_download.assert_called_once_with(
            "https://cdn.rd.com/file1.iso",
            output_dir="/downloads",
            filename="file1.iso",
            on_progress=ANY,
        )


@pytest.mark.asyncio
async def test_process_download_job_success_with_auto_extract() -> None:
    job = DownloadJob(
        job_id="job-2",
        source_url="magnet:?xt=urn:btih:xyz",
        link_type=LinkType.MAGNET,
        output_dir="/downloads/games",
    )
    JOBS["job-2"] = job

    settings = Settings(auto_extract=True, cleanup_after_extract=True, rd_api_key="test_rd_key")

    with (
        patch("app.main.RealDebridClient.resolve_to_direct_files", new_callable=AsyncMock) as mock_resolve,
        patch("app.main.Aria2Downloader.download", new_callable=AsyncMock) as mock_download,
        patch("app.main.ArchiveExtractor.extract_and_cleanup", new_callable=AsyncMock) as mock_extract,
    ):
        mock_resolve.return_value = [
            ResolvedFile(
                url="https://cdn.rd.com/game.part01.rar",
                filename="game.part01.rar",
                filesize=2048,
            )
        ]
        mock_download.return_value = "/downloads/games/game.part01.rar"
        mock_extract.return_value = [Path("/downloads/games/game.iso")]

        await process_download_job("job-2", "test_rd_key", settings)

        assert job.status == JobStatus.COMPLETED
        assert job.filenames == ["game.part01.rar"]
        assert job.extracted_files == ["game.iso"]
        assert job.total_files == 1
        assert job.completed_files == 1
        assert job.progress_percent == 100.0
        mock_extract.assert_called_once_with(
            Path("/downloads/games/game.part01.rar"),
            destination_dir=Path("/downloads/games"),
        )


@pytest.mark.asyncio
async def test_process_download_job_failure() -> None:
    job = DownloadJob(
        job_id="job-3",
        source_url="https://rapidgator.net/file/bad",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
    )
    JOBS["job-3"] = job

    settings = Settings(rd_api_key="test_rd_key")

    with patch(
        "app.main.RealDebridClient.resolve_to_direct_files",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Real-Debrid error: Link unrestrict failed"),
    ):
        await process_download_job("job-3", "test_rd_key", settings)

        assert job.status == JobStatus.FAILED
        assert "Real-Debrid error: Link unrestrict failed" in (job.error or "")


@pytest.mark.asyncio
async def test_process_download_job_unknown_job() -> None:
    settings = Settings(rd_api_key="test_rd_key")
    # Must not raise an exception when job_id not found in JOBS
    await process_download_job("unknown-id", "test_rd_key", settings)


def test_get_settings_endpoint() -> None:
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "debrid_provider" in data
    assert "rd_api_key_masked" in data
    assert "aria2c_max_connections" in data
    assert "supported_providers" in data
    assert "realdebrid" in data["supported_providers"]


def test_update_settings_endpoint(tmp_path: Path) -> None:
    settings_file = str(tmp_path / "settings.json")
    with (
        patch("app.config.SETTINGS_INSTANCE", None),
        patch.dict("os.environ", {"CONFIG_FILE": settings_file}),
    ):
        update_resp = client.put(
            "/api/settings",
            json={
                "debrid_provider": "realdebrid",
                "rd_api_key": "new_secret_key_12345",
                "aria2c_max_connections": 8,
                "max_concurrent_downloads": 4,
            },
        )
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data["debrid_provider"] == "realdebrid"
        assert data["aria2c_max_connections"] == 8
        assert data["max_concurrent_downloads"] == 4
        assert data["rd_api_key_masked"].endswith("2345")


def test_test_debrid_endpoint_success() -> None:
    with patch("app.main.get_debrid_provider") as mock_get_provider:
        mock_provider = AsyncMock()
        mock_provider.test_account.return_value = {
            "valid": True,
            "username": "premium_user",
            "type": "premium",
            "expiration": "2029-12-31T23:59:59Z",
        }
        mock_get_provider.return_value = mock_provider

        resp = client.post(
            "/api/settings/test-debrid",
            json={"provider": "realdebrid", "apiKey": "valid_token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["provider"] == "realdebrid"
        assert data["username"] == "premium_user"
        assert data["account_type"] == "premium"


def test_test_debrid_endpoint_missing_key() -> None:
    with patch("app.main.get_settings") as mock_get_settings:
        mock_get_settings.return_value = Settings(rd_api_key="")
        resp = client.post("/api/settings/test-debrid", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "No API key" in data["message"]


def test_settings_html_dashboard() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache, no-store, must-revalidate"
    assert "Debrid Hub Settings" in resp.text
    assert 'data-tab="jobs"' in resp.text
    assert 'data-tab="add"' in resp.text
    assert 'data-tab="settings"' in resp.text
    assert 'id="tab-jobs"' in resp.text
    assert 'id="tab-add"' in resp.text
    assert 'id="tab-settings"' in resp.text
    assert "quick-download-form" in resp.text
    assert '/static/dashboard.css?v=' in resp.text
    assert '/static/dashboard.js?v=' in resp.text
    assert '<style>' not in resp.text and "<style>" not in resp.text
    assert 'id="filter-active-btn"' in resp.text
    assert 'id="filter-completed-btn"' in resp.text
    assert 'id="filter-failed-btn"' in resp.text
    assert 'id="stat-active-count"' in resp.text
    assert 'class="summary-pill"' in resp.text
    assert "Debrid Hub v" in resp.text
    assert 'id="key-card"' in resp.text
    assert 'id="active-key-masked"' in resp.text
    assert 'id="btn-edit-key"' in resp.text
    assert 'id="key-edit-box"' in resp.text
    assert 'id="btn-toggle-key-visibility"' in resp.text
    assert 'id="btn-cancel-edit-key"' in resp.text
    assert len(resp.text.splitlines()) < 220

    resp_settings = client.get("/settings")
    assert resp_settings.status_code == 200
    assert "Debrid Hub Settings" in resp_settings.text
    assert "Download Threads" in resp_settings.text
    assert "Max Concurrent Downloads" in resp_settings.text

    resp_dashboard = client.get("/dashboard")
    assert resp_dashboard.status_code == 200
    assert "Debrid Hub" in resp_dashboard.text

    resp_css = client.get("/static/dashboard.css")
    assert resp_css.status_code == 200
    assert "--bg:" in resp_css.text

    resp_js = client.get("/static/dashboard.js")
    assert resp_js.status_code == 200
    assert "quick-download-form" in resp_js.text


@pytest.mark.asyncio
async def test_process_download_job_success_with_zip_and_7z_auto_extract() -> None:
    job = DownloadJob(
        job_id="job-zip",
        source_url="https://hoster.com/archive.zip",
        link_type=LinkType.HOSTER,
        output_dir="/downloads/archives",
    )
    JOBS["job-zip"] = job

    settings = Settings(auto_extract=True, cleanup_after_extract=True, rd_api_key="test_rd_key")

    with (
        patch("app.main.RealDebridClient.resolve_to_direct_files", new_callable=AsyncMock) as mock_resolve,
        patch("app.main.Aria2Downloader.download", new_callable=AsyncMock) as mock_download,
        patch("app.main.ArchiveExtractor.extract_and_cleanup", new_callable=AsyncMock) as mock_extract,
    ):
        mock_resolve.return_value = [
            ResolvedFile(url="https://cdn.rd.com/package.zip", filename="package.zip", filesize=1024),
            ResolvedFile(url="https://cdn.rd.com/roms.7z", filename="roms.7z", filesize=2048),
        ]
        mock_download.side_effect = [
            "/downloads/archives/package.zip",
            "/downloads/archives/roms.7z",
        ]
        mock_extract.side_effect = [
            [Path("/downloads/archives/extracted1.bin")],
            [Path("/downloads/archives/extracted2.bin")],
        ]

        await process_download_job("job-zip", "test_rd_key", settings)

        assert job.status == JobStatus.COMPLETED
        assert job.filenames == ["package.zip", "roms.7z"]
        assert job.extracted_files == ["extracted1.bin", "extracted2.bin"]
        assert mock_extract.call_count == 2


def test_delete_job_cancels_running_task() -> None:
    from unittest.mock import MagicMock

    job = DownloadJob(
        job_id="job-cancel",
        source_url="https://hoster.com/huge.bin",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.DOWNLOADING,
    )
    JOBS["job-cancel"] = job

    mock_task = MagicMock()
    mock_task.done.return_value = False
    ACTIVE_TASKS["job-cancel"] = mock_task

    resp = client.delete("/api/status/job-cancel")
    assert resp.status_code == 200
    assert "job-cancel" not in JOBS
    mock_task.cancel.assert_called_once()


def test_clear_all_jobs_requires_confirmation() -> None:
    # 400 when confirm is false or absent
    resp = client.delete("/api/status/all")
    assert resp.status_code == 400
    assert "confirm=true" in resp.json()["detail"]


def test_clear_all_jobs_success() -> None:
    from unittest.mock import MagicMock

    store = get_job_store()
    job = DownloadJob(
        job_id="job-running",
        source_url="https://hoster.com/file.bin",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.DOWNLOADING,
    )
    store.upsert_job(job)
    JOBS["job-running"] = job

    mock_task = MagicMock()
    mock_task.done.return_value = False
    ACTIVE_TASKS["job-running"] = mock_task

    resp = client.delete("/api/status/all?confirm=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 1
    assert data["remaining"] == 0
    assert "job-running" not in JOBS
    mock_task.cancel.assert_called_once()


def test_retry_job_success() -> None:
    store = get_job_store()
    job = DownloadJob(
        job_id="job-retry",
        source_url="https://hoster.com/broken.rar",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.FAILED,
        error="Network error",
        progress_percent=45.0,
    )
    store.upsert_job(job)
    JOBS["job-retry"] = job

    with patch("app.main.process_download_job"):
        resp = client.post("/api/status/job-retry/retry")
        assert resp.status_code == 202
        data = resp.json()
        assert data["job_id"] == "job-retry"
        assert data["status"] == "queued"
        assert data["progress_percent"] == 0.0
        assert data["error"] is None


def test_retry_job_not_found() -> None:
    resp = client.post("/api/status/unknown-job/retry")
    assert resp.status_code == 404


def test_retry_job_already_running() -> None:
    from unittest.mock import MagicMock

    store = get_job_store()
    job = DownloadJob(
        job_id="job-active",
        source_url="https://hoster.com/active.rar",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.DOWNLOADING,
    )
    store.upsert_job(job)
    JOBS["job-active"] = job

    mock_task = MagicMock()
    mock_task.done.return_value = False
    ACTIVE_TASKS["job-active"] = mock_task

    resp = client.post("/api/status/job-active/retry")
    assert resp.status_code == 400
    assert "running" in resp.json()["detail"].lower()


def test_list_jobs_filtering_and_pagination() -> None:
    store = get_job_store()
    for i in range(4):
        st = JobStatus.COMPLETED if i < 2 else JobStatus.FAILED
        j = DownloadJob(
            job_id=f"job-list-{i}",
            source_url=f"https://ex.com/{i}",
            link_type=LinkType.HOSTER,
            output_dir="/downloads",
            status=st,
        )
        store.upsert_job(j)

    # Filter completed
    resp = client.get("/api/status?status=completed")
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 2
    assert all(j["status"] == "completed" for j in jobs)

    # Pagination
    resp_page = client.get("/api/status?limit=1&offset=0")
    assert resp_page.status_code == 200
    assert len(resp_page.json()) == 1


@pytest.mark.asyncio
async def test_job_event_bus_subscribe_publish_and_throttle() -> None:
    from app.events import JobEventBus

    bus = JobEventBus()
    queue = bus.subscribe()
    assert queue in bus._subscribers

    # Immediate status change event
    bus.publish("job_update", {"job_id": "job-1", "status": "downloading"}, job_id="job-1", is_progress=False)
    received1 = await queue.get()
    assert "event: job_update" in received1
    assert "downloading" in received1

    # Immediate second event with different status
    bus.publish("job_update", {"job_id": "job-1", "status": "extracting"}, job_id="job-1", is_progress=False)
    received2 = await queue.get()
    assert "event: job_update" in received2
    assert "extracting" in received2

    # High frequency progress updates for the same status - second within 1s is throttled
    bus.publish(
        "job_update",
        {"job_id": "job-2", "status": "downloading", "progress_percent": 10.0},
        job_id="job-2",
        is_progress=True,
    )
    bus.publish(
        "job_update",
        {"job_id": "job-2", "status": "downloading", "progress_percent": 11.0},
        job_id="job-2",
        is_progress=True,
    )
    received_prog1 = await queue.get()
    assert "10.0" in received_prog1
    assert queue.empty()

    # Unsubscribe
    bus.unsubscribe(queue)
    assert queue not in bus._subscribers


def test_status_stream_receives_init() -> None:
    store = get_job_store()
    job = DownloadJob(
        job_id="job-stream-1",
        source_url="magnet:?xt=urn:btih:stream1",
        link_type=LinkType.MAGNET,
        output_dir="/downloads",
        status=JobStatus.QUEUED,
    )
    store.upsert_job(job)

    with client.stream("GET", "/api/status/stream") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        lines = []
        for line in response.iter_lines():
            if line:
                lines.append(line)
                if "event: init" in line:
                    break
        assert any("event: init" in l for l in lines)


def test_status_stream_auth_protection() -> None:
    mock_settings = Settings(
        rd_api_key="rd_secret",
        api_auth_key="nas_auth_token",
        download_dir="/downloads",
        aria2c_max_connections=16,
        max_concurrent_downloads=3,
        auto_extract=True,
        cleanup_after_extract=True,
    )
    app.dependency_overrides[get_settings] = lambda: mock_settings
    try:
        # 401 when no key provided
        resp_unauth = client.get("/api/status/stream")
        assert resp_unauth.status_code == 401

        # 200 via X-API-Key header
        with client.stream("GET", "/api/status/stream", headers={"X-API-Key": "nas_auth_token"}) as resp_hdr:
            assert resp_hdr.status_code == 200

        # 200 via ?apiKey= query parameter
        with client.stream("GET", "/api/status/stream?apiKey=nas_auth_token") as resp_query:
            assert resp_query.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_status_stream_disconnect_cleanup() -> None:
    assert len(EVENT_BUS._subscribers) == 0

    with client.stream("GET", "/api/status/stream") as response:
        assert response.status_code == 200
        assert len(EVENT_BUS._subscribers) == 1
        for line in response.iter_lines():
            if line and "event: init" in line:
                break

    assert len(EVENT_BUS._subscribers) == 0


def test_clear_jobs_publishes_selective_cleared_event() -> None:
    queue = EVENT_BUS.subscribe()
    store = get_job_store()
    job1 = DownloadJob(
        job_id="job-c1",
        source_url="https://ex.com/1",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.COMPLETED,
    )
    job2 = DownloadJob(
        job_id="job-d1",
        source_url="https://ex.com/2",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
        status=JobStatus.DOWNLOADING,
    )
    store.upsert_job(job1)
    store.upsert_job(job2)
    JOBS["job-c1"] = job1
    JOBS["job-d1"] = job2

    resp = client.delete("/api/status")
    assert resp.status_code == 200
    assert not queue.empty()
    msg = queue.get_nowait()
    assert "jobs_cleared" in msg
    assert '"all": false' in msg
    EVENT_BUS.unsubscribe(queue)


def test_upload_torrent_success() -> None:
    """Verify uploading a valid .torrent file queues a download job with metadata."""
    sample_torrent = b"d4:infod4:name12:Ubuntu.Linux12:piece lengthi16384e6:pieces20:12345678901234567890ee"
    files = {"file": ("ubuntu.torrent", sample_torrent, "application/x-bittorrent")}

    with patch("app.main.process_download_job"):
        resp = client.post("/api/upload/torrent", files=files)
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["link_type"] == "torrent"
        assert "Ubuntu.Linux" in data["message"]
        job_id = data["job_id"]
        assert job_id in JOBS
        job = JOBS[job_id]
        assert job.link_type == LinkType.TORRENT
        assert job.filenames == ["Ubuntu.Linux"]
        assert job.source == "upload"
        assert job.source_url.startswith("magnet:?xt=urn:btih:")
        assert "Ubuntu.Linux" in job.source_url


def test_upload_torrent_with_subfolder_and_category() -> None:
    """Verify subfolder and category form parameters are respected on torrent upload."""
    sample_torrent = b"d4:infod4:name12:Ubuntu.Linux12:piece lengthi16384e6:pieces20:12345678901234567890ee"
    files = {"file": ("ubuntu.torrent", sample_torrent, "application/x-bittorrent")}
    data = {"subfolder": "isos/linux", "category": "iso", "source": "web_ui"}

    with patch("app.main.process_download_job"):
        resp = client.post("/api/upload/torrent", files=files, data=data)
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]
        job = JOBS[job_id]
        assert job.output_dir.endswith("isos/linux")
        assert job.category == "iso"
        assert job.source == "web_ui"


def test_upload_torrent_empty_file() -> None:
    """Verify empty torrent file returns 400 Bad Request."""
    files = {"file": ("empty.torrent", b"", "application/x-bittorrent")}
    resp = client.post("/api/upload/torrent", files=files)
    assert resp.status_code == 400
    assert "Empty torrent file" in resp.json()["detail"]


def test_upload_torrent_invalid_content() -> None:
    """Verify non-bencoded or malformed file returns 400 Bad Request."""
    files = {"file": ("invalid.torrent", b"not-a-torrent-file-content", "application/x-bittorrent")}
    resp = client.post("/api/upload/torrent", files=files)
    assert resp.status_code == 400
    assert "Invalid .torrent file" in resp.json()["detail"]


def test_upload_torrent_auth_protection() -> None:
    """Verify upload endpoint enforces API_AUTH_KEY when configured."""
    sample_torrent = b"d4:infod4:name12:Ubuntu.Linux12:piece lengthi16384e6:pieces20:12345678901234567890ee"
    files = lambda: {"file": ("ubuntu.torrent", sample_torrent, "application/x-bittorrent")}

    def mock_settings() -> Settings:
        return Settings(api_auth_key="secret-upload-key", rd_api_key="rd-key")

    app.dependency_overrides[get_settings] = mock_settings
    try:
        # 1. No key -> 401
        resp = client.post("/api/upload/torrent", files=files())
        assert resp.status_code == 401

        # 2. Wrong key -> 401
        resp = client.post("/api/upload/torrent", files=files(), headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

        # 3. Valid key in header -> 202
        with patch("app.main.process_download_job"):
            resp = client.post("/api/upload/torrent", files=files(), headers={"X-API-Key": "secret-upload-key"})
            assert resp.status_code == 202

        # 4. Valid key in form field -> 202
        with patch("app.main.process_download_job"):
            resp = client.post("/api/upload/torrent", files=files(), data={"apiKey": "secret-upload-key"})
            assert resp.status_code == 202
    finally:
        app.dependency_overrides.clear()


def test_download_job_source_capture_methods() -> None:
    """Verify source field tagging for various capture methods."""
    with patch("app.main.process_download_job"):
        # 1. Default when omitted is 'paste'
        resp1 = client.post("/api/download", json={"url": "https://1fichier.com/?pastedefault"})
        assert resp1.status_code == 202
        job1 = JOBS[resp1.json()["job_id"]]
        assert job1.source == "paste"

        # 2. Explicit 'context_menu'
        resp2 = client.post("/api/download", json={"url": "https://1fichier.com/?contextmenu", "source": "context_menu"})
        assert resp2.status_code == 202
        job2 = JOBS[resp2.json()["job_id"]]
        assert job2.source == "context_menu"

        # 3. Explicit 'click_n_load'
        resp3 = client.post("/api/download", json={"url": "https://1fichier.com/?cnllink", "source": "click_n_load"})
        assert resp3.status_code == 202
        job3 = JOBS[resp3.json()["job_id"]]
        assert job3.source == "click_n_load"

        # 4. Explicit 'paste'
        resp4 = client.post("/api/download", json={"url": "https://1fichier.com/?pasteexplicit", "source": "paste"})
        assert resp4.status_code == 202
        job4 = JOBS[resp4.json()["job_id"]]
        assert job4.source == "paste"


def test_client_identity_ingestion() -> None:
    """Verify client_id and client_name are persisted on download and torrent upload."""
    with patch("app.main.process_download_job"):
        resp = client.post(
            "/api/download",
            json={
                "url": "https://1fichier.com/?identity1",
                "source": "context_menu",
                "client_id": "browser-uuid-abc",
                "client_name": "Mac Studio",
            },
        )
        assert resp.status_code == 202
        dl_job_id = resp.json()["job_id"]

        sample_torrent = b"d4:infod4:name12:Ubuntu.Linux12:piece lengthi16384e6:pieces20:12345678901234567890ee"
        files = {"file": ("ubuntu.torrent", sample_torrent, "application/x-bittorrent")}
        upload_resp = client.post(
            "/api/upload/torrent",
            files=files,
            data={"client_id": "browser-uuid-xyz", "client_name": "Firefox • Linux"},
        )
        assert upload_resp.status_code == 202
        up_job_id = upload_resp.json()["job_id"]

        dl_status = client.get(f"/api/status/{dl_job_id}")
        assert dl_status.status_code == 200
        assert dl_status.json()["client_id"] == "browser-uuid-abc"
        assert dl_status.json()["client_name"] == "Mac Studio"

        up_status = client.get(f"/api/status/{up_job_id}")
        assert up_status.status_code == 200
        assert up_status.json()["client_id"] == "browser-uuid-xyz"
        assert up_status.json()["client_name"] == "Firefox • Linux"


def _make_rar5_volume_bytes(volume_number: int, *, endarc_next_volume: bool, encrypted_headers: bool = False) -> bytes:
    """Create minimal valid RAR5 volume bytes for multi-job extraction pipeline tests."""
    magic = b"Rar!\x1a\x07\x01\x00"
    if encrypted_headers:
        body = b"\x04\x00\x00\x00"
        return magic + b"\x00\x00\x00\x00" + bytes([len(body)]) + body

    main_body = bytes([1, 0, 3, volume_number])  # HEAD_MAIN, flags=0, archive_flags=VOL|VOLNUM
    file_body = bytes([2, 0x02, 3, 0, 0]) + b"abc"  # HEAD_FILE with 3 bytes data
    endarc_body = bytes([5, 0, 1 if endarc_next_volume else 0])
    return (
        magic
        + b"\x00\x00\x00\x00"
        + bytes([len(main_body)])
        + main_body
        + b"\x00\x00\x00\x00"
        + bytes([len(file_body) - 3])
        + file_body
        + b"\x00\x00\x00\x00"
        + bytes([len(endarc_body)])
        + endarc_body
    )


@pytest.mark.asyncio
async def test_multipart_rar_defers_until_final_part_completes(tmp_path: Path) -> None:
    p1_path = tmp_path / "game.part01.rar"
    p2_path = tmp_path / "game.part02.rar"
    JOBS["job-p1"] = DownloadJob(
        job_id="job-p1",
        source_url="https://1fichier.com/?part1",
        link_type=LinkType.HOSTER,
        output_dir=str(tmp_path),
    )
    JOBS["job-p2"] = DownloadJob(
        job_id="job-p2",
        source_url="https://1fichier.com/?part2",
        link_type=LinkType.HOSTER,
        output_dir=str(tmp_path),
    )
    settings = Settings(auto_extract=True, cleanup_after_extract=True, rd_api_key="test_rd_key")

    async def fake_download(url: str, output_dir: str | None = None, filename: str | None = None, on_progress=None) -> str:
        if "part01" in url:
            p1_path.write_bytes(_make_rar5_volume_bytes(0, endarc_next_volume=True))
            return str(p1_path)
        p2_path.write_bytes(_make_rar5_volume_bytes(1, endarc_next_volume=False))
        return str(p2_path)

    async def fake_extract(archive_path: Path, destination_dir: Path) -> list[Path]:
        p1_path.unlink(missing_ok=True)
        p2_path.unlink(missing_ok=True)
        out = destination_dir / "game.iso"
        out.write_text("iso")
        return [out]

    with (
        patch(
            "app.main.RealDebridClient.resolve_to_direct_files",
            new_callable=AsyncMock,
            side_effect=[
                [ResolvedFile(url="https://cdn.rd.com/game.part01.rar", filename="game.part01.rar")],
                [ResolvedFile(url="https://cdn.rd.com/game.part02.rar", filename="game.part02.rar")],
            ],
        ),
        patch("app.main.Aria2Downloader.download", new_callable=AsyncMock, side_effect=fake_download),
        patch("app.main.ArchiveExtractor.extract_and_cleanup", new_callable=AsyncMock, side_effect=fake_extract) as mock_ext,
    ):
        await process_download_job("job-p1", "test_rd_key", settings)
        assert JOBS["job-p1"].status == JobStatus.COMPLETED
        assert JOBS["job-p1"].extracted_files == []
        mock_ext.assert_not_called()

        await process_download_job("job-p2", "test_rd_key", settings)
        assert JOBS["job-p2"].status == JobStatus.COMPLETED
        assert JOBS["job-p2"].extracted_files == ["game.iso"]
        mock_ext.assert_called_once_with(p1_path, destination_dir=tmp_path)


@pytest.mark.asyncio
async def test_multipart_rar_out_of_order_completion(tmp_path: Path) -> None:
    p1_path = tmp_path / "movie.part01.rar"
    p2_path = tmp_path / "movie.part02.rar"
    JOBS["job-p2"] = DownloadJob(
        job_id="job-p2",
        source_url="https://1fichier.com/?part2",
        link_type=LinkType.HOSTER,
        output_dir=str(tmp_path),
    )
    JOBS["job-p1"] = DownloadJob(
        job_id="job-p1",
        source_url="https://1fichier.com/?part1",
        link_type=LinkType.HOSTER,
        output_dir=str(tmp_path),
    )
    settings = Settings(auto_extract=True, cleanup_after_extract=True, rd_api_key="test_rd_key")

    async def fake_download(url: str, output_dir: str | None = None, filename: str | None = None, on_progress=None) -> str:
        if "part02" in url:
            p2_path.write_bytes(_make_rar5_volume_bytes(1, endarc_next_volume=False))
            return str(p2_path)
        p1_path.write_bytes(_make_rar5_volume_bytes(0, endarc_next_volume=True))
        return str(p1_path)

    with (
        patch(
            "app.main.RealDebridClient.resolve_to_direct_files",
            new_callable=AsyncMock,
            side_effect=[
                [ResolvedFile(url="https://cdn.rd.com/movie.part02.rar", filename="movie.part02.rar")],
                [ResolvedFile(url="https://cdn.rd.com/movie.part01.rar", filename="movie.part01.rar")],
            ],
        ),
        patch("app.main.Aria2Downloader.download", new_callable=AsyncMock, side_effect=fake_download),
        patch(
            "app.main.ArchiveExtractor.extract_and_cleanup",
            new_callable=AsyncMock,
            return_value=[tmp_path / "movie.mkv"],
        ) as mock_ext,
    ):
        await process_download_job("job-p2", "test_rd_key", settings)
        assert JOBS["job-p2"].status == JobStatus.COMPLETED
        mock_ext.assert_not_called()

        await process_download_job("job-p1", "test_rd_key", settings)
        assert JOBS["job-p1"].status == JobStatus.COMPLETED
        assert JOBS["job-p1"].extracted_files == ["movie.mkv"]
        mock_ext.assert_called_once_with(p1_path, destination_dir=tmp_path)


@pytest.mark.asyncio
async def test_multipart_rar_defers_when_sibling_job_inflight(tmp_path: Path) -> None:
    p1_path = tmp_path / "enc.part01.rar"
    JOBS["job-1"] = DownloadJob(
        job_id="job-1",
        source_url="https://1fichier.com/enc.part01.rar",
        link_type=LinkType.HOSTER,
        output_dir=str(tmp_path),
    )
    JOBS["job-2"] = DownloadJob(
        job_id="job-2",
        source_url="https://1fichier.com/enc.part02.rar",
        link_type=LinkType.HOSTER,
        status=JobStatus.QUEUED,
        output_dir=str(tmp_path),
    )
    settings = Settings(auto_extract=True, cleanup_after_extract=True, rd_api_key="test_rd_key")

    async def fake_download(url: str, output_dir: str | None = None, filename: str | None = None, on_progress=None) -> str:
        # Encrypted headers mean has_next_volume is None, so readiness falls back to sibling check
        p1_path.write_bytes(_make_rar5_volume_bytes(0, endarc_next_volume=False, encrypted_headers=True))
        return str(p1_path)

    with (
        patch(
            "app.main.RealDebridClient.resolve_to_direct_files",
            new_callable=AsyncMock,
            return_value=[ResolvedFile(url="https://cdn.rd.com/enc.part01.rar", filename="enc.part01.rar")],
        ),
        patch("app.main.Aria2Downloader.download", new_callable=AsyncMock, side_effect=fake_download),
        patch("app.main.ArchiveExtractor.extract_and_cleanup", new_callable=AsyncMock) as mock_ext,
    ):
        await process_download_job("job-1", "test_rd_key", settings)
        assert JOBS["job-1"].status == JobStatus.COMPLETED
        mock_ext.assert_not_called()


@pytest.mark.asyncio
async def test_multipart_rar_concurrent_completion_extracts_once(tmp_path: Path) -> None:
    p1_path = tmp_path / "race.part01.rar"
    p2_path = tmp_path / "race.part02.rar"
    JOBS["job-r1"] = DownloadJob(
        job_id="job-r1",
        source_url="https://1fichier.com/?r1",
        link_type=LinkType.HOSTER,
        output_dir=str(tmp_path),
    )
    JOBS["job-r2"] = DownloadJob(
        job_id="job-r2",
        source_url="https://1fichier.com/?r2",
        link_type=LinkType.HOSTER,
        output_dir=str(tmp_path),
    )
    settings = Settings(
        auto_extract=True,
        cleanup_after_extract=False,
        max_concurrent_downloads=4,
        rd_api_key="test_rd_key",
    )

    download_barrier = asyncio.Barrier(2)

    async def fake_resolve(url: str) -> list[ResolvedFile]:
        if "r1" in url:
            return [ResolvedFile(url="https://cdn.rd.com/race.part01.rar", filename="race.part01.rar")]
        return [ResolvedFile(url="https://cdn.rd.com/race.part02.rar", filename="race.part02.rar")]

    async def fake_download(url: str, output_dir: str | None = None, filename: str | None = None, on_progress=None) -> str:
        if "part01" in url:
            p1_path.write_bytes(_make_rar5_volume_bytes(0, endarc_next_volume=True))
            path_out = str(p1_path)
        else:
            p2_path.write_bytes(_make_rar5_volume_bytes(1, endarc_next_volume=False))
            path_out = str(p2_path)
        await download_barrier.wait()
        return path_out

    async def fake_extract(archive_path: Path, destination_dir: Path) -> list[Path]:
        await asyncio.sleep(0.05)
        return [destination_dir / "race.bin"]

    with (
        patch("app.main.RealDebridClient.resolve_to_direct_files", new_callable=AsyncMock, side_effect=fake_resolve),
        patch("app.main.Aria2Downloader.download", new_callable=AsyncMock, side_effect=fake_download),
        patch("app.main.ArchiveExtractor.extract_and_cleanup", new_callable=AsyncMock, side_effect=fake_extract) as mock_ext,
    ):
        await asyncio.gather(
            process_download_job("job-r1", "test_rd_key", settings),
            process_download_job("job-r2", "test_rd_key", settings),
        )
        assert JOBS["job-r1"].status == JobStatus.COMPLETED
        assert JOBS["job-r2"].status == JobStatus.COMPLETED
        mock_ext.assert_called_once_with(p1_path, destination_dir=tmp_path)







