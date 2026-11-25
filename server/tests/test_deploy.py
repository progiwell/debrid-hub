"""Unit and integration tests for deployment scripts and graceful container shutdown.

Verifies that deploy.sh and docker-entrypoint.sh properly shut down containers with
exit code 0, preventing false-positive container manager abnormal exit notifications.
"""

import http.client
import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVER_DIR = Path(__file__).resolve().parent.parent


def test_deploy_script_exists_and_executable():
    """Verify deploy.sh exists, has executable permissions, and has valid bash syntax."""
    deploy_sh = SERVER_DIR / "deploy.sh"
    assert deploy_sh.is_file(), "deploy.sh must exist in server/"

    # Check executable bit
    mode = deploy_sh.stat().st_mode
    assert bool(mode & stat.S_IXUSR), "deploy.sh must be executable by user"

    # Verify bash syntax
    result = subprocess.run(["bash", "-n", str(deploy_sh)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, f"Syntax error in deploy.sh: {result.stderr}"


def test_deploy_script_contains_graceful_stop_sequence():
    """Verify deploy.sh invokes stop before pull and up."""
    deploy_sh = SERVER_DIR / "deploy.sh"
    content = deploy_sh.read_text(encoding="utf-8")

    assert "stop" in content, "deploy.sh must stop the container before recreating"
    assert "pull" in content, "deploy.sh must pull the latest image"
    assert "up -d" in content or "up" in content, "deploy.sh must restart the container in detached mode"
    assert "debrid-hub" in content, "deploy.sh must target debrid-hub service"


def test_docker_entrypoint_exists_and_executable():
    """Verify docker-entrypoint.sh exists, is executable, and has valid sh syntax."""
    entrypoint_sh = SERVER_DIR / "docker-entrypoint.sh"
    assert entrypoint_sh.is_file(), "docker-entrypoint.sh must exist in server/"

    mode = entrypoint_sh.stat().st_mode
    assert bool(mode & stat.S_IXUSR), "docker-entrypoint.sh must be executable by user"

    result = subprocess.run(["sh", "-n", str(entrypoint_sh)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, f"Syntax error in docker-entrypoint.sh: {result.stderr}"


def test_docker_entrypoint_graceful_sigterm_exit_zero():
    """Verify docker-entrypoint.sh handles SIGTERM and exits cleanly with code 0."""
    entrypoint_sh = SERVER_DIR / "docker-entrypoint.sh"

    # Run entrypoint wrapping a sleep command
    proc = subprocess.Popen(
        [str(entrypoint_sh), "sleep", "10"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.5)

    # Send SIGTERM (simulating Docker stop)
    proc.send_signal(signal.SIGTERM)
    exit_code = proc.wait(timeout=5)

    assert exit_code == 0, f"Expected clean exit code 0 on SIGTERM, got {exit_code}"


def test_docker_entrypoint_graceful_sigint_exit_zero():
    """Verify docker-entrypoint.sh handles SIGINT and exits cleanly with code 0."""
    entrypoint_sh = SERVER_DIR / "docker-entrypoint.sh"

    proc = subprocess.Popen(
        [str(entrypoint_sh), "sleep", "10"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.5)

    # Send SIGINT (simulating Ctrl+C)
    proc.send_signal(signal.SIGINT)
    exit_code = proc.wait(timeout=5)

    assert exit_code == 0, f"Expected clean exit code 0 on SIGINT, got {exit_code}"


def test_docker_entrypoint_preserves_non_zero_exit_codes():
    """Verify docker-entrypoint.sh preserves real failure exit codes from child process."""
    entrypoint_sh = SERVER_DIR / "docker-entrypoint.sh"

    proc = subprocess.run(
        [str(entrypoint_sh), "sh", "-c", "exit 42"],
        check=False,
        capture_output=True,
    )

    assert proc.returncode == 42, f"Expected exit code 42, got {proc.returncode}"


def test_docker_compose_configures_stop_grace_period():
    """Verify docker-compose.yml configures stop_grace_period for clean shutdowns."""
    compose_file = SERVER_DIR / "docker-compose.yml"
    content = compose_file.read_text(encoding="utf-8")

    assert "stop_grace_period" in content, "docker-compose.yml must specify stop_grace_period"


def test_env_template_structure_and_port():
    """Verify .env.template exists, defaults to port 38415, and .env.example is removed."""
    env_template = REPO_ROOT / ".env.template"
    env_example = REPO_ROOT / ".env.example"

    assert env_template.is_file(), ".env.template must exist in repo root"
    assert not env_example.exists(), ".env.example must not exist (standardized on .env.template)"

    template_text = env_template.read_text(encoding="utf-8")

    # Verify Section 1: Required Host Settings
    assert "SECTION 1: REQUIRED HOST SETTINGS" in template_text
    assert "PORT=38415" in template_text
    assert "NAS_DOWNLOAD_PATH=/data/downloads" in template_text
    assert "NAS_CONFIG_PATH=" in template_text
    assert "UMASK=002" in template_text

    # Verify Section 2: Headless / Automated Deployments Only
    assert "SECTION 2: HEADLESS / AUTOMATED DEPLOYMENTS ONLY" in template_text
    assert "# RD_API_KEY=" in template_text
    assert "# ARIA2C_MAX_CONNECTIONS=" in template_text

    # Verify docker-compose.yml defaults to port 38415 and /data/downloads
    compose_file = SERVER_DIR / "docker-compose.yml"
    compose_content = compose_file.read_text(encoding="utf-8")
    assert "${PORT:-38415}:8080" in compose_content, "docker-compose.yml must default host port to 38415"
    assert "${NAS_DOWNLOAD_PATH:-/data/downloads}:/downloads" in compose_content, "docker-compose.yml must default download path to /data/downloads"


def test_dockerfile_configures_timeout_graceful_shutdown():
    """Verify Dockerfile CMD specifies --timeout-graceful-shutdown 5 for clean Uvicorn stops."""
    dockerfile = SERVER_DIR / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")

    assert "--timeout-graceful-shutdown" in content, "Dockerfile CMD must specify --timeout-graceful-shutdown"
    assert '"5"' in content, "Dockerfile CMD must set graceful shutdown timeout to 5 seconds"


def test_dockerfile_bundles_unrar():
    """Verify Dockerfile compiles and installs official unrar alongside 7zip."""
    dockerfile = SERVER_DIR / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")

    assert "unrarsrc" in content, "Dockerfile must download and build official unrar"
    assert "/usr/bin/unrar" in content, "Dockerfile must install unrar to /usr/bin/unrar"
    assert "7zip" in content, "Dockerfile must retain 7zip for zip/7z extractions"



def test_docker_entrypoint_bounded_wait_on_hanging_process():
    """Verify docker-entrypoint.sh bounds wait time and exits with code 0 even if child ignores SIGTERM."""
    entrypoint_sh = SERVER_DIR / "docker-entrypoint.sh"

    # Child process traps SIGTERM and sleeps, simulating a hanging or uncooperative process
    proc = subprocess.Popen(
        [str(entrypoint_sh), "sh", "-c", "trap '' TERM; sleep 30"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "SHUTDOWN_TIMEOUT_SECONDS": "1"},
        text=True,
    )
    time.sleep(0.3)

    # Send SIGTERM to the entrypoint wrapper
    proc.send_signal(signal.SIGTERM)
    t0 = time.time()
    exit_code = proc.wait(timeout=5)
    elapsed = time.time() - t0

    assert exit_code == 0, f"Expected clean exit code 0 after bounded wait, got {exit_code}"
    assert elapsed < 4.0, f"Expected bounded termination in <4s with SHUTDOWN_TIMEOUT_SECONDS=1, took {elapsed:.2f}s"


def test_uvicorn_graceful_shutdown_with_active_sse_connection(tmp_path):
    """Verify docker-entrypoint wrapping Uvicorn exits 0 within timeout when active SSE stream is open."""
    # Find free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    entrypoint_sh = SERVER_DIR / "docker-entrypoint.sh"
    uvicorn_bin = Path(sys.executable).parent / "uvicorn"
    if not uvicorn_bin.is_file():
        uvicorn_bin = Path("uvicorn")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVER_DIR)
    env["SHUTDOWN_TIMEOUT_SECONDS"] = "5"
    env["CONFIG_FILE"] = str(tmp_path / "settings.json")

    proc = subprocess.Popen(
        [
            str(entrypoint_sh),
            str(uvicorn_bin),
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--timeout-graceful-shutdown",
            "2",
        ],
        cwd=str(SERVER_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )

    try:
        # Wait for server to become responsive
        healthy = False
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)

        assert healthy, "Uvicorn test server failed to start within 5s"

        # Connect to the SSE endpoint in a background thread using standard library urllib
        sse_opened = threading.Event()
        sse_error = []

        def connect_sse():
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status/stream")
                with urllib.request.urlopen(req, timeout=10) as stream_resp:
                    sse_opened.set()
                    while stream_resp.read(1024):
                        pass
            except (urllib.error.URLError, OSError, http.client.RemoteDisconnected) as e:
                sse_error.append(e)

        t = threading.Thread(target=connect_sse, daemon=True)
        t.start()

        assert sse_opened.wait(timeout=3), "Failed to establish SSE stream within 3s"

        # Send SIGTERM to simulate docker stop / Watchtower update
        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        exit_code = proc.wait(timeout=6)
        elapsed = time.time() - t0

        assert exit_code == 0, f"Expected clean exit code 0 on SIGTERM with active SSE stream, got {exit_code}"
        assert elapsed < 5.0, f"Expected graceful shutdown in <5s with 2s timeout, took {elapsed:.2f}s"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_docker_publish_workflow_separates_edge_and_latest():
    """Verify docker-publish.yml publishes :edge from main and :latest/semver from prod/tags."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")

    assert 'branches: [ "main", "prod" ]' in workflow
    assert "latest=false" in workflow
    assert "type=raw,value=edge,enable=${{ github.ref == 'refs/heads/main' }}" in workflow
    assert (
        "type=raw,value=latest,enable=${{ github.ref == 'refs/heads/prod' || "
        "startsWith(github.ref, 'refs/tags/v') || github.event_name == 'release' }}"
    ) in workflow
    assert "type=semver,pattern={{major}}.{{minor}}" in workflow


def test_tests_workflow_covers_prod_prs():
    """Verify tests.yml runs on pull requests targeting both main and prod."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "branches: [ main, prod ]" in workflow


def test_release_workflow_and_changelog_exist():
    """Verify release.yml packages versioned Chrome/Firefox extensions and CHANGELOG.md exists."""
    release_yml = REPO_ROOT / ".github" / "workflows" / "release.yml"
    assert release_yml.is_file(), ".github/workflows/release.yml must exist"
    content = release_yml.read_text(encoding="utf-8")
    assert '"v*.*.*"' in content
    assert "extension/build.py --sync --validate --package --no-build" in content
    assert "debrid-hub-chrome-${VERSION}.zip" in content
    assert "debrid-hub-firefox-${VERSION}.xpi" in content

    changelog = REPO_ROOT / "CHANGELOG.md"
    assert changelog.is_file(), "CHANGELOG.md must exist at repository root"
    assert "## [0.2.0]" in changelog.read_text(encoding="utf-8")


def test_dockerfile_and_compose_configure_nonroot_puid_pgid():
    """Verify Dockerfile, docker-compose.yml, and .env.template configure non-root PUID/PGID."""
    dockerfile = (SERVER_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "su-exec" in dockerfile
    assert "adduser" in dockerfile
    assert "PUID=1000" in dockerfile
    assert "PGID=1000" in dockerfile

    compose = (SERVER_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert "PUID=${PUID:-1000}" in compose
    assert "PGID=${PGID:-1000}" in compose

    env_tpl = (REPO_ROOT / ".env.template").read_text(encoding="utf-8")
    assert "PUID=1000" in env_tpl
    assert "PGID=1000" in env_tpl


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_docker_entrypoint_drops_privileges_via_su_exec(tmp_path: Path):
    """Verify docker-entrypoint.sh chowns config/downloads and drops root via su-exec."""
    mockbin = tmp_path / "mockbin"
    mockbin.mkdir()
    chown_log = tmp_path / "chown.log"
    su_log = tmp_path / "su_exec.log"

    _write_executable(mockbin / "id", "#!/bin/sh\necho 0\n")
    _write_executable(mockbin / "chown", f'#!/bin/sh\necho "$*" >> "{chown_log}"\n')
    _write_executable(
        mockbin / "su-exec",
        f'#!/bin/sh\necho "$1|$HOME" > "{su_log}"\nshift\nexec "$@"\n',
    )

    config_dir = tmp_path / "cfg"
    download_dir = tmp_path / "dl"
    env = {
        "PATH": f"{mockbin}:/usr/bin:/bin",
        "PUID": "1026",
        "PGID": "100",
        "CONFIG_FILE": str(config_dir / "settings.json"),
        "DOWNLOAD_DIR": str(download_dir),
    }

    res = subprocess.run(
        [str(SERVER_DIR / "docker-entrypoint.sh"), "sh", "-c", "exit 0"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert su_log.read_text(encoding="utf-8").strip() == "1026:100|/tmp"
    chown_lines = chown_log.read_text(encoding="utf-8").splitlines()
    assert f"-R 1026:100 {config_dir}" in chown_lines
    assert f"1026:100 {download_dir}" in chown_lines


def test_docker_entrypoint_rejects_root_or_invalid_puid_pgid():
    """Verify docker-entrypoint.sh refuses UID/GID 0 and non-numeric PUID/PGID values."""
    entrypoint_sh = str(SERVER_DIR / "docker-entrypoint.sh")
    for bad_env in (
        {"PUID": "0", "PGID": "1000"},
        {"PUID": "1000", "PGID": "0"},
        {"PUID": "root", "PGID": "1000"},
        {"PUID": "1000", "PGID": "abc"},
    ):
        env = {"PATH": "/usr/bin:/bin", **bad_env}
        res = subprocess.run(
            [entrypoint_sh, "sh", "-c", "exit 0"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode != 0
        assert "[ERROR]" in res.stderr


def test_docker_entrypoint_su_exec_graceful_sigterm_exit_zero(tmp_path: Path):
    """Verify SIGTERM exits 0 cleanly when running child through su-exec."""
    mockbin = tmp_path / "mockbin"
    mockbin.mkdir()
    _write_executable(mockbin / "id", "#!/bin/sh\necho 0\n")
    _write_executable(mockbin / "chown", "#!/bin/sh\nexit 0\n")
    _write_executable(mockbin / "su-exec", '#!/bin/sh\nshift\nexec "$@"\n')

    env = {
        "PATH": f"{mockbin}:/usr/bin:/bin",
        "PUID": "1000",
        "PGID": "1000",
        "CONFIG_FILE": str(tmp_path / "cfg" / "settings.json"),
        "DOWNLOAD_DIR": str(tmp_path / "dl"),
    }
    proc = subprocess.Popen(
        [str(SERVER_DIR / "docker-entrypoint.sh"), "sleep", "10"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.5)
    proc.send_signal(signal.SIGTERM)
    exit_code = proc.wait(timeout=5)
    assert exit_code == 0, f"Expected exit code 0 on SIGTERM via su-exec, got {exit_code}"


