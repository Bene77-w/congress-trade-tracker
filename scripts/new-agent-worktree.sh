#!/usr/bin/env bash
# Legt einen Agent-Worktree an und provisioniert vitale, gitignorierte Dateien.
#   read-only Inputs (venv/.env/data/raw) -> Symlink (teilen)
#   schreibbarer Zustand (Master/Cache)   -> Kopie via cp --reflink=auto (isolieren)
#
# Nutzung:
#   scripts/new-agent-worktree.sh <name> [base-branch]        # isoliert (Default)
#   scripts/new-agent-worktree.sh --shared <name> [base]      # 1 Worktree für mehrere Agents
#   scripts/new-agent-worktree.sh --share-data <name> [base]  # Master/Cache teilen statt kopieren
#
# HINWEIS: Diese Worktrees sind für Agenten gedacht, die AUF DEM HOST laufen
# (Claude Code, Host-opencode). Für den DeepSeek-Docker-Agenten NICHT geeignet —
# dafür gibt es ~/opencode-sandbox/deepseek-clone.sh (eigenständiger Clone).
set -euo pipefail

SHARED=0; SHARE_DATA=0
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --shared)     SHARED=1 ;;
    --share-data) SHARE_DATA=1 ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
  shift
done

NAME="${1:?Name fehlt — Aufruf: new-agent-worktree.sh [--shared|--share-data] <name> [base]}"
BASE="${2:-main}"

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PARENT="$(dirname "$REPO")"; BASENAME="$(basename "$REPO")"
WT="$PARENT/${BASENAME}-agent-$NAME"
BRANCH="agent/$NAME"

LINK_PATHS=( "venv" ".env" "data/raw" )                                   # -> Symlink
COPY_PATHS=( "data/cache/price_history.parquet" \
             "data/processed/trades_master_complete.csv" )               # -> Kopie

echo "Repo:     $REPO"
echo "Worktree: $WT"
echo "Branch:   $BRANCH  (Basis: $BASE)"
[[ "$SHARED" == 1 ]] && echo "Modus:    SHARED (mehrere Agents, ein Worktree)"

# --- 1. Worktree + Branch -------------------------------------------------
if [[ -d "$WT" ]]; then
  if [[ "$SHARED" == 1 ]]; then
    echo "Shared-Worktree existiert bereits — wird wiederverwendet."
  else
    echo "FEHLER: Verzeichnis existiert schon: $WT" >&2; exit 1
  fi
elif git -C "$REPO" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git -C "$REPO" worktree add "$WT" "$BRANCH"            # Branch existiert -> auschecken
else
  git -C "$REPO" worktree add "$WT" -b "$BRANCH" "$BASE" # neue Branch von BASE
fi

# --- 2. Read-only Inputs symlinken (idempotent, nur Vorhandenes) ----------
for p in "${LINK_PATHS[@]}"; do
  if [[ -e "$WT/$p" ]]; then echo "  vorhanden $p"; continue; fi
  if [[ -e "$REPO/$p" ]]; then
    mkdir -p "$(dirname "$WT/$p")"
    ln -s "$REPO/$p" "$WT/$p"; echo "  symlink   $p"
  else
    echo "  (fehlt)   $p — übersprungen"
  fi
done

# --- 3. Schreibbaren Zustand bereitstellen --------------------------------
for p in "${COPY_PATHS[@]}"; do
  if [[ -e "$WT/$p" ]]; then echo "  vorhanden $p"; continue; fi
  if [[ -e "$REPO/$p" ]]; then
    mkdir -p "$(dirname "$WT/$p")"
    if [[ "$SHARE_DATA" == 1 ]]; then
      ln -s "$REPO/$p" "$WT/$p"; echo "  symlink*  $p (GETEILT — Vorsicht bei Schreibzugriff)"
    else
      cp --reflink=auto "$REPO/$p" "$WT/$p"; echo "  kopiert   $p"
    fi
  else
    echo "  (fehlt)   $p — übersprungen"
  fi
done

# --- 4. Shared-Leitplanke -------------------------------------------------
if [[ "$SHARED" == 1 && ! -f "$WT/SHARED_WORKTREE.md" ]]; then
  cat > "$WT/SHARED_WORKTREE.md" <<'EOF'
# Shared Worktree — mehrere Agents in EINEM Verzeichnis

git hat hier nur EINEN Index und EIN HEAD. Damit zwei Agents kollisionsfrei
arbeiten, gelten ZWEI Regeln:

  1. Jeder Agent fasst NUR seine eigenen, DISJUNKTEN Dateien an.
  2. git add / commit macht EINE Instanz — am besten der Mensch, nicht die Agents.

Nur für kosmetische / winzige Änderungen gedacht.
Für echte Parallelarbeit: je Agent ein eigener (isolierter) Worktree.
EOF
  echo "  Leitplanke geschrieben: SHARED_WORKTREE.md"
fi

echo
echo "FERTIG. Agent starten mit:"
echo "  cd \"$WT\" && claude     # oder: opencode"
