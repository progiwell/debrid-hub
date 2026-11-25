#!/usr/bin/env python3
"""Build, sync, and packaging helper for Debrid Hub Browser Extension."""

import argparse
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

EXT_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXT_DIR.parent
FIREFOX_DIR = EXT_DIR / "firefox"

SHARED_FILES = [
    "background.js",
    "content.js",
    "popup.css",
    "popup.html",
    "popup.js",
    "url_parser.js",
]


def _resolve_npm() -> str:
    """Locate npm on PATH or in standard Homebrew Node@22/Node paths."""
    npm_bin = shutil.which("npm")
    if npm_bin:
        return npm_bin
    for candidate in (
        "/opt/homebrew/opt/node@22/bin/npm",
        "/opt/homebrew/bin/npm",
        "/usr/local/bin/npm",
    ):
        if Path(candidate).is_file():
            return candidate
    raise FileNotFoundError(
        "npm executable not found. Install Node.js (v22+) and run `npm ci` in the repo root."
    )


def build_frontend():
    """Compile TypeScript and Tailwind CSS bundles for the browser extension."""
    npm_bin = _resolve_npm()
    env = os.environ.copy()
    npm_dir = str(Path(npm_bin).parent)
    if npm_dir not in env.get("PATH", ""):
        env["PATH"] = f"{npm_dir}:{env.get('PATH', '')}"

    result = subprocess.run(
        [npm_bin, "run", "build:ext"],
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"npm run build:ext failed with exit code {result.returncode}")
    print("✓ Compiled TypeScript and Tailwind CSS extension bundles.")


def sync_firefox_dir():
    """Copy shared and compiled assets as regular files into extension/firefox/.

    Firefox blocks moz-extension:// loads for symlinks that resolve outside the
    unpacked add-on root directory, so every asset must be a regular file copy.
    """
    FIREFOX_DIR.mkdir(parents=True, exist_ok=True)
    for fname in SHARED_FILES:
        src = EXT_DIR / fname
        if not src.exists():
            raise FileNotFoundError(f"Missing compiled extension asset: {src}")
        target = FIREFOX_DIR / fname
        if target.is_symlink() or target.exists():
            target.unlink()
        shutil.copy2(src, target)
    print("✓ Synced extension/firefox/ assets successfully.")


def validate_manifests():
    """Verify both Chrome and Firefox manifests and assets for compliance."""
    chrome_manifest_path = EXT_DIR / "manifest.json"
    firefox_manifest_path = FIREFOX_DIR / "manifest.json"

    assert chrome_manifest_path.exists(), "Chrome manifest.json missing"
    assert firefox_manifest_path.exists(), "Firefox manifest.json missing"

    with open(chrome_manifest_path, "r", encoding="utf-8") as f:
        chrome_data = json.load(f)
    with open(firefox_manifest_path, "r", encoding="utf-8") as f:
        firefox_data = json.load(f)

    # Security permission & CSP audit
    allowed_permissions = {"contextMenus", "storage", "notifications", "alarms", "activeTab"}
    allowed_host_permissions = {"<all_urls>"}
    for label, manifest_data in (("Chrome", chrome_data), ("Firefox", firefox_data)):
        assert manifest_data.get("manifest_version") == 3, f"{label} manifest must target MV3"
        perms = set(manifest_data.get("permissions", []))
        unexpected_perms = perms - allowed_permissions
        assert not unexpected_perms, f"{label} manifest has unexpected permissions: {sorted(unexpected_perms)}"
        host_perms = set(manifest_data.get("host_permissions", []))
        unexpected_hosts = host_perms - allowed_host_permissions
        assert not unexpected_hosts, f"{label} manifest has unexpected host_permissions: {sorted(unexpected_hosts)}"
        assert "externally_connectable" not in manifest_data, (
            f"{label} manifest must NOT expose externally_connectable"
        )
        csp_val = manifest_data.get("content_security_policy", {}).get("extension_pages", "")
        assert "unsafe-eval" not in csp_val and "unsafe-inline" not in csp_val, (
            f"{label} manifest CSP must NOT allow unsafe-eval or unsafe-inline"
        )

    # Chrome requirements: strictly service_worker, no scripts
    chrome_bg = chrome_data.get("background", {})
    assert "service_worker" in chrome_bg, "Chrome manifest must use service_worker"
    assert "scripts" not in chrome_bg, "Chrome manifest must NOT include scripts (violates MV3)"

    # Firefox requirements: strictly scripts, no service_worker, gecko ID configured
    firefox_bg = firefox_data.get("background", {})
    assert "scripts" in firefox_bg, "Firefox manifest must use scripts"
    assert "service_worker" not in firefox_bg, "Firefox manifest must NOT use service_worker (disabled in Firefox)"
    assert "gecko" in firefox_data.get("browser_specific_settings", {}), "Firefox manifest must include gecko ID"

    firefox_csp = firefox_data.get("content_security_policy", {}).get("extension_pages", "")
    assert "script-src 'self'" in firefox_csp, (
        "Firefox manifest must define content_security_policy.extension_pages to prevent "
        "Firefox MV3 default CSP from upgrading LAN/Tailscale HTTP requests to HTTPS"
    )
    assert "upgrade-insecure-requests" not in firefox_csp, (
        "Firefox manifest CSP must NOT include upgrade-insecure-requests (breaks plain HTTP LAN/Tailscale endpoints)"
    )

    for fname in SHARED_FILES:
        ff_asset = FIREFOX_DIR / fname
        assert ff_asset.exists(), f"Missing Firefox extension asset: {ff_asset}"
        assert not ff_asset.is_symlink(), (
            f"Firefox extension asset must be a copied file, not a symlink "
            f"(Firefox blocks parent-escaping symlinks): {ff_asset}"
        )
        assert ff_asset.is_file(), f"Firefox extension asset is not a regular file: {ff_asset}"

    print("✓ Validated Chrome and Firefox manifests: 100% compliant.")


def package_extensions(output_dir=None):
    """Package zip and xpi files into dist/."""
    out_dir = Path(output_dir) if output_dir else (REPO_ROOT / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Package Chrome ZIP
    chrome_zip = out_dir / "debrid-hub-chrome.zip"
    with zipfile.ZipFile(chrome_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(EXT_DIR / "manifest.json", "manifest.json")
        for fname in SHARED_FILES:
            zf.write(EXT_DIR / fname, fname)
    print(f"✓ Packaged Chrome extension: {chrome_zip}")

    # 2. Package Firefox XPI
    firefox_xpi = out_dir / "debrid-hub-firefox.xpi"
    with zipfile.ZipFile(firefox_xpi, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(FIREFOX_DIR / "manifest.json", "manifest.json")
        for fname in SHARED_FILES:
            zf.write(EXT_DIR / fname, fname)
    print(f"✓ Packaged Firefox add-on: {firefox_xpi}")


def main():
    parser = argparse.ArgumentParser(description="Extension build and sync helper")
    parser.add_argument("--sync", action="store_true", help="Sync Firefox assets")
    parser.add_argument("--package", action="store_true", help="Package release zip/xpi into dist/")
    parser.add_argument("--validate", action="store_true", help="Validate manifests")
    parser.add_argument("--no-build", action="store_true", help="Skip TypeScript/Tailwind compilation step")
    args = parser.parse_args()

    if not args.no_build:
        build_frontend()
    sync_firefox_dir()
    validate_manifests()
    if args.package:
        package_extensions()


if __name__ == "__main__":
    main()
