/** Queue filtering, summary counters, and Tailwind job card rendering helpers. */

import type { DownloadJob, QueueScope, StatusFilter } from "./types";

export const ACTIVE_STATUSES = ["queued", "resolving_debrid", "downloading", "extracting"] as const;
export const MAX_RECENT_COMPLETED = 5;

export function escapeHtml(str: string | null | undefined): string {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function sortJobsNewestFirst(jobs: DownloadJob[]): DownloadJob[] {
  return jobs.slice().sort((a, b) => {
    const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
    const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
    return tb - ta;
  });
}

export function formatOriginBadge(source: string | null | undefined): string {
  const src = (source || "").toLowerCase();
  if (!src) return "";
  if (src.includes("sonarr")) return "📺 Sonarr";
  if (src.includes("radarr")) return "🎬 Radarr";
  if (src.includes("lidarr")) return "🎵 Lidarr";
  if (src.includes("readarr")) return "📚 Readarr";
  if (src === "context_menu" || src === "contextmenu" || src === "extension") return "🖱️ Context Menu";
  if (src === "click_n_load" || src === "cnl") return "⚡ Click'n'Load";
  if (src === "paste" || src === "web" || src === "manual") return "📋 Paste";
  if (src === "upload") return "📁 Upload";
  return `🤖 ${src}`;
}

export function formatTypeBadge(linkType: string | null | undefined): string {
  const lt = (linkType || "").toLowerCase();
  if (lt === "magnet") return "🧲 Magnet";
  if (lt === "torrent") return "📦 Torrent";
  if (lt === "hoster") return "🔗 Hoster";
  return lt;
}

export function resolveDisplayFilename(job: DownloadJob): string {
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
  return job.source_url || job.job_id;
}

export const STATUS_FILTERS: Record<
  Exclude<StatusFilter, null>,
  { predicate: (job: DownloadJob) => boolean; emptyText: string; pillLabel: string }
> = {
  active: {
    predicate: (j) => (ACTIVE_STATUSES as readonly string[]).includes(j.status),
    emptyText: "⚡ No active jobs running.",
    pillLabel: "active",
  },
  completed: {
    predicate: (j) => j.status === "completed",
    emptyText: "✅ No completed jobs found.",
    pillLabel: "completed",
  },
  failed: {
    predicate: (j) => j.status === "failed",
    emptyText: "⚠️ No failed jobs found.",
    pillLabel: "failed",
  },
};

export function renderJobCardHtml(job: DownloadJob, currentQueueScope: QueueScope): string {
  const status = job.status || "queued";
  const statusClass = `badge-${status}`;
  const statusLabel = status.replace("_", " ");
  const displayName = resolveDisplayFilename(job);
  const errorHtml = job.error ? `<div class="job-error">⚠️ ${escapeHtml(job.error)}</div>` : "";

  const rawPct = Number(job.progress_percent);
  const pct = Number.isFinite(rawPct) ? Math.min(Math.max(rawPct, 0), 100) : 0;
  const completedFiles = Number(job.completed_files) || 0;
  const totalFiles = Number(job.total_files) || 1;
  const showBar = ["downloading", "extracting", "completed"].includes(status);
  const speedHtml =
    status === "downloading" && job.download_speed ? ` • ⚡ ${escapeHtml(job.download_speed)}` : "";

  const progressBarHtml = showBar
    ? `
      <div class="progress-container">
        <div class="progress-bar" style="width: ${pct}%;"></div>
      </div>
      <div class="progress-info">
        <span>${completedFiles} / ${totalFiles} file(s)${speedHtml}</span>
        <span>${pct}%</span>
      </div>
    `
    : "";

  const originBadge = formatOriginBadge(job.source);
  const typeBadge = formatTypeBadge(job.link_type);
  const deviceBadge = currentQueueScope === "all" && job.client_name ? `💻 ${job.client_name}` : "";
  const tagsDisplay = [originBadge, deviceBadge, typeBadge].filter(Boolean).join(" • ");

  return `
    <div class="job-card">
      <div class="job-header">
        <span class="job-badge ${escapeHtml(statusClass)}">${escapeHtml(statusLabel)}</span>
        <span class="job-subtext">${escapeHtml(tagsDisplay)}</span>
      </div>
      <div class="job-filename">${escapeHtml(displayName)}</div>
      ${progressBarHtml}
      ${errorHtml}
    </div>
  `;
}
