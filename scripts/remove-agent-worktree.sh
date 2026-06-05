#!/usr/bin/env bash
# Entfernt einen Agent-Worktree und (optional) seine Branch.
#   scripts/remove-agent-worktree.sh <name> [--force] [--keep-branch]
#
# Zu --force: Ein per new-agent-worktree.sh angelegter Worktree enthält IMMER
# untracked Dateien (die Daten-Kopien + die Symlinks). Deshalb verweigert
# 'git worktree remove' hier praktisch immer ohne --force. Das Skript probiert
# daher zuerst OHNE Gewalt (Schutz vor Datenverlust) und sagt dir, falls du
# bewusst --force brauchst. Sichere/committe echte Arbeit ZUERST.
set -euo pipefail

NAME=""; FORCE=0; KEEP_BRANCH=0
for arg in "$@"; do
  case "$arg" in
    --force)       FORCE=1 ;;
    --keep-branch) KEEP_BRANCH=1 ;;
    --*) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
    *)   NAME="$arg" ;;
  esac
done
: "${NAME:?Name fehlt — Aufruf: remove-agent-worktree.sh <name> [--force] [--keep-branch]}"

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PARENT="$(dirname "$REPO")"; BASENAME="$(basename "$REPO")"
WT="$PARENT/${BASENAME}-agent-$NAME"
BRANCH="agent/$NAME"

echo "Entferne Worktree: $WT"
if [[ "$FORCE" == 1 ]]; then
  git -C "$REPO" worktree remove --force "$WT"
else
  # Erst sanft versuchen. Schlägt es wegen untracked/geänderter Dateien fehl,
  # NICHT automatisch forcieren — der Mensch entscheidet bewusst.
  if ! git -C "$REPO" worktree remove "$WT"; then
    echo >&2
    echo "Abbruch: Der Worktree enthält untracked/geänderte Dateien." >&2
    echo "Das ist bei provisionierten Worktrees normal (Kopien + Symlinks)." >&2
    echo "Sichere/committe echte Arbeit, dann bewusst erzwingen:" >&2
    echo "    scripts/remove-agent-worktree.sh $NAME --force" >&2
    exit 1
  fi
fi
git -C "$REPO" worktree prune

if [[ "$KEEP_BRANCH" == 0 ]]; then
  git -C "$REPO" branch -d "$BRANCH" \
    || echo "Hinweis: Branch $BRANCH nicht gelöscht (ungemergt?). Bewusst mit 'git branch -D $BRANCH'."
fi
echo "FERTIG."
