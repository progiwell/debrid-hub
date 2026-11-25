"""FastAPI server for the Debrid Hub container."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from app import __commit__, __version__
from app.config import Settings, get_settings, update_settings
from app.db import get_job_store
from app.debrid import (
    SUPPORTED_PROVIDERS,
    RealDebridClient,  # noqa: F401
    classify_url,
    get_debrid_provider,
    is_info_hash,
)
from app.dependencies import (
    get_authenticated_settings,
    get_download_auth_settings,
    get_stream_auth_settings,
    get_upload_auth_settings,
)
from app.downloader import Aria2Downloader
from app.events import EVENT_BUS
from app.extractor import ArchiveExtractor
from app.qbittorrent import router as qbittorrent_router
from app.schemas import (
    DownloadJob,
    DownloadRequest,
    DownloadResponse,
    JobStatus,
    LinkType,
    SettingsResponse,
    SettingsUpdateRequest,
    TestDebridRequest,
    TestDebridResponse,
)
from app.torrent_utils import (
    create_magnet_from_torrent,
    extract_magnet_hash,
    extract_magnet_name,
    extract_torrent_metadata,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for startup database initialization and recovery."""
    settings = get_settings()
    store = get_job_store(settings)
    store.init_db()
    recovered = store.recover_interrupted_jobs()
    if recovered > 0:
        logger.warning("Recovered %d interrupted jobs on startup", recovered)
    for j in store.list_jobs(limit=1000):
        JOBS[j.job_id] = j
    yield
    store.close()


app = FastAPI(
    title="Debrid Hub API",
    version=__version__,
    description="Lightweight Real-Debrid + Aria2c + Unrar downloader daemon.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(qbittorrent_router)

# In-memory job store
JOBS: dict[str, DownloadJob] = {}

# Active background asyncio tasks mapped by job_id
ACTIVE_TASKS: dict[str, asyncio.Task] = {}

# Concurrency semaphore for background downloads
DOWNLOAD_SEMAPHORE: asyncio.Semaphore | None = None

# Per-archive extraction locks preventing concurrent volume races
ARCHIVE_LOCKS: dict[str, asyncio.Lock] = {}

# Signatures (size, mtime_ns) of recently extracted primary archives when cleanup is disabled
EXTRACTED_ARCHIVE_SIGNATURES: dict[str, tuple[int, int]] = {}

# Template and static asset directories
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_download_semaphore(max_concurrency: int) -> asyncio.Semaphore:
    """Return singleton semaphore controlling maximum concurrent download jobs."""
    global DOWNLOAD_SEMAPHORE
    if DOWNLOAD_SEMAPHORE is None:
        DOWNLOAD_SEMAPHORE = asyncio.Semaphore(max_concurrency)
    return DOWNLOAD_SEMAPHORE


def get_archive_lock(lock_key: str) -> asyncio.Lock:
    """Return a per-archive asyncio.Lock, pruning stale unlocked entries if needed."""
    if lock_key not in ARCHIVE_LOCKS and len(ARCHIVE_LOCKS) > 500:
        for stale_key in [k for k, v in ARCHIVE_LOCKS.items() if not v.locked()]:
            ARCHIVE_LOCKS.pop(stale_key, None)
            if len(ARCHIVE_LOCKS) <= 500:
                break
    if lock_key not in ARCHIVE_LOCKS:
        ARCHIVE_LOCKS[lock_key] = asyncio.Lock()
    return ARCHIVE_LOCKS[lock_key]


def _record_extracted_signature(sig_key: str, sig: tuple[int, int]) -> None:
    """Cache extracted archive signature, bounding memory to 500 entries."""
    EXTRACTED_ARCHIVE_SIGNATURES.pop(sig_key, None)
    EXTRACTED_ARCHIVE_SIGNATURES[sig_key] = sig
    while len(EXTRACTED_ARCHIVE_SIGNATURES) > 500:
        oldest_key = next(iter(EXTRACTED_ARCHIVE_SIGNATURES))
        EXTRACTED_ARCHIVE_SIGNATURES.pop(oldest_key, None)


def _has_inflight_sibling_job(
    current_job: DownloadJob,
    archive_key: str,
    extractor: ArchiveExtractor,
    include_unresolved: bool = False,
) -> bool:
    """Return True if another active job in the same output_dir targets the same archive set."""
    current_dir = Path(current_job.output_dir).resolve()
    for other in list(JOBS.values()):
        if other.job_id == current_job.job_id:
            continue
        if Path(other.output_dir).resolve() != current_dir:
            continue

        is_inflight = other.status in (JobStatus.QUEUED, JobStatus.RESOLVING_DEBRID) or (
            other.status == JobStatus.DOWNLOADING
            and (other.total_files == 0 or other.completed_files < other.total_files)
        )
        if not is_inflight:
            continue

        candidate_names = list(other.filenames)
        source_name = Path(urlparse(other.source_url).path).name
        if source_name:
            candidate_names.append(source_name)

        if any(extractor.get_archive_group_key(Path(name)) == archive_key for name in candidate_names):
            return True
        if include_unresolved and other.status in (JobStatus.QUEUED, JobStatus.RESOLVING_DEBRID) and not other.filenames:
            return True
    return False


def mask_secret(secret: str | None) -> str:
    """Mask sensitive API keys for safe response serialization."""
    if not secret:
        return ""
    if len(secret) <= 6:
        return "******"
    return f"{'*' * (len(secret) - 4)}{secret[-4:]}"


def format_settings_response(settings: Settings) -> SettingsResponse:
    """Construct typed SettingsResponse with masked credentials."""
    return SettingsResponse(
        debrid_provider=settings.debrid_provider,
        rd_api_key_masked=mask_secret(settings.rd_api_key),
        rd_base_url=settings.rd_base_url,
        download_dir=settings.download_dir,
        aria2c_max_connections=settings.aria2c_max_connections,
        aria2c_split=settings.aria2c_split,
        max_concurrent_downloads=settings.max_concurrent_downloads,
        auto_extract=settings.auto_extract,
        cleanup_after_extract=settings.cleanup_after_extract,
        supported_providers=SUPPORTED_PROVIDERS,
    )


def verify_auth(x_api_key: str | None, settings: Settings) -> None:
    """Validate optional API authentication key (retained for backward compatibility)."""
    if settings.api_auth_key and (not x_api_key or x_api_key != settings.api_auth_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


def sanitize_subfolder(base_dir: str, subfolder: str | None) -> str:
    """Compute safe output directory preventing .. path traversal."""
    base_path = Path(base_dir).resolve()
    if not subfolder:
        return str(base_path)

    cleaned = os.path.normpath("/" + subfolder.strip()).lstrip("/")
    target_path = (base_path / cleaned).resolve()
    try:
        target_path.relative_to(base_path)
    except ValueError:
        return str(base_path)
    return str(target_path)


def get_effective_rd_key(payload_key: str | None, settings: Settings) -> str:
    """Resolve effective Real-Debrid API key from payload or settings."""
    if payload_key:
        if settings.api_auth_key and payload_key == settings.api_auth_key and settings.rd_api_key:
            return settings.rd_api_key
        return payload_key
    return settings.rd_api_key


async def process_download_job(job_id: str, rd_api_key: str, settings: Settings) -> None:
    """Execute the end-to-end download and extraction pipeline with concurrency control."""
    store = get_job_store(settings)
    job = JOBS.get(job_id) or store.get_job(job_id)
    if not job:
        return
    JOBS[job_id] = job

    current_task = asyncio.current_task()
    if current_task:
        ACTIVE_TASKS[job_id] = current_task

    semaphore = get_download_semaphore(settings.max_concurrent_downloads)
    try:
        async with semaphore:
            try:
                # 1. Resolve direct URLs via configured Debrid provider
                job.status = JobStatus.RESOLVING_DEBRID
                store.upsert_job(job)
                EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)
                provider = get_debrid_provider(
                    provider_name=settings.debrid_provider,
                    api_key=rd_api_key,
                    base_url=settings.rd_base_url,
                )
                async with provider:
                    resolved_files = await provider.resolve_to_direct_files(job.source_url)
                job.resolved_urls = [f.url for f in resolved_files]
                job.total_files = len(resolved_files)
                job.completed_files = 0
                job.progress_percent = 0.0

                # Populate filenames immediately from resolved files before downloading starts
                resolved_filenames = [f.filename for f in resolved_files if f.filename]
                if resolved_filenames:
                    job.filenames = resolved_filenames

                # 2. Download each resolved link via Aria2c
                job.status = JobStatus.DOWNLOADING
                store.upsert_job(job)
                EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)
                downloader = Aria2Downloader(
                    download_dir=job.output_dir,
                    max_connections=settings.aria2c_max_connections,
                    split=settings.aria2c_split,
                )
                downloaded_paths: list[str] = []
                for idx, file_item in enumerate(resolved_files):
                    def handle_file_progress(file_pct: float, speed_str: str | None = None, current_idx: int = idx) -> None:
                        job.download_speed = speed_str
                        if job.total_files > 0:
                            current_total = ((current_idx + (file_pct / 100.0)) / job.total_files) * 100.0
                            job.progress_percent = round(min(current_total, 99.9), 1)
                        EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id, is_progress=True)

                    file_path = await downloader.download(
                        file_item.url,
                        output_dir=job.output_dir,
                        filename=file_item.filename,
                        on_progress=handle_file_progress,
                    )
                    downloaded_paths.append(file_path)
                    actual_name = Path(file_path).name
                    if actual_name not in job.filenames:
                        job.filenames.append(actual_name)
                    job.completed_files += 1
                    job.progress_percent = (
                        round((job.completed_files / job.total_files) * 100.0, 1)
                        if job.total_files > 0
                        else 100.0
                    )
                    store.upsert_job(job)
                    EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)

                job.download_speed = None

                # 3. Auto-extract archives if enabled
                if settings.auto_extract:
                    extractor = ArchiveExtractor(cleanup_after_extract=settings.cleanup_after_extract)
                    seen_archive_keys: set[str] = set()
                    resolved_dir = Path(job.output_dir).resolve()
                    for file_path_str in downloaded_paths:
                        p = Path(file_path_str)
                        archive_key = extractor.get_archive_group_key(p)
                        if not archive_key or archive_key in seen_archive_keys:
                            continue
                        seen_archive_keys.add(archive_key)

                        async with get_archive_lock(f"{resolved_dir}::{archive_key}"):
                            readiness = await asyncio.to_thread(extractor.check_archive_readiness, p)
                            if not readiness.ready or readiness.primary_archive is None:
                                logger.info(
                                    "Deferring extraction for job %s (%s): %s",
                                    job.job_id,
                                    p.name,
                                    readiness.reason,
                                )
                                continue

                            if _has_inflight_sibling_job(
                                job, archive_key, extractor, include_unresolved=readiness.encrypted_headers
                            ):
                                logger.info(
                                    "Deferring extraction for job %s (%s): sibling job still in progress",
                                    job.job_id,
                                    p.name,
                                )
                                continue

                            primary = readiness.primary_archive
                            primary_sig_key: str | None = None
                            primary_sig: tuple[int, int] | None = None
                            if primary.exists():
                                st = primary.stat()
                                primary_sig_key = str(primary.resolve())
                                primary_sig = (st.st_size, st.st_mtime_ns)
                                if EXTRACTED_ARCHIVE_SIGNATURES.get(primary_sig_key) == primary_sig:
                                    logger.info(
                                        "Skipping already-extracted archive %s for job %s",
                                        primary.name,
                                        job.job_id,
                                    )
                                    continue

                            job.status = JobStatus.EXTRACTING
                            store.upsert_job(job)
                            EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)
                            extracted_paths = await extractor.extract_and_cleanup(
                                primary, destination_dir=Path(job.output_dir)
                            )
                            if primary_sig_key and primary_sig is not None and primary.exists():
                                _record_extracted_signature(primary_sig_key, primary_sig)
                            for extracted in extracted_paths:
                                name = extracted.name
                                if name and name not in job.extracted_files:
                                    job.extracted_files.append(name)
                            store.upsert_job(job)
                            EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)

                # 4. Completed
                job.status = JobStatus.COMPLETED
                job.download_speed = None
                job.progress_percent = 100.0
                store.upsert_job(job)
                EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)

            except asyncio.CancelledError:
                job.status = JobStatus.FAILED
                job.download_speed = None
                job.error = "Job cancelled"
                store.upsert_job(job)
                EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)
                raise
            except Exception as exc:  # noqa: BLE001
                job.status = JobStatus.FAILED
                job.download_speed = None
                job.error = str(exc)
                store.upsert_job(job)
                EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)
    finally:
        ACTIVE_TASKS.pop(job_id, None)


# ---------------------------------------------------------
# HTTP Routes
# ---------------------------------------------------------


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Container health check endpoint."""
    return {"status": "ok", "version": __version__, "commit": __commit__}


@app.get("/api/settings", response_model=SettingsResponse)
async def get_current_settings(
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
) -> SettingsResponse:
    """Retrieve current runtime configuration with sensitive API tokens masked."""
    return format_settings_response(settings)


@app.put("/api/settings", response_model=SettingsResponse)
async def update_current_settings(
    payload: SettingsUpdateRequest,
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
) -> SettingsResponse:
    """Update runtime settings and persist to disk without requiring a container restart."""
    updates = payload.model_dump(exclude_unset=True)
    updated = update_settings(updates)

    global DOWNLOAD_SEMAPHORE
    if "max_concurrent_downloads" in updates:
        DOWNLOAD_SEMAPHORE = asyncio.Semaphore(updated.max_concurrent_downloads)

    return format_settings_response(updated)


@app.post("/api/settings/test-debrid", response_model=TestDebridResponse)
async def test_debrid_connection(
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
    payload: TestDebridRequest | None = None,
) -> TestDebridResponse:
    """Validate provider credentials and test connectivity."""
    provider_name = (payload.provider if payload and payload.provider else None) or settings.debrid_provider
    api_key = (payload.api_key if payload and payload.api_key else None) or settings.rd_api_key

    if not api_key:
        return TestDebridResponse(
            ok=False,
            provider=provider_name,
            message="No API key provided or configured.",
        )

    try:
        provider = get_debrid_provider(
            provider_name=provider_name,
            api_key=api_key,
            base_url=settings.rd_base_url,
        )
        async with provider:
            account_data = await provider.test_account()
        return TestDebridResponse(
            ok=True,
            provider=provider_name,
            username=account_data.get("username"),
            account_type=account_data.get("type"),
            expiration=account_data.get("expiration"),
            message="Debrid connection successful",
        )
    except Exception as exc:  # noqa: BLE001
        return TestDebridResponse(
            ok=False,
            provider=provider_name,
            message=str(exc),
        )


@app.post("/api/download", response_model=DownloadResponse, status_code=202)
async def create_download(
    payload: DownloadRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_download_auth_settings)],
) -> DownloadResponse:
    """Ingest a Magnet URI or Hoster URL and queue a Debrid -> Aria2c download job."""
    rd_api_key = get_effective_rd_key(payload.api_key, settings)
    raw_url = payload.url.strip()
    effective_url = f"magnet:?xt=urn:btih:{raw_url}" if is_info_hash(raw_url) else raw_url
    link_type = classify_url(effective_url)
    job_id = str(uuid.uuid4())
    output_dir = sanitize_subfolder(settings.download_dir, payload.subfolder)
    info_hash = (
        extract_magnet_hash(effective_url)
        or hashlib.sha1(effective_url.encode(), usedforsecurity=False).hexdigest().lower()
    )
    source = payload.source or "paste"
    magnet_name = extract_magnet_name(effective_url)
    initial_filenames = [magnet_name] if magnet_name else []

    job = DownloadJob(
        job_id=job_id,
        source_url=effective_url,
        link_type=link_type,
        status=JobStatus.QUEUED,
        output_dir=output_dir,
        category=payload.category,
        source=source,
        client_id=payload.client_id,
        client_name=payload.client_name,
        info_hash=info_hash,
        filenames=initial_filenames,
    )
    store = get_job_store(settings)
    store.upsert_job(job)
    JOBS[job_id] = job
    EVENT_BUS.publish("job_created", job.model_dump(mode="json"), job_id=job.job_id)

    background_tasks.add_task(process_download_job, job_id, rd_api_key, settings)

    return DownloadResponse(
        job_id=job_id,
        status=JobStatus.QUEUED,
        link_type=link_type,
        message="Download job queued",
    )


@app.post("/api/upload/torrent", response_model=DownloadResponse, status_code=202)
async def upload_torrent(
    file: Annotated[UploadFile, File(...)],
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_upload_auth_settings)],
    subfolder: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
    source: Annotated[str | None, Form()] = None,
    client_id: Annotated[str | None, Form()] = None,
    client_name: Annotated[str | None, Form()] = None,
    apiKey: Annotated[str | None, Form()] = None,
    api_key: Annotated[str | None, Form()] = None,
) -> DownloadResponse:
    """Ingest an uploaded .torrent file and queue a Debrid -> Aria2c download job."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty torrent file")

    if not content.startswith(b"d") or b"4:info" not in content:
        raise HTTPException(status_code=400, detail="Invalid .torrent file: missing bencoded info dictionary")

    try:
        info_hash, name = extract_torrent_metadata(content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid .torrent file: {exc}") from exc

    if (not name or name in ("download.torrent", "torrent")) and file.filename:
        fn = Path(file.filename).stem
        if fn and fn != "download":
            name = fn

    magnet_uri = create_magnet_from_torrent(info_hash, name)
    effective_key = apiKey or api_key
    rd_api_key = get_effective_rd_key(effective_key, settings)
    job_id = str(uuid.uuid4())
    output_dir = sanitize_subfolder(settings.download_dir, subfolder)
    job_source = source or "upload"
    initial_filenames = [name] if name else []

    job = DownloadJob(
        job_id=job_id,
        source_url=magnet_uri,
        link_type=LinkType.TORRENT,
        status=JobStatus.QUEUED,
        output_dir=output_dir,
        category=category,
        source=job_source,
        client_id=client_id,
        client_name=client_name,
        info_hash=info_hash,
        filenames=initial_filenames,
    )
    store = get_job_store(settings)
    store.upsert_job(job)
    JOBS[job_id] = job
    EVENT_BUS.publish("job_created", job.model_dump(mode="json"), job_id=job.job_id)

    background_tasks.add_task(process_download_job, job_id, rd_api_key, settings)

    return DownloadResponse(
        job_id=job_id,
        status=JobStatus.QUEUED,
        link_type=LinkType.TORRENT,
        message=f"Torrent '{name}' queued for download",
    )


@app.get("/api/status/stream")
async def stream_job_status(
    request: Request,
    settings: Annotated[Settings, Depends(get_stream_auth_settings)],
) -> StreamingResponse:
    """Stream real-time job state transitions and download progress events via SSE."""
    store = get_job_store(settings)

    async def event_generator():
        queue = EVENT_BUS.subscribe()
        try:
            # 1. Yield initial snapshot of active/recent jobs
            jobs = store.list_jobs(limit=100)
            init_json = json.dumps([j.model_dump(mode="json") for j in jobs])
            yield f"event: init\ndata: {init_json}\n\n"

            # 2. Stream real-time events as they occur
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message
                except TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            EVENT_BUS.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/status", response_model=list[DownloadJob])
async def list_jobs(
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
    status: JobStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DownloadJob]:
    """List all tracked download jobs ordered by newest first, optionally filtered by status."""
    store = get_job_store(settings)
    for j_id, j in list(JOBS.items()):
        if not store.get_job(j_id):
            store.upsert_job(j)
    return store.list_jobs(status=status, limit=limit, offset=offset)


@app.get("/api/status/{job_id}", response_model=DownloadJob)
async def get_job_status(
    job_id: str,
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
) -> DownloadJob:
    """Retrieve details and status for a specific download job."""
    store = get_job_store(settings)
    job = store.get_job(job_id) or JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/status/{job_id}/retry", response_model=DownloadJob, status_code=202)
async def retry_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
) -> DownloadJob:
    """Retry a failed or interrupted download job."""
    store = get_job_store(settings)
    job = store.get_job(job_id) or JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    task = ACTIVE_TASKS.get(job_id)
    if task and not task.done():
        raise HTTPException(status_code=400, detail="Job is currently running")

    job.status = JobStatus.QUEUED
    job.progress_percent = 0.0
    job.completed_files = 0
    magnet_name = extract_magnet_name(job.source_url)
    job.filenames = [magnet_name] if magnet_name else []
    job.extracted_files = []
    job.error = None
    job.completed_at = None

    store.upsert_job(job)
    JOBS[job_id] = job
    EVENT_BUS.publish("job_update", job.model_dump(mode="json"), job_id=job.job_id)

    background_tasks.add_task(process_download_job, job_id, settings.rd_api_key, settings)
    return job


@app.delete("/api/status/all")
async def clear_all_jobs(
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
    confirm: bool = False,
) -> dict[str, Any]:
    """Purge all download jobs from database history. Requires ?confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required: pass ?confirm=true to clear all jobs",
        )

    for _job_id, task in list(ACTIVE_TASKS.items()):
        if not task.done():
            task.cancel()

    store = get_job_store(settings)
    deleted_count = store.clear_jobs(clear_all=True)
    JOBS.clear()
    EVENT_BUS.publish("jobs_cleared", {"all": True})
    return {"message": "All jobs cleared", "deleted": deleted_count, "remaining": 0}


@app.delete("/api/status/{job_id}")
async def delete_job(
    job_id: str,
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
) -> dict[str, str]:
    """Delete a specific job from history and cancel any active running tasks."""
    store = get_job_store(settings)
    job = store.get_job(job_id) or JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    task = ACTIVE_TASKS.get(job_id)
    if task and not task.done():
        task.cancel()

    store.delete_job(job_id)
    JOBS.pop(job_id, None)
    EVENT_BUS.publish("job_deleted", {"job_id": job_id}, job_id=job_id)
    return {"message": "Job deleted", "job_id": job_id}


@app.delete("/api/status")
async def clear_jobs(
    settings: Annotated[Settings, Depends(get_authenticated_settings)],
    status: JobStatus | None = None,
) -> dict[str, int]:
    """Clear completed, failed, or status-filtered jobs from history."""
    store = get_job_store(settings)
    for j_id, j in list(JOBS.items()):
        if not store.get_job(j_id):
            store.upsert_job(j)

    if status is not None:
        target_jobs = store.list_jobs(status=status, limit=1000)
    else:
        target_jobs = store.list_jobs(status=JobStatus.COMPLETED, limit=1000) + store.list_jobs(
            status=JobStatus.FAILED, limit=1000
        )

    for j in target_jobs:
        task = ACTIVE_TASKS.get(j.job_id)
        if task and not task.done():
            task.cancel()
        JOBS.pop(j.job_id, None)

    deleted_count = store.clear_jobs(status=status)
    remaining_count = store.count_jobs()
    status_val = status.value if hasattr(status, "value") else (str(status) if status else "finished")
    EVENT_BUS.publish("jobs_cleared", {"status": status_val, "all": False})
    return {"deleted": deleted_count, "remaining": remaining_count}


def load_settings_template() -> str:
    """Load settings HTML template from disk."""
    template_path = TEMPLATES_DIR / "settings.html"
    return template_path.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def serve_settings_ui() -> HTMLResponse:
    """Serve lightweight in-browser settings configuration dashboard."""
    html = load_settings_template()
    html = html.replace("{{ version }}", __version__).replace("{{ commit }}", __commit__)
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
