import logging
from typing import Dict, List, Any, Optional
from config import settings
from config.committee_mapping import CommitteeMapper

logger = logging.getLogger(__name__)


class CommitteeParser:
    """
    Parses complex committee strings from member CSVs.

    Format: "Committee|Role:Subcommittee|Role;NextCommittee|..."
    Separators: `;` = committees, `|` = role, `:` = subcommittees
    """

    @staticmethod
    def parse_committees(committee_string: str, chamber: str) -> List[Dict[str, Any]]:
        """
        Parse committee string into structured data.

        Args:
            committee_string: Pipe-delimited committee string
            chamber: 'House' or 'Senate'

        Returns:
            List of dicts:
            [
                {
                    'committee': 'Armed Services',
                    'role': 'Chairman',
                    'subcommittees': [
                        {'name': 'Seapower', 'role': 'Ranking Member'},
                        {'name': 'Cyber', 'role': ''}
                    ]
                },
                ...
            ]
        """
        if not committee_string or not isinstance(committee_string, str):
            return []

        try:
            committees = []

            # Split by ';' to get individual committees
            committee_parts = committee_string.split(';')

            for part in committee_parts:
                part = part.strip()
                if not part:
                    continue

                # Check if this committee has subcommittees (contains ':')
                if ':' in part:
                    # Split by ':' to separate main committee from subcommittees
                    main_and_subs = part.split(':')
                    main_part = main_and_subs[0].strip()
                    sub_parts = main_and_subs[1:]

                    # Parse main committee (format: "Name|Role" or just "Name")
                    committee_name, main_role = CommitteeParser._parse_name_and_role(main_part)

                    # Parse subcommittees
                    subcommittees = []
                    for sub_part in sub_parts:
                        sub_part = sub_part.strip()
                        if not sub_part:
                            continue

                        sub_name, sub_role = CommitteeParser._parse_name_and_role(sub_part)
                        if sub_name:
                            subcommittees.append({
                                'name': sub_name,
                                'role': sub_role
                            })

                    committees.append({
                        'committee': committee_name,
                        'role': main_role,
                        'subcommittees': subcommittees
                    })

                else:
                    # No subcommittees, just a main committee
                    committee_name, role = CommitteeParser._parse_name_and_role(part)
                    if committee_name:
                        committees.append({
                            'committee': committee_name,
                            'role': role,
                            'subcommittees': []
                        })

            return committees

        except Exception as e:
            logger.warning(f"Failed to parse committee string: {e}")
            logger.debug(f"Malformed string: {committee_string}")
            return []

    @staticmethod
    def _parse_name_and_role(text: str) -> tuple[str, str]:
        """
        Parse "Name|Role" or just "Name" into name and role.

        Args:
            text: Text to parse

        Returns:
            Tuple of (name, role)
        """
        text = text.strip()
        if not text:
            return '', ''

        # Split by '|' to separate name from role
        parts = text.split('|')

        if len(parts) >= 2:
            name = parts[0].strip()
            role = parts[1].strip()
        else:
            name = parts[0].strip()
            role = ''

        return name, role


class CommitteeFilter:
    """
    Filters trades based on committee-sector relevance.

    Combines CommitteeParser and CommitteeMapper to determine
    if a politician's committees are relevant to a trade.
    """

    def __init__(self, mapper: Optional[CommitteeMapper] = None):
        """
        Initialize filter.

        Args:
            mapper: CommitteeMapper instance (creates default if None)
        """
        self.mapper = mapper or CommitteeMapper()
        self.parser = CommitteeParser()

        # Load weights from settings
        self.rank_weights = getattr(settings, 'COMMITTEE_RANK_WEIGHTS', {
            1: 1.00, 2: 0.75, 3: 0.50, 4: 0.25
        })
        self.confidence_weights = getattr(settings, 'COMMITTEE_CONFIDENCE_WEIGHTS', {
            'Hoch': 1.0, 'Mittel': 0.7, 'Niedrig': 0.4
        })
        self.role_weights = getattr(settings, 'COMMITTEE_ROLE_WEIGHTS', {
            'Chairman': 1.5, 'Ranking Member': 1.5,
            'Vice Chairman': 1.2, 'Cochairman': 1.5,
            '': 1.0
        })

    def calculate_relevance(
        self,
        committees_string: str,
        chamber: str,
        ticker: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculate committee relevance for a trade.

        Args:
            committees_string: Politician's committee memberships
            chamber: 'House' or 'Senate'
            ticker: Stock ticker (optional, for future Modul 5)

        Returns:
            {
                'relevant_sectors': [...],
                'relevance_score': 85,
                'confidence_level': 'Hoch',
                'match_count': 3
            }
        """
        # 1. Parse committee string
        parsed = self.parser.parse_committees(committees_string, chamber)

        if not parsed:
            return {
                'relevant_sectors': [],
                'relevance_score': 0,
                'confidence_level': '',
                'match_count': 0
            }

        # 2. For each committee/subcommittee: lookup sectors
        all_sectors = []

        for comm in parsed:
            # Process subcommittees
            if comm['subcommittees']:
                for subcom in comm['subcommittees']:
                    sectors = self.mapper.get_sectors_for_subcommittee(
                        chamber, comm['committee'], subcom['name']
                    )
                    # Attach role info for scoring
                    role = subcom['role'] or comm['role']
                    for s in sectors:
                        s['role'] = role
                        s['committee'] = comm['committee']
                        s['subcommittee'] = subcom['name']
                    all_sectors.extend(sectors)

            # If no subcommittees, get committee-level sectors
            else:
                sectors = self.mapper.get_all_sectors_for_committee(
                    chamber, comm['committee']
                )
                for s in sectors:
                    s['role'] = comm['role']
                    s['committee'] = comm['committee']
                    s['subcommittee'] = ''
                all_sectors.extend(sectors)

        # 3. Deduplicate (keep highest scoring entry for each sector/industry)
        unique_sectors = self._deduplicate_sectors(all_sectors)

        # 4. Calculate weighted relevance score
        relevance_score = self._calculate_score(unique_sectors)

        # 5. Determine confidence level (highest from matches)
        confidence_level = self._get_highest_confidence(unique_sectors)

        # 6. Return
        return {
            'relevant_sectors': unique_sectors,
            'relevance_score': relevance_score,
            'confidence_level': confidence_level,
            'match_count': len(unique_sectors)
        }

    def _deduplicate_sectors(self, sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicate sectors, keeping the highest-scoring entry for each sector/industry.

        Args:
            sectors: List of sector dicts

        Returns:
            Deduplicated list
        """
        unique = {}

        for sector in sectors:
            key = (sector['sector'], sector['industry'])

            if key not in unique:
                unique[key] = sector
            else:
                # Keep the one with better rank (lower is better)
                if sector['rank'] < unique[key]['rank']:
                    unique[key] = sector
                # If same rank, prefer higher confidence
                elif sector['rank'] == unique[key]['rank']:
                    conf_weight_new = self.confidence_weights.get(sector['confidence'], 0.4)
                    conf_weight_existing = self.confidence_weights.get(unique[key]['confidence'], 0.4)
                    if conf_weight_new > conf_weight_existing:
                        unique[key] = sector

        return list(unique.values())

    def _calculate_score(self, sectors: List[Dict[str, Any]]) -> int:
        """
        Calculate 0-100 relevance score from sector matches.

        Scoring algorithm:
        - Base score: Rank-based (1=100, 2=75, 3=50, 4=25)
        - Confidence multiplier: Hoch=1.0, Mittel=0.7, Niedrig=0.4
        - Role multiplier: Chairman/Ranking=1.5x, Vice=1.2x, Member=1.0x
        - Result: Maximum score across all matches (capped at 100)

        Args:
            sectors: List of sector dicts with rank, confidence, role

        Returns:
            Score 0-100
        """
        if not sectors:
            return 0

        max_score = 0

        for sector in sectors:
            rank = sector.get('rank', 99)
            confidence = sector.get('confidence', 'Niedrig')
            role = sector.get('role', '')

            # Base score from rank
            base_score = self.rank_weights.get(rank, 0.0) * 100

            # Confidence multiplier
            conf_mult = self.confidence_weights.get(confidence, 0.4)

            # Role multiplier
            role_mult = self.role_weights.get(role, 1.0)

            # Calculate final score for this sector
            sector_score = base_score * conf_mult * role_mult

            # Track maximum
            max_score = max(max_score, sector_score)

        # Cap at 100
        return min(100, int(round(max_score)))

    def _get_highest_confidence(self, sectors: List[Dict[str, Any]]) -> str:
        """
        Get the highest confidence level from sector matches.

        Args:
            sectors: List of sector dicts

        Returns:
            'Hoch', 'Mittel', 'Niedrig', or empty string
        """
        if not sectors:
            return ''

        # Priority: Hoch > Mittel > Niedrig
        confidences = [s.get('confidence', '') for s in sectors]

        if 'Hoch' in confidences:
            return 'Hoch'
        elif 'Mittel' in confidences:
            return 'Mittel'
        elif 'Niedrig' in confidences:
            return 'Niedrig'
        else:
            return ''

    def filter_by_ticker_sector(
        self,
        sectors: List[Dict[str, Any]],
        ticker: str
    ) -> List[Dict[str, Any]]:
        """
        Filter sectors to only those matching ticker's actual sector.

        [PLACEHOLDER for Modul 5]
        For now, returns all sectors unchanged.

        Args:
            sectors: List of sector dicts
            ticker: Stock ticker

        Returns:
            Filtered list (currently unchanged)
        """
        # TODO: Implement in Modul 5
        # Will use yfinance or pre-built ticker->sector mapping
        return sectors
