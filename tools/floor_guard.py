"""Floor-Guard: prueft den Diff gegen eine Basis auf Zuege, die die Grundregeln aus CONSTRAINTS.md senken.

Aufruf: python tools/floor_guard.py [--base <ref>]   (Standard: Tag sanierung-anker)
Exit: 0 sauber, 1 Verstoss (blockiert), 2 Guard konnte nicht laufen.

Portiert aus der Referenz des Skills constraint-driven-development (references/floor-guard.md).
Geloeschte Tests und entfernte Asserts sind erlaubt, wenn eine Commit-Nachricht seit der Basis
eine Zeile `Tests-entfernt: <pfad-oder-glob> <Grund>` enthaelt, die die Datei abdeckt.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
import sys

SUPPRESSIONS = re.compile(r"#\s*noqa|#\s*type:\s*ignore|pragma:\s*no cover|#\s*nosec|pylint:\s*disable|nosemgrep")
STUBS = re.compile(r"raise NotImplementedError|except[^:]*:\s*pass\b|\bTODO\b|\bFIXME\b")
SKIPS = re.compile(r"pytest\.mark\.(skip|xfail)|pytest\.skip\(|unittest\.skip|pytest\.importorskip")
ASSERTS = re.compile(r"\bassert\b|pytest\.raises|assert_called|assert_frame_equal")
MIN_BEFORE = re.compile(r"(>=|>|≥|mindestens|min\.?|nicht fallen|nicht sinken)\s*$")
MAX_BEFORE = re.compile(r"(<=|<|≤|höchstens|max\.?|unter|nicht wachsen|nicht steigen)\s*$")


def git(*args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
    res = subprocess.run(["git", *args], capture_output=True, text=True)
    if res.returncode not in ok_codes:
        print(f"floor-guard: git {' '.join(args)} fehlgeschlagen: {res.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return res.stdout


def parse_diff(diff: str):
    added, removed, deleted = [], [], []
    path = old = ""
    header = False
    for line in diff.splitlines():
        if line.startswith("diff "):
            header = True
        elif line.startswith("@@"):
            header = False
        elif header:
            if line.startswith("--- "):
                old = re.sub(r"^[ab]/", "", line[4:])
            elif line.startswith("+++ "):
                new = re.sub(r"^[ab]/", "", line[4:])
                path = old if new == "/dev/null" else new
                if new == "/dev/null":
                    deleted.append(path)
        elif line.startswith("+"):
            added.append((path, line[1:]))
        elif line.startswith("-"):
            removed.append((path, line[1:]))
    return added, removed, deleted


def rule_key(text: str):
    s = text.strip()
    if s.startswith("|"):
        cells = [c.strip() for c in s.split("|") if c.strip()]
        return cells[0] if cells and not set(cells[0]) <= set("-: ") else None
    if s[:2] in ("- ", "* "):
        return s[2:].split(":")[0].strip()
    return None


def thresholds(text: str):
    out = []
    for m in re.finditer(r"\d+(?:[.,]\d+)?", text):
        before = text[max(0, m.start() - 24):m.start()].lower()
        direction = "min" if MIN_BEFORE.search(before) else "max" if MAX_BEFORE.search(before) else None
        out.append((float(m.group().replace(",", ".")), direction))
    return out


def main() -> int:
    base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "sanierung-anker"
    top = git("rev-parse", "--show-toplevel").strip()
    merge_base = git("-C", top, "merge-base", base, "HEAD").strip()
    diff = git("-C", top, "diff", "--unified=0", merge_base, "--")
    for f in filter(None, git("-C", top, "ls-files", "--others", "--exclude-standard").splitlines()):
        diff += "\n" + git("-C", top, "diff", "--no-index", "--unified=0", "/dev/null", f, ok_codes=(0, 1))
    added, removed, deleted = parse_diff(diff)

    log = git("-C", top, "log", "--format=%B", f"{merge_base}..HEAD")
    allowed = [ln.split(":", 1)[1].split()[0] for ln in log.splitlines()
               if ln.startswith("Tests-entfernt:") and ln.split(":", 1)[1].split()]
    covered = lambda f: any(fnmatch.fnmatch(f, g) for g in allowed)
    is_test = lambda f: f.startswith("tests/")
    is_constraints = lambda f: f.endswith("CONSTRAINTS.md")
    is_self = lambda f: f == "tools/floor_guard.py"

    findings = []
    for f, text in added:
        if not f.endswith(".py") or is_self(f):
            continue
        if SUPPRESSIONS.search(text):
            findings.append(("checker-stummgeschaltet", f, text))
        if STUBS.search(text):
            findings.append(("unfertige-arbeit", f, text))
        if SKIPS.search(text):
            findings.append(("test-erleichtert", f, text))
    for f, text in added:
        if is_constraints(f) and re.match(r"^\|\s*W\d+\s*\|", text.strip()):
            findings.append(("neue-ausnahme", f, text))
    for f in deleted:
        if is_test(f) and not covered(f):
            findings.append(("test-geloescht", f, "Datei geloescht ohne Tests-entfernt-Zeile"))
    for f, text in removed:
        if is_test(f) and f not in deleted and ASSERTS.search(text) and not covered(f):
            findings.append(("assert-entfernt", f, text))

    rem_rules = [(f, t) for f, t in removed if is_constraints(f) and rule_key(t)]
    add_rules = [(f, t) for f, t in added if is_constraints(f) and rule_key(t)]
    for f, old in rem_rules:
        new = next((t for _, t in add_rules if rule_key(t) == rule_key(old)), None)
        if new is None:
            if not re.match(r"^\|\s*W\d+\s*\|", old.strip()):
                findings.append(("regel-entfernt", f, old))
            continue
        was, now = thresholds(old), thresholds(new)
        for direction in ("min", "max", None):
            a = [n for n, d in was if d == direction]
            b = [n for n, d in now if d == direction]
            for i, x in enumerate(a):
                if i >= len(b):
                    findings.append(("schwelle-entfernt", f, f"{old} -> {new}"))
                elif b[i] != x and (direction is None or (b[i] < x if direction == "min" else b[i] > x)):
                    findings.append(("schwelle-gelockert", f, f"{old.strip()} -> {new.strip()}"))

    if not findings:
        print("floor-guard: sauber")
        return 0
    print(f"floor-guard: {len(findings)} Verstoss/Verstoesse:", file=sys.stderr)
    for rule, f, text in findings:
        print(f"  [{rule}] {f}: {text.strip()[:120]}", file=sys.stderr)
    print("\nCode korrigieren oder ueber eine besprochene Ausnahme in CONSTRAINTS.md fuehren.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
