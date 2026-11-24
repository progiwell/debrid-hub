"""Unit tests for persistent configuration loader and settings manager."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import (
    Settings,
    get_settings,
    load_settings,
    reset_settings_instance,
    save_settings,
    update_settings,
)


def test_settings_env_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DEBRID_PROVIDER", "realdebrid")
    monkeypatch.setenv("MAX_CONCURRENT_DOWNLOADS", "5")
    monkeypatch.setenv("AUTO_EXTRACT", "false")
    monkeypatch.setenv("CONFIG_FILE", "/non/existent/path.json")

    settings = load_settings()
    assert settings.debrid_provider == "realdebrid"
    assert settings.max_concurrent_downloads == 5
    assert settings.auto_extract is False


def test_persistent_save_and_load(tmp_path: Path) -> None:
    config_file = str(tmp_path / "config" / "settings.json")
    settings = Settings(
        config_file=config_file,
        debrid_provider="realdebrid",
        rd_api_key="token_12345",
        aria2c_max_connections=8,
        max_concurrent_downloads=2,
        auto_extract=True,
    )

    save_settings(settings)
    assert Path(config_file).is_file()

    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["rd_api_key"] == "token_12345"
    assert data["aria2c_max_connections"] == 8

    # Load from disk
    loaded = Settings(config_file=config_file)
    with open(config_file, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    for k, v in disk_data.items():
        setattr(loaded, k, v)

    assert loaded.rd_api_key == "token_12345"
    assert loaded.aria2c_max_connections == 8


def test_get_and_update_settings(tmp_path: Path) -> None:
    config_file = str(tmp_path / "settings.json")
    reset_settings_instance()

    settings = get_settings()
    settings.config_file = config_file

    updated = update_settings({
        "debrid_provider": "alldebrid",
        "aria2c_max_connections": 24,
        "auto_extract": False,
    })

    assert updated.debrid_provider == "alldebrid"
    assert updated.aria2c_max_connections == 24
    assert updated.auto_extract is False

    # Check that it wrote to disk
    assert Path(config_file).is_file()
    with open(config_file, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["debrid_provider"] == "alldebrid"
    assert saved["aria2c_max_connections"] == 24
    assert saved["auto_extract"] is False

    reset_settings_instance()
