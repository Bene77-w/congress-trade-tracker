#!/usr/bin/env bash
# Prueflauf am Ticketende (CONSTRAINTS.md). Aufruf: tools/check.sh [--base <ref>]  (Standard: sanierung-anker)
# Exit 1, wenn eine blockierende Pruefung scheitert; Warnungen werden nur ausgegeben.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
BASE=sanierung-anker; [[ "${1:-}" == "--base" ]] && BASE="$2"
VENV="${VENV:-/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/venv/bin}"
WARN_BIS=2026-12-04   # bis dahin warnen Zeilenbudget und Architekturgrenzen nur (CONSTRAINTS.md)
fail=0; warn() { echo "WARNUNG: $*"; }
soft() { if [[ "$(date +%F)" < "$WARN_BIS" ]]; then warn "$1 (blockiert ab $WARN_BIS)"; else echo "FEHLER: $1"; fail=1; fi; }

"$VENV/python" tools/floor_guard.py --base "$BASE" || fail=1

mapfile -t changed < <(git diff --name-only --diff-filter=AM "$(git merge-base "$BASE" HEAD)" -- ; git ls-files --others --exclude-standard)
if ((${#changed[@]})); then
  "$VENV/detect-secrets" scan "${changed[@]}" | "$VENV/python" -c '
import json, sys
r = json.load(sys.stdin)["results"]
for f, hits in r.items():
    for h in hits: print(f"secrets: {h[\"type\"]} in {f}:{h[\"line_number\"]}", file=sys.stderr)
sys.exit(1 if r else 0)' || fail=1
fi

"$VENV/python" tools/check_lines.py || soft "Zeilenbudget ueberschritten"
"$VENV/lint-imports" --no-logo --contract app-grenze --contract keine-app-importe >/dev/null 2>&1 \
  || soft "Architekturgrenze verletzt (lint-imports --contract app-grenze --contract keine-app-importe)"
"$VENV/lint-imports" --no-logo --contract signale-unabhaengig --contract netz-nur-in-adaptern >/dev/null 2>&1 \
  || warn "gemessene Grenzen noch verletzt (signale-unabhaengig, netz-nur-in-adaptern)"

"$VENV/python" -m pytest -q -p no:cacheprovider || fail=1
exit $fail
