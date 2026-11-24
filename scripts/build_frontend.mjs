import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as esbuild from "esbuild";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");

const args = process.argv.slice(2);
const watchMode = args.includes("--watch");
const targetArg = args.find((a) => a.startsWith("--target="))?.split("=")[1] || "all";

const FIREFOX_SHARED_FILES = [
  "background.js",
  "content.js",
  "popup.css",
  "popup.html",
  "popup.js",
  "url_parser.js",
];

/** Copy a single extension asset into extension/firefox/, replacing any symlink. */
function copyFirefoxAsset(fname) {
  const extDir = path.join(REPO_ROOT, "extension");
  const firefoxDir = path.join(extDir, "firefox");
  const src = path.join(extDir, fname);
  if (!fs.existsSync(firefoxDir) || !fs.existsSync(src)) return;

  const target = path.join(firefoxDir, fname);
  try {
    const stat = fs.lstatSync(target);
    if (stat.isSymbolicLink()) {
      fs.unlinkSync(target);
    }
  } catch {
    // Target does not exist yet
  }
  fs.copyFileSync(src, target);
}

/** Sync all shared/compiled extension assets into extension/firefox/. */
function syncFirefoxAssets() {
  for (const fname of FIREFOX_SHARED_FILES) {
    copyFirefoxAsset(fname);
  }
}

/** Sync an individual esbuild output into extension/firefox/ after incremental rebuilds. */
function syncFirefoxWatchPlugin(outfile) {
  return {
    name: "sync-firefox-watch",
    setup(build) {
      build.onEnd((result) => {
        if (result.errors.length === 0) {
          copyFirefoxAsset(path.basename(outfile));
        }
      });
    },
  };
}

function runTailwind(inputRel, outputRel) {
  const inputPath = path.join(REPO_ROOT, inputRel);
  const outputPath = path.join(REPO_ROOT, outputRel);
  if (!fs.existsSync(inputPath)) return;

  const tailwindBin = path.join(REPO_ROOT, "node_modules", ".bin", "tailwindcss");
  const res = spawnSync(
    tailwindBin,
    ["-i", inputPath, "-o", outputPath, "--minify"],
    { cwd: REPO_ROOT, stdio: "inherit" }
  );
  if (res.status !== 0) {
    throw new Error(`Tailwind build failed for ${inputRel}`);
  }
}

function getExtensionEntries() {
  const extSrc = path.join(REPO_ROOT, "extension", "src");
  if (!fs.existsSync(extSrc)) return [];

  const entries = [
    {
      entryPoints: [path.join(extSrc, "url_parser.ts")],
      outfile: path.join(REPO_ROOT, "extension", "url_parser.js"),
      bundle: true,
      format: "iife",
      globalName: "__UrlParserExports",
      target: "es2022",
      platform: "neutral",
      mainFields: ["browser", "module", "main"],
      footer: {
        js: [
          'if (typeof module !== "undefined" && module.exports) { module.exports = __UrlParserExports; }',
          'if (typeof globalThis !== "undefined") { Object.assign(globalThis, __UrlParserExports); }',
        ].join("\n"),
      },
    },
    {
      entryPoints: [path.join(extSrc, "content.ts")],
      outfile: path.join(REPO_ROOT, "extension", "content.js"),
      bundle: true,
      format: "iife",
      globalName: "__ContentExports",
      target: "es2022",
      platform: "browser",
      footer: {
        js: 'if (typeof module !== "undefined" && module.exports) { module.exports = __ContentExports; }',
      },
    },
    {
      entryPoints: [path.join(extSrc, "background.ts")],
      outfile: path.join(REPO_ROOT, "extension", "background.js"),
      bundle: true,
      format: "iife",
      target: "es2022",
      platform: "browser",
    },
    {
      entryPoints: [path.join(extSrc, "popup.ts")],
      outfile: path.join(REPO_ROOT, "extension", "popup.js"),
      bundle: true,
      format: "iife",
      target: "es2022",
      platform: "browser",
    },
  ];

  if (watchMode) {
    return entries.map((cfg) => ({
      ...cfg,
      plugins: [syncFirefoxWatchPlugin(cfg.outfile)],
    }));
  }
  return entries;
}

function getServerEntries() {
  const serverSrc = path.join(REPO_ROOT, "server", "app", "static", "src");
  if (!fs.existsSync(serverSrc)) return [];

  return [
    {
      entryPoints: [path.join(serverSrc, "dashboard.ts")],
      outfile: path.join(REPO_ROOT, "server", "app", "static", "dashboard.js"),
      bundle: true,
      format: "iife",
      target: "es2022",
      platform: "browser",
    },
  ];
}

async function buildAll() {
  const buildConfigs = [];
  if (targetArg === "all" || targetArg === "ext") {
    buildConfigs.push(...getExtensionEntries());
  }
  if (targetArg === "all" || targetArg === "server") {
    buildConfigs.push(...getServerEntries());
  }

  if (watchMode) {
    const contexts = await Promise.all(buildConfigs.map((cfg) => esbuild.context(cfg)));
    await Promise.all(contexts.map((ctx) => ctx.watch()));
    if (targetArg === "all" || targetArg === "ext") {
      runTailwind("extension/src/styles.css", "extension/popup.css");
      syncFirefoxAssets();
    }
    if (targetArg === "all" || targetArg === "server") {
      runTailwind("server/app/static/src/styles.css", "server/app/static/dashboard.css");
    }
    console.log("[build_frontend] Watching TypeScript bundles...");
  } else {
    await Promise.all(buildConfigs.map((cfg) => esbuild.build(cfg)));
    if (targetArg === "all" || targetArg === "ext") {
      runTailwind("extension/src/styles.css", "extension/popup.css");
      syncFirefoxAssets();
    }
    if (targetArg === "all" || targetArg === "server") {
      runTailwind("server/app/static/src/styles.css", "server/app/static/dashboard.css");
    }
    console.log("[build_frontend] Build complete.");
  }
}

buildAll().catch((err) => {
  console.error("[build_frontend] Error:", err);
  process.exit(1);
});
