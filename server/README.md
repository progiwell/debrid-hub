# Debrid Hub — Server Backend (`server/`)

Lean, lightweight Real-Debrid server downloader daemon designed for any standard Docker runtime, Linux host, or home server.

- **Memory Target**: `<50MB` RAM typical usage (256MB safety cap for large extractions).
- **Core Stack**: Python 3.11 Alpine + FastAPI + Aria2c (16 parallel chunk connections) + Unrar auto-extraction.

---

## 1. Prerequisites

- Docker Engine and Docker Compose.
- A host download directory (e.g., `/data/downloads`).
- Real-Debrid API token from [real-debrid.com/apitoken](https://real-debrid.com/apitoken).

---

## 2. Configuration (`.env`)

Copy `.env.template` from the repository root to create your `.env` file in your project folder:

```bash
cp .env.template .env
```

### Primary Host Configuration (Required)

Normal users only need to configure the top section of `.env` for Docker host networking and paths. All download preferences and API credentials can be configured directly in the web dashboard!

```bash
# Host port mapping for Web UI and REST API (Default: 38415)
# Zero-collision high port, avoiding conflicts with 8080/8181/9000/5000
PORT=38415

# Host volume path where completed files will be saved (TRaSH Guides standard: /data/downloads)
NAS_DOWNLOAD_PATH=/data/downloads

# Host persistent config directory where settings and jobs database will live
NAS_CONFIG_PATH=./config

# Optional shared secret to protect your server daemon from unauthorized local network clients
API_AUTH_KEY=

# Non-root container UID/GID matching your host user (Synology: 1026/100, Unraid: 99/100)
PUID=1000
PGID=1000

# File permission mask for shared host storage (002 allows group write)
UMASK=002
```

### Web UI vs. Headless Deployments

- **Standard Setup (Recommended)**: Once the container starts, open `http://<server-ip>:38415/settings` in your browser. Enter your Real-Debrid API key and adjust settings (Download Threads, Auto-Extract). Settings are saved permanently to `/config/settings.json` and persist across all container updates.
- **Headless / Automated Deployments**: If you are automating deployments via Ansible, CI/CD, or Kubernetes and do not want to use the Web UI, you can uncomment the optional variables in the bottom section of `.env` (`RD_API_KEY`, `ARIA2C_MAX_CONNECTIONS`, etc.) to boot the container fully configured from scratch. See [Advanced & Headless Deployments](#7-advanced--headless-deployments) below.

### Primary Environment Variable Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `38415` | Host port exposed for the Web UI and REST API. |
| `NAS_DOWNLOAD_PATH` | `/data/downloads` | Host volume path where files are saved ([TRaSH Guides](https://trash-guides.info/File-and-Folder-Structure/How-to-set-up/Docker/) standard). |
| `NAS_CONFIG_PATH` | `./config` | Host volume path for persistent database and `settings.json`. |
| `API_AUTH_KEY` | `""` | Optional shared secret. If set, requests require matching `X-API-Key`. |
| `PUID` | `1000` | Host User ID used to run the non-root container process via `su-exec`. |
| `PGID` | `1000` | Host Group ID used to run the non-root container process via `su-exec`. |
| `UMASK` | `002` | File creation permissions mask on shared host storage. |

---

## 3. Deployment with Docker Compose

### Option A: Using Docker Compose CLI (Recommended)

1. Create a deployment directory on your Docker host (e.g., `/opt/debrid-hub`) and place `server/docker-compose.yml` and your `.env` file into it (no cloning or manual building required).
2. Pull the pre-built multi-arch image (`ghcr.io/progiwell/debrid-hub:latest`) and launch in detached mode:
   ```bash
   cd /opt/debrid-hub
   docker compose up -d
   ```

---

### Option B: Using a Docker Web UI (Portainer, Dockge, Container Manager, etc.)

1. Create a new Compose stack named `debrid-hub`.
2. Paste or upload `server/docker-compose.yml` along with your `.env` values (`PORT`, `NAS_DOWNLOAD_PATH`, `NAS_CONFIG_PATH`, `UMASK`).
3. Deploy the stack to pull `ghcr.io/progiwell/debrid-hub:latest` and start the service.

---

### Graceful Updates & Deployment

Abruptly replacing or recreating a running container (`docker compose up -d --force-recreate`) can terminate active downloads and trigger false-positive abnormal exit alerts in host monitoring tools and container managers.

To prevent this, gracefully stop the container prior to pulling and recreating. A graceful stop sends `SIGTERM`, allowing the Python FastAPI server and download workers to drain cleanly and exit with code `0`:

#### 1. Using the Deployment Script (`deploy.sh`)
```bash
./deploy.sh
```

#### 2. Using Direct Docker Compose Commands
```bash
docker compose stop debrid-hub && \
docker compose pull && \
docker compose up -d
```

#### 3. Using a Docker Web UI
1. Stop the `debrid-hub` container cleanly and wait until its state reports **Stopped**.
2. Pull the latest image and start or redeploy the stack.

---

### Image Tags & Version Pinning

| Tag | Source | Notes |
| :--- | :--- | :--- |
| `:latest` | `prod` branch / `v*.*.*` tags | Stable production releases (default in `server/docker-compose.yml`). |
| `:0.2` / `:v0.2.0` | `v*.*.*` release tags | Pin to a minor series (`0.2`) or exact patch (`v0.2.0`) for controlled upgrades. |
| `:edge` | `main` branch | Pre-release integration builds for staging/testing before `main` → `prod` promotion. |
| `:sha-<commit>` | Any `main` / `prod` / tag push | Exact immutable commit build. |

To pin your Compose stack to a stable minor release instead of tracking `:latest`:
```yaml
services:
  debrid-hub:
    image: ghcr.io/progiwell/debrid-hub:0.2
```

---

## 4. Testing & Verification via `curl`

### 1. Health Check
```bash
curl http://<server-ip>:38415/health
```
**Expected Response:**
```json
{"status": "ok", "version": "0.1.0"}
```

### 2. Queue a Magnet Link
```bash
curl -X POST http://<server-ip>:38415/api/download \
  -H "Content-Type: application/json" \
  -d '{
    "url": "magnet:?xt=urn:btih:example-hash",
    "subfolder": "movies"
  }'
```
**Expected Response (`202 Accepted`):**
```json
{
  "job_id": "c86e0828-98e9-4458-ba6d-e4aa347e3355",
  "status": "queued",
  "link_type": "magnet",
  "message": "Download job queued"
}
```

### 3. Upload a .torrent File
```bash
curl -X POST http://<server-ip>:38415/api/upload/torrent \
  -F "file=@/path/to/sample.torrent" \
  -F "subfolder=isos"
```
**Expected Response (`202 Accepted`):**
```json
{
  "job_id": "c86e0828-98e9-4458-ba6d-e4aa347e3355",
  "status": "queued",
  "link_type": "torrent",
  "message": "Torrent 'sample' queued for download"
}
```

### 4. Check All Jobs Status
```bash
curl http://<server-ip>:38415/api/status
# Or with optional status filter / pagination:
# curl "http://<server-ip>:38415/api/status?status=completed&limit=10&offset=0"
```

### 5. Check Specific Job Details
```bash
curl http://<server-ip>:38415/api/status/c86e0828-98e9-4458-ba6d-e4aa347e3355
```

### 6. Retry a Failed or Interrupted Job
```bash
curl -X POST http://<server-ip>:38415/api/status/c86e0828-98e9-4458-ba6d-e4aa347e3355/retry
```

### 7. Delete a Specific Job
```bash
curl -X DELETE http://<server-ip>:38415/api/status/c86e0828-98e9-4458-ba6d-e4aa347e3355
```

### 8. Clear Completed or Failed Jobs
```bash
# Clear all completed and failed jobs:
curl -X DELETE http://<server-ip>:38415/api/status

# Or clear only failed jobs:
curl -X DELETE "http://<server-ip>:38415/api/status?status=failed"
```

### 9. Clear All Jobs (With Safety Confirmation)
```bash
curl -X DELETE "http://<server-ip>:38415/api/status/all?confirm=true"
```

### 10. Real-Time Status Stream (Server-Sent Events)
```bash
curl -N -H "Accept: text/event-stream" http://<server-ip>:38415/api/status/stream
# Or with API key authentication:
# curl -N -H "X-API-Key: your_auth_key" http://<server-ip>:38415/api/status/stream
# curl -N "http://<server-ip>:38415/api/status/stream?apiKey=your_auth_key"
```

If `API_AUTH_KEY` is configured in `.env`, include the header on all requests:
```bash
-H "X-API-Key: your_auth_key"
```

---

## 5. Monitoring, Healthcheck & Logs

### Built-in Docker Healthcheck
The container includes an automated `HEALTHCHECK` running every 30s:
```bash
docker inspect --format '{{.State.Health.Status}}' debrid-hub
```
Status will show `healthy`.

### Viewing Logs
- **Docker Web UI**: Open your container dashboard → select `debrid-hub` → **Logs** tab.
- **CLI**:
  ```bash
  docker compose logs -f
  ```

### Resource Utilization
Verify the lean memory footprint:
```bash
docker stats debrid-hub --no-stream
```
Memory consumption will typically remain below `35MB-45MB`.

---

## 6. Media Automation Integration (Sonarr, Radarr, Prowlarr)

`debrid-hub` provides a built-in **qBittorrent Web API v2 emulation layer** (`/api/v2/*`). This allows Sonarr, Radarr, Lidarr, Readarr, and Prowlarr to connect directly as if they were talking to native qBittorrent:

### Adding to Sonarr / Radarr

1. In Sonarr/Radarr, navigate to **Settings** $\to$ **Download Clients** $\to$ click **`+`** $\to$ select **qBittorrent**.
2. Fill in the connection settings:
   - **Name**: `Debrid Hub`
   - **Host**: Your server LAN IP, Docker container name, or Tailscale address (e.g., `192.168.1.100`, `debrid-hub`, or `100.64.0.10`)
   - **Port**: `38415` (or `8080` if communicating within the same Docker bridge network)
   - **Username / Password**: Leave blank (or enter your configured `API_AUTH_KEY` in the Password field)
   - **Category**: `tv` (or `sonarr`) for Sonarr, `movies` (or `radarr`) for Radarr
3. Click **Test** $\to$ verify the green checkmark $\to$ click **Save**.

Downloads initiated by Sonarr and Radarr will automatically show with **`📺 Sonarr`** and **`🎬 Radarr`** badges in the web dashboard, resolve torrents/magnets through Debrid, and notify Sonarr/Radarr when download and extraction are complete for automated library import.

---

### TRaSH Guides & Docker Path Alignment

Following [TRaSH Guides](https://trash-guides.info/File-and-Folder-Structure/How-to-set-up/Docker/) standards, download clients and Servarr apps should ideally share a single parent volume (e.g., `/data`) to enable **Instant Atomic Moves** (`mv` pointer renames) instead of slow, I/O-heavy cross-filesystem copies.

Depending on your Docker environment, choose one of the two standard configurations below:

#### Option A: Unified `/data` Mount (Recommended for TRaSH Setups)
If your Sonarr and Radarr containers mount the entire parent data directory (`- /data:/data`), configure Debrid Hub to match so that all containers share identical internal paths:

```yaml
# server/docker-compose.yml
services:
  debrid-hub:
    ...
    environment:
      - DOWNLOAD_DIR=/data/downloads
    volumes:
      - /data:/data
      - ./config:/config
```

With this setup, Debrid Hub reports download paths directly as `/data/downloads/<category>`, allowing Sonarr and Radarr to import files instantly with **zero Remote Path Mapping configuration required**.

#### Option B: Default `/downloads` Mount + Remote Path Mapping
If keeping Debrid Hub's default volume mapping (`${NAS_DOWNLOAD_PATH:-/data/downloads}:/downloads`), Debrid Hub reports completed downloads as `/downloads/<category>`. 

If your *Arr applications access downloads through a different path (such as `/data/downloads/` or `/mnt/user/data/downloads/`), configure a **Remote Path Mapping** in Sonarr/Radarr:

1. In Sonarr / Radarr, go to **Settings** $\to$ **Download Clients**.
2. Scroll to the bottom to **Remote Path Mappings** and click **`+`**.
3. Fill in:
   - **Host**: Your Debrid Hub host/IP (must match the **Host** field used in your qBittorrent client configuration above)
   - **Remote Path**: `/downloads/`
   - **Local Path**: `/data/downloads/` (your *Arr container's internal path to the downloads directory)
4. Click **Save**.

---

### Permissions & Extraction Best Practices

- **Non-Root Execution (`PUID` / `PGID` + `UMASK=002`)**: Debrid Hub drops root privileges at startup via `su-exec` so `uvicorn`, `aria2c`, `unrar`, and `7z` never run as UID `0`, while matching your host UID/GID (e.g., `1026:100` on Synology DSM or `99:100` on Unraid). Together with `UMASK=002`, completed media is saved with `664` file permissions (`775` for directories), allowing Sonarr and Radarr to read, rename, and clean up files without permission errors.
- **No Unpackerr Required**: Standard torrent clients require running a separate [Unpackerr](https://unpackerr.zip/) container to extract compressed `.rar` archives before importing. Debrid Hub includes **native multi-threaded archive extraction** (`unrar` and `7z`) with automatic archive cleanup (`CLEANUP_AFTER_EXTRACT=true`), delivering clean, ready-to-import media files directly to your *Arr apps.

---

## 7. Advanced & Headless Deployments

For automated environments (such as Kubernetes, Terraform, Ansible, or headless remote VMs) where you want the daemon fully operational upon initial boot without opening the web dashboard:

1. **Uncomment and set Section 2 variables in `.env`**:
   ```bash
   RD_API_KEY=your_real_debrid_api_token_here
   RD_BASE_URL=https://api.real-debrid.com/rest/1.0
   ARIA2C_MAX_CONNECTIONS=16
   ARIA2C_SPLIT=16
   MAX_CONCURRENT_DOWNLOADS=3
   AUTO_EXTRACT=true
   CLEANUP_AFTER_EXTRACT=true
   ```

2. **Headless Environment Variables**:
   | Variable | Default | Description |
   | :--- | :--- | :--- |
   | `RD_API_KEY` | `""` | Initial Real-Debrid API token loaded on boot. |
   | `RD_BASE_URL` | `https://api.real-debrid.com/rest/1.0` | API base endpoint for provider requests. |
   | `ARIA2C_MAX_CONNECTIONS` | `16` | Maximum connections per server for aria2c. |
   | `ARIA2C_SPLIT` | `16` | Number of parallel chunks per download for aria2c. |
   | `MAX_CONCURRENT_DOWNLOADS` | `3` | Maximum number of files downloaded in parallel. |
   | `AUTO_EXTRACT` | `true` | Automatically extract downloaded archive volumes. |
   | `CLEANUP_AFTER_EXTRACT` | `true` | Delete source archive volumes upon successful extraction. |

3. **Deploy headless via CLI**:
   ```bash
   docker compose up -d
   ```
   The service will boot ready to process downloads immediately. When changes are later made via the Web UI, they will be saved to `/config/settings.json`, overriding the initial environment defaults.
