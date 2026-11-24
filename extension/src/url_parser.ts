/**
 * URL Classifier, File-Page Detector & Multi-Link Extractor for Debrid Hub.
 */

import type { LinkType } from "./types";

const KNOWN_HOSTERS = [
  "1fichier.com",
  "rapidgator.net",
  "mega.nz",
  "mega.co.nz",
  "mediafire.com",
  "pixeldrain.com",
  "turbobit.net",
  "nitroflare.com",
  "ddownload.com",
  "uploaded.net",
  "ul.to",
  "k2s.cc",
  "keep2share.cc",
  "alfafile.net",
  "katfile.com",
  "clicknupload",
  "filefactory.com",
  "uptobox.com",
  "real-debrid.com",
  "debrid.it",
  "zippyshare.com",
  "send.cm",
  "dropgalaxy.com",
  "filer.net",
  "hexupload.net",
  "hitfile.net",
  "rosefile.net",
  "userscloud.com",
  "vshare.io",
  "rapidrar.com",
  "streamtape.com",
];

const DIRECT_DOWNLOAD_EXTENSIONS =
  /\.(rar|zip|7z|tar|gz|bz2|xz|iso|mkv|mp4|avi|mov|mp3|flac|epub|pdf|pkg|nsp|xci|bin)$/i;

const NON_FILE_PATHS =
  /^\/(login|register|signup|signin|tarifs|pricing|upgrade|account|profile|faq|contact|terms|privacy|dmca|legal|about|support|news|affiliate|contact\.html|index\.html|index\.php|hlp\.html)?\/?$/i;

export function isInfoHash(text: string | null | undefined): boolean {
  if (!text || typeof text !== "string") return false;
  const trimmed = text.trim();
  return /^[a-f0-9]{40}$/i.test(trimmed) || /^[a-z2-7]{32}$/i.test(trimmed);
}

export function infoHashToMagnet(hash: string | null | undefined): string {
  if (!hash) return "";
  return `magnet:?xt=urn:btih:${hash.trim()}`;
}

export function extractInfoHashes(text: string | null | undefined): string[] {
  if (!text || typeof text !== "string") return [];
  const results: string[] = [];

  const hexMatches = text.match(/\b[a-f0-9]{40}\b/gi) || [];
  for (const h of hexMatches) {
    const lower = h.toLowerCase();
    if (!results.includes(lower)) {
      results.push(lower);
    }
  }

  const b32Matches = text.match(/\b[a-z2-7]{32}\b/gi) || [];
  for (const h of b32Matches) {
    const upper = h.toUpperCase();
    if (!results.includes(upper)) {
      results.push(upper);
    }
  }
  return results;
}

export function classifyUrl(url: string | null | undefined): LinkType | null {
  if (!url || typeof url !== "string") return null;
  const trimmed = url.trim();
  const lower = trimmed.toLowerCase();

  if (lower.startsWith("magnet:?") && lower.includes("xt=urn:")) {
    return "magnet";
  }

  if (isInfoHash(trimmed)) {
    return "magnet";
  }

  if (lower.endsWith(".torrent") || lower.startsWith("torrent:")) {
    return "torrent";
  }

  if (/^https?:\/\//i.test(trimmed)) {
    try {
      const parsed = new URL(trimmed);
      const hostname = parsed.hostname.toLowerCase();
      const isKnownHoster = KNOWN_HOSTERS.some(
        (domain) => hostname === domain || hostname.endsWith("." + domain)
      );
      if (isKnownHoster || DIRECT_DOWNLOAD_EXTENSIONS.test(parsed.pathname)) {
        return "hoster";
      }
    } catch {
      return null;
    }
  }

  return null;
}

/**
 * Strictly detect whether a URL represents a downloadable hoster landing page
 * or direct media/archive URL, excluding homepages, login, and static site pages.
 */
export function isDownloadablePageUrl(url: string | null | undefined): boolean {
  if (!url || typeof url !== "string") return false;
  const trimmed = url.trim();
  if (!/^https?:\/\//i.test(trimmed)) return false;

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return false;
  }

  const hostname = parsed.hostname.toLowerCase();
  const { pathname, search, hash } = parsed;

  if (DIRECT_DOWNLOAD_EXTENSIONS.test(pathname) || pathname.toLowerCase().endsWith(".torrent")) {
    return true;
  }

  if (hostname === "1fichier.com" || hostname.endsWith(".1fichier.com")) {
    if (search && /^\?[a-zA-Z0-9_-]{8,}/.test(search)) return true;
    if (pathname && /^\/[a-zA-Z0-9_-]{8,}$/.test(pathname) && !NON_FILE_PATHS.test(pathname)) return true;
    return false;
  }

  if (hostname === "rapidgator.net" || hostname === "rg.to" || hostname.endsWith(".rapidgator.net")) {
    return /^\/file\/[a-zA-Z0-9_-]+/i.test(pathname);
  }

  if (hostname === "mega.nz" || hostname === "mega.co.nz") {
    if (/^\/file\/[a-zA-Z0-9_-]+/i.test(pathname)) return true;
    if (/^\/#[!a-zA-Z0-9_-]+/i.test(pathname)) return true;
    if (/^#!?[a-zA-Z0-9_-]+!/i.test(hash)) return true;
    return false;
  }

  if (hostname === "mediafire.com" || hostname.endsWith(".mediafire.com")) {
    if (/^\/file\/[a-zA-Z0-9_-]+/i.test(pathname)) return true;
    if (search && /^\?[a-zA-Z0-9_-]{8,}/.test(search)) return true;
    return false;
  }

  if (hostname === "pixeldrain.com" || hostname.endsWith(".pixeldrain.com")) {
    return /^\/(u|l|api\/file)\/[a-zA-Z0-9_-]+/i.test(pathname);
  }

  if (
    hostname === "turbobit.net" ||
    hostname === "hitfile.net" ||
    hostname.endsWith(".turbobit.net") ||
    hostname.endsWith(".hitfile.net")
  ) {
    if (NON_FILE_PATHS.test(pathname)) return false;
    return /^\/([a-zA-Z0-9_-]{12,}(\.html)?|download\/free\/[a-zA-Z0-9_-]+)/i.test(pathname);
  }

  if (hostname === "nitroflare.com" || hostname.endsWith(".nitroflare.com")) {
    return /^\/view\/[a-zA-Z0-9_-]+/i.test(pathname);
  }

  if (hostname === "ddownload.com" || hostname.endsWith(".ddownload.com")) {
    if (NON_FILE_PATHS.test(pathname)) return false;
    return /^\/[a-zA-Z0-9]{12,}(\/.*)?$/i.test(pathname);
  }

  if (hostname === "k2s.cc" || hostname === "keep2share.cc") {
    return /^\/file\/[a-zA-Z0-9_-]+/i.test(pathname);
  }

  if (hostname === "katfile.com" || hostname.endsWith(".katfile.com")) {
    if (NON_FILE_PATHS.test(pathname)) return false;
    return /^\/[a-zA-Z0-9]{10,}(\/.*)?$/i.test(pathname);
  }

  if (hostname === "filefactory.com" || hostname.endsWith(".filefactory.com")) {
    return /^\/file\/[a-zA-Z0-9_-]+/i.test(pathname);
  }

  if (hostname === "uptobox.com" || hostname.endsWith(".uptobox.com")) {
    if (NON_FILE_PATHS.test(pathname)) return false;
    return /^\/[a-zA-Z0-9]{12,}/i.test(pathname);
  }

  if (hostname === "dropgalaxy.com" || hostname.endsWith(".dropgalaxy.com")) {
    if (NON_FILE_PATHS.test(pathname)) return false;
    return /^\/(drive\/)?[a-zA-Z0-9_-]{8,}/i.test(pathname);
  }

  if (hostname === "alfafile.net" || hostname.endsWith(".alfafile.net")) {
    return /^\/file\/[a-zA-Z0-9_-]+/i.test(pathname);
  }

  if (hostname.includes("clicknupload")) {
    if (NON_FILE_PATHS.test(pathname)) return false;
    return /^\/[a-zA-Z0-9]{8,}(\/.*)?$/i.test(pathname);
  }

  if (hostname === "filer.net" || hostname.endsWith(".filer.net")) {
    return /^\/get\/[a-zA-Z0-9_-]+/i.test(pathname);
  }

  const isGenericHoster = KNOWN_HOSTERS.some(
    (domain) => hostname === domain || hostname.endsWith("." + domain)
  );
  if (isGenericHoster) {
    if (pathname === "/" || !pathname || NON_FILE_PATHS.test(pathname)) {
      return false;
    }
    const segments = pathname.split("/").filter(Boolean);
    if (segments.length >= 1) {
      const first = segments[0];
      if (/^[a-zA-Z0-9_-]{6,}$/.test(first) && !/\.(html?|php)$/i.test(first)) {
        return true;
      }
    }
  }

  return false;
}

export function cleanExtractedUrl(rawUrl: string | null | undefined): string {
  if (!rawUrl) return "";
  return rawUrl.trim().replace(/[.,;:)\]>!?]+$/, "");
}

export function extractUrlsFromText(text: string | null | undefined): string[] {
  if (!text || typeof text !== "string") return [];

  const unique = new Set<string>();
  const regex = /(?:magnet:\?[^\s"'<>]+|https?:\/\/[^\s"'<>]+)/gi;
  const matches = text.match(regex) || [];

  for (const raw of matches) {
    const cleaned = cleanExtractedUrl(raw);
    if (classifyUrl(cleaned)) {
      unique.add(cleaned);
    }
  }

  const textWithoutUrls = text.replace(regex, " ");
  for (const h of extractInfoHashes(textWithoutUrls)) {
    unique.add(infoHashToMagnet(h));
  }

  return Array.from(unique);
}

export function getOriginPermissionPattern(url: string | null | undefined): string | null {
  if (!url || typeof url !== "string") return null;
  try {
    const parsed = new URL(url.trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    if (!parsed.hostname) {
      return null;
    }
    return `${parsed.protocol}//${parsed.hostname}/*`;
  } catch {
    return null;
  }
}
