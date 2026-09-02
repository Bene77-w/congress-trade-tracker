"""Lokale Safety-Vertraege des opt-in VPS-E2E-Tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "integration" / "test_deploy_e2e.sh"
FIXTURE = ROOT / "tests" / "fixtures" / "docker-compose.deploy-e2e.yml"
CLEANUP = ROOT / "tests" / "integration" / "remote_cleanup_deploy_e2e.sh"
PREFLIGHT = ROOT / "tests" / "integration" / "remote_preflight_deploy_e2e.sh"


def test_e2e_requires_explicit_opt_in():
    env = os.environ.copy()
    env["RUN_DEPLOY_E2E"] = "0"
    result = subprocess.run(
        ["bash", SCRIPT], text=True, capture_output=True, check=False, env=env,
    )
    assert result.returncode == 2
    assert "RUN_DEPLOY_E2E=1" in result.stderr


def test_e2e_fixture_is_isolated_from_production_ports_and_names():
    text = FIXTURE.read_text(encoding="utf-8")
    assert "caddy" not in text.lower()
    assert '"80:80"' not in text
    assert '"443:443"' not in text
    assert "127.0.0.1:__E2E_PORT__:8501" in text
    assert "__E2E_CONTAINER_NAME__" in text
    assert "__E2E_IMAGE__" in text
    assert "__E2E_PROJECT_NAME__" in text


def test_e2e_has_failure_safe_cleanup_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    cleanup = CLEANUP.read_text(encoding="utf-8")
    preflight = PREFLIGHT.read_text(encoding="utf-8")
    assert "trap cleanup EXIT" in text
    assert "docker compose" in cleanup and "down --volumes --remove-orphans" in cleanup
    assert 'docker compose -p "$project_name"' in cleanup
    assert 'DEPLOY_COMPOSE_PROJECT="$E2E_PROJECT_NAME"' in text
    assert 'docker image rm -f -- "$image_name"' in cleanup
    assert "e2e-image-ids" in cleanup
    assert "fremde Tags und bleibt erhalten" in cleanup
    assert 'ledger_ids="$(sort -u' in cleanup
    assert "Ledger bleibt erhalten" in cleanup
    assert "congress-tracker-e2e-" in cleanup
    assert "scope_id" in cleanup
    assert 'rm -rf -- "$remote_parent"' in cleanup
    assert "cleanup_rc=0" in cleanup
    assert "label=com.docker.compose.project=$project_name" in cleanup
    assert "docker network rm" in cleanup
    assert "docker volume rm" in cleanup
    assert cleanup.count("docker info") >= 1
    assert "remote_cleanup_is_complete" in text
    assert 'REMOTE_PARENT="$E2E_REMOTE_BASE/congress-tracker-e2e-$RUN_ID"' in text
    assert '"$E2E_REMOTE_BASE" < "$REMOTE_PREFLIGHT_SCRIPT"' in text
    assert "PROD_BEFORE" in text and "production_state" in text
    assert "RestartCount" in text
    assert "dashboard-health {{.State.Health.Status}}" in text
    assert "Ungueltiges DEPLOY_TARGET" in text
    assert "Ungueltiger E2E_PORT" in text
    assert 'command -v -- "$E2E_PYTHON_BIN"' in text
    assert "docker exec -i '$E2E_CONTAINER_NAME' python - '$expected'" in text
    assert "pd.read_parquet" in text
    assert 'manifest["rows"] == expected' in text
    assert "actual is expected" in text
    assert "verify_marker 1" in text
    assert "verify_marker 0" in text
    assert "revert --no-edit HEAD" in text
    assert "Git-Worktree ist nicht sauber" in text
    assert 'cat-file -e "HEAD:$required_path"' in text
    assert "trap '' HUP INT TERM" in text
    assert "worktree list --porcelain" in text
    assert "preflight_no_orphans" in text
    assert "Verwaiste Deployment-E2E-Ressourcen" in preflight
    assert "all_containers=" in preflight and "all_images=" in preflight
    assert "listeners=\"$(ss -ltn)\"" in text
    assert 'container_before' in text and 'container_after' in text
    assert "Testcontainer nicht neu erstellt" in text
    assert "original_rc" in text and "cleanup_rc" in text
