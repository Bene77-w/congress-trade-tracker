#!/usr/bin/env bash
# Deployment-Snapshot bauen, hochladen und Dashboard neu starten.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
DEPLOY_TARGET="${DEPLOY_TARGET:-deploy@server.example}"
DEPLOY_PATH="${DEPLOY_PATH:-/srv/congress-tracker}"
PYTHON_BIN="${PYTHON_BIN:-python}"
DRY_RUN=0

usage() {
  echo "Aufruf: scripts/deploy_data.sh [--dry-run]"
}

case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }

if [[ ! "$DEPLOY_TARGET" =~ ^([A-Za-z0-9._][A-Za-z0-9._-]*@)?[A-Za-z0-9._][A-Za-z0-9._-]*$ ]]; then
  echo "Ungueltiges DEPLOY_TARGET: $DEPLOY_TARGET" >&2
  exit 2
fi
if [[ ! "$DEPLOY_PATH" =~ ^/[A-Za-z0-9._/-]+/congress-tracker$ \
      || "$DEPLOY_PATH" == *".."* ]]; then
  echo "Unsicherer DEPLOY_PATH: $DEPLOY_PATH" >&2
  exit 2
fi

run() {
  if (( DRY_RUN )); then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

if (( ! DRY_RUN )); then
  command -v "$PYTHON_BIN" >/dev/null || {
    echo "Python-Befehl fehlt: $PYTHON_BIN" >&2
    exit 1
  }
  command -v rsync >/dev/null || { echo "Befehl fehlt: rsync" >&2; exit 1; }
  command -v ssh >/dev/null || { echo "Befehl fehlt: ssh" >&2; exit 1; }
fi

cd "$PROJECT_ROOT"
run ssh "$DEPLOY_TARGET" \
  "test -f '$DEPLOY_PATH/deploy/docker-compose.prod.yml' && test -f '$DEPLOY_PATH/deploy/.env'"
run "$PYTHON_BIN" scripts/build_deploy_snapshot.py

if (( ! DRY_RUN )); then
  [[ -s data/deploy/trades_snapshot.parquet ]] || {
    echo "Snapshot fehlt: data/deploy/trades_snapshot.parquet" >&2
    exit 1
  }
  [[ -s data/deploy/snapshot_manifest.json ]] || {
    echo "Manifest fehlt: data/deploy/snapshot_manifest.json" >&2
    exit 1
  }
fi

run rsync -avz data/deploy/ "$DEPLOY_TARGET:$DEPLOY_PATH/data/deploy/"
REMOTE_CMD="cd '$DEPLOY_PATH' && docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --force-recreate --wait --wait-timeout 180 dashboard && docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env ps dashboard"
run ssh "$DEPLOY_TARGET" "$REMOTE_CMD"
