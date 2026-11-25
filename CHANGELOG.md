# Changelog

All notable changes to **Debrid Hub** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Automated CI security & static analysis gate (`.github/workflows/security.yml` with Gitleaks, Ruff, Bandit, `pip-audit`, extension manifest audit, and Trivy container scanning) and application security test suites (`server/tests/test_security.py`, `extension/tests/test_security_xss.js`).
- `main` → `prod` release promotion workflow separating `:edge` (`main`) and `:latest`/semver (`prod` & `v*.*.*` tags) GHCR container images.
- Versioned Chrome (`.zip`) and Firefox (`.xpi`) extension release packaging workflow (`.github/workflows/release.yml`) with `SHA256SUMS.txt`.
- Grouped Dependabot configuration (`.github/dependabot.yml`), Node 24-compatible GitHub Actions upgrades, and dual-reviewer PR coverage (`GEMINI_API_KEY` + `GROQ_API_KEY`).

### Security
- Pre-extraction Zip/RAR4/RAR5 path-traversal & symlink checks, post-extraction symlink containment, `aria2c`/`unrar`/`7z` CLI flag delimiters, constant-time API key comparison, `/api/v2/*` route authentication coverage, and XSS escaping hardening.
- Alpine base package upgrades and removal of build-only `setuptools`/`wheel` packages from the runtime Docker image.

## [0.2.0] - 2026-11-24

### Added
- **Server (`server/`)**: Lightweight FastAPI + `aria2c` + `unrar`/`7z` Alpine daemon with SQLite job persistence (`jobs.db`), real-time SSE updates (`/api/status/stream`), qBittorrent Web API v2 emulation (`/api/v2/*`) for Sonarr/Radarr/Prowlarr, and Vanilla TypeScript + Tailwind CSS Web Dashboard.
- **Browser Extension (`extension/`)**: Unified Manifest V3 Chrome & Firefox extension with context-menu queuing, multi-profile server settings, active-tab file-page detection, Click'n'Load (`127.0.0.1:9666`) interception, and live SSE queue monitoring.
- **Legal & Licensing**: GNU GPLv3 (`GPL-3.0-or-later`) license and dual-use disclaimer.
