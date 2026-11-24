"""qBittorrent Web API v2 emulation layer for Sonarr, Radarr, and Prowlarr."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.config import Settings, get_settings
from app.db import get_job_store
from app.debrid import classify_url
from app.events import EVENT_BUS
from app.schemas import DownloadJob, JobStatus, LinkType
from app.torrent_utils import (
    create_magnet_from_torrent,
    extract_magnet_hash,
    extract_magnet_name,
    extract_torrent_metadata,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["qBittorrent"])


# -------------------------------------------------------------
# Helpers
# -------------------------------------------------------------


VALID_AUTH_TOKENS = {"debrid-hub-session", "debrid-downloader-session", "debrid-nas-auth-token"}


def verify_qb_auth(
    settings: Settings,
    request: Request,
    auth_cookie: str | None = None,
    x_api_key: str | None = None,
) -> bool:
    """Validate request authorization against API_AUTH_KEY if configured."""
    if not settings.api_auth_key:
        return True

    # Check X-API-Key header or SID cookie
    if x_api_key and hmac.compare_digest(x_api_key.strip(), settings.api_auth_key):
        return True
    if auth_cookie and auth_cookie.strip() in VALID_AUTH_TOKENS:
        return True

    # Check Basic Auth if sent by Sonarr/Radarr
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        try:
            raw = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, _, password = raw.partition(":")
            if hmac.compare_digest(password, settings.api_auth_key) or hmac.compare_digest(
                username, settings.api_auth_key
            ):
                return True
        except (ValueError, binascii.Error):
            pass

    return False


def require_qb_auth(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    auth_cookie: Annotated[str | None, Cookie(alias="SID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Settings:
    """FastAPI dependency enforcing qBittorrent API authentication when API_AUTH_KEY is set."""
    if not verify_qb_auth(settings, request, auth_cookie=auth_cookie, x_api_key=x_api_key):
        raise HTTPException(status_code=403, detail="Forbidden")
    return settings


def determine_source(category: str | None) -> str:
    """Infer the origin client (sonarr, radarr, or generic arr) from category."""
    if not category:
        return "arr"
    cat_lower = category.lower()
    if "sonarr" in cat_lower or "tv" in cat_lower or "series" in cat_lower:
        return "sonarr"
    if "radarr" in cat_lower or "movie" in cat_lower:
        return "radarr"
    if "lidarr" in cat_lower or "music" in cat_lower:
        return "lidarr"
    if "readarr" in cat_lower or "book" in cat_lower:
        return "readarr"
    return cat_lower


def format_torrent_hash(job: DownloadJob) -> str:
    """Return a 40-character lowercase hex string for the torrent info-hash."""
    if job.info_hash:
        return job.info_hash.lower()
    magnet_h = extract_magnet_hash(job.source_url)
    if magnet_h:
        return magnet_h.lower()
    return hashlib.sha1(job.job_id.encode()).hexdigest().lower()


def map_job_state_to_qbittorrent(status: JobStatus) -> str:
    """Map internal job lifecycle status to qBittorrent state string."""
    if status == JobStatus.QUEUED:
        return "queuedDL"
    if status == JobStatus.RESOLVING_DEBRID:
        return "metaDL"
    if status == JobStatus.DOWNLOADING:
        return "downloading"
    if status == JobStatus.EXTRACTING:
        return "moving"
    if status == JobStatus.COMPLETED:
        return "pausedUP"
    return "error"


def job_to_torrent_dict(job: DownloadJob) -> dict[str, Any]:
    """Convert a DownloadJob instance into standard qBittorrent torrent representation."""
    info_hash = format_torrent_hash(job)

    # Derive torrent display name
    name = (
        (job.filenames[0] if job.filenames else None)
        or (job.extracted_files[0] if job.extracted_files else None)
        or extract_magnet_name(job.source_url)
        or job.source_url
    )

    # Calculate timestamps
    try:
        added_on = int(datetime.fromisoformat(job.created_at).timestamp())
    except (ValueError, TypeError):
        added_on = int(datetime.now(UTC).timestamp())

    completion_on = -1
    if job.status == JobStatus.COMPLETED and job.completed_at:
        try:
            completion_on = int(datetime.fromisoformat(job.completed_at).timestamp())
        except (ValueError, TypeError):
            completion_on = added_on

    progress = round(min(max(job.progress_percent / 100.0, 0.0), 1.0), 4)

    # Nominal size
    total_size = max(job.total_files, 1) * 1_000_000_000
    completed_bytes = int(total_size * progress)
    amount_left = total_size - completed_bytes

    dlspeed = 50_000_000 if job.status == JobStatus.DOWNLOADING else 0

    content_path = job.output_dir
    if job.extracted_files:
        content_path = str(Path(job.output_dir) / job.extracted_files[0])
    elif job.filenames:
        content_path = str(Path(job.output_dir) / job.filenames[0])

    return {
        "added_on": added_on,
        "amount_left": amount_left,
        "auto_tmm": False,
        "availability": 1.0,
        "category": job.category or "",
        "completed": completed_bytes,
        "completion_on": completion_on,
        "content_path": content_path,
        "dl_limit": -1,
        "dlspeed": dlspeed,
        "download_path": "",
        "downloaded": completed_bytes,
        "downloaded_session": completed_bytes,
        "eta": 0 if job.status == JobStatus.COMPLETED else (30 if job.status == JobStatus.DOWNLOADING else 8640000),
        "f_l_piece_prio": False,
        "force_start": False,
        "hash": info_hash,
        "infohash_v1": info_hash,
        "infohash_v2": "",
        "last_activity": int(datetime.now(UTC).timestamp()),
        "magnet_uri": job.source_url if job.source_url.startswith("magnet:") else "",
        "max_ratio": -1,
        "max_seeding_time": -1,
        "name": name,
        "num_complete": 10,
        "num_incomplete": 0,
        "num_leechs": 0,
        "num_seeds": 10,
        "priority": 0,
        "progress": progress,
        "ratio": 1.0,
        "ratio_limit": -1,
        "save_path": job.output_dir,
        "seeding_time": 0,
        "seeding_time_limit": -1,
        "seen_complete": completion_on if completion_on > 0 else added_on,
        "seq_dl": False,
        "size": total_size,
        "state": map_job_state_to_qbittorrent(job.status),
        "super_seeding": False,
        "tags": job.source or "",
        "time_active": 60,
        "total_size": total_size,
        "tracker": "",
        "trackers_count": 0,
        "up_limit": -1,
        "uploaded": 0,
        "uploaded_session": 0,
        "upspeed": 0,
    }


# -------------------------------------------------------------
# Auth Endpoints
# -------------------------------------------------------------


@router.post("/auth/login")
async def qb_login(
    response: Response,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> Response:
    """Handle qBittorrent authentication."""
    if settings.api_auth_key and not (
        hmac.compare_digest(password, settings.api_auth_key)
        or hmac.compare_digest(username, settings.api_auth_key)
    ):
        return Response(content="Fails.", media_type="text/plain", status_code=200)

    # Set authentication cookie
    res = Response(content="Ok.", media_type="text/plain")
    res.set_cookie(
        key="SID",
        value="debrid-hub-session",
        httponly=True,
        samesite="lax",
        path="/",
    )
    return res


@router.post("/auth/logout")
async def qb_logout() -> Response:
    """Clear session and log out."""
    res = Response(content="Ok.", media_type="text/plain")
    res.delete_cookie(key="SID", path="/")
    return res


# -------------------------------------------------------------
# Application & Preferences Endpoints
# -------------------------------------------------------------


@router.get("/app/version")
async def qb_app_version(
    _settings: Annotated[Settings, Depends(require_qb_auth)],
) -> Response:
    """Return emulated qBittorrent version."""
    return Response(content="v4.5.2", media_type="text/plain")


@router.get("/app/webapiVersion")
async def qb_webapi_version(
    _settings: Annotated[Settings, Depends(require_qb_auth)],
) -> Response:
    """Return emulated qBittorrent Web API version."""
    return Response(content="2.8.19", media_type="text/plain")


@router.get("/app/buildInfo")
async def qb_build_info(
    _settings: Annotated[Settings, Depends(require_qb_auth)],
) -> dict[str, Any]:
    """Return simulated build environment info."""
    return {
        "qt": "5.15.2",
        "libtorrent": "1.2.14.0",
        "boost": "1.76.0",
        "openssl": "1.1.1k",
        "bitness": 64,
    }


@router.get("/app/preferences")
async def qb_preferences(
    settings: Annotated[Settings, Depends(require_qb_auth)],
) -> dict[str, Any]:
    """Return qBittorrent preferences required by Sonarr / Radarr."""
    return {
        "save_path": settings.download_dir,
        "temp_path": settings.download_dir,
        "scan_dirs": {},
        "download_in_scan_dirs": {},
        "mail_notification_email": "",
        "mail_notification_smtp": "",
        "mail_notification_ssl_enabled": False,
        "mail_notification_auth_enabled": False,
        "mail_notification_username": "",
        "mail_notification_password": "",
        "autorun_enabled": False,
        "autorun_program": "",
        "queueing_enabled": False,
        "max_active_downloads": settings.max_concurrent_downloads,
        "max_active_torrents": settings.max_concurrent_downloads * 2,
        "max_active_uploads": 0,
        "dont_count_slow_torrents": False,
        "slow_torrent_dl_rate_threshold": 2,
        "slow_torrent_ul_rate_threshold": 2,
        "slow_torrent_inactive_timer": 60,
        "max_ratio_enabled": False,
        "max_ratio": -1,
        "max_ratio_act": 0,
        "web_ui_port": 8080,
    }


@router.get("/app/defaultSavePath")
async def qb_default_save_path(
    settings: Annotated[Settings, Depends(require_qb_auth)],
) -> Response:
    """Return default download folder path."""
    return Response(content=settings.download_dir, media_type="text/plain")


# -------------------------------------------------------------
# Torrents Management Endpoints
# -------------------------------------------------------------


@router.get("/torrents/info")
async def qb_torrents_info(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    filter_status: Annotated[str | None, Query(alias="filter")] = None,
    category: Annotated[str | None, Query()] = None,
    hashes: Annotated[str | None, Query()] = None,
    auth_cookie: Annotated[str | None, Cookie(alias="SID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> list[dict[str, Any]]:
    """Return list of torrents matching filter criteria."""
    if not verify_qb_auth(settings, request, auth_cookie=auth_cookie, x_api_key=x_api_key):
        raise HTTPException(status_code=403, detail="Forbidden")

    store = get_job_store(settings)
    jobs = store.list_jobs(limit=1000)

    # Filter by hashes if specified (separated by pipe |)
    if hashes:
        requested_hashes = {h.strip().lower() for h in hashes.split("|") if h.strip()}
        jobs = [j for j in jobs if format_torrent_hash(j) in requested_hashes or j.job_id in requested_hashes]

    # Filter by category if specified
    if category:
        cat_lower = category.strip().lower()
        jobs = [j for j in jobs if (j.category or "").strip().lower() == cat_lower]

    # Filter by status string if specified
    if filter_status:
        f_lower = filter_status.strip().lower()
        if f_lower == "downloading":
            jobs = [
                j
                for j in jobs
                if j.status
                in (
                    JobStatus.QUEUED,
                    JobStatus.RESOLVING_DEBRID,
                    JobStatus.DOWNLOADING,
                    JobStatus.EXTRACTING,
                )
            ]
        elif f_lower in ("completed", "seeding", "paused"):
            jobs = [j for j in jobs if j.status == JobStatus.COMPLETED]
        elif f_lower == "active":
            jobs = [
                j
                for j in jobs
                if j.status in (JobStatus.RESOLVING_DEBRID, JobStatus.DOWNLOADING, JobStatus.EXTRACTING)
            ]
        elif f_lower == "inactive":
            jobs = [j for j in jobs if j.status in (JobStatus.QUEUED, JobStatus.COMPLETED, JobStatus.FAILED)]
        elif f_lower == "errored":
            jobs = [j for j in jobs if j.status == JobStatus.FAILED]

    return [job_to_torrent_dict(j) for j in jobs]


@router.post("/torrents/add")
async def qb_torrents_add(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    urls: Annotated[str | None, Form()] = None,
    torrents: Annotated[list[UploadFile] | None, File()] = None,
    savepath: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
    tags: Annotated[str | None, Form()] = None,
    paused: Annotated[str | None, Form()] = None,
    auth_cookie: Annotated[str | None, Cookie(alias="SID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Response:
    """Ingest magnet URLs or .torrent files from Sonarr, Radarr, or Prowlarr."""
    if not verify_qb_auth(settings, request, auth_cookie=auth_cookie, x_api_key=x_api_key):
        raise HTTPException(status_code=403, detail="Forbidden")

    store = get_job_store(settings)
    source = determine_source(category)

    # Determine destination subfolder
    subfolder = None
    if savepath and savepath.strip():
        # Strip settings.download_dir prefix if passed
        clean_save = savepath.strip()
        dl_dir = settings.download_dir.rstrip("/")
        if clean_save.startswith(dl_dir):
            rel = clean_save[len(dl_dir) :].strip("/")
            subfolder = rel if rel else None
        else:
            subfolder = Path(clean_save).name
    elif category and category.strip():
        subfolder = category.strip()

    from app.main import (
        JOBS,
        get_effective_rd_key,
        process_download_job,
        sanitize_subfolder,
    )

    output_dir = sanitize_subfolder(settings.download_dir, subfolder)
    rd_api_key = get_effective_rd_key(None, settings)

    items_added = 0

    # 1. Process URL(s)
    if urls and urls.strip():
        url_list = [u.strip() for u in urls.splitlines() if u.strip()]
        for u in url_list:
            info_h = extract_magnet_hash(u) or hashlib.sha1(u.encode()).hexdigest().lower()
            link_type = classify_url(u)
            job_id = str(uuid.uuid4())

            name = extract_magnet_name(u)
            filenames = [name] if name else []

            job = DownloadJob(
                job_id=job_id,
                source_url=u,
                link_type=link_type,
                status=JobStatus.QUEUED,
                output_dir=output_dir,
                category=category,
                source=source,
                info_hash=info_h,
                filenames=filenames,
            )
            JOBS[job_id] = job
            store.upsert_job(job)
            EVENT_BUS.publish("job_created", job.model_dump(mode="json"), job_id=job.job_id)
            background_tasks.add_task(process_download_job, job_id, rd_api_key, settings)
            items_added += 1

    # 2. Process uploaded .torrent file(s)
    if torrents:
        for t in torrents:
            try:
                content = await t.read()
                if not content:
                    continue
                info_h, name = extract_torrent_metadata(content)
                magnet_uri = create_magnet_from_torrent(info_h, name)
                job_id = str(uuid.uuid4())

                job = DownloadJob(
                    job_id=job_id,
                    source_url=magnet_uri,
                    link_type=LinkType.TORRENT,
                    status=JobStatus.QUEUED,
                    output_dir=output_dir,
                    category=category,
                    source=source,
                    info_hash=info_h,
                    filenames=[name],
                )
                JOBS[job_id] = job
                store.upsert_job(job)
                EVENT_BUS.publish("job_created", job.model_dump(mode="json"), job_id=job.job_id)
                background_tasks.add_task(process_download_job, job_id, rd_api_key, settings)
                items_added += 1
            except (ValueError, OSError) as exc:
                logger.warning("Error processing uploaded .torrent file %s: %s", t.filename, exc)

    logger.info("qBittorrent add: queued %d job(s) from category '%s' (source: %s)", items_added, category, source)
    return Response(content="Ok.", media_type="text/plain")


@router.post("/torrents/delete")
async def qb_torrents_delete(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    hashes: Annotated[str, Form()],
    delete_files: Annotated[bool, Form(alias="deleteFiles")] = False,
    auth_cookie: Annotated[str | None, Cookie(alias="SID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Response:
    """Delete torrents requested by Sonarr / Radarr cleanup."""
    if not verify_qb_auth(settings, request, auth_cookie=auth_cookie, x_api_key=x_api_key):
        raise HTTPException(status_code=403, detail="Forbidden")

    from app.main import ACTIVE_TASKS, JOBS

    store = get_job_store(settings)
    target_hashes = [h.strip().lower() for h in hashes.split("|") if h.strip()]

    for th in target_hashes:
        job = store.get_job_by_hash(th)
        if not job:
            job = JOBS.get(th)

        if job:
            # Cancel running task if active
            task = ACTIVE_TASKS.get(job.job_id)
            if task and not task.done():
                task.cancel()

            # Delete physical files if requested (strictly contained inside job.output_dir)
            if delete_files and job.filenames:
                base_dir = Path(job.output_dir).resolve()
                for fn in job.filenames:
                    raw_path = Path(job.output_dir) / fn
                    if raw_path.is_symlink():
                        continue
                    target = raw_path.resolve()
                    if target.is_relative_to(base_dir) and target.is_file():
                        try:
                            target.unlink()
                        except OSError:
                            pass

            store.delete_job(job.job_id)
            JOBS.pop(job.job_id, None)
            EVENT_BUS.publish("job_deleted", {"job_id": job.job_id}, job_id=job.job_id)

    return Response(content="Ok.", media_type="text/plain")


@router.get("/torrents/properties")
async def qb_torrents_properties(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    torrent_hash: Annotated[str, Query(alias="hash")],
    auth_cookie: Annotated[str | None, Cookie(alias="SID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> dict[str, Any]:
    """Return detailed properties for a torrent."""
    if not verify_qb_auth(settings, request, auth_cookie=auth_cookie, x_api_key=x_api_key):
        raise HTTPException(status_code=403, detail="Forbidden")

    store = get_job_store(settings)
    job = store.get_job_by_hash(torrent_hash)
    if not job:
        raise HTTPException(status_code=404, detail="Torrent not found")

    item = job_to_torrent_dict(job)
    return {
        "save_path": job.output_dir,
        "creation_date": item["added_on"],
        "piece_size": 1048576,
        "pieces_num": 100,
        "pieces_have": 100 if job.status == JobStatus.COMPLETED else int(job.progress_percent),
        "total_wasted": 0,
        "total_uploaded": 0,
        "total_downloaded": item["downloaded"],
        "up_limit": -1,
        "dl_limit": -1,
        "time_elapsed": 60,
        "seeding_time": 0,
        "nb_connections": 0,
        "nb_connections_limit": 100,
        "share_ratio": 1.0,
        "addition_date": item["added_on"],
        "completion_date": item["completion_on"],
    }


@router.get("/torrents/files")
async def qb_torrents_files(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    torrent_hash: Annotated[str, Query(alias="hash")],
    auth_cookie: Annotated[str | None, Cookie(alias="SID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> list[dict[str, Any]]:
    """Return file breakdown for a torrent."""
    if not verify_qb_auth(settings, request, auth_cookie=auth_cookie, x_api_key=x_api_key):
        raise HTTPException(status_code=403, detail="Forbidden")

    store = get_job_store(settings)
    job = store.get_job_by_hash(torrent_hash)
    if not job:
        raise HTTPException(status_code=404, detail="Torrent not found")

    names = job.filenames or job.extracted_files or [job.source_url]
    progress = 1.0 if job.status == JobStatus.COMPLETED else (job.progress_percent / 100.0)

    return [
        {
            "index": idx,
            "name": name,
            "size": 1_000_000_000,
            "progress": progress,
            "priority": 1,
            "is_seed": True,
            "piece_range": [0, 100],
            "availability": 1.0,
        }
        for idx, name in enumerate(names)
    ]


@router.get("/torrents/categories")
async def qb_torrents_categories(
    settings: Annotated[Settings, Depends(require_qb_auth)],
) -> dict[str, Any]:
    """Return registered categories."""
    store = get_job_store(settings)
    jobs = store.list_jobs(limit=500)
    cats = {}
    for j in jobs:
        if j.category and j.category not in cats:
            cats[j.category] = {
                "name": j.category,
                "savePath": j.output_dir,
            }
    if not cats:
        cats["tv"] = {"name": "tv", "savePath": f"{settings.download_dir}/tv"}
        cats["movies"] = {"name": "movies", "savePath": f"{settings.download_dir}/movies"}
    return cats


@router.post("/torrents/pause")
async def qb_torrents_pause(
    _settings: Annotated[Settings, Depends(require_qb_auth)],
) -> Response:
    """Acknowledge torrent pause command."""
    return Response(content="Ok.", media_type="text/plain")


@router.post("/torrents/resume")
async def qb_torrents_resume(
    _settings: Annotated[Settings, Depends(require_qb_auth)],
) -> Response:
    """Acknowledge torrent resume command."""
    return Response(content="Ok.", media_type="text/plain")


@router.post("/torrents/setCategory")
async def qb_torrents_set_category(
    settings: Annotated[Settings, Depends(require_qb_auth)],
    hashes: Annotated[str, Form()],
    category: Annotated[str, Form()] = "",
) -> Response:
    """Update category on existing torrents."""
    store = get_job_store(settings)
    from app.main import JOBS

    for h in [x.strip().lower() for x in hashes.split("|") if x.strip()]:
        job = store.get_job_by_hash(h)
        if job:
            job.category = category
            job.source = determine_source(category)
            store.upsert_job(job)
            JOBS[job.job_id] = job

    return Response(content="Ok.", media_type="text/plain")
