"""
Congress Trade Tracker - Name Matching System
Fuzzy-Matching von Quiver-Namen zu Congress-Mitgliedern
"""

import os
import sys
import logging
from typing import Optional

import pandas as pd
from rapidfuzz import fuzz, process

# Config importieren
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from config import settings

logger = logging.getLogger(__name__)


class NameMatcher:
    """
    Matcht Namen aus Quiver-Daten zu Congress-Mitgliedern.

    Matching-Reihenfolge:
    1. Alias-Lookup (manuelle Korrekturen)
    2. Exakter Match
    3. Fuzzy-Match (rapidfuzz, Schwellenwert aus Config)
    """

    def __init__(
        self,
        house_members_path: Optional[str] = None,
        senate_members_path: Optional[str] = None,
        aliases_path: Optional[str] = None
    ):
        """
        Initialisiert den NameMatcher.

        Args:
            house_members_path: Pfad zur house_members.csv
            senate_members_path: Pfad zur senate_members.csv
            aliases_path: Pfad zur name_aliases.csv
        """
        self.threshold = getattr(settings, 'FUZZY_MATCH_THRESHOLD', 80)

        # Standard-Pfade aus Config (bei Neuwahlen in settings.py anpassen)
        raw_path = getattr(settings, 'DATA_RAW_PATH', 'data/raw')
        config_path = getattr(settings, 'DATA_CONFIG_PATH', 'data/config')

        # Neue CSV-Dateien (nach PDF-Extraktion)
        house_file = getattr(settings, 'HOUSE_COMMITTEES_FILE', 'house_member&(sub)committees.csv')
        senate_file = getattr(settings, 'SENATE_COMMITTEES_FILE', 'us_senate_member&(sub)committees.csv')

        self.house_path = house_members_path or os.path.join(BASE_DIR, raw_path, house_file)
        self.senate_path = senate_members_path or os.path.join(BASE_DIR, raw_path, senate_file)
        self.aliases_path = aliases_path or os.path.join(BASE_DIR, config_path, 'name_aliases.csv')

        # Lookup-Dictionaries
        self.house_lookup = {}  # name_lower -> {name, committees, ...}
        self.senate_lookup = {}
        self.aliases = {}  # quiver_name_lower -> {correct_name, chamber}

        # Daten laden
        self._load_data()

    def _load_data(self):
        """Laedt alle Referenzdaten."""
        self._load_house_members()
        self._load_senate_members()
        self._load_aliases()

    def _load_house_members(self):
        """Laedt House Members CSV."""
        if not os.path.exists(self.house_path):
            logger.warning(f"House Members nicht gefunden: {self.house_path}")
            return

        df = pd.read_csv(self.house_path)

        # CSV-Struktur: Name, Party, State, Committees, ExOfficio
        for _, row in df.iterrows():
            name = str(row.get('Name', '')).strip()
            if name:
                name_lower = name.lower()
                self.house_lookup[name_lower] = {
                    'name': name,
                    'party': row.get('Party', ''),
                    'state': row.get('State', ''),
                    'committees': row.get('Committees', ''),
                    'ex_officio': row.get('ExOfficio', ''),
                    'chamber': 'House'
                }

        logger.info(f"House Members geladen: {len(self.house_lookup)}")

    def _load_senate_members(self):
        """Laedt Senate Members CSV."""
        if not os.path.exists(self.senate_path):
            logger.warning(f"Senate Members nicht gefunden: {self.senate_path}")
            return

        df = pd.read_csv(self.senate_path)

        # CSV-Struktur: Name, Party, State, Committees, ExOfficio
        for _, row in df.iterrows():
            name = str(row.get('Name', '')).strip()
            if name:
                name_lower = name.lower()
                self.senate_lookup[name_lower] = {
                    'name': name,
                    'party': row.get('Party', ''),
                    'state': row.get('State', ''),
                    'committees': row.get('Committees', ''),
                    'ex_officio': row.get('ExOfficio', ''),
                    'chamber': 'Senate'
                }

        logger.info(f"Senate Members geladen: {len(self.senate_lookup)}")

    def _load_aliases(self):
        """Laedt Name-Aliases CSV."""
        if not os.path.exists(self.aliases_path):
            logger.info(f"Aliases-Datei nicht vorhanden (wird bei Bedarf erstellt): {self.aliases_path}")
            return

        df = pd.read_csv(self.aliases_path)
        for _, row in df.iterrows():
            quiver_name = str(row.get('quiver_name', '')).strip()
            correct_name = str(row.get('correct_name', '')).strip()
            chamber = str(row.get('chamber', '')).strip().lower()

            if quiver_name and correct_name:
                self.aliases[quiver_name.lower()] = {
                    'correct_name': correct_name,
                    'chamber': chamber
                }

        logger.info(f"Aliases geladen: {len(self.aliases)}")

    def _get_lookup_for_chamber(self, chamber: str) -> dict:
        """Gibt das passende Lookup-Dict fuer die Chamber zurueck."""
        chamber_lower = str(chamber).lower().strip()
        if chamber_lower == 'house':
            return self.house_lookup
        elif chamber_lower == 'senate':
            return self.senate_lookup
        else:
            # Falls Chamber unbekannt, beide durchsuchen
            return {**self.house_lookup, **self.senate_lookup}

    def match(self, quiver_name: str, chamber: str = '') -> dict:
        """
        Matcht einen Quiver-Namen zu einem Congress-Mitglied.

        Args:
            quiver_name: Name aus der Quiver-CSV
            chamber: 'house' oder 'senate'

        Returns:
            dict mit:
            - matched: True/False
            - matched_name: Der gematchte Name
            - committees: Committee-Zugehoerigkeiten
            - score: Match-Score (100 = exakt, <100 = fuzzy)
            - source: 'alias', 'exact', 'fuzzy', oder 'none'
        """
        if not quiver_name or pd.isna(quiver_name):
            return self._no_match(quiver_name)

        quiver_name = str(quiver_name).strip()
        quiver_lower = quiver_name.lower()
        lookup = self._get_lookup_for_chamber(chamber)

        # 1. Alias-Lookup (manuelle Korrekturen)
        if quiver_lower in self.aliases:
            alias_info = self.aliases[quiver_lower]
            correct_name = alias_info['correct_name']
            correct_lower = correct_name.lower()

            # Suche in passendem Lookup
            if correct_lower in lookup:
                member = lookup[correct_lower]
                return {
                    'matched': True,
                    'matched_name': member['name'],
                    'party': member.get('party', ''),
                    'state': member.get('state', ''),
                    'committees': member.get('committees', ''),
                    'ex_officio': member.get('ex_officio', ''),
                    'chamber': member.get('chamber', ''),
                    'score': 100,
                    'source': 'alias'
                }

        # 2. Exakter Match
        if quiver_lower in lookup:
            member = lookup[quiver_lower]
            return {
                'matched': True,
                'matched_name': member['name'],
                'party': member.get('party', ''),
                'state': member.get('state', ''),
                'committees': member.get('committees', ''),
                'ex_officio': member.get('ex_officio', ''),
                'chamber': member.get('chamber', ''),
                'score': 100,
                'source': 'exact'
            }

        # 3. Fuzzy Match
        if lookup:
            # Liste aller Namen fuer fuzzy matching
            all_names = list(lookup.keys())

            # Besten Match finden
            result = process.extractOne(
                quiver_lower,
                all_names,
                scorer=fuzz.WRatio,
                score_cutoff=self.threshold
            )

            if result:
                matched_lower, score, _ = result
                member = lookup[matched_lower]
                return {
                    'matched': True,
                    'matched_name': member['name'],
                    'party': member.get('party', ''),
                    'state': member.get('state', ''),
                    'committees': member.get('committees', ''),
                    'ex_officio': member.get('ex_officio', ''),
                    'chamber': member.get('chamber', ''),
                    'score': int(score),
                    'source': 'fuzzy'
                }

        # Kein Match gefunden
        return self._no_match(quiver_name)

    def _no_match(self, quiver_name: str) -> dict:
        """Gibt ein No-Match-Resultat zurueck."""
        return {
            'matched': False,
            'matched_name': '',
            'party': '',
            'state': '',
            'committees': '',
            'ex_officio': '',
            'chamber': '',
            'score': 0,
            'source': 'none'
        }

    def get_best_fuzzy_match(self, quiver_name: str, chamber: str = '') -> dict:
        """
        Findet den besten Fuzzy-Match (auch unter Threshold).
        Nuetzlich fuer unmatched_names.csv.
        """
        if not quiver_name or pd.isna(quiver_name):
            return {'best_match': '', 'score': 0}

        quiver_lower = str(quiver_name).lower().strip()
        lookup = self._get_lookup_for_chamber(chamber)

        if not lookup:
            return {'best_match': '', 'score': 0}

        all_names = list(lookup.keys())
        result = process.extractOne(
            quiver_lower,
            all_names,
            scorer=fuzz.WRatio
        )

        if result:
            matched_lower, score, _ = result
            member = lookup[matched_lower]
            return {
                'best_match': member['name'],
                'score': int(score)
            }

        return {'best_match': '', 'score': 0}

    def get_statistics(self) -> dict:
        """Gibt Statistiken ueber geladene Daten zurueck."""
        return {
            'house_members': len(self.house_lookup),
            'senate_members': len(self.senate_lookup),
            'aliases': len(self.aliases),
            'fuzzy_threshold': self.threshold
        }


def test_matcher():
    """Test-Funktion fuer den NameMatcher."""
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("NameMatcher Test")
    print("=" * 60)

    matcher = NameMatcher()
    stats = matcher.get_statistics()

    print(f"\nStatistiken:")
    print(f"  House Members: {stats['house_members']}")
    print(f"  Senate Members: {stats['senate_members']}")
    print(f"  Aliases: {stats['aliases']}")
    print(f"  Fuzzy Threshold: {stats['fuzzy_threshold']}%")

    # Test-Namen
    test_names = [
        ("Nancy Pelosi", "house"),
        ("Rick Larsen", "house"),
        ("Tim Scott", "senate"),
        ("John Boozman", "senate"),
        ("UNBEKANNTER NAME", "house"),
    ]

    print("\n--- Test-Matches ---")
    for name, chamber in test_names:
        result = matcher.match(name, chamber)
        status = "[OK]" if result['matched'] else "[X]"
        print(f"{status} '{name}' ({chamber})")
        print(f"     -> {result['matched_name']} (Score: {result['score']}, Source: {result['source']})")
        if result['committees']:
            committees_preview = result['committees'][:60] + "..." if len(result['committees']) > 60 else result['committees']
            print(f"     -> Committees: {committees_preview}")
        print()


if __name__ == "__main__":
    test_matcher()
