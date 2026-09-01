"""Nicht-netzwerkende Vertragstests fuer die Deployment-Shellskripte."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent.parent


def _run(script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env.update(env)
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_deploy_code_dry_run_protects_remote_state():
    result = _run(
        "deploy_code.sh",
        "--dry-run",
        DEPLOY_TARGET="deploy@example.org",
        DEPLOY_PATH="/srv/congress-tracker",
        TMPDIR="/nicht-vorhanden",
    )

    assert result.returncode == 0, result.stderr
    assert "test\\ -f" in result.stdout
    assert "--delete" in result.stdout
    assert "--exclude=.env" in result.stdout
    assert "--exclude=deploy/.env" in result.stdout
    assert "--exclude=data/" in result.stdout
    assert "git-archive:HEAD" in result.stdout
    assert "deploy@example.org:/srv/congress-tracker/" in result.stdout
    assert "up\\ -d\\ --build" in result.stdout
    assert "--wait\\ --wait-timeout\\ 180" in result.stdout
    assert "deploy/.env\\ ps" in result.stdout


def test_deploy_data_dry_run_covers_complete_sequence():
    result = _run(
        "deploy_data.sh",
        "--dry-run",
        DEPLOY_TARGET="deploy@example.org",
        DEPLOY_PATH="/srv/congress-tracker",
        PYTHON_BIN="/opt/venv/bin/python",
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 4
    assert "test\\ -f" in lines[0]
    assert "build_deploy_snapshot.py" in lines[1]
    assert "data/deploy/" in lines[2]
    assert "deploy@example.org:/srv/congress-tracker/data/deploy/" in lines[2]
    assert "--force-recreate" in lines[3]
    assert "--wait\\ --wait-timeout\\ 180\\ dashboard" in lines[3]
    assert "ps\\ dashboard" in lines[3]


def test_deploy_scripts_reject_unsafe_remote_path():
    for script in ("deploy_code.sh", "deploy_data.sh"):
        for path in ("/", "/etc", "/root", "/opt", "/srv/anderes-projekt"):
            result = _run(script, "--dry-run", DEPLOY_PATH=path)
            assert result.returncode == 2
            assert "Unsicherer DEPLOY_PATH" in result.stderr
            assert "DRY-RUN" not in result.stdout


def test_deploy_scripts_reject_unknown_arguments():
    for script in ("deploy_code.sh", "deploy_data.sh"):
        result = _run(script, "--unbekannt")
        assert result.returncode == 2
        assert "Aufruf:" in result.stderr


def test_deploy_scripts_reject_ssh_option_as_target():
    for script in ("deploy_code.sh", "deploy_data.sh"):
        result = _run(
            script,
            "--dry-run",
            DEPLOY_TARGET="-oProxyCommand",
            DEPLOY_PATH="/srv/congress-tracker",
        )
        assert result.returncode == 2
        assert "Ungueltiges DEPLOY_TARGET" in result.stderr


def test_deploy_code_rejects_dirty_worktree(tmp_path):
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "deploy_code.sh"
    script.write_bytes((ROOT / "scripts" / "deploy_code.sh").read_bytes())
    readme = repo / "README.md"
    readme.write_text("sauber\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.org"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "Test"], check=True)
    readme.write_text("dirty\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", script],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "DEPLOY_TARGET": "deploy@example.org",
            "DEPLOY_PATH": "/srv/congress-tracker",
        },
    )

    assert result.returncode == 1
    assert "Git-Worktree ist nicht sauber" in result.stderr
    assert "rsync" not in result.stdout
