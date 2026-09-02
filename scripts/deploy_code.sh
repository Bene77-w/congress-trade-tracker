#!/usr/bin/env bash
# Code des oeffentlichen Dashboards per rsync ausrollen und neu bauen.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
DEPLOY_CONFIG_FILE="${DEPLOY_CONFIG_FILE:-$PROJECT_ROOT/.deploy.env}"
[[ ! -f "$DEPLOY_CONFIG_FILE" ]] || source "$DEPLOY_CONFIG_FILE"
DEPLOY_COMPOSE_PROJECT="${DEPLOY_COMPOSE_PROJECT:-congress-tracker}"
DRY_RUN=0

usage() {
  echo "Aufruf: scripts/deploy_code.sh [--dry-run]"
}

case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }

: "${DEPLOY_TARGET:?DEPLOY_TARGET fehlt; .deploy.env.example nach .deploy.env kopieren}"
: "${DEPLOY_PATH:?DEPLOY_PATH fehlt; .deploy.env.example nach .deploy.env kopieren}"

if [[ ! "$DEPLOY_TARGET" =~ ^([A-Za-z0-9._][A-Za-z0-9._-]*@)?[A-Za-z0-9._][A-Za-z0-9._-]*$ ]]; then
  echo "Ungueltiges DEPLOY_TARGET: $DEPLOY_TARGET" >&2
  exit 2
fi
if [[ ! "$DEPLOY_PATH" =~ ^/[A-Za-z0-9._/-]+/congress-tracker$ \
      || "$DEPLOY_PATH" == *".."* ]]; then
  echo "Unsicherer DEPLOY_PATH: $DEPLOY_PATH" >&2
  exit 2
fi
if [[ ! "$DEPLOY_COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "Ungueltiges DEPLOY_COMPOSE_PROJECT: $DEPLOY_COMPOSE_PROJECT" >&2
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
  command -v git >/dev/null || { echo "Befehl fehlt: git" >&2; exit 1; }
  if [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain)" ]]; then
    echo "Code-Deployment abgebrochen: Git-Worktree ist nicht sauber." >&2
    echo "Bitte Aenderungen zuerst committen oder entfernen." >&2
    exit 1
  fi
  command -v tar >/dev/null || { echo "Befehl fehlt: tar" >&2; exit 1; }
  command -v rsync >/dev/null || { echo "Befehl fehlt: rsync" >&2; exit 1; }
  command -v ssh >/dev/null || { echo "Befehl fehlt: ssh" >&2; exit 1; }
fi

cd "$PROJECT_ROOT"
run ssh "$DEPLOY_TARGET" \
  "test -f '$DEPLOY_PATH/deploy/docker-compose.prod.yml' && test -f '$DEPLOY_PATH/deploy/.env'"

if (( DRY_RUN )); then
  DEPLOY_SOURCE="<git-archive:HEAD>"
else
  DEPLOY_SOURCE="$(mktemp -d)"
  trap 'rm -rf -- "$DEPLOY_SOURCE"' EXIT
  git archive --format=tar HEAD -- . ':(exclude).deploy.env' \
    ':(exclude)data' ':(exclude)logs' \
    | tar -xf - -C "$DEPLOY_SOURCE"
fi

run rsync -az --delete \
  --exclude=.git/ --exclude=.env --exclude=.deploy.env \
  --exclude=venv/ --exclude=archive/ \
  --exclude=__pycache__/ --exclude=data/ --exclude=logs/ \
  --exclude=deploy/.env \
  "$DEPLOY_SOURCE/" "$DEPLOY_TARGET:$DEPLOY_PATH/"

REMOTE_CMD="cd '$DEPLOY_PATH' && docker compose -p '$DEPLOY_COMPOSE_PROJECT' -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build --wait --wait-timeout 180 && docker compose -p '$DEPLOY_COMPOSE_PROJECT' -f deploy/docker-compose.prod.yml --env-file deploy/.env ps"
run ssh "$DEPLOY_TARGET" "$REMOTE_CMD"
