/** Chrome/Firefox storage persistence, profile migration, and client identity helpers. */

import type { ClientIdentity, ExtensionSettings, ServerProfile } from "./types";

export function normalizeEndpoint(rawUrl: string): string {
  if (!rawUrl) return "";
  return rawUrl.trim().replace(/\/+$/, "");
}

export function isValidHttpUrl(url: string): boolean {
  if (!url) return false;
  const lower = url.trim().toLowerCase();
  return lower.startsWith("http://") || lower.startsWith("https://");
}

export function deriveServerName(url: string, fallback = "Default"): string {
  try {
    return new URL(url).hostname || fallback;
  } catch {
    return fallback;
  }
}

export function notifyBadgeUpdate(): void {
  if (typeof chrome !== "undefined" && chrome.runtime?.sendMessage) {
    chrome.runtime.sendMessage({ type: "UPDATE_BADGE" }, () => {
      void chrome.runtime.lastError;
    });
  }
}

const STORAGE_FALLBACK_SERIALIZERS: ReadonlyArray<[keyof StoredPayload, (value: unknown) => string]> = [
  ["endpoints", (v) => JSON.stringify(v)],
  ["activeEndpointId", (v) => String(v || "")],
  ["serverEndpoint", (v) => String(v || "")],
  ["nasEndpoint", (v) => String(v || "")],
  ["apiKey", (v) => String(v || "")],
  ["enableNotifications", (v) => String(v)],
  ["clientId", (v) => String(v || "")],
  ["clientName", (v) => String(v || "")],
  ["queueScope", (v) => String(v || "browser")],
];

export type StoredPayload = Partial<ExtensionSettings>;

export function persistLocalSettings(payload: StoredPayload): Promise<void> {
  const normalizedPayload: StoredPayload = { ...payload };
  if ("serverEndpoint" in normalizedPayload && !("nasEndpoint" in normalizedPayload)) {
    normalizedPayload.nasEndpoint = normalizedPayload.serverEndpoint;
  } else if ("nasEndpoint" in normalizedPayload && !("serverEndpoint" in normalizedPayload)) {
    normalizedPayload.serverEndpoint = normalizedPayload.nasEndpoint;
  }

  try {
    if ("serverEndpoint" in normalizedPayload) {
      localStorage.setItem("hasConfiguredEndpoint", normalizedPayload.serverEndpoint ? "1" : "0");
    }
  } catch {
    // ignore storage errors in restricted contexts
  }

  return new Promise((resolve) => {
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      chrome.storage.local.set(normalizedPayload, () => resolve());
    } else {
      for (const [key, serialize] of STORAGE_FALLBACK_SERIALIZERS) {
        if (key in normalizedPayload) {
          localStorage.setItem(key, serialize(normalizedPayload[key]));
        }
      }
      resolve();
    }
  });
}

async function resolveClientIdentityFromBackground(): Promise<ClientIdentity | null> {
  const nav = typeof navigator !== "undefined" ? (navigator as Navigator & { brave?: unknown }) : undefined;
  const isBrave = Boolean(nav?.brave);
  if (typeof chrome !== "undefined" && chrome.runtime?.sendMessage) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "ENSURE_CLIENT_IDENTITY", isBrave }, (identity?: ClientIdentity) => {
        void chrome.runtime.lastError;
        resolve(identity?.clientId ? identity : null);
      });
    });
  }
  return null;
}

export async function getStoredSettings(): Promise<ExtensionSettings> {
  return new Promise((resolve) => {
    const finishResolve = async (rawItems: Record<string, unknown>) => {
      let endpoints: ServerProfile[] = Array.isArray(rawItems.endpoints)
        ? rawItems.endpoints
            .filter(
              (ep): ep is ServerProfile =>
                Boolean(ep && typeof (ep as ServerProfile).url === "string" && (ep as ServerProfile).url.trim())
            )
            .map((ep, idx) => ({
              id: ep.id || `server_${idx + 1}`,
              name: (ep.name || "").trim() || "Default",
              url: normalizeEndpoint(ep.url),
              apiKey: (ep.apiKey || "").trim(),
            }))
        : [];

      let activeId = typeof rawItems.activeEndpointId === "string" ? rawItems.activeEndpointId : "";
      const legacyUrl = normalizeEndpoint(
        String(rawItems.serverEndpoint || rawItems.nasEndpoint || "")
      );
      const legacyKey = String(rawItems.apiKey || "").trim();

      // Automatic migration from legacy single serverEndpoint/nasEndpoint -> endpoints array
      if (endpoints.length === 0 && legacyUrl) {
        endpoints = [
          {
            id: "default",
            name: "Default",
            url: legacyUrl,
            apiKey: legacyKey,
          },
        ];
        activeId = "default";
        await persistLocalSettings({ endpoints, activeEndpointId: activeId });
      }

      const activeProfile = endpoints.find((ep) => ep.id === activeId) || endpoints[0] || null;
      if (activeProfile) {
        activeId = activeProfile.id;
      }

      let clientId = String(rawItems.clientId || "").trim();
      let clientName = String(rawItems.clientName || "").trim();
      if (!clientId || !clientName) {
        const bgIdentity = await resolveClientIdentityFromBackground();
        if (bgIdentity) {
          clientId = clientId || bgIdentity.clientId;
          clientName = clientName || bgIdentity.clientName;
        } else {
          if (!clientId) {
            clientId =
              typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
                ? crypto.randomUUID()
                : `client-${Date.now()}`;
          }
          if (!clientName) {
            clientName = "Browser";
          }
          await persistLocalSettings({ clientId, clientName });
        }
      }

      const resolvedUrl = activeProfile ? activeProfile.url : "";
      try {
        if (typeof localStorage !== "undefined") {
          localStorage.setItem("hasConfiguredEndpoint", resolvedUrl ? "1" : "0");
        }
      } catch {
        // ignore storage errors
      }

      resolve({
        endpoints,
        activeEndpointId: activeId,
        serverEndpoint: resolvedUrl,
        nasEndpoint: resolvedUrl,
        apiKey: activeProfile ? activeProfile.apiKey : "",
        enableNotifications: rawItems.enableNotifications !== false,
        clientId,
        clientName,
        queueScope: rawItems.queueScope === "all" ? "all" : "browser",
      });
    };

    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      chrome.storage.local.get(
        [
          "endpoints",
          "activeEndpointId",
          "serverEndpoint",
          "nasEndpoint",
          "apiKey",
          "enableNotifications",
          "clientId",
          "clientName",
          "queueScope",
        ],
        (items) => {
          void finishResolve(items || {});
        }
      );
    } else {
      const storedNotif = localStorage.getItem("enableNotifications");
      let parsedEndpoints: unknown[] = [];
      try {
        parsedEndpoints = JSON.parse(localStorage.getItem("endpoints") || "[]") as unknown[];
      } catch {
        parsedEndpoints = [];
      }
      void finishResolve({
        endpoints: parsedEndpoints,
        activeEndpointId: localStorage.getItem("activeEndpointId") || "",
        serverEndpoint: localStorage.getItem("serverEndpoint") || localStorage.getItem("nasEndpoint") || "",
        apiKey: localStorage.getItem("apiKey") || "",
        enableNotifications: storedNotif === null ? true : storedNotif === "true",
        clientId: localStorage.getItem("clientId") || "",
        clientName: localStorage.getItem("clientName") || "",
        queueScope: localStorage.getItem("queueScope") || "browser",
      });
    }
  });
}
