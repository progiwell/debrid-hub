const assert = require("assert");
const fs = require("fs");
const path = require("path");

console.log("Running popup_filter tests...");

const ACTIVE_STATUSES = ["queued", "resolving_debrid", "downloading", "extracting"];
const MAX_RECENT_COMPLETED = 5;

// Mock job generator
function makeJob(id, status, client_id = "test-client", createdOffsetMs = 0) {
  return {
    job_id: id,
    status,
    client_id,
    created_at: new Date(Date.now() - createdOffsetMs).toISOString(),
    filenames: [`file-${id}.mkv`],
  };
}

function sortJobsNewestFirst(jobs) {
  return jobs.slice().sort((a, b) => {
    const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
    const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
    return tb - ta;
  });
}

function filterQueueJobs(jobs, { scope, clientId, statusFilter, maxCompleted = MAX_RECENT_COMPLETED }) {
  const scopedJobs = Array.isArray(jobs)
    ? scope === "all"
      ? jobs
      : jobs.filter((j) => j.client_id && j.client_id === clientId)
    : [];

  const activeCount = scopedJobs.filter((j) => ACTIVE_STATUSES.includes(j.status)).length;
  const completedCount = scopedJobs.filter((j) => j.status === "completed").length;
  const failedCount = scopedJobs.filter((j) => j.status === "failed").length;

  const sorted = sortJobsNewestFirst(scopedJobs);
  let visibleJobs = [];
  let emptyState = null;

  if (scopedJobs.length === 0) {
    emptyState =
      scope === "browser" && jobs.length > 0
        ? "No jobs from this browser yet.<br/>Switch to <b>All Jobs</b> to view *arr or other devices."
        : "No active or recent jobs found.";
    return { visibleJobs: [], activeCount, completedCount, failedCount, emptyState, capNote: false };
  }

  if (statusFilter === "active") {
    visibleJobs = sorted.filter((j) => ACTIVE_STATUSES.includes(j.status));
    if (visibleJobs.length === 0) {
      emptyState = "⚡ No active jobs running.";
    }
    return { visibleJobs, activeCount, completedCount, failedCount, emptyState, capNote: false };
  } else if (statusFilter === "completed") {
    visibleJobs = sorted.filter((j) => j.status === "completed");
    if (visibleJobs.length === 0) {
      emptyState = "✅ No completed jobs found.";
    }
    return { visibleJobs, activeCount, completedCount, failedCount, emptyState, capNote: false };
  } else if (statusFilter === "failed") {
    visibleJobs = sorted.filter((j) => j.status === "failed");
    if (visibleJobs.length === 0) {
      emptyState = "⚠️ No failed jobs found.";
    }
    return { visibleJobs, activeCount, completedCount, failedCount, emptyState, capNote: false };
  } else {
    let completedShown = 0;
    visibleJobs = sorted.filter((j) => {
      if (j.status === "completed") {
        if (completedShown >= maxCompleted) return false;
        completedShown++;
      }
      return true;
    });
    return {
      visibleJobs,
      activeCount,
      completedCount,
      failedCount,
      emptyState: null,
      capNote: completedCount > maxCompleted,
    };
  }
}

// 1. Test Active Filter
const sampleJobs = [
  makeJob("1", "queued", "client-a", 1000),
  makeJob("2", "downloading", "client-a", 2000),
  makeJob("3", "extracting", "client-a", 3000),
  makeJob("4", "completed", "client-a", 4000),
  makeJob("5", "completed", "client-a", 5000),
  makeJob("6", "failed", "client-a", 6000),
  makeJob("7", "downloading", "client-b", 7000),
];

const activeBrowserResult = filterQueueJobs(sampleJobs, {
  scope: "browser",
  clientId: "client-a",
  statusFilter: "active",
});
assert.strictEqual(activeBrowserResult.visibleJobs.length, 3, "Should show 3 active jobs for client-a");
assert.deepStrictEqual(
  activeBrowserResult.visibleJobs.map((j) => j.job_id),
  ["1", "2", "3"]
);
assert.strictEqual(activeBrowserResult.emptyState, null);

// 2. Test Completed Filter
const completedBrowserResult = filterQueueJobs(sampleJobs, {
  scope: "browser",
  clientId: "client-a",
  statusFilter: "completed",
});
assert.strictEqual(completedBrowserResult.visibleJobs.length, 2, "Should show 2 completed jobs");
assert.deepStrictEqual(
  completedBrowserResult.visibleJobs.map((j) => j.job_id),
  ["4", "5"]
);

// 3. Test Failed Filter
const failedBrowserResult = filterQueueJobs(sampleJobs, {
  scope: "browser",
  clientId: "client-a",
  statusFilter: "failed",
});
assert.strictEqual(failedBrowserResult.visibleJobs.length, 1, "Should show 1 failed job");
assert.strictEqual(failedBrowserResult.visibleJobs[0].job_id, "6");

// 4. Test Toggle-Off / Default View (capped completed at 5)
const manyCompletedJobs = [
  makeJob("act-1", "downloading", "client-a", 100),
  ...Array.from({ length: 8 }, (_, i) => makeJob(`comp-${i}`, "completed", "client-a", 1000 + i * 100)),
];
const defaultViewResult = filterQueueJobs(manyCompletedJobs, {
  scope: "browser",
  clientId: "client-a",
  statusFilter: null,
});
assert.strictEqual(defaultViewResult.visibleJobs.length, 6, "1 active + 5 capped completed");
assert.strictEqual(defaultViewResult.capNote, true, "Cap note should be visible when completed > 5");
assert.strictEqual(defaultViewResult.completedCount, 8, "Completed count should reflect total");

// 5. Test Contextual Empty States
const noActiveJobs = [makeJob("comp-1", "completed", "client-a", 100)];
const emptyActiveResult = filterQueueJobs(noActiveJobs, {
  scope: "browser",
  clientId: "client-a",
  statusFilter: "active",
});
assert.strictEqual(emptyActiveResult.visibleJobs.length, 0);
assert.ok(emptyActiveResult.emptyState.includes("No active jobs running"));

const noFailedJobs = [makeJob("act-1", "downloading", "client-a", 100)];
const emptyFailedResult = filterQueueJobs(noFailedJobs, {
  scope: "browser",
  clientId: "client-a",
  statusFilter: "failed",
});
assert.strictEqual(emptyFailedResult.visibleJobs.length, 0);
assert.ok(emptyFailedResult.emptyState.includes("No failed jobs"));

// 6. Test Scope Filtering (All Jobs vs This Browser)
const allJobsActiveResult = filterQueueJobs(sampleJobs, {
  scope: "all",
  clientId: "client-a",
  statusFilter: "active",
});
assert.strictEqual(allJobsActiveResult.visibleJobs.length, 4, "Should include client-b active job when scope is all");

// 7. Verify popup.html & popup.js DOM Contracts
const EXT_DIR = path.resolve(__dirname, "..");
const popupHtmlPath = path.join(EXT_DIR, "popup.html");
const popupHtml = fs.readFileSync(popupHtmlPath, "utf8");

assert.ok(popupHtml.includes('id="scopeAllBtn"'), "scopeAllBtn exists");
assert.ok(popupHtml.includes(">🌐 All Jobs<"), "scopeAllBtn must read '🌐 All Jobs'");
assert.ok(!popupHtml.includes(">🌐 All Server<"), "scopeAllBtn must not read '🌐 All Server'");
assert.ok(popupHtml.includes('id="filterActiveBtn"'), "filterActiveBtn exists");
assert.ok(popupHtml.includes('id="filterCompletedBtn"'), "filterCompletedBtn exists");
assert.ok(popupHtml.includes('id="filterFailedBtn"'), "filterFailedBtn exists");
assert.ok(popupHtml.includes('class="summary-pill"'), "summary-pill class exists");

assert.ok(popupHtml.includes('href="popup.css"'), "popup.html links external popup.css");
assert.ok(!popupHtml.includes("<style>"), "popup.html must not contain inline <style> block");
assert.ok(popupHtml.split("\n").length < 250, "popup.html remains concise semantic HTML");

const popupCssPath = path.join(EXT_DIR, "popup.css");
const popupCss = fs.readFileSync(popupCssPath, "utf8");
assert.ok(popupCss.includes(".summary-pill"), "popup.css contains .summary-pill styling");

assert.ok(popupHtml.includes('id="activePageBanner"'), "activePageBanner exists in popup.html");
assert.ok(popupHtml.includes('id="downloadActivePageBtn"'), "downloadActivePageBtn exists in popup.html");
assert.ok(popupHtml.includes('id="serverEndpoint"'), "serverEndpoint input exists in popup.html");
assert.ok(popupHtml.includes('id="openServerSettingsBtn"'), "openServerSettingsBtn exists in popup.html");

const manifestPath = path.join(EXT_DIR, "manifest.json");
const manifestJson = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
assert.ok(manifestJson.permissions.includes("activeTab"), "manifest.json includes activeTab permission");
assert.ok(manifestJson.background.service_worker, "Chrome manifest must have service_worker");
assert.strictEqual(manifestJson.background.scripts, undefined, "Chrome manifest must NOT have scripts");

// Verify Firefox manifest and non-symlink assets
const firefoxManifestPath = path.join(EXT_DIR, "firefox", "manifest.json");
const firefoxManifestJson = JSON.parse(fs.readFileSync(firefoxManifestPath, "utf8"));
assert.ok(Array.isArray(firefoxManifestJson.background.scripts), "Firefox manifest must have scripts array");
assert.strictEqual(firefoxManifestJson.background.service_worker, undefined, "Firefox manifest must NOT have service_worker");
assert.ok(firefoxManifestJson.browser_specific_settings.gecko.id, "Firefox manifest must have gecko ID");
const firefoxCsp = firefoxManifestJson.content_security_policy?.extension_pages || "";
assert.ok(
  firefoxCsp.includes("script-src 'self'"),
  "Firefox manifest must define content_security_policy.extension_pages to prevent MV3 upgrade-insecure-requests"
);
assert.ok(
  !firefoxCsp.includes("upgrade-insecure-requests"),
  "Firefox manifest CSP must NOT include upgrade-insecure-requests"
);

const FIREFOX_SHARED_FILES = [
  "background.js",
  "content.js",
  "popup.css",
  "popup.html",
  "popup.js",
  "url_parser.js",
];
for (const fname of FIREFOX_SHARED_FILES) {
  const ffAssetPath = path.join(EXT_DIR, "firefox", fname);
  assert.ok(fs.existsSync(ffAssetPath), `Firefox asset ${fname} must exist`);
  const stat = fs.lstatSync(ffAssetPath);
  assert.strictEqual(
    stat.isSymbolicLink(),
    false,
    `Firefox asset ${fname} must not be a symlink (Firefox blocks parent-escaping symlinks)`
  );
  assert.strictEqual(stat.isFile(), true, `Firefox asset ${fname} must be a regular file`);
}

const popupJsPath = path.join(EXT_DIR, "popup.js");
const popupJs = fs.readFileSync(popupJsPath, "utf8");
assert.ok(popupJs.includes("Switch to <b>All Jobs</b>"), "popup.js empty state refers to All Jobs");
assert.ok(!popupJs.includes("Switch to <b>All Server</b>"), "popup.js empty state must not refer to All Server");
assert.ok(popupJs.includes("currentStatusFilter"), "popup.js tracks currentStatusFilter");
assert.ok(popupJs.includes("setStatusFilter"), "popup.js has setStatusFilter function");
assert.ok(popupJs.includes("checkActiveTabFilePage"), "popup.js checks active tab for downloadable page");
assert.ok(popupJs.includes("serverEndpointInput"), "popup.js uses serverEndpointInput");
assert.ok(popupJs.includes("openServerSettingsBtn"), "popup.js uses openServerSettingsBtn");
assert.ok(popupJs.includes("checkMissingFirefoxPermission"), "popup.js uses shared checkMissingFirefoxPermission helper");
assert.ok(popupJs.includes("promptFirefoxPermissionAction"), "popup.js uses shared promptFirefoxPermissionAction helper");

const backgroundJsPath = path.join(EXT_DIR, "background.js");
const backgroundJs = fs.readFileSync(backgroundJsPath, "utf8");
assert.ok(backgroundJs.includes('"download-to-server"'), "background.js uses download-to-server menu id");
assert.ok(backgroundJs.includes('"pollServerStatus"'), "background.js uses pollServerStatus alarm name");

const contentJsPath = path.join(EXT_DIR, "content.js");
const contentJs = fs.readFileSync(contentJsPath, "utf8");
assert.ok(contentJs.includes('"debrid-hub-toast"'), "content.js uses debrid-hub-toast id");

console.log("All popup_filter unit tests passed cleanly!");
