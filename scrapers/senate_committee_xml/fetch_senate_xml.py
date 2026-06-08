"""Senate-Committee-XML Fetch + Snapshot (Phase 1b, Variante A).

Lädt pro Committee-Code das strukturierte Membership-XML von senate.gov und legt
es als Snapshot unter raw/ ab. Die Snapshots werden committet → die Pipeline ist
deterministisch (Netz nur beim Refresh), analog zum bisherigen House-PDF.

senate.gov sitzt hinter Akamai (TLS-Fingerprint-Check) → plain curl/requests = 403.
curl_cffi mit Chrome-Impersonation umgeht das headless (kein Browser nötig).

Code-Liste = Discovery aus committees/membership_assignments.htm (Dropdown
"by committee"), 119. Kongress: 26 Committees. Namen liest der Parser autoritativ
aus <committee_name> im XML, nicht aus dieser Liste.
"""
from __future__ import annotations

import sys
from pathlib import Path

from curl_cffi import requests

RAW_DIR = Path(__file__).resolve().parent / "raw"
URL_TMPL = "https://www.senate.gov/general/committee_membership/committee_memberships_{code}.xml"

# Discovery-Snapshot (Dropdown "By committee", 119. Kongress). Reihenfolge wie im Dropdown.
COMMITTEE_CODES = [
    "SSAF", "SSAP", "SSAS", "SSBK", "SSCM", "SSEG", "SSEV", "SSFI", "SSFR",
    "SSHR", "SSGA", "SLIA", "SSRA", "SSSB", "SSBU", "SSJU", "SSVA",
    "JSPR", "JSTX", "JSLC", "JSEC", "SLET", "SLIN", "SPAG", "SCNC", "JCSE",
]


def fetch_one(code: str) -> str:
    """Lädt ein Committee-XML; wirft bei != 200 oder leerem Body."""
    url = URL_TMPL.format(code=code)
    r = requests.get(url, impersonate="chrome", timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"{code}: HTTP {r.status_code}")
    text = r.text
    if "<committee_membership>" not in text:
        raise RuntimeError(f"{code}: kein <committee_membership> im Body (len={len(text)})")
    return text


def fetch_all(raw_dir: Path = RAW_DIR) -> list[str]:
    """Lädt alle COMMITTEE_CODES nach raw/<code>.xml. Returnt die geschriebenen Codes."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for code in COMMITTEE_CODES:
        xml = fetch_one(code)
        (raw_dir / f"{code}.xml").write_text(xml, encoding="utf-8")
        written.append(code)
        print(f"  {code}: {len(xml)} bytes")
    return written


if __name__ == "__main__":
    print(f"Fetch {len(COMMITTEE_CODES)} Senate-Committee-XMLs → {RAW_DIR}")
    written = fetch_all()
    print(f"OK: {len(written)}/{len(COMMITTEE_CODES)} geschrieben")
    sys.exit(0)
