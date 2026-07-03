import logging
import re
from typing import Dict, List, Any, Optional
from config import settings
from config.committee_mapping import CommitteeMapper
from utils.committee_parser import parse_committees_full

logger = logging.getLogger(__name__)

# Fuehrungsrolle -> Bonus-Key (SCORING_EX_OFFICIO_ROLE_BONUSES), getrennt nach
# Ebene. Cochairman zaehlt als Chairman seiner Ebene (User-Entscheidung);
# Chairwoman-Varianten defensiv mit abgedeckt, obwohl die F2-Producer sie
# bereits zu Chairman normalisieren. Unbekannte/leere Rollen -> kein Key.
COMMITTEE_ROLE_KEYS = {
    'chairman':        'committee_chairman',
    'chairwoman':      'committee_chairman',
    'cochairman':      'committee_chairman',
    'cochairwoman':    'committee_chairman',
    'vicechairman':    'committee_vice_chairman',
    'vicechairwoman':  'committee_vice_chairman',
    'rankingmember':   'committee_ranking_member',
    'exofficio':       'committee_ex_officio',
}
SUBCOMMITTEE_ROLE_KEYS = {
    'chair':           'subcommittee_chair',
    'chairman':        'subcommittee_chair',
    'chairwoman':      'subcommittee_chair',
    'cochair':         'subcommittee_chair',
    'cochairman':      'subcommittee_chair',
    'vicechair':       'subcommittee_vice_chair',
    'vicechairman':    'subcommittee_vice_chair',
    'rankingmember':   'subcommittee_ranking_member',
    'exofficio':       'subcommittee_ex_officio',
}


def _normalize_role(role: Any) -> str:
    """Reduziert eine Rollen-Bezeichnung auf Kleinbuchstaben a-z."""
    if role is None:
        return ''
    return re.sub(r'[^a-z]', '', str(role).lower())


def role_keys_for_entries(entries: List[Dict[str, Any]]) -> List[str]:
    """Leitet die Menge der Fuehrungsrollen-Keys aus Sektor-Eintraegen ab.

    Jeder Eintrag traegt beide Rollen-Ebenen explizit (committee_role +
    subcommittee_role). Rueckgabe sortiert, damit die Master-Spalte
    deterministisch bleibt.
    """
    keys = set()
    for entry in entries:
        key = COMMITTEE_ROLE_KEYS.get(
            _normalize_role(entry.get('committee_role')))
        if key:
            keys.add(key)
        key = SUBCOMMITTEE_ROLE_KEYS.get(
            _normalize_role(entry.get('subcommittee_role')))
        if key:
            keys.add(key)
    return sorted(keys)


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
                'ex_officio_role_keys': str,  # ';'-getrennte Fuehrungsrollen-
                                              #   Keys der relevanten Treffer
                                              #   (prae-Dedup abgeleitet)
            }
        """
        empty_result = {
            'insider_sectors': [],
            'relevant_sectors': [],
            'match_count': 0,
            'ex_officio_role_keys': '',
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
                'ex_officio_role_keys': '',
            }

        relevant_sectors = self._match_pass(unique_sectors,
                                            t_sector_n, t_industry_n)

        # Fuehrungsrollen-Keys PRAE-Dedup ableiten: die Dedup behaelt pro
        # (sector, industry) nur den Eintrag mit niedrigstem Rank — dabei kann
        # ein Fuehrungsrollen-Eintrag von einem rollenlosen verdraengt werden.
        # Fuer den Bonus zaehlt aber jede Fuehrungsrolle, deren (Sub-)Committee
        # den Trade-Sektor trifft → Match-Pass ueber die ungedupte Liste.
        role_keys = role_keys_for_entries(
            self._match_pass(all_sectors, t_sector_n, t_industry_n)
        )

        return {
            'insider_sectors': unique_sectors,
            'relevant_sectors': relevant_sectors,
            'match_count': len(relevant_sectors),
            'ex_officio_role_keys': ';'.join(role_keys),
        }

    def _match_pass(
        self,
        entries: List[Dict[str, Any]],
        t_sector_n: str,
        t_industry_n: str,
    ) -> List[Dict[str, Any]]:
        """Match-Pass ueber Sektor-Eintraege, Auswahl nach match_mode.

        Liefert Kopien der getroffenen Eintraege, jeweils mit ``match_type``
        (``'full'`` = Sector UND Industry, ``'sector'`` = nur Sector).
        """
        full_matches: List[Dict[str, Any]] = []
        sector_only_matches: List[Dict[str, Any]] = []

        for entry in entries:
            entry_sector = self._norm(entry.get('sector'))
            entry_industry = self._norm(entry.get('industry'))

            hit_sector = bool(t_sector_n) and entry_sector == t_sector_n
            hit_industry = bool(t_industry_n) and entry_industry == t_industry_n

            if hit_sector and hit_industry:
                full_matches.append(dict(entry, match_type='full'))
            elif hit_sector:
                sector_only_matches.append(dict(entry, match_type='sector'))
            # hit_industry-only existiert per Hierarchie nicht — Industry
            # impliziert immer einen Sector. Wenn Industry trifft aber Sector
            # nicht, ist das ein Datenfehler im Mapping und wird verworfen.

        if self.match_mode == 'sector_and_industry':
            return full_matches
        if self.match_mode == 'hybrid':
            return full_matches if full_matches else sector_only_matches
        # 'sector_or_industry'
        return full_matches + sector_only_matches

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
