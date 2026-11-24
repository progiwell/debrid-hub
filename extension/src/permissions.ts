/** Firefox & Chrome runtime host permission helpers. */

import { deriveServerName } from "./storage";
import type { FirefoxPermissionInfo, StatusMessageType } from "./types";
import { getOriginPermissionPattern } from "./url_parser";

export function isFirefoxBrowser(): boolean {
  return (
    typeof navigator !== "undefined" &&
    (navigator.userAgent.includes("Firefox") ||
      (navigator.userAgent.includes("Gecko/") && !navigator.userAgent.includes("Chrome")))
  );
}

export function callPermissionApi(
  method: "contains" | "request",
  originPattern: string,
  fallback: boolean
): Promise<boolean> {
  return new Promise((resolve) => {
    const permissionsApi = typeof chrome !== "undefined" ? chrome.permissions : undefined;
    if (!permissionsApi || typeof permissionsApi[method] !== "function") {
      resolve(fallback);
      return;
    }
    try {
      const maybePromise = permissionsApi[method]({ origins: [originPattern] }, (granted: boolean) => {
        if (typeof chrome !== "undefined" && chrome.runtime?.lastError) {
          resolve(false);
        } else {
          resolve(Boolean(granted));
        }
      }) as unknown;
      if (maybePromise && typeof (maybePromise as Promise<boolean>).then === "function") {
        (maybePromise as Promise<boolean>).then(resolve).catch(() => resolve(false));
      }
    } catch {
      resolve(false);
    }
  });
}

export function hasHostPermission(originPattern: string): Promise<boolean> {
  return callPermissionApi("contains", originPattern, true);
}

export function requestHostPermission(originPattern: string): Promise<boolean> {
  return callPermissionApi("request", originPattern, false);
}

export async function checkMissingFirefoxPermission(endpoint: string): Promise<FirefoxPermissionInfo> {
  const originPattern = getOriginPermissionPattern(endpoint);
  if (!isFirefoxBrowser() || !originPattern) {
    return { missing: false, originPattern: null, hostname: "" };
  }
  const granted = await hasHostPermission(originPattern);
  return {
    missing: !granted,
    originPattern,
    hostname: deriveServerName(endpoint),
  };
}

export function promptFirefoxPermissionAction(
  permInfo: FirefoxPermissionInfo,
  statusMessage: string,
  statusType: StatusMessageType,
  showStatusWithAction: (
    message: string,
    type: StatusMessageType,
    buttonConfig: { text: string; className: string; onClick: (e: MouseEvent) => void } | null
  ) => void,
  showStatus: (message: string, type: StatusMessageType) => void,
  onGranted: (hostname: string) => void
): void {
  const { originPattern, hostname } = permInfo;
  if (!originPattern) return;

  showStatusWithAction(statusMessage, statusType, {
    text: `🔑 Allow Connection to ${hostname}`,
    className: "btn-primary",
    onClick: async (e: MouseEvent) => {
      const targetBtn = e.currentTarget as HTMLButtonElement | null;
      if (targetBtn) {
        targetBtn.disabled = true;
        targetBtn.textContent = "Requesting permission...";
      }
      const granted = await requestHostPermission(originPattern);
      if (granted) {
        onGranted(hostname);
      } else {
        showStatus(`🔴 Permission denied. Firefox requires permission to reach ${hostname}.`, "error");
      }
    },
  });
}
