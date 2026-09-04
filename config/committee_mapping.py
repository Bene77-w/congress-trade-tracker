import pandas as pd
import os
import re
import logging
from typing import Dict, List, Tuple, Optional, Any
from config import settings

from committee_matcher.roster_coverage import _lookup_committee
from committee_matcher.units import match_key as _match_key

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Unitarten des Matchers, die den Committee-Lookup speisen duerfen. Ein
#: Subcommittee gehoert nicht dazu: sein Sektor ist seiner, nicht der des
#: Mutterausschusses.
COMMITTEE_UNIT_KINDS = frozenset({'committee_direct', 'committee_parent_core'})
UNIT_KIND_SUBCOMMITTEE = 'subcommittee'


def _chamber_key(chamber: str) -> str:
    """Kammer der Trade-Seite ('House') als Kammer des Matchers ('house')."""
    return str(chamber or '').strip().lower()


def _dedupe_rang(entry: Dict[str, Any]) -> Tuple[int, int]:
    """Sortierschluessel der Dublettenwahl; kleiner gewinnt.

    Zuerst die Konfidenz, absteigend: sie hat die Aufgabe von ``Sector_Rank``
    uebernommen. Der Rang bleibt als zweites Kriterium stehen, solange der
    Legacy-Weg existiert — dort ist ``confidence`` ein Wort ('Hoch') und
    taugt nicht zum Vergleich.
    """
    stufe = entry.get('confidence')
    return (-stufe if isinstance(stufe, int) else 0,
            entry.get('rank', 99))


def dedupe_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Je (sector, industry) bleibt ein Eintrag: der mit der hoechsten Konfidenz.

    Eine Regel an einer Stelle: ``CommitteeTag`` dedupliziert eine Ebene
    hoeher noch einmal und benutzt dieselbe Funktion, damit beide Ebenen
    nicht verschiedene Sieger kueren koennen.
    """
    unique: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for entry in entries:
        key = (entry['sector'], entry['industry'])
        vorhanden = unique.get(key)
        if vorhanden is None or _dedupe_rang(entry) < _dedupe_rang(vorhanden):
            unique[key] = entry
    # Kopien: die Lookups halten dieselben Objekte, und der Aufrufer haengt
    # Rollen an die Eintraege.
    return [entry.copy() for entry in unique.values()]


class CommitteeMapper:
    """
    Maps congressional subcommittees to industry sectors and industries.

    Loads subcommittee_sector_industry_mapping.csv and provides fast O(1) lookup
    of sectors/industries for any subcommittee.
    """

    def __init__(self, mapping_path: Optional[str] = None):
        """
        Initialize mapper and load mapping data.

        Ohne ``mapping_path`` gilt die aktive Mappingrevision des Committee
        Matchers V2 (``COMMITTEE_MAPPING_REVIEW_ID``). Mit Pfad wird die
        Legacy-CSV gelesen — der Weg bleibt erhalten, bis der Cutover (P4)
        ihn entfernt, damit der Schattenvergleich beide Staende nebeneinander
        bauen kann, ohne dass ein Schalter durch den Produktivcode laeuft.

        Args:
            mapping_path: Pfad einer Legacy-Mapping-CSV; ohne Angabe die
                aktive Revision.
        """
        self.mapping_path = mapping_path
        self.revision = None
        self.mapping: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        self.committee_mapping: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self.stats = {
            'total_mappings': 0,
            'unique_subcommittees': 0,
            'unique_committees': 0
        }

        if mapping_path is None:
            self._load_revision()
        else:
            self._load_mapping()

    # -- V2: die aktive Mappingrevision ------------------------------------
    def _unit_entry(self, assignment) -> Dict[str, Any]:
        """Eine Zuordnung als Lookup-Eintrag.

        ``rank`` faellt weg: seine einzige Aufgabe war die Dublettenwahl, und
        die trifft jetzt ``confidence``. Namen statt IDs, weil der Abgleich
        gegen ``sector_yf``/``industry_yf`` des Tickers laeuft — und beide
        Seiten stammen nun aus derselben yfinance-Taxonomie.
        """
        return {
            'sector': assignment.sector_name,
            'industry': assignment.industry_name,
            'confidence': assignment.confidence,
        }

    def _load_revision(self) -> None:
        """Baut die Lookups aus der aktiven Revision.

        Drei Festlegungen unterscheiden diesen Weg von der Legacy-Kette:

        * Kanonisiert wird mit ``units.match_key`` — derselben Funktion, mit
          der der Matcher seine Unit-IDs bildet. Eine zweite Normalisierung
          neben ihr waere eine zweite Wahrheit ueber denselben Namen.
        * Den Committee-Lookup speisen allein Units mit ``committee_direct``
          oder ``committee_parent_core``. Die Legacy-Aggregation aller
          Subcommittee-Zeilen entfaellt: sie fuellte den bewusst erzeugten
          Parent-Core mit Sektoren auf, die dort niemand gemappt hat.
        * ``joint`` trifft beide Kammern; die Regel kommt aus
          ``roster_coverage._lookup_committee`` und wird nicht nachgebaut.
        """
        from config import committee_revision

        self.revision = committee_revision.load_active_revision(settings)
        committees: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        subcommittees: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for assignment in self.revision.assignments:
            eintrag = self._unit_entry(assignment)
            schluessel_committee = _match_key(assignment.committee_name)
            if assignment.unit_kind == UNIT_KIND_SUBCOMMITTEE:
                subcommittees.setdefault(
                    (assignment.chamber, schluessel_committee,
                     _match_key(assignment.unit_name)), []).append(eintrag)
            elif assignment.unit_kind in COMMITTEE_UNIT_KINDS:
                committees.setdefault(
                    (assignment.chamber, schluessel_committee), []
                ).append(eintrag)

        self.mapping = subcommittees
        self.committee_mapping = committees
        self.stats['total_mappings'] = len(self.revision.assignments)
        self.stats['unique_subcommittees'] = len(subcommittees)
        self.stats['unique_committees'] = len(committees)
        logger.info(
            "Mapping aus Revision %s: %d Zuordnungen, %d Subcommittees, "
            "%d Committees", self.revision.review_id,
            self.stats['total_mappings'], self.stats['unique_subcommittees'],
            self.stats['unique_committees'])

    def _index(self) -> Dict[str, Dict]:
        """Die Lookups in der Form, die ``_lookup_committee`` erwartet."""
        return {'committees': self.committee_mapping,
                'subcommittees': self.mapping}

    def _revision_subcommittee(self, chamber: str, committee: str,
                               subcommittee: str) -> List[Dict[str, Any]]:
        """Subcommittee-Lookup mit derselben Joint-Regel wie oben.

        Vier Joint-Units fuehrt der Matcher unter ``joint``, die Trade-Seite
        kennt nur ``house``/``senate``. Ohne den Fallback traefe ein
        Joint-Subcommittee nie.
        """
        key = (_match_key(committee), _match_key(subcommittee))
        for kammer in (_chamber_key(chamber), 'joint'):
            treffer = self.mapping.get((kammer,) + key)
            if treffer:
                return treffer
        return []

    def _normalize(self, text: str) -> str:
        """
        Normalize text for matching.

        Args:
            text: Text to normalize

        Returns:
            Normalized text (lowercase, stripped, cleaned)
        """
        if not text or pd.isna(text):
            return ''

        text = str(text).lower().strip()
        # Innere Mehrfach-Whitespaces kollabieren — Quelldaten (z.B. Senate-XML
        # SSBK-Sub) enthalten vereinzelt Doppel-Leerzeichen, Mapping-CSV nicht.
        text = re.sub(r'\s+', ' ', text)

        # Handle Senate "Committee on X" format
        if text.startswith('committee on '):
            text = text.replace('committee on ', '', 1)
        if text.startswith('subcommittee on '):
            text = text.replace('subcommittee on ', '', 1)
        # Befund A: Artikel nach dem Praefix strippen, sonst matcht
        # "Committee on the Judiciary" nicht das Mapping "Judiciary".
        if text.startswith('the '):
            text = text[4:]

        return text

    def _load_mapping(self) -> None:
        """Load CSV and build lookup dictionaries."""
        if not os.path.exists(self.mapping_path):
            raise FileNotFoundError(
                f"Committee mapping not found: {self.mapping_path}\n"
                f"Please ensure subcommittee_sector_industry_mapping.csv exists in data/mappings/"
            )

        logger.info(f"Loading committee-sector mapping from {self.mapping_path}")

        try:
            df = pd.read_csv(self.mapping_path)
        except Exception as e:
            logger.error(f"Failed to load mapping CSV: {e}")
            raise

        # Validate required columns
        required_cols = ['Chamber', 'Committee', 'Subcommittee', 'Sector', 'Industry', 'Sector_Rank', 'Konfidenz']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns in mapping CSV: {missing_cols}")

        # Build lookup dictionary
        for _, row in df.iterrows():
            chamber = self._normalize(row['Chamber'])
            committee = self._normalize(row['Committee'])
            subcommittee = self._normalize(row['Subcommittee'])

            if not chamber or not committee:
                continue

            # Create sector/industry entry
            entry = {
                'sector': str(row['Sector']).strip(),
                'industry': str(row['Industry']).strip(),
                'rank': int(row['Sector_Rank']) if pd.notna(row['Sector_Rank']) else 99,
                'confidence': str(row['Konfidenz']).strip()
            }

            # Add to subcommittee mapping. Leeres Subcommittee = bewusste
            # Committee-Level-Zeile (z.B. Joint/Select ohne Subs) -> nur ins
            # Committee-Level-Dict, kein Pseudo-Subcommittee-Key.
            if subcommittee:
                key = (chamber, committee, subcommittee)
                if key not in self.mapping:
                    self.mapping[key] = []
                self.mapping[key].append(entry)

            # Add to committee-level mapping (for aggregation)
            comm_key = (chamber, committee)
            if comm_key not in self.committee_mapping:
                self.committee_mapping[comm_key] = []
            self.committee_mapping[comm_key].append(entry)

        # Calculate statistics
        self.stats['total_mappings'] = len(df)
        self.stats['unique_subcommittees'] = len(self.mapping)
        self.stats['unique_committees'] = len(self.committee_mapping)

        logger.info(
            f"Loaded {self.stats['total_mappings']} mappings: "
            f"{self.stats['unique_subcommittees']} subcommittees, "
            f"{self.stats['unique_committees']} committees"
        )

    def get_sectors_for_subcommittee(
        self,
        chamber: str,
        committee: str,
        subcommittee: str
    ) -> List[Dict[str, Any]]:
        """
        Get all sectors/industries for a specific subcommittee.

        Args:
            chamber: 'House' or 'Senate'
            committee: Committee name
            subcommittee: Subcommittee name

        Returns:
            List of dicts with sector, industry, rank, confidence
            Empty list if not found
        """
        if self.revision is not None:
            result = self._revision_subcommittee(chamber, committee,
                                                 subcommittee)
        else:
            key = (self._normalize(chamber), self._normalize(committee),
                   self._normalize(subcommittee))
            result = self.mapping.get(key, [])

        if not result:
            logger.debug(
                f"No mapping found for {chamber}/{committee}/{subcommittee}"
            )

        # Return copy to prevent external modification
        return [entry.copy() for entry in result]

    def get_all_sectors_for_committee(
        self,
        chamber: str,
        committee: str
    ) -> List[Dict[str, Any]]:
        """
        Get all sectors for a main committee (aggregates all its subcommittees).

        Useful for Ex Officio members or when subcommittee info is missing.

        Args:
            chamber: 'House' or 'Senate'
            committee: Committee name

        Returns:
            List of dicts with sector, industry, rank, confidence
            Empty list if not found
        """
        if self.revision is not None:
            result = _lookup_committee(self._index(),
                                       _chamber_key(chamber), committee) or []
        else:
            key = (self._normalize(chamber), self._normalize(committee))
            result = self.committee_mapping.get(key, [])

        if not result:
            logger.debug(
                f"No mapping found for {chamber}/{committee}"
            )

        return dedupe_entries(result)

    def get_statistics(self) -> Dict[str, int]:
        """
        Return statistics about loaded mappings.

        Returns:
            Dict with total_mappings, unique_subcommittees, unique_committees
        """
        return self.stats.copy()
