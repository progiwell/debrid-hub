/** Content Script — Click'n'Load (127.0.0.1:9666) Interceptor for Debrid Hub. */

import type { QueueUrlsResponse, StatusMessageType } from "./types";

/** Display a non-intrusive in-page toast notification. */
function showToast(message: string, type: StatusMessageType = "info"): void {
  const existing = document.getElementById("debrid-hub-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "debrid-hub-toast";
  toast.style.cssText = `
    position: fixed;
    bottom: 20px;
    right: 20px;
    z-index: 2147483647;
    padding: 10px 16px;
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 13px;
    font-weight: 500;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    transition: opacity 0.3s ease, transform 0.3s ease;
    opacity: 0;
    transform: translateY(10px);
    pointer-events: none;
    max-width: 320px;
  `;

  if (type === "success") {
    toast.style.backgroundColor = "#065f46";
    toast.style.color = "#ffffff";
    toast.style.border = "1px solid #047857";
  } else if (type === "error") {
    toast.style.backgroundColor = "#991b1b";
    toast.style.color = "#ffffff";
    toast.style.border = "1px solid #b91c1c";
  } else {
    toast.style.backgroundColor = "#1e40af";
    toast.style.color = "#ffffff";
    toast.style.border = "1px solid #2563eb";
  }

  toast.textContent = message;
  document.body.appendChild(toast);

  // Trigger fade-in
  requestAnimationFrame(() => {
    toast.style.opacity = "1";
    toast.style.transform = "translateY(0)";
  });

  // Auto remove after 4 seconds
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(10px)";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

/** Extract 32-character hexadecimal key from Click'n'Load jk field without using eval. */
export function extractHexKey(jk: unknown): string | null {
  if (!jk || typeof jk !== "string") return null;
  const match = jk.match(/['"]([0-9a-fA-F]{32})['"]/);
  if (match) return match[1];

  const directMatch = jk.trim().match(/^[0-9a-fA-F]{32}$/);
  if (directMatch) return directMatch[0];

  return null;
}

/** Convert 32-char hex string to 16-byte Uint8Array. */
export function hexToBytes(hex: string): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
  }
  return bytes;
}

/** Decrypt Click'n'Load v2 AES-128-CBC payload using Web Crypto API. */
export async function decryptCnlV2(cryptedBase64: string, jk: string): Promise<string[] | null> {
  try {
    const hexKey = extractHexKey(jk);
    if (!hexKey) return null;

    const keyBytes = hexToBytes(hexKey);
    // In CNL v2 standard, the IV is the key itself
    const iv = keyBytes;

    const binaryString = atob(cryptedBase64.trim());
    const cipherBytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      cipherBytes[i] = binaryString.charCodeAt(i);
    }

    const cryptoKey = await crypto.subtle.importKey(
      "raw",
      keyBytes,
      { name: "AES-CBC" },
      false,
      ["decrypt"]
    );

    const decryptedBuffer = await crypto.subtle.decrypt(
      { name: "AES-CBC", iv },
      cryptoKey,
      cipherBytes
    );

    const decoded = new TextDecoder("utf-8").decode(decryptedBuffer);
    return decoded
      .split(/[\r\n]+/)
      .map((u) => u.trim())
      .filter((u) => u.length > 0);
  } catch (err) {
    console.warn("[Debrid Hub] Failed to decrypt Click'n'Load v2 payload:", err);
    return null;
  }
}

/** Extract URLs from a Click'n'Load form element or FormData. */
export async function extractCnlUrls(formData: FormData): Promise<string[]> {
  const urls: string[] = [];

  // 1. Check for standard Click'n'Load v2 encrypted payload
  const crypted = formData.get("crypted");
  const jk = formData.get("jk");
  if (typeof crypted === "string" && typeof jk === "string" && crypted && jk) {
    const decrypted = await decryptCnlV2(crypted, jk);
    if (decrypted && decrypted.length > 0) {
      urls.push(...decrypted);
    }
  }

  // 2. Check for plaintext URLs field
  const rawUrls = formData.get("urls");
  if (rawUrls && typeof rawUrls === "string") {
    const splitUrls = rawUrls
      .split(/[\s\r\n]+/)
      .map((u) => u.trim())
      .filter((u) => u.startsWith("http://") || u.startsWith("https://") || u.startsWith("magnet:?"));
    urls.push(...splitUrls);
  }

  return Array.from(new Set(urls));
}

/** Send intercepted URLs to background service worker. */
function sendUrlsToBackground(urls: string[]): void {
  if (!urls || urls.length === 0) {
    showToast("⚡ Click'n'Load: No valid links found", "error");
    return;
  }

  showToast(`⚡ Click'n'Load: Intercepted ${urls.length} link(s), queueing to server...`, "info");

  chrome.runtime.sendMessage(
    { type: "QUEUE_URLS", urls, source: "click_n_load" },
    (response?: QueueUrlsResponse) => {
      if (chrome.runtime.lastError) {
        showToast(`🔴 Failed to send to server: ${chrome.runtime.lastError.message}`, "error");
        return;
      }
      if (response?.ok) {
        showToast(`🟢 Click'n'Load: Successfully queued ${response.queued} link(s) to server!`, "success");
      } else if (response && response.queued > 0) {
        showToast(`🟡 Queued ${response.queued} link(s), but some failed.`, "error");
      } else {
        const errorMsg = response?.errors?.[0]?.error || "Could not queue links to server";
        showToast(`🔴 Click'n'Load error: ${errorMsg}`, "error");
      }
    }
  );
}

/** Check if a URL or action target points to Click'n'Load port 9666. */
export function isCnlTarget(url: unknown): boolean {
  if (!url || typeof url !== "string") return false;
  const lower = url.toLowerCase();
  return (
    lower.includes(":9666") ||
    lower.includes("127.0.0.1:9666") ||
    lower.includes("localhost:9666") ||
    lower.includes("/flashgot") ||
    lower.includes("flashgot_add")
  );
}

/** Decode a single form-urlencoded query component (`+` -> space). */
function decodeQueryComponent(raw: string): string {
  return decodeURIComponent(raw.replace(/\+/g, " "));
}

/** Extract download URLs from a Click'n'Load href without iterating URLSearchParams (Firefox Xray safe). */
export function extractCnlHrefUrls(href: string, baseHref?: string): string[] {
  if (!href || typeof href !== "string") return [];
  let search = "";
  try {
    const parsed = new URL(href, baseHref || "http://127.0.0.1");
    search = parsed.search;
  } catch {
    return [];
  }

  const urls: string[] = [];
  const query = search.startsWith("?") ? search.slice(1) : search;
  for (const pair of query.split("&")) {
    if (!pair) continue;
    const eqIdx = pair.indexOf("=");
    const rawKey = eqIdx >= 0 ? pair.slice(0, eqIdx) : pair;
    const rawVal = eqIdx >= 0 ? pair.slice(eqIdx + 1) : "";
    let key = "";
    let val = "";
    try {
      key = decodeQueryComponent(rawKey).toLowerCase();
      val = decodeQueryComponent(rawVal);
    } catch {
      continue;
    }
    if (key.includes("url") || key.includes("link")) {
      const parts = val
        .split(/[\s\r\n]+/)
        .filter((u) => u.startsWith("http://") || u.startsWith("https://") || u.startsWith("magnet:?"));
      urls.push(...parts);
    }
  }
  return Array.from(new Set(urls));
}

if (typeof document !== "undefined") {
  // 1. Intercept <form> submissions to 127.0.0.1:9666 (Click'n'Load standard)
  document.addEventListener(
    "submit",
    async (event: SubmitEvent) => {
      const form = event.target;
      if (!form || !(form instanceof HTMLFormElement)) return;

      const action = form.getAttribute("action") || "";
      if (!isCnlTarget(action)) return;

      event.preventDefault();
      event.stopImmediatePropagation();

      const formData = new FormData(form);
      const urls = await extractCnlUrls(formData);
      sendUrlsToBackground(urls);
    },
    true
  );

  // 2. Intercept button/link clicks with Click'n'Load indicators
  document.addEventListener(
    "click",
    async (event: MouseEvent) => {
      const rawTarget = event.target;
      const elementTarget =
        rawTarget instanceof Element
          ? rawTarget
          : rawTarget instanceof Node
            ? rawTarget.parentElement
            : null;
      if (!elementTarget) return;

      const target = elementTarget.closest("a, button, input[type='submit'], input[type='button']");
      if (!target) return;

      const href = target.getAttribute("href") || "";
      const onclick = target.getAttribute("onclick") || "";

      if (isCnlTarget(href) || isCnlTarget(onclick)) {
        event.preventDefault();
        event.stopImmediatePropagation();

        const form = target.closest("form");
        if (form instanceof HTMLFormElement) {
          const formData = new FormData(form);
          const urls = await extractCnlUrls(formData);
          sendUrlsToBackground(urls);
        } else if (href) {
          const urls = extractCnlHrefUrls(href, window.location.href);
          if (urls.length > 0) {
            sendUrlsToBackground(urls);
          } else {
            showToast("⚡ Click'n'Load link intercepted", "info");
          }
        }
      }
    },
    true
  );
}
