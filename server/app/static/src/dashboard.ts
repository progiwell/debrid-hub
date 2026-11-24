/** Debrid Hub Server Web Dashboard Controller (Vanilla TypeScript). */

import type {
  DownloadJob,
  DownloadResponse,
  JobStatus,
  SettingsResponse,
  SettingsUpdateRequest,
  StatusFilter,
  TestDebridRequest,
  TestDebridResponse,
} from "../../../../extension/src/types";

type DashboardTab = "jobs" | "add" | "settings";

const ACTIVE_STATUSES: readonly JobStatus[] = ["queued", "resolving_debrid", "downloading", "extracting"];

const STATUS_FILTERS: Record<
  Exclude<StatusFilter, null>,
  { predicate: (job: DownloadJob) => boolean; emptyText: string; label: string }
> = {
  active: {
    predicate: (j) => (ACTIVE_STATUSES as readonly string[]).includes(j.status),
    emptyText: "⚡ No active downloads running.",
    label: "active",
  },
  completed: {
    predicate: (j) => j.status === "completed",
    emptyText: "✅ No completed downloads found.",
    label: "completed",
  },
  failed: {
    predicate: (j) => j.status === "failed",
    emptyText: "⚠️ No failed downloads found.",
    label: "failed",
  },
};

export function escapeHtml(str: string | null | undefined): string {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sortJobsNewestFirst(jobs: DownloadJob[]): DownloadJob[] {
  return jobs.slice().sort((a, b) => {
    const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
    const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
    return tb - ta;
  });
}

function formatOriginBadgeHtml(source: string | null | undefined): string {
  const src = (source || "").toLowerCase();
  if (!src) return "";
  if (src.includes("sonarr")) return `<span class="origin-badge badge-sonarr">📺 Sonarr</span>`;
  if (src.includes("radarr")) return `<span class="origin-badge badge-radarr">🎬 Radarr</span>`;
  if (src.includes("lidarr")) return `<span class="origin-badge badge-lidarr">🎵 Lidarr</span>`;
  if (src.includes("readarr")) return `<span class="origin-badge badge-readarr">📚 Readarr</span>`;
  if (src === "context_menu" || src === "contextmenu" || src === "extension") {
    return `<span class="origin-badge badge-context-menu">🖱️ Context Menu</span>`;
  }
  if (src === "click_n_load" || src === "cnl") {
    return `<span class="origin-badge badge-click-n-load">⚡ Click'n'Load</span>`;
  }
  if (src === "paste" || src === "web" || src === "manual") {
    return `<span class="origin-badge badge-paste">📋 Paste</span>`;
  }
  if (src === "upload") return `<span class="origin-badge badge-upload">📁 Upload</span>`;
  return `<span class="origin-badge badge-generic-origin">🤖 ${escapeHtml(source)}</span>`;
}

function formatTypeBadgeHtml(linkType: string | null | undefined): string {
  const lt = (linkType || "").toLowerCase();
  if (lt === "magnet") return `<span class="type-badge type-magnet">🧲 Magnet</span>`;
  if (lt === "torrent") return `<span class="type-badge type-torrent">📦 Torrent</span>`;
  if (lt === "hoster") return `<span class="type-badge type-hoster">🔗 Hoster</span>`;
  return "";
}

function resolveJobTitle(job: DownloadJob): string {
  if (job.filenames && job.filenames.length > 0) {
    return job.filenames.join(", ");
  }
  if (job.source_url && job.source_url.startsWith("magnet:?")) {
    const match = job.source_url.match(/[?&]dn=([^&]+)/);
    if (match) {
      try {
        return decodeURIComponent(match[1].replace(/\+/g, " "));
      } catch {
        return match[1];
      }
    }
  }
  if (job.source_url && job.source_url.length > 80) {
    return `${job.source_url.substring(0, 80)}...`;
  }
  return job.source_url || job.job_id;
}

export function renderJobCardHtml(job: DownloadJob): string {
  const title = resolveJobTitle(job);
  const statusClass = `badge-${job.status}`;
  const rawPct = Number(job.progress_percent);
  const pct = Number.isFinite(rawPct) ? Math.min(Math.max(rawPct, 0), 100) : 0;
  const isActive = ["downloading", "resolving_debrid"].includes(job.status);
  const isExtracting = job.status === "extracting";
  const isFailed = job.status === "failed";
  const safeId = escapeHtml(job.job_id);

  const retryBtn = isFailed
    ? `<button type="button" class="btn-secondary btn-sm" data-action="retry" data-job-id="${safeId}">🔁 Retry</button>`
    : "";
  const deleteBtn = `<button type="button" class="btn-danger btn-sm" data-action="delete" data-job-id="${safeId}">✕ Delete</button>`;
  const extractedHtml =
    job.extracted_files && job.extracted_files.length > 0
      ? `<div class="job-extracted">📦 Extracted: ${escapeHtml(job.extracted_files.join(", "))}</div>`
      : "";
  const errorHtml = job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : "";
  const totalFiles = Number(job.total_files) || 0;
  const completedFiles = Number(job.completed_files) || 0;
  const filesInfo = totalFiles > 0 ? `${completedFiles} / ${totalFiles} files (${pct.toFixed(1)}%)` : `${pct.toFixed(1)}%`;

  const originHtml = formatOriginBadgeHtml(job.source);
  const typeHtml = formatTypeBadgeHtml(job.link_type);
  const deviceHtml = job.client_name ? `<span class="origin-badge badge-device">💻 ${escapeHtml(job.client_name)}</span>` : "";
  const categoryHtml = job.category ? `<span class="origin-badge badge-generic-origin">📁 ${escapeHtml(job.category)}</span>` : "";
  const speedHtml =
    job.status === "downloading" && job.download_speed
      ? `<span class="text-[var(--info)] font-semibold mr-2">⚡ ${escapeHtml(job.download_speed)}</span>`
      : "";

  return `
    <div class="job-card" id="card-${safeId}">
      <div class="job-header">
        <div class="min-w-0">
          <div class="job-title" title="${escapeHtml(job.source_url)}">${escapeHtml(title)}</div>
          <div class="tag-group">${originHtml}${deviceHtml}${typeHtml}${categoryHtml}</div>
        </div>
        <span class="status-badge ${escapeHtml(statusClass)}">${escapeHtml(job.status.replace("_", " "))}</span>
      </div>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill ${isActive ? "active" : ""} ${isExtracting ? "extracting" : ""}" style="width: ${pct}%;"></div>
      </div>
      <div class="job-meta">
        <span>📁 ${escapeHtml(job.output_dir)}</span>
        <span>${speedHtml}${filesInfo}</span>
      </div>
      ${extractedHtml}
      ${errorHtml}
      <div class="job-actions">${retryBtn}${deleteBtn}</div>
    </div>
  `;
}

// -------------------------------------------------------------
// Dashboard State & Controllers
// -------------------------------------------------------------
const tabs = document.querySelectorAll<HTMLButtonElement>(".nav-tab");
const tabContents: Record<DashboardTab, HTMLElement | null> = {
  jobs: document.getElementById("tab-jobs"),
  add: document.getElementById("tab-add"),
  settings: document.getElementById("tab-settings"),
};

const filterActiveBtn = document.getElementById("filter-active-btn") as HTMLButtonElement | null;
const filterCompletedBtn = document.getElementById("filter-completed-btn") as HTMLButtonElement | null;
const filterFailedBtn = document.getElementById("filter-failed-btn") as HTMLButtonElement | null;
const statActiveCountEl = document.getElementById("stat-active-count");
const statCompletedCountEl = document.getElementById("stat-completed-count");
const statFailedCountEl = document.getElementById("stat-failed-count");
const queueBadgeEl = document.getElementById("queue-badge");
const jobsContainer = document.getElementById("jobs-container");
const autoRefreshCheckbox = document.getElementById("auto-refresh") as HTMLInputElement | null;

let currentJobs: DownloadJob[] = [];
let currentStatusFilter: StatusFilter = null;
let eventSource: EventSource | null = null;
let sseConnected = false;

function activateTab(tabName: DashboardTab): void {
  tabs.forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === tabName);
  });
  (Object.keys(tabContents) as DashboardTab[]).forEach((key) => {
    tabContents[key]?.classList.toggle("active", key === tabName);
  });
  window.location.hash = tabName;
  if (tabName === "jobs") {
    void loadJobs();
    setupLiveUpdates();
  } else if (tabName === "settings") {
    void loadConfig();
  }
}

tabs.forEach((t) => {
  t.addEventListener("click", () => {
    const raw = t.dataset.tab;
    const tab: DashboardTab = raw === "add" || raw === "settings" ? raw : "jobs";
    activateTab(tab);
  });
});

function applyStatusFilterUI(): void {
  filterActiveBtn?.classList.toggle("active", currentStatusFilter === "active");
  filterActiveBtn?.setAttribute("aria-pressed", currentStatusFilter === "active" ? "true" : "false");

  filterCompletedBtn?.classList.toggle("active", currentStatusFilter === "completed");
  filterCompletedBtn?.setAttribute("aria-pressed", currentStatusFilter === "completed" ? "true" : "false");

  filterFailedBtn?.classList.toggle("active", currentStatusFilter === "failed");
  filterFailedBtn?.setAttribute("aria-pressed", currentStatusFilter === "failed" ? "true" : "false");
}

function setStatusFilter(filter: StatusFilter): void {
  currentStatusFilter = filter === null || currentStatusFilter === filter ? null : filter;
  applyStatusFilterUI();
  renderFilteredJobs();
}

filterActiveBtn?.addEventListener("click", () => setStatusFilter("active"));
filterCompletedBtn?.addEventListener("click", () => setStatusFilter("completed"));
filterFailedBtn?.addEventListener("click", () => setStatusFilter("failed"));

function upsertJob(job: DownloadJob): void {
  const idx = currentJobs.findIndex((j) => j.job_id === job.job_id);
  if (idx >= 0) {
    currentJobs[idx] = job;
  } else {
    currentJobs.unshift(job);
  }
}

function updateCounters(): void {
  const activeCount = currentJobs.filter((j) => (ACTIVE_STATUSES as readonly string[]).includes(j.status)).length;
  const completedCount = currentJobs.filter((j) => j.status === "completed").length;
  const failedCount = currentJobs.filter((j) => j.status === "failed").length;

  if (queueBadgeEl) queueBadgeEl.textContent = String(activeCount);
  if (statActiveCountEl) statActiveCountEl.textContent = String(activeCount);
  if (statCompletedCountEl) statCompletedCountEl.textContent = String(completedCount);
  if (statFailedCountEl) statFailedCountEl.textContent = String(failedCount);
}

function renderFilteredJobs(): void {
  updateCounters();
  if (!jobsContainer) return;

  const sorted = sortJobsNewestFirst(currentJobs);
  if (sorted.length === 0) {
    jobsContainer.innerHTML = `
      <div class="empty-state">
        No jobs found. Switch to the <strong>📥 Add</strong> tab to queue a link or <strong>.torrent</strong> file.
      </div>
    `;
    return;
  }

  const activeFilterCfg = currentStatusFilter ? STATUS_FILTERS[currentStatusFilter] : undefined;
  const visible = activeFilterCfg ? sorted.filter(activeFilterCfg.predicate) : sorted;

  if (visible.length === 0 && activeFilterCfg) {
    jobsContainer.innerHTML = `
      <div class="empty-state">
        <div>${activeFilterCfg.emptyText}</div>
        <div class="mt-2 text-xs">
          Click the ${activeFilterCfg.label} pill or
          <button type="button" id="reset-filter-btn" class="text-[var(--info)] underline bg-transparent border-0 p-0 text-xs font-semibold cursor-pointer">show all</button>
        </div>
      </div>
    `;
    document.getElementById("reset-filter-btn")?.addEventListener("click", () => setStatusFilter(null));
    return;
  }

  jobsContainer.innerHTML = visible.map((job) => renderJobCardHtml(job)).join("");
}

async function loadJobs(): Promise<void> {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error("Failed to fetch jobs");
    currentJobs = (await res.json()) as DownloadJob[];
    renderFilteredJobs();
  } catch (err) {
    if (jobsContainer) {
      const msg = err instanceof Error ? err.message : String(err);
      jobsContainer.innerHTML = `<div class="empty-state text-[var(--danger)]">Error loading downloads: ${escapeHtml(msg)}</div>`;
    }
  }
}

function showDownloadAlert(message: string, colorVar: string, autoHideMs = 5000): void {
  const alertEl = document.getElementById("dl-alert");
  if (!alertEl) return;
  alertEl.style.display = "block";
  alertEl.style.color = colorVar;
  alertEl.textContent = message;
  if (autoHideMs > 0) {
    setTimeout(() => {
      alertEl.style.display = "none";
    }, autoHideMs);
  }
}

// Quick download submit
document.getElementById("quick-download-form")?.addEventListener("submit", async (e: SubmitEvent) => {
  e.preventDefault();
  const urlInput = document.getElementById("dl-url") as HTMLInputElement | null;
  const subfolderInput = document.getElementById("dl-subfolder") as HTMLInputElement | null;
  const submitBtn = document.getElementById("btn-submit-dl") as HTMLButtonElement | null;

  const url = (urlInput?.value || "").trim();
  const subfolder = (subfolderInput?.value || "").trim() || undefined;
  if (!url) return;

  if (submitBtn) submitBtn.disabled = true;
  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, subfolder, source: "paste", client_name: "Web Dashboard" }),
    });
    const data = (await res.json()) as DownloadResponse & { detail?: string };
    if (!res.ok) throw new Error(data.detail || "Download request failed");

    showDownloadAlert(`Download queued! (Job ID: ${data.job_id.substring(0, 8)}...)`, "var(--success)");
    if (urlInput) urlInput.value = "";
    activateTab("jobs");
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    showDownloadAlert(`Error: ${msg}`, "var(--danger)");
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
});

// Torrent File Upload / Drag-and-Drop
const dropzone = document.getElementById("torrent-dropzone");
const fileInput = document.getElementById("torrent-file-input") as HTMLInputElement | null;
const browseBtn = document.getElementById("btn-browse-torrent");

function highlightDropzone(e: Event): void {
  e.preventDefault();
  e.stopPropagation();
  dropzone?.classList.add("dragover");
}

function unhighlightDropzone(e: Event): void {
  e.preventDefault();
  e.stopPropagation();
  dropzone?.classList.remove("dragover");
}

async function uploadTorrentFile(file: File | undefined): Promise<void> {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".torrent")) {
    showDownloadAlert("Please select a valid .torrent file.", "var(--danger)", 4000);
    return;
  }

  const subfolderInput = document.getElementById("dl-subfolder") as HTMLInputElement | null;
  const subfolder = subfolderInput ? subfolderInput.value.trim() : "";

  showDownloadAlert(`Uploading torrent: ${file.name}...`, "var(--info)", 0);

  const formData = new FormData();
  formData.append("file", file);
  formData.append("client_name", "Web Dashboard");
  if (subfolder) formData.append("subfolder", subfolder);

  try {
    const res = await fetch("/api/upload/torrent", { method: "POST", body: formData });
    const data = (await res.json()) as DownloadResponse & { detail?: string };
    if (!res.ok) throw new Error(data.detail || "Torrent upload failed");

    showDownloadAlert(
      `Torrent queued! ${data.message || ""} (Job ID: ${data.job_id.substring(0, 8)}...)`,
      "var(--success)"
    );
    activateTab("jobs");
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    showDownloadAlert(`Error: ${msg}`, "var(--danger)");
  }
}

if (dropzone && fileInput) {
  dropzone.addEventListener("click", (e: MouseEvent) => {
    if (e.target !== browseBtn) fileInput.click();
  });
  browseBtn?.addEventListener("click", (e: MouseEvent) => {
    e.stopPropagation();
    fileInput.click();
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, highlightDropzone, false);
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, unhighlightDropzone, false);
  });

  dropzone.addEventListener("drop", (e: DragEvent) => {
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) {
      for (let i = 0; i < files.length; i++) {
        void uploadTorrentFile(files[i]);
      }
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files.length > 0) {
      for (let i = 0; i < fileInput.files.length; i++) {
        void uploadTorrentFile(fileInput.files[i]);
      }
      fileInput.value = "";
    }
  });
}

async function retryJob(jobId: string): Promise<void> {
  try {
    const res = await fetch(`/api/status/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
    if (!res.ok) {
      const data = (await res.json()) as { detail?: string };
      throw new Error(data.detail || "Retry failed");
    }
    await loadJobs();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    alert(`Failed to retry job: ${msg}`);
  }
}

async function deleteJob(jobId: string): Promise<void> {
  if (!confirm("Delete this download job from history?")) return;
  try {
    const res = await fetch(`/api/status/${encodeURIComponent(jobId)}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Delete failed");
    await loadJobs();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    alert(`Failed to delete job: ${msg}`);
  }
}

jobsContainer?.addEventListener("click", (e: MouseEvent) => {
  const rawTarget = e.target;
  if (!(rawTarget instanceof Element)) return;
  const btn = rawTarget.closest<HTMLButtonElement>("button[data-action][data-job-id]");
  if (!btn) return;
  const action = btn.dataset.action;
  const jobId = btn.dataset.jobId;
  if (!jobId) return;
  if (action === "retry") void retryJob(jobId);
  else if (action === "delete") void deleteJob(jobId);
});

document.getElementById("btn-clear-finished")?.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/status", { method: "DELETE" });
    if (!res.ok) throw new Error("Clear failed");
    await loadJobs();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    alert(`Error clearing finished jobs: ${msg}`);
  }
});

document.getElementById("btn-clear-all")?.addEventListener("click", async () => {
  if (!confirm("⚠️ Are you sure you want to clear ALL downloads and wipe job history?")) return;
  try {
    const res = await fetch("/api/status/all?confirm=true", { method: "DELETE" });
    if (!res.ok) throw new Error("Clear all failed");
    await loadJobs();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    alert(`Error clearing all jobs: ${msg}`);
  }
});

const refreshBtn = document.getElementById("btn-refresh") as HTMLButtonElement | null;
const refreshIcon = document.getElementById("refresh-icon");

async function handleManualRefresh(): Promise<void> {
  if (refreshBtn) refreshBtn.disabled = true;
  refreshIcon?.classList.add("spinning");
  jobsContainer?.classList.add("is-refreshing");

  const minDelay = new Promise((resolve) => setTimeout(resolve, 350));
  try {
    await Promise.all([loadJobs(), minDelay]);
    refreshBtn?.classList.add("refresh-success");
    setTimeout(() => {
      refreshBtn?.classList.remove("refresh-success");
    }, 600);
  } finally {
    refreshIcon?.classList.remove("spinning");
    jobsContainer?.classList.remove("is-refreshing");
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

refreshBtn?.addEventListener("click", () => void handleManualRefresh());
autoRefreshCheckbox?.addEventListener("change", setupLiveUpdates);

// Real-Time SSE Stream (native browser auto-reconnect)
function setupLiveUpdates(): void {
  if (!autoRefreshCheckbox || !autoRefreshCheckbox.checked) {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    sseConnected = false;
    return;
  }

  if (typeof EventSource === "undefined") {
    return;
  }

  if (eventSource) {
    eventSource.close();
  }

  try {
    eventSource = new EventSource("/api/status/stream");

    eventSource.addEventListener("init", (e: MessageEvent<string>) => {
      sseConnected = true;
      try {
        currentJobs = JSON.parse(e.data) as DownloadJob[];
        renderFilteredJobs();
      } catch (err) {
        console.error("Failed to parse init stream data:", err);
      }
    });

    eventSource.addEventListener("job_created", (e: MessageEvent<string>) => {
      sseConnected = true;
      try {
        upsertJob(JSON.parse(e.data) as DownloadJob);
        renderFilteredJobs();
      } catch {
        // ignore parse error
      }
    });

    eventSource.addEventListener("job_update", (e: MessageEvent<string>) => {
      sseConnected = true;
      try {
        upsertJob(JSON.parse(e.data) as DownloadJob);
        renderFilteredJobs();
      } catch {
        // ignore parse error
      }
    });

    eventSource.addEventListener("job_deleted", (e: MessageEvent<string>) => {
      sseConnected = true;
      try {
        const data = JSON.parse(e.data) as { job_id: string };
        currentJobs = currentJobs.filter((j) => j.job_id !== data.job_id);
        renderFilteredJobs();
      } catch {
        // ignore parse error
      }
    });

    eventSource.addEventListener("jobs_cleared", (e: MessageEvent<string>) => {
      sseConnected = true;
      try {
        const data = JSON.parse(e.data) as { all?: boolean; status?: string };
        if (data.all) {
          currentJobs = [];
        } else if (data.status === "finished") {
          currentJobs = currentJobs.filter((j) => !["completed", "failed"].includes(j.status));
        } else if (data.status) {
          currentJobs = currentJobs.filter((j) => j.status !== data.status);
        } else {
          currentJobs = currentJobs.filter((j) => !["completed", "failed"].includes(j.status));
        }
      } catch {
        void loadJobs();
      }
      renderFilteredJobs();
    });

    eventSource.onerror = () => {
      sseConnected = false;
    };
  } catch (err) {
    console.error("EventSource connection error:", err);
    sseConnected = false;
  }
}

// -------------------------------------------------------------
// Settings Controller
// -------------------------------------------------------------
const keyCard = document.getElementById("key-card") as HTMLElement | null;
const keyCardMasked = document.getElementById("active-key-masked") as HTMLElement | null;
const keyEditBox = document.getElementById("key-edit-box") as HTMLElement | null;
const apiKeyInput = document.getElementById("api-key") as HTMLInputElement | null;
const btnEditKey = document.getElementById("btn-edit-key") as HTMLButtonElement | null;
const btnCancelEditKey = document.getElementById("btn-cancel-edit-key") as HTMLButtonElement | null;
const btnToggleKeyVisibility = document.getElementById("btn-toggle-key-visibility") as HTMLButtonElement | null;

let isKeyInEditMode = false;
let hasConfiguredKey = false;

function resetKeyVisibility(): void {
  if (apiKeyInput) apiKeyInput.type = "password";
  if (btnToggleKeyVisibility) btnToggleKeyVisibility.textContent = "👁️";
}

function showMsg(text: string, type: "success" | "error"): void {
  const el = document.getElementById("status-msg");
  if (!el) return;
  el.textContent = text;
  el.className = type === "error" ? "status-error" : "status-success";
  el.style.display = "block";
}

btnEditKey?.addEventListener("click", () => {
  isKeyInEditMode = true;
  resetKeyVisibility();
  keyCard?.classList.add("hidden");
  keyEditBox?.classList.remove("hidden");
  if (apiKeyInput) {
    apiKeyInput.value = "";
    apiKeyInput.focus();
  }
});

btnCancelEditKey?.addEventListener("click", () => {
  if (!hasConfiguredKey) return;
  isKeyInEditMode = false;
  resetKeyVisibility();
  if (apiKeyInput) apiKeyInput.value = "";
  keyCard?.classList.remove("hidden");
  keyEditBox?.classList.add("hidden");
});

btnToggleKeyVisibility?.addEventListener("click", () => {
  if (!apiKeyInput) return;
  const isPass = apiKeyInput.type === "password";
  apiKeyInput.type = isPass ? "text" : "password";
  if (btnToggleKeyVisibility) {
    btnToggleKeyVisibility.textContent = isPass ? "🙈" : "👁️";
  }
});

async function loadConfig(): Promise<void> {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) throw new Error("Failed to load settings");
    const data = (await res.json()) as SettingsResponse;

    const providerEl = document.getElementById("provider") as HTMLSelectElement | null;
    const maxConnEl = document.getElementById("max-connections") as HTMLInputElement | null;
    const concEl = document.getElementById("concurrency") as HTMLInputElement | null;
    const autoExtractEl = document.getElementById("auto-extract") as HTMLInputElement | null;
    const cleanupEl = document.getElementById("cleanup-extract") as HTMLInputElement | null;

    if (providerEl) providerEl.value = data.debrid_provider;
    if (maxConnEl) maxConnEl.value = String(data.aria2c_max_connections);
    if (concEl) concEl.value = String(data.max_concurrent_downloads);
    if (autoExtractEl) autoExtractEl.checked = data.auto_extract;
    if (cleanupEl) cleanupEl.checked = data.cleanup_after_extract;

    const hasKey = Boolean(data.rd_api_key_masked && data.rd_api_key_masked.trim());
    hasConfiguredKey = hasKey;

    if (hasKey) {
      isKeyInEditMode = false;
      resetKeyVisibility();
      if (keyCardMasked) keyCardMasked.textContent = data.rd_api_key_masked;
      keyCard?.classList.remove("hidden");
      keyEditBox?.classList.add("hidden");
      btnCancelEditKey?.classList.remove("hidden");
      if (apiKeyInput) apiKeyInput.value = "";
    } else {
      isKeyInEditMode = true;
      resetKeyVisibility();
      keyCard?.classList.add("hidden");
      keyEditBox?.classList.remove("hidden");
      btnCancelEditKey?.classList.add("hidden");
    }
  } catch (err) {
    showMsg(err instanceof Error ? err.message : String(err), "error");
  }
}

document.getElementById("btn-test")?.addEventListener("click", async () => {
  showMsg("Testing provider connection...", "success");
  const provider = (document.getElementById("provider") as HTMLSelectElement | null)?.value;
  const key = isKeyInEditMode ? (apiKeyInput?.value || "").trim() : undefined;
  const body: TestDebridRequest = { provider, apiKey: key || undefined };

  try {
    const res = await fetch("/api/settings/test-debrid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = (await res.json()) as TestDebridResponse;
    if (data.ok) {
      showMsg(
        `Connected to ${data.provider}! User: ${data.username || "unknown"} (${data.account_type || "unknown"}, expires ${data.expiration || "N/A"})`,
        "success"
      );
    } else {
      showMsg(`Connection test failed: ${data.message || "Unknown error"}`, "error");
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    showMsg(`Test error: ${msg}`, "error");
  }
});

document.getElementById("settings-form")?.addEventListener("submit", async (e: SubmitEvent) => {
  e.preventDefault();
  const providerEl = document.getElementById("provider") as HTMLSelectElement | null;
  const maxConnEl = document.getElementById("max-connections") as HTMLInputElement | null;
  const concEl = document.getElementById("concurrency") as HTMLInputElement | null;
  const autoExtractEl = document.getElementById("auto-extract") as HTMLInputElement | null;
  const cleanupEl = document.getElementById("cleanup-extract") as HTMLInputElement | null;

  const payload: SettingsUpdateRequest = {
    debrid_provider: providerEl?.value,
    aria2c_max_connections: parseInt(maxConnEl?.value || "16", 10),
    max_concurrent_downloads: parseInt(concEl?.value || "3", 10),
    auto_extract: autoExtractEl?.checked,
    cleanup_after_extract: cleanupEl?.checked,
  };
  const key = (apiKeyInput?.value || "").trim();
  if (isKeyInEditMode && key) {
    payload.rd_api_key = key;
  }

  try {
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Save failed: ${res.statusText}`);
    showMsg("Settings updated and persisted successfully!", "success");
    await loadConfig();
  } catch (err) {
    showMsg(err instanceof Error ? err.message : String(err), "error");
  }
});

// Boot
if (window.location.hash === "#add") {
  activateTab("add");
} else if (
  window.location.hash === "#settings" ||
  (window.location.pathname === "/settings" && !["#jobs", "#downloads"].includes(window.location.hash))
) {
  activateTab("settings");
} else {
  activateTab("jobs");
}
setupLiveUpdates();
