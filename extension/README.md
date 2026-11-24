# 🧩 Debrid Hub — Browser Extension (`extension/`)

Manifest V3 browser extension for Chrome, Brave, Edge, and Firefox that unifies torrent magnets, `.torrent` uploads, info-hashes, premium file hoster links (`1fichier`, `rapidgator`, `mega`, etc.), and `Click'n'Load` (`127.0.0.1:9666`) into a single right-click action targeting your **Debrid Hub** server over LAN, Tailscale, or localhost.

---

## 🚀 Features

- **1-Click Context Menu**: Right-click any link, info-hash, highlighted text block, or anywhere on a downloadable hoster file landing page $\to$ **"Download with Debrid Hub"**.
- **1-Click Active Tab File Page Detector**: When browsing on a supported hoster file page (`1fichier`, `rapidgator`, `mega`, `mediafire`, `pixeldrain`, `turbobit`, `nitroflare`, etc.) or direct media link, opening the extension popup displays a prominent **`⬇️ Download Current Page File`** banner to queue the file in 1 click.
- **Multi-Link Text Extractor**: Highlight a block of text containing multiple magnet and hoster links; the extension parses, strips punctuation, and queues all links in parallel.
- **Click'n'Load (127.0.0.1:9666) Interceptor**: Transparently intercepts Click'n'Load v2 form submissions, decrypts AES-128-CBC payloads using Web Crypto (`crypto.subtle`) without `eval`, and sends them to your server while showing in-page toast feedback.
- **3-Tab Popup Interface**: Organized into **`📊 Jobs`** (default), **`📥 Add`** (paste URLs / drop `.torrent` files), and **`⚙️ Settings`**. Defaults to **`📊 Jobs`** on browser startup and remembers your last-viewed tab for the current browser session (`chrome.storage.session`), automatically switching to **`📊 Jobs`** when submitting new downloads.
- **Non-Destructive Capped Queue & Interactive Summary Filter Pills**: Displays live status counter pills (**`⚡ Active`**, **`✅ Completed`**, **`⚠️ Failed`**) that you can click to filter the queue strictly to that status (with contextual empty states), and click again to toggle back to the default view (all active, all failed, plus 5 most recent completed jobs without wiping server history)—use **`🌐 Open Server Dashboard`** to manage or clear full history.
- **Scoped Queue & Customizable Browser Identity**: Filter between **`💻 This Browser`** (default, keeping notifications and badge counts focused on links you queued from this browser) and **`🌐 All Jobs`** (including `*arr` and other devices), and customize your **Browser / Device Name** in Settings to identify origins on the Server Dashboard.
- **Live Active Job Badge**: Action badge icon shows real-time count of active jobs (`queued`, `resolving_debrid`, `downloading`, `extracting`) updated via background alarm.
- **Optional Desktop Notifications**: Native system notifications upon download and archive extraction completion, configurable with a toggle in settings.
- **Connectivity Tester**: Built-in `Test Connection` against `GET /health` with live daemon version reporting.

---

## 📦 Build & Installation Instructions

The extension is authored in modular **Vanilla TypeScript** (`extension/src/*.ts`) and **Tailwind CSS** (`extension/src/styles.css`). Compiled `.js`/`.css` bundles are gitignored and generated on demand.

```bash
# From the repository root:
npm ci
npm run build:ext

# Sync Firefox assets, validate Chrome + Firefox manifests, and optionally package dist/*.zip/*.xpi
python3 extension/build.py --sync --validate
python3 extension/build.py --package
```

During local development, run `npm run watch` to rebuild TypeScript bundles automatically on save.

### Option A: Chromium Browsers (Google Chrome, Brave, Microsoft Edge)

1. Build the extension (`npm run build:ext` or `python3 extension/build.py --sync`).
2. Open your browser's extension management page:
   - **Chrome**: `chrome://extensions/`
   - **Brave**: `brave://extensions/`
   - **Edge**: `edge://extensions/`
3. Enable **Developer mode** (toggle in the top-right corner).
4. Click the **Load unpacked** button.
5. Select the `extension/` folder from this repository.
6. The **Debrid Hub** extension icon will appear in your browser toolbar. Pin it for easy access.

---

### Option B: Mozilla Firefox

1. Build and sync Firefox assets (`python3 extension/build.py --sync` or `npm run build:ext`).
2. Open Firefox and navigate to `about:debugging#/runtime/this-firefox`.
3. Under **Temporary Extensions**, click **Load Temporary Add-on...**.
4. Select `manifest.json` inside the `extension/firefox/` directory.
5. The extension will load without any service worker or schema errors.
6. *(Firefox MV3)* Newly loaded add-ons appear under the **🧩 Extensions** puzzle menu by default — click the puzzle icon → gear next to **Debrid Hub** → **Pin to Toolbar**.

---

## ⚙️ Initial Configuration

1. Click the **Debrid Hub** extension icon in your browser toolbar to open the popup.
2. If this is your first time, the popup will open to the **⚙️ Settings** tab automatically.
3. Configure your connection settings:
   - **Server Profiles**: Click **`+ Add Server`** to save multiple server endpoints (e.g., Tailscale vs. LAN, or secondary servers). When 2 or more profiles exist, a dropdown switcher appears in the popup header.
   - **Server Name**: Friendly label for the server profile (e.g., `Home Server`, `Local Docker`).
   - **Server Endpoint URL**: The address of your Debrid Hub server:
     - Tailscale: `http://100.x.y.z:38415` or `http://your-server.tailscale.net:38415`
     - Local Area Network (LAN): `http://192.168.1.100:38415` or `http://server.local:38415`
   - **Server Auth Secret** *(Optional)*: Secret token if `API_AUTH_KEY` protection is enabled on your server container. *(Note: Real-Debrid API credentials and storage destinations are managed centrally via **`🌐 Open Server Dashboard`** at `/settings`)*.
   - **Browser / Device Name**: Auto-detected label (e.g., `Chrome • macOS`) that you can customize (e.g., `Mac Studio`) to identify downloads on the Server Dashboard.
   - **Desktop notification on completion**: Checkbox to toggle native notifications when downloads or extractions complete (enabled by default).
4. Click **Test Connection** to verify that your server daemon is online.
   - *Firefox Note*: Firefox Manifest V3 restricts network requests until origin permissions are granted. If the connection fails with a permission notice, click the **`[ 🔑 Allow Connection to <hostname> ]`** button directly in the status box. Firefox will show a native prompt requesting permission specifically for your server URL (not all websites). Once allowed, it automatically re-tests the connection.
5. Click **Save Settings**.

---

## 📂 File Structure

```text
extension/
├── README.md              # Extension build, installation & user guide
├── manifest.json          # Canonical Chromium Manifest V3 (service_worker)
├── build.py               # TypeScript/Tailwind compile, Firefox sync, and packaging helper
├── src/                   # Modular TypeScript & Tailwind CSS sources
│   ├── types.ts           # Shared extension & server REST API TypeScript contracts
│   ├── url_parser.ts      # Link classifier, hoster page detector & multi-link extractor
│   ├── permissions.ts     # Firefox/Chrome runtime host permission helpers
│   ├── storage.ts         # Server profile storage, migration & client identity helpers
│   ├── queue_ui.ts        # Queue filtering, summary counters & job card rendering
│   ├── background.ts      # Service worker: context menu, alarm badge & POST /api/download
│   ├── content.ts         # In-page Click'n'Load interceptor & AES-128-CBC decryptor
│   ├── popup.ts           # Popup controller: 3-tab navigation, profiles & SSE monitor
│   └── styles.css         # Tailwind CSS v4 entrypoint & semantic theme tokens
├── popup.html             # Popup UI markup (compiled bundles popup.js/popup.css are gitignored)
├── tests/                 # Node.js unit test suites
│   ├── test_url_parser.js   # Unit tests for url_parser.js
│   └── test_popup_filter.js # Unit tests for popup filtering & manifest contracts
└── firefox/               # Dedicated Firefox Manifest V3 profile
    ├── manifest.json      # Firefox MV3 manifest (scripts & gecko ID)
    └── ...                # Copied shared/compiled extension assets (synced by build_frontend.mjs / build.py)
```
