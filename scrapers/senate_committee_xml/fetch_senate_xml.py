"""Senate-Committee-XML Fetch + Snapshot + Discovery (Phase 1b, Variante A).

Lädt pro Committee das strukturierte Membership-XML von senate.gov und legt es als
Snapshot unter raw/ ab. Snapshots werden committet → die Pipeline ist deterministisch
(Netz nur beim Refresh), analog zum House-PDF.

KEIN Hardcoding der Committees: die Liste wird bei jedem Lauf dynamisch aus dem
Dropdown der membership_assignments.htm-Seite ermittelt (Discovery). Damit passt sich
der Scraper automatisch an jeden neuen Kongress an. Ein Manifest (raw/_manifest.json)
hält die zuletzt gesehene Liste; Abweichungen werden als Change-Report geloggt
(+ NEU / − ENTFALLEN), sodass ein Kongress-Wechsel sofort sichtbar ist.

senate.gov sitzt hinter Akamai (TLS-Fingerprint-Check) → plain curl/requests = 403.
curl_cffi mit Chrome-Impersonation umgeht das headless (kein Browser nötig).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

RAW_DIR = Path(__file__).resolve().parent / "raw"
MANIFEST = RAW_DIR / "_manifest.json"
DISCOVERY_URL = "https://www.senate.gov/committees/membership_assignments.htm"
XML_URL_TMPL = "https://www.senate.gov/general/committee_membership/committee_memberships_{code}.xml"

# Untergrenze als Sanity-Gate: bricht laut ab, falls die Discovery-Seite kaputt/leer
# ist, statt still mit einer Mülliste weiterzulaufen (119. Kongress = 26 Committees).
MIN_EXPECTED_COMMITTEES = 10


def _get(url: str) -> str:
    r = requests.get(url, impersonate="chrome", timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"{url}: HTTP {r.status_code}")
    return r.text


def discover_committee_codes() -> list[tuple[str, str]]:
    """Ermittelt (code, name) aller Committees aus dem 'By committee'-Dropdown.

    Reihenfolge wie im Dropdown, dedupliziert. Wirft bei < MIN_EXPECTED_COMMITTEES.
    """
    html = _get(DISCOVERY_URL)
    # <option value="/general/committee_membership/committee_memberships_SSAF.htm">Committee on …</option>
    pattern = re.compile(
        r'committee_memberships_([A-Z0-9]+)\.htm[^"]*"[^>]*>([^<]*)</option>',
        re.IGNORECASE,
    )
    seen: dict[str, str] = {}
    for code, name in pattern.findall(html):
        code = code.strip().upper()
        if code not in seen:
            seen[code] = name.strip()
    codes = list(seen.items())
    if len(codes) < MIN_EXPECTED_COMMITTEES:
        raise RuntimeError(
            f"Discovery lieferte nur {len(codes)} Committees (< {MIN_EXPECTED_COMMITTEES}) "
            f"— Seite/Layout vermutlich geändert: {DISCOVERY_URL}")
    return codes


def _load_prev_codes() -> set[str]:
    if not MANIFEST.exists():
        return set()
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        return {c["code"] for c in data.get("committees", [])}
    except (json.JSONDecodeError, KeyError):
        return set()


def _report_changes(prev: set[str], current: list[tuple[str, str]]) -> None:
    cur_codes = {c for c, _ in current}
    added = [(c, n) for c, n in current if c not in prev]
    removed = sorted(prev - cur_codes)
    if not prev:
        print("  (kein vorheriges Manifest — Erst-Discovery)")
        return
    if not added and not removed:
        print("  Committee-Liste unverändert ggü. letztem Lauf")
        return
    for c, n in added:
        print(f"  + NEU:       {c}  {n}")
    for c in removed:
        print(f"  - ENTFALLEN: {c}")


def _write_manifest(current: list[tuple[str, str]]) -> None:
    MANIFEST.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": DISCOVERY_URL,
        "committees": [{"code": c, "name": n} for c, n in current],
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_one(code: str) -> str:
    """Lädt ein Committee-XML; wirft bei != 200 oder leerem/falschem Body."""
    text = _get(XML_URL_TMPL.format(code=code))
    if "<committee_membership>" not in text:
        raise RuntimeError(f"{code}: kein <committee_membership> im Body (len={len(text)})")
    return text


def fetch_all(raw_dir: Path = RAW_DIR) -> list[str]:
    """Discovery + Fetch aller aktuellen Committees nach raw/<code>.xml.

    Schreibt Manifest + Change-Report. Returnt die geschriebenen Codes.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    current = discover_committee_codes()
    print(f"Discovery: {len(current)} Committees")
    _report_changes(_load_prev_codes(), current)
    written = []
    for code, _name in current:
        (raw_dir / f"{code}.xml").write_text(fetch_one(code), encoding="utf-8")
        written.append(code)
    _write_manifest(current)
    return written


if __name__ == "__main__":
    print(f"Fetch Senate-Committee-XMLs → {RAW_DIR}")
    written = fetch_all()
    print(f"OK: {len(written)} XMLs geschrieben")
    sys.exit(0)
