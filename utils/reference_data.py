"""Ausschüsse eines Trades aus den Referenztabellen (data/reference/).

Verknüpft wird über BioGuideID und Kongress des Transaktionsdatums, nie über Namen.
Welche Zeiträume abgedeckt sind, steht allein in ``congresses.csv``; ein Kongress ohne
eigene Erhebung liest über ``data_from_congress`` die Daten eines anderen. Ausschüsse
werden immer über ``(congress, committee_id)`` gewählt.

``Member_Committees_Full`` behält das F2-Format (``utils/committee_parser.py``), damit
Tags, Signale und App unverändert lesen können. ``ReferenceMapper`` liefert dazu die
Industries eines Kongresses für ``CommitteeTag`` und die Ausschussansicht der App.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from config import settings

STATUS_MATCHED = 'matched'                    # Ausschüsse gefunden
STATUS_NO_MEMBERSHIP = 'no_membership'        # Kongress abgedeckt, keine Mitgliedschaft in den Daten
STATUS_NOT_COVERED = 'congress_not_covered'   # Transaktionsdatum in keinem Kongress aus congresses.csv

# Die Tabellen, die ReferenceData liest; der Deployment-Snapshot kopiert genau diese.
TABLES = ('congresses', 'committees', 'committee_memberships', 'committee_industries')

# F2 schreibt Rollen ebenenabhängig: Ausschuss "Chairman"/"Vice Chairman",
# Unterausschuss "Chair"/"Vice Chair". Die Quelle mischt die Formen.
_SUB_ROLES = {'Chairman': 'Chair', 'Chairwoman': 'Chair',
              'Vice Chairman': 'Vice Chair', 'Vice Chairwoman': 'Vice Chair'}
_TOP_ROLES = {'Chair': 'Chairman', 'Vice Chair': 'Vice Chairman'}


def _role(title: str, top: bool) -> str:
    role = _SUB_ROLES.get(title, title)
    return _TOP_ROLES.get(role, role) if top else role


def _name(row: Any) -> str:
    """Anzeigename im F2-String: Vollausschüsse ohne das Kammer-Präfix der Quelle."""
    return row.name if row.parent_committee_id else re.sub(r'^(House|Senate) ', '', row.name)


def _key(value: Any) -> str:
    """Kongressnummer als Text, auch wenn pandas sie als 119.0 zurückliest."""
    text = '' if value is None or pd.isna(value) else str(value).strip()
    return text[:-2] if text.endswith('.0') else text


class ReferenceData:
    """Liest die Referenztabellen einmal und ordnet Trades zu."""

    def __init__(self, ref_dir: Path | str | None = None):
        ref = Path(ref_dir) if ref_dir else settings.BASE_DIR / settings.REFERENCE_DATA_PATH

        def read(name: str) -> pd.DataFrame:
            return pd.read_csv(ref / f'{name}.csv', dtype=str, keep_default_na=False)

        congresses = read('congresses')
        self._congresses = [(r.congress, pd.Timestamp(r.start_date), pd.Timestamp(r.end_date))
                            for r in congresses.itertuples()]
        self._data_from = dict(zip(congresses.congress, congresses.data_from_congress))
        self._committees = {(r.congress, r.committee_id): r
                            for r in read('committees').itertuples()}
        self._memberships: dict[tuple, list] = {}
        for r in read('committee_memberships').itertuples():
            self._memberships.setdefault((r.congress, r.bioguide_id), []).append(r)
        self._industries: dict[tuple, list[dict]] = {}
        for r in read('committee_industries').itertuples():
            if r.committee_id:
                self._industries.setdefault((r.congress, r.committee_id), []).append(
                    {'sector': r.sector_name, 'industry': r.industry_name, 'confidence': int(r.confidence)})
        self._mappers: dict[str, ReferenceMapper] = {}

    def congress_of(self, dates: pd.Series) -> pd.Series:
        """Kongress je Datum; leer, wenn kein Kongress aus congresses.csv das Datum abdeckt."""
        dates = pd.to_datetime(dates, errors='coerce')
        result = pd.Series('', index=dates.index, dtype=object)
        for congress, start, end in self._congresses:
            result[(dates >= start) & (dates < end)] = congress
        return result

    def member_committees(self, bioguide_id: str, congress: str) -> tuple[str, bool]:
        """(F2-String, hat mindestens eine Ex-Officio-Position) für eine Person in einem Kongress."""
        source = self._data_from.get(congress, '')
        rows = self._memberships.get((source, bioguide_id), [])
        tops: dict[str, list] = {}   # Vollausschuss-ID -> [Rolle, {Unterausschuss-ID: Rolle}]
        for row in rows:
            committee = self._committees[source, row.committee_id]
            entry = tops.setdefault(committee.parent_committee_id or row.committee_id, ['', {}])
            if committee.parent_committee_id:
                entry[1][row.committee_id] = _role(row.title, top=False)
            else:
                entry[0] = _role(row.title, top=True)
        parts = []
        for top_id in sorted(tops):
            role, subs = tops[top_id]
            text = f'{_name(self._committees[source, top_id])}|{role}'
            if subs:
                text += ':' + '~'.join(f'{_name(self._committees[source, sub_id])}|{sub_role}'
                                       for sub_id, sub_role in sorted(subs.items()))
            parts.append(text)
        return ';'.join(parts), any(row.title == 'Ex Officio' for row in rows)

    def assign(self, df: pd.DataFrame) -> pd.DataFrame:
        """Setzt Member_Congress, Member_Committee_Status, Member_Committees_Full,
        Member_ExOfficio und Member_Chamber. Partei und Bundesstaat bleiben, wie die Quelle sie liefert."""
        df = df.copy()
        congress = self.congress_of(df['transaction_date'])
        bioguide = df['bio_guide_id'].fillna('').astype(str).str.strip()
        keys = list(zip(bioguide, congress))
        found = {key: self.member_committees(*key) for key in set(keys) if key[1]}
        df['Member_Congress'] = congress
        df['Member_Committees_Full'] = [found[k][0] if k[1] else '' for k in keys]
        df['Member_Committee_Status'] = [
            STATUS_NOT_COVERED if not k[1] else STATUS_MATCHED if found[k][0] else STATUS_NO_MEMBERSHIP
            for k in keys]
        df['Member_ExOfficio'] = ['' if not k[1] or not found[k][0] else str(found[k][1]).lower() for k in keys]
        df['Member_Chamber'] = df['chamber'].fillna('').astype(str).str.strip().str.title()
        return df

    def mapper(self, congress: Any) -> 'ReferenceMapper':
        """Ausschuss -> Industries für den Kongress eines Trades (Member_Congress)."""
        source = self._data_from.get(_key(congress), '')
        if source not in self._mappers:
            self._mappers[source] = ReferenceMapper(
                source,
                {cid: row for (c, cid), row in self._committees.items() if c == source},
                {cid: rows for (c, cid), rows in self._industries.items() if c == source})
        return self._mappers[source]


class ReferenceMapper:
    """Industries je Ausschuss eines Kongresses.

    ``committees``: Ausschuss-ID -> Zeile aus ``committees.csv``; ``industries``: Ausschuss-ID ->
    Industries, beides nur dieses Kongresses. Namen sind die des F2-Strings desselben Kongresses,
    deshalb genügt ein exakter Vergleich.
    """

    def __init__(self, congress: str, committees: dict[str, Any], industries: dict[str, list[dict]]):
        self.congress = congress
        self._industries = industries
        self._ids = {(row.parent_committee_id or row.chamber, _name(row)): cid
                     for cid, row in committees.items()}

    def _committee_id(self, chamber: str, committee: str) -> str:
        for scope in (str(chamber or '').strip().lower(), 'joint'):
            if (scope, committee.strip()) in self._ids:
                return self._ids[scope, committee.strip()]
        return ''

    def get_all_sectors_for_committee(self, chamber: str, committee: str) -> list[dict]:
        return [dict(e) for e in self._industries.get(self._committee_id(chamber, committee), [])]

    def get_sectors_for_subcommittee(self, chamber: str, committee: str, subcommittee: str) -> list[dict]:
        sub_id = self._ids.get((self._committee_id(chamber, committee), subcommittee.strip()), '')
        return [dict(e) for e in self._industries.get(sub_id, [])]
