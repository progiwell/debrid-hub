"""SQLite persistence store for download jobs history and crash recovery."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.schemas import DownloadJob, JobStatus, LinkType

logger = logging.getLogger(__name__)


class JobStore:
    """Thread-safe SQLite store for download jobs."""

    def __init__(self, db_path: str | Path = "/config/jobs.db") -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        if self.db_path != ":memory:":
            try:
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Could not create directory for %s: %s. Using in-memory database.", self.db_path, exc)
                self.db_path = ":memory:"
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_db()

    def close(self) -> None:
        """Close SQLite database connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Error closing SQLite connection: %s", exc)

    def init_db(self) -> None:
        """Create jobs table and indexes if they do not exist."""
        with self._lock:
            if self.db_path != ":memory:":
                try:
                    self._conn.execute("PRAGMA journal_mode=WAL;")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to enable WAL mode: %s", exc)

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    link_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolved_urls_json TEXT NOT NULL DEFAULT '[]',
                    output_dir TEXT NOT NULL,
                    filenames_json TEXT NOT NULL DEFAULT '[]',
                    extracted_files_json TEXT NOT NULL DEFAULT '[]',
                    total_files INTEGER NOT NULL DEFAULT 0,
                    completed_files INTEGER NOT NULL DEFAULT 0,
                    progress_percent REAL NOT NULL DEFAULT 0.0,
                    download_speed TEXT,
                    error TEXT,
                    category TEXT,
                    source TEXT,
                    client_id TEXT,
                    client_name TEXT,
                    info_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )
            # Automatic schema migration for existing databases
            cursor = self._conn.execute("PRAGMA table_info(jobs);")
            columns = {row["name"] for row in cursor.fetchall()}
            if "category" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN category TEXT;")
            if "source" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN source TEXT;")
            if "client_id" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN client_id TEXT;")
            if "client_name" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN client_name TEXT;")
            if "info_hash" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN info_hash TEXT;")
            if "download_speed" not in columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN download_speed TEXT;")

            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_info_hash ON jobs(info_hash);")
            self._conn.commit()

    def _row_to_job(self, row: sqlite3.Row) -> DownloadJob:
        keys = row.keys()
        return DownloadJob(
            job_id=row["job_id"],
            source_url=row["source_url"],
            link_type=LinkType(row["link_type"]),
            status=JobStatus(row["status"]),
            resolved_urls=json.loads(row["resolved_urls_json"] or "[]"),
            output_dir=row["output_dir"],
            filenames=json.loads(row["filenames_json"] or "[]"),
            extracted_files=json.loads(row["extracted_files_json"] or "[]"),
            total_files=row["total_files"],
            completed_files=row["completed_files"],
            progress_percent=row["progress_percent"],
            download_speed=row["download_speed"] if "download_speed" in keys else None,
            error=row["error"],
            category=row["category"] if "category" in keys else None,
            source=row["source"] if "source" in keys else None,
            client_id=row["client_id"] if "client_id" in keys else None,
            client_name=row["client_name"] if "client_name" in keys else None,
            info_hash=row["info_hash"] if "info_hash" in keys else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    def upsert_job(self, job: DownloadJob) -> None:
        """Insert or update a job record."""
        now_iso = datetime.now(UTC).isoformat()
        job.updated_at = now_iso
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED) and not job.completed_at:
            job.completed_at = now_iso

        sql = """
            INSERT INTO jobs (
                job_id, source_url, link_type, status,
                resolved_urls_json, output_dir, filenames_json, extracted_files_json,
                total_files, completed_files, progress_percent, download_speed, error,
                category, source, client_id, client_name, info_hash,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                source_url=excluded.source_url,
                link_type=excluded.link_type,
                status=excluded.status,
                resolved_urls_json=excluded.resolved_urls_json,
                output_dir=excluded.output_dir,
                filenames_json=excluded.filenames_json,
                extracted_files_json=excluded.extracted_files_json,
                total_files=excluded.total_files,
                completed_files=excluded.completed_files,
                progress_percent=excluded.progress_percent,
                download_speed=excluded.download_speed,
                error=excluded.error,
                category=excluded.category,
                source=excluded.source,
                client_id=excluded.client_id,
                client_name=excluded.client_name,
                info_hash=excluded.info_hash,
                updated_at=excluded.updated_at,
                completed_at=COALESCE(excluded.completed_at, jobs.completed_at);
        """
        params = (
            job.job_id,
            job.source_url,
            job.link_type.value if hasattr(job.link_type, "value") else str(job.link_type),
            job.status.value if hasattr(job.status, "value") else str(job.status),
            json.dumps(job.resolved_urls),
            job.output_dir,
            json.dumps(job.filenames),
            json.dumps(job.extracted_files),
            job.total_files,
            job.completed_files,
            job.progress_percent,
            job.download_speed,
            job.error,
            job.category,
            job.source,
            job.client_id,
            job.client_name,
            job.info_hash,
            job.created_at,
            job.updated_at,
            job.completed_at,
        )
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def get_job_by_hash(self, info_hash: str) -> DownloadJob | None:
        """Fetch a single job by its info_hash or job_id."""
        normalized = info_hash.strip().lower()
        sql = "SELECT * FROM jobs WHERE LOWER(info_hash) = ? OR LOWER(job_id) = ? LIMIT 1;"
        with self._lock:
            cursor = self._conn.execute(sql, (normalized, normalized))
            row = cursor.fetchone()
            if row:
                return self._row_to_job(row)
        return None

    def get_job(self, job_id: str) -> DownloadJob | None:
        """Fetch a single job by id."""
        sql = "SELECT * FROM jobs WHERE job_id = ?;"
        with self._lock:
            cursor = self._conn.execute(sql, (job_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_job(row)
        return None

    def list_jobs(
        self,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DownloadJob]:
        """List jobs ordered by created_at descending."""
        if status:
            sql = "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?;"
            params = (status.value if hasattr(status, "value") else str(status), limit, offset)
        else:
            sql = "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?;"
            params = (limit, offset)

        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [self._row_to_job(row) for row in cursor.fetchall()]

    def delete_job(self, job_id: str) -> bool:
        """Delete a job by id. Returns True if deleted, False if not found."""
        sql = "DELETE FROM jobs WHERE job_id = ?;"
        with self._lock:
            cursor = self._conn.execute(sql, (job_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def clear_jobs(self, status: JobStatus | None = None, clear_all: bool = False) -> int:
        """Clear jobs matching criteria. Returns number of deleted rows."""
        with self._lock:
            if clear_all:
                cursor = self._conn.execute("DELETE FROM jobs;")
            elif status:
                status_val = status.value if hasattr(status, "value") else str(status)
                cursor = self._conn.execute("DELETE FROM jobs WHERE status = ?;", (status_val,))
            else:
                cursor = self._conn.execute("DELETE FROM jobs WHERE status IN ('completed', 'failed');")
            self._conn.commit()
            return cursor.rowcount

    def count_jobs(self, status: JobStatus | None = None) -> int:
        """Return total count of jobs."""
        with self._lock:
            if status:
                status_val = status.value if hasattr(status, "value") else str(status)
                cursor = self._conn.execute("SELECT COUNT(*) FROM jobs WHERE status = ?;", (status_val,))
            else:
                cursor = self._conn.execute("SELECT COUNT(*) FROM jobs;")
            row = cursor.fetchone()
            return row[0] if row else 0

    def recover_interrupted_jobs(self) -> int:
        """Transition any non-terminal jobs left in active state to failed on daemon restart."""
        now_iso = datetime.now(UTC).isoformat()
        sql = """
            UPDATE jobs
            SET status = ?,
                error = ?,
                updated_at = ?,
                completed_at = COALESCE(completed_at, ?)
            WHERE status NOT IN (?, ?);
        """
        params = (
            JobStatus.FAILED.value,
            "⚠️ Interrupted by container restart",
            now_iso,
            now_iso,
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
        )
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor.rowcount


JOB_STORE_INSTANCE: JobStore | None = None


def get_job_store(settings: Settings | None = None) -> JobStore:
    """Return singleton JobStore instance."""
    global JOB_STORE_INSTANCE
    if JOB_STORE_INSTANCE is None:
        s = settings or get_settings()
        JOB_STORE_INSTANCE = JobStore(db_path=s.jobs_db_path)
    return JOB_STORE_INSTANCE


def reset_job_store(new_store: JobStore | None = None) -> None:
    """Reset or replace singleton for test isolation."""
    global JOB_STORE_INSTANCE
    if JOB_STORE_INSTANCE is not None:
        try:
            JOB_STORE_INSTANCE.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error closing previous JobStore: %s", exc)
    JOB_STORE_INSTANCE = new_store
