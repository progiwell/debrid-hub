"""Unit tests for SQLite JobStore persistence and crash recovery."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from app.db import JobStore
from app.schemas import DownloadJob, JobStatus, LinkType


@pytest.fixture
def store() -> JobStore:
    """Create an in-memory JobStore for isolated test runs."""
    s = JobStore(db_path=":memory:")
    yield s
    s.close()


def test_init_db_creates_schema(store: JobStore) -> None:
    """Verify table creation and schema integrity."""
    with store._lock:
        cursor = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs';"
        )
        assert cursor.fetchone() is not None

        cursor = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_jobs_status';"
        )
        assert cursor.fetchone() is not None


def test_upsert_and_get_job(store: JobStore) -> None:
    """Verify storing and retrieving a full DownloadJob record."""
    job = DownloadJob(
        job_id="job-101",
        source_url="https://rapidgator.net/file/101",
        link_type=LinkType.HOSTER,
        status=JobStatus.DOWNLOADING,
        resolved_urls=["https://cdn.real-debrid.com/d/101"],
        output_dir="/downloads/movies",
        filenames=["movie.part1.rar", "movie.part2.rar"],
        extracted_files=["movie.mkv"],
        total_files=2,
        completed_files=1,
        progress_percent=50.0,
        error=None,
    )
    store.upsert_job(job)

    retrieved = store.get_job("job-101")
    assert retrieved is not None
    assert retrieved.job_id == "job-101"
    assert retrieved.source_url == "https://rapidgator.net/file/101"
    assert retrieved.link_type == LinkType.HOSTER
    assert retrieved.status == JobStatus.DOWNLOADING
    assert retrieved.resolved_urls == ["https://cdn.real-debrid.com/d/101"]
    assert retrieved.output_dir == "/downloads/movies"
    assert retrieved.filenames == ["movie.part1.rar", "movie.part2.rar"]
    assert retrieved.extracted_files == ["movie.mkv"]
    assert retrieved.total_files == 2
    assert retrieved.completed_files == 1
    assert retrieved.progress_percent == 50.0
    assert retrieved.error is None
    assert retrieved.created_at is not None
    assert retrieved.updated_at is not None
    assert retrieved.completed_at is None


def test_upsert_update_existing_job(store: JobStore) -> None:
    """Verify updating fields on an existing job."""
    job = DownloadJob(
        job_id="job-102",
        source_url="magnet:?xt=urn:btih:102",
        link_type=LinkType.MAGNET,
        status=JobStatus.QUEUED,
        output_dir="/downloads",
    )
    store.upsert_job(job)

    # Update job to completed
    job.status = JobStatus.COMPLETED
    job.progress_percent = 100.0
    job.completed_files = 1
    job.total_files = 1
    job.filenames = ["release.zip"]
    store.upsert_job(job)

    updated = store.get_job("job-102")
    assert updated is not None
    assert updated.status == JobStatus.COMPLETED
    assert updated.progress_percent == 100.0
    assert updated.completed_at is not None


def test_list_jobs_ordering_and_filtering(store: JobStore) -> None:
    """Verify list_jobs ordering by created_at desc and filtering by status."""
    for i in range(5):
        status = JobStatus.COMPLETED if i % 2 == 0 else JobStatus.FAILED
        job = DownloadJob(
            job_id=f"job-{i}",
            source_url=f"https://example.com/{i}",
            link_type=LinkType.HOSTER,
            status=status,
            output_dir="/downloads",
        )
        store.upsert_job(job)
        time.sleep(0.002)

    # List all
    all_jobs = store.list_jobs(limit=10)
    assert len(all_jobs) == 5
    # Check ordering (newest first)
    assert all_jobs[0].job_id == "job-4"
    assert all_jobs[-1].job_id == "job-0"

    # Filter by completed
    completed = store.list_jobs(status=JobStatus.COMPLETED)
    assert len(completed) == 3
    assert all(j.status == JobStatus.COMPLETED for j in completed)

    # Filter with pagination
    paged = store.list_jobs(limit=2, offset=1)
    assert len(paged) == 2
    assert paged[0].job_id == "job-3"
    assert paged[1].job_id == "job-2"


def test_delete_job(store: JobStore) -> None:
    """Verify deleting job by id."""
    job = DownloadJob(
        job_id="job-del",
        source_url="https://example.com/del",
        link_type=LinkType.HOSTER,
        output_dir="/downloads",
    )
    store.upsert_job(job)
    assert store.get_job("job-del") is not None

    deleted = store.delete_job("job-del")
    assert deleted is True
    assert store.get_job("job-del") is None

    # Deleting non-existent job returns False
    assert store.delete_job("non-existent") is False


def test_clear_jobs_modes(store: JobStore) -> None:
    """Verify clear_jobs with terminal defaults, status filter, and clear_all."""
    # Insert 1 completed, 1 failed, 1 queued, 1 downloading
    jobs = [
        DownloadJob(
            job_id="j-comp",
            source_url="https://ex.com/1",
            link_type=LinkType.HOSTER,
            status=JobStatus.COMPLETED,
            output_dir="/d",
        ),
        DownloadJob(
            job_id="j-fail",
            source_url="https://ex.com/2",
            link_type=LinkType.HOSTER,
            status=JobStatus.FAILED,
            output_dir="/d",
        ),
        DownloadJob(
            job_id="j-queue",
            source_url="https://ex.com/3",
            link_type=LinkType.HOSTER,
            status=JobStatus.QUEUED,
            output_dir="/d",
        ),
        DownloadJob(
            job_id="j-down",
            source_url="https://ex.com/4",
            link_type=LinkType.HOSTER,
            status=JobStatus.DOWNLOADING,
            output_dir="/d",
        ),
    ]
    for j in jobs:
        store.upsert_job(j)

    # Clear terminal (completed + failed)
    deleted = store.clear_jobs()
    assert deleted == 2
    assert store.count_jobs() == 2
    assert store.get_job("j-queue") is not None
    assert store.get_job("j-down") is not None

    # Clear by specific status
    deleted_down = store.clear_jobs(status=JobStatus.DOWNLOADING)
    assert deleted_down == 1
    assert store.count_jobs() == 1
    assert store.get_job("j-down") is None

    # Clear all
    deleted_all = store.clear_jobs(clear_all=True)
    assert deleted_all == 1
    assert store.count_jobs() == 0


def test_recover_interrupted_jobs(store: JobStore) -> None:
    """Verify interrupted jobs in active states are transitioned to failed on restart."""
    active_statuses = [
        JobStatus.QUEUED,
        JobStatus.RESOLVING_DEBRID,
        JobStatus.DOWNLOADING,
        JobStatus.EXTRACTING,
    ]
    for idx, st in enumerate(active_statuses):
        store.upsert_job(
            DownloadJob(
                job_id=f"active-{idx}",
                source_url=f"https://ex.com/{idx}",
                link_type=LinkType.HOSTER,
                status=st,
                output_dir="/d",
            )
        )

    # Add 1 completed and 1 failed job
    store.upsert_job(
        DownloadJob(
            job_id="already-comp",
            source_url="https://ex.com/comp",
            link_type=LinkType.HOSTER,
            status=JobStatus.COMPLETED,
            output_dir="/d",
        )
    )
    store.upsert_job(
        DownloadJob(
            job_id="already-fail",
            source_url="https://ex.com/fail",
            link_type=LinkType.HOSTER,
            status=JobStatus.FAILED,
            error="Original error",
            output_dir="/d",
        )
    )

    recovered_count = store.recover_interrupted_jobs()
    assert recovered_count == 4

    for idx in range(len(active_statuses)):
        j = store.get_job(f"active-{idx}")
        assert j is not None
        assert j.status == JobStatus.FAILED
        assert j.error == "⚠️ Interrupted by container restart"
        assert j.completed_at is not None

    # Completed and already-failed jobs untouched
    comp = store.get_job("already-comp")
    assert comp is not None
    assert comp.status == JobStatus.COMPLETED

    fail = store.get_job("already-fail")
    assert fail is not None
    assert fail.status == JobStatus.FAILED
    assert fail.error == "Original error"


def test_store_directory_fallback(tmp_path: Path) -> None:
    """Verify that a valid directory path works and falls back gracefully on error."""
    db_file = tmp_path / "subdir" / "test_jobs.db"
    store = JobStore(db_path=db_file)
    assert Path(db_file).exists()
    store.close()


def test_download_speed_persistence(store: JobStore) -> None:
    """Verify download_speed is persisted and retrieved properly."""
    job = DownloadJob(
        job_id="job-speed",
        source_url="https://rapidgator.net/file/speed",
        link_type=LinkType.HOSTER,
        status=JobStatus.DOWNLOADING,
        output_dir="/downloads",
        download_speed="14.2MiB/s",
    )
    store.upsert_job(job)

    retrieved = store.get_job("job-speed")
    assert retrieved is not None
    assert retrieved.download_speed == "14.2MiB/s"

    # Reset speed on completion
    job.status = JobStatus.COMPLETED
    job.download_speed = None
    store.upsert_job(job)

    updated = store.get_job("job-speed")
    assert updated is not None
    assert updated.download_speed is None


def test_client_identity_persistence(store: JobStore) -> None:
    """Verify client_id and client_name are persisted and updated properly."""
    job = DownloadJob(
        job_id="job-client-id",
        source_url="https://1fichier.com/?clienttest",
        link_type=LinkType.HOSTER,
        status=JobStatus.QUEUED,
        output_dir="/downloads",
        source="context_menu",
        client_id="uuid-browser-1234",
        client_name="Mac Studio (Chrome)",
    )
    store.upsert_job(job)

    retrieved = store.get_job("job-client-id")
    assert retrieved is not None
    assert retrieved.client_id == "uuid-browser-1234"
    assert retrieved.client_name == "Mac Studio (Chrome)"

    # Updating client_name works on upsert
    job.client_name = "Work Laptop"
    store.upsert_job(job)
    updated = store.get_job("job-client-id")
    assert updated is not None
    assert updated.client_id == "uuid-browser-1234"
    assert updated.client_name == "Work Laptop"


