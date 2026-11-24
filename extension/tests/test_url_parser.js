const assert = require("assert");
const {
  classifyUrl,
  cleanExtractedUrl,
  extractUrlsFromText,
  isInfoHash,
  infoHashToMagnet,
  extractInfoHashes,
  isDownloadablePageUrl,
  getOriginPermissionPattern,
} = require("../url_parser.js");

console.log("Running url_parser tests...");

// 0. isInfoHash & infoHashToMagnet tests
assert.strictEqual(
  isInfoHash("4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"),
  true,
  "Should recognize 40-char lowercase hex info hash"
);
assert.strictEqual(
  isInfoHash("4A2B3C4D5E6F7A8B9C0D1E2F3A4B5C6D7E8F9A0B"),
  true,
  "Should recognize 40-char uppercase hex info hash"
);
assert.strictEqual(
  isInfoHash("JINWZX3XQ4VMBCLY47A5G7V54Z3V7B4Q"),
  true,
  "Should recognize 32-char base32 info hash"
);
assert.strictEqual(isInfoHash("12345"), false, "Should reject too short hash");
assert.strictEqual(isInfoHash("not-a-hash-at-all"), false, "Should reject non-hash text");
assert.strictEqual(isInfoHash(""), false, "Should reject empty string");
assert.strictEqual(isInfoHash(null), false, "Should reject null");

assert.strictEqual(
  infoHashToMagnet("4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"),
  "magnet:?xt=urn:btih:4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
  "Should format into valid magnet URI"
);

// 1. classifyUrl tests
assert.strictEqual(
  classifyUrl("4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"),
  "magnet",
  "Should classify raw 40-char hex info hash as magnet"
);
assert.strictEqual(
  classifyUrl("JINWZX3XQ4VMBCLY47A5G7V54Z3V7B4Q"),
  "magnet",
  "Should classify raw 32-char base32 info hash as magnet"
);
assert.strictEqual(
  classifyUrl("magnet:?xt=urn:btih:d6b490f89839447385a8286a11e4e4a0ecdb799c&dn=test"),
  "magnet",
  "Should classify valid magnet URI"
);
assert.strictEqual(
  classifyUrl("  MAGNET:?XT=URN:BTIH:12345&DN=UPPERCASE  "),
  "magnet",
  "Should handle case insensitivity and whitespace"
);
assert.strictEqual(classifyUrl("magnet:?dn=invalid_without_xt"), null, "Should reject magnet missing xt=urn:");
assert.strictEqual(classifyUrl("magnet:?"), null, "Should reject empty magnet URI");

assert.strictEqual(classifyUrl("https://1fichier.com/?abcdef1234"), "hoster", "Should classify 1fichier");
assert.strictEqual(classifyUrl("https://rapidgator.net/file/123/game.part1.rar"), "hoster", "Should classify rapidgator");
assert.strictEqual(classifyUrl("https://mega.nz/file/abc#xyz"), "hoster", "Should classify mega.nz");
assert.strictEqual(classifyUrl("https://mediafire.com/file/12345/archive.zip"), "hoster", "Should classify mediafire");
assert.strictEqual(classifyUrl("https://pixeldrain.com/u/abcde"), "hoster", "Should classify pixeldrain");
assert.strictEqual(classifyUrl("https://customcdn.example.org/path/to/archive.7z"), "hoster", "Should classify direct archive extension");

assert.strictEqual(classifyUrl("https://example.com/movie.torrent"), "torrent", "Should classify .torrent link");
assert.strictEqual(classifyUrl("https://google.com"), null, "Should reject regular non-hoster web pages");
assert.strictEqual(classifyUrl("https://en.wikipedia.org/wiki/Debian"), null, "Should reject non-hoster article links");
assert.strictEqual(classifyUrl("not-a-url"), null, "Should reject invalid string");
assert.strictEqual(classifyUrl(""), null, "Should reject empty string");
assert.strictEqual(classifyUrl(null), null, "Should reject null");

// 2. cleanExtractedUrl tests
assert.strictEqual(
  cleanExtractedUrl("https://rapidgator.net/file/123/test.zip.)"),
  "https://rapidgator.net/file/123/test.zip",
  "Should strip trailing .) punctuation"
);
assert.strictEqual(
  cleanExtractedUrl("https://1fichier.com/?abc!"),
  "https://1fichier.com/?abc",
  "Should strip trailing !"
);
assert.strictEqual(cleanExtractedUrl(""), "");

// 3. extractUrlsFromText tests
const sampleText = `
Here are the download links for the game:
1. https://rapidgator.net/file/12345/game.rar,
2. Check out Wikipedia: https://en.wikipedia.org/wiki/Game
3. Alternative: https://1fichier.com/?game123.
4. Magnet: magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=game!
5. Duplicate: https://rapidgator.net/file/12345/game.rar
`;

const extracted = extractUrlsFromText(sampleText);
assert.strictEqual(extracted.length, 3, "Should extract exactly 3 unique valid download links");
assert.ok(extracted.includes("https://rapidgator.net/file/12345/game.rar"));
assert.ok(extracted.includes("https://1fichier.com/?game123"));
assert.ok(extracted.includes("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=game"));
assert.ok(!extracted.some((u) => u.includes("wikipedia.org")), "Should filter out non-hoster URLs");

// 4. Info Hash extraction from text / webpage selections (AudiobookBay format)
const abbSelection = `
Torrent Details:
Title: Tailored Realities by Brandon Sanderson
Info Hash: 4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b
Category: Audiobooks
`;
const extractedFromAbb = extractUrlsFromText(abbSelection);
assert.strictEqual(extractedFromAbb.length, 1, "Should extract exactly 1 magnet from ABB text selection");
assert.strictEqual(
  extractedFromAbb[0],
  "magnet:?xt=urn:btih:4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
  "Should correctly convert highlighted ABB Info Hash into magnet link"
);

// 5. Standalone info hash paste in popup
const rawHashPaste = "  4A2B3C4D5E6F7A8B9C0D1E2F3A4B5C6D7E8F9A0B  ";
const extractedFromPaste = extractUrlsFromText(rawHashPaste);
assert.strictEqual(extractedFromPaste.length, 1, "Should extract 1 magnet from raw pasted hash");
assert.strictEqual(
  extractedFromPaste[0],
  "magnet:?xt=urn:btih:4a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
  "Should normalize uppercase hash to valid magnet"
);

// 6. getOriginPermissionPattern tests
assert.strictEqual(
  getOriginPermissionPattern("http://192.168.1.100:38415"),
  "http://192.168.1.100/*",
  "Should format LAN IP with port into origin match pattern without port"
);
assert.strictEqual(
  getOriginPermissionPattern("http://192.168.1.100:38415/"),
  "http://192.168.1.100/*",
  "Should handle trailing slash"
);
assert.strictEqual(
  getOriginPermissionPattern("https://nas.example.com:8443/settings"),
  "https://nas.example.com/*",
  "Should format HTTPS domain with port and path into scheme://host/* pattern"
);
assert.strictEqual(
  getOriginPermissionPattern("http://localhost:38415"),
  "http://localhost/*",
  "Should format localhost"
);
assert.strictEqual(
  getOriginPermissionPattern("http://[::1]:38415"),
  "http://[::1]/*",
  "Should format IPv6 literal"
);
assert.strictEqual(
  getOriginPermissionPattern("http://server.local:5000"),
  "http://server.local/*",
  "Should format mDNS local hostname"
);
assert.strictEqual(getOriginPermissionPattern("magnet:?xt=urn:btih:1234"), null, "Should return null for magnet");
assert.strictEqual(getOriginPermissionPattern("ftp://ftp.example.com"), null, "Should return null for non-http/https");
assert.strictEqual(getOriginPermissionPattern(""), null, "Should return null for empty string");
assert.strictEqual(getOriginPermissionPattern(null), null, "Should return null for null");
assert.strictEqual(getOriginPermissionPattern("not a url"), null, "Should return null for invalid url string");

// 5. isDownloadablePageUrl tests
// 1fichier
assert.strictEqual(isDownloadablePageUrl("https://1fichier.com/?toqwqbnkdnmg48t5d996"), true, "1fichier file query URL");
assert.strictEqual(isDownloadablePageUrl("https://subdomain.1fichier.com/?toqwqbnkdnmg48t5d996"), true, "1fichier subdomain file query");
assert.strictEqual(isDownloadablePageUrl("https://1fichier.com/"), false, "1fichier homepage should be false");
assert.strictEqual(isDownloadablePageUrl("https://1fichier.com/tarifs.html"), false, "1fichier pricing page should be false");
assert.strictEqual(isDownloadablePageUrl("https://1fichier.com/hlp.html"), false, "1fichier help page should be false");
assert.strictEqual(isDownloadablePageUrl("https://1fichier.com/login.pl"), false, "1fichier login page should be false");

// Rapidgator
assert.strictEqual(isDownloadablePageUrl("https://rapidgator.net/file/8239842834/MyMovie.mkv.html"), true, "Rapidgator file URL");
assert.strictEqual(isDownloadablePageUrl("https://rg.to/file/8239842834"), true, "rg.to short file URL");
assert.strictEqual(isDownloadablePageUrl("https://rapidgator.net/"), false, "Rapidgator homepage should be false");
assert.strictEqual(isDownloadablePageUrl("https://rapidgator.net/login"), false, "Rapidgator login should be false");

// Mega
assert.strictEqual(isDownloadablePageUrl("https://mega.nz/file/ABC12345#KeyXYZ123"), true, "Mega /file/ URL");
assert.strictEqual(isDownloadablePageUrl("https://mega.nz/#!ABC12345!KeyXYZ123"), true, "Mega legacy #! URL");
assert.strictEqual(isDownloadablePageUrl("https://mega.co.nz/#!ABC12345!KeyXYZ123"), true, "Mega.co.nz URL");
assert.strictEqual(isDownloadablePageUrl("https://mega.nz/"), false, "Mega homepage should be false");

// Mediafire
assert.strictEqual(isDownloadablePageUrl("https://mediafire.com/file/1234567890/test.zip/file"), true, "Mediafire /file/ URL");
assert.strictEqual(isDownloadablePageUrl("https://mediafire.com/?1234567890"), true, "Mediafire /?id URL");
assert.strictEqual(isDownloadablePageUrl("https://mediafire.com/upgrade"), false, "Mediafire upgrade should be false");
assert.strictEqual(isDownloadablePageUrl("https://mediafire.com/"), false, "Mediafire homepage should be false");

// Pixeldrain
assert.strictEqual(isDownloadablePageUrl("https://pixeldrain.com/u/abc12345"), true, "Pixeldrain /u/ file URL");
assert.strictEqual(isDownloadablePageUrl("https://pixeldrain.com/l/abc12345"), true, "Pixeldrain /l/ list URL");
assert.strictEqual(isDownloadablePageUrl("https://pixeldrain.com/"), false, "Pixeldrain homepage should be false");

// Turbobit & Nitroflare & DDownload & K2s & Katfile
assert.strictEqual(isDownloadablePageUrl("https://turbobit.net/abc123456789.html"), true, "Turbobit file URL");
assert.strictEqual(isDownloadablePageUrl("https://turbobit.net/download/free/abc123456789"), true, "Turbobit free download URL");
assert.strictEqual(isDownloadablePageUrl("https://turbobit.net/"), false, "Turbobit homepage should be false");
assert.strictEqual(isDownloadablePageUrl("https://nitroflare.com/view/ABC1234567"), true, "Nitroflare view URL");
assert.strictEqual(isDownloadablePageUrl("https://nitroflare.com/"), false, "Nitroflare homepage should be false");
assert.strictEqual(isDownloadablePageUrl("https://ddownload.com/abc123456789/movie.mkv"), true, "DDownload file URL");
assert.strictEqual(isDownloadablePageUrl("https://k2s.cc/file/abc12345"), true, "K2S file URL");
assert.strictEqual(isDownloadablePageUrl("https://katfile.com/abc123456789"), true, "Katfile file URL");

// Direct file extensions
assert.strictEqual(isDownloadablePageUrl("https://example.com/downloads/movie.mkv"), true, "Direct .mkv URL");
assert.strictEqual(isDownloadablePageUrl("https://example.com/archive.part1.rar"), true, "Direct .rar URL");
assert.strictEqual(isDownloadablePageUrl("https://example.com/game.nsp"), true, "Direct .nsp URL");
assert.strictEqual(isDownloadablePageUrl("https://example.com/file.torrent"), true, "Direct .torrent URL");

// Negative non-file URLs
assert.strictEqual(isDownloadablePageUrl("https://google.com"), false, "Google should be false");
assert.strictEqual(isDownloadablePageUrl("https://example.com/index.html"), false, "Generic index.html should be false");
assert.strictEqual(isDownloadablePageUrl("https://example.com/articles/news"), false, "Generic news page should be false");
assert.strictEqual(isDownloadablePageUrl("magnet:?xt=urn:btih:12345"), false, "Magnet URI should be false for page URL");
assert.strictEqual(isDownloadablePageUrl(""), false, "Empty string should be false");
assert.strictEqual(isDownloadablePageUrl(null), false, "Null should be false");

console.log("✅ All url_parser tests passed!");
