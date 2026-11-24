const assert = require("assert");
const {
  extractCnlHrefUrls,
  isCnlTarget,
  extractHexKey,
} = require("../content.js");

console.log("Running content tests...");

// 1. Single URL-encoded magnet URI in FlashGot href
const magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=DebridHub-CNL-Test";
const href1 = `http://127.0.0.1:9666/flashgot?urls=${encodeURIComponent(magnet)}`;
assert.deepStrictEqual(
  extractCnlHrefUrls(href1, "http://127.0.0.1:8765/"),
  [magnet],
  "Should extract single encoded magnet link from urls param"
);

// 2. Multiple newline/space separated links across urls + link params, deduplicated
const linkA = "https://1fichier.com/?abc123456";
const linkB = "https://rapidgator.net/file/999/archive.rar";
const href2 =
  "http://127.0.0.1:9666/flashgot?" +
  `urls=${encodeURIComponent(`${linkA}\n${linkB}`)}` +
  `&downloadLink=${encodeURIComponent(`${linkB} ${magnet}`)}` +
  "&source=test&ignored=ftp://not-http.example/file";
assert.deepStrictEqual(
  extractCnlHrefUrls(href2, "http://127.0.0.1:8765/"),
  [linkA, linkB, magnet],
  "Should extract and deduplicate multiple http/https/magnet URLs from url/link params"
);

// 3. Survives malformed percent-encoding in an unrelated parameter and ignores non-url params
const hrefMalformed =
  "http://127.0.0.1:9666/flashgot?bad=%ZZinvalid&token=abc&urls=" + encodeURIComponent(linkA);
assert.deepStrictEqual(
  extractCnlHrefUrls(hrefMalformed, "http://127.0.0.1:8765/"),
  [linkA],
  "Should skip malformed percent-encoding pair and still extract valid urls param"
);

// 4. Relative path & empty/invalid inputs
assert.deepStrictEqual(
  extractCnlHrefUrls("/flashgot?link=https%3A%2F%2Fmega.nz%2Ffile%2Fxyz", "http://127.0.0.1:9666/"),
  ["https://mega.nz/file/xyz"],
  "Should resolve relative href against baseHref"
);
assert.deepStrictEqual(extractCnlHrefUrls("", "http://127.0.0.1:8765/"), []);
assert.deepStrictEqual(extractCnlHrefUrls("http://[invalid", "not-a-base"), []);

// 5. isCnlTarget & extractHexKey sanity checks
assert.strictEqual(isCnlTarget("http://127.0.0.1:9666/flashgot?urls=x"), true);
assert.strictEqual(isCnlTarget("https://example.com/file"), false);
assert.strictEqual(
  extractHexKey("function f(){ return '0123456789abcdef0123456789abcdef';}"),
  "0123456789abcdef0123456789abcdef"
);

console.log("✅ All content tests passed!");
