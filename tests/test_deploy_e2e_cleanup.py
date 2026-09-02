"""Ausfuehrbare Fehlerpfadtests fuer den gestreamten Remote-Cleanup."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
CLEANUP = ROOT / "tests" / "integration" / "remote_cleanup_deploy_e2e.sh"
PREFLIGHT = ROOT / "tests" / "integration" / "remote_preflight_deploy_e2e.sh"
IMAGE_ID = "sha256:" + "a" * 64


def _remote_tree() -> tuple[Path, Path]:
    parent = Path(tempfile.mkdtemp(prefix="congress-tracker-e2e-unit-", dir="/tmp"))
    remote = parent / "congress-tracker"
    remote.mkdir()
    return parent, remote


def _fake_docker(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -u
mode="${FAKE_DOCKER_MODE:-empty}"
args="$*"
[[ -z "${FAKE_DOCKER_LOG:-}" ]] || printf '%s\n' "$args" >> "$FAKE_DOCKER_LOG"
[[ "$1" == info ]] && exit 0
if [[ "$mode" == down_fail && "$1" == compose ]]; then exit 9; fi
if [[ "$mode" == query_fail && "$1 $2" == "network ls" ]]; then exit 8; fi
if [[ "$mode" == ledger_query_fail && "$1 $2 $3" == "image ls -aq" ]]; then exit 7; fi
if [[ "$mode" == preflight_query_fail && "$1 $2" == "ps -a" ]]; then exit 6; fi
if [[ "$mode" == foreign_tag && "$1 $2" == "image ls" ]]; then
  echo "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  exit 0
fi
if [[ "$mode" == foreign_tag && "$1 $2" == "image inspect" ]]; then
  [[ "$args" == *--format* ]] && echo "congress-tracker-dashboard:latest"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return fake_bin


def _run(
    mode: str, parent: Path, remote: Path, fake_bin: Path, *,
    verify: bool = False, log: Path | None = None,
):
    suffix = parent.name.removeprefix("congress-tracker-e2e-unit-")
    scope = f"unit-{suffix}"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_DOCKER_MODE"] = mode
    if log is not None:
        env["FAKE_DOCKER_LOG"] = str(log)
    return subprocess.run(
        [
            "bash", str(CLEANUP), "verify" if verify else "cleanup",
            str(remote), str(parent), f"congress-tracker-e2e-{scope}",
            f"congress-dashboard-e2e-{scope}",
            f"congress-tracker-dashboard-e2e-{scope}",
        ],
        env=env, text=True, capture_output=True, check=False,
    )


def test_cleanup_success_removes_exact_test_parent(tmp_path):
    parent, remote = _remote_tree()
    try:
        result = _run("empty", parent, remote, _fake_docker(tmp_path))
        assert result.returncode == 0, result.stderr
        assert not parent.exists()
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_compose_down_failure_is_propagated_and_keeps_ledger(tmp_path):
    parent, remote = _remote_tree()
    try:
        deploy = remote / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.prod.yml").write_text("services: {}\n")
        (deploy / ".env").write_text("APP_AUTH_ENABLED=0\n")
        (parent / "e2e-image-ids").write_text(IMAGE_ID + "\n")
        result = _run("down_fail", parent, remote, _fake_docker(tmp_path))
        assert result.returncode != 0
        assert parent.exists()
        assert (parent / "e2e-image-ids").exists()
        assert "Ledger bleibt erhalten" in result.stderr
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_verify_docker_query_failure_cannot_look_empty(tmp_path):
    parent, remote = _remote_tree()
    shutil.rmtree(parent)
    result = _run("query_fail", parent, remote, _fake_docker(tmp_path), verify=True)
    assert result.returncode != 0


def test_verify_existing_remote_parent_cannot_look_empty(tmp_path):
    parent, remote = _remote_tree()
    try:
        result = _run("empty", parent, remote, _fake_docker(tmp_path), verify=True)
        assert result.returncode != 0
        assert parent.exists()
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_foreign_image_tag_is_never_removed_by_id(tmp_path):
    parent, remote = _remote_tree()
    try:
        (parent / "e2e-image-ids").write_text(IMAGE_ID + "\n")
        log = tmp_path / "docker.log"
        fake_bin = _fake_docker(tmp_path)
        result = _run("foreign_tag", parent, remote, fake_bin, log=log)
        assert result.returncode != 0
        assert parent.exists()
        assert "fremde Tags und bleibt erhalten" in result.stderr
        calls = log.read_text(encoding="utf-8")
        assert f"image rm -f -- {IMAGE_ID}" not in calls
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_ledger_query_failure_keeps_parent_for_retry(tmp_path):
    parent, remote = _remote_tree()
    try:
        (parent / "e2e-image-ids").write_text(IMAGE_ID + "\n")
        result = _run("ledger_query_fail", parent, remote, _fake_docker(tmp_path))
        assert result.returncode != 0
        assert parent.exists()
        assert (parent / "e2e-image-ids").exists()
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_orphan_preflight_propagates_docker_query_failure(tmp_path):
    fake_bin = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_DOCKER_MODE"] = "preflight_query_fail"
    result = subprocess.run(
        ["bash", PREFLIGHT], env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0


def test_cleanup_rejects_parent_traversal_and_preserves_sentinel(tmp_path):
    parent, _ = _remote_tree()
    sentinel = parent / "sentinel"
    sentinel.write_text("bleibt\n", encoding="utf-8")
    malicious_parent = Path(f"{parent}/..")
    malicious_remote = Path(f"{malicious_parent}/congress-tracker")
    try:
        result = _run("empty", malicious_parent, malicious_remote, _fake_docker(tmp_path))
        assert result.returncode == 2
        assert sentinel.read_text(encoding="utf-8") == "bleibt\n"
    finally:
        shutil.rmtree(parent, ignore_errors=True)
