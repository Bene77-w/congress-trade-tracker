"""Zeilen-Ratchet: zaehlt Produktiv- und Testzeilen und vergleicht mit den Obergrenzen in CONSTRAINTS.md.

Aufruf: python tools/check_lines.py   Exit: 0 eingehalten, 1 Obergrenze ueberschritten, 2 Grenze nicht lesbar.
Produktivcode = alle versionierten *.py ausser tests/ und tools/.
"""
import re
import subprocess
import sys

files = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True, check=True).stdout.split()
counts = {"Produktivcode": 0, "Tests": 0}
for f in files:
    if f.startswith("tools/"):
        continue
    with open(f, encoding="utf-8", errors="replace") as fh:
        counts["Tests" if f.startswith("tests/") else "Produktivcode"] += sum(1 for _ in fh)

text = open("CONSTRAINTS.md", encoding="utf-8").read()
status = 0
for name, value in counts.items():
    m = re.search(rf"^\|\s*{name}\s*\|\s*≤\s*(\d+)", text, re.M)
    if not m:
        print(f"check-lines: Obergrenze fuer {name} in CONSTRAINTS.md nicht gefunden", file=sys.stderr)
        sys.exit(2)
    limit = int(m.group(1))
    if value > limit:
        print(f"check-lines: {name} {value} > {limit} (Obergrenze ueberschritten)", file=sys.stderr)
        status = 1
    else:
        hint = " -> Obergrenze in CONSTRAINTS.md auf den neuen Stand senken" if value < limit else ""
        print(f"check-lines: {name} {value} <= {limit}{hint}")
sys.exit(status)
