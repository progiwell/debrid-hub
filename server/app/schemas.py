"""Pydantic request and response schemas for the Debrid Hub REST API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class LinkType(str, Enum):
    """Classification of incoming download links."""

    MAGNET = "magnet"
    HOSTER = "hoster"
    TORRENT = "torrent"


class JobStatus(str, Enum):
    """Lifecycle states of a server download job."""

    QUEUED = "queued"
    RESOLVING_DEBRID = "resolving_debrid"
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    COMPLETED = "completed"
    FAILED = "failed"


class ResolvedFile(BaseModel):
    """Direct CDN download URL along with provider-resolved file metadata."""

    url: str
    filename: str | None = None
    filesize: int | None = None


class DownloadRequest(BaseModel):
    """Payload sent by the browser extension or MCP agent to trigger a download."""

    url: str = Field(..., description="Magnet URI or file hoster URL to download via Real-Debrid")
    api_key: str | None = Field(
        default=None,
        alias="apiKey",
        description="Optional Real-Debrid API key override or server auth token",
    )
    subfolder: str | None = Field(
        default=None,
        description="Optional relative subfolder inside /downloads (e.g. 'switch')",
    )
    category: str | None = Field(
        default=None,
        description="Optional category tag (e.g. 'tv', 'movies', 'switch')",
    )
    source: str | None = Field(
        default=None,
        description="Origin capture method or client tag (e.g. 'context_menu', 'click_n_load', 'paste', 'upload', 'sonarr', 'radarr')",
    )
    client_id: str | None = Field(
        default=None,
        description="Unique identifier of the submitting browser or client",
    )
    client_name: str | None = Field(
        default=None,
        description="Friendly display name of the submitting browser or device",
    )

    model_config = {"populate_by_name": True}


class DownloadJob(BaseModel):
    """Representation of an active or historical download job."""

    job_id: str
    source_url: str
    link_type: LinkType
    status: JobStatus = JobStatus.QUEUED
    resolved_urls: list[str] = Field(default_factory=list)
    output_dir: str
    filenames: list[str] = Field(default_factory=list)
    extracted_files: list[str] = Field(default_factory=list)
    total_files: int = 0
    completed_files: int = 0
    progress_percent: float = 0.0
    download_speed: str | None = None
    error: str | None = None
    category: str | None = None
    source: str | None = None
    client_id: str | None = None
    client_name: str | None = None
    info_hash: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None


class DownloadResponse(BaseModel):
    """Response returned when a new download request is accepted."""

    job_id: str
    status: JobStatus
    link_type: LinkType
    message: str


class SettingsResponse(BaseModel):
    """Current runtime settings with sensitive secrets masked."""

    debrid_provider: str
    rd_api_key_masked: str
    rd_base_url: str
    download_dir: str
    aria2c_max_connections: int
    aria2c_split: int
    max_concurrent_downloads: int
    auto_extract: bool
    cleanup_after_extract: bool
    supported_providers: list[str] = Field(default_factory=list)


class SettingsUpdateRequest(BaseModel):
    """Payload to update runtime settings on the server daemon without container restart."""

    debrid_provider: str | None = None
    rd_api_key: str | None = None
    rd_base_url: str | None = None
    download_dir: str | None = None
    aria2c_max_connections: int | None = None
    aria2c_split: int | None = None
    max_concurrent_downloads: int | None = None
    auto_extract: bool | None = None
    cleanup_after_extract: bool | None = None


class TestDebridRequest(BaseModel):
    """Request payload to test provider connectivity and API key."""

    provider: str | None = None
    api_key: str | None = Field(default=None, alias="apiKey")

    model_config = {"populate_by_name": True}


class TestDebridResponse(BaseModel):
    """Result of provider account validation."""

    ok: bool
    provider: str
    username: str | None = None
    account_type: str | None = None
    expiration: str | None = None
    message: str | None = None
