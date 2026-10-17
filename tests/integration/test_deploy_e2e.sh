#!/usr/bin/env bash
# Echte, isolierte VPS-Pruefung fuer deploy_code.sh und deploy_data.sh.
set -Eeuo pipefail

if [[ "${RUN_DEPLOY_E2E:-0}" != "1" ]]; then
  echo "Abbruch: echten VPS-Test nur mit RUN_DEPLOY_E2E=1 starten." >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_CONFIG_FILE="${DEPLOY_CONFIG_FILE:-$PROJECT_ROOT/.deploy.env}"
[[ ! -f "$DEPLOY_CONFIG_FILE" ]] || source "$DEPLOY_CONFIG_FILE"
FIXTURE="$PROJECT_ROOT/tests/fixtures/docker-compose.deploy-e2e.yml"
REMOTE_CLEANUP_SCRIPT="$PROJECT_ROOT/tests/integration/remote_cleanup_deploy_e2e.sh"
REMOTE_PREFLIGHT_SCRIPT="$PROJECT_ROOT/tests/integration/remote_preflight_deploy_e2e.sh"
command -v git >/dev/null || { echo "Befehl fehlt: git" >&2; exit 1; }
if [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain)" ]]; then
  echo "E2E-Test abgebrochen: Git-Worktree ist nicht sauber." >&2
  echo "Testskript und Deployment-Aenderungen zuerst committen." >&2
  exit 1
fi
for required_path in \
  scripts/deploy_code.sh \
  scripts/deploy_data.sh \
  tests/integration/test_deploy_e2e.sh \
  tests/integration/remote_cleanup_deploy_e2e.sh \
  tests/integration/remote_preflight_deploy_e2e.sh \
  tests/fixtures/docker-compose.deploy-e2e.yml; do
  git -C "$PROJECT_ROOT" cat-file -e "HEAD:$required_path" || {
    echo "E2E-Datei fehlt in HEAD: $required_path" >&2
    exit 1
  }
done
: "${DEPLOY_TARGET:?DEPLOY_TARGET fehlt; .deploy.env.example nach .deploy.env kopieren}"
: "${E2E_REMOTE_BASE:?E2E_REMOTE_BASE fehlt; .deploy.env.example nach .deploy.env kopieren}"
: "${E2E_MASTER_SOURCE:?E2E_MASTER_SOURCE fehlt; .deploy.env.example nach .deploy.env kopieren}"
E2E_PYTHON_BIN="${E2E_PYTHON_BIN:-python3}"

RUN_ID="$(date -u +%Y%m%d%H%M%S)-$$"
REMOTE_PARENT="$E2E_REMOTE_BASE/congress-tracker-e2e-$RUN_ID"
REMOTE_PATH="$REMOTE_PARENT/congress-tracker"
E2E_PROJECT_NAME="congress-tracker-e2e-$RUN_ID"
E2E_CONTAINER_NAME="congress-dashboard-e2e-$RUN_ID"
E2E_IMAGE="congress-tracker-dashboard-e2e-$RUN_ID"
E2E_PORT="${E2E_PORT:-$((20000 + ($$ % 10000)))}"
CODE_MARKER="DEPLOY_E2E_CODE_$RUN_ID"
LOCAL_TMP="$(mktemp -d)"
WORKTREE="$LOCAL_TMP/worktree"
WORKTREE_READY=0
REMOTE_READY=0
PROD_BEFORE=""

log() {
  printf '[deploy-e2e] %s\n' "$*"
}

production_state() {
  ssh "$DEPLOY_TARGET" \
    "docker inspect --format '{{.Name}} {{.Id}} {{.RestartCount}} {{.State.Status}}' congress-dashboard congress-caddy && docker inspect --format 'dashboard-health {{.State.Health.Status}}' congress-dashboard"
}

preflight_no_orphans() {
  ssh "$DEPLOY_TARGET" bash -s -- "$E2E_REMOTE_BASE" < "$REMOTE_PREFLIGHT_SCRIPT"
}

remote_cleanup_resources() {
  ssh "$DEPLOY_TARGET" bash -s -- cleanup \
    "$REMOTE_PATH" "$REMOTE_PARENT" "$E2E_PROJECT_NAME" \
    "$E2E_CONTAINER_NAME" "$E2E_IMAGE" < "$REMOTE_CLEANUP_SCRIPT"
}

remote_cleanup_is_complete() {
  ssh "$DEPLOY_TARGET" bash -s -- verify \
    "$REMOTE_PATH" "$REMOTE_PARENT" "$E2E_PROJECT_NAME" \
    "$E2E_CONTAINER_NAME" "$E2E_IMAGE" < "$REMOTE_CLEANUP_SCRIPT"
}

cleanup() {
  local original_rc=$?
  local cleanup_rc=0
  trap - EXIT
  trap '' HUP INT TERM
  set +e
  log "Cleanup startet (Test-Exit $original_rc)"

  if (( REMOTE_READY )); then
    remote_cleanup_resources || cleanup_rc=1
    remote_cleanup_is_complete || cleanup_rc=1
  fi

  if (( WORKTREE_READY )); then
    if git -C "$PROJECT_ROOT" worktree list --porcelain \
      | grep -Fxq "worktree $WORKTREE"; then
      git -C "$PROJECT_ROOT" worktree remove --force "$WORKTREE" || cleanup_rc=1
    fi
    git -C "$PROJECT_ROOT" worktree prune || cleanup_rc=1
  fi
  rm -rf -- "$LOCAL_TMP" || cleanup_rc=1

  if [[ -n "$PROD_BEFORE" ]]; then
    local prod_after
    prod_after="$(production_state)" || cleanup_rc=1
    if [[ "$PROD_BEFORE" != "$prod_after" ]]; then
      echo "Produktionszustand hat sich veraendert:" >&2
      diff -u <(printf '%s\n' "$PROD_BEFORE") \
        <(printf '%s\n' "$prod_after") >&2 || true
      cleanup_rc=1
    fi
  fi

  if (( cleanup_rc == 0 )); then
    log "CLEANUP_OK: Container, Image, Volumes, Netzwerk und Remote-Pfad entfernt"
  else
    echo "[deploy-e2e] CLEANUP_FEHLER" >&2
  fi
  if (( cleanup_rc != 0 )); then
    exit 1
  fi
  exit "$original_rc"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

for command_name in git ssh rsync curl; do
  command -v "$command_name" >/dev/null || {
    echo "Befehl fehlt: $command_name" >&2
    exit 1
  }
done
E2E_PYTHON_BIN="$(command -v -- "$E2E_PYTHON_BIN")" \
  || { echo "Python fehlt: $E2E_PYTHON_BIN" >&2; exit 1; }
[[ -s "$E2E_MASTER_SOURCE" ]] || { echo "Master fehlt: $E2E_MASTER_SOURCE" >&2; exit 1; }
[[ -s "$FIXTURE" ]] || { echo "Compose-Fixture fehlt: $FIXTURE" >&2; exit 1; }
[[ "$DEPLOY_TARGET" =~ ^([A-Za-z0-9._][A-Za-z0-9._-]*@)?[A-Za-z0-9._][A-Za-z0-9._-]*$ ]] || {
  echo "Ungueltiges DEPLOY_TARGET: $DEPLOY_TARGET" >&2
  exit 2
}
[[ "$E2E_PORT" =~ ^[0-9]+$ ]] && (( E2E_PORT >= 1024 && E2E_PORT <= 65535 )) || {
  echo "Ungueltiger E2E_PORT: $E2E_PORT" >&2
  exit 2
}
[[ "$E2E_REMOTE_BASE" =~ ^/[A-Za-z0-9._/-]+$ \
   && "$E2E_REMOTE_BASE" != "/" \
   && "$E2E_REMOTE_BASE" != */ \
   && "$E2E_REMOTE_BASE" != *"//"* \
   && "$E2E_REMOTE_BASE" != *"/./"* \
   && "$E2E_REMOTE_BASE" != *"/." \
   && "$E2E_REMOTE_BASE" != *"/../"* \
   && "$E2E_REMOTE_BASE" != *".."* ]] || exit 1
[[ "$REMOTE_PARENT" =~ ^/[A-Za-z0-9._/-]+/congress-tracker-e2e-[0-9]{14}-[0-9]+$ \
   && "$REMOTE_PARENT" == "$E2E_REMOTE_BASE"/* ]] || exit 1
[[ "$REMOTE_PATH" == "$REMOTE_PARENT/congress-tracker" ]] || exit 1
[[ "$E2E_PROJECT_NAME" == "congress-tracker-e2e-$RUN_ID" ]] || exit 1
[[ "$E2E_CONTAINER_NAME" == "congress-dashboard-e2e-$RUN_ID" ]] || exit 1
[[ "$E2E_IMAGE" == "congress-tracker-dashboard-e2e-$RUN_ID" ]] || exit 1

log "Produktionszustand erfassen"
PROD_BEFORE="$(production_state)"
preflight_no_orphans
ssh "$DEPLOY_TARGET" bash -s -- "$REMOTE_PARENT" "$E2E_PORT" <<'REMOTE'
set -euo pipefail
remote_parent="$1"
port="$2"
[[ ! -e "$remote_parent" ]]
listeners="$(ss -ltn)"
[[ "$listeners" != *":$port "* ]]
REMOTE

log "Detached Test-Worktree erstellen"
WORKTREE_READY=1
git -C "$PROJECT_ROOT" worktree add --detach "$WORKTREE" HEAD
cp "$FIXTURE" "$WORKTREE/deploy/docker-compose.prod.yml"
"$E2E_PYTHON_BIN" - "$WORKTREE/deploy/docker-compose.prod.yml" \
  "$E2E_PROJECT_NAME" "$E2E_CONTAINER_NAME" "$E2E_IMAGE" "$E2E_PORT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
values = {
    "__E2E_PROJECT_NAME__": sys.argv[2],
    "__E2E_CONTAINER_NAME__": sys.argv[3],
    "__E2E_IMAGE__": sys.argv[4],
    "__E2E_PORT__": sys.argv[5],
}
text = path.read_text(encoding="utf-8")
for placeholder, value in values.items():
    if text.count(placeholder) != 1:
        raise SystemExit(f"Compose-Platzhalter fehlt/mehrdeutig: {placeholder}")
    text = text.replace(placeholder, value)
if "__E2E_" in text:
    raise SystemExit("Nicht ersetzter E2E-Platzhalter")
path.write_text(text, encoding="utf-8")
PY
git -C "$WORKTREE" add deploy/docker-compose.prod.yml
git -C "$WORKTREE" -c user.name='Deployment E2E' \
  -c user.email='deployment-e2e@local' commit -qm 'E2E: isolierte Compose-Baseline'

log "Remote-Testziel initialisieren"
REMOTE_READY=1
ssh "$DEPLOY_TARGET" "mkdir -p '$REMOTE_PATH/deploy'"
rsync -az "$WORKTREE/deploy/docker-compose.prod.yml" \
  "$DEPLOY_TARGET:$REMOTE_PATH/deploy/docker-compose.prod.yml"
printf '%s\n' 'APP_AUTH_ENABLED=0' 'APP_PASSWORD=' \
  | ssh "$DEPLOY_TARGET" "umask 077; cat > '$REMOTE_PATH/deploy/.env'"

make_master() {
  local rows="$1"
  mkdir -p "$WORKTREE/data/processed"
  "$E2E_PYTHON_BIN" - "$E2E_MASTER_SOURCE" \
    "$WORKTREE/data/processed/trades_master_complete.csv" "$rows" <<'PY'
from pathlib import Path
import sys
import pandas as pd

source, target, rows = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
df = pd.read_csv(source, nrows=rows, low_memory=False)
if len(df) != rows:
    raise SystemExit(f"Master hat nur {len(df)} statt {rows} Testzeilen")
target.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(target, index=False)
PY
}

deploy_code() {
  DEPLOY_TARGET="$DEPLOY_TARGET" DEPLOY_PATH="$REMOTE_PATH" \
    DEPLOY_COMPOSE_PROJECT="$E2E_PROJECT_NAME" \
    "$WORKTREE/scripts/deploy_code.sh"
  ssh "$DEPLOY_TARGET" \
    "docker image inspect --format '{{.Id}}' '$E2E_IMAGE' >> '$REMOTE_PARENT/e2e-image-ids'"
}

deploy_data() {
  local container_before container_after
  container_before="$(ssh "$DEPLOY_TARGET" \
    "docker inspect --format '{{.Id}}' '$E2E_CONTAINER_NAME'")"
  DEPLOY_TARGET="$DEPLOY_TARGET" DEPLOY_PATH="$REMOTE_PATH" \
    DEPLOY_COMPOSE_PROJECT="$E2E_PROJECT_NAME" \
    PYTHON_BIN="$E2E_PYTHON_BIN" "$WORKTREE/scripts/deploy_data.sh"
  container_after="$(ssh "$DEPLOY_TARGET" \
    "docker inspect --format '{{.Id}}' '$E2E_CONTAINER_NAME'")"
  [[ -n "$container_before" && -n "$container_after" \
      && "$container_before" != "$container_after" ]] || {
    echo "Datendeployment hat den Testcontainer nicht neu erstellt." >&2
    return 1
  }
}

verify_rows() {
  local expected="$1"
  [[ "$expected" =~ ^[0-9]+$ ]] || return 2
  ssh "$DEPLOY_TARGET" \
    "docker exec -i '$E2E_CONTAINER_NAME' python - '$expected'" <<'PY'
import json
from pathlib import Path
import sys
import pandas as pd

expected = int(sys.argv[1])
manifest = json.loads(
    Path("/app/data/deploy/snapshot_manifest.json").read_text(encoding="utf-8")
)
assert manifest["rows"] == expected, manifest
snapshot = pd.read_parquet("/app/data/deploy/trades_snapshot.parquet")
assert len(snapshot) == expected, {"snapshot_rows": len(snapshot), "expected": expected}
required = {"trade_id", "disclosure_date"}
assert required <= set(snapshot.columns), sorted(required - set(snapshot.columns))
PY
}

verify_marker() {
  local expected_presence="$1"
  [[ "$expected_presence" == "0" || "$expected_presence" == "1" ]] || return 2
  ssh "$DEPLOY_TARGET" \
    "docker exec -i '$E2E_CONTAINER_NAME' python - '$CODE_MARKER' '$expected_presence'" <<'PY'
from pathlib import Path
import sys

marker, expected_raw = sys.argv[1], sys.argv[2]
text = Path("/app/app/streamlit_app.py").read_text(encoding="utf-8")
actual = marker in text
expected = bool(int(expected_raw))
assert actual is expected, {"marker_present": actual, "expected": expected}
PY
}

log "Baseline-Code und 80-Zeilen-Snapshot deployen"
make_master 80
deploy_code
deploy_data
ssh "$DEPLOY_TARGET" \
  "curl -fsS 'http://127.0.0.1:$E2E_PORT/_stcore/health' | grep -qi ok"
verify_rows 80

log "Codeaenderung deployen und Marker pruefen"
"$E2E_PYTHON_BIN" - "$WORKTREE/app/streamlit_app.py" "$CODE_MARKER" <<'PY'
from pathlib import Path
import sys

path, marker = Path(sys.argv[1]), sys.argv[2]
text = path.read_text(encoding="utf-8")
needle = "US-Kongress-Trades · Live-Scoring"
if needle not in text:
    raise SystemExit("Subtitle-Anker fuer E2E-Code-Marker fehlt")
path.write_text(text.replace(needle, f"{needle} · {marker}", 1), encoding="utf-8")
PY
git -C "$WORKTREE" add app/streamlit_app.py
git -C "$WORKTREE" -c user.name='Deployment E2E' \
  -c user.email='deployment-e2e@local' commit -qm 'E2E: Code-Marker setzen'
deploy_code
verify_marker 1

log "Datenaenderung auf 81 Zeilen deployen"
make_master 81
deploy_data
verify_rows 81

log "Code auf Test-Baseline zurueckrollen"
git -C "$WORKTREE" revert --no-edit HEAD
deploy_code
verify_marker 0

log "Daten auf 80 Zeilen zurueckrollen"
make_master 80
deploy_data
verify_rows 80

log "E2E_OK: Code- und Datendeployment samt Rollback verifiziert"
