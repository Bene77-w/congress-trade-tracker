#!/usr/bin/env bash
# Read-only Altlastencheck, vom E2E-Test per SSH/stdin ausgefuehrt.
set -euo pipefail

docker info >/dev/null 2>&1
shopt -s nullglob
remote_base="${1:-}"
[[ "$remote_base" =~ ^/[A-Za-z0-9._/-]+$ \
   && "$remote_base" != "/" \
   && "$remote_base" != */ \
   && "$remote_base" != *"//"* \
   && "$remote_base" != *"/./"* \
   && "$remote_base" != *"/." \
   && "$remote_base" != *"/../"* \
   && "$remote_base" != *".."* ]] || exit 2
paths=("$remote_base"/congress-tracker-e2e-*)
all_containers="$(docker ps -a --format '{{.Names}}')"
all_images="$(docker image ls --format '{{.Repository}}:{{.Tag}}')"
all_networks="$(docker network ls --format '{{.Name}} {{.Labels}}')"
all_volumes="$(docker volume ls --format '{{.Name}} {{.Labels}}')"
containers=""; images=""; networks=""; volumes=""
while IFS= read -r line; do
  [[ "$line" == congress-dashboard-e2e-* ]] && containers+="$line"$'\n'
done <<< "$all_containers"
while IFS= read -r line; do
  [[ "$line" == congress-tracker-dashboard-e2e-* ]] && images+="$line"$'\n'
done <<< "$all_images"
while IFS= read -r line; do
  [[ "$line" == *com.docker.compose.project=congress-tracker-e2e-* ]] \
    && networks+="$line"$'\n'
done <<< "$all_networks"
while IFS= read -r line; do
  [[ "$line" == *com.docker.compose.project=congress-tracker-e2e-* ]] \
    && volumes+="$line"$'\n'
done <<< "$all_volumes"
if (( ${#paths[@]} )) || [[ -n "$containers$images$networks$volumes" ]]; then
  echo "Verwaiste Deployment-E2E-Ressourcen gefunden; vor neuem Lauf bereinigen." >&2
  printf 'Pfade: %s\nContainer: %s\nImages: %s\nNetzwerke: %s\nVolumes: %s\n' \
    "${paths[*]:-}" "$containers" "$images" "$networks" "$volumes" >&2
  exit 1
fi
