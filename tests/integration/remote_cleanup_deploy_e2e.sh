#!/usr/bin/env bash
# Wird vom E2E-Test per SSH/stdin auf dem VPS ausgefuehrt.
set -uo pipefail

mode="${1:-}"
remote_path="${2:-}"
remote_parent="${3:-}"
project_name="${4:-}"
container_name="${5:-}"
image_name="${6:-}"

if [[ "$remote_parent" =~ ^/[A-Za-z0-9._/-]+/congress-tracker-e2e-([0-9]{14}-[0-9]+)$ \
      && "$remote_parent" != *"//"* \
      && "$remote_parent" != *"/./"* \
      && "$remote_parent" != *"/../"* \
      && "$remote_parent" != *".."* ]]; then
  scope_id="${BASH_REMATCH[1]}"
elif [[ "$remote_parent" =~ ^/tmp/congress-tracker-e2e-unit-([A-Za-z0-9._-]+)$ ]]; then
  scope_id="unit-${BASH_REMATCH[1]}"
else
  exit 2
fi
[[ "$remote_path" == "$remote_parent/congress-tracker" ]] || exit 2
[[ "$project_name" == "congress-tracker-e2e-$scope_id" ]] || exit 2
[[ "$container_name" == "congress-dashboard-e2e-$scope_id" ]] || exit 2
[[ "$image_name" == "congress-tracker-dashboard-e2e-$scope_id" ]] || exit 2
docker info >/dev/null 2>&1 || exit 1

verify_empty() {
  local ids
  [[ ! -e "$remote_parent" ]] || return 1
  ids="$(docker ps -aq --filter "name=^/${container_name}$")" || return 1
  [[ -z "$ids" ]] || return 1
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=$project_name")" \
    || return 1
  [[ -z "$ids" ]] || return 1
  ids="$(docker network ls -q --filter "label=com.docker.compose.project=$project_name")" \
    || return 1
  [[ -z "$ids" ]] || return 1
  ids="$(docker volume ls -q --filter "label=com.docker.compose.project=$project_name")" \
    || return 1
  [[ -z "$ids" ]] || return 1
  ids="$(docker image ls -q --filter "reference=$image_name")" || return 1
  [[ -z "$ids" ]]
}

if [[ "$mode" == "verify" ]]; then
  verify_empty
  exit $?
fi
[[ "$mode" == "cleanup" ]] || exit 2

cleanup_rc=0
if [[ -f "$remote_path/deploy/docker-compose.prod.yml" \
      && -f "$remote_path/deploy/.env" ]]; then
  (cd "$remote_path" && docker compose -p "$project_name" \
    -f deploy/docker-compose.prod.yml --env-file deploy/.env \
    down --volumes --remove-orphans) || cleanup_rc=1
fi

ids="$(docker ps -aq --filter "name=^/${container_name}$")" || cleanup_rc=1
[[ -z "$ids" ]] || docker rm -f $ids >/dev/null || cleanup_rc=1
ids="$(docker ps -aq --filter "label=com.docker.compose.project=$project_name")" \
  || cleanup_rc=1
[[ -z "$ids" ]] || docker rm -f $ids >/dev/null || cleanup_rc=1
ids="$(docker network ls -q --filter "label=com.docker.compose.project=$project_name")" \
  || cleanup_rc=1
[[ -z "$ids" ]] || docker network rm $ids >/dev/null || cleanup_rc=1
ids="$(docker volume ls -q --filter "label=com.docker.compose.project=$project_name")" \
  || cleanup_rc=1
[[ -z "$ids" ]] || docker volume rm $ids >/dev/null || cleanup_rc=1
ids="$(docker image ls -q --filter "reference=$image_name")" || cleanup_rc=1
[[ -z "$ids" ]] || docker image rm -f -- "$image_name" >/dev/null || cleanup_rc=1

if [[ -f "$remote_parent/e2e-image-ids" ]]; then
  if ledger_ids="$(sort -u "$remote_parent/e2e-image-ids")"; then
    while IFS= read -r image_id; do
      [[ -z "$image_id" ]] && continue
      [[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]] || { cleanup_rc=1; continue; }
      all_image_ids="$(docker image ls -aq --no-trunc)" \
        || { cleanup_rc=1; continue; }
      image_exists=0
      while IFS= read -r listed_id; do
        [[ "$listed_id" == "$image_id" ]] && image_exists=1
      done <<< "$all_image_ids"
      if (( image_exists )); then
        tags="$(docker image inspect --format '{{range .RepoTags}}{{println .}}{{end}}' \
          "$image_id")" || { cleanup_rc=1; continue; }
        if [[ -n "$tags" ]]; then
          echo "E2E-Image-ID traegt fremde Tags und bleibt erhalten: $image_id $tags" >&2
          cleanup_rc=1
        else
          docker image rm -f -- "$image_id" >/dev/null || cleanup_rc=1
        fi
      fi
    done <<< "$ledger_ids"
  else
    cleanup_rc=1
  fi
fi

if (( cleanup_rc == 0 )); then
  rm -rf -- "$remote_parent" || cleanup_rc=1
else
  echo "Cleanup unvollstaendig; Ledger bleibt erhalten: $remote_parent" >&2
fi
exit "$cleanup_rc"
