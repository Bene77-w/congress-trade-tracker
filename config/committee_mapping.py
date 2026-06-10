import pandas as pd
import os
import logging
from typing import Dict, List, Tuple, Optional, Any
from config import settings

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CommitteeMapper:
    """
    Maps congressional subcommittees to industry sectors and industries.

    Loads subcommittee_sector_industry_mapping.csv and provides fast O(1) lookup
    of sectors/industries for any subcommittee.
    """

    def __init__(self, mapping_path: Optional[str] = None):
        """
        Initialize mapper and load mapping data.

        Args:
            mapping_path: Path to mapping CSV (defaults to settings.COMMITTEE_MAPPING_FILE)
        """
        if mapping_path is None:
            filename = getattr(settings, 'COMMITTEE_MAPPING_FILE', 'subcommittee_sector_industry_mapping.csv')
            mapping_path = os.path.join(BASE_DIR, settings.DATA_MAPPINGS_PATH, filename)

        self.mapping_path = mapping_path
        self.mapping: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        self.committee_mapping: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self.stats = {
            'total_mappings': 0,
            'unique_subcommittees': 0,
            'unique_committees': 0
        }

        self._load_mapping()

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
        chamber_norm = self._normalize(chamber)
        committee_norm = self._normalize(committee)
        subcommittee_norm = self._normalize(subcommittee)

        key = (chamber_norm, committee_norm, subcommittee_norm)
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
        chamber_norm = self._normalize(chamber)
        committee_norm = self._normalize(committee)

        key = (chamber_norm, committee_norm)
        result = self.committee_mapping.get(key, [])

        if not result:
            logger.debug(
                f"No mapping found for {chamber}/{committee}"
            )

        # Deduplicate by sector/industry combination
        unique_entries = {}
        for entry in result:
            unique_key = (entry['sector'], entry['industry'])
            if unique_key not in unique_entries:
                unique_entries[unique_key] = entry.copy()
            else:
                # Keep the one with better rank
                if entry['rank'] < unique_entries[unique_key]['rank']:
                    unique_entries[unique_key] = entry.copy()

        return list(unique_entries.values())

    def get_statistics(self) -> Dict[str, int]:
        """
        Return statistics about loaded mappings.

        Returns:
            Dict with total_mappings, unique_subcommittees, unique_committees
        """
        return self.stats.copy()
