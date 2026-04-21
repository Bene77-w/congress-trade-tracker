"""
Congress Trade Tracker - Congress.gov Vote Fetcher

Laedt Bill-Daten und Floor-Votes aus der Congress.gov API (v3)
und cached sie lokal.

Zwei-Phasen-Ansatz:
  Phase 1: Alle Bills eines Kongresses listen → Detail abrufen (policyArea)
           → Cache in congress_bills_cache.csv (einmalig ~4h, danach Delta)
  Phase 2: Fuer Bills mit gemappter policyArea: Actions abrufen
           → Floor-Votes extrahieren → congress_votes.csv

Nur House-Votes (vorerst). Senate-Votes benoetigen XML-Parser.

Usage:
    python scripts/fetch_congress_votes.py              # Delta (Phase 1+2)
    python scripts/fetch_congress_votes.py --full        # Cache komplett neu
    python scripts/fetch_congress_votes.py --phase2-only  # Nur Votes (Cache muss existieren)
    python scripts/fetch_congress_votes.py --stats        # Statistiken
    python scripts/fetch_congress_votes.py --dry-run      # Nur zaehlen
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime

import requests
import pandas as pd

# Pfade konfigurieren
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'data', 'mappings'))

from config import settings
from vote_sector_mapping import get_relevant_sectors, POLICY_AREA_TO_SECTORS

logger = logging.getLogger(__name__)

# Pfade
RAW_DIR = os.path.join(BASE_DIR, settings.DATA_RAW_PATH)
BILLS_CACHE_PATH = os.path.join(RAW_DIR, 'congress_bills_cache.csv')
VOTES_CSV_PATH = os.path.join(RAW_DIR, 'congress_votes.csv')

# API
API_BASE_URL = 'https://api.congress.gov/v3'
API_KEY = settings.CONGRESS_API_KEY
RATE_LIMIT_SECONDS = 1.0

# Cache-Spalten
BILLS_CACHE_COLUMNS = [
    'congress', 'bill_type', 'bill_number', 'bill_title',
    'policy_area', 'mapped_sectors', 'origin_chamber',
    'introduced_date', 'update_date', 'fetched_at',
]

# Votes-Spalten
VOTES_CSV_COLUMNS = [
    'congress', 'bill_type', 'bill_number', 'bill_title',
    'policy_area', 'mapped_sectors', 'vote_date',
    'action_text', 'chamber', 'bill_url',
]


class CongressVoteFetcher:
    """Fetcht Floor-Votes aus der Congress.gov API (Zwei-Phasen-Ansatz)."""

    def __init__(self):
        if not API_KEY:
            raise ValueError(
                "CONGRESS_API_KEY nicht gesetzt!\n"
                "Bitte in .env eintragen: CONGRESS_API_KEY=dein_key\n"
                "Registrierung: https://api.data.gov/signup/"
            )
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})
        self.stats = {
            'bills_listed': 0,
            'bills_detail_fetched': 0,
            'bills_cached_already': 0,
            'bills_with_mapped_pa': 0,
            'actions_checked': 0,
            'votes_found': 0,
            'api_calls': 0,
            'errors': 0,
        }

    # ─── API Helper ───────────────────────────────────────────────

    def _api_get(self, url: str, params: dict = None) -> dict:
        """GET-Request mit Rate-Limiting und 429-Retry."""
        if not url.startswith('http'):
            url = f"{API_BASE_URL}/{url.lstrip('/')}"

        if params is None:
            params = {}
        params['api_key'] = API_KEY
        params.setdefault('format', 'json')

        time.sleep(RATE_LIMIT_SECONDS)
        self.stats['api_calls'] += 1

        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError:
            if resp.status_code == 429:
                logger.warning("Rate limit erreicht, warte 60s...")
                time.sleep(60)
                return self._api_get(url, params)
            logger.error(f"API-Fehler {resp.status_code} fuer {url}")
            self.stats['errors'] += 1
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Request fehlgeschlagen: {e}")
            self.stats['errors'] += 1
            raise

    # ─── Phase 1: Bill-Cache aufbauen ─────────────────────────────

    def _list_all_bills(self, congress: int) -> list[dict]:
        """Laedt Bill-Liste paginiert (nur Metadaten, keine policyArea)."""
        all_bills = []
        offset = 0
        limit = 250

        print(f"  Lade Bill-Liste fuer {congress}. Kongress...")

        while True:
            try:
                data = self._api_get(f'bill/{congress}', {
                    'limit': limit, 'offset': offset, 'sort': 'updateDate+asc'
                })
            except requests.exceptions.RequestException:
                logger.error(f"Abbruch bei offset={offset}")
                break

            bills = data.get('bills', [])
            if not bills:
                break

            all_bills.extend(bills)

            if len(all_bills) % 1000 == 0 or len(bills) < limit:
                print(f"    {len(all_bills)} Bills gelistet...")

            if len(bills) < limit:
                break
            offset += limit

        self.stats['bills_listed'] = len(all_bills)
        print(f"  {len(all_bills)} Bills total gelistet")
        return all_bills

    def _fetch_bill_detail(self, congress: int, bill_type: str, bill_number: int) -> dict:
        """Fetcht Bill-Detail (fuer policyArea)."""
        try:
            data = self._api_get(f'bill/{congress}/{bill_type}/{bill_number}')
            return data.get('bill', {})
        except requests.exceptions.RequestException:
            return {}

    def _load_bills_cache(self) -> pd.DataFrame:
        """Laedt bestehenden Bill-Cache."""
        if os.path.exists(BILLS_CACHE_PATH):
            return pd.read_csv(BILLS_CACHE_PATH)
        return pd.DataFrame(columns=BILLS_CACHE_COLUMNS)

    def _get_cached_bill_keys(self, cache_df: pd.DataFrame) -> set:
        """Erzeugt Set von (bill_type, bill_number) Keys aus Cache."""
        if cache_df.empty:
            return set()
        return set(
            zip(cache_df['bill_type'].astype(str), cache_df['bill_number'].astype(int))
        )

    def run_phase1(self, congress: int, full: bool = False, dry_run: bool = False) -> pd.DataFrame:
        """
        Phase 1: Bill-Details fetchen und cachen.

        Args:
            congress: Kongress-Nummer
            full: True = Cache komplett neu aufbauen
            dry_run: True = nur zaehlen

        Returns:
            Aktualisierter Cache-DataFrame
        """
        print(f"\n{'='*60}")
        print("PHASE 1: Bill-Details cachen (policyArea)")
        print(f"{'='*60}")

        # Cache laden
        if full:
            cache_df = pd.DataFrame(columns=BILLS_CACHE_COLUMNS)
            print("  Full-Modus: Cache wird neu aufgebaut")
        else:
            cache_df = self._load_bills_cache()
            print(f"  Bestehender Cache: {len(cache_df)} Bills")

        cached_keys = self._get_cached_bill_keys(cache_df)

        # Bill-Liste laden
        all_bills = self._list_all_bills(congress)

        # Neue Bills identifizieren (nicht im Cache)
        new_bills = []
        for b in all_bills:
            bt = b.get('type', '').lower()
            bn = int(b.get('number', 0))
            if (bt, bn) not in cached_keys:
                new_bills.append(b)

        self.stats['bills_cached_already'] = len(all_bills) - len(new_bills)
        print(f"\n  {len(new_bills)} neue Bills zu fetchen "
              f"({self.stats['bills_cached_already']} bereits im Cache)")

        if dry_run:
            print(f"  [DRY-RUN] Ueberspringe Detail-Fetching")
            return cache_df

        if not new_bills:
            print(f"  Cache ist aktuell, keine neuen Bills")
            return cache_df

        # Details fetchen
        new_records = []
        fetch_start = time.time()

        for i, bill in enumerate(new_bills):
            bt = bill.get('type', '').lower()
            bn = int(bill.get('number', 0))

            detail = self._fetch_bill_detail(congress, bt, bn)

            if not detail:
                continue

            # policyArea extrahieren
            pa_obj = detail.get('policyArea')
            policy_area = ''
            if isinstance(pa_obj, dict):
                policy_area = pa_obj.get('name', '')
            elif isinstance(pa_obj, str):
                policy_area = pa_obj

            # Sektor-Mapping
            mapped = get_relevant_sectors(policy_area) if policy_area else []
            mapped_str = ','.join(mapped) if mapped else ''

            new_records.append({
                'congress': congress,
                'bill_type': bt,
                'bill_number': bn,
                'bill_title': detail.get('title', ''),
                'policy_area': policy_area,
                'mapped_sectors': mapped_str,
                'origin_chamber': detail.get('originChamber', ''),
                'introduced_date': detail.get('introducedDate', ''),
                'update_date': bill.get('updateDate', ''),
                'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })

            self.stats['bills_detail_fetched'] += 1

            # Fortschritt (alle 100 Bills)
            if (i + 1) % 100 == 0:
                elapsed = time.time() - fetch_start
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                remaining = (len(new_bills) - i - 1) / rate if rate > 0 else 0
                print(f"    {i+1}/{len(new_bills)} Bills gefetcht "
                      f"({elapsed/60:.0f}min, ~{remaining/60:.0f}min verbleibend)")

        # Cache aktualisieren
        if new_records:
            new_df = pd.DataFrame(new_records, columns=BILLS_CACHE_COLUMNS)
            cache_df = pd.concat([cache_df, new_df], ignore_index=True)

            # Dedup
            cache_df = cache_df.drop_duplicates(
                subset=['congress', 'bill_type', 'bill_number'], keep='last'
            )

            cache_df.to_csv(BILLS_CACHE_PATH, index=False, encoding='utf-8')
            print(f"\n  Cache gespeichert: {len(cache_df)} Bills total")

            # Statistik
            with_pa = cache_df[cache_df['policy_area'].notna() & (cache_df['policy_area'] != '')]
            with_mapped = cache_df[cache_df['mapped_sectors'].notna() & (cache_df['mapped_sectors'] != '')]
            print(f"  Mit Policy Area:   {len(with_pa)}")
            print(f"  Mit Sektor-Mapping: {len(with_mapped)}")

        elapsed_total = time.time() - fetch_start
        print(f"\n  Phase 1 abgeschlossen in {elapsed_total/60:.1f} Minuten")

        return cache_df

    # ─── Phase 2: Votes extrahieren ───────────────────────────────

    def _fetch_bill_actions(self, congress: int, bill_type: str, bill_number: int) -> list[dict]:
        """Fetcht Actions fuer einen Bill."""
        try:
            data = self._api_get(
                f'bill/{congress}/{bill_type}/{bill_number}/actions',
                {'limit': 250}
            )
            return data.get('actions', [])
        except requests.exceptions.RequestException:
            return []

    def _extract_house_votes(self, actions: list[dict]) -> list[dict]:
        """Extrahiert House Floor-Votes mit recordedVotes aus Actions."""
        votes = []
        for action in actions:
            if action.get('type') != 'Floor':
                continue
            if not action.get('recordedVotes'):
                continue

            # Nur House-Votes
            source = action.get('sourceSystem', {})
            source_name = source.get('name', '') if isinstance(source, dict) else ''
            source_code = source.get('code', 0) if isinstance(source, dict) else 0
            if 'House' not in source_name and source_code != 2:
                continue

            action_date = action.get('actionDate', '')
            if action_date:
                votes.append({
                    'vote_date': action_date,
                    'action_text': action.get('text', ''),
                })
        return votes

    def run_phase2(self, cache_df: pd.DataFrame, congress: int, dry_run: bool = False) -> list[dict]:
        """
        Phase 2: Fuer Bills mit gemappter policyArea Actions abrufen und Votes extrahieren.

        Args:
            cache_df: Bill-Cache aus Phase 1
            congress: Kongress-Nummer
            dry_run: Nur zaehlen

        Returns:
            Liste von Vote-Records
        """
        print(f"\n{'='*60}")
        print("PHASE 2: Floor-Votes extrahieren")
        print(f"{'='*60}")

        if cache_df.empty:
            print("  Kein Bill-Cache vorhanden. Bitte zuerst Phase 1 ausfuehren.")
            return []

        # Nur Bills mit gemapptem Sektor
        mapped_bills = cache_df[
            (cache_df['mapped_sectors'].notna()) &
            (cache_df['mapped_sectors'] != '') &
            (cache_df['congress'] == congress)
        ].copy()

        self.stats['bills_with_mapped_pa'] = len(mapped_bills)
        print(f"  {len(mapped_bills)} Bills mit Sektor-Mapping")

        if dry_run:
            print("  [DRY-RUN] Ueberspringe Action-Fetching")
            return []

        # Bestehende Votes laden (fuer Dedup)
        existing_votes = set()
        if os.path.exists(VOTES_CSV_PATH):
            existing_df = pd.read_csv(VOTES_CSV_PATH)
            for _, row in existing_df.iterrows():
                existing_votes.add((
                    str(row.get('bill_type', '')),
                    int(row.get('bill_number', 0)),
                    str(row.get('vote_date', '')),
                ))

        # Actions fuer jeden gemappten Bill abrufen
        vote_records = []
        fetch_start = time.time()

        for i, (_, bill) in enumerate(mapped_bills.iterrows()):
            bt = bill['bill_type']
            bn = int(bill['bill_number'])

            self.stats['actions_checked'] += 1
            actions = self._fetch_bill_actions(congress, bt, bn)
            votes = self._extract_house_votes(actions)

            for vote in votes:
                # Dedup-Check
                vote_key = (str(bt), bn, vote['vote_date'])
                if vote_key in existing_votes:
                    continue

                vote_records.append({
                    'congress': congress,
                    'bill_type': bt,
                    'bill_number': bn,
                    'bill_title': bill['bill_title'],
                    'policy_area': bill['policy_area'],
                    'mapped_sectors': bill['mapped_sectors'],
                    'vote_date': vote['vote_date'],
                    'action_text': vote['action_text'],
                    'chamber': 'House',
                    'bill_url': '',
                })
                self.stats['votes_found'] += 1

            # Fortschritt
            if (i + 1) % 50 == 0:
                elapsed = time.time() - fetch_start
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                remaining = (len(mapped_bills) - i - 1) / rate if rate > 0 else 0
                print(f"    {i+1}/{len(mapped_bills)} Bills geprueft, "
                      f"{self.stats['votes_found']} neue Votes "
                      f"(~{remaining/60:.0f}min verbleibend)")

        elapsed_total = time.time() - fetch_start
        print(f"\n  Phase 2 abgeschlossen in {elapsed_total/60:.1f} Minuten")
        print(f"  {self.stats['votes_found']} neue Votes gefunden")

        return vote_records

    def save_votes(self, new_records: list[dict]):
        """Speichert Votes in congress_votes.csv (Append + Dedup)."""
        if not new_records:
            print("  Keine neuen Votes zu speichern.")
            return

        new_df = pd.DataFrame(new_records, columns=VOTES_CSV_COLUMNS)

        if os.path.exists(VOTES_CSV_PATH):
            existing_df = pd.read_csv(VOTES_CSV_PATH)
            combined = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined = new_df

        # Dedup
        dedup_cols = ['congress', 'bill_type', 'bill_number', 'vote_date', 'chamber']
        before = len(combined)
        combined = combined.drop_duplicates(subset=dedup_cols, keep='last')
        dupes = before - len(combined)

        combined = combined.sort_values('vote_date', ascending=False)
        combined.to_csv(VOTES_CSV_PATH, index=False, encoding='utf-8')

        print(f"  Gespeichert: {VOTES_CSV_PATH}")
        print(f"  {len(new_records)} neue, {dupes} Duplikate entfernt, {len(combined)} total")

    # ─── Haupteinstiegspunkt ──────────────────────────────────────

    def run(self, full: bool = False, phase2_only: bool = False, dry_run: bool = False):
        """
        Fuehrt Phase 1 + Phase 2 aus.

        Args:
            full: Cache komplett neu aufbauen
            phase2_only: Nur Phase 2 (Cache muss existieren)
            dry_run: Nur zaehlen
        """
        congress = settings.VOTE_CONGRESS_NUMBER

        print(f"\n{'='*60}")
        print(f"CONGRESS VOTE FETCHER — {congress}. Kongress")
        print(f"{'='*60}")

        # Phase 1
        if phase2_only:
            cache_df = self._load_bills_cache()
            if cache_df.empty:
                print("\n[X] Kein Bill-Cache vorhanden!")
                print("    Bitte zuerst ohne --phase2-only ausfuehren.")
                return
            print(f"\n  Cache geladen: {len(cache_df)} Bills (Phase 1 uebersprungen)")
        else:
            cache_df = self.run_phase1(congress, full=full, dry_run=dry_run)

        # Phase 2
        vote_records = self.run_phase2(cache_df, congress, dry_run=dry_run)

        # Speichern
        if not dry_run:
            self.save_votes(vote_records)

        # Zusammenfassung
        print(f"\n{'='*60}")
        print("ZUSAMMENFASSUNG")
        print(f"{'='*60}")
        print(f"  API-Calls total:      {self.stats['api_calls']}")
        print(f"  Bills gelistet:       {self.stats['bills_listed']}")
        print(f"  Bills Detail-Fetch:   {self.stats['bills_detail_fetched']}")
        print(f"  Bills aus Cache:      {self.stats['bills_cached_already']}")
        print(f"  Bills mit Mapping:    {self.stats['bills_with_mapped_pa']}")
        print(f"  Actions geprueft:     {self.stats['actions_checked']}")
        print(f"  Votes gefunden:       {self.stats['votes_found']}")
        print(f"  Fehler:               {self.stats['errors']}")


def show_stats():
    """Zeigt Statistiken der gecachten Daten."""
    print(f"\n{'='*60}")
    print("CONGRESS VOTES — Statistiken")
    print(f"{'='*60}")

    # Bill-Cache
    if os.path.exists(BILLS_CACHE_PATH):
        cache = pd.read_csv(BILLS_CACHE_PATH)
        with_pa = cache[cache['policy_area'].notna() & (cache['policy_area'] != '')]
        with_mapped = cache[cache['mapped_sectors'].notna() & (cache['mapped_sectors'] != '')]
        print(f"\n  Bill-Cache: {len(cache)} Bills")
        print(f"    Mit Policy Area:    {len(with_pa)} ({len(with_pa)/len(cache)*100:.0f}%)")
        print(f"    Mit Sektor-Mapping: {len(with_mapped)} ({len(with_mapped)/len(cache)*100:.0f}%)")

        # Top Policy Areas
        print(f"\n  Top 10 Policy Areas:")
        pa_counts = with_pa['policy_area'].value_counts().head(10)
        for pa, count in pa_counts.items():
            mapped = get_relevant_sectors(pa)
            tag = f" -> {', '.join(mapped)}" if mapped else " [unmapped]"
            print(f"    {count:4d}x  {pa}{tag}")
    else:
        print("\n  Kein Bill-Cache vorhanden.")

    # Votes
    if os.path.exists(VOTES_CSV_PATH):
        votes = pd.read_csv(VOTES_CSV_PATH)
        print(f"\n  Votes-CSV: {len(votes)} Floor-Votes")
        dates = pd.to_datetime(votes['vote_date'], errors='coerce').dropna()
        if not dates.empty:
            print(f"    Zeitraum:  {dates.min().date()} bis {dates.max().date()}")
        mapped_v = votes[votes['mapped_sectors'].notna() & (votes['mapped_sectors'] != '')]
        print(f"    Gemappt:   {len(mapped_v)} ({len(mapped_v)/len(votes)*100:.0f}%)")
    else:
        print("\n  Keine congress_votes.csv vorhanden.")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    parser = argparse.ArgumentParser(description='Congress.gov Vote Fetcher')
    parser.add_argument('--full', action='store_true',
                        help='Cache komplett neu aufbauen')
    parser.add_argument('--phase2-only', action='store_true',
                        help='Nur Phase 2 (Votes), Cache muss existieren')
    parser.add_argument('--dry-run', action='store_true',
                        help='Nur zaehlen, nichts fetchen')
    parser.add_argument('--stats', action='store_true',
                        help='Statistiken anzeigen')
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    try:
        fetcher = CongressVoteFetcher()
        fetcher.run(full=args.full, phase2_only=args.phase2_only, dry_run=args.dry_run)
    except ValueError as e:
        print(f"\n[X] FEHLER: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n  Abgebrochen. Bereits gecachte Bills bleiben erhalten.")
        sys.exit(0)
    except Exception as e:
        print(f"\n[X] FEHLER: {e}")
        logger.exception("Vote-Fetch fehlgeschlagen")
        sys.exit(1)


if __name__ == '__main__':
    main()
