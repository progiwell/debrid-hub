/** Popup Controller: 3-Tab Navigation (Jobs, Add, Settings), Quick-Submit, and Live Queue Monitor. */

import {
  checkMissingFirefoxPermission,
  promptFirefoxPermissionAction,
  requestHostPermission,
} from "./permissions";
import {
  ACTIVE_STATUSES,
  MAX_RECENT_COMPLETED,
  STATUS_FILTERS,
  escapeHtml,
  renderJobCardHtml,
  sortJobsNewestFirst,
} from "./queue_ui";
import {
  deriveServerName,
  getStoredSettings,
  isValidHttpUrl,
  normalizeEndpoint,
  notifyBadgeUpdate,
  persistLocalSettings,
} from "./storage";
import type {
  DownloadJob,
  PopupTab,
  QueueScope,
  QueueUrlsResponse,
  ServerProfile,
  StatusFilter,
  StatusMessageType,
} from "./types";
import { extractUrlsFromText, isDownloadablePageUrl } from "./url_parser";

const VALID_TABS: readonly PopupTab[] = ["jobs", "add", "settings"];

function isPopupTab(tab: unknown): tab is PopupTab {
  return typeof tab === "string" && (VALID_TABS as readonly string[]).includes(tab);
}

document.addEventListener("DOMContentLoaded", () => {
  // Navigation & Header elements
  const tabJobsBtn = document.getElementById("tabJobsBtn") as HTMLButtonElement | null;
  const tabAddBtn = document.getElementById("tabAddBtn") as HTMLButtonElement | null;
  const tabSettingsBtn = document.getElementById("tabSettingsBtn") as HTMLButtonElement | null;
  const jobsPane = document.getElementById("jobsPane") as HTMLElement | null;
  const addPane = document.getElementById("addPane") as HTMLElement | null;
  const settingsPane = document.getElementById("settingsPane") as HTMLElement | null;
  const headerEndpointSelect = document.getElementById("headerEndpointSelect") as HTMLSelectElement | null;
  const endpointStatus = document.getElementById("endpointStatus") as HTMLElement | null;

  // Settings & Profile elements
  const profilesSection = document.getElementById("profilesSection") as HTMLElement | null;
  const profilesList = document.getElementById("profilesList") as HTMLElement | null;
  const addProfileBtn = document.getElementById("addProfileBtn") as HTMLButtonElement | null;
  const activeServerCard = document.getElementById("activeServerCard") as HTMLElement | null;
  const cardServerName = document.getElementById("cardServerName") as HTMLElement | null;
  const cardServerUrl = document.getElementById("cardServerUrl") as HTMLElement | null;
  const cardServerStatus = document.getElementById("cardServerStatus") as HTMLElement | null;
  const editServerBtn = document.getElementById("editServerBtn") as HTMLButtonElement | null;
  const serverEditForm = document.getElementById("serverEditForm") as HTMLElement | null;
  const profileNameGroup = document.getElementById("profileNameGroup") as HTMLElement | null;
  const profileNameInput = document.getElementById("profileName") as HTMLInputElement | null;
  const serverEndpointInput = (
    document.getElementById("serverEndpoint") || document.getElementById("nasEndpoint")
  ) as HTMLInputElement | null;
  const apiKeyInput = document.getElementById("apiKey") as HTMLInputElement | null;
  const cancelEditBtn = document.getElementById("cancelEditBtn") as HTMLButtonElement | null;
  const addServerSingleWrap = document.getElementById("addServerSingleWrap") as HTMLElement | null;
  const addServerSingleBtn = document.getElementById("addServerSingleBtn") as HTMLButtonElement | null;
  const clientNameInput = document.getElementById("clientName") as HTMLInputElement | null;
  const enableNotificationsInput = document.getElementById("enableNotifications") as HTMLInputElement | null;
  const saveBtn = document.getElementById("saveBtn") as HTMLButtonElement | null;
  const testBtn = document.getElementById("testBtn") as HTMLButtonElement | null;
  const statusBox = document.getElementById("statusBox") as HTMLElement | null;

  // Multi-server & identity state
  let storedEndpoints: ServerProfile[] = [];
  let activeEndpointId = "";
  let editingProfileId = "";
  let settingsMode: "view" | "edit" | "add" = "view";
  let currentClientId = "";
  let currentClientName = "";
  let currentQueueScope: QueueScope = "browser";

  // Queue & Quick-Submit elements
  const scopeBrowserBtn = document.getElementById("scopeBrowserBtn") as HTMLButtonElement | null;
  const scopeAllBtn = document.getElementById("scopeAllBtn") as HTMLButtonElement | null;
  const pasteUrlsInput = document.getElementById("pasteUrls") as HTMLTextAreaElement | null;
  const quickSubfolderInput = document.getElementById("quickSubfolder") as HTMLInputElement | null;
  const submitUrlsBtn = document.getElementById("submitUrlsBtn") as HTMLButtonElement | null;
  const refreshQueueBtn = document.getElementById("refreshQueueBtn") as HTMLButtonElement | null;
  const queueList = document.getElementById("queueList") as HTMLElement | null;
  const countActiveEl = document.getElementById("countActive") as HTMLElement | null;
  const countCompletedEl = document.getElementById("countCompleted") as HTMLElement | null;
  const countFailedEl = document.getElementById("countFailed") as HTMLElement | null;
  const filterActiveBtn = document.getElementById("filterActiveBtn") as HTMLButtonElement | null;
  const filterCompletedBtn = document.getElementById("filterCompletedBtn") as HTMLButtonElement | null;
  const filterFailedBtn = document.getElementById("filterFailedBtn") as HTMLButtonElement | null;

  // Status Filter State: null | "active" | "completed" | "failed"
  let currentStatusFilter: StatusFilter = null;

  // Active page 1-click download elements
  const activePageBanner = document.getElementById("activePageBanner") as HTMLElement | null;
  const activePageUrlText = document.getElementById("activePageUrlText") as HTMLElement | null;
  const downloadActivePageBtn = document.getElementById("downloadActivePageBtn") as HTMLButtonElement | null;
  let currentActiveTabUrl = "";
  const queueCapNote = document.getElementById("queueCapNote") as HTMLElement | null;
  const capNoteDashboardLink = document.getElementById("capNoteDashboardLink") as HTMLElement | null;
  const openDashboardJobsBtn = document.getElementById("openDashboardJobsBtn") as HTMLButtonElement | null;
  const openServerSettingsBtn = (
    document.getElementById("openServerSettingsBtn") || document.getElementById("openNasSettingsBtn")
  ) as HTMLButtonElement | null;

  // Live Queue State & EventSource Stream
  let queueJobs: DownloadJob[] = [];
  let queueEventSource: EventSource | null = null;

  // Fail-safe reveal guard: never allow the popup to remain hidden if async init stalls
  setTimeout(() => {
    document.body?.classList.remove("tab-pending");
  }, 100);

  function setPopupActionTab(tab: PopupTab): void {
    if (typeof chrome !== "undefined" && chrome.action && typeof chrome.action.setPopup === "function") {
      const validTab: PopupTab = isPopupTab(tab) ? tab : "jobs";
      chrome.action.setPopup({ popup: `popup.html?tab=${validTab}` }, () => {
        void chrome.runtime.lastError;
      });
    }
  }

  async function getSessionTab(): Promise<PopupTab> {
    if (typeof chrome !== "undefined" && chrome.storage?.session) {
      return new Promise((resolve) => {
        chrome.storage.session.get(["activeTab"], (items: Record<string, unknown>) => {
          const tab = items?.activeTab;
          resolve(isPopupTab(tab) ? tab : "jobs");
        });
      });
    }
    if (typeof chrome !== "undefined" && chrome.runtime?.sendMessage) {
      return new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: "GET_SESSION_TAB" }, (res?: { tab?: unknown }) => {
          void chrome.runtime.lastError;
          resolve(isPopupTab(res?.tab) ? res.tab : "jobs");
        });
      });
    }
    return "jobs";
  }

  function setSessionTab(tab: PopupTab): void {
    if (!isPopupTab(tab)) return;
    setPopupActionTab(tab);
    if (typeof chrome !== "undefined" && chrome.storage?.session) {
      void chrome.storage.session.set({ activeTab: tab });
    } else if (typeof chrome !== "undefined" && chrome.runtime?.sendMessage) {
      chrome.runtime.sendMessage({ type: "SET_SESSION_TAB", tab }, () => {
        void chrome.runtime.lastError;
      });
    }
  }

  function applyTabDom(activeTab: string): PopupTab {
    const target: PopupTab = isPopupTab(activeTab) ? activeTab : "jobs";

    tabJobsBtn?.classList.toggle("active", target === "jobs");
    tabAddBtn?.classList.toggle("active", target === "add");
    tabSettingsBtn?.classList.toggle("active", target === "settings");

    jobsPane?.classList.toggle("active", target === "jobs");
    addPane?.classList.toggle("active", target === "add");
    settingsPane?.classList.toggle("active", target === "settings");

    document.body?.classList.remove("tab-pending");
    return target;
  }

  function switchTab(activeTab: PopupTab, options: { saveSession?: boolean } = {}): void {
    const { saveSession = true } = options;
    const target = applyTabDom(activeTab);

    if (saveSession) {
      setSessionTab(target);
    }

    if (target === "jobs") {
      void fetchQueue();
    } else if (queueEventSource) {
      queueEventSource.close();
      queueEventSource = null;
    }
  }

  // Synchronous pre-paint tab selection to eliminate initial frame flash
  const urlTab = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("tab") : null;
  let hasCachedEndpoint: string | null = null;
  try {
    hasCachedEndpoint = typeof localStorage !== "undefined" ? localStorage.getItem("hasConfiguredEndpoint") : null;
  } catch {
    hasCachedEndpoint = null;
  }

  if (hasCachedEndpoint === "0") {
    applyTabDom("settings");
  } else if (isPopupTab(urlTab)) {
    applyTabDom(urlTab);
  }

  tabJobsBtn?.addEventListener("click", () => switchTab("jobs"));
  tabAddBtn?.addEventListener("click", () => switchTab("add"));
  tabSettingsBtn?.addEventListener("click", () => switchTab("settings"));

  function showStatus(message: string, type: StatusMessageType = "info"): void {
    showStatusWithAction(message, type, null);
  }

  function showStatusWithAction(
    message: string,
    type: StatusMessageType = "info",
    buttonConfig: { text: string; className: string; onClick: (e: MouseEvent) => void } | null = null
  ): void {
    if (!statusBox) return;
    statusBox.textContent = "";
    statusBox.className = `status-box ${type}`;

    const textSpan = document.createElement("span");
    textSpan.textContent = message;
    statusBox.appendChild(textSpan);

    if (buttonConfig && buttonConfig.text && typeof buttonConfig.onClick === "function") {
      const btnWrapper = document.createElement("div");
      btnWrapper.className = "mt-2";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = buttonConfig.className || "btn-primary";
      btn.style.width = "100%";
      btn.style.padding = "6px 10px";
      btn.style.fontSize = "11px";
      btn.style.fontWeight = "600";
      btn.textContent = buttonConfig.text;
      btn.addEventListener("click", buttonConfig.onClick);
      btnWrapper.appendChild(btn);
      statusBox.appendChild(btnWrapper);
    }
  }

  function clearStatus(): void {
    if (!statusBox) return;
    statusBox.textContent = "";
    statusBox.className = "status-box";
  }

  function syncProfileFormInputs(profile: ServerProfile | null | undefined): void {
    if (profileNameInput) profileNameInput.value = profile ? profile.name : "";
    if (serverEndpointInput) serverEndpointInput.value = profile ? profile.url : "";
    if (apiKeyInput) apiKeyInput.value = profile ? profile.apiKey : "";
  }

  function renderHeaderEndpointSwitcher(): void {
    if (!headerEndpointSelect) return;
    if (storedEndpoints.length < 2) {
      headerEndpointSelect.style.display = "none";
      headerEndpointSelect.innerHTML = "";
      return;
    }

    headerEndpointSelect.innerHTML = storedEndpoints
      .map((ep) => `<option value="${escapeHtml(ep.id)}">${escapeHtml(ep.name)}</option>`)
      .join("");
    headerEndpointSelect.value = activeEndpointId;
    headerEndpointSelect.style.display = "inline-block";
  }

  function renderProfilesList(): void {
    if (!profilesList) return;
    if (storedEndpoints.length === 0) {
      profilesList.innerHTML = `<div class="hint mb-1">No servers configured yet. Add one below.</div>`;
      return;
    }

    const canDelete = storedEndpoints.length > 1;
    profilesList.innerHTML = storedEndpoints
      .map((ep) => {
        const isActive = ep.id === activeEndpointId;
        const isEditing = ep.id === editingProfileId;
        const deleteBtn = canDelete
          ? `<button type="button" class="btn-delete-profile" data-delete-id="${escapeHtml(ep.id)}" title="Delete profile">🗑️</button>`
          : "";
        return `
          <div class="profile-item ${isEditing ? "editing" : ""}" data-profile-id="${escapeHtml(ep.id)}">
            <div class="profile-info">
              <div class="profile-name-row">
                <span class="profile-name-text">${escapeHtml(ep.name)}</span>
                ${isActive ? `<span class="profile-active-tag">Active</span>` : ""}
              </div>
              <div class="profile-url">${escapeHtml(ep.url)}</div>
            </div>
            ${deleteBtn}
          </div>
        `;
      })
      .join("");

    profilesList.querySelectorAll<HTMLElement>(".profile-item").forEach((row) => {
      row.addEventListener("click", () => {
        const profileId = row.getAttribute("data-profile-id");
        const matched = storedEndpoints.find((ep) => ep.id === profileId);
        if (!matched) return;
        activeEndpointId = matched.id;
        editingProfileId = matched.id;
        syncProfileFormInputs(matched);
        clearStatus();
        settingsMode = "view";
        renderSettingsView();
        void persistLocalSettings({
          activeEndpointId: matched.id,
          serverEndpoint: matched.url,
          apiKey: matched.apiKey,
        });
        if (endpointStatus) endpointStatus.textContent = "Checking...";
        void checkEndpointHealth(matched.url, matched.apiKey);
        queueJobs = [];
        if (jobsPane?.classList.contains("active")) {
          void fetchQueue();
        } else if (queueEventSource) {
          queueEventSource.close();
          queueEventSource = null;
        }
        notifyBadgeUpdate();
      });
    });

    profilesList.querySelectorAll<HTMLElement>("[data-delete-id]").forEach((btn) => {
      btn.addEventListener("click", async (e: MouseEvent) => {
        e.stopPropagation();
        if (storedEndpoints.length <= 1) return;
        const deleteId = btn.getAttribute("data-delete-id");
        const wasActive = activeEndpointId === deleteId;
        storedEndpoints = storedEndpoints.filter((ep) => ep.id !== deleteId);

        if (wasActive && storedEndpoints.length > 0) {
          activeEndpointId = storedEndpoints[0].id;
        }
        if (editingProfileId === deleteId) {
          editingProfileId = activeEndpointId;
          const nextEdit = storedEndpoints.find((ep) => ep.id === editingProfileId);
          syncProfileFormInputs(nextEdit);
        }

        const activeProfile = storedEndpoints.find((ep) => ep.id === activeEndpointId) || storedEndpoints[0];
        await persistLocalSettings({
          endpoints: storedEndpoints,
          activeEndpointId: activeProfile ? activeProfile.id : "",
          serverEndpoint: activeProfile ? activeProfile.url : "",
          apiKey: activeProfile ? activeProfile.apiKey : "",
        });

        if (storedEndpoints.length <= 1) {
          settingsMode = "view";
        }
        renderSettingsView();
        showStatus("Server profile deleted.", "info");

        if (wasActive && activeProfile) {
          queueJobs = [];
          void checkEndpointHealth(activeProfile.url, activeProfile.apiKey);
          if (jobsPane?.classList.contains("active")) {
            void fetchQueue();
          } else if (queueEventSource) {
            queueEventSource.close();
            queueEventSource = null;
          }
          notifyBadgeUpdate();
        }
      });
    });
  }

  function renderSettingsView(): void {
    const activeProfile = storedEndpoints.find((ep) => ep.id === activeEndpointId) || storedEndpoints[0] || null;
    const hasConfiguredServer = Boolean(activeProfile && activeProfile.url);
    const isMultiServer = storedEndpoints.length > 1;

    // 1. Multi-server profile list section
    if (profilesSection) {
      profilesSection.classList.toggle("hidden", !isMultiServer);
    }

    // 2. Single-server "+ Add Server" link (only show if exactly 1 server is configured and in view mode)
    if (addServerSingleWrap) {
      addServerSingleWrap.classList.toggle(
        "hidden",
        isMultiServer || !hasConfiguredServer || settingsMode !== "view"
      );
    }

    // 3. Fallback: if no server configured yet, force edit mode
    if (!hasConfiguredServer) {
      settingsMode = "edit";
    }

    if (settingsMode === "view" && hasConfiguredServer) {
      activeServerCard?.classList.remove("hidden");
      serverEditForm?.classList.add("hidden");

      if (cardServerName) {
        cardServerName.textContent = activeProfile?.name || "Default";
      }
      if (cardServerUrl) {
        cardServerUrl.textContent = activeProfile?.url || "";
        cardServerUrl.title = activeProfile?.url || "";
      }
      if (cardServerStatus && endpointStatus) {
        cardServerStatus.textContent = endpointStatus.textContent || "Checking...";
      }
    } else {
      activeServerCard?.classList.add("hidden");
      serverEditForm?.classList.remove("hidden");

      if (cancelEditBtn) {
        cancelEditBtn.classList.toggle("hidden", !hasConfiguredServer);
      }

      if (profileNameGroup) {
        profileNameGroup.classList.toggle("hidden", !isMultiServer && settingsMode !== "add");
      }
    }

    renderProfilesList();
    renderHeaderEndpointSwitcher();
  }

  function applyScopeToggleUI(): void {
    scopeBrowserBtn?.classList.toggle("active", currentQueueScope !== "all");
    scopeAllBtn?.classList.toggle("active", currentQueueScope === "all");
  }

  async function setQueueScope(scope: QueueScope): Promise<void> {
    currentQueueScope = scope === "all" ? "all" : "browser";
    applyScopeToggleUI();
    await persistLocalSettings({ queueScope: currentQueueScope });
    renderQueue(queueJobs);
    notifyBadgeUpdate();
  }

  scopeBrowserBtn?.addEventListener("click", () => void setQueueScope("browser"));
  scopeAllBtn?.addEventListener("click", () => void setQueueScope("all"));

  function applyStatusFilterUI(): void {
    filterActiveBtn?.classList.toggle("active", currentStatusFilter === "active");
    filterActiveBtn?.setAttribute("aria-pressed", currentStatusFilter === "active" ? "true" : "false");

    filterCompletedBtn?.classList.toggle("active", currentStatusFilter === "completed");
    filterCompletedBtn?.setAttribute("aria-pressed", currentStatusFilter === "completed" ? "true" : "false");

    filterFailedBtn?.classList.toggle("active", currentStatusFilter === "failed");
    filterFailedBtn?.setAttribute("aria-pressed", currentStatusFilter === "failed" ? "true" : "false");
  }

  function setStatusFilter(filter: StatusFilter): void {
    if (filter === null || currentStatusFilter === filter) {
      currentStatusFilter = null;
    } else {
      currentStatusFilter = filter;
    }
    applyStatusFilterUI();
    renderQueue(queueJobs);
  }

  filterActiveBtn?.addEventListener("click", () => setStatusFilter("active"));
  filterCompletedBtn?.addEventListener("click", () => setStatusFilter("completed"));
  filterFailedBtn?.addEventListener("click", () => setStatusFilter("failed"));

  function checkActiveTabFilePage(): void {
    if (typeof chrome !== "undefined" && chrome.tabs && typeof chrome.tabs.query === "function") {
      try {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          if (chrome.runtime.lastError || !Array.isArray(tabs) || tabs.length === 0) return;
          const activeTab = tabs[0];
          const tabUrl = activeTab?.url;
          if (tabUrl && isDownloadablePageUrl(tabUrl)) {
            currentActiveTabUrl = tabUrl;
            if (activePageUrlText) {
              activePageUrlText.textContent = tabUrl;
              activePageUrlText.title = tabUrl;
            }
            if (activePageBanner) {
              activePageBanner.style.display = "flex";
            }
          } else {
            currentActiveTabUrl = "";
            if (activePageBanner) {
              activePageBanner.style.display = "none";
            }
          }
        });
      } catch {
        // Tab query failed or restricted
      }
    }
  }

  downloadActivePageBtn?.addEventListener("click", () => {
    if (!currentActiveTabUrl) return;
    downloadActivePageBtn.disabled = true;
    downloadActivePageBtn.textContent = "Queueing...";

    if (typeof chrome !== "undefined" && chrome.runtime?.sendMessage) {
      chrome.runtime.sendMessage(
        { type: "QUEUE_URLS", urls: [currentActiveTabUrl], source: "context_menu" },
        (res?: QueueUrlsResponse) => {
          downloadActivePageBtn.disabled = false;
          downloadActivePageBtn.textContent = "⬇️ Download Current Page File";
          if (res?.ok) {
            currentActiveTabUrl = "";
            if (activePageBanner) activePageBanner.style.display = "none";
            showStatus("🟢 Successfully queued active page file!", "success");
            switchTab("jobs");
          } else {
            const err = res?.errors?.[0]?.error || "Failed to queue file";
            showStatus(`🔴 ${err}`, "error");
          }
        }
      );
    }
  });

  // Populate settings inputs & resolve active tab in parallel
  Promise.all([getStoredSettings(), getSessionTab()])
    .then(([settings, sessionTab]) => {
      storedEndpoints = settings.endpoints;
      activeEndpointId = settings.activeEndpointId;
      editingProfileId = settings.activeEndpointId || `server_${Date.now()}`;
      currentClientId = settings.clientId;
      currentClientName = settings.clientName;
      currentQueueScope = settings.queueScope;

      const activeProfile = storedEndpoints.find((ep) => ep.id === activeEndpointId) || null;
      syncProfileFormInputs(activeProfile);
      if (clientNameInput) clientNameInput.value = currentClientName;
      if (enableNotificationsInput) enableNotificationsInput.checked = settings.enableNotifications !== false;

      applyScopeToggleUI();
      settingsMode = settings.serverEndpoint ? "view" : "edit";
      renderSettingsView();

      checkActiveTabFilePage();
      if (!settings.serverEndpoint) {
        if (endpointStatus) endpointStatus.textContent = "Not configured";
        switchTab("settings", { saveSession: false });
      } else {
        void checkEndpointHealth(settings.serverEndpoint, settings.apiKey);
        const hasExtRuntime =
          typeof chrome !== "undefined" &&
          Boolean(chrome.storage?.session || chrome.runtime?.sendMessage);
        const targetTab: PopupTab = hasExtRuntime
          ? sessionTab
          : isPopupTab(urlTab)
            ? urlTab
            : sessionTab;
        if (urlTab && urlTab !== targetTab) {
          setPopupActionTab(targetTab);
        }
        switchTab(targetTab, { saveSession: false });
      }
    })
    .catch(() => {
      checkActiveTabFilePage();
      applyTabDom("jobs");
    });

  // Switch active server via header dropdown
  headerEndpointSelect?.addEventListener("change", async () => {
    const newId = headerEndpointSelect.value;
    const selected = storedEndpoints.find((ep) => ep.id === newId);
    if (!selected) return;

    activeEndpointId = selected.id;
    editingProfileId = selected.id;
    syncProfileFormInputs(selected);
    settingsMode = "view";
    renderSettingsView();

    await persistLocalSettings({
      activeEndpointId: selected.id,
      serverEndpoint: selected.url,
      apiKey: selected.apiKey,
    });

    if (endpointStatus) endpointStatus.textContent = "Checking...";
    void checkEndpointHealth(selected.url, selected.apiKey);

    queueJobs = [];
    if (jobsPane?.classList.contains("active")) {
      void fetchQueue();
    } else if (queueEventSource) {
      queueEventSource.close();
      queueEventSource = null;
    }

    notifyBadgeUpdate();
  });

  function startAddServer(): void {
    editingProfileId = `server_${Date.now()}`;
    const newIdx = storedEndpoints.length + 1;
    const defaultName = storedEndpoints.length === 0 ? "Default" : `Server ${newIdx}`;
    if (profileNameInput) profileNameInput.value = defaultName;
    if (serverEndpointInput) serverEndpointInput.value = "";
    if (apiKeyInput) apiKeyInput.value = "";
    clearStatus();
    settingsMode = "add";
    renderSettingsView();
    if (profileNameGroup && !profileNameGroup.classList.contains("hidden")) {
      profileNameInput?.focus();
    } else {
      serverEndpointInput?.focus();
    }
  }

  addProfileBtn?.addEventListener("click", startAddServer);
  addServerSingleBtn?.addEventListener("click", startAddServer);

  editServerBtn?.addEventListener("click", () => {
    const activeProfile = storedEndpoints.find((ep) => ep.id === activeEndpointId) || storedEndpoints[0] || null;
    editingProfileId = activeProfile ? activeProfile.id : "";
    syncProfileFormInputs(activeProfile);
    clearStatus();
    settingsMode = "edit";
    renderSettingsView();
    serverEndpointInput?.focus();
  });

  cancelEditBtn?.addEventListener("click", () => {
    const activeProfile = storedEndpoints.find((ep) => ep.id === activeEndpointId) || storedEndpoints[0] || null;
    editingProfileId = activeProfile ? activeProfile.id : "";
    syncProfileFormInputs(activeProfile);
    clearStatus();
    settingsMode = "view";
    renderSettingsView();
  });

  enableNotificationsInput?.addEventListener("change", async () => {
    if (enableNotificationsInput) {
      await persistLocalSettings({ enableNotifications: enableNotificationsInput.checked });
      showStatus("Notification preferences saved.", "info");
    }
  });

  clientNameInput?.addEventListener("change", async () => {
    if (clientNameInput) {
      const val = clientNameInput.value.trim() || currentClientName || "Browser";
      currentClientName = val;
      clientNameInput.value = val;
      await persistLocalSettings({ clientName: val });
      showStatus("Browser device name saved.", "info");
    }
  });

  // Clear status feedback when editing inputs
  [profileNameInput, serverEndpointInput, apiKeyInput, clientNameInput, pasteUrlsInput].forEach((input) => {
    input?.addEventListener("input", clearStatus);
  });

  // Check endpoint connectivity
  async function checkEndpointHealth(endpoint: string, apiKey: string): Promise<void> {
    if (!endpointStatus) return;
    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (apiKey) headers["X-API-Key"] = apiKey;
      const signal = typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(3000) : undefined;
      const res = await fetch(`${endpoint}/health`, { method: "GET", headers, signal });
      if (res.ok) {
        const data = (await res.json()) as { version?: string; commit?: string };
        const ver = data.version || "0.2.0";
        const commitSuffix = data.commit && data.commit !== "dev" ? ` (${data.commit})` : "";
        endpointStatus.textContent = storedEndpoints.length >= 2 ? `🟢 v${ver}` : `🟢 Online (v${ver}${commitSuffix})`;
        endpointStatus.title = `Online — Daemon v${ver}${commitSuffix}`;
      } else {
        endpointStatus.textContent = `🔴 HTTP ${res.status}`;
        endpointStatus.title = `HTTP ${res.status}`;
      }
    } catch {
      const perm = await checkMissingFirefoxPermission(endpoint);
      if (perm.missing) {
        endpointStatus.textContent = "🔴 Permission Needed";
        endpointStatus.title = "Permission required by Firefox — click Settings to allow";
      } else {
        endpointStatus.textContent = "🔴 Offline";
        endpointStatus.title = "Offline";
      }
    }
    if (cardServerStatus && endpointStatus) {
      cardServerStatus.textContent = endpointStatus.textContent;
    }
  }

  // Save Settings
  saveBtn?.addEventListener("click", async () => {
    const rawEndpoint = serverEndpointInput?.value || "";
    const endpoint = normalizeEndpoint(rawEndpoint);
    const apiKey = (apiKeyInput?.value || "").trim();
    const enableNotifications = enableNotificationsInput ? enableNotificationsInput.checked : true;

    if (!endpoint) {
      showStatus("Please enter your Server Endpoint URL.", "error");
      return;
    }

    if (!isValidHttpUrl(endpoint)) {
      showStatus("Invalid URL: Server Endpoint must start with http:// or https://", "error");
      return;
    }

    const name = (profileNameInput?.value || "").trim() || "Default";
    if (profileNameInput && !profileNameInput.value.trim()) {
      profileNameInput.value = name;
    }

    const updatedClientName = (clientNameInput?.value || "").trim() || currentClientName || "Browser";
    currentClientName = updatedClientName;
    if (clientNameInput) {
      clientNameInput.value = updatedClientName;
    }

    const targetId = editingProfileId || activeEndpointId || `server_${Date.now()}`;
    const existingIdx = storedEndpoints.findIndex((ep) => ep.id === targetId);
    const updatedProfile: ServerProfile = { id: targetId, name, url: endpoint, apiKey };

    if (existingIdx >= 0) {
      storedEndpoints[existingIdx] = updatedProfile;
    } else {
      storedEndpoints.push(updatedProfile);
    }

    activeEndpointId = updatedProfile.id;
    editingProfileId = updatedProfile.id;

    await persistLocalSettings({
      endpoints: storedEndpoints,
      activeEndpointId,
      serverEndpoint: endpoint,
      apiKey,
      enableNotifications,
      clientName: updatedClientName,
    });

    settingsMode = "view";
    renderSettingsView();
    queueJobs = [];

    const perm = await checkMissingFirefoxPermission(endpoint);
    if (perm.missing) {
      promptFirefoxPermissionAction(
        perm,
        `Settings saved! Firefox requires permission to connect to ${perm.hostname}.`,
        "info",
        showStatusWithAction,
        showStatus,
        (hostname) => {
          showStatus(`🟢 Permission granted for ${hostname}! Checking status...`, "success");
          void checkEndpointHealth(endpoint, apiKey);
          notifyBadgeUpdate();
        }
      );
    } else {
      showStatus("Settings saved successfully!", "success");
    }

    void checkEndpointHealth(endpoint, apiKey);
    notifyBadgeUpdate();
  });

  // Test Connection
  testBtn?.addEventListener("click", async () => {
    const activeProfile = storedEndpoints.find((ep) => ep.id === activeEndpointId) || storedEndpoints[0] || null;
    const rawEndpoint = (settingsMode === "view" && activeProfile?.url)
      ? activeProfile.url
      : (serverEndpointInput?.value || "");
    const endpoint = normalizeEndpoint(rawEndpoint);
    const apiKey = (settingsMode === "view" && activeProfile?.url)
      ? activeProfile.apiKey
      : (apiKeyInput?.value || "").trim();

    if (!endpoint || !isValidHttpUrl(endpoint)) {
      showStatus("Please enter a valid Server Endpoint URL starting with http:// or https://", "error");
      return;
    }

    showStatus("Connecting to server...", "info");
    testBtn.disabled = true;
    if (saveBtn) saveBtn.disabled = true;

    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (apiKey) headers["X-API-Key"] = apiKey;
      const signal = typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(5000) : undefined;
      const response = await fetch(`${endpoint}/health`, { method: "GET", headers, signal });

      if (!response.ok) {
        showStatus(`HTTP ${response.status} ${response.statusText}`, "error");
        return;
      }

      const data = (await response.json()) as { status?: string; version?: string; commit?: string };
      if (data && data.status === "ok") {
        const commitSuffix = data.commit && data.commit !== "dev" ? ` (${data.commit})` : "";
        showStatus(`🟢 Online — Daemon v${data.version || "0.2.0"}${commitSuffix}`, "success");
        if (endpointStatus) endpointStatus.textContent = `🟢 Online (v${data.version || "0.2.0"}${commitSuffix})`;
        if (cardServerStatus && endpointStatus) cardServerStatus.textContent = endpointStatus.textContent;
      } else {
        showStatus(`🟡 Daemon returned: ${JSON.stringify(data)}`, "info");
      }
    } catch (err) {
      const errName = err instanceof Error ? err.name : "";
      const rawError = err instanceof Error ? err.message : "Failed to fetch";
      const isTimeout = errName === "TimeoutError";
      const isNetworkError =
        !isTimeout &&
        (rawError.includes("NetworkError") ||
          rawError.includes("Failed to fetch") ||
          rawError.includes("Network request failed") ||
          rawError.includes("network error"));

      const perm = isNetworkError
        ? await checkMissingFirefoxPermission(endpoint)
        : { missing: false, originPattern: null, hostname: "" };
      if (perm.missing) {
        promptFirefoxPermissionAction(
          perm,
          `🔴 Connection blocked: Firefox requires permission to connect to ${perm.hostname}.`,
          "error",
          showStatusWithAction,
          showStatus,
          (hostname) => {
            showStatus(`🟢 Permission granted for ${hostname}! Testing connection...`, "success");
            setTimeout(() => {
              testBtn?.click();
            }, 400);
          }
        );
      } else {
        const errorMsg = isTimeout
          ? "Connection timed out (5s). Check Tailscale/LAN connection."
          : `Unreachable: ${rawError}`;
        showStatus(`🔴 ${errorMsg}`, "error");
      }
      if (endpointStatus) endpointStatus.textContent = "🔴 Offline";
      if (cardServerStatus && endpointStatus) cardServerStatus.textContent = endpointStatus.textContent;
    } finally {
      testBtn.disabled = false;
      if (saveBtn) saveBtn.disabled = false;
    }
  });

  // Quick-Submit URLs to Server
  submitUrlsBtn?.addEventListener("click", () => {
    const rawText = pasteUrlsInput?.value || "";
    if (!rawText.trim()) {
      showStatus("Please paste at least one Magnet URI or Hoster link.", "error");
      return;
    }

    const urls = extractUrlsFromText(rawText);
    if (urls.length === 0) {
      showStatus("No valid Magnet or Hoster links detected.", "error");
      return;
    }

    const subfolder = (quickSubfolderInput?.value || "").trim();
    showStatus(`Sending ${urls.length} link(s) to server...`, "info");
    submitUrlsBtn.disabled = true;

    if (typeof chrome !== "undefined" && chrome.runtime?.sendMessage) {
      chrome.runtime.sendMessage(
        { type: "QUEUE_URLS", urls, subfolder, source: "paste" },
        (response?: QueueUrlsResponse) => {
          submitUrlsBtn.disabled = false;
          if (chrome.runtime.lastError) {
            showStatus(`Error: ${chrome.runtime.lastError.message}`, "error");
            return;
          }
          if (response?.ok) {
            if (pasteUrlsInput) pasteUrlsInput.value = "";
            showStatus(`🟢 Successfully queued ${response.queued} download(s)!`, "success");
            switchTab("jobs");
          } else if (response && response.queued > 0) {
            showStatus(`🟡 Queued ${response.queued} item(s), but some failed.`, "error");
            switchTab("jobs");
          } else {
            const errMsg = response?.errors?.[0]?.error || "Failed to queue downloads";
            showStatus(`🔴 ${errMsg}`, "error");
          }
        }
      );
    } else {
      submitUrlsBtn.disabled = false;
      showStatus("chrome.runtime messaging unavailable.", "error");
    }
  });

  // Torrent File Upload in Popup
  const torrentDropzone = document.getElementById("torrentDropzone") as HTMLElement | null;
  const torrentFileInput = document.getElementById("torrentFileInput") as HTMLInputElement | null;

  function highlightTorrentDrop(e: Event): void {
    e.preventDefault();
    e.stopPropagation();
    torrentDropzone?.classList.add("dragover");
  }

  function unhighlightTorrentDrop(e: Event): void {
    e.preventDefault();
    e.stopPropagation();
    torrentDropzone?.classList.remove("dragover");
  }

  async function uploadTorrentFile(file: File | undefined): Promise<void> {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".torrent")) {
      showStatus("Please select a valid .torrent file.", "error");
      return;
    }

    const settings = await getStoredSettings();
    if (!settings.serverEndpoint) {
      showStatus("Server Endpoint not configured. Please configure it in Settings.", "error");
      switchTab("settings");
      return;
    }

    const subfolder = (quickSubfolderInput?.value || "").trim();
    showStatus(`Uploading ${file.name} to server...`, "info");

    const formData = new FormData();
    formData.append("file", file);
    if (subfolder) formData.append("subfolder", subfolder);
    if (settings.apiKey) formData.append("apiKey", settings.apiKey);
    if (settings.clientId) formData.append("client_id", settings.clientId);
    if (settings.clientName) formData.append("client_name", settings.clientName);

    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (settings.apiKey) {
        headers["X-API-Key"] = settings.apiKey;
      }
      const response = await fetch(`${settings.serverEndpoint}/api/upload/torrent`, {
        method: "POST",
        headers,
        body: formData,
      });

      const resJson = (await response.json().catch(() => null)) as { detail?: string; message?: string } | null;
      if (!response.ok) {
        const errorDetail = resJson?.detail || `Upload failed (HTTP ${response.status})`;
        showStatus(`🔴 ${errorDetail}`, "error");
        return;
      }

      showStatus(`🟢 Successfully queued ${resJson?.message || file.name}!`, "success");
      switchTab("jobs");
      notifyBadgeUpdate();
    } catch (err) {
      const perm = await checkMissingFirefoxPermission(settings.serverEndpoint);
      if (perm.missing) {
        promptFirefoxPermissionAction(
          perm,
          `🔴 Upload blocked: Firefox requires permission to connect to ${perm.hostname}.`,
          "error",
          showStatusWithAction,
          showStatus,
          (hostname) => {
            showStatus(`🟢 Permission granted for ${hostname}! Please re-try uploading.`, "success");
          }
        );
      } else {
        const errMsg = err instanceof Error ? err.message : String(err);
        showStatus(`🔴 Error uploading torrent: ${errMsg}`, "error");
      }
    }
  }

  if (torrentDropzone && torrentFileInput) {
    torrentDropzone.addEventListener("click", () => {
      torrentFileInput.click();
    });

    ["dragenter", "dragover"].forEach((eventName) => {
      torrentDropzone.addEventListener(eventName, highlightTorrentDrop, false);
    });
    ["dragleave", "drop"].forEach((eventName) => {
      torrentDropzone.addEventListener(eventName, unhighlightTorrentDrop, false);
    });

    torrentDropzone.addEventListener("drop", (e: DragEvent) => {
      const files = e.dataTransfer?.files;
      if (files && files.length > 0) {
        for (let i = 0; i < files.length; i++) {
          void uploadTorrentFile(files[i]);
        }
      }
    });

    torrentFileInput.addEventListener("change", () => {
      if (torrentFileInput.files && torrentFileInput.files.length > 0) {
        for (let i = 0; i < torrentFileInput.files.length; i++) {
          void uploadTorrentFile(torrentFileInput.files[i]);
        }
        torrentFileInput.value = "";
      }
    });
  }

  function connectQueueStream(endpoint: string, apiKey: string): void {
    if (queueEventSource) {
      queueEventSource.close();
      queueEventSource = null;
    }
    if (!endpoint || typeof EventSource === "undefined") return;

    try {
      const baseUrl = endpoint.replace(/\/+$/, "");
      const url = new URL(`${baseUrl}/api/status/stream`);
      if (apiKey) url.searchParams.set("apiKey", apiKey);
      queueEventSource = new EventSource(url.toString());

      queueEventSource.addEventListener("init", (e: MessageEvent<string>) => {
        try {
          queueJobs = JSON.parse(e.data) as DownloadJob[];
          renderQueue(queueJobs);
        } catch {
          // ignore parse errors
        }
      });

      queueEventSource.addEventListener("job_created", (e: MessageEvent<string>) => {
        try {
          const job = JSON.parse(e.data) as DownloadJob;
          upsertQueueJob(job);
          renderQueue(queueJobs);
        } catch {
          // ignore parse errors
        }
      });

      queueEventSource.addEventListener("job_update", (e: MessageEvent<string>) => {
        try {
          const job = JSON.parse(e.data) as DownloadJob;
          upsertQueueJob(job);
          renderQueue(queueJobs);
        } catch {
          // ignore parse errors
        }
      });

      queueEventSource.addEventListener("job_deleted", (e: MessageEvent<string>) => {
        try {
          const data = JSON.parse(e.data) as { job_id: string };
          queueJobs = queueJobs.filter((j) => j.job_id !== data.job_id);
          renderQueue(queueJobs);
        } catch {
          // ignore parse errors
        }
      });

      queueEventSource.addEventListener("jobs_cleared", (e: MessageEvent<string>) => {
        try {
          const data = JSON.parse(e.data) as { all?: boolean; status?: string };
          if (data.all) {
            queueJobs = [];
          } else if (data.status === "finished") {
            queueJobs = queueJobs.filter((j) => !["completed", "failed"].includes(j.status));
          } else if (data.status) {
            queueJobs = queueJobs.filter((j) => j.status !== data.status);
          } else {
            queueJobs = queueJobs.filter((j) => !["completed", "failed"].includes(j.status));
          }
        } catch {
          queueJobs = queueJobs.filter((j) => !["completed", "failed"].includes(j.status));
        }
        renderQueue(queueJobs);
      });

      queueEventSource.onerror = () => {
        // EventSource automatically retries connection
      };
    } catch (err) {
      console.error("Queue SSE connection error:", err);
    }
  }

  function upsertQueueJob(job: DownloadJob): void {
    const idx = queueJobs.findIndex((j) => j.job_id === job.job_id);
    if (idx >= 0) {
      queueJobs[idx] = job;
    } else {
      queueJobs.unshift(job);
    }
  }

  function updateSummaryCounters(jobs: DownloadJob[]): {
    activeCount: number;
    completedCount: number;
    failedCount: number;
  } {
    const activeCount = Array.isArray(jobs)
      ? jobs.filter((j) => (ACTIVE_STATUSES as readonly string[]).includes(j.status)).length
      : 0;
    const completedCount = Array.isArray(jobs) ? jobs.filter((j) => j.status === "completed").length : 0;
    const failedCount = Array.isArray(jobs) ? jobs.filter((j) => j.status === "failed").length : 0;

    if (countActiveEl) countActiveEl.textContent = String(activeCount);
    if (countCompletedEl) countCompletedEl.textContent = String(completedCount);
    if (countFailedEl) countFailedEl.textContent = String(failedCount);
    if (tabJobsBtn) {
      tabJobsBtn.textContent = activeCount > 0 ? `📊 Jobs (${activeCount})` : "📊 Jobs";
    }
    return { activeCount, completedCount, failedCount };
  }

  function renderQueue(jobs: DownloadJob[]): void {
    if (!queueList) return;
    const scopedJobs = Array.isArray(jobs)
      ? currentQueueScope === "all"
        ? jobs
        : jobs.filter((j) => j.client_id && j.client_id === currentClientId)
      : [];

    const { completedCount } = updateSummaryCounters(scopedJobs);

    if (scopedJobs.length === 0) {
      if (queueCapNote) queueCapNote.style.display = "none";
      const emptyMsg =
        currentQueueScope === "browser" && Array.isArray(jobs) && jobs.length > 0
          ? `No jobs from this browser yet.<br/>Switch to <b>All Jobs</b> to view *arr or other devices.`
          : `No active or recent jobs found.`;
      queueList.innerHTML = `<div class="empty-queue">${emptyMsg}</div>`;
      return;
    }

    // Filter queue by active pill if engaged; otherwise show all active + all failed + 5 recent completed
    const sorted = sortJobsNewestFirst(scopedJobs);
    let visibleJobs: DownloadJob[] = [];
    const activeFilterCfg = currentStatusFilter ? STATUS_FILTERS[currentStatusFilter] : undefined;

    if (activeFilterCfg) {
      visibleJobs = sorted.filter(activeFilterCfg.predicate);
      if (queueCapNote) queueCapNote.style.display = "none";
      if (visibleJobs.length === 0) {
        queueList.innerHTML = `
          <div class="empty-queue">
            <div>${activeFilterCfg.emptyText}</div>
            <div class="hint mt-1">Click the ${activeFilterCfg.pillLabel} pill or <a id="resetFilterLink" class="text-[var(--primary)] underline cursor-pointer">show all</a></div>
          </div>
        `;
        document.getElementById("resetFilterLink")?.addEventListener("click", () => setStatusFilter(null));
        return;
      }
    } else {
      let completedShown = 0;
      visibleJobs = sorted.filter((job) => {
        if (job.status === "completed") {
          if (completedShown >= MAX_RECENT_COMPLETED) return false;
          completedShown++;
        }
        return true;
      });

      if (queueCapNote) {
        queueCapNote.style.display = completedCount > MAX_RECENT_COMPLETED ? "block" : "none";
      }
    }

    queueList.innerHTML = visibleJobs
      .map((job) => renderJobCardHtml(job, currentQueueScope))
      .join("");
  }

  // Live Queue Fetcher
  async function fetchQueue(): Promise<void> {
    if (!queueList) return;
    const settings = await getStoredSettings();

    if (!settings.serverEndpoint) {
      updateSummaryCounters([]);
      if (queueCapNote) queueCapNote.style.display = "none";
      queueList.innerHTML = `<div class="empty-queue">Server Endpoint not configured.<br/>Go to <b>Settings</b> to connect.</div>`;
      return;
    }

    connectQueueStream(settings.serverEndpoint, settings.apiKey);

    if (queueJobs.length === 0) {
      queueList.innerHTML = `<div class="empty-queue">Loading active jobs...</div>`;
    }

    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (settings.apiKey) headers["X-API-Key"] = settings.apiKey;

      const signal = typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(5000) : undefined;
      const response = await fetch(`${settings.serverEndpoint}/api/status`, {
        method: "GET",
        headers,
        signal,
      });

      if (!response.ok) {
        if (queueJobs.length === 0) {
          queueList.innerHTML = `<div class="empty-queue text-[var(--error-text)]">Failed to load jobs (HTTP ${response.status})</div>`;
        }
        return;
      }

      queueJobs = (await response.json()) as DownloadJob[];
      renderQueue(queueJobs);
    } catch (err) {
      if (queueJobs.length === 0) {
        const perm = await checkMissingFirefoxPermission(settings.serverEndpoint);
        if (perm.missing) {
          queueList.innerHTML = `
            <div class="empty-queue text-[var(--error-text)] text-center">
              <div>Firefox requires permission to connect to ${escapeHtml(perm.hostname)}.</div>
              <button id="grantQueuePermBtn" class="btn-primary mt-2 text-[11px] px-2.5 py-1.5">
                🔑 Allow Connection to ${escapeHtml(perm.hostname)}
              </button>
            </div>
          `;
          document.getElementById("grantQueuePermBtn")?.addEventListener("click", async (e: MouseEvent) => {
            const btn = e.currentTarget as HTMLButtonElement;
            btn.disabled = true;
            btn.textContent = "Requesting permission...";
            const granted = await requestHostPermission(perm.originPattern || "");
            if (granted) {
              void fetchQueue();
            } else {
              btn.disabled = false;
              btn.textContent = `🔑 Allow Connection to ${perm.hostname}`;
            }
          });
        } else {
          const msg = err instanceof Error ? err.message : "Failed to fetch status";
          queueList.innerHTML = `<div class="empty-queue text-[var(--error-text)]">Unreachable: ${escapeHtml(msg)}</div>`;
        }
      }
    }
  }

  refreshQueueBtn?.addEventListener("click", () => {
    void fetchQueue();
  });

  async function openServerDashboard(targetPath = "/settings"): Promise<void> {
    const config = await getStoredSettings();
    if (!config.serverEndpoint) {
      showStatus("Please enter and save a Server Endpoint URL first.", "error");
      return;
    }
    const cleanEndpoint = config.serverEndpoint.replace(/\/+$/, "");
    const targetUrl = `${cleanEndpoint}${targetPath}`;
    if (typeof chrome !== "undefined" && chrome.tabs?.create) {
      void chrome.tabs.create({ url: targetUrl });
    } else if (typeof window !== "undefined") {
      window.open(targetUrl, "_blank");
    }
  }

  openServerSettingsBtn?.addEventListener("click", () => void openServerDashboard("/settings"));
  openDashboardJobsBtn?.addEventListener("click", () => void openServerDashboard("/dashboard#jobs"));
  capNoteDashboardLink?.addEventListener("click", () => void openServerDashboard("/dashboard#jobs"));

  window.addEventListener("beforeunload", () => {
    if (queueEventSource) {
      queueEventSource.close();
      queueEventSource = null;
    }
  });
});
