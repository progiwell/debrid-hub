/** Background Service Worker (Manifest V3) for Debrid Hub. */

import type {
  ClientIdentity,
  DownloadJob,
  DownloadRequest,
  DownloadResponse,
  PopupTab,
  QueueErrorItem,
  QueueUrlsResponse,
  RuntimeMessage,
  ServerProfile,
} from "./types";
import {
  extractUrlsFromText,
  infoHashToMagnet,
  isDownloadablePageUrl,
  isInfoHash,
} from "./url_parser";

const CONTEXT_MENU_ID = "download-to-server";
const NOTIFICATION_ICON =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
const ALARM_NAME = "pollServerStatus";
const ACTIVE_STATUSES: readonly string[] = ["queued", "resolving_debrid", "downloading", "extracting"];
const VALID_TABS: readonly PopupTab[] = ["jobs", "add", "settings"];

let sessionActiveTab: PopupTab = "jobs";

interface BackgroundSettings {
  serverEndpoint: string;
  nasEndpoint: string;
  apiKey: string;
  enableNotifications: boolean;
  endpoints: ServerProfile[];
  activeEndpointId: string;
  clientId: string;
  clientName: string;
  queueScope: "browser" | "all";
}

function isPopupTab(tab: unknown): tab is PopupTab {
  return typeof tab === "string" && (VALID_TABS as readonly string[]).includes(tab);
}

/** Ensure the periodic background alarm exists. */
function ensureAlarm(): void {
  if (typeof chrome !== "undefined" && chrome.alarms) {
    chrome.alarms.clear("pollNasStatus", () => {
      void chrome.runtime.lastError;
    });
    chrome.alarms.get(ALARM_NAME, (alarm) => {
      if (!alarm) {
        chrome.alarms.create(ALARM_NAME, { periodInMinutes: 0.5 });
      }
    });
  }
}

function detectDefaultClientName(isBrave = false): string {
  const ua = typeof navigator !== "undefined" ? navigator.userAgent || "" : "";
  let browser = "Browser";
  if (ua.includes("Edg/")) browser = "Edge";
  else if (ua.includes("Firefox/")) browser = "Firefox";
  else if (isBrave || ua.includes("Brave")) browser = "Brave";
  else if (ua.includes("Chrome/")) browser = "Chrome";
  else if (ua.includes("Safari/")) browser = "Safari";

  let os = "";
  if (ua.includes("Macintosh") || ua.includes("Mac OS")) os = "macOS";
  else if (ua.includes("Windows")) os = "Windows";
  else if (ua.includes("Linux")) os = "Linux";

  return os ? `${browser} • ${os}` : browser;
}

function generateClientId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "client-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

async function ensureClientIdentity(isBrave = false): Promise<ClientIdentity> {
  return new Promise((resolve) => {
    chrome.storage.local.get(["clientId", "clientName"], (items: Record<string, unknown>) => {
      const updates: Partial<ClientIdentity> = {};
      const existingId = typeof items.clientId === "string" ? items.clientId.trim() : "";
      const existingName = typeof items.clientName === "string" ? items.clientName.trim() : "";
      const clientId = existingId || generateClientId();
      const clientName = existingName || detectDefaultClientName(isBrave);

      if (!existingId) updates.clientId = clientId;
      if (!existingName) updates.clientName = clientName;

      if (Object.keys(updates).length > 0) {
        chrome.storage.local.set(updates, () => resolve({ clientId, clientName }));
      } else {
        resolve({ clientId, clientName });
      }
    });
  });
}

function syncActionPopupTab(tab: PopupTab): void {
  if (typeof chrome !== "undefined" && chrome.action && typeof chrome.action.setPopup === "function") {
    const validTab: PopupTab = isPopupTab(tab) ? tab : "jobs";
    chrome.action.setPopup({ popup: `popup.html?tab=${validTab}` }, () => {
      void chrome.runtime.lastError;
    });
  }
}

function resetSessionTab(): void {
  sessionActiveTab = "jobs";
  syncActionPopupTab("jobs");
  if (typeof chrome !== "undefined" && chrome.storage?.session) {
    chrome.storage.session.remove(["activeTab"], () => {
      void chrome.runtime.lastError;
    });
  }
}

// Setup context menu and alarm on extension installation / startup
chrome.runtime.onInstalled.addListener(() => {
  resetSessionTab();
  chrome.contextMenus.removeAll(() => {
    void chrome.runtime.lastError;
    chrome.contextMenus.create({
      id: CONTEXT_MENU_ID,
      title: "Download with Debrid Hub",
      contexts: ["link", "selection", "page"],
    });
  });
  void ensureClientIdentity();
  ensureAlarm();
  void updateBadgeAndCheckCompletions();
});

if (chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => {
    resetSessionTab();
    void ensureClientIdentity();
    ensureAlarm();
    void updateBadgeAndCheckCompletions();
  });
}

if (chrome.alarms?.onAlarm) {
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM_NAME) {
      void updateBadgeAndCheckCompletions();
    }
  });
}

/** Retrieve saved settings from chrome.storage.local. */
async function getSettings(): Promise<BackgroundSettings> {
  const identity = await ensureClientIdentity();
  return new Promise((resolve) => {
    chrome.storage.local.get(
      ["endpoints", "activeEndpointId", "serverEndpoint", "nasEndpoint", "apiKey", "enableNotifications", "queueScope"],
      (items: Record<string, unknown>) => {
        const endpoints = Array.isArray(items.endpoints) ? (items.endpoints as ServerProfile[]) : [];
        let activeId = typeof items.activeEndpointId === "string" ? items.activeEndpointId : "";
        let activeUrl = String(items.serverEndpoint || items.nasEndpoint || "")
          .trim()
          .replace(/\/+$/, "");
        let activeApiKey = String(items.apiKey || "").trim();

        if (endpoints.length > 0) {
          const matched = endpoints.find((ep) => ep.id === activeId) || endpoints[0];
          activeId = matched.id || "";
          activeUrl = (matched.url || "").trim().replace(/\/+$/, "");
          activeApiKey = (matched.apiKey || "").trim();
        }

        resolve({
          serverEndpoint: activeUrl,
          nasEndpoint: activeUrl,
          apiKey: activeApiKey,
          enableNotifications: items.enableNotifications !== false,
          endpoints,
          activeEndpointId: activeId,
          clientId: identity.clientId,
          clientName: identity.clientName,
          queueScope: items.queueScope === "all" ? "all" : "browser",
        });
      }
    );
  });
}

/** Retrieve list of previously notified job IDs to avoid duplicate alerts. */
async function getNotifiedJobIds(): Promise<string[]> {
  return new Promise((resolve) => {
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      chrome.storage.local.get(["notifiedJobIds"], (items: Record<string, unknown>) => {
        resolve(Array.isArray(items.notifiedJobIds) ? (items.notifiedJobIds as string[]) : []);
      });
    } else {
      resolve([]);
    }
  });
}

/** Save list of notified job IDs, capping to the latest 500 entries. */
async function saveNotifiedJobIds(ids: string[]): Promise<void> {
  return new Promise((resolve) => {
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      const trimmed = ids.slice(-500);
      chrome.storage.local.set({ notifiedJobIds: trimmed }, () => resolve());
    } else {
      resolve();
    }
  });
}

/** Display a desktop notification. */
function notify(title: string, message: string): void {
  if (typeof chrome !== "undefined" && chrome.notifications) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: NOTIFICATION_ICON,
      title: title || "Debrid Hub",
      message: message || "",
    });
  }
}

/** Fetch server job list to update icon badge count and send completion notifications. */
async function updateBadgeAndCheckCompletions(): Promise<void> {
  try {
    const settings = await getSettings();
    if (!settings.serverEndpoint) {
      if (typeof chrome !== "undefined" && chrome.action) {
        void chrome.action.setBadgeText({ text: "" });
      }
      return;
    }

    const headers: Record<string, string> = { Accept: "application/json" };
    if (settings.apiKey) {
      headers["X-API-Key"] = settings.apiKey;
    }

    const signal = typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(5000) : undefined;
    const response = await fetch(`${settings.serverEndpoint}/api/status`, {
      method: "GET",
      headers,
      signal,
    });

    if (!response.ok) {
      return;
    }

    const jobs = (await response.json()) as unknown;
    if (!Array.isArray(jobs)) return;

    const typedJobs = jobs as DownloadJob[];
    const scopedJobs =
      settings.queueScope === "all"
        ? typedJobs
        : typedJobs.filter((j) => j.client_id && j.client_id === settings.clientId);

    // 1. Calculate active job count & update badge
    const activeCount = scopedJobs.filter((j) => ACTIVE_STATUSES.includes(j.status)).length;
    if (typeof chrome !== "undefined" && chrome.action) {
      void chrome.action.setBadgeText({ text: activeCount > 0 ? String(activeCount) : "" });
      void chrome.action.setBadgeBackgroundColor({ color: "#2563eb" });
    }

    // 2. Check for newly completed jobs in active scope and trigger desktop notifications if enabled
    const notifiedJobIds = await getNotifiedJobIds();
    const notifiedSet = new Set<string>(notifiedJobIds);
    let hasNewCompletions = false;

    for (const job of scopedJobs) {
      if (job.status === "completed" && !notifiedSet.has(job.job_id)) {
        notifiedSet.add(job.job_id);
        hasNewCompletions = true;

        if (settings.enableNotifications !== false) {
          const name =
            job.filenames && job.filenames.length > 0
              ? job.filenames[0]
              : job.extracted_files && job.extracted_files.length > 0
                ? job.extracted_files[0]
                : "Download";
          notify("🎉 Download Complete", `${name} has been saved to your server.`);
        }
      }
    }

    if (hasNewCompletions) {
      await saveNotifiedJobIds(Array.from(notifiedSet));
    }
  } catch (err) {
    console.warn("[Debrid Hub] Error checking completions / updating badge:", err);
  }
}

/** Send a single download job to the server daemon. */
async function queueSingleUrl(
  url: string,
  subfolderOverride?: string,
  sourceOverride?: string
): Promise<DownloadResponse> {
  const settings = await getSettings();
  if (!settings.serverEndpoint) {
    throw new Error("Server Endpoint not configured. Please open extension settings.");
  }

  const payload: DownloadRequest = {
    url,
    source: sourceOverride || "context_menu",
    client_id: settings.clientId,
    client_name: settings.clientName,
  };
  if (settings.apiKey) {
    payload.apiKey = settings.apiKey;
  }
  if (subfolderOverride) {
    payload.subfolder = subfolderOverride;
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (settings.apiKey) {
    headers["X-API-Key"] = settings.apiKey;
  }

  const response = await fetch(`${settings.serverEndpoint}/api/download`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const errJson = (await response.json()) as { detail?: string } | null;
      if (errJson?.detail) {
        detail = errJson.detail;
      }
    } catch {
      // ignore invalid error body
    }
    throw new Error(`HTTP ${response.status}: ${detail}`);
  }

  return (await response.json()) as DownloadResponse;
}

/** Queue a list of URLs to the server, collecting results and reporting notifications. */
async function queueUrls(
  urls: string[],
  subfolderOverride?: string,
  sourceOverride?: string
): Promise<QueueUrlsResponse> {
  if (!Array.isArray(urls) || urls.length === 0) {
    return { ok: false, queued: 0, errors: [{ error: "No URLs provided" }] };
  }

  let queued = 0;
  const errors: QueueErrorItem[] = [];

  for (const url of urls) {
    try {
      await queueSingleUrl(url, subfolderOverride, sourceOverride);
      queued++;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      errors.push({ url, error: message });
    }
  }

  if (queued > 0 && errors.length === 0) {
    notify(
      "Download Queued",
      queued === 1 ? "Successfully queued download to server." : `Successfully queued ${queued} downloads to server.`
    );
  } else if (queued > 0 && errors.length > 0) {
    notify(
      "Partially Queued",
      `Queued ${queued} download(s), but ${errors.length} failed. (${errors[0].error})`
    );
  } else {
    notify("Download Failed", errors[0]?.error || "Could not queue download to server.");
  }

  // Instantly refresh badge count after queueing
  void updateBadgeAndCheckCompletions();

  return { ok: errors.length === 0, queued, errors };
}

// Context Menu Click Dispatcher
chrome.contextMenus.onClicked.addListener(async (info) => {
  if (info.menuItemId !== CONTEXT_MENU_ID) return;

  let urlsToQueue: string[] = [];

  if (info.linkUrl) {
    const rawLink = info.linkUrl.trim();
    urlsToQueue = isInfoHash(rawLink) ? [infoHashToMagnet(rawLink)] : [rawLink];
  } else if (info.selectionText) {
    const trimmedSelection = info.selectionText.trim();
    urlsToQueue = isInfoHash(trimmedSelection)
      ? [infoHashToMagnet(trimmedSelection)]
      : extractUrlsFromText(info.selectionText);
  } else if (info.pageUrl) {
    const pageUrl = info.pageUrl.trim();
    if (isDownloadablePageUrl(pageUrl)) {
      urlsToQueue = [pageUrl];
    } else {
      notify(
        "Not a Downloadable File Page",
        "The current page does not appear to be a supported hoster file or media link."
      );
      return;
    }
  }

  if (urlsToQueue.length === 0) {
    notify("No Links Found", "Could not find any valid Magnet, Torrent, or Hoster links in selection.");
    return;
  }

  await queueUrls(urlsToQueue, undefined, "context_menu");
});

// Internal Runtime Message Dispatcher (used by content.ts Click'n'Load and popup.ts)
chrome.runtime.onMessage.addListener((rawMessage: unknown, _sender, sendResponse) => {
  const message = rawMessage as RuntimeMessage | undefined;
  if (!message || typeof message.type !== "string") return false;

  if (message.type === "QUEUE_URLS") {
    void queueUrls(message.urls, message.subfolder, message.source || "context_menu").then((result) => {
      sendResponse(result);
    });
    return true;
  }

  if (message.type === "UPDATE_BADGE") {
    void updateBadgeAndCheckCompletions().then(() => {
      sendResponse({ ok: true });
    });
    return true;
  }

  if (message.type === "GET_SESSION_TAB") {
    sendResponse({ tab: sessionActiveTab });
    return false;
  }

  if (message.type === "SET_SESSION_TAB") {
    if (isPopupTab(message.tab)) {
      sessionActiveTab = message.tab;
      syncActionPopupTab(sessionActiveTab);
    }
    sendResponse({ ok: true, tab: sessionActiveTab });
    return false;
  }

  if (message.type === "ENSURE_CLIENT_IDENTITY") {
    void ensureClientIdentity(Boolean(message.isBrave)).then((identity) => {
      sendResponse(identity);
    });
    return true;
  }

  return false;
});

// Initial boot
ensureAlarm();
void updateBadgeAndCheckCompletions();
