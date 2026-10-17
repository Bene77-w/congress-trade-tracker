"""Roster-Coverage des Committee Matchers V2 (Plan 2.3).

Einzige Roster-Implementierung des Pakets. Die produktiven House-/Senate-Roster
werden read-only ueber ``utils.committee_parser.parse_committees_full``
eingelesen; Roster-Daten pruefen ausschliesslich Coverage und beeinflussen
keine fachliche Mappingentscheidung.

::

    coverage_status = mapped | excluded | missing
    match_basis     = exact | alias | manual_exclusion | null

- ``mapped``: Unit-ID vorhanden, Matchbasis ``exact`` oder ``alias``;
- ``excluded``: Unit-ID ``null``, Matchbasis ``manual_exclusion``;
- ``missing``: Unit-ID und Matchbasis ``null``, Handlungshinweis Pflicht.

Aliasse und Ausschluesse liegen versioniert und begruendet in
``data/config/committee_matcher_roster_rules.yaml``. Fuzzy Matching erzeugt
ausschliesslich Vorschlaege und niemals eine Zuordnung.

Joint-Regel: dasselbe Joint Committee taucht in beiden Rostern auf. Beide
Vorkommen zeigen auf dieselbe Joint-Unit; die Kammer des Rosters bleibt als
Provenienz erhalten, es entsteht keine zweite Unit.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import paths
from .errors import ErrorCollector, Stage
from .models import MatcherUnit, RosterCoverageRow
from .status import CoverageStatus, MatchBasis, UnitKind
from .units import match_key

#: Kontrollinput mit Aliassen und Ausschluessen.
ROSTER_RULES_PATH = paths.ROSTER_RULES_PATH
ROSTER_RULES_VERSION = "roster-rules-v1"

#: Kammern mit eigenem Roster und ihr Dateiname in ``config/settings.py``.
ROSTER_SETTINGS = {
    "house": "HOUSE_COMMITTEES_FILE",
    "senate": "SENATE_COMMITTEES_FILE",
}

#: Spalte des Rosters mit dem F2-Committee-String.
COMMITTEE_COLUMN = "Committees"

#: Mindestaehnlichkeit eines Fuzzy-Vorschlags in Prozent. Bewusst hoch und mit
#: ``token_sort_ratio`` statt Teilstringlogik: ein Vorschlag ist eine Hilfe fuer
#: Bene und darf nicht wie ein Treffer aussehen.
FUZZY_SUGGESTION_THRESHOLD = 88


@dataclass(frozen=True)
class RosterEntry:
    """Ein eindeutiges Rostervorkommen mit Provenienz."""

    chamber: str
    committee: str
    subcommittee: str | None
    members: int

    @property
    def unit_name(self) -> str:
        return self.subcommittee or self.committee


@dataclass
class RosterRules:
    """Manuelle Alias- und Ausschlussregeln."""

    aliases: dict[tuple[str, str, str | None], str]
    exclusions: dict[tuple[str, str, str | None], str]
    version: str = ROSTER_RULES_VERSION


def _rule_key(chamber: str, committee: str, subcommittee: str | None) -> tuple[str, str, str | None]:
    return (
        chamber.strip().casefold(),
        match_key(committee),
        match_key(subcommittee) if subcommittee else None,
    )


def load_rules(collector: ErrorCollector, path: Path = ROSTER_RULES_PATH) -> RosterRules:
    """Liest die versionierten Roster-Regeln.

    Fehlt die Datei, arbeitet die Coverage ohne Aliasse weiter und meldet das
    als Warnung: unbekannte Rosternamen erscheinen dann als ``missing``.
    """
    empty = RosterRules(aliases={}, exclusions={})
    if not path.exists():
        collector.warning(
            "ROS_RULES_MISSING", Stage.ROSTER, paths.relative_to_base(path),
            "Roster-Regeln fehlen; es gelten keine Aliasse und Ausschluesse.",
            "Datei anlegen, sobald Bene Aliasse oder Ausschluesse festlegt.",
        )
        return empty
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        collector.blocker(
            "ROS_RULES_MALFORMED", Stage.ROSTER, paths.relative_to_base(path),
            f"Roster-Regeln sind kein gueltiges YAML ({exc.__class__.__name__}).",
            "Datei korrigieren.",
        )
        return empty
    if not isinstance(data, dict):
        collector.blocker(
            "ROS_RULES_MALFORMED", Stage.ROSTER, paths.relative_to_base(path),
            "Roster-Regeln sind kein Mapping.",
            "Datei gemaess Vertrag schreiben.",
        )
        return empty
    if data.get("rules_version") != ROSTER_RULES_VERSION:
        collector.blocker(
            "ROS_RULES_VERSION", Stage.ROSTER, paths.relative_to_base(path),
            f"rules_version={data.get('rules_version')!r} statt {ROSTER_RULES_VERSION!r}.",
            "Regeln gemeinsam mit ihrer Version pflegen.",
        )
        return empty

    aliases: dict[tuple[str, str, str | None], str] = {}
    exclusions: dict[tuple[str, str, str | None], str] = {}
    for kind, target in (("aliases", aliases), ("exclusions", exclusions)):
        entries = data.get(kind) or []
        if not isinstance(entries, list):
            collector.blocker(
                "ROS_RULES_MALFORMED", Stage.ROSTER, paths.relative_to_base(path),
                f"'{kind}' ist keine Liste.",
                "Datei gemaess Vertrag schreiben.",
            )
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("committee"):
                collector.blocker(
                    "ROS_RULES_ENTRY", Stage.ROSTER, paths.relative_to_base(path),
                    f"Eintrag in '{kind}' ohne 'committee'.",
                    "Eintrag vervollstaendigen.",
                )
                continue
            if not str(entry.get("reason", "")).strip():
                collector.blocker(
                    "ROS_RULES_REASON", Stage.ROSTER, str(entry.get("committee")),
                    f"Eintrag in '{kind}' ohne Begruendung.",
                    "Jede manuelle Regel wird begruendet.",
                )
                continue
            key = _rule_key(
                str(entry.get("chamber", "")),
                str(entry["committee"]),
                str(entry["subcommittee"]) if entry.get("subcommittee") else None,
            )
            if kind == "aliases":
                if not entry.get("unit_id"):
                    collector.blocker(
                        "ROS_RULES_ENTRY", Stage.ROSTER, str(entry["committee"]),
                        "Alias ohne 'unit_id'.",
                        "Ziel-Unit-ID eintragen.",
                    )
                    continue
                target[key] = str(entry["unit_id"])
            else:
                target[key] = str(entry["reason"])
    return RosterRules(aliases=aliases, exclusions=exclusions)


def read_roster(
    collector: ErrorCollector, chamber: str, path: Path
) -> tuple[RosterEntry, ...]:
    """Liest ein Roster und aggregiert eindeutige Committee-/Subcommittee-Vorkommen."""
    from utils.committee_parser import parse_committees_full

    if not path.exists():
        collector.blocker(
            "ROS_FILE_MISSING", Stage.ROSTER, paths.relative_to_base(path),
            f"Roster der Kammer {chamber} fehlt.",
            "Roster-CSV bereitstellen oder Pfad in config/settings.py korrigieren.",
        )
        return ()
    paths.assert_readable(path)

    counts: dict[tuple[str, str | None], int] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if COMMITTEE_COLUMN not in (reader.fieldnames or []):
            collector.blocker(
                "ROS_COLUMN_MISSING", Stage.ROSTER, paths.relative_to_base(path),
                f"Spalte {COMMITTEE_COLUMN!r} fehlt.",
                "Roster-Producer pruefen.",
            )
            return ()
        for row in reader:
            for committee in parse_committees_full(row.get(COMMITTEE_COLUMN)):
                name = str(committee.get("committee", "")).strip()
                if not name:
                    continue
                counts[(name, None)] = counts.get((name, None), 0) + 1
                for sub in committee.get("subcommittees", []):
                    sub_name = str(sub.get("name", "")).strip()
                    if sub_name:
                        key = (name, sub_name)
                        counts[key] = counts.get(key, 0) + 1

    return tuple(
        RosterEntry(chamber=chamber, committee=committee, subcommittee=sub, members=members)
        for (committee, sub), members in sorted(
            counts.items(), key=lambda item: (item[0][0], item[0][1] or "")
        )
    )


def _unit_index(units: "tuple[MatcherUnit, ...] | list[MatcherUnit]") -> dict:
    """Baut die Nachschlagetabellen fuer Committee- und Subcommittee-Units."""
    committees: dict[tuple[str, str], MatcherUnit] = {}
    subcommittees: dict[tuple[str, str, str], MatcherUnit] = {}
    for unit in units:
        if unit.kind is UnitKind.SUBCOMMITTEE:
            subcommittees[
                (unit.chamber, match_key(unit.committee_name), match_key(unit.unit_name))
            ] = unit
        else:
            committees[(unit.chamber, match_key(unit.committee_name))] = unit
    return {"committees": committees, "subcommittees": subcommittees}


def _lookup_committee(index: dict, chamber: str, committee: str) -> MatcherUnit | None:
    """Sucht die Committee-Unit; Joint-Committees gelten kammeruebergreifend."""
    key = match_key(committee)
    unit = index["committees"].get((chamber, key))
    if unit is not None:
        return unit
    return index["committees"].get(("joint", key))


def _fuzzy_suggestion(name: str, candidates: "list[str]") -> str | None:
    if not candidates:
        return None
    try:
        from rapidfuzz import fuzz, process
    except ImportError:  # pragma: no cover - RapidFuzz ist gepinnt vorhanden
        return None
    best = process.extractOne(
        match_key(name), candidates, scorer=fuzz.token_sort_ratio
    )
    if best and best[1] >= FUZZY_SUGGESTION_THRESHOLD:
        return best[0]
    return None


def build_coverage(
    collector: ErrorCollector,
    units: "tuple[MatcherUnit, ...] | list[MatcherUnit]",
    entries: "tuple[RosterEntry, ...] | list[RosterEntry]",
    rules: RosterRules,
) -> tuple[RosterCoverageRow, ...]:
    """Bildet je Rostervorkommen genau eine Coverage-Zeile."""
    index = _unit_index(units)
    known_ids = {unit.unit_id for unit in units}
    committee_keys = sorted({key for _, key in index["committees"]})
    rows: list[RosterCoverageRow] = []

    for entry in entries:
        key = _rule_key(entry.chamber, entry.committee, entry.subcommittee)
        excluded = rules.exclusions.get(key)
        if excluded is not None:
            rows.append(
                RosterCoverageRow(
                    chamber=entry.chamber,
                    committee_name=entry.committee,
                    unit_name=entry.unit_name,
                    coverage_status=CoverageStatus.EXCLUDED,
                    match_basis=MatchBasis.MANUAL_EXCLUSION,
                    hint=excluded,
                )
            )
            continue

        alias = rules.aliases.get(key)
        if alias is not None:
            if alias not in known_ids:
                collector.blocker(
                    "ROS_ALIAS_UNKNOWN", Stage.ROSTER, alias,
                    f"Alias fuer {entry.unit_name!r} zeigt auf eine unbekannte Unit.",
                    "Alias auf eine bestehende Unit-ID korrigieren.",
                )
                continue
            rows.append(
                RosterCoverageRow(
                    chamber=entry.chamber,
                    committee_name=entry.committee,
                    unit_name=entry.unit_name,
                    coverage_status=CoverageStatus.MAPPED,
                    unit_id=alias,
                    match_basis=MatchBasis.ALIAS,
                )
            )
            continue

        committee_unit = _lookup_committee(index, entry.chamber, entry.committee)
        unit: MatcherUnit | None
        if entry.subcommittee is None:
            unit = committee_unit
        elif committee_unit is None:
            unit = None
        else:
            unit = index["subcommittees"].get(
                (
                    committee_unit.chamber,
                    match_key(committee_unit.committee_name),
                    match_key(entry.subcommittee),
                )
            )

        if unit is not None:
            rows.append(
                RosterCoverageRow(
                    chamber=entry.chamber,
                    committee_name=entry.committee,
                    unit_name=entry.unit_name,
                    coverage_status=CoverageStatus.MAPPED,
                    unit_id=unit.unit_id,
                    match_basis=MatchBasis.EXACT,
                )
            )
            continue

        if entry.subcommittee is None:
            candidates = committee_keys
        elif committee_unit is None:
            candidates = committee_keys
        else:
            candidates = sorted(
                sub
                for chamber, committee, sub in index["subcommittees"]
                if (chamber, committee)
                == (committee_unit.chamber, match_key(committee_unit.committee_name))
            )
        suggestion = _fuzzy_suggestion(entry.unit_name, candidates)
        hint = (
            "Alias oder Ausschluss in "
            f"{paths.relative_to_base(ROSTER_RULES_PATH)} ergaenzen"
        )
        if suggestion:
            hint += f"; Vorschlag (unverbindlich): {suggestion!r}"
        rows.append(
            RosterCoverageRow(
                chamber=entry.chamber,
                committee_name=entry.committee,
                unit_name=entry.unit_name,
                coverage_status=CoverageStatus.MISSING,
                hint=hint,
                fuzzy_suggestion=suggestion,
            )
        )
    return tuple(rows)


def roster_paths() -> dict[str, Path]:
    """Roster-Pfade aus den bestehenden Projektsettings."""
    from config import settings as project_settings

    result: dict[str, Path] = {}
    for chamber, setting in ROSTER_SETTINGS.items():
        filename = getattr(project_settings, setting, None)
        if filename:
            result[chamber] = paths.MAPPING_ROOT / str(filename)
    return result


def report_gaps(
    collector: ErrorCollector,
    units: "tuple[MatcherUnit, ...] | list[MatcherUnit]",
    rows: "tuple[RosterCoverageRow, ...] | list[RosterCoverageRow]",
) -> tuple[str, ...]:
    """Meldet beide Luecken-Richtungen und liefert die Units ohne Rostervorkommen.

    Beide Richtungen sind Warnungen, keine Blocker: ob daraus Alias, Ausschluss
    oder ein offener Punkt wird, entscheidet Gate C. Sie muessen aber Befunde
    sein, damit der Lauf nicht faelschlich mit Exit 0 endet.
    """
    for row in rows:
        if row.coverage_status is CoverageStatus.MISSING:
            collector.warning(
                "ROS_UNIT_MISSING", Stage.ROSTER,
                f"{row.chamber}: {row.unit_name}",
                "Rostervorkommen hat keine Unit.",
                row.hint or "Alias oder Ausschluss festlegen.",
            )
    covered = {row.unit_id for row in rows if row.unit_id}
    without_roster = tuple(
        sorted(unit.unit_id for unit in units if unit.unit_id not in covered)
    )
    for unit_id in without_roster:
        collector.warning(
            "ROS_UNIT_WITHOUT_ROSTER", Stage.ROSTER, unit_id,
            "Unit kommt in keinem Roster vor.",
            "Rosterschreibweise per Alias verbinden oder die Abwesenheit "
            "in den Rosterregeln begruenden.",
        )
    return without_roster


def run_coverage(
    collector: ErrorCollector,
    units: "tuple[MatcherUnit, ...] | list[MatcherUnit]",
    rules_path: Path = ROSTER_RULES_PATH,
) -> tuple[RosterCoverageRow, ...]:
    """Liest beide Roster und liefert die vollstaendige Coverage."""
    rules = load_rules(collector, rules_path)
    entries: list[RosterEntry] = []
    for chamber, path in sorted(roster_paths().items()):
        entries.extend(read_roster(collector, chamber, path))
    return build_coverage(collector, units, entries, rules)
