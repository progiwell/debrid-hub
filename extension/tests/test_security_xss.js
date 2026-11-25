const assert = require("assert");
const path = require("path");
const vm = require("vm");
const esbuild = require("esbuild");

console.log("Running XSS security tests for extension and dashboard renderers...");

const REPO_ROOT = path.resolve(__dirname, "..", "..");

function loadTsModule(entryRelPath, globalName, extraGlobals = {}) {
  const result = esbuild.buildSync({
    entryPoints: [path.join(REPO_ROOT, entryRelPath)],
    bundle: true,
    write: false,
    format: "iife",
    globalName,
    platform: "browser",
    target: "es2022",
  });
  const code = result.outputFiles[0].text;
  const noop = () => {};
  const stubEl = () => ({
    classList: { toggle: noop, add: noop, remove: noop },
    addEventListener: noop,
    setAttribute: noop,
    style: {},
    value: "",
    textContent: "",
    innerHTML: "",
    dataset: {},
  });
  const context = vm.createContext({
    console,
    Date,
    Math,
    Number,
    String,
    Array,
    Object,
    JSON,
    URL,
    URLSearchParams,
    encodeURIComponent,
    decodeURIComponent,
    setTimeout: noop,
    alert: noop,
    confirm: () => false,
    EventSource: undefined,
    window: { location: { hash: "", pathname: "/" } },
    document: {
      querySelectorAll: () => [],
      getElementById: () => stubEl(),
      createElement: () => stubEl(),
    },
    ...extraGlobals,
  });
  vm.runInContext(code, context);
  return context[globalName];
}

const queueUi = loadTsModule("extension/src/queue_ui.ts", "__QueueUiExports");
const dashboard = loadTsModule("server/app/static/src/dashboard.ts", "__DashboardExports");

const xssPayloads = [
  `<img src=x onerror=alert(1)>`,
  `<script>alert('xss')</script>`,
  `" onmouseover="alert(1)`,
  `' onclick='alert(1)`,
  `<svg/onload=alert(1)>`,
];

for (const payload of xssPayloads) {
  const escapedQueue = queueUi.escapeHtml(payload);
  const escapedDash = dashboard.escapeHtml(payload);
  for (const escaped of [escapedQueue, escapedDash]) {
    assert.ok(!escaped.includes("<"), `Expected '<' to be escaped in: ${escaped}`);
    assert.ok(!escaped.includes(">"), `Expected '>' to be escaped in: ${escaped}`);
    assert.ok(!escaped.includes('"'), `Expected '"' to be escaped in: ${escaped}`);
    assert.ok(!escaped.includes("'"), `Expected "'" to be escaped in: ${escaped}`);
  }
}

const maliciousJob = {
  job_id: `id" onclick="alert(1)`,
  source_url: `magnet:?xt=urn:btih:1111111111111111111111111111111111111111&dn=${encodeURIComponent('<img src=x onerror=alert(1)>')}`,
  link_type: "magnet",
  status: "failed",
  output_dir: `/downloads/<script>alert(1)</script>`,
  filenames: [`<img src=x onerror=alert(1)>.mkv`],
  extracted_files: [`<svg onload=alert(1)>.mp4`],
  total_files: 1,
  completed_files: 0,
  progress_percent: 50,
  download_speed: `1MB/s" onmouseover="alert(1)`,
  error: `<script>alert('err')</script>`,
  category: `<img src=x onerror=alert(1)>`,
  source: `<img src=x onerror=alert(1)>`,
  client_name: `' onclick='alert(1)`,
  created_at: new Date().toISOString(),
};

const queueHtml = queueUi.renderJobCardHtml(maliciousJob, "all");
const dashHtml = dashboard.renderJobCardHtml(maliciousJob);

for (const [label, html] of [
  ["queue_ui", queueHtml],
  ["dashboard", dashHtml],
]) {
  assert.ok(!html.includes("<img"), `${label} rendered unescaped <img tag`);
  assert.ok(!html.includes("<script"), `${label} rendered unescaped <script tag`);
  assert.ok(!html.includes("<svg"), `${label} rendered unescaped <svg tag`);
  assert.ok(!/on(error|load|mouseover|click)=["']/i.test(html), `${label} rendered unescaped event handler attribute`);
  assert.ok(html.includes("&lt;img"), `${label} should HTML-escape <img payload`);
}

console.log("All XSS security tests passed cleanly!");
