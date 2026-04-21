"""
Congress.gov API Data Fetcher
==============================
Fetcht alle Daten die für Filter 4A (Vote Timing) und 4B (Committee Activity) benötigt werden.

Vote-Daten (Floor Votes) werden aus Bill Actions extrahiert — kein separater Vote-Endpoint nötig.

Laufzeit: ~4-5 Stunden (abhängig von Rate Limit)
Rate Limit: 5.000 Requests/Stunde (mit API Key)

Verwendung:
    Erstlauf (~7-8h, einmalig pro Kongress):
        python scrapers/fetch_congress_data.py

    Delta-Update (~5-30 Min, wöchentlich):
        python scrapers/fetch_congress_data.py --update

    Erstlauf wiederholen (ignoriert completed_steps):
        python scrapers/fetch_congress_data.py --force

    Bei Abbruch: Script erneut starten — setzt via Checkpoint-System fort.

Output:
    data/congress_api/bills_list.json        — Alle Bills (IDs, Typen)
    data/congress_api/bill_details.json      — policyArea, title, sponsors pro Bill
    data/congress_api/bill_actions.json      — Actions pro Bill (inkl. Floor Votes!)
    data/congress_api/bill_committees.json   — Committee-Zuweisungen pro Bill
    data/congress_api/bill_summaries.json    — CRS Summaries pro Bill
    data/congress_api/bill_subjects.json     — Legislative Subjects pro Bill (Rider-Erkennung)
    data/congress_api/fetch_checkpoint.json  — Checkpoint für Resume + Delta-Update
    data/congress_api/fetch_log.json         — Fortschritt + Fehler-Log
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# .env aus Projekt-Root laden
load_dotenv()

# =============================================================================
# KONFIGURATION
# =============================================================================

CONGRESS = 119  # 119th Congress (2025-2027)
BASE_URL = "https://api.congress.gov/v3"
OUTPUT_DIR = Path("data/congress_api")
CHECKPOINT_FILE = OUTPUT_DIR / "fetch_checkpoint.json"

# Rate Limit: 5000/Stunde = ~1.38/Sekunde → 0.75s Pause = sicher
REQUEST_DELAY = 0.75  # Sekunden zwischen Requests
MAX_RETRIES = 3
RETRY_DELAY = 5  # Sekunden bei Fehler

# API Pagination
PAGE_LIMIT = 250  # Max pro Request (Congress.gov Maximum)


# =============================================================================
# HELPER
# =============================================================================

class CongressFetcher:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.request_count = 0
        self.errors = []
        self.checkpoint = self._load_checkpoint()

    def _load_checkpoint(self) -> dict:
        if CHECKPOINT_FILE.exists():
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                cp = json.load(f)
                print(f"[CHECKPOINT] Fortgesetzt: {cp.get('last_step', '?')} "
                      f"({cp.get('requests_total', 0)} bisherige Requests)")
                return cp
        return {"completed_steps": [], "requests_total": 0}

    def _save_checkpoint(self, step: str, extra: dict = None):
        self.checkpoint["last_step"] = step
        self.checkpoint["last_update"] = datetime.now(timezone.utc).isoformat()
        self.checkpoint["requests_total"] = self.request_count
        if extra:
            self.checkpoint.update(extra)
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(self.checkpoint, f, indent=2)

    def _mark_step_done(self, step: str):
        if step not in self.checkpoint.get("completed_steps", []):
            self.checkpoint.setdefault("completed_steps", []).append(step)
        self._save_checkpoint(step)

    def _is_step_done(self, step: str) -> bool:
        return step in self.checkpoint.get("completed_steps", [])

    def _fetch(self, endpoint: str, params: dict = None) -> dict | None:
        """Einzelner API Request mit Retry-Logik."""
        if params is None:
            params = {}
        params["api_key"] = self.api_key
        params["format"] = "json"

        url = f"{BASE_URL}{endpoint}"

        for attempt in range(MAX_RETRIES):
            try:
                time.sleep(REQUEST_DELAY)
                resp = self.session.get(url, params=params, timeout=30)
                self.request_count += 1

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    # Rate Limit erreicht — länger warten
                    wait = 60 * (attempt + 1)
                    print(f"  [RATE LIMIT] Warte {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  [HTTP {resp.status_code}] {endpoint}")
                    if attempt == MAX_RETRIES - 1:
                        self.errors.append({
                            "endpoint": endpoint,
                            "status": resp.status_code,
                            "time": datetime.now(timezone.utc).isoformat()
                        })
                        return None

            except requests.RequestException as e:
                print(f"  [ERROR] {endpoint}: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                else:
                    self.errors.append({
                        "endpoint": endpoint,
                        "error": str(e),
                        "time": datetime.now(timezone.utc).isoformat()
                    })
                    return None

        return None

    def _fetch_paginated(self, endpoint: str, result_key: str,
                         params: dict = None, max_pages: int = 100) -> list:
        """Paginiert durch alle Ergebnisse eines Endpoints."""
        all_results = []
        offset = 0

        for page in range(max_pages):
            p = dict(params or {})
            p["offset"] = offset
            p["limit"] = PAGE_LIMIT

            data = self._fetch(endpoint, p)
            if data is None:
                break

            items = data.get(result_key, [])
            if not items:
                break

            all_results.extend(items)
            offset += PAGE_LIMIT

            # Fortschritt
            total = data.get("pagination", {}).get("count", "?")
            print(f"  {endpoint}: {len(all_results)}/{total} "
                  f"(Request #{self.request_count})", end="\r")

            # Keine weitere Seite?
            if len(items) < PAGE_LIMIT:
                break

        print()  # Newline nach \r
        return all_results

    def _fetch_legislative_subjects(self, bill_type: str, number: str) -> list:
        """Fetcht alle legislative_subjects eines Bills (paginiert).

        Der Subjects-Endpoint verschachtelt die Daten unter subjects.legislativeSubjects,
        daher kann _fetch_paginated (Top-Level-Key) nicht direkt verwendet werden.
        """
        endpoint = f"/bill/{CONGRESS}/{bill_type}/{number}/subjects"
        all_subjects = []
        offset = 0

        for _ in range(10):  # Safety limit — kein Bill hat >2500 Subjects
            data = self._fetch(endpoint, {"offset": offset, "limit": PAGE_LIMIT})
            if not data:
                break

            items = data.get("subjects", {}).get("legislativeSubjects", [])
            if not items:
                break

            all_subjects.extend(items)

            if len(items) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT

        return [s.get("name", "") for s in all_subjects if s.get("name", "")]

    def _save_json(self, filename: str, data: any):
        """Speichert Daten als JSON."""
        filepath = OUTPUT_DIR / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        size_mb = filepath.stat().st_size / 1024 / 1024
        print(f"  → Gespeichert: {filepath} ({size_mb:.1f} MB)")

    # =================================================================
    # SCHRITT 1: BILLS (Übersicht)
    # =================================================================

    def fetch_bills(self):
        """Fetcht alle Bills des aktuellen Kongresses mit Basis-Infos."""
        step = "bills_list"
        if self._is_step_done(step):
            print(f"[SKIP] {step} bereits abgeschlossen")
            return

        print(f"\n{'='*60}")
        print(f"SCHRITT 1: Bills (Übersicht)")
        print(f"{'='*60}")

        all_bills = []
        for bill_type in ["hr", "s", "hjres", "sjres"]:
            print(f"\n  Typ: {bill_type}")
            bills = self._fetch_paginated(
                f"/bill/{CONGRESS}/{bill_type}",
                result_key="bills"
            )
            for b in bills:
                b["bill_type"] = bill_type
            all_bills.extend(bills)

            # Checkpoint nach jedem Typ
            self._save_checkpoint(f"bills_list_{bill_type}",
                                  {"bills_fetched": len(all_bills)})

        self._save_json("bills_list.json", all_bills)
        print(f"\n  Total: {len(all_bills)} Bills gefetcht")

        self._mark_step_done(step)

    # =================================================================
    # SCHRITT 2: BILL DETAILS (policyArea, committees, title)
    # =================================================================

    def fetch_bill_details(self):
        """Fetcht Details (policyArea, title, sponsors) für jeden Bill."""
        step = "bill_details"
        if self._is_step_done(step):
            print(f"[SKIP] {step} bereits abgeschlossen")
            return

        print(f"\n{'='*60}")
        print(f"SCHRITT 2: Bill Details (policyArea, committees)")
        print(f"{'='*60}")

        # Bills-Liste laden
        bills_file = OUTPUT_DIR / "bills_list.json"
        if not bills_file.exists():
            print("[ERROR] bills_list.json nicht gefunden. Erst Schritt 2 ausführen.")
            return

        with open(bills_file, "r", encoding="utf-8") as f:
            bills = json.load(f)

        # Bereits gefetchte Details laden (für Resume)
        details_file = OUTPUT_DIR / "bill_details.json"
        existing_details = {}
        if details_file.exists():
            with open(details_file, "r", encoding="utf-8") as f:
                for d in json.load(f):
                    key = f"{d.get('bill_type', '')}-{d.get('number', '')}"
                    existing_details[key] = d

        print(f"  {len(bills)} Bills total, {len(existing_details)} bereits gefetcht")

        details = list(existing_details.values())
        save_interval = 100  # Alle 100 Bills zwischenspeichern

        for i, bill in enumerate(bills):
            bill_type = bill.get("bill_type", bill.get("type", "")).lower()
            number = bill.get("number")
            key = f"{bill_type}-{number}"

            # Bereits vorhanden?
            if key in existing_details:
                continue

            # Detail-Endpoint
            data = self._fetch(f"/bill/{CONGRESS}/{bill_type}/{number}")
            if data and "bill" in data:
                b = data["bill"]
                detail = {
                    "bill_type": bill_type,
                    "number": number,
                    "bill_id": f"{bill_type}-{CONGRESS}-{number}",
                    "title": b.get("title", ""),
                    "short_title": (b.get("shortTitle") or [{}])[0].get("title", "") if b.get("shortTitle") else "",
                    "policy_area": b.get("policyArea", {}).get("name", "") if b.get("policyArea") else "",
                    "introduced_date": b.get("introducedDate", ""),
                    "latest_action": b.get("latestAction", {}),
                    "sponsors": [
                        {
                            "name": s.get("fullName", ""),
                            "party": s.get("party", ""),
                            "state": s.get("state", ""),
                        }
                        for s in (b.get("sponsors") or [])
                    ],
                    "cosponsors_count": b.get("cosponsors", {}).get("count", 0),
                    # Committee-Info kommt aus separatem Endpoint
                    "committees_url": b.get("committees", {}).get("url", ""),
                }
                details.append(detail)
                existing_details[key] = detail

            # Fortschritt + Zwischenspeichern
            if (i + 1) % save_interval == 0:
                self._save_json("bill_details.json", details)
                self._save_checkpoint(f"bill_details_{i+1}",
                                      {"details_fetched": len(details)})
                print(f"  Fortschritt: {i+1}/{len(bills)} "
                      f"({len(details)} Details, Request #{self.request_count})")

        self._save_json("bill_details.json", details)
        print(f"\n  Total: {len(details)} Bill Details gefetcht")

        self._mark_step_done(step)

    # =================================================================
    # SCHRITT 3: BILL ACTIONS (referred, hearing, markup, reported)
    # =================================================================

    def fetch_bill_actions(self):
        """Fetcht Actions für jeden Bill (für Filter 4B: Committee Activity)."""
        step = "bill_actions"
        if self._is_step_done(step):
            print(f"[SKIP] {step} bereits abgeschlossen")
            return

        print(f"\n{'='*60}")
        print(f"SCHRITT 3: Bill Actions")
        print(f"{'='*60}")

        # Bill Details laden
        details_file = OUTPUT_DIR / "bill_details.json"
        if not details_file.exists():
            print("[ERROR] bill_details.json nicht gefunden. Erst Schritt 3 ausführen.")
            return

        with open(details_file, "r", encoding="utf-8") as f:
            bills = json.load(f)

        # Bereits gefetchte Actions laden (für Resume)
        actions_file = OUTPUT_DIR / "bill_actions.json"
        existing_actions = {}
        if actions_file.exists():
            with open(actions_file, "r", encoding="utf-8") as f:
                for a in json.load(f):
                    existing_actions[a.get("bill_id", "")] = a

        print(f"  {len(bills)} Bills total, {len(existing_actions)} Actions bereits gefetcht")

        all_actions = list(existing_actions.values())
        save_interval = 100

        for i, bill in enumerate(bills):
            bill_id = bill.get("bill_id", "")
            bill_type = bill.get("bill_type", "")
            number = bill.get("number", "")

            if bill_id in existing_actions:
                continue

            actions = self._fetch_paginated(
                f"/bill/{CONGRESS}/{bill_type}/{number}/actions",
                result_key="actions",
                max_pages=5  # Die meisten Bills haben <50 Actions
            )

            # Relevante Actions filtern und strukturieren
            processed_actions = []
            for a in actions:
                action_data = {
                    "date": a.get("actionDate", ""),
                    "text": a.get("text", ""),
                    "type": a.get("type", ""),
                    "action_code": a.get("actionCode", ""),
                    "source_system": a.get("sourceSystem", {}).get("name", ""),
                    # Committee-Referenz (wenn vorhanden)
                    "committee": a.get("committee", {}),
                    # Roll Call Nummer (wenn vorhanden — verknüpft mit Votes)
                    "roll_call": a.get("recordedVotes", []),
                }
                processed_actions.append(action_data)

            bill_actions = {
                "bill_id": bill_id,
                "bill_type": bill_type,
                "number": number,
                "title": bill.get("title", ""),
                "policy_area": bill.get("policy_area", ""),
                "actions": processed_actions,
                "action_count": len(processed_actions),
            }
            all_actions.append(bill_actions)
            existing_actions[bill_id] = bill_actions

            # Fortschritt + Zwischenspeichern
            if (i + 1) % save_interval == 0:
                self._save_json("bill_actions.json", all_actions)
                self._save_checkpoint(f"bill_actions_{i+1}",
                                      {"actions_fetched": len(all_actions)})
                print(f"  Fortschritt: {i+1}/{len(bills)} "
                      f"({len(all_actions)} Actions, Request #{self.request_count})")

        self._save_json("bill_actions.json", all_actions)
        print(f"\n  Total: {len(all_actions)} Bill Actions gefetcht")

        self._mark_step_done(step)

    # =================================================================
    # SCHRITT 4: BILL COMMITTEES (welches Committee hat den Bill?)
    # =================================================================

    def fetch_bill_committees(self):
        """Fetcht Committee-Zuweisungen pro Bill (für Filter 4B)."""
        step = "bill_committees"
        if self._is_step_done(step):
            print(f"[SKIP] {step} bereits abgeschlossen")
            return

        print(f"\n{'='*60}")
        print(f"SCHRITT 4: Bill Committee-Zuweisungen")
        print(f"{'='*60}")

        details_file = OUTPUT_DIR / "bill_details.json"
        if not details_file.exists():
            print("[ERROR] bill_details.json nicht gefunden.")
            return

        with open(details_file, "r", encoding="utf-8") as f:
            bills = json.load(f)

        # Resume
        committees_file = OUTPUT_DIR / "bill_committees.json"
        existing = {}
        if committees_file.exists():
            with open(committees_file, "r", encoding="utf-8") as f:
                for c in json.load(f):
                    existing[c.get("bill_id", "")] = c

        print(f"  {len(bills)} Bills total, {len(existing)} bereits gefetcht")

        all_committees = list(existing.values())
        save_interval = 200

        for i, bill in enumerate(bills):
            bill_id = bill.get("bill_id", "")
            bill_type = bill.get("bill_type", "")
            number = bill.get("number", "")

            if bill_id in existing:
                continue

            data = self._fetch(
                f"/bill/{CONGRESS}/{bill_type}/{number}/committees"
            )

            committees = []
            if data:
                for c in data.get("committees", []):
                    committees.append({
                        "name": c.get("name", ""),
                        "chamber": c.get("chamber", ""),
                        "system_code": c.get("systemCode", ""),
                        "committee_type": c.get("type", ""),
                        # Subcommittees, falls vorhanden
                        "subcommittees": [
                            {
                                "name": sc.get("name", ""),
                                "system_code": sc.get("systemCode", ""),
                            }
                            for sc in c.get("subcommittees", [])
                        ],
                    })

            entry = {
                "bill_id": bill_id,
                "bill_type": bill_type,
                "number": number,
                "policy_area": bill.get("policy_area", ""),
                "committees": committees,
            }
            all_committees.append(entry)
            existing[bill_id] = entry

            if (i + 1) % save_interval == 0:
                self._save_json("bill_committees.json", all_committees)
                self._save_checkpoint(f"bill_committees_{i+1}",
                                      {"committees_fetched": len(all_committees)})
                print(f"  Fortschritt: {i+1}/{len(bills)} "
                      f"(Request #{self.request_count})")

        self._save_json("bill_committees.json", all_committees)
        print(f"\n  Total: {len(all_committees)} Bill Committee-Zuweisungen")

        self._mark_step_done(step)

    # =================================================================
    # SCHRITT 5: BILL SUMMARIES (CRS Zusammenfassungen)
    # =================================================================

    def fetch_bill_summaries(self):
        """Fetcht CRS Summaries pro Bill (für Keyword-Fallback bei unsicheren Policy Areas)."""
        step = "bill_summaries"
        if self._is_step_done(step):
            print(f"[SKIP] {step} bereits abgeschlossen")
            return

        print(f"\n{'='*60}")
        print(f"SCHRITT 5: Bill Summaries (CRS)")
        print(f"{'='*60}")

        details_file = OUTPUT_DIR / "bill_details.json"
        if not details_file.exists():
            print("[ERROR] bill_details.json nicht gefunden. Erst Schritt 3 ausführen.")
            return

        with open(details_file, "r", encoding="utf-8") as f:
            bills = json.load(f)

        # Resume
        summaries_file = OUTPUT_DIR / "bill_summaries.json"
        existing = {}
        if summaries_file.exists():
            with open(summaries_file, "r", encoding="utf-8") as f:
                for s in json.load(f):
                    existing[s.get("bill_id", "")] = s

        print(f"  {len(bills)} Bills total, {len(existing)} Summaries bereits gefetcht")

        all_summaries = list(existing.values())
        save_interval = 100

        for i, bill in enumerate(bills):
            bill_id = bill.get("bill_id", "")
            bill_type = bill.get("bill_type", "")
            number = bill.get("number", "")

            if bill_id in existing:
                continue

            data = self._fetch(
                f"/bill/{CONGRESS}/{bill_type}/{number}/summaries"
            )

            summaries = []
            if data:
                for s in data.get("summaries", []):
                    summaries.append({
                        "action_date": s.get("actionDate", ""),
                        "action_desc": s.get("actionDesc", ""),
                        "text": s.get("text", ""),
                        "version_code": s.get("versionCode", ""),
                        "update_date": s.get("updateDate", ""),
                    })

            entry = {
                "bill_id": bill_id,
                "bill_type": bill_type,
                "number": number,
                "title": bill.get("title", ""),
                "policy_area": bill.get("policy_area", ""),
                "summaries": summaries,
                "has_summary": len(summaries) > 0,
            }
            all_summaries.append(entry)
            existing[bill_id] = entry

            if (i + 1) % save_interval == 0:
                self._save_json("bill_summaries.json", all_summaries)
                self._save_checkpoint(f"bill_summaries_{i+1}",
                                      {"summaries_fetched": len(all_summaries)})
                print(f"  Fortschritt: {i+1}/{len(bills)} "
                      f"({sum(1 for s in all_summaries if s['has_summary'])} mit Summary, "
                      f"Request #{self.request_count})")

        self._save_json("bill_summaries.json", all_summaries)
        with_summary = sum(1 for s in all_summaries if s["has_summary"])
        print(f"\n  Total: {len(all_summaries)} Bills, {with_summary} mit Summary")

        self._mark_step_done(step)

    # =================================================================
    # SCHRITT 6: BILL SUBJECTS (Legislative Subjects für Rider-Erkennung)
    # =================================================================

    def fetch_bill_subjects(self):
        """Fetcht Legislative Subjects pro Bill (löst Rider-Problem bei Omnibus-Bills)."""
        step = "bill_subjects"
        if self._is_step_done(step):
            print(f"[SKIP] {step} bereits abgeschlossen")
            return

        print(f"\n{'='*60}")
        print(f"SCHRITT 6: Bill Subjects (Legislative Subjects)")
        print(f"{'='*60}")

        details_file = OUTPUT_DIR / "bill_details.json"
        if not details_file.exists():
            print("[ERROR] bill_details.json nicht gefunden.")
            return

        with open(details_file, "r", encoding="utf-8") as f:
            bills = json.load(f)

        # Resume
        subjects_file = OUTPUT_DIR / "bill_subjects.json"
        existing = {}
        if subjects_file.exists():
            with open(subjects_file, "r", encoding="utf-8") as f:
                for s in json.load(f):
                    existing[s.get("bill_id", "")] = s

        print(f"  {len(bills)} Bills total, {len(existing)} Subjects bereits gefetcht")

        all_subjects = list(existing.values())
        save_interval = 200

        for i, bill in enumerate(bills):
            bill_id = bill.get("bill_id", "")
            bill_type = bill.get("bill_type", "")
            number = bill.get("number", "")

            if bill_id in existing:
                continue

            legislative_subjects = self._fetch_legislative_subjects(bill_type, number)

            entry = {
                "bill_id": bill_id,
                "bill_type": bill_type,
                "number": number,
                "title": bill.get("title", ""),
                "policy_area": bill.get("policy_area", ""),
                "legislative_subjects": legislative_subjects,
                "subject_count": len(legislative_subjects),
            }
            all_subjects.append(entry)
            existing[bill_id] = entry

            if (i + 1) % save_interval == 0:
                self._save_json("bill_subjects.json", all_subjects)
                self._save_checkpoint(f"bill_subjects_{i+1}",
                                      {"subjects_fetched": len(all_subjects)})
                with_subjects = sum(1 for s in all_subjects if s["subject_count"] > 0)
                print(f"  Fortschritt: {i+1}/{len(bills)} "
                      f"({with_subjects} mit Subjects, "
                      f"Request #{self.request_count})")

        self._save_json("bill_subjects.json", all_subjects)
        with_subjects = sum(1 for s in all_subjects if s["subject_count"] > 0)
        print(f"\n  Total: {len(all_subjects)} Bills, {with_subjects} mit Subjects")

        self._mark_step_done(step)

    # =================================================================
    # UPDATE HELPERS
    # =================================================================

    def _merge_into_json(self, filename: str, new_records: list,
                         key: str = "bill_id") -> tuple:
        """Merged neue Records in bestehende JSON-Datei (update oder insert).
        Gibt (updated, added) zurück."""
        filepath = OUTPUT_DIR / filename
        existing = {}
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                for r in json.load(f):
                    existing[r.get(key, "")] = r

        updated = added = 0
        for r in new_records:
            k = r.get(key, "")
            if not k:
                continue
            if k in existing:
                updated += 1
            else:
                added += 1
            existing[k] = r

        self._save_json(filename, list(existing.values()))
        return updated, added

    def _fetch_single_bill_all_types(self, bill_type: str, number: str) -> dict:
        """Fetcht alle 6 Datentypen für einen einzelnen Bill.
        Wird von run_update() genutzt um Code-Duplizierung zu vermeiden."""
        bill_id = f"{bill_type}-{CONGRESS}-{number}"

        # Details
        details_entry = None
        data = self._fetch(f"/bill/{CONGRESS}/{bill_type}/{number}")
        if data and "bill" in data:
            b = data["bill"]
            policy_area = b.get("policyArea", {}).get("name", "") if b.get("policyArea") else ""
            details_entry = {
                "bill_id": bill_id,
                "bill_type": bill_type,
                "number": number,
                "title": b.get("title", ""),
                "short_title": (b.get("shortTitle") or [{}])[0].get("title", "") if b.get("shortTitle") else "",
                "policy_area": policy_area,
                "introduced_date": b.get("introducedDate", ""),
                "latest_action": b.get("latestAction", {}),
                "sponsors": [
                    {"name": s.get("fullName", ""), "party": s.get("party", ""), "state": s.get("state", "")}
                    for s in (b.get("sponsors") or [])
                ],
                "cosponsors_count": b.get("cosponsors", {}).get("count", 0),
                "committees_url": b.get("committees", {}).get("url", ""),
            }
        else:
            policy_area = ""

        # Actions
        actions_raw = self._fetch_paginated(
            f"/bill/{CONGRESS}/{bill_type}/{number}/actions",
            result_key="actions", max_pages=5
        )
        actions_entry = {
            "bill_id": bill_id, "bill_type": bill_type, "number": number,
            "title": details_entry.get("title", "") if details_entry else "",
            "policy_area": policy_area,
            "actions": [
                {
                    "date": a.get("actionDate", ""),
                    "text": a.get("text", ""),
                    "type": a.get("type", ""),
                    "action_code": a.get("actionCode", ""),
                    "source_system": a.get("sourceSystem", {}).get("name", ""),
                    "committee": a.get("committee", {}),
                    "roll_call": a.get("recordedVotes", []),
                }
                for a in actions_raw
            ],
            "action_count": len(actions_raw),
        }

        # Committees
        committees_data = self._fetch(f"/bill/{CONGRESS}/{bill_type}/{number}/committees")
        committees_entry = {
            "bill_id": bill_id, "bill_type": bill_type, "number": number,
            "policy_area": policy_area,
            "committees": [
                {
                    "name": c.get("name", ""),
                    "chamber": c.get("chamber", ""),
                    "system_code": c.get("systemCode", ""),
                    "committee_type": c.get("type", ""),
                    "subcommittees": [
                        {"name": sc.get("name", ""), "system_code": sc.get("systemCode", "")}
                        for sc in c.get("subcommittees", [])
                    ],
                }
                for c in (committees_data.get("committees", []) if committees_data else [])
            ],
        }

        # Summaries
        summaries_data = self._fetch(f"/bill/{CONGRESS}/{bill_type}/{number}/summaries")
        summaries_list = [
            {
                "action_date": s.get("actionDate", ""),
                "action_desc": s.get("actionDesc", ""),
                "text": s.get("text", ""),
                "version_code": s.get("versionCode", ""),
                "update_date": s.get("updateDate", ""),
            }
            for s in (summaries_data.get("summaries", []) if summaries_data else [])
        ]
        summaries_entry = {
            "bill_id": bill_id, "bill_type": bill_type, "number": number,
            "title": details_entry.get("title", "") if details_entry else "",
            "policy_area": policy_area,
            "summaries": summaries_list,
            "has_summary": len(summaries_list) > 0,
        }

        # Subjects (paginiert — löst Pagination-Bug für Omnibus-Bills)
        legislative_subjects = self._fetch_legislative_subjects(bill_type, number)
        subjects_entry = {
            "bill_id": bill_id, "bill_type": bill_type, "number": number,
            "title": details_entry.get("title", "") if details_entry else "",
            "policy_area": policy_area,
            "legislative_subjects": legislative_subjects,
            "subject_count": len(legislative_subjects),
        }

        return {
            "bill_id": bill_id,
            "details": details_entry,
            "actions": actions_entry,
            "committees": committees_entry,
            "summaries": summaries_entry,
            "subjects": subjects_entry,
        }

    def _flush_update(self, details: list, actions: list, committees: list,
                      summaries: list, subjects: list):
        """Merged alle gesammelten Update-Records in die JSON-Dateien."""
        if details:
            u, a = self._merge_into_json("bill_details.json", details)
            print(f"  details:    {u} aktualisiert, {a} neu")
        if actions:
            u, a = self._merge_into_json("bill_actions.json", actions)
            print(f"  actions:    {u} aktualisiert, {a} neu")
        if committees:
            u, a = self._merge_into_json("bill_committees.json", committees)
            print(f"  committees: {u} aktualisiert, {a} neu")
        if summaries:
            u, a = self._merge_into_json("bill_summaries.json", summaries)
            print(f"  summaries:  {u} aktualisiert, {a} neu")
        if subjects:
            u, a = self._merge_into_json("bill_subjects.json", subjects)
            print(f"  subjects:   {u} aktualisiert, {a} neu")

    # =================================================================
    # DELTA UPDATE
    # =================================================================

    def run_update(self):
        """Delta-Update: Fetcht nur Bills die seit dem letzten Lauf geändert wurden."""
        from_dt = (self.checkpoint.get("last_update_run")
                   or self.checkpoint.get("last_full_run"))
        if not from_dt:
            print("[ERROR] Kein abgeschlossener Erstlauf gefunden.")
            print("  Erst ohne --update starten: python scrapers/fetch_congress_data.py")
            sys.exit(1)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        start = datetime.now()

        print(f"Congress.gov API — Delta Update")
        print(f"Congress: {CONGRESS}")
        print(f"Geändert seit: {from_dt[:19]}")
        print(f"{'='*60}")

        # Geänderte Bills über fromDateTime fetchen
        changed_bills = []
        for bill_type in ["hr", "s", "hjres", "sjres"]:
            print(f"  Typ {bill_type}...", end=" ")
            bills = self._fetch_paginated(
                f"/bill/{CONGRESS}/{bill_type}",
                result_key="bills",
                params={"fromDateTime": from_dt, "sort": "updateDate+asc"},
            )
            for b in bills:
                b["bill_type"] = bill_type
            changed_bills.extend(bills)
            print(f"{len(bills)} geändert")

        print(f"\n  {len(changed_bills)} Bills seit letztem Lauf geändert")

        if not changed_bills:
            print("  Alles aktuell — kein Update nötig.")
            self.checkpoint["last_update_run"] = datetime.now(timezone.utc).isoformat()
            self.checkpoint["update_count"] = self.checkpoint.get("update_count", 0) + 1
            self._save_checkpoint("update_complete")
            return

        # Pro Bill alle 6 Typen neu fetchen
        new_details, new_actions, new_committees = [], [], []
        new_summaries, new_subjects = [], []
        save_interval = 50

        try:
            for i, bill in enumerate(changed_bills):
                bill_type = bill.get("bill_type", bill.get("type", "")).lower()
                number = bill.get("number", "")

                fetched = self._fetch_single_bill_all_types(bill_type, number)

                if fetched.get("details"):
                    new_details.append(fetched["details"])
                if fetched.get("actions"):
                    new_actions.append(fetched["actions"])
                if fetched.get("committees"):
                    new_committees.append(fetched["committees"])
                if fetched.get("summaries"):
                    new_summaries.append(fetched["summaries"])
                if fetched.get("subjects"):
                    new_subjects.append(fetched["subjects"])

                print(f"  [{i+1}/{len(changed_bills)}] {fetched['bill_id']} "
                      f"(Request #{self.request_count})", end="\r")

                if (i + 1) % save_interval == 0:
                    print()
                    self._flush_update(new_details, new_actions, new_committees,
                                       new_summaries, new_subjects)
                    new_details = new_actions = new_committees = []
                    new_summaries = new_subjects = []

        except KeyboardInterrupt:
            print(f"\n[ABBRUCH] Bisher verarbeitete Bills werden gespeichert...")

        print()
        self._flush_update(new_details, new_actions, new_committees,
                           new_summaries, new_subjects)

        # Checkpoint aktualisieren
        self.checkpoint["last_update_run"] = datetime.now(timezone.utc).isoformat()
        self.checkpoint["update_count"] = self.checkpoint.get("update_count", 0) + 1
        self._save_checkpoint("update_complete")

        elapsed = datetime.now() - start
        print(f"\n{'='*60}")
        print(f"UPDATE FERTIG")
        print(f"Bills verarbeitet: {len(changed_bills)}")
        print(f"Requests: {self.request_count}")
        print(f"Dauer: {elapsed}")
        print(f"{'='*60}")

    # =================================================================
    # RUN
    # =================================================================

    def run(self):
        """Führt alle Schritte aus."""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        start = datetime.now()

        print(f"Congress.gov API Fetcher")
        print(f"Congress: {CONGRESS}")
        print(f"Output: {OUTPUT_DIR}/")
        print(f"Start: {start.strftime('%H:%M:%S')}")
        print(f"{'='*60}")

        all_steps = ["bills_list", "bill_details", "bill_actions",
                     "bill_committees", "bill_summaries", "bill_subjects"]

        try:
            self.fetch_bills()           # Schritt 1: ~30 Min
            self.fetch_bill_details()    # Schritt 2: ~1.5-2 Std
            self.fetch_bill_actions()    # Schritt 3: ~1-2 Std (enthält Vote-Daten!)
            self.fetch_bill_committees() # Schritt 4: ~1 Std
            self.fetch_bill_summaries()  # Schritt 5: ~1.5 Std
            self.fetch_bill_subjects()   # Schritt 6: ~2.5 Std
        except KeyboardInterrupt:
            print(f"\n\n[ABBRUCH] Checkpoint gespeichert. Erneut starten zum Fortsetzen.")
            self._save_checkpoint("interrupted")
        finally:
            # last_full_run nur setzen wenn alle Schritte abgeschlossen
            completed = self.checkpoint.get("completed_steps", [])
            if all(s in completed for s in all_steps):
                self.checkpoint["last_full_run"] = datetime.now(timezone.utc).isoformat()
                self._save_checkpoint("full_run_complete")

            elapsed = datetime.now() - start
            log = {
                "congress": CONGRESS,
                "start": start.isoformat(),
                "elapsed_seconds": elapsed.total_seconds(),
                "total_requests": self.request_count,
                "errors": self.errors,
                "completed_steps": self.checkpoint.get("completed_steps", []),
            }
            self._save_json("fetch_log.json", log)

            print(f"\n{'='*60}")
            print(f"FERTIG")
            print(f"Dauer: {elapsed}")
            print(f"Requests: {self.request_count}")
            print(f"Fehler: {len(self.errors)}")
            print(f"{'='*60}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Congress.gov API Data Fetcher für Filter 4"
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Congress.gov API Key (default: aus .env CONGRESS_API_KEY)"
    )
    parser.add_argument(
        "--congress",
        type=int,
        default=119,
        help="Congress-Nummer (default: 119)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.75,
        help="Pause zwischen Requests in Sekunden (default: 0.75)"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Delta-Update: nur neue/geänderte Bills seit letztem Lauf fetchen"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Erstlauf erzwingen: ignoriert completed_steps und startet neu"
    )
    args = parser.parse_args()

    # API Key: CLI-Argument > .env > Fehler
    api_key = args.api_key or os.getenv("CONGRESS_API_KEY")
    if not api_key:
        print("[ERROR] Kein API Key gefunden.")
        print("  Option 1: --api-key DEIN_KEY")
        print("  Option 2: CONGRESS_API_KEY in .env setzen")
        sys.exit(1)

    global CONGRESS, REQUEST_DELAY
    CONGRESS = args.congress
    REQUEST_DELAY = args.delay

    fetcher = CongressFetcher(api_key=api_key)

    if args.force:
        fetcher.checkpoint["completed_steps"] = []
        fetcher.checkpoint.pop("last_full_run", None)
        fetcher._save_checkpoint("force_reset")
        print("[FORCE] Checkpoint zurückgesetzt — Erstlauf wird wiederholt.")

    if args.update:
        fetcher.run_update()
    else:
        fetcher.run()


if __name__ == "__main__":
    main()
