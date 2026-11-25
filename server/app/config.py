"""Configuration settings and persistent config manager for the Debrid Hub server service."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    """Runtime settings loaded from environment variables and overlaid with persistent config."""

    config_file: str = os.getenv("CONFIG_FILE", "/config/settings.json")
    debrid_provider: str = os.getenv("DEBRID_PROVIDER", "realdebrid")
    rd_api_key: str = os.getenv("RD_API_KEY", "")
    rd_base_url: str = os.getenv("RD_BASE_URL", "https://api.real-debrid.com/rest/1.0")
    host: str = os.getenv("HOST", "0.0.0.0")  # nosec B104
    port: int = int(os.getenv("PORT", "8080"))
    api_auth_key: str = os.getenv("API_AUTH_KEY", "")
    download_dir: str = os.getenv("DOWNLOAD_DIR", "/downloads")
    aria2c_max_connections: int = int(os.getenv("ARIA2C_MAX_CONNECTIONS", "16"))
    aria2c_split: int = int(os.getenv("ARIA2C_SPLIT", "16"))
    max_concurrent_downloads: int = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
    auto_extract: bool = os.getenv("AUTO_EXTRACT", "true").lower() in ("1", "true", "yes")
    cleanup_after_extract: bool = os.getenv("CLEANUP_AFTER_EXTRACT", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    jobs_db_path: str = os.getenv("JOBS_DB_PATH", "/config/jobs.db")


SETTINGS_INSTANCE: Settings | None = None


def load_settings() -> Settings:
    """Instantiate settings with environment defaults overlaid with persistent JSON file if present."""
    settings = Settings(
        config_file=os.getenv("CONFIG_FILE", "/config/settings.json"),
        debrid_provider=os.getenv("DEBRID_PROVIDER", "realdebrid"),
        rd_api_key=os.getenv("RD_API_KEY", ""),
        rd_base_url=os.getenv("RD_BASE_URL", "https://api.real-debrid.com/rest/1.0"),
        host=os.getenv("HOST", "0.0.0.0"),  # nosec B104
        port=int(os.getenv("PORT", "8080")),
        api_auth_key=os.getenv("API_AUTH_KEY", ""),
        download_dir=os.getenv("DOWNLOAD_DIR", "/downloads"),
        aria2c_max_connections=int(os.getenv("ARIA2C_MAX_CONNECTIONS", "16")),
        aria2c_split=int(os.getenv("ARIA2C_SPLIT", "16")),
        max_concurrent_downloads=int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3")),
        auto_extract=os.getenv("AUTO_EXTRACT", "true").lower() in ("1", "true", "yes"),
        cleanup_after_extract=os.getenv("CLEANUP_AFTER_EXTRACT", "true").lower()
        in ("1", "true", "yes"),
        jobs_db_path=os.getenv("JOBS_DB_PATH", "/config/jobs.db"),
    )

    p = Path(settings.config_file)
    if p.is_file():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
            for k, v in data.items():
                if hasattr(settings, k) and v is not None:
                    # Cast int and bool safely if needed
                    expected_type = type(getattr(settings, k))
                    if expected_type is int and not isinstance(v, int):
                        v = int(v)
                    elif expected_type is bool and not isinstance(v, bool):
                        v = str(v).lower() in ("1", "true", "yes")
                    setattr(settings, k, v)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load persistent config from %s: %s", p, exc)

    return settings


def save_settings(settings: Settings) -> None:
    """Persist current settings to disk in JSON format."""
    p = Path(settings.config_file)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(asdict(settings), f, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist settings to %s: %s", p, exc)


def get_settings() -> Settings:
    """Return the active singleton Settings instance."""
    global SETTINGS_INSTANCE
    if SETTINGS_INSTANCE is None:
        SETTINGS_INSTANCE = load_settings()
    return SETTINGS_INSTANCE


def update_settings(updates: dict[str, Any]) -> Settings:
    """Update settings in memory and persist changes to disk."""
    settings = get_settings()
    for k, v in updates.items():
        if hasattr(settings, k) and v is not None:
            expected_type = type(getattr(settings, k))
            if expected_type is int and not isinstance(v, int):
                v = int(v)
            elif expected_type is bool and not isinstance(v, bool):
                v = str(v).lower() in ("1", "true", "yes")
            setattr(settings, k, v)

    save_settings(settings)
    return settings


def reset_settings_instance() -> None:
    """Reset singleton for test isolation."""
    global SETTINGS_INSTANCE
    SETTINGS_INSTANCE = None
