# 🚀 Debrid Hub & Browser Extension (`debrid-hub`)

A unified, lightweight Real-Debrid hub replacing fragmented legacy tools (JDownloader 2 + RDT-Client + multiple browser extensions). Torrent files, Magnet links, Info-Hashes, and File Hoster URLs go into **Debrid Hub**, get resolved via your Debrid service, and come back to be downloaded (`aria2c`), extracted (`unrar`), and organized directly on your Debrid Hub server:
1. **`server/`**: A lean Alpine Docker daemon (`<50MB` RAM) running Python FastAPI + `aria2c` + `unrar` for high-speed multi-connection downloads and automatic archive extraction directly to local or shared storage.
2. **`extension/`**: A Manifest V3 Chrome/Firefox extension providing a 1-click context menu ("Download with Debrid Hub"), magnet/hoster auto-detection, active tab file page detection, and Click'n'Load (`127.0.0.1:9666`) interception over LAN/Tailscale.

---

## 🏗️ Repository Structure

```text
debrid-hub/
├── AGENTS.md                        # AI Agent collaboration & repository guide
├── README.md                        # Project overview & quickstart
├── .env.template                    # Environment variable template
├── .dockerignore                    # Docker build context exclusions
├── package.json                     # Frontend scripts (typecheck, build, build:ext, build:server, test:ext, watch)
├── tsconfig.json                    # Strict TypeScript compiler configuration
├── scripts/
│   └── build_frontend.mjs           # Shared esbuild + Tailwind CSS bundler
├── .github/
│   └── workflows/                   # CI tests, PR reviewer, & multi-arch Docker publish
├── server/                          # Lean Server Backend (FastAPI + Aria2c + Unrar)
│   ├── Dockerfile                   # Multi-stage Alpine container image (node:22-alpine UI builder → python:3.11-alpine)
│   ├── docker-compose.yml           # Container Manager / Docker Compose specification
│   ├── docker-entrypoint.sh         # Container entrypoint with graceful SIGTERM handling
│   ├── deploy.sh                    # Graceful deployment & update script
│   ├── README.md                    # Deployment & API usage guide
│   ├── requirements.txt             # Production Python dependencies
│   ├── requirements-dev.txt         # Test & linting dependencies
│   ├── app/
│   │   ├── main.py                  # FastAPI application & REST endpoints
│   │   ├── config.py                # Environment & runtime configuration
│   │   ├── db.py                    # SQLite job persistence & crash recovery
│   │   ├── debrid/                  # Multi-Debrid provider package (Real-Debrid, etc.)
│   │   ├── debrid.py                # Backwards-compatibility re-export module
│   │   ├── dependencies.py          # FastAPI auth & settings dependency providers
│   │   ├── downloader.py            # Aria2c multi-connection download runner
│   │   ├── events.py                # In-memory SSE pub/sub event bus
│   │   ├── extractor.py             # Post-download unrar/7z extraction & cleanup
│   │   ├── qbittorrent.py           # qBittorrent Web API v2 emulation layer
│   │   ├── schemas.py               # Pydantic request/response models
│   │   ├── torrent_utils.py         # Magnet URI & bencoded .torrent metadata parser
│   │   ├── static/src/              # Dashboard TypeScript & Tailwind CSS sources (dashboard.ts, styles.css)
│   │   └── templates/
│   │       └── settings.html        # In-browser Web Dashboard & Settings UI
│   └── tests/                       # Pytest unit & integration test suite
├── extension/                       # Unified Browser Extension (Manifest V3)
│   ├── README.md                    # Extension build, installation & usage guide
│   ├── manifest.json                # Chrome MV3 extension configuration
│   ├── build.py                     # Frontend compiler, Firefox sync, manifest validator & bundler helper
│   ├── firefox/                     # Dedicated Firefox MV3 manifest & synced extension assets
│   ├── src/                         # Modular TypeScript & Tailwind CSS sources
│   ├── popup.html                   # Extension popup UI
│   └── tests/                       # Node.js unit tests (test_url_parser.js, test_popup_filter.js)
└── scratch/                         # Ephemeral git worktrees for isolated issue development
```

---

## ⚡ Quickstart

### Local Development & Testing
```bash
# 1. Backend tests (Python)
python3 -m venv venv
venv/bin/pip install -r server/requirements.txt -r server/requirements-dev.txt

# 2. Frontend typecheck, build & extension unit tests (Node.js)
npm ci
npm run typecheck
npm run build
npm run test:ext

# 3. Run Python server tests (after building frontend dashboard assets)
PYTHONPATH=server venv/bin/pytest server/tests/ -x
```

### Running via Docker Compose (Official GHCR Image)
```bash
cp .env.template .env
# Edit .env with your host mount paths (default port: 38415, default download path: /data/downloads)
docker compose -f server/docker-compose.yml up -d
# Open http://<server-ip>:38415/settings in your browser to configure your Real-Debrid API token
```

Following the [TRaSH Guides](https://trash-guides.info/File-and-Folder-Structure/How-to-set-up/Docker/) directory standard, the default download path is `/data/downloads`, enabling instant atomic moves/hardlinks into media managers (Sonarr, Radarr, Plex).

---

## ⚖️ Dual-Use & Legal Disclaimer

**Debrid Hub** is a general-purpose, client-side API interface and self-hosted download manager intended strictly for lawful use (such as downloading open-source software distributions, public-domain archives, personal cloud backups, and other legally authorized files).

- **No Content Hosting or Indexing:** Debrid Hub and its authors do **not** host, store, index, search, scrape, cache, stream, or distribute any media, torrent files, or copyrighted material.
- **Independent Client Software:** This software only communicates with user-configured endpoints and third-party Debrid APIs using credentials supplied directly by the user. Debrid Hub is an independent open-source project and is not affiliated with, sponsored by, or endorsed by Real-Debrid, AllDebrid, TorBox, or any other third-party service.
- **User Responsibility:** Users are solely responsible for all URLs, files, and content they process with this software, and for ensuring their usage complies with all applicable local, national, and international laws as well as the Terms of Service of any third-party providers they choose to connect.
- **No Warranty:** This software is provided **"AS IS"**, without warranty of any kind, express or implied. See [LICENSE](LICENSE) for full terms.

---

## 📄 License

Copyright © 2026 **Progiwell**.

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0-or-later)** — see the [LICENSE](LICENSE) file for details.

*Note: The official Docker image bundles third-party CLI utilities (`aria2c`, `7zip`, and `unrar`) executed as standalone subprocesses; each utility remains governed by its own respective license.*
