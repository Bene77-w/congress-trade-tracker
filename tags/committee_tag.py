import logging
from typing import Dict, List, Any, Optional
from config import settings
from config.committee_mapping import CommitteeMapper
from utils.committee_parser import parse_committees_full

logger = logging.getLogger(__name__)


class CommitteeTag:
    """
    Tagger: annotiert Trades mit Committee-Sector-Relevanzinfo.

    Schreibt Insider_Sectors und Relevant_Sectors in den Master. Kombiniert
    den zentralen F2-Parser (utils/committee_parser.py) und CommitteeMapper,
    um zu bestimmen, ob die Committee-Zugehoerigkeit eines Politikers
    thematisch zum Trade passt.

    In der User-Sprache entspricht dieser Tag dem historischen "Filter 3".
    """

    def __init__(self, mapper: Optional[CommitteeMapper] = None):
        """
        Initialize tag.

        Args:
            mapper: CommitteeMapper instance (creates default if None)
        """
        self.mapper = mapper or CommitteeMapper()

        self.ex_officio_roles = {
            r.strip().lower()
            for r in getattr(settings, 'COMMITTEE_EX_OFFICIO_ROLES',
                             ['Chairman', 'Ranking Member', 'Vice Chairman',
                              'Cochairman', 'EXOfficio'])
        }
        # Match-Modus fuer Intersection Insider_Sectors × Ticker
        self.match_mode = getattr(settings, 'RELEVANT_SECTORS_MATCH_MODE',
                                  'sector_or_industry')

    @staticmethod
    def _norm(value: Any) -> str:
        """Normalisiert Sektor/Industry-Strings für robusten Vergleich."""
        if value is None:
            return ''
        s = str(value).strip().lower()
        if s in ('nan', 'none', 'n/a'):
            return ''
        return s

    def calculate_relevance(
        self,
        committees_string: str,
        chamber: str,
        ticker: Optional[str] = None,
        ticker_sector: Optional[str] = None,
        ticker_industry: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Berechnet Insider_Sectors (alle Committee-Sektoren des Members) und
        Relevant_Sectors (Intersection mit Ticker_Sector/Industry, ggf. nach
        match_mode gefiltert). Jeder Eintrag in Relevant_Sectors traegt das
        Feld ``match_type`` (``'full'`` wenn Sector UND Industry treffen,
        ``'sector'`` wenn nur Sector trifft — ``'industry-only'`` existiert
        nicht, weil Industry per Hierarchie immer einen Sector impliziert).

        Match-Modi (settings.RELEVANT_SECTORS_MATCH_MODE):
            'sector_or_industry'  — full + sector landen in der Liste
            'sector_and_industry' — nur full landet in der Liste (Default)
            'hybrid'              — full bevorzugt; nur wenn keine full,
                                    landen sector-Eintraege als Fallback

        Returns:
            {
                'insider_sectors': [...],     # alle (sub)committee-Sektoren
                'relevant_sectors': [...],    # Treffer nach match_mode,
                                              #   jeder mit ``match_type``
                'match_count': int,           # = len(relevant_sectors)
            }
        """
        empty_result = {
            'insider_sectors': [],
            'relevant_sectors': [],
            'match_count': 0,
        }

        # 1. Parse committee string (F2, zentraler Parser)
        parsed = parse_committees_full(committees_string)
        if not parsed:
            return empty_result

        # 2. Mapping-Einträge sammeln. Jeder Eintrag traegt BEIDE Rollen-Ebenen
        #    explizit (committee_role + subcommittee_role) statt eines
        #    vermischten 'role' — Basis der rollen-differenzierten Ex-Officio-
        #    Boni. subcommittee == '' markiert Committee-Ebene.
        all_sectors = []
        for comm in parsed:
            if comm['subcommittees']:
                for subcom in comm['subcommittees']:
                    sectors = self.mapper.get_sectors_for_subcommittee(
                        chamber, comm['committee'], subcom['name']
                    )
                    for s in sectors:
                        s['committee_role'] = comm['role']
                        s['subcommittee_role'] = subcom['role']
                        s['committee'] = comm['committee']
                        s['subcommittee'] = subcom['name']
                    all_sectors.extend(sectors)
            else:
                sectors = self.mapper.get_all_sectors_for_committee(
                    chamber, comm['committee']
                )
                for s in sectors:
                    s['committee_role'] = comm['role']
                    s['subcommittee_role'] = ''
                    s['committee'] = comm['committee']
                    s['subcommittee'] = ''
                all_sectors.extend(sectors)

        unique_sectors = self._deduplicate_sectors(all_sectors)

        # 3. Match-Pass über alle Einträge
        t_sector_n = self._norm(ticker_sector)
        t_industry_n = self._norm(ticker_industry)

        if not t_sector_n and not t_industry_n:
            # Kein Ticker-Sektor/Industry vorhanden → keine Match-Berechnung möglich
            return {
                'insider_sectors': unique_sectors,
                'relevant_sectors': [],
                'match_count': 0,
            }

        full_matches: List[Dict[str, Any]] = []
        sector_only_matches: List[Dict[str, Any]] = []

        for entry in unique_sectors:
            entry_sector = self._norm(entry.get('sector'))
            entry_industry = self._norm(entry.get('industry'))

            hit_sector = bool(t_sector_n) and entry_sector == t_sector_n
            hit_industry = bool(t_industry_n) and entry_industry == t_industry_n

            if hit_sector and hit_industry:
                tagged = dict(entry, match_type='full')
                full_matches.append(tagged)
            elif hit_sector:
                tagged = dict(entry, match_type='sector')
                sector_only_matches.append(tagged)
            # hit_industry-only existiert per Hierarchie nicht — Industry
            # impliziert immer einen Sector. Wenn Industry trifft aber Sector
            # nicht, ist das ein Datenfehler im Mapping und wird verworfen.

        # Auswahl nach Match-Modus
        if self.match_mode == 'sector_and_industry':
            relevant_sectors = full_matches
        elif self.match_mode == 'hybrid':
            relevant_sectors = full_matches if full_matches else sector_only_matches
        else:  # 'sector_or_industry'
            relevant_sectors = full_matches + sector_only_matches

        return {
            'insider_sectors': unique_sectors,
            'relevant_sectors': relevant_sectors,
            'match_count': len(relevant_sectors),
        }

    def _deduplicate_sectors(self, sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Dedupliziert (sector, industry)-Kombinationen.
        Bei Duplikaten gewinnt der niedrigere Rank (= primärer Sektor),
        bei Gleichstand bleibt der erste Eintrag.
        """
        unique: Dict[tuple, Dict[str, Any]] = {}
        for sector in sectors:
            key = (sector['sector'], sector['industry'])
            if key not in unique:
                unique[key] = sector
            elif sector.get('rank', 99) < unique[key].get('rank', 99):
                unique[key] = sector
        return list(unique.values())

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
