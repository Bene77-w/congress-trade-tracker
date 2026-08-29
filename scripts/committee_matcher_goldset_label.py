#!/usr/bin/env python3
"""Agentengestuetztes Labeln des Entwicklungs-Gold-Sets (Committee Matcher V2).

Das Werkzeug laesst die 22 Units der Stichprobe von einem starken Agenten
labeln - headless ueber Codex oder Claude Code, gleiche Eingabe, gleiches
Ausgabeformat. Die Labels sind ein Vorschlag; das Gold-Set entsteht erst durch
die Abnahme durch Bene.

Plan: ``organisation/committee_matcher_goldset_labeling_plan.md``.

Abgrenzung zum Pruefling
------------------------
Der Produktivpfad ``python -m committee_matcher run plan|execute`` ist der
Pruefling der spaeteren Evaluation und wird hier nicht benutzt. Erlaubt sind
die Lade- und Provenienzmodule des Pakets (``input_corpus``, ``units``,
``taxonomy_snapshot``, ``prompts``, ``settings_view``, ``errors``, ``paths``);
``provider``, ``workflow``, ``mapping``, ``artifacts`` und ``review`` bleiben
aussen vor.

Die Grenze verlaeuft zwischen Provenienz und Rendering. Inhaltshash der
Taxonomie und Hash des Modellvertrags werden bewusst mit denselben Funktionen
gebildet wie im Lauf: waeren sie es nicht, koennte die Evaluation Gold-Set und
Lauf nicht an denselben Stand binden. Das Rendering von Taxonomie und Unit baut
dieses Skript dagegen selbst - ein Renderingfehler des Prueflings darf sich
nicht in die Referenz vererben.

Exit-Codes
----------
0 Erfolg; 1 blockiert (Bedienfehler, fehlende Voraussetzung); 2 Erfolg mit
Befunden zu einzelnen Units.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from itertools import zip_longest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, NoReturn, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from committee_matcher import input_corpus, paths, prompts, settings_view  # noqa: E402
from committee_matcher import taxonomy_snapshot  # noqa: E402
from committee_matcher import units as unit_builder  # noqa: E402
from committee_matcher.errors import ErrorCollector, Severity  # noqa: E402
from committee_matcher.input_corpus import normalize_text  # noqa: E402
from committee_matcher.prompts import untrusted_block  # noqa: E402

#: Module, die dieses Skript nicht laden darf. Der Selbsttest in
#: ``tests/test_goldset_label.py`` prueft die Liste gegen die Importe.
FORBIDDEN_MODULES = (
    "committee_matcher.provider",
    "committee_matcher.workflow",
    "committee_matcher.mapping",
    "committee_matcher.artifacts",
    "committee_matcher.review",
)

GOLDSET_ROOT = paths.ARTIFACT_ROOT / "goldset"
LABELING_ROOT = GOLDSET_ROOT / "labeling"
TASK_ROOT = LABELING_ROOT / "tasks"
RUN_ROOT = LABELING_ROOT / "runs"
#: Maschinelle Zwischentabellen zwischen ``collect``, ``merge`` und ``to-xlsx``.
#: Bene bearbeitet sie nicht - dafuer ist die XLSX da.
COLLECT_ROOT = LABELING_ROOT / "collected"
MERGE_REPORT = LABELING_ROOT / "disagreements.md"
#: Die Datei, die Bene bearbeitet.
WORKBOOK_PATH = GOLDSET_ROOT / "goldset_review.xlsx"
SAMPLE_PATH = GOLDSET_ROOT / "sample_units.json"
TAXONOMY_PATH = paths.ARTIFACT_ROOT / "taxonomy" / "approved_baseline.json"
MAPPING_PROMPT_PATH = paths.PROMPT_ROOT / "mapping_system.md"

#: Fassung der Aufgabenpakete. Wird in jedes Paket geschrieben; ``run`` und
#: ``collect`` lehnen fremde Fassungen ab, statt gegen ein anderes Format zu
#: arbeiten.
TASK_FORMAT = "goldset-task-v1"
#: Fassung der Stichprobendatei.
SAMPLE_FORMAT = "goldset-sample-v1"
#: Fassung einer abgelegten Antwort.
ANSWER_FORMAT = "goldset-answer-v1"

#: Spalten des Blatts ``labels``. Bewusst als Literale gefuehrt und nicht aus
#: ``review.py`` importiert: die Uebereinstimmung mit dem Reviewvertrag ist
#: gewollt, die Kopplung an den Pruefling nicht.
LABEL_COLUMNS: tuple[str, ...] = (
    "unit_id", "industry_id", "confidence", "evidence_excerpt",
    "rationale_short", "no_mapping_reason",
)

#: Begruendungs- und Adjudikationsmaterial. Es steht bewusst neben der
#: Arbeitstabelle und nicht in ihr: Steuerspalten im Blatt ``labels`` waren im
#: Reviewpfad die Hauptfehlerquelle.
NOTE_COLUMNS: tuple[str, ...] = (
    "unit_id", "engine", "model", "label_uncertain", "uncertainty_note",
    "broad_scope", "broad_scope_reason", "sources", "dissens",
)

#: Provenienz je Unit. Sie bindet das Gold-Set an denselben Stand, gegen den
#: die Evaluation den Pruefling misst.
INFO_COLUMNS: tuple[str, ...] = (
    "unit_id", "snapshot_id", "taxonomy_content_hash", "mapping_input_hash",
    "prompt_sha256", "prompt_version", "engine", "model", "effort",
    "prompt_delivery", "created_at",
)

#: Lesehilfe: der Volltext, gegen den Bene die Zitate prueft.
JURISDICTION_COLUMNS: tuple[str, ...] = (
    "unit_id", "chamber", "committee_name", "unit_name", "input_basis",
    "jurisdiction_text",
)

#: Auswahlliste der Spalte ``industry_id``.
TAXONOMY_COLUMNS: tuple[str, ...] = (
    "industry_id", "industry_name", "sector_name", "description",
)

SHEET_LABELS = "labels"
SHEET_JURISDICTION = "jurisdiction"
SHEET_TAXONOMY = "taxonomy"
SHEET_NOTES = "agent_notes"
SHEET_INFO = "info"

#: Unterstuetzte Engines. Der Name waehlt den Aufrufweg, nicht das Modell:
#: Modell und Denktiefe sind freie Parameter, damit kuenftige Modelle ohne
#: Codeaenderung funktionieren.
ENGINES: tuple[str, ...] = ("claude", "codex")

MIN_CONFIDENCE = 1
MAX_CONFIDENCE = 4
#: Obergrenzen des Modellvertrags (``data/config/committee_matcher_prompts``).
REGULAR_ASSIGNMENT_LIMIT = 2
BROAD_SCOPE_ASSIGNMENT_LIMIT = 5
#: Die Broad-Scope-Ausnahme traegt nur hohe Confidence.
BROAD_SCOPE_MIN_CONFIDENCE = 3
#: Unittypen, die nie mehr als ``REGULAR_ASSIGNMENT_LIMIT`` Zuordnungen tragen
#: duerfen. Nachgebaut statt importiert, damit die Wache nicht dieselbe
#: Auslegung teilt wie der Pruefling.
NARROW_UNIT_KINDS: tuple[str, ...] = ("committee_parent_core",)
NAME_ONLY_BASIS = "name_only"


# -- Fehlerbild ------------------------------------------------------------
#: Vorhersehbare Stoerungen. Der Text steht hier und nicht an der Fundstelle,
#: damit dieselbe Ursache ueberall gleich heisst.
ERROR_CATALOG: dict[str, str] = {
    "GS_CLI_MISSING": "Die CLI der gewaehlten Engine ist nicht aufrufbar.",
    "GS_CLI_FLAG": "Die installierte CLI-Fassung kennt eine benutzte Option nicht.",
    "GS_CLI_UNAUTHORIZED": "Die CLI meldet eine fehlende Anmeldung.",
    "GS_MODEL_UNKNOWN": "Die CLI kennt den angegebenen Modellnamen nicht.",
    "GS_EFFORT_REJECTED": "Die CLI verwirft die angegebene Denktiefe und rechnet mit der Standardstufe.",
    "GS_WEBSEARCH_OFF": "Der Lauf hat keine Quelle genannt; die Websuche war vermutlich aus.",
    "GS_TIMEOUT": "Der Lauf hat den Zeitdeckel erreicht.",
    "GS_RATE_LIMITED": "Die CLI meldet ein erreichtes Ratenlimit des Abos.",
    "GS_SCHEMA_INVALID": "Die Antwort haelt das Ausgabeschema nicht ein.",
    "GS_UNIT_ID_MISMATCH": "Die Antwort traegt eine andere unit_id als die Anfrage.",
    "GS_INDUSTRY_UNKNOWN": "Die industry_id steht nicht im freigegebenen Snapshot.",
    "GS_EVIDENCE_NOT_VERBATIM": "Das Zitat steht nicht woertlich im Zustaendigkeitstext.",
    "GS_COUNT_EXCEEDED": "Die Zahl der Zuordnungen ueberschreitet den Modellvertrag.",
    "GS_BROAD_SCOPE_INVALID": "Die Broad-Scope-Ausnahme haelt ihre Bedingungen nicht ein.",
    "GS_DECISION_CONFLICT": "Die Unit traegt Zuordnung und no_mapping_reason zugleich.",
    "GS_UNIT_MISSING": "Eine Unit der Stichprobe fehlt.",
    "GS_UNIT_DUPLICATE": "Eine Zuordnung erscheint doppelt.",
    "GS_SNAPSHOT_DRIFT": "Die Taxonomie hat sich seit dem Aufgabenpaket geaendert.",
    "GS_CORPUS_DRIFT": "Der Zustaendigkeitstext hat sich seit dem Aufgabenpaket geaendert.",
    "GS_TASK_MISSING": "Zu dieser Unit gibt es kein Aufgabenpaket.",
    "GS_TASK_FORMAT": "Das Aufgabenpaket hat eine fremde Fassung.",
    "GS_TASK_LAYOUT": "Das Aufgabenverzeichnis enthaelt etwas anderes als Aufgabenpakete.",
    "GS_CONTRACT_INVALID": "Der Modellvertrag fehlt oder haelt seine eigene Form nicht ein.",
    "GS_SAMPLE_STALE": "Die Stichprobe beschreibt eine Unit anders als der Korpus sie liefert.",
    "GS_SAMPLE_INVALID": "Die Stichprobendatei ist unbrauchbar.",
    "GS_TAXONOMY_INVALID": "Die freigegebene Taxonomie ist unbrauchbar.",
    "GS_CORPUS_BLOCKED": "Der Eingabekorpus laesst sich nicht fehlerfrei laden.",
    "GS_UNIT_UNKNOWN": "Die Stichprobe nennt eine Unit, die der Korpus nicht kennt.",
    "GS_EVIDENCE_MISMATCH": "Der geladene Zustaendigkeitstext deckt sich nicht mit der Quelldatei.",
    "GS_SETTING_INVALID": "Ein GOLDSET_LABEL_*-Setting hat einen unbrauchbaren Wert.",
    "GS_ANSWER_FORMAT": "Eine abgelegte Antwort hat eine fremde Fassung.",
    "GS_LABELS_MISSING": "Eine Zwischentabelle des vorherigen Schritts fehlt.",
    "GS_LABELS_FORMAT": "Eine Zwischentabelle hat eine fremde Spaltenfolge.",
    "GS_MERGE_SCOPE": "Der Vergleich zweier Laeufe ist so nicht durchfuehrbar.",
    "GS_INDUSTRY_CASE": "Die industry_id weicht nur in der Schreibweise ab (Autokorrektur).",
    "GS_XLSX_MISSING": "Die Reviewdatei fehlt.",
    "GS_XLSX_LAYOUT": "Die Reviewdatei hat ein fremdes Blatt- oder Spaltenbild.",
    "GS_XLSX_OVERWRITE": "Die Zieldatei ist vorhanden und wuerde ueberschrieben.",
    "GS_XLSX_LOCKED": "Die Reviewdatei ist in einem anderen Programm geoeffnet.",
    "GS_XLSX_FORMULA": "Eine Zelle enthaelt eine Formel statt eines Texts.",
    "GS_XLSX_WHITESPACE": "Ein Zellwert traegt Leerraum am Rand.",
    "GS_FAKE_MODE": "Der Trockenlauf ist eingeschaltet, aber nicht benutzbar.",
    "GS_RUN_FAILED": "Der Lauf schlug aus einer Ursache fehl, die nicht im Katalog steht.",
    "GS_ANSWER_AMBIGUOUS": "Die Antwortdatei enthaelt mehr als ein JSON-Objekt.",
    "GS_RESUME_MISMATCH": "Eine vorhandene Antwort passt nicht zum aktuellen Lauf.",
    "GS_EFFORT_UNCONFIRMED": "Die CLI bestaetigt die verwendete Denktiefe nicht.",
}


@dataclass(frozen=True)
class Problem:
    """Ein benannter Befund mit Fundstelle im Code.

    Die Codestelle wird zur Laufzeit aus dem Aufrufrahmen ermittelt und nicht
    von Hand gepflegt: eine gepflegte Zeilennummer ist nach der ersten
    Umstellung falsch und fuehrt beim Suchen in die Irre.
    """

    code: str
    obj: str
    was: str
    warum: str
    abhilfe: str
    where: str = ""

    def render(self) -> str:
        head = ERROR_CATALOG.get(self.code, "")
        lines = [f"[{self.code}] {self.obj}"]
        if head and head not in self.was:
            lines.append(f"  Kurz:    {head}")
        lines.extend([
            f"  Was:     {self.was}",
            f"  Warum:   {self.warum}",
            f"  Abhilfe: {self.abhilfe}",
        ])
        if self.where:
            lines.append(f"  Code:    {self.where}")
        return "\n".join(lines)


class GoldsetBlocked(Exception):
    """Abbruch mit genau einem Befund."""

    def __init__(self, problem: Problem) -> None:
        super().__init__(problem.render())
        self.problem = problem


def _where(depth: int) -> str:
    """Datei und Zeile des Aufrufers, relativ zum Projektstamm."""
    frame = inspect.stack()[depth + 1]
    try:
        rel = Path(frame.filename).resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        rel = frame.filename
    return f"{rel}:{frame.lineno}"


def problem(code: str, obj: str, was: str, warum: str, abhilfe: str, *,
            depth: int = 1) -> Problem:
    """Ein Befund. ``warum`` ist Pflicht.

    Eine Meldung ohne Ursache zwingt den Leser, sie sich selbst zu erklaeren -
    genau die Arbeit, die dieses Fehlerbild abnehmen soll. Der Zwang steht in
    der Signatur, damit er beim Schreiben auffaellt und nicht erst im Betrieb.
    """
    if code not in ERROR_CATALOG:  # Schutz gegen Tippfehler im Code selbst.
        raise KeyError(f"unbekannter Fehlercode: {code}")
    return Problem(code=code, obj=obj, was=was, warum=warum, abhilfe=abhilfe,
                   where=_where(depth))


def block(code: str, obj: str, was: str, warum: str, abhilfe: str, *,
          depth: int = 2) -> NoReturn:
    """Bricht mit genau einem Befund ab.

    ``depth`` zeigt auf den Rahmen, der als Fundstelle gemeldet wird. Ruft eine
    Hilfsfunktion ``block`` auf, muss sie um ihre eigene Ebene erhoehen, sonst
    zeigt die Meldung auf die Hilfsfunktion statt auf die Pruefung.
    """
    raise GoldsetBlocked(problem(code, obj, was, warum, abhilfe, depth=depth))


# -- Settings --------------------------------------------------------------
#: Defaults der Laufparameter. Sie stehen in ``config/settings.py`` bewusst
#: ohne das Praefix ``COMMITTEE_MATCHER_``: dieses Werkzeug ist nicht Teil des
#: Matcherlaufs, seine Werte gehoeren nicht in ``settings_view`` und nicht in
#: die Auditartefakte eines Runs.
SETTING_DEFAULTS: dict[str, Any] = {
    "GOLDSET_LABEL_ENGINE": "claude",
    "GOLDSET_LABEL_MODEL_CLAUDE": "opus",
    "GOLDSET_LABEL_MODEL_CODEX": "gpt-5.6-sol",
    "GOLDSET_LABEL_EFFORT": "high",
    "GOLDSET_LABEL_TIMEOUT_SECONDS": 1800,
    "GOLDSET_LABEL_TOTAL_TIMEOUT_SECONDS": 43200,
}


#: Bereich der Zeitdeckel. Unten schneidet ein zu kleiner Wert einen
#: regulaeren Lauf ab, oben ist der Deckel keine Notbremse mehr.
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 172_800


@dataclass(frozen=True)
class LabelSettings:
    """Die Rohwerte aus ``config/settings.py``, ungeprueft.

    Geprueft wird erst der aufgeloeste Lauf (``resolve_config``): ein
    unbrauchbares Setting, das ein Parameter ohnehin uebersteuert, darf den
    Lauf nicht blockieren - sonst zwingt ein Tippfehler in einem ungenutzten
    Wert zum Editieren der Konfiguration.
    """

    engine: Any
    model_claude: Any
    model_codex: Any
    effort: Any
    timeout_seconds: Any
    total_timeout_seconds: Any

    def model_for(self, engine: str) -> Any:
        return self.model_claude if engine == "claude" else self.model_codex


def load_label_settings() -> LabelSettings:
    """Liest die ``GOLDSET_LABEL_*``-Settings mit Default, ohne zu pruefen."""
    import config.settings as settings_module

    def value(name: str) -> Any:
        return getattr(settings_module, name, SETTING_DEFAULTS[name])

    return LabelSettings(
        engine=value("GOLDSET_LABEL_ENGINE"),
        model_claude=value("GOLDSET_LABEL_MODEL_CLAUDE"),
        model_codex=value("GOLDSET_LABEL_MODEL_CODEX"),
        effort=value("GOLDSET_LABEL_EFFORT"),
        timeout_seconds=value("GOLDSET_LABEL_TIMEOUT_SECONDS"),
        total_timeout_seconds=value("GOLDSET_LABEL_TOTAL_TIMEOUT_SECONDS"),
    )


@dataclass(frozen=True)
class Choice:
    """Ein aufgeloester Wert samt der Stelle, die ihn gesetzt hat."""

    value: Any
    source: str


def _choose(parameter: str, given: Any, setting_name: str,
            settings_value: Any) -> Choice:
    """Parameter vor Setting - aber nur, wenn der Parameter gesetzt ist.

    Verglichen wird gegen ``None`` und nicht gegen die Wahrheit des Werts:
    ein ausdrueckliches ``--model ''`` ist ein Bedienfehler und muss als
    solcher gemeldet werden, statt still auf das Setting zurueckzufallen.
    """
    if given is None:
        return Choice(settings_value, f"config/settings.py:{setting_name}")
    return Choice(given, parameter)


def _require_text(choice: Choice, was: str) -> str:
    if not isinstance(choice.value, str) or not choice.value.strip():
        block("GS_SETTING_INVALID", choice.source,
              f"{was} ist {choice.value!r} und damit kein nicht-leerer Text.",
              "Der Wert wird roh an die CLI durchgereicht; leer oder falsch "
              "getippt erzeugt er dort einen Aufruffehler.",
              f"Wert an der genannten Stelle setzen.", depth=3)
    return choice.value.strip()


def _require_seconds(choice: Choice, was: str) -> int:
    raw = choice.value
    if not isinstance(raw, int) or isinstance(raw, bool) or \
            not MIN_TIMEOUT_SECONDS <= raw <= MAX_TIMEOUT_SECONDS:
        block("GS_SETTING_INVALID", choice.source,
              f"{was} ist {raw!r} und liegt nicht als ganze Zahl zwischen "
              f"{MIN_TIMEOUT_SECONDS} und {MAX_TIMEOUT_SECONDS}.",
              "Ein zu kleiner Deckel schneidet einen regulaeren Lauf ab, ein "
              "beliebig grosser ist keine Notbremse mehr.",
              "Wert an der genannten Stelle korrigieren.", depth=3)
    return raw


# -- Eingaben --------------------------------------------------------------
@dataclass(frozen=True)
class SampleUnit:
    """Eine Unit der Stichprobe.

    ``focus`` dokumentiert die Fallabdeckung und bleibt in der Stichprobendatei:
    er gelangt weder in ein Aufgabenpaket noch sonst in das Verzeichnis, das der
    Lauf liest. Ein Hinweis auf die erwartete Antwort wuerde das Gold-Set
    entwerten.
    """

    nr: int
    unit_id: str
    chamber: str
    committee_name: str
    unit_name: str
    input_basis: str
    focus: str


SAMPLE_FIELDS = ("nr", "unit_id", "chamber", "committee_name", "unit_name",
                 "input_basis", "focus")


def _sample_broken(detail: str, warum: str, abhilfe: str, obj: str = "") -> NoReturn:
    block("GS_SAMPLE_INVALID", obj or paths.relative_to_base(SAMPLE_PATH),
          detail, warum, abhilfe, depth=3)


def load_sample() -> tuple[SampleUnit, ...]:
    """Liest die Stichprobe streng.

    Streng heisst: Typen, Pflichtfelder, eindeutige IDs und die im Dokument
    selbst genannte Sollmenge. Die Menge steht als ``expected_unit_count`` in
    der Datei und nicht als Konstante im Code, damit ein spaeteres Holdout
    anderer Groesse dasselbe Werkzeug benutzen kann - gepruefte Vollstaendigkeit
    bleibt trotzdem erzwungen.
    """
    shown = paths.relative_to_base(SAMPLE_PATH)
    if not SAMPLE_PATH.exists():
        _sample_broken(
            "Die Stichprobendatei fehlt.",
            "Ohne sie ist unbekannt, welche Units zu labeln sind.",
            "Datei aus organisation/committee_matcher_goldset_stichprobe.md "
            "wiederherstellen; sie haelt die 22 Unit-IDs.")
    try:
        doc = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _sample_broken(f"Die Datei ist nicht lesbar: {error}",
                       "Ein unvollstaendiger Schreibvorgang oder ein Handfehler "
                       "im JSON.",
                       "Datei als UTF-8-JSON wiederherstellen.")
    if not isinstance(doc, dict):
        _sample_broken(f"Der Inhalt ist ein {type(doc).__name__}, kein Objekt.",
                       "Erwartet wird ein Objekt mit 'units'.",
                       "Datei gegen das Format goldset-sample-v1 pruefen.")
    if doc.get("sample_version") != SAMPLE_FORMAT:
        _sample_broken(
            f"Fassung {doc.get('sample_version')!r} statt {SAMPLE_FORMAT!r}.",
            "Eine fremde Fassung kann andere Felder tragen, als dieses "
            "Werkzeug liest.",
            "Stichprobendatei auf die erwartete Fassung bringen.")
    raw_units = doc.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        _sample_broken("Der Schluessel 'units' ist leer oder keine Liste.",
                       "Ohne Units gaebe es nichts zu labeln, der Lauf endete "
                       "still erfolgreich.",
                       "Stichprobe mit einer Zeile je Unit fuellen.")
    expected = doc.get("expected_unit_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        _sample_broken(
            f"'expected_unit_count' ist {expected!r} statt einer Zahl ab 1.",
            "Die Sollmenge bindet die Vollstaendigkeit des Satzes; ohne sie "
            "faellt eine geloeschte Zeile nicht auf.",
            "Feld auf die Zahl der Units der Stichprobe setzen.")

    sample: list[SampleUnit] = []
    seen_ids: set[str] = set()
    seen_nr: set[int] = set()
    for index, entry in enumerate(raw_units, start=1):
        obj = f"{shown}#units[{index}]"
        if not isinstance(entry, dict):
            _sample_broken(f"Der Eintrag ist ein {type(entry).__name__}, kein Objekt.",
                           "Erwartet wird je Unit ein Objekt.",
                           "Eintrag als Objekt schreiben.", obj)
        missing = [key for key in SAMPLE_FIELDS if not entry.get(key)]
        if missing:
            _sample_broken(f"Es fehlen die Felder {', '.join(missing)}.",
                           "Alle sieben Felder werden gelesen; ein fehlendes "
                           "koennte still zu einer falschen Unit fuehren.",
                           "Eintrag vervollstaendigen.", obj)
        for key in SAMPLE_FIELDS:
            if key == "nr":
                continue
            if not isinstance(entry[key], str) or not entry[key].strip():
                _sample_broken(
                    f"Feld {key!r} ist {entry[key]!r} statt eines nichtleeren "
                    "Textes.",
                    "Ein reiner Leerraum sieht gefuellt aus, bricht aber den "
                    "Vergleich gegen den Korpus.",
                    f"{key} als Text schreiben.", obj)
        # Fortlaufende Nummern werden bewusst nicht erzwungen: ein spaeterer
        # Teilsatz - etwa nur die schweren Faelle - traegt dieselben Nummern
        # wie die Gesamttabelle, aus der er stammt.
        if (not isinstance(entry["nr"], int) or isinstance(entry["nr"], bool)
                or entry["nr"] < 1):
            _sample_broken(f"Feld 'nr' ist {entry['nr']!r} statt einer ganzen "
                           "Zahl ab 1.",
                           "Die Nummer verweist auf die Zeile der Tabelle in "
                           "organisation/committee_matcher_goldset_stichprobe.md.",
                           "nr als positive ganze Zahl schreiben.", obj)
        if entry["unit_id"] in seen_ids:
            _sample_broken(f"unit_id {entry['unit_id']!r} kommt mehrfach vor.",
                           "Die Unit wuerde doppelt gelabelt und im Bericht "
                           "doppelt gezaehlt.",
                           "Doppelten Eintrag entfernen.", obj)
        if entry["nr"] in seen_nr:
            _sample_broken(f"nr {entry['nr']} kommt mehrfach vor.",
                           "Die Nummer muss eindeutig auf eine Zeile der "
                           "Stichprobentabelle zeigen.",
                           "Nummerierung korrigieren.", obj)
        seen_ids.add(entry["unit_id"])
        seen_nr.add(entry["nr"])
        sample.append(SampleUnit(
            nr=entry["nr"], unit_id=entry["unit_id"],
            chamber=entry["chamber"], committee_name=entry["committee_name"],
            unit_name=entry["unit_name"], input_basis=entry["input_basis"],
            focus=entry["focus"],
        ))
    if len(sample) != expected:
        _sample_broken(
            f"Die Datei nennt {expected} Units, enthaelt aber {len(sample)}.",
            "Eine geloeschte oder ergaenzte Zeile wuerde den Satz sonst still "
            "veraendern.",
            "Entweder die fehlende Unit ergaenzen oder "
            "'expected_unit_count' bewusst anpassen.")
    return tuple(sample)


@dataclass(frozen=True)
class Industry:
    industry_id: str
    industry_name: str
    sector_id: str
    sector_name: str
    description: str


@dataclass(frozen=True)
class Taxonomy:
    snapshot_id: str
    content_hash: str
    yfinance_version: str
    industries: tuple[Industry, ...]

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(industry.industry_id for industry in self.industries)


def _taxonomy_broken(detail: str, warum: str, abhilfe: str) -> NoReturn:
    block("GS_TAXONOMY_INVALID", paths.relative_to_base(TAXONOMY_PATH),
          detail, warum, abhilfe, depth=3)


def load_taxonomy() -> Taxonomy:
    """Liest die freigegebene Taxonomie und rechnet ihren Inhaltshash nach.

    Der im Dokument stehende Hash wird nicht geglaubt, sondern mit den
    Vertragsfunktionen des Moduls neu gebildet. Ohne Nachrechnung waere die
    spaetere Driftwache wirkungslos: ein veraenderter Inhalt mit altem Hash
    kaeme unbemerkt in die Aufgabenpakete, und die Evaluation binde Gold-Set und
    Lauf an einen Stand, den es nie gab.
    """
    if not TAXONOMY_PATH.exists():
        _taxonomy_broken(
            "Es ist keine Taxonomie freigegeben.",
            "Gelabelt wird ausschliesslich gegen einen freigegebenen Stand.",
            "'python -m committee_matcher snapshot approve --run-id <revision>' "
            "ausfuehren.")
    try:
        doc = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _taxonomy_broken(f"Die Datei ist nicht lesbar: {error}",
                         "Ein abgebrochener Schreibvorgang oder ein Handeingriff.",
                         "Snapshot neu freigeben.")
    if not isinstance(doc, dict):
        _taxonomy_broken(f"Der Inhalt ist ein {type(doc).__name__}, kein Objekt.",
                         "Erwartet wird das Baselinedokument des Snapshots.",
                         "Snapshot neu bauen und freigeben.")

    version = doc.get("content_version")
    if version != taxonomy_snapshot.TAXONOMY_CONTENT_VERSION:
        _taxonomy_broken(
            f"Inhaltsfassung {version!r} statt "
            f"{taxonomy_snapshot.TAXONOMY_CONTENT_VERSION!r}.",
            "Eine andere Fassung kann andere Felder oder eine andere "
            "Hashbildung bedeuten.",
            "Snapshot mit der aktuellen Fassung neu bauen und freigeben.")
    # Ohne Typkoerzierung: str(123) waere ein gueltig aussehender Snapshotname,
    # den der Produktivloader ablehnt. Das Gold-Set darf nicht mehr durchlassen
    # als der Lauf.
    provenance: dict[str, str] = {}
    broken = []
    for name in ("approved_run_id", "yfinance_version", "taxonomy_content_hash"):
        raw = doc.get(name)
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            # Nicht getrimmt, sondern abgelehnt: ein Hash mit Leerraum wird vom
            # Produktivloader anders gelesen als hier, und dann waeren Gold-Set
            # und Lauf an verschiedene Werte gebunden.
            broken.append(f"{name}={raw!r}")
            continue
        provenance[name] = raw
    if broken:
        _taxonomy_broken(
            f"Provenienzfelder fehlen, sind kein Text oder tragen Leerraum "
            f"am Rand: {', '.join(broken)}.",
            "Ohne Provenienz laesst sich das Gold-Set spaeter keinem Stand "
            "zuordnen.",
            "Snapshot neu freigeben; 'snapshot approve' setzt die Felder.")
    snapshot_id = provenance["approved_run_id"]
    yfinance_version = provenance["yfinance_version"]
    stated_hash = provenance["taxonomy_content_hash"]

    records = taxonomy_snapshot.strict_records(doc.get("industries"))
    if not records:
        _taxonomy_broken(
            "Die Industryliste ist leer oder verletzt den Inhaltsvertrag "
            "(fuenf Pflichtfelder, keine leeren Werte, keine doppelte ID).",
            "Eine Industry ohne Beschreibung oder eine doppelte ID macht die "
            "Auswahlgrundlage unbrauchbar.",
            "Snapshot neu bauen und freigeben.")
    computed = taxonomy_snapshot.hash_records(records)
    if computed != stated_hash:
        _taxonomy_broken(
            f"Der nachgerechnete Inhaltshash {computed[:12]} weicht vom "
            f"eingetragenen {stated_hash[:12]} ab.",
            "Der Inhalt wurde nach der Freigabe geaendert; der Eintrag "
            "beschreibt einen anderen Stand als die Datei.",
            "Aenderung zuruecknehmen oder 'snapshot build' und "
            "'snapshot approve' erneut ausfuehren.")

    # Kanonisch nach industry_id sortiert - dieselbe Ordnung, die in den
    # Inhaltshash eingeht. Eine umsortierte Baseline erzeugte sonst bei
    # gleichem Hash eine andere Auswahlliste und damit einen
    # Reihenfolgeunterschied, den niemand mehr sieht.
    industries = tuple(Industry(**record)
                       for record in sorted(records,
                                            key=lambda item: item["industry_id"]))
    return Taxonomy(snapshot_id=snapshot_id, content_hash=computed,
                    yfinance_version=yfinance_version, industries=industries)


def load_corpus_units() -> dict[str, Any]:
    """Laedt die Units des Eingabekorpus ueber die Lademodule des Pakets.

    Der Korpusparser bleibt die eine Wahrheit ueber die Eingabetexte; ihn hier
    nachzubauen waere eine zweite Fehlerquelle. Was dieses Skript nicht
    uebernimmt, ist das Rendering: der Prompt entsteht weiter unten selbst.
    """
    collector = ErrorCollector()
    settings = settings_view.load_settings()
    result = input_corpus.load_documents(collector, settings.offline)
    corpus_units = unit_builder.build_units(collector, result.documents)
    blockers = [finding for finding in collector.findings
                if finding.severity is Severity.BLOCKER]
    if blockers:
        detail = "; ".join(f"{finding.code} {finding.obj}" for finding in blockers[:3])
        block("GS_CORPUS_BLOCKED", paths.relative_to_base(paths.INPUT_ROOT),
              f"{len(blockers)} blockierende Befunde: {detail}"
              + (" ..." if len(blockers) > 3 else ""),
              "Ein Korpus, den der Matcher nicht laden kann, liefert auch dem "
              "Gold-Set keine belastbaren Texte.",
              "'python -m committee_matcher input validate' ausfuehren und die "
              "Befunde beheben.")
    return {unit.unit_id: unit for unit in corpus_units}


def _normalized(text: str) -> str:
    """Der Textbegriff des Datenvertrags: NFKC plus reduzierte Leerraeume.

    Eine eigene, schwaechere Normalisierung waere hier kein Gewinn an
    Unabhaengigkeit, sondern ein Fehlalarm: der Korpusparser kanonisiert
    'fi' zu 'fi' und dekomponierte Umlaute; eine Wache, die nur Leerraeume
    zusammenfasst, wuerde gueltigen Unicode-Korpus blockieren.
    """
    return normalize_text(text)


def source_section(unit: Any, raw: str) -> str | None:
    """Der Rohabschnitt der Unit aus der Korpusdatei.

    Subcommittees stehen unter ihrer eigenen H2-Ueberschrift, ein Committee im
    Text vor der ersten H2. Der Kommentarabschnitt am Dateiende gehoert zu
    keiner Unit. Gibt ``None`` zurueck, wenn sich kein Abschnitt zuordnen laesst.
    """
    body = raw.lstrip("\ufeff")
    if body.startswith("---"):
        # Das Frontmatter gehoert zu keiner Unit; ohne diesen Schnitt zaehlte
        # es beim Committeeabschnitt als Zustaendigkeitstext mit.
        parts = body.split("\n---", 1)
        if len(parts) == 2:
            body = parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""

    heading = f"## {unit.unit_name}".strip()
    is_subcommittee = unit.kind.value == "subcommittee"
    collected: list[str] = []
    inside = False
    started = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if inside:
                break
            if is_subcommittee and stripped == heading:
                inside = True
                started = True
            continue
        if stripped.startswith("# "):
            # Die H1 eroeffnet den Committeeabschnitt; ihre Zeile selbst
            # gehoert nicht dazu.
            if not is_subcommittee and not started:
                inside = True
                started = True
            continue
        if inside:
            collected.append(line)
    if not started:
        return None
    return "\n".join(collected).strip()


def check_evidence_against_source(unit: Any) -> None:
    """Prueft den geladenen Zustaendigkeitstext gegen seinen eigenen Abschnitt.

    Verglichen wird ausschliesslich mit dem Abschnitt dieser Unit, nicht mit
    der ganzen Datei: ein Text aus dem Parent oder aus dem Nachbarabschnitt
    bestuende einen Dateivergleich, waere als Beleg aber falsch. Verlangt wird
    Gleichheit nach Normalisierung der Leerraeume - der Parser fasst
    Zeilenumbrueche zusammen, kuerzt innerhalb eines Abschnitts aber nicht. Jede
    Abweichung ist damit entweder Verlust oder Umschreibung, und beides wuerde
    den Agenten gegen eine Fassung labeln lassen, die es im Korpus nicht gibt.
    """
    source = paths.INPUT_ROOT / unit.source_path
    if not source.exists():
        block("GS_EVIDENCE_MISMATCH", unit.unit_id,
              f"Die Quelldatei {unit.source_path} fehlt.",
              "Ohne Quelle laesst sich der geladene Text nicht gegenpruefen.",
              "Korpus wiederherstellen oder Stichprobe anpassen.")
    raw = source.read_text(encoding="utf-8")
    section = source_section(unit, raw)

    if unit.input_basis.value == "name_only":
        # Die Basis steht als Deklaration im Frontmatter, der Parser liest den
        # Abschnitt dort gar nicht erst; was unter der Ueberschrift steht, ist
        # eine redaktionelle Notiz und kein verlorener Beleg. Die Wache gegen
        # eine stille Umdeklaration ist der Abgleich der Eingabebasis gegen die
        # Stichprobe (GS_SAMPLE_STALE), nicht dieser Textvergleich.
        return
    if not unit.evidence_text:
        # Leere Evidenz trotz zugesagtem Zustaendigkeitstext ist die
        # vollstaendige Form der Kuerzung und darf nicht als name_only
        # durchgehen.
        block("GS_EVIDENCE_MISMATCH", unit.unit_id,
              "Die Unit ist als 'jurisdiction_text' deklariert, der Lader "
              "liefert aber keinen Text.",
              "Der Parser hat den Abschnitt nicht erkannt oder vollstaendig "
              "verworfen; das Paket haette keinen belegfaehigen Text.",
              f"Abschnitt '{unit.unit_name}' in {unit.source_path} pruefen; "
              "wenn dort wirklich kein Text steht, gehoert die Unit im "
              "Frontmatter auf 'name_only'.")

    if section is None:
        block("GS_EVIDENCE_MISMATCH", unit.unit_id,
              f"In {unit.source_path} gibt es keinen Abschnitt "
              f"'## {unit.unit_name}'.",
              "Ohne zuordenbaren Abschnitt laesst sich nicht pruefen, ob der "
              "geladene Text zu dieser Unit gehoert.",
              "Ueberschrift in der Korpusdatei gegen den Unitnamen abgleichen.")
    expected = _normalized(section)
    actual = _normalized(unit.evidence_text)
    if actual == expected:
        return

    if actual in expected:
        fehlend = len(expected) - len(actual)
        block("GS_EVIDENCE_MISMATCH", unit.unit_id,
              f"Der geladene Text deckt {len(actual)} von {len(expected)} "
              f"Zeichen des Abschnitts ab; {fehlend} Zeichen fehlen.",
              "Der Parser hat gekuerzt; dem Agenten fehlte belegfaehiger Text, "
              "und die Evidenzpruefung des Gold-Sets pruefte gegen weniger, als "
              "im Korpus steht.",
              f"Abschnitt '{unit.unit_name}' in {unit.source_path} gegen den "
              "geladenen Text vergleichen und den Korpusparser pruefen.")
    block("GS_EVIDENCE_MISMATCH", unit.unit_id,
          f"Der geladene Text stimmt mit dem Abschnitt '{unit.unit_name}' nicht "
          f"ueberein ({len(actual)} gegen {len(expected)} Zeichen).",
          "Entweder stammt der Text aus einem anderen Abschnitt derselben "
          "Datei oder er wurde umgeschrieben.",
          f"Quelldatei {unit.source_path} und den Korpusparser pruefen; ohne "
          "Deckung ist die Evidenzpruefung des Gold-Sets wertlos.")


# -- Rendering -------------------------------------------------------------
def render_taxonomy(taxonomy: Taxonomy) -> str:
    return "\n".join(
        f"- {industry.industry_id} | {industry.industry_name} "
        f"| Sektor: {industry.sector_name} | {industry.description}"
        for industry in taxonomy.industries
    )


def render_unit(unit: Any) -> str:
    """Eine Unit als Datenabschnitt.

    Der Parenttext erscheint als Kontext und nie als Beleg; ``name_only``
    bekommt gar keinen Zustaendigkeitstext, weil dort ausschliesslich der
    Unitname belegen darf.
    """
    lines = [
        f"## unit_id: {unit.unit_id}",
        f"Typ: {unit.kind.value}",
        f"Kammer: {unit.chamber}",
        f"Committee: {unit.committee_name}",
        f"Name der Einheit: {unit.unit_name}",
    ]
    if unit.parent_committee_name:
        lines.append(f"Uebergeordnetes Committee: {unit.parent_committee_name}")
    if unit.input_basis.value == "name_only":
        lines.append(
            "Zustaendigkeitstext: nicht vorhanden. Belege ausschliesslich mit "
            "dem Namen der Einheit.")
    else:
        lines.append(f"Zustaendigkeitstext: {unit.evidence_text}")
    if unit.parent_context_text:
        lines.append(
            "Kontext des uebergeordneten Committees (nur zum Verstaendnis, "
            f"nicht als Beleg zulaessig): {unit.parent_context_text}")
    return "\n".join(lines)


#: Zusatzauftrag, der das Gold-Set vom Produktivlauf unterscheidet. Er steht
#: hinter dem Modellvertrag, damit dessen Regeln unveraendert gelten.
RESEARCH_BRIEF = """
# Auftrag dieses Laufs

Du erstellst ein Referenzlabel fuer die Entwicklungsevaluation dieses Systems.
Ein Mensch nimmt dein Ergebnis ab; deine Aufgabe ist nicht Tempo, sondern
Belastbarkeit. Du beantwortest genau eine Einheit.

# Recherche

Du darfst und sollst im Internet nachschlagen, was du zur Beurteilung brauchst:
die tatsaechliche Zustaendigkeit des Ausschusses, die Bedeutung von
Fachbegriffen und Programmnamen im Zustaendigkeitstext, die Abgrenzung einer
Industry gegen ihre Nachbarn. Genau darin unterscheidest du dich vom
gemessenen System, das nur diesen Prompt sieht.

Zwei Grenzen:

- Die Recherche stuetzt dein Verstaendnis, sie ersetzt den Text nicht. Jedes
  `evidence_excerpt` bleibt ein woertlicher Ausschnitt aus dem uebergebenen
  Zustaendigkeitstext dieser Einheit; ein Zitat aus dem Web ist unzulaessig.
  Bei `name_only` zitierst du den Namen der Einheit.
- Was du nachgeschlagen hast, nennst du in `sources`: je Eintrag die URL und
  in einem Halbsatz, was sie belegt. Ohne Recherche bleibt die Liste leer.

# Selbsteinschaetzung

Setze `label_uncertain` auf `true` und begruende in `uncertainty_note`, wenn
eine fachlich vertretbare Gegenmeinung existiert - etwa wenn zwei Industries
gleich gut passen, der Text zu knapp fuer eine sichere Entscheidung ist oder
die Zustaendigkeit an einer Branchengrenze liegt. Diese Faelle entscheidet der
Mensch; eine ehrliche Markierung ist wertvoller als eine glatte Antwort.

# Ausgabe

Ausschliesslich das geforderte JSON-Objekt fuer diese eine Einheit, ohne
Fliesstext und ohne Codeblockzaeune.
""".strip()


def build_system_prompt(contract: str) -> str:
    return "\n\n".join([contract.strip(), RESEARCH_BRIEF])


def build_user_prompt(unit: Any, taxonomy: Taxonomy) -> str:
    header = [
        f"Angeforderte Einheit (unit_id zeichengenau uebernehmen): {unit.unit_id}",
        f"Taxonomie: {len(taxonomy.industries)} Industries, Snapshot "
        f"{taxonomy.snapshot_id}.",
    ]
    return "\n".join([
        "\n".join(header),
        "",
        untrusted_block("TAXONOMIE", render_taxonomy(taxonomy)),
        "",
        untrusted_block("EINHEIT", render_unit(unit)),
    ])


def output_schema() -> dict:
    """Ausgabeschema beider Engines.

    Strikt notiert - jedes Feld ist gelistet und ``additionalProperties`` ist
    aus -, weil beide CLIs das Schema an Endpoints weiterreichen, die im
    strikten Modus genau das verlangen. Optionale Felder sind nullable statt
    weggelassen, sonst waere ein fehlendes Feld nicht von einem bewusst leeren
    zu unterscheiden.
    """
    assignment = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "industry_id": {
                "type": "string",
                "description": "Exakte industry_id aus der uebergebenen Taxonomie.",
            },
            "confidence": {
                "type": "integer",
                "enum": list(range(MIN_CONFIDENCE, MAX_CONFIDENCE + 1)),
                "description": "Belegstaerke nach der Rubrik im Systemprompt.",
            },
            "evidence_excerpt": {
                "type": "string",
                "description": "Woertlicher Ausschnitt aus dem Zustaendigkeitstext.",
            },
            "rationale_short": {
                "type": "string",
                "description": "Ein bis zwei Saetze, die Evidence und Kerngeschaeft verbinden.",
            },
        },
        "required": ["industry_id", "confidence", "evidence_excerpt", "rationale_short"],
    }
    source = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string"},
            "belegt": {
                "type": "string",
                "description": "Was diese Quelle belegt, in einem Halbsatz.",
            },
        },
        "required": ["url", "belegt"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "unit_id": {"type": "string"},
            "decision": {"type": "string", "enum": ["mapped", "no_mapping"]},
            "no_mapping_reason": {"type": ["string", "null"]},
            "broad_scope_exception": {"type": "boolean"},
            "broad_scope_reason": {"type": ["string", "null"]},
            "assignments": {"type": "array", "items": assignment},
            "sources": {"type": "array", "items": source},
            "label_uncertain": {"type": "boolean"},
            "uncertainty_note": {"type": ["string", "null"]},
        },
        "required": ["unit_id", "decision", "no_mapping_reason",
                     "broad_scope_exception", "broad_scope_reason", "assignments",
                     "sources", "label_uncertain", "uncertainty_note"],
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- prepare ---------------------------------------------------------------
@dataclass
class TaskReport:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)


def load_contract() -> Any:
    """Den Modellvertrag ueber den Prompt-Loader des Moduls.

    Der Loader prueft Frontmatter, prompt_id und Schemafassung und bildet den
    Hash ueber denselben Text, der auch im Lauf gesendet wird. Ein eigener
    Parser waere hier keine Unabhaengigkeit, sondern eine zweite Auslegung
    derselben Datei - und ein abweichender ``prompt_sha256`` machte Gold-Set
    und Lauf spaeter unvergleichbar.
    """
    collector = ErrorCollector()
    contract = prompts.load_prompt(collector, "mapping_system")
    if contract is None:
        detail = "; ".join(f"{finding.code} {finding.message}"
                           for finding in collector.findings) or "kein Befund gemeldet"
        block("GS_CONTRACT_INVALID", paths.relative_to_base(MAPPING_PROMPT_PATH),
              detail,
              "Der Vertrag geht unveraendert an den labelnden Agenten; ohne "
              "gueltigen Vertrag labelte er nach anderen Regeln als der "
              "Pruefling.",
              "Befund in data/config/committee_matcher_prompts/mapping_system.md "
              "beheben; 'python -m committee_matcher prompt validate' zeigt ihn "
              "einzeln.")
    return contract


def build_task(sample_unit: SampleUnit, unit: Any, taxonomy: Taxonomy,
               contract: Any) -> dict:
    """Das Aufgabenpaket einer Unit.

    Enthaelt bewusst nichts aus ``sample_units.json`` ausser der Nummer: die
    Spalte ``focus`` nennt den erwarteten Fall und wuerde die Antwort vorgeben.
    """
    system_prompt = build_system_prompt(contract.text)
    user_prompt = build_user_prompt(unit, taxonomy)
    return {
        "task_format": TASK_FORMAT,
        "nr": sample_unit.nr,
        "unit_id": unit.unit_id,
        "chamber": unit.chamber,
        "committee_name": unit.committee_name,
        "unit_name": unit.unit_name,
        "input_basis": unit.input_basis.value,
        "unit_kind": unit.kind.value,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "schema": output_schema(),
        "binding": {
            "snapshot_id": taxonomy.snapshot_id,
            "taxonomy_content_hash": taxonomy.content_hash,
            "yfinance_version": taxonomy.yfinance_version,
            "mapping_input_hash": unit.mapping_input_hash or "",
            "source_path": unit.source_path,
            "source_bytes_sha256": unit.source_bytes_sha256 or "",
            "prompt_id": contract.prompt_id,
            "prompt_version": contract.version,
            "schema_version": contract.schema_version,
            "prompt_sha256": contract.sha256,
            "system_prompt_sha256": sha256_text(system_prompt),
            "user_prompt_sha256": sha256_text(user_prompt),
            "evidence_text": unit.evidence_text,
        },
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


#: Feld, das sich bei jedem Lauf aendert und deshalb aus dem Inhaltsvergleich
#: herausfaellt - allerdings erst, nachdem es selbst geprueft wurde.
VOLATILE_KEYS = ("created_at",)


def valid_timestamp(value: Any) -> bool:
    """Vollstaendiger Zeitstempel mit Zeitzone.

    Ein reines Datum oder eine naive Uhrzeit liesse offen, auf welche Zone sich
    die Provenienz bezieht - bei einem Artefakt, das spaeter einer Evaluation
    zugeordnet wird, ist das keine Kleinigkeit.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def unchanged_payload(previous: Any, current: dict) -> bool:
    """Ob eine vorhandene Datei denselben Inhalt traegt.

    Verglichen wird auf Gleichheit beider Schluesselmengen, nicht nur auf
    Enthaltensein: ein zusaetzliches Feld in der vorhandenen Datei - etwa ein
    von Hand ergaenzter Hinweis auf die erwartete Antwort - muss als Aenderung
    gelten und ueberschrieben werden. Ein fehlender oder unlesbarer Zeitstempel
    gilt ebenfalls als Aenderung: er wird nur deshalb aus dem Vergleich
    genommen, weil er sich planmaessig unterscheidet, nicht weil er beliebig
    sein duerfte.
    """
    if not isinstance(previous, dict):
        return False
    if not valid_timestamp(previous.get("created_at")):
        return False
    strip = lambda payload: {key: value for key, value in payload.items()
                             if key not in VOLATILE_KEYS}
    return strip(previous) == strip(current)


def assert_task_root_sane() -> None:
    """Prueft das Aufgabenverzeichnis, bevor darin gelesen oder geschrieben wird.

    Ein Verzeichnis oder ein Symlink an der Stelle eines Aufgabenpakets erzeugte
    sonst einen rohen Betriebssystemfehler - oder schlimmer: ein Symlink liesse
    ein Paket ausserhalb des Artefaktbaums liegen, wo es niemand mit dem Lauf
    zusammen sichert.
    """
    if not TASK_ROOT.exists():
        return
    if not TASK_ROOT.is_dir() or TASK_ROOT.is_symlink():
        block("GS_TASK_LAYOUT", paths.relative_to_base(TASK_ROOT),
              "Der Pfad ist kein regulaeres Verzeichnis.",
              "Ein Symlink oder eine Datei an dieser Stelle waere ein "
              "Schreibziel ausserhalb des Artefaktbaums.",
              "Pfad entfernen; 'prepare' legt das Verzeichnis neu an.")
    for entry in TASK_ROOT.iterdir():
        if entry.is_symlink():
            block("GS_TASK_LAYOUT",
                  f"{paths.relative_to_base(TASK_ROOT)}/{entry.name}",
                  "Der Eintrag ist ein Symlink.",
                  "Ein verlinktes Aufgabenpaket liegt ausserhalb des "
                  "Artefaktbaums und wird nicht mit ihm gesichert.",
                  "Symlink entfernen und 'prepare' erneut ausfuehren.")


def command_prepare(args: argparse.Namespace) -> int:
    assert_task_root_sane()
    sample = load_sample()
    taxonomy = load_taxonomy()
    corpus = load_corpus_units()
    contract = load_contract()

    report = TaskReport()
    for sample_unit in sample:
        unit = corpus.get(sample_unit.unit_id)
        if unit is None:
            block("GS_UNIT_UNKNOWN", sample_unit.unit_id,
                  "Die Stichprobe nennt eine Unit, die der Korpus nicht kennt.",
                  "Meist wurde ein Committee- oder Subcommitteename im Korpus "
                  "umbenannt; die ID wird aus dem Namen gebildet.",
                  "Unit-ID in sample_units.json gegen "
                  "'python -m committee_matcher units list' pruefen.")
        if unit.input_basis.value != sample_unit.input_basis:
            block("GS_SAMPLE_STALE", sample_unit.unit_id,
                  f"Eingabebasis ist {unit.input_basis.value!r}, die Stichprobe "
                  f"erwartet {sample_unit.input_basis!r}.",
                  "Die Fallabdeckung der Stichprobe haengt an dieser Basis; "
                  "eine stille Aenderung verschoebe, was gemessen wird.",
                  "Entweder den Korpus zuruecknehmen oder die Stichprobe und "
                  "ihre Begruendung in "
                  "organisation/committee_matcher_goldset_stichprobe.md anpassen.")
        check_evidence_against_source(unit)

        task = build_task(sample_unit, unit, taxonomy, contract)
        target = TASK_ROOT / f"{unit.unit_id}.json"
        if target.exists() and unchanged_payload(read_json(target), task):
            report.unchanged.append(unit.unit_id)
            continue
        write_json(target, task)
        report.written.append(unit.unit_id)

    expected_files = {f"{item.unit_id}.json" for item in sample} | {"index.json"}
    if TASK_ROOT.exists():
        # Alle Eintraege, nicht nur *.json: eine liegengebliebene .tmp aus einem
        # abgebrochenen Schreibvorgang oder ein Unterverzeichnis gehoeren
        # genauso wenig in ein Verzeichnis, das der Lauf liest.
        report.orphans = sorted(
            entry.name + ("/" if entry.is_dir() else "")
            for entry in TASK_ROOT.iterdir()
            if entry.name not in expected_files
            or not (entry.is_file() and not entry.is_symlink()))

    index = {
        "task_format": TASK_FORMAT,
        "snapshot_id": taxonomy.snapshot_id,
        "taxonomy_content_hash": taxonomy.content_hash,
        "prompt_sha256": contract.sha256,
        "prompt_version": contract.version,
        "unit_count": len(sample),
        # Bewusst ohne 'focus': das Verzeichnis wird vom Lauf gelesen, und die
        # Fallabdeckung nennt die erwartete Antwort.
        "units": [{"nr": item.nr, "unit_id": item.unit_id,
                   "input_basis": item.input_basis} for item in sample],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    index_path = TASK_ROOT / "index.json"
    previous_index = read_json(index_path) if index_path.exists() else None
    if not unchanged_payload(previous_index, index):
        write_json(index_path, index)

    print(f"Aufgabenpakete: {len(sample)} Units, {len(report.written)} geschrieben, "
          f"{len(report.unchanged)} unveraendert")
    print(f"Snapshot: {taxonomy.snapshot_id} ({len(taxonomy.industries)} Industries, "
          f"Hash {taxonomy.content_hash[:12]} nachgerechnet)")
    print(f"Vertrag:  {contract.path} ({contract.version}, Hash "
          f"{contract.sha256[:12]})")
    print(f"Ablage:   {paths.relative_to_base(TASK_ROOT)}")
    if args.show:
        preview = TASK_ROOT / f"{sample[0].unit_id}.json"
        print("\n--- Beispielpaket " + paths.relative_to_base(preview) + " ---")
        task = read_json(preview) or {}
        print(task.get("system_prompt", "")[:1200])
        print("...")
        print(task.get("user_prompt", "")[:600])
    if report.orphans:
        print()
        print(problem(
            "GS_TASK_LAYOUT", paths.relative_to_base(TASK_ROOT),
            f"Fremde Dateien im Aufgabenverzeichnis: {', '.join(report.orphans)}.",
            "Sie stammen aus einer frueheren Stichprobe. Der Lauf liest je Unit "
            "gezielt eine Datei, ein Bericht ueber das Verzeichnis zaehlt sie "
            "aber mit.",
            "Dateien loeschen, sobald die zugehoerige Stichprobe nicht mehr "
            "gilt.").render())
        return 2
    return 0


# -- Engines ---------------------------------------------------------------
@dataclass(frozen=True)
class EngineResult:
    """Ergebnis eines headless Laufs, unabhaengig von der Engine."""

    answer: dict | None
    exit_code: int
    duration_s: float
    timed_out: bool
    stdout: str
    stderr: str
    num_turns: int | None = None
    cost_usd: float | None = None
    session_id: str | None = None
    #: Auslastung des Abo-Fensters, sofern die CLI sie meldet.
    rate_limit: dict | None = None
    #: Die Abschlussdatei trug mehr als ein JSON-Objekt. Eigenes Feld statt
    #: einer Textmarke im stderr: eine Marke, die aus dem Protokoll der CLI
    #: stammen koennte, ist als Merkmal nicht verlaesslich.
    ambiguous: bool = False


@dataclass(frozen=True)
class EngineConfig:
    engine: str
    model: str
    effort: str
    timeout_seconds: int
    total_timeout_seconds: int = MAX_TIMEOUT_SECONDS
    #: Wurde ``timeout_seconds`` von der Restzeit des Gesamtdeckels gekappt?
    capped: bool = False


#: Wie der Modellvertrag die Engine erreicht. Die Wege sind bewusst
#: verschieden: Claude Code brachte sonst seinen eigenen Coding-Systemprompt
#: mit, und Codex exec nimmt keinen Systemprompt entgegen. Der Wert steht in
#: jeder Antwort, damit die Abweichung spaeter nachvollziehbar bleibt.
PROMPT_DELIVERY = {"claude": "system_prompt", "codex": "combined_user_prompt"}


def cli_name(engine: str) -> str:
    return "claude" if engine == "claude" else "codex"


def assert_cli_available(engine: str) -> str:
    if fake_dir() is not None:
        return ""  # Trockenlauf: es wird keine CLI aufgerufen.
    found = shutil.which(cli_name(engine))
    if not found:
        block("GS_CLI_MISSING", cli_name(engine),
              f"Das Programm {cli_name(engine)!r} liegt in keinem PATH-Verzeichnis.",
              "Ohne die CLI gibt es keinen Aufrufweg fuer diese Engine.",
              f"CLI installieren oder mit --engine "
              f"{'codex' if engine == 'claude' else 'claude'} arbeiten.")
    return found


def _looks_like(haystack: str, *needles: str) -> bool:
    lowered = haystack.lower()
    return any(needle in lowered for needle in needles)


#: Merkmal der Claude-CLI fuer eine verworfene Denktiefe. Sie meldet den Fall
#: als Warnung auf stderr und laeuft mit der Standardstufe weiter.
EFFORT_FALLBACK_MARK = "unknown --effort value"


def effort_fallback(result: EngineResult) -> str | None:
    """Die Warnzeile, mit der die CLI die Denktiefe verworfen hat.

    Der praktisch vorkommende Fehler ist nicht die Ablehnung, sondern der
    stille Rueckfall: die Claude-CLI warnt und rechnet mit der Standardstufe
    weiter, Exit-Code 0. Ein so entstandenes Label traegt im Manifest eine
    Denktiefe, mit der es nie erzeugt wurde - deshalb ist der Fall ein
    Abbruchgrund und keine Nebenmeldung.
    """
    for line in result.stderr.splitlines():
        if EFFORT_FALLBACK_MARK in line.lower():
            return line.strip()
    return None


def effort_echoed(result: EngineResult, effort: str) -> bool:
    """Nennt das Protokoll der CLI die angeforderte Denktiefe selbst?"""
    text = f"{result.stdout}\n{result.stderr}".casefold()
    wert = effort.casefold()
    return any(mark in text for mark in (
        f'"effort":"{wert}"', f'"effort": "{wert}"',
        f'reasoning_effort="{wert}"', f'"reasoning_effort":"{wert}"',
        f'"reasoning_effort": "{wert}"'))


def effort_confirmation(result: EngineResult, config: EngineConfig) -> str:
    """Wie belastbar die Denktiefe dieses Laufs ist.

    Abgeleitet aus dem Protokoll des Laufs statt aus dem Enginenamen: eine
    Konstante je Engine behauptet dieselbe Belastbarkeit auch dann noch, wenn
    die CLI ihr Verhalten geaendert hat oder das Protokoll fehlt.

    - ``bestaetigt``: die CLI nennt die Stufe selbst zurueck;
    - ``ohne-warnung``: sie haette eine unbekannte Stufe sichtbar verworfen
      (Claude), tat es aber nicht - ein schwaecherer, aber echter Beleg;
    - ``unbestaetigt``: die CLI nimmt jeden Wert kommentarlos an (Codex).
    """
    if effort_fallback(result) is not None:
        return "verworfen"
    if effort_echoed(result, config.effort):
        return "bestaetigt"
    return "ohne-warnung" if config.engine == "claude" else "unbestaetigt"


def assert_effort_accepted(result: EngineResult, config: EngineConfig) -> None:
    """Bricht ab, wenn die CLI die verlangte Denktiefe nicht uebernommen hat."""
    warning = effort_fallback(result)
    if warning is None:
        return
    block("GS_EFFORT_REJECTED", config.effort,
          f"Die CLI hat die Denktiefe {config.effort!r} verworfen und mit der "
          "Standardstufe gerechnet.",
          "Die CLI meldet das nur als Warnung und endet mit Exit 0; ohne diese "
          "Wache entstuenden Labels mit einer anderen Denktiefe, als das "
          "Manifest ausweist. Codex nimmt umgekehrt jeden Wert kommentarlos "
          "an - dort faellt ein Tippfehler nicht auf.",
          f"Stufe mit '--effort' korrigieren. Die CLI nennt die zulaessigen "
          f"Stufen in ihrer Warnung: {warning}")


def _claude_digest(result: EngineResult) -> str:
    """Fehlermerkmale eines Claude-Laufs.

    Der Rohstdout taugt nicht als Suchgrund: sein Init-Ereignis listet unter
    anderem die Slash-Befehle auf, darunter 'effort' - eine Suche nach
    Textmerkmalen im Gesamtstrom ordnete deshalb jeden Fehlschlag der
    Denktiefe zu. Gesucht wird nur in stderr und im Abschlussereignis.
    """
    parts = [result.stderr]
    for event in _claude_events(result.stdout):
        if event.get("type") != "result" and not event.get("is_error"):
            continue
        for key in ("subtype", "result", "error", "terminal_reason",
                    "api_error_status"):
            value = event.get(key)
            if value not in (None, "", [], {}):
                parts.append(f"{key}={value}")
    if len(parts) == 1:
        parts.append(result.stdout[-2000:])
    return "\n".join(str(part) for part in parts)


def _codex_digest(result: EngineResult) -> str:
    """Fehlermerkmale eines Codex-Laufs aus dem JSONL-Protokoll."""
    parts = [result.stderr]
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        kind = str(event.get("type", ""))
        is_error = "error" in kind or "failed" in kind or (
            isinstance(item, dict) and item.get("type") == "error")
        if is_error:
            parts.append(stripped)
    if len(parts) == 1:
        parts.append(result.stdout[-2000:])
    return "\n".join(parts)


def failure_digest(engine: str, result: EngineResult) -> str:
    return (_claude_digest if engine == "claude" else _codex_digest)(result)


#: Fehler, die den ganzen Satz betreffen und nicht die einzelne Unit. Nach
#: ihnen weiterzulaufen hiesse, 21 aussichtslose Aufrufe hinterherzuschicken.
SYSTEMIC_CODES = ("GS_MODEL_UNKNOWN", "GS_CLI_UNAUTHORIZED", "GS_CLI_FLAG",
                  "GS_RATE_LIMITED")

#: Statuswerte, die beide CLIs strukturiert melden. Sie stehen vor jedem
#: Textmerkmal: ein Protokoll kann mehrere Merkmale zugleich enthalten, aber
#: nur einen Status.
STATUS_PATTERN = re.compile(r'(?:api_error_status|"status")\s*[=:]\s*"?(\d{3})')

#: Textmerkmale je Ursache. Die Reihenfolge entscheidet erst, wenn kein
#: Status vorliegt.
FAILURE_MARKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GS_CLI_FLAG", ("unexpected argument", "unrecognized argument",
                     "unknown option", "unexpected option",
                     "unknown feature flag")),
    ("GS_MODEL_UNKNOWN", ("unrecognized_model", "model metadata for",
                          "model is not supported", "unknown model",
                          "model not found", "model_not_found", "invalid model",
                          "unbekanntes modell", "issue with the selected model",
                          "may not exist or you may not have access")),
    ("GS_CLI_UNAUTHORIZED", ("not logged in", "please run /login", "unauthorized",
                             "authentication", "nicht angemeldet")),
    ("GS_RATE_LIMITED", ("rate limit", "rate_limit", "too many requests",
                         "usage limit", "quota")),
)

#: HTTP-Status zu Ursache. 404 allein sagt nur, dass ein Endpunkt fehlt;
#: welcher, entscheidet erst das Textmerkmal.
STATUS_CODES = {"401": "GS_CLI_UNAUTHORIZED", "403": "GS_CLI_UNAUTHORIZED",
                "429": "GS_RATE_LIMITED"}


def _cause_text(code: str, engine: str, config: EngineConfig,
                result: EngineResult) -> Problem:
    """Der Befundtext zu einer erkannten Ursache."""
    texte = {
        "GS_CLI_FLAG": (
            cli_name(engine),
            "Die installierte CLI-Fassung kennt eine der benutzten Optionen "
            "nicht.",
            "Die Aufrufwege sind gegen die Fassungen geschrieben, die zum "
            "Zeitpunkt der Implementierung installiert waren; ein Update kann "
            "Optionen umbenennen.",
            f"Rohausgabe lesen und den Aufrufweg in run_{engine}() an die neue "
            "Fassung anpassen."),
        "GS_MODEL_UNKNOWN": (
            config.model,
            f"Die CLI kennt das Modell {config.model!r} nicht oder das Abo "
            "gibt es nicht frei.",
            "Modellnamen werden bewusst ohne Whitelist durchgereicht, damit "
            "kuenftige Modelle ohne Codeaenderung funktionieren; dafuer faellt "
            "ein Tippfehler erst hier auf.",
            "Namen pruefen und mit '--model' korrigieren; 'preflight' testet "
            "ihn mit einem einzigen kurzen Aufruf."),
        "GS_CLI_UNAUTHORIZED": (
            cli_name(engine),
            "Die CLI meldet eine fehlende oder abgelaufene Anmeldung.",
            "Headless-Laeufe koennen sich nicht selbst anmelden.",
            f"'{cli_name(engine)}' einmal interaktiv starten und anmelden, "
            "danach den Lauf wiederholen."),
        "GS_RATE_LIMITED": (
            cli_name(engine),
            "Die CLI meldet ein erreichtes Ratenlimit des Abos.",
            "Beide Engines rechnen ueber ein Abo ab; das Limit ist zeitlich, "
            "nicht mengenmaessig.",
            "Spaeter fortsetzen - 'run' ueberspringt bereits vorhandene "
            "Antworten und labelt nur den Rest."),
        "GS_ANSWER_AMBIGUOUS": (
            config.engine,
            "Die Abschlussdatei enthaelt mehr als ein JSON-Objekt.",
            "Welches davon die Antwort ist, waere geraten; ein geratenes Label "
            "ist als Referenz wertlos.",
            "Log der Unit lesen und die Unit wiederholen."),
        "GS_SCHEMA_INVALID": (
            config.engine,
            "Die Antwort haelt die Form des Ausgabevertrags nicht ein.",
            "Beide CLIs erzwingen das Schema serverseitig; haelt eine Antwort "
            "es dennoch nicht ein, stammt sie aus einem Fehlerpfad.",
            "Log der Unit lesen und die Unit wiederholen."),
        "GS_RUN_FAILED": (
            config.engine,
            f"Der Lauf endete mit Exit {result.exit_code} ohne verwertbare "
            "Antwort.",
            "Die Ursache steht nicht in der Liste der bekannten Stoerungen; "
            "sie als Schemafehler auszugeben waere eine falsche Faehrte.",
            "Rohausgabe im Log der Unit lesen; sie steht neben der "
            "Antwortdatei."),
    }
    obj, was, warum, abhilfe = texte[code]
    return problem(code, obj, was, warum, abhilfe, depth=2)


def classify_failure(engine: str, result: EngineResult, config: EngineConfig,
                     shape_detail: str | None = None) -> Problem:
    """Ordnet einen fehlgeschlagenen Lauf einer bekannten Ursache zu.

    Die Reihenfolge ist bewusst: Zeitdeckel, dann strukturierter Status, dann
    Textmerkmale. Ein Protokoll kann mehrere Merkmale zugleich tragen - etwa
    ein unbekanntes Modell und daraufhin einen 401 -, ein Status ist dagegen
    eindeutig. Gesucht wird in einem Auszug statt im Rohstrom, damit
    Protokollrauschen keine Ursache vortaeuscht: das Init-Ereignis der
    Claude-CLI listet unter anderem den Slash-Befehl 'effort' auf.
    """
    if result.timed_out:
        # Der Unitdeckel wird von der Restzeit des Gesamtdeckels gekappt; ein
        # Abbruch unter dem eingestellten Wert ist deshalb kein Haenger,
        # sondern das Ende des Satzes - und braucht eine andere Abhilfe.
        gekappt = config.capped
        return problem(
            "GS_TIMEOUT", config.engine,
            f"Der Lauf wurde nach {config.timeout_seconds} s abgebrochen"
            + (" (Restzeit des Gesamtdeckels, nicht der volle Unitdeckel)."
               if gekappt else "."),
            "Die Restzeit des Gesamtdeckels reichte fuer diese Unit nicht mehr."
            if gekappt else
            "Ein Lauf mit Recherche braucht Minuten, keine halbe Stunde; "
            "erreicht er den Deckel, haengt er oder wartet auf ein Limit.",
            "Gesamtdeckel anheben oder den Satz erneut aufrufen; 'run' setzt "
            "bei den offenen Units fort." if gekappt else
            "Unit einzeln wiederholen: 'run --engine "
            f"{config.engine} --unit-id <id>'. Der uebrige Satz bleibt "
            "erhalten.")
    if result.ambiguous:
        return _cause_text("GS_ANSWER_AMBIGUOUS", engine, config, result)
    if shape_detail:
        found = _cause_text("GS_SCHEMA_INVALID", engine, config, result)
        return Problem(code=found.code, obj=found.obj,
                       was=f"{found.was} Beanstandet: {shape_detail}.",
                       warum=found.warum, abhilfe=found.abhilfe, where=found.where)

    text = failure_digest(engine, result)
    treffer = [code for code, marks in FAILURE_MARKS if _looks_like(text, *marks)]
    # Der Status ist das haertere Merkmal als der Text: er wird nach vorn
    # geholt, auch wenn eine Textmarke denselben Code schon gefunden hat -
    # sonst entschiede die Reihenfolge der Marken statt der Antwort der CLI.
    for status in STATUS_PATTERN.findall(text):
        code = STATUS_CODES.get(status)
        if code is None and status == "404":
            code = "GS_MODEL_UNKNOWN" if "GS_MODEL_UNKNOWN" in treffer else None
        if code is None:
            continue
        if code in treffer:
            treffer.remove(code)
        treffer.insert(0, code)

    if not treffer:
        return _cause_text("GS_RUN_FAILED", engine, config, result)
    found = _cause_text(treffer[0], engine, config, result)
    if len(treffer) > 1:
        weitere = ", ".join(treffer[1:])
        return Problem(code=found.code, obj=found.obj, was=found.was,
                       warum=found.warum,
                       abhilfe=found.abhilfe + f" Das Protokoll trug zugleich "
                               f"Merkmale von: {weitere}.",
                       where=found.where)
    return found


def _as_text(raw: Any) -> str:
    """Ausgabe als Text, gleich ob sie als Bytes oder als Text ankommt."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


#: Umgebungsvariablen, die die CLI sehen darf. Positivliste statt geerbter
#: Umgebung: ``OPENAI_BASE_URL`` oder ``ANTHROPIC_BASE_URL`` lenkten den Lauf
#: sonst still auf einen anderen Endpunkt um, und das Label truege im Manifest
#: trotzdem den Namen des angefragten Modells.
ENV_ALLOWED: tuple[str, ...] = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM",
    "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    "CODEX_HOME", "CLAUDE_CONFIG_DIR",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
)


def clean_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Die Umgebung des Unterprozesses, auf das Noetige beschraenkt."""
    env = {name: os.environ[name] for name in ENV_ALLOWED if name in os.environ}
    env.update(extra or {})
    return env


def _spawn(command: list[str], *, cwd: Path, timeout: int,
           stdin_text: str | None = None,
           env: dict[str, str] | None = None) -> EngineResult:
    """Fuehrt eine CLI aus und misst sie.

    Weitergereicht wird nur die Positivliste ``ENV_ALLOWED``: beide CLIs holen
    ihre Anmeldung aus der Umgebung, aber eine geerbte Endpunktvariable
    verfaelschte den Lauf unbemerkt. Ferngehalten wird auch der
    Arbeitskontext - das Arbeitsverzeichnis liegt ausserhalb des Repos.
    """
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=str(cwd), input=stdin_text, capture_output=True,
            text=True, timeout=timeout, env=env if env is not None else clean_env())
    except subprocess.TimeoutExpired as expired:
        # Die Teilausgabe eines abgebrochenen Laufs kommt als Bytes, auch bei
        # text=True: die Ausnahme wird gebildet, bevor der Textmodus greift.
        # Ohne Umwandlung faellt jede spaetere Textpruefung mit TypeError aus -
        # und zwar genau im Timeoutfall, in dem das Log am meisten zaehlt.
        return EngineResult(
            answer=None, exit_code=-1, duration_s=time.monotonic() - started,
            timed_out=True, stdout=_as_text(expired.stdout),
            stderr=_as_text(expired.stderr))
    return EngineResult(
        answer=None, exit_code=completed.returncode,
        duration_s=time.monotonic() - started, timed_out=False,
        stdout=completed.stdout, stderr=completed.stderr)


def _first_json_object(text: str) -> dict | None:
    """Das erste vollstaendige JSON-Objekt eines Textes.

    Beide CLIs koennen der Antwort Hinweiszeilen voranstellen. Gesucht wird
    deshalb geklammert statt zeilenweise geparst; ein Objekt in einem
    Codeblock findet dieselbe Suche mit.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    start = -1
    return None


def _json_objects(text: str) -> tuple[dict | None, int]:
    """Das erste vollstaendige JSON-Objekt und die Zahl der weiteren.

    Mehrdeutigkeit ist kein Detail: enthaelt die Abschlussdatei zwei Objekte,
    ist nicht entscheidbar, welches die Antwort ist, und die Wahl des ersten
    waere geraten.
    """
    gefunden: list[dict] = []
    rest = text
    while True:
        objekt = _first_json_object(rest)
        if objekt is None:
            break
        gefunden.append(objekt)
        schnitt = _object_end(rest)
        if schnitt < 0:
            break
        rest = rest[schnitt:]
    if not gefunden:
        return None, 0
    return gefunden[0], len(gefunden) - 1


def _object_end(text: str) -> int:
    """Der Index hinter dem ersten vollstaendigen JSON-Objekt; -1 ohne Fund."""
    depth = 0
    in_string = False
    escaped = False
    started = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
            started = True
        elif char == "}":
            depth -= 1
            if depth == 0 and started:
                return index + 1
    return -1


def _claude_events(stdout: str) -> list[dict]:
    """Die Ereignisliste eines ``--output-format json``-Laufs.

    Die CLI schreibt ein Array aus Init-, Nachrichten- und Abschlussereignis.
    Ein Parser, der nur das erste Objekt liest, faende die Initialisierung und
    hielte den Lauf faelschlich fuer ergebnislos.
    """
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        found = _first_json_object(stdout)
        return [found] if isinstance(found, dict) else []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return [parsed] if isinstance(parsed, dict) else []


def answer_shape_problem(answer: Any, task: Mapping[str, Any]) -> str | None:
    """Der Grund, warum eine Antwort formal unbrauchbar ist - oder ``None``.

    Beide CLIs erzwingen das Schema serverseitig, doch keine der beiden
    garantiert es: eine Antwort aus einem Fehlerpfad, einem Nachrichtentext
    oder einer alten CLI-Fassung kaeme sonst als gueltiges Label in die
    Ablage und wuerde vom Resume dauerhaft uebersprungen. Geprueft wird hier
    nur die Form; Taxonomie und Beleg prueft ``collect``.
    """
    if not isinstance(answer, dict):
        return "die Antwort ist kein Objekt"
    fehlend = [name for name in ("unit_id", "decision", "assignments", "sources")
               if name not in answer]
    if fehlend:
        return f"Pflichtfelder fehlen: {', '.join(fehlend)}"
    if answer.get("unit_id") != task["unit_id"]:
        return (f"unit_id={answer.get('unit_id')!r} statt {task['unit_id']!r}")
    if answer.get("decision") not in ("mapped", "no_mapping"):
        return f"decision={answer.get('decision')!r}"
    if not isinstance(answer.get("assignments"), list):
        return "assignments ist keine Liste"
    if not isinstance(answer.get("sources"), list):
        return "sources ist keine Liste"
    if answer["decision"] == "mapped" and not answer["assignments"]:
        return "decision=mapped ohne eine einzige Zuordnung"
    if answer["decision"] == "no_mapping" and answer["assignments"]:
        return "decision=no_mapping mit Zuordnungen"
    return None


def run_claude(task: dict, config: EngineConfig, workdir: Path) -> EngineResult:
    """Ein Lauf ueber Claude Code.

    ``--safe-mode`` haelt CLAUDE.md, Skills, Plugins, Hooks und MCP-Server
    draussen, laesst Anmeldung und Werkzeuge aber unberuehrt; ohne ihn labelte
    der Agent unter den Projektregeln dieses Repos. ``--system-prompt`` ersetzt
    den mitgelieferten Coding-Systemprompt durch den Modellvertrag, und
    ``--tools`` laesst genau die Websuche uebrig - keine Datei-, Bash- oder
    Editierwerkzeuge.
    """
    command = [
        cli_name("claude"), "-p", task["user_prompt"],
        "--system-prompt", task["system_prompt"],
        "--json-schema", json.dumps(task["schema"], ensure_ascii=False),
        "--output-format", "json",
        "--model", config.model,
        "--effort", config.effort,
        "--tools", "WebSearch,WebFetch",
        "--safe-mode",
        "--no-session-persistence",
        "--strict-mcp-config",
    ]
    result = _spawn(command, cwd=workdir, timeout=config.timeout_seconds)
    if result.timed_out:
        return result

    events = _claude_events(result.stdout)
    limits = [event.get("rate_limit_info") for event in events
              if event.get("type") == "rate_limit_event"]
    rate_limit = limits[-1] if limits and isinstance(limits[-1], dict) else None
    finals = [event for event in events if event.get("type") == "result"]
    if not finals:
        return EngineResult(
            answer=None, exit_code=result.exit_code, duration_s=result.duration_s,
            timed_out=False, stdout=result.stdout, stderr=result.stderr,
            rate_limit=rate_limit)

    final = finals[-1]
    # 'structured_output' ist bereits geparst; 'result' traegt denselben Inhalt
    # als Text und dient nur als Rueckfallweg.
    payload = final.get("structured_output")
    if not isinstance(payload, dict):
        payload = _first_json_object(final.get("result") or "")
    if final.get("is_error") or not isinstance(payload, dict):
        payload = None
    return EngineResult(
        answer=payload, exit_code=result.exit_code, duration_s=result.duration_s,
        timed_out=False, stdout=result.stdout, stderr=result.stderr,
        num_turns=final.get("num_turns"), cost_usd=final.get("total_cost_usd"),
        session_id=final.get("session_id"), rate_limit=rate_limit)


def run_codex(task: dict, config: EngineConfig, workdir: Path) -> EngineResult:
    """Ein Lauf ueber Codex.

    ``--ignore-user-config`` haelt die globale ``config.toml`` draussen, damit
    Modell und Denktiefe ausschliesslich aus den Parametern dieses Werkzeugs
    kommen; die Anmeldung bleibt davon unberuehrt. Der Sandboxmodus
    ``read-only`` und das leere Arbeitsverzeichnis halten den Lauf von jeder
    Projektdatei fern. Codex exec nimmt keinen Systemprompt entgegen, deshalb
    steht der Vertrag am Anfang derselben Eingabe.

    Die Websuche kommt ueber ``-c tools.web_search=true``; ein Flag ``--search``
    kennt ``codex exec`` in Fassung 0.150 nicht. Dass die Einstellung wirkt,
    zeigen die ``web_search``-Ereignisse im JSONL-Protokoll - ohne sie faengt
    ``collect`` den Fall ueber die fehlende Quellenliste.
    """
    schema_file = workdir / "schema.json"
    schema_file.write_text(json.dumps(task["schema"], ensure_ascii=False),
                           encoding="utf-8")
    answer_file = workdir / "antwort.txt"
    prompt = "\n\n".join([task["system_prompt"], task["user_prompt"]])
    home = workdir / "codex-home"
    home.mkdir(mode=0o700, exist_ok=True)
    auth = Path.home() / ".codex" / "auth.json"
    if auth.exists():
        # Symlink statt Kopie: die Anmeldung bleibt an ihrem Ort, das
        # temporaere Home traegt kein Geheimnis, das jemand vergessen koennte.
        link = home / "auth.json"
        if not link.exists():
            link.symlink_to(auth)
    env = clean_env({"CODEX_HOME": str(home)})

    command = [
        cli_name("codex"), "exec",
        "--output-schema", str(schema_file),
        "--output-last-message", str(answer_file),
        "--json",
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "-c", "tools.web_search=true",
        "--color", "never",
        "-C", str(workdir),
        "-m", config.model,
        "-c", f'model_reasoning_effort="{config.effort}"',
        "-",
    ]
    result = _spawn(command, cwd=workdir, timeout=config.timeout_seconds,
                    stdin_text=prompt, env=env)
    if result.timed_out:
        return result

    turns = result.stdout.count('"type":"turn.completed"') or None
    # Die Antwort kommt ausschliesslich aus der Abschlussdatei. Der Rueckfall
    # auf das erste JSON-Objekt des Protokolls nahm frueher ein
    # Fehlerereignis als Antwort - und der Resume uebersprang die Unit danach
    # dauerhaft. Ein Lauf ohne Exit 0 hat keine Antwort, auch wenn die Datei
    # etwas enthaelt.
    if result.exit_code != 0 or not answer_file.exists():
        return EngineResult(
            answer=None, exit_code=result.exit_code, duration_s=result.duration_s,
            timed_out=False, stdout=result.stdout, stderr=result.stderr,
            num_turns=turns)
    raw = answer_file.read_text(encoding="utf-8")
    payload, weitere = _json_objects(raw)
    if weitere:
        return EngineResult(
            answer=None, exit_code=result.exit_code, duration_s=result.duration_s,
            timed_out=False, stdout=result.stdout,
            stderr=result.stderr + "\ndie Abschlussdatei enthaelt "
            f"{weitere + 1} JSON-Objekte", num_turns=turns, ambiguous=True)
    return EngineResult(
        answer=payload, exit_code=result.exit_code, duration_s=result.duration_s,
        timed_out=False, stdout=result.stdout, stderr=result.stderr,
        num_turns=turns)


ENGINE_RUNNERS = {"claude": run_claude, "codex": run_codex}


def resolve_engine(args: argparse.Namespace, settings: LabelSettings) -> str:
    """Der Enginename allein - genug fuer die Kommandos ohne CLI-Aufruf."""
    choice = _choose("--engine", getattr(args, "engine", None),
                     "GOLDSET_LABEL_ENGINE", settings.engine)
    engine = _require_text(choice, "Die Engine")
    if engine not in ENGINES:
        block("GS_SETTING_INVALID", choice.source,
              f"Engine {engine!r} ist unbekannt.",
              "Nur fuer diese CLIs ist ein Aufrufweg implementiert.",
              f"Zulaessig sind {' und '.join(repr(name) for name in ENGINES)}.")
    return engine


def engine_config(args: argparse.Namespace, settings: LabelSettings) -> EngineConfig:
    """Loest Parameter und Settings auf und prueft nur die effektiven Werte."""
    engine = resolve_engine(args, settings)
    model = _require_text(
        _choose("--model", getattr(args, "model", None),
                f"GOLDSET_LABEL_MODEL_{engine.upper()}", settings.model_for(engine)),
        "Der Modellname")
    effort = _require_text(
        _choose("--effort", getattr(args, "effort", None),
                "GOLDSET_LABEL_EFFORT", settings.effort),
        "Die Denktiefe")
    unit_deckel = _require_seconds(
        _choose("--timeout", getattr(args, "timeout", None),
                "GOLDSET_LABEL_TIMEOUT_SECONDS", settings.timeout_seconds),
        "Der Zeitdeckel je Unit")
    gesamt_choice = _choose("--total-timeout", getattr(args, "total_timeout", None),
                            "GOLDSET_LABEL_TOTAL_TIMEOUT_SECONDS",
                            settings.total_timeout_seconds)
    gesamt = _require_seconds(gesamt_choice, "Der Gesamtdeckel")
    if gesamt < unit_deckel:
        block("GS_SETTING_INVALID", gesamt_choice.source,
              f"Der Gesamtdeckel {gesamt} s liegt unter dem Deckel je Unit "
              f"{unit_deckel} s.",
              "Der Satz waere abgebrochen, bevor die erste Unit ihren eigenen "
              "Deckel ausschoepfen darf.",
              "Gesamtdeckel mindestens auf den Wert je Unit anheben.")
    return EngineConfig(engine=engine, model=model, effort=effort,
                        timeout_seconds=unit_deckel,
                        total_timeout_seconds=gesamt)


# -- preflight -------------------------------------------------------------
#: Kleinstmoegliche Anfrage: sie prueft Aufrufweg, Anmeldung, Modellname und
#: Denktiefe, ohne die Aufgabe zu beruehren.
PREFLIGHT_PROMPT = "Antworte ausschliesslich mit dem Wort: bereit"


def command_preflight(args: argparse.Namespace) -> int:
    settings = load_label_settings()
    config = engine_config(args, settings)
    assert_cli_available(config.engine)
    print(f"Preflight {config.engine}: Modell {config.model}, Denktiefe "
          f"{config.effort}, Deckel {config.timeout_seconds} s")

    workdir = Path(tempfile.mkdtemp(prefix="goldset-preflight-"))
    try:
        probe = {
            "user_prompt": PREFLIGHT_PROMPT,
            "system_prompt": "Du antwortest knapp und ohne Rueckfrage.",
            "schema": {
                "type": "object", "additionalProperties": False,
                "properties": {"antwort": {"type": "string"}},
                "required": ["antwort"],
            },
        }
        # Der Preflight laeuft mit einem kurzen eigenen Deckel: haengt die CLI
        # hier, hilft kein halbstuendiges Warten.
        short = EngineConfig(config.engine, config.model, config.effort,
                             min(config.timeout_seconds, 300))
        result = engine_runner(config.engine)(probe, short, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    assert_effort_accepted(result, config)
    if result.timed_out or result.exit_code != 0 or result.answer is None:
        # Gemeldet wird der Deckel, der tatsaechlich galt - nicht der des Laufs.
        print(classify_failure(config.engine, result, short).render(),
              file=sys.stderr)
        if result.stderr.strip():
            print("\n--- Rohausgabe ---\n" + result.stderr.strip()[:2000],
                  file=sys.stderr)
        return 1
    print(f"Antwort nach {result.duration_s:.1f} s: "
          f"{json.dumps(result.answer, ensure_ascii=False)[:120]}")
    bestaetigung = effort_confirmation(result, config)
    unbestaetigt = bestaetigung == "unbestaetigt"
    print(f"Denktiefe {config.effort}: {bestaetigung}")
    if unbestaetigt:
        print(problem(
            "GS_EFFORT_UNCONFIRMED", config.effort,
            f"Die {cli_name(config.engine)}-CLI hat die Denktiefe "
            f"{config.effort!r} weder bestaetigt noch abgelehnt.",
            "Sie nimmt 'model_reasoning_effort' kommentarlos an; ein "
            "Tippfehler faellt hier nicht auf und stuende spaeter als Tatsache "
            "im Manifest.",
            "Stufe gegen die Dokumentation der CLI pruefen. Die Provenienz "
            "jeder Antwort weist sie als 'unbestaetigt' aus.").render())
    if result.cost_usd is not None:
        print(f"Rechnerischer Gegenwert: {result.cost_usd:.6f} USD "
              "(Abo, nicht abgerechnet)")
    report_rate_limit(result)
    # Exit 2 statt 0: der Preflight ist bestanden, aber mit Befund - die
    # Denktiefe des folgenden Satzes ist dann nicht belegt.
    return 2 if unbestaetigt else 0


def report_rate_limit(result: EngineResult) -> float | None:
    """Meldet die Auslastung des Abo-Fensters, wenn die CLI sie kennt.

    Die Zahl ist eine unverbindliche Momentaufnahme und keine Zusage: sie
    stammt aus dem Preflightlauf, nicht aus einer Abfrage, und nicht jede CLI
    meldet sie. Sie hilft bei der Frage, ob ein Satz von 22 Laeufen jetzt
    sinnvoll startet - mehr nicht.
    """
    info = result.rate_limit
    if not info:
        print("Abo-Auslastung: nicht verfuegbar (die CLI meldet sie nicht).")
        return None
    windows = info.get("unifiedWindows") or {}
    parts = []
    for name, label in (("five_hour", "5 h"), ("seven_day", "7 d")):
        window = windows.get(name)
        if isinstance(window, dict) and window.get("utilization") is not None:
            parts.append(f"{label} {float(window['utilization']):.0%}")
    if parts:
        print("Abo-Auslastung: " + ", ".join(parts)
              + (f" (Status {info.get('status')})" if info.get("status") else ""))
    value = windows.get("five_hour", {}).get("utilization") if windows else None
    return float(value) if value is not None else None


# -- run -------------------------------------------------------------------
def load_task(unit_id: str) -> dict:
    path = TASK_ROOT / f"{unit_id}.json"
    if not path.exists():
        block("GS_TASK_MISSING", unit_id,
              f"Es gibt keine Datei {paths.relative_to_base(path)}.",
              "Der Lauf liest je Unit gezielt ein Paket; ohne Paket gibt es "
              "keine Anfrage.",
              "'prepare' ausfuehren.")
    task = read_json(path)
    if not isinstance(task, dict) or task.get("task_format") != TASK_FORMAT:
        block("GS_TASK_FORMAT", paths.relative_to_base(path),
              f"Fassung {task.get('task_format') if isinstance(task, dict) else None!r} "
              f"statt {TASK_FORMAT!r}.",
              "Eine fremde Fassung kann andere Felder tragen, als der Lauf "
              "sendet.",
              "'prepare' erneut ausfuehren.")
    return task


def check_task_binding(task: dict, taxonomy: Taxonomy, contract: Any) -> None:
    """Bindet den Lauf an den Stand, gegen den das Paket gebaut wurde."""
    binding = task.get("binding", {})
    if binding.get("taxonomy_content_hash") != taxonomy.content_hash:
        block("GS_SNAPSHOT_DRIFT", task["unit_id"],
              f"Das Paket nennt den Taxonomiehash "
              f"{str(binding.get('taxonomy_content_hash'))[:12]}, freigegeben "
              f"ist {taxonomy.content_hash[:12]}.",
              "Der Agent labelte sonst gegen eine Auswahlliste, die die "
              "Evaluation spaeter nicht mehr kennt.",
              "'prepare' erneut ausfuehren und die betroffenen Units neu "
              "labeln.")
    if binding.get("prompt_sha256") != contract.sha256:
        block("GS_SNAPSHOT_DRIFT", task["unit_id"],
              f"Das Paket nennt den Vertragshash "
              f"{str(binding.get('prompt_sha256'))[:12]}, aktuell ist "
              f"{contract.sha256[:12]}.",
              "Ein geaenderter Modellvertrag aendert die Regeln, nach denen "
              "gelabelt wird.",
              "'prepare' erneut ausfuehren.")


def check_task_against_corpus(task: dict, corpus: dict[str, Any]) -> None:
    unit = corpus.get(task["unit_id"])
    if unit is None:
        block("GS_UNIT_UNKNOWN", task["unit_id"],
              "Der Korpus kennt die Unit des Aufgabenpakets nicht mehr.",
              "Ein Committee- oder Subcommitteename wurde umbenannt.",
              "'prepare' erneut ausfuehren.")
    if normalize_text(task["binding"].get("evidence_text", "")) != \
            normalize_text(unit.evidence_text):
        block("GS_CORPUS_DRIFT", task["unit_id"],
              "Der Zustaendigkeitstext im Aufgabenpaket weicht vom Korpus ab.",
              "Der Korpus wurde geaendert, nachdem das Paket gebaut wurde; ein "
              "Label gegen den alten Text waere spaeter nicht zuzuordnen.",
              "'prepare' erneut ausfuehren und diese Unit neu labeln.")


def answer_path(engine: str, unit_id: str) -> Path:
    return RUN_ROOT / engine / f"{unit_id}.json"


def log_path(engine: str, unit_id: str) -> Path:
    return RUN_ROOT / engine / f"{unit_id}.log"


def store_answer(config: EngineConfig, task: dict, result: EngineResult,
                 started_at: str) -> None:
    record = {
        "answer_format": ANSWER_FORMAT,
        "unit_id": task["unit_id"],
        "nr": task["nr"],
        "engine": config.engine,
        "model": config.model,
        # Angefordert und bestaetigt sind zweierlei: die Codex-CLI nimmt jeden
        # Wert fuer 'model_reasoning_effort' kommentarlos an. Ein Manifest, das
        # beides gleichsetzte, behauptete eine Denktiefe, die niemand geprueft
        # hat.
        "effort": config.effort,
        "requested_effort": config.effort,
        "effort_confirmation": effort_confirmation(result, config),
        "prompt_delivery": PROMPT_DELIVERY[config.engine],
        "binding": task["binding"],
        "answer": result.answer,
        "meta": {
            "duration_s": round(result.duration_s, 1),
            "num_turns": result.num_turns,
            "cost_usd": result.cost_usd,
            "session_id": result.session_id,
            "exit_code": result.exit_code,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_json(answer_path(config.engine, task["unit_id"]), record)


#: Felder, in denen eine vorhandene Antwort mit dem aktuellen Lauf
#: uebereinstimmen muss, damit das Ueberspringen zulaessig ist.
RESUME_FIELDS = ("engine", "model", "effort", "prompt_delivery")
RESUME_BINDING_FIELDS = ("taxonomy_content_hash", "prompt_sha256",
                         "system_prompt_sha256", "user_prompt_sha256")


def resume_problem(config: EngineConfig, task: Mapping[str, Any],
                   record: Any) -> Problem | None:
    """Prueft, ob eine vorhandene Antwort zum aktuellen Lauf gehoert.

    Ohne diese Pruefung genuegte die blosse Existenz der Datei: ein Satz
    koennte dann aus zwei Modellen, zwei Denktiefen oder zwei Prompt-Staenden
    zusammengesetzt sein, ohne dass es irgendwo auffiele. Die Antwort wird
    nicht selbst neu erzeugt - das kostet Kontingent und ist Benes
    Entscheidung -, sondern gemeldet.
    """
    unit_id = task["unit_id"]
    hinweis = (f"Antwort pruefen und mit 'run --engine {config.engine} "
               f"--unit-id {unit_id} --force' neu erzeugen.")
    if not isinstance(record, dict) or record.get("answer_format") != ANSWER_FORMAT:
        return problem(
            "GS_RESUME_MISMATCH", unit_id,
            "Die vorhandene Antwortdatei ist unlesbar oder traegt eine fremde "
            "Fassung.", "Ein unlesbarer Rest eines abgebrochenen Laufs wuerde "
            "sonst dauerhaft als erledigt gelten.", hinweis, depth=2)
    abweichend = [name for name in RESUME_FIELDS
                  if record.get(name) != getattr(config, name, None)
                  and name != "prompt_delivery"]
    if record.get("prompt_delivery") != PROMPT_DELIVERY[config.engine]:
        abweichend.append("prompt_delivery")
    binding = record.get("binding") or {}
    abweichend += [name for name in RESUME_BINDING_FIELDS
                   if binding.get(name) != task["binding"].get(name)]
    if abweichend:
        gegenueber = ", ".join(
            f"{name}={record.get(name, (record.get('binding') or {}).get(name))!r}"
            for name in abweichend)
        return problem(
            "GS_RESUME_MISMATCH", unit_id,
            f"Die vorhandene Antwort weicht ab: {gegenueber}.",
            "Ein Satz aus verschiedenen Modellen, Denktiefen oder "
            "Promptstaenden ist als Referenz nicht vergleichbar.",
            hinweis, depth=2)
    if answer_shape_problem(record.get("answer"), task) is not None:
        return problem(
            "GS_RESUME_MISMATCH", unit_id,
            "Die vorhandene Antwort haelt die Form des Ausgabevertrags nicht "
            "ein.",
            "Sie waere sonst dauerhaft uebersprungen und fiele erst im "
            "'collect' auf - nach dem letzten bezahlten Lauf.",
            hinweis, depth=2)
    return None


def store_log(config: EngineConfig, unit_id: str, result: EngineResult) -> None:
    """Rohausgabe je Unit.

    Sie ist der einzige Ort, an dem die Ursache eines unklaren Fehlschlags
    steht; ohne sie bliebe von einem misslungenen Lauf nur ein Exit-Code.
    """
    target = log_path(config.engine, unit_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join([
        f"# {config.engine} | {config.model} | effort {config.effort}",
        f"# exit {result.exit_code} | {result.duration_s:.1f} s"
        + (" | Zeitdeckel erreicht" if result.timed_out else ""),
        "",
        "--- stdout ---", result.stdout.strip(),
        "", "--- stderr ---", result.stderr.strip(), "",
    ]), encoding="utf-8")


def command_run(args: argparse.Namespace) -> int:
    settings = load_label_settings()
    config = engine_config(args, settings)
    assert_cli_available(config.engine)

    sample = load_sample()
    taxonomy = load_taxonomy()
    corpus = load_corpus_units()
    contract = load_contract()

    wanted = list(args.unit_id) if args.unit_id else [item.unit_id for item in sample]
    known = {item.unit_id for item in sample}
    unknown = [unit_id for unit_id in wanted if unit_id not in known]
    if unknown:
        block("GS_UNIT_UNKNOWN", ", ".join(unknown),
              "Die genannten Units stehen nicht in der Stichprobe.",
              "Gelabelt wird ausschliesslich die eingefrorene Stichprobe.",
              "Unit-ID gegen "
              f"{paths.relative_to_base(SAMPLE_PATH)} pruefen.")

    runner = engine_runner(config.engine)
    total_deadline = time.monotonic() + config.total_timeout_seconds
    done = failed = skipped = 0
    offen: list[str] = []
    problems: list[Problem] = []
    # Die Belastbarkeit der Denktiefe steht erst nach dem Lauf fest und wird
    # je Antwort im Feld 'effort_confirmation' vermerkt.
    print(f"Lauf {config.engine}: Modell {config.model}, Denktiefe "
          f"{config.effort}, "
          f"{len(wanted)} Units, Deckel {config.timeout_seconds} s je Unit, "
          f"{config.total_timeout_seconds} s gesamt")

    for index, unit_id in enumerate(wanted, start=1):
        task = load_task(unit_id)
        check_task_binding(task, taxonomy, contract)
        check_task_against_corpus(task, corpus)

        target = answer_path(config.engine, unit_id)
        if target.exists() and not args.force:
            stale = resume_problem(config, task, read_json(target))
            if stale is None:
                skipped += 1
                print(f"[{index}/{len(wanted)}] {unit_id}: vorhanden, uebersprungen")
            else:
                problems.append(stale)
                offen.append(unit_id)
                print(f"[{index}/{len(wanted)}] {unit_id}: vorhanden, aber "
                      f"nicht passend [{stale.code}]")
            continue

        # Der Unterprozess bekommt nur die Zeit, die der Gesamtdeckel noch
        # hergibt: ein Deckel, der erst zwischen zwei Units greift, koennte um
        # einen vollen Unitdeckel ueberzogen werden.
        # Aufgerundet: bei ``gesamt == unit_deckel`` haette schon die
        # Millisekunde bis hierher den ersten Lauf verhindert.
        rest = math.ceil(total_deadline - time.monotonic())
        if rest < MIN_TIMEOUT_SECONDS:
            offen.extend(wanted[index - 1:])
            print(f"Gesamtdeckel von {config.total_timeout_seconds} s erreicht; "
                  f"{len(wanted) - index + 1} Units offen. Erneuter Aufruf "
                  "setzt fort.")
            break
        laufconfig = EngineConfig(
            engine=config.engine, model=config.model, effort=config.effort,
            timeout_seconds=min(config.timeout_seconds, rest),
            total_timeout_seconds=config.total_timeout_seconds,
            capped=rest < config.timeout_seconds)

        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"[{index}/{len(wanted)}] {unit_id}: laeuft ...", flush=True)
        workdir = Path(tempfile.mkdtemp(prefix="goldset-label-"))
        try:
            result = runner(task, laufconfig, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        store_log(laufconfig, unit_id, result)
        assert_effort_accepted(result, laufconfig)

        shape = (answer_shape_problem(result.answer, task)
                 if result.answer is not None else None)
        if result.answer is None or shape is not None:
            failed += 1
            offen.append(unit_id)
            found = classify_failure(config.engine, result, laufconfig, shape)
            problems.append(found)
            print(f"    fehlgeschlagen nach {result.duration_s:.0f} s "
                  f"[{found.code}]; Log: "
                  f"{paths.relative_to_base(log_path(config.engine, unit_id))}")
            if found.code in SYSTEMIC_CODES:
                # Diese Ursachen treffen jeden weiteren Aufruf gleichermassen;
                # der Satz wuerde sonst 21-mal dasselbe erfolglos wiederholen.
                offen.extend(wanted[index:])
                print(f"    Der Fehler betrifft den ganzen Satz; der Lauf "
                      f"bricht ab. {len(wanted) - index} Units bleiben offen.")
                break
            continue

        store_answer(laufconfig, task, result, started_at)
        done += 1
        answer = result.answer
        decision = answer.get("decision")
        count = len(answer.get("assignments") or [])
        marks = []
        if answer.get("label_uncertain"):
            marks.append("unsicher")
        if answer.get("broad_scope_exception"):
            marks.append("broad-scope")
        sources = len(answer.get("sources") or [])
        print(f"    {decision} ({count} Zuordnungen, {sources} Quellen"
              + (", " + ", ".join(marks) if marks else "")
              + f") nach {result.duration_s:.0f} s"
              + (f", {result.num_turns} Turns" if result.num_turns else ""))

    print(f"\nFertig: {done} gelabelt, {skipped} uebersprungen, {failed} "
          f"fehlgeschlagen")
    print(f"Ablage: {paths.relative_to_base(RUN_ROOT / config.engine)}")
    if offen:
        eindeutig = list(dict.fromkeys(offen))
        print(f"Offen: {len(eindeutig)} Units - "
              + ", ".join(eindeutig[:5])
              + (" ..." if len(eindeutig) > 5 else ""))
    if problems:
        print()
        for found in problems[:5]:
            print(found.render())
            print()
        if len(problems) > 5:
            print(f"... und {len(problems) - 5} weitere; siehe Logs.")
    return 2 if problems or offen else 0


# -- Sammeln ---------------------------------------------------------------
def labels_path(engine: str) -> Path:
    return COLLECT_ROOT / f"labels_{engine}.csv"


def notes_path(engine: str) -> Path:
    return COLLECT_ROOT / f"notes_{engine}.csv"


def write_csv(path: Path, columns: Sequence[str],
              rows: Sequence[Mapping[str, Any]]) -> None:
    """Schreibt eine Zwischentabelle.

    Alle Felder werden gequotet und Zeilenenden fest auf ``\\n`` gesetzt: die
    Zitate enthalten Kommas und Zeilenumbrueche, und eine Datei, deren Form vom
    Betriebssystem abhaengt, waere nicht zeichengenau vergleichbar.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns),
                                quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: text_value(row.get(name, "")) for name in columns})


def read_csv(path: Path, columns: Sequence[str], *, was: str) -> list[dict[str, str]]:
    """Liest eine Zwischentabelle und prueft ihren Kopf."""
    if not path.exists():
        block("GS_LABELS_MISSING", paths.relative_to_base(path),
              f"Die Datei {was} fehlt.",
              "Der Schritt baut auf dem Ergebnis des vorherigen auf; ohne sie "
              "gibt es nichts zu verarbeiten.",
              "Vorherigen Schritt ausfuehren.", depth=3)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        if header != tuple(columns):
            block("GS_LABELS_FORMAT", paths.relative_to_base(path),
                  f"Kopfzeile {header} statt {tuple(columns)}.",
                  "Eine fremde Spaltenfolge wuerde spaltenweise falsch "
                  "gelesen, ohne dass es auffiele.",
                  "Datei neu erzeugen statt von Hand zu bearbeiten.", depth=3)
        return [{name: (row.get(name) or "") for name in columns} for row in reader]


def text_value(value: Any) -> str:
    """Zellwert als Text; ``None`` und Zahlen werden zu Text, nicht zu 'None'."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    return str(value)


def evidence_contexts(task: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Erlaubte Belegquellen einer Unit als ``(Bezeichnung, Text)``.

    Nachgebaut statt aus ``validation`` importiert: die Regel ist Teil des
    Modellvertrags, den dieses Werkzeug selbst durchsetzt, und die Wache soll
    nicht dieselbe Auslegung teilen wie der Pruefling. ``name_only`` darf nur
    den Namen belegen, der Parenttext ist nie Beleg.
    """
    contexts: list[tuple[str, str]] = [("Name der Einheit", task.get("unit_name", ""))]
    committee = task.get("committee_name", "")
    if task.get("unit_kind") != "subcommittee" and committee != task.get("unit_name"):
        contexts.append(("Name des Committees", committee))
    evidence = task.get("binding", {}).get("evidence_text", "")
    if task.get("input_basis") != "name_only" and evidence:
        contexts.append(("Zustaendigkeitstext", evidence))
    return tuple(contexts)


def evidence_supported(task: Mapping[str, Any], excerpt: str) -> bool:
    needle = _normalized(excerpt).casefold()
    if not needle:
        return False
    return any(needle in _normalized(text).casefold()
               for _, text in evidence_contexts(task))


@dataclass
class UnitLabels:
    """Das Ergebnis einer Unit nach der formalen Pruefung."""

    unit_id: str
    rows: list[dict[str, str]] = field(default_factory=list)
    note: dict[str, str] = field(default_factory=dict)
    info: dict[str, str] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)


def visible_text(value: Any) -> bool:
    """Traegt der Wert sichtbaren Inhalt?

    ``strip()`` allein genuegt nicht: unsichtbare Formatzeichen (Kategorie Cf,
    etwa Zero-Width-Space) sind kein Leerraum und liessen ein leeres
    Pflichtfeld formal bestehen.
    """
    if not isinstance(value, str) or not value:
        return False
    return bool("".join(ch for ch in unicodedata.normalize("NFKC", value)
                        if unicodedata.category(ch) != "Cf").strip())


def assignment_limit(task: Mapping[str, Any]) -> int:
    """Obergrenze der Zuordnungen dieser Unit aus ihrem Typ und ihrer Basis.

    Die Grenze haengt an der Unit, nicht am Antwortfeld ``broad_scope_exception``:
    ein Kernbereich eines Committees und eine Unit ohne Zustaendigkeitstext
    tragen hoechstens ``REGULAR_ASSIGNMENT_LIMIT`` Zuordnungen, und die
    Ausnahme steht ihnen gar nicht offen.
    """
    if task.get("unit_kind") in NARROW_UNIT_KINDS or \
            task.get("input_basis") == NAME_ONLY_BASIS:
        return REGULAR_ASSIGNMENT_LIMIT
    return BROAD_SCOPE_ASSIGNMENT_LIMIT


def _broad_problem(unit_id: str, was: str, abhilfe: str) -> Problem:
    return problem(
        "GS_BROAD_SCOPE_INVALID", unit_id, was,
        "Der Modellvertrag laesst mehr als "
        f"{REGULAR_ASSIGNMENT_LIMIT} Zuordnungen nur unter allen Bedingungen "
        "der Ausnahme zu; ein Gold-Set, das sie verletzt, misst den Lauf "
        "gegen eine Regel, an die er selbst gebunden ist.",
        abhilfe, depth=2)


def check_broad_scope(task: Mapping[str, Any], unit_id: str, decision: str,
                      assignments: Sequence[Mapping[str, Any]], broad: bool,
                      broad_reason: Any) -> list[Problem]:
    """Prueft Anzahlgrenze und Broad-Scope-Ausnahme gegen den Modellvertrag."""
    found: list[Problem] = []
    count = len(assignments)
    items = [item for item in assignments if isinstance(item, Mapping)]
    limit = assignment_limit(task)
    grund = (f"Unittyp {task.get('unit_kind')!r}, Basis "
             f"{task.get('input_basis')!r}")

    if count > limit:
        found.append(problem(
            "GS_COUNT_EXCEEDED", unit_id,
            f"{count} Zuordnungen, erlaubt sind {limit} ({grund}).",
            "Die Obergrenze steht im Modellvertrag; wird sie ueberschritten, "
            "misst die Evaluation das Gold-Set gegen eine andere Regel als den "
            "Lauf.",
            "Zuordnungen in der Reviewdatei kuerzen oder die Unit wiederholen."))

    if decision == "no_mapping":
        if broad or broad_reason:
            found.append(_broad_problem(
                unit_id,
                "decision=no_mapping traegt eine Broad-Scope-Ausnahme.",
                "Felder 'broad_scope' und 'broad_scope_reason' leeren."))
        return found

    if count <= REGULAR_ASSIGNMENT_LIMIT:
        if broad or broad_reason:
            found.append(_broad_problem(
                unit_id,
                f"Broad-Scope-Ausnahme bei {count} Zuordnungen; sie ist erst "
                f"ab {REGULAR_ASSIGNMENT_LIMIT + 1} zulaessig.",
                "Ausnahme entfernen oder die Unit wiederholen."))
        return found

    if limit == REGULAR_ASSIGNMENT_LIMIT:
        found.append(_broad_problem(
            unit_id,
            f"Die Ausnahme steht dieser Unit nicht offen ({grund}).",
            "Auf hoechstens "
            f"{REGULAR_ASSIGNMENT_LIMIT} Zuordnungen kuerzen."))
        return found

    if not broad:
        found.append(_broad_problem(
            unit_id,
            f"{count} Zuordnungen ohne gesetzte Ausnahme.",
            "Feld 'broad_scope' setzen und begruenden oder kuerzen."))
    if not visible_text(broad_reason):
        found.append(_broad_problem(
            unit_id, "Die Ausnahme traegt keine sichtbare Begruendung.",
            "Feld 'broad_scope_reason' im Blatt 'notes' fuellen."))

    weak = sorted(str(item.get("industry_id", "")) for item in items
                  if not isinstance(item.get("confidence"), int)
                  or isinstance(item.get("confidence"), bool)
                  or item.get("confidence", 0) < BROAD_SCOPE_MIN_CONFIDENCE)
    if weak:
        found.append(_broad_problem(
            unit_id,
            f"Confidence unter {BROAD_SCOPE_MIN_CONFIDENCE} bei: "
            f"{', '.join(weak)}.",
            "Confidence anheben oder die schwachen Zuordnungen streichen."))

    for feld, name in (("evidence_excerpt", "Evidence"),
                       ("rationale_short", "Rationale")):
        werte = [_normalized(str(item.get(feld, ""))).casefold()
                 for item in items]
        if len(set(werte)) != len(werte):
            found.append(_broad_problem(
                unit_id,
                f"Die Ausnahme verlangt eine eigene {name} je Industry; "
                f"{len(werte) - len(set(werte))} Wert(e) wiederholen sich.",
                f"Spalte '{feld}' je Zeile eigenstaendig fuellen."))
    return found


def _field_problem(unit_id: str, detail: str, abhilfe: str) -> Problem:
    return problem(
        "GS_SCHEMA_INVALID", unit_id, detail,
        "Beide CLIs erzwingen das Schema serverseitig; haelt eine Antwort es "
        "dennoch nicht ein, ist sie kein brauchbares Label.",
        abhilfe, depth=2)


def check_answer(task: Mapping[str, Any], record: Mapping[str, Any],
                 taxonomy: Taxonomy) -> UnitLabels:
    """Prueft eine abgelegte Antwort gegen Vertrag, Taxonomie und Belegtext.

    Die Pruefung sammelt statt abzubrechen: ein Satz von 22 Antworten soll
    seine Maengel in einem Durchgang zeigen, nicht einen nach dem anderen.
    """
    unit_id = task["unit_id"]
    result = UnitLabels(unit_id=unit_id)
    answer = record.get("answer")
    if not isinstance(answer, dict):
        result.problems.append(_field_problem(
            unit_id, "Die Antwortdatei enthaelt kein Antwortobjekt.",
            f"Unit mit 'run --unit-id {unit_id} --force' wiederholen."))
        return result

    if answer.get("unit_id") != unit_id:
        result.problems.append(problem(
            "GS_UNIT_ID_MISMATCH", unit_id,
            f"Die Antwort nennt {answer.get('unit_id')!r}.",
            "Eine vertauschte Zuordnung von Antwort und Unit verfaelscht das "
            "Gold-Set an zwei Stellen zugleich.",
            f"Unit mit 'run --unit-id {unit_id} --force' wiederholen."))

    decision = answer.get("decision")
    assignments = answer.get("assignments")
    # Der Vertrag unterscheidet 'nicht gesetzt' von 'leer gesetzt': bei
    # ``mapped`` lehnt er jeden Wert ausser ``None`` ab, auch den leeren
    # String. ``or ""`` haette beide Faelle zu einem verschmolzen.
    # Die beiden Begruendungsfelder sind ``str | None``; ein ``False`` waere
    # sonst falsy und ergaebe stillschweigend 'keine Begruendung'.
    wiederholen = f"Unit mit 'run --unit-id {unit_id} --force' wiederholen."
    roh_reason = answer.get("no_mapping_reason")
    roh_broad_reason = answer.get("broad_scope_reason")
    for feld, wert in (("no_mapping_reason", roh_reason),
                       ("broad_scope_reason", roh_broad_reason)):
        if wert is not None and not isinstance(wert, str):
            result.problems.append(_field_problem(
                unit_id, f"{feld}={wert!r} ist weder Text noch leer.",
                wiederholen))
    reason = roh_reason.strip() if visible_text(roh_reason) else ""
    # Strikt wie der Vertrag: ``bool("")`` machte aus einem Textfeld
    # klaglos ein Nein, und eine 1 aus einem Ja - beides lehnt
    # ``MappingUnitResult`` ab, und beides verschoebe die Anzahlgrenze.
    roh_broad = answer.get("broad_scope_exception", False)
    if not isinstance(roh_broad, bool):
        result.problems.append(_field_problem(
            unit_id,
            f"broad_scope_exception={roh_broad!r} ist kein Wahrheitswert.",
            wiederholen))
    broad = roh_broad is True
    if decision not in ("mapped", "no_mapping") or not isinstance(assignments, list):
        result.problems.append(_field_problem(
            unit_id, f"decision={decision!r}, assignments={type(assignments).__name__}.",
            f"Unit mit 'run --unit-id {unit_id} --force' wiederholen."))
        return result

    if decision == "no_mapping" and (assignments or not reason):
        result.problems.append(problem(
            "GS_DECISION_CONFLICT", unit_id,
            f"decision=no_mapping mit {len(assignments)} Zuordnungen und "
            f"Begruendung {'vorhanden' if reason else 'leer'}.",
            "Eine Unit hat entweder Zuordnungen oder genau eine Begruendung; "
            "beides zugleich laesst offen, was das Gold-Set behauptet.",
            "Antwort im Log pruefen und die Unit wiederholen."))
    if decision == "mapped" and (not assignments or roh_reason is not None):
        result.problems.append(problem(
            "GS_DECISION_CONFLICT", unit_id,
            f"decision=mapped mit {len(assignments)} Zuordnungen und "
            f"no_mapping_reason={roh_reason!r}.",
            "Eine leere Zuordnungsliste mit decision=mapped ist kein Urteil, "
            "und eine Begruendung neben Zuordnungen widerspricht ihnen.",
            "Antwort im Log pruefen und die Unit wiederholen."))

    result.problems.extend(check_broad_scope(
        task, unit_id, decision, assignments, broad,
        answer.get("broad_scope_reason")))

    seen: set[str] = set()
    for position, item in enumerate(assignments, start=1):
        if not isinstance(item, dict):
            result.problems.append(_field_problem(
                unit_id, f"Zuordnung {position} ist kein Objekt.",
                "Unit wiederholen."))
            continue
        confidence = item.get("confidence")
        # Ohne die Typwache machte ``str()`` aus ``None`` den sichtbaren Text
        # 'None' und aus ``1`` die Zeichenkette '1'; beide bestuenden jede
        # weitere Pruefung, obwohl der Vertrag nur Text zulaesst.
        texte: dict[str, str] = {}
        typfehler: set[str] = set()
        for feld in ("industry_id", "evidence_excerpt", "rationale_short"):
            wert = item.get(feld, "")
            if not isinstance(wert, str):
                typfehler.add(feld)
                result.problems.append(_field_problem(
                    unit_id,
                    f"Zuordnung {position}: {feld}={wert!r} ist kein Text.",
                    f"Unit mit 'run --unit-id {unit_id} --force' wiederholen."))
            texte[feld] = wert if isinstance(wert, str) else ""
        industry_id = texte["industry_id"].strip()
        excerpt = texte["evidence_excerpt"]
        rationale = texte["rationale_short"]

        if "industry_id" in typfehler:
            # Der ersatzweise leere Wert erzeugte sonst gleich darauf einen
            # zweiten Befund ueber eine unbekannte Industry; gemeldet gehoert
            # der Typfehler, nicht seine Folge.
            continue

        if industry_id not in taxonomy.ids:
            near = [known for known in sorted(taxonomy.ids)
                    if known.casefold() == industry_id.casefold()]
            result.problems.append(problem(
                "GS_INDUSTRY_UNKNOWN", f"{unit_id} / {industry_id}",
                f"Die industry_id steht nicht im Snapshot {taxonomy.snapshot_id}."
                + (f" Gemeint ist vermutlich {near[0]!r}." if near else ""),
                "Die Evaluation vergleicht IDs zeichengenau; eine unbekannte ID "
                "zaehlte als falsche Zuordnung, obwohl sie nur falsch "
                "geschrieben ist.",
                "Zeile in der Reviewdatei korrigieren; die Auswahlliste des "
                "Blatts 'taxonomy' nennt die zulaessigen IDs."))
        if industry_id in seen:
            result.problems.append(problem(
                "GS_UNIT_DUPLICATE", f"{unit_id} / {industry_id}",
                "Dieselbe Industry erscheint mehrfach.",
                "Eine doppelte Zuordnung zaehlte in der Evaluation doppelt und "
                "verschoebe Praezision und Trefferquote.",
                "Doppelte Zeile in der Reviewdatei loeschen."))
        seen.add(industry_id)

        if not isinstance(confidence, int) or isinstance(confidence, bool) or \
                not MIN_CONFIDENCE <= confidence <= MAX_CONFIDENCE:
            result.problems.append(_field_problem(
                unit_id,
                f"confidence={confidence!r} liegt nicht zwischen "
                f"{MIN_CONFIDENCE} und {MAX_CONFIDENCE}.",
                "Wert in der Reviewdatei korrigieren."))
        if not visible_text(excerpt) or not visible_text(rationale):
            result.problems.append(_field_problem(
                unit_id,
                f"Zuordnung {position} ({industry_id}) hat ein leeres "
                "Pflichtfeld (evidence_excerpt oder rationale_short).",
                "Feld in der Reviewdatei fuellen oder die Unit wiederholen."))
        elif not evidence_supported(task, excerpt):
            quellen = ", ".join(label for label, _ in evidence_contexts(task))
            result.problems.append(problem(
                "GS_EVIDENCE_NOT_VERBATIM", f"{unit_id} / {industry_id}",
                f"Das Zitat {excerpt[:60]!r} steht in keiner erlaubten Quelle "
                f"({quellen}).",
                "Ein erfundenes oder aus dem Parenttext entliehenes Zitat "
                "belegt die Zuordnung nicht; das Gold-Set traegt sonst eine "
                "Begruendung, die der Korpus nicht hergibt.",
                "Zitat gegen das Blatt 'jurisdiction' pruefen und ersetzen "
                "oder die Zuordnung streichen."))

        # Randleerraum wird hier abgeschnitten und nicht in die
        # Zwischentabelle uebernommen: sonst meldete 'verify' ihn spaeter als
        # Autokorrektur der Tabellenkalkulation, obwohl er aus der Antwort
        # stammt. Die Belegpruefung normalisiert ohnehin.
        result.rows.append({
            "unit_id": unit_id,
            "industry_id": industry_id,
            "confidence": text_value(confidence),
            "evidence_excerpt": excerpt.strip(),
            "rationale_short": rationale.strip(),
            "no_mapping_reason": "",
        })

    if decision == "no_mapping":
        result.rows.append({
            "unit_id": unit_id, "industry_id": "", "confidence": "",
            "evidence_excerpt": "", "rationale_short": "",
            "no_mapping_reason": reason,
        })

    sources = answer.get("sources")
    sources = sources if isinstance(sources, list) else []
    if not sources:
        result.problems.append(problem(
            "GS_WEBSEARCH_OFF", unit_id,
            "Die Antwort nennt keine einzige Quelle.",
            "Die Recherche ist Teil des Auftrags; ohne Quelle laesst sich "
            "nicht unterscheiden, ob der Agent recherchiert hat oder ob die "
            "Websuche abgeschaltet war.",
            "Log der Unit auf 'web_search'-Ereignisse pruefen; fehlen sie, "
            "greift der Suchschalter nicht und der Satz ist zu wiederholen."))

    binding = record.get("binding", {})
    result.note = {
        "unit_id": unit_id,
        "engine": text_value(record.get("engine")),
        "model": text_value(record.get("model")),
        "label_uncertain": text_value(answer.get("label_uncertain")),
        "uncertainty_note": text_value(answer.get("uncertainty_note")),
        "broad_scope": text_value(answer.get("broad_scope_exception")),
        "broad_scope_reason": text_value(answer.get("broad_scope_reason")),
        "sources": " | ".join(
            f"{str(item.get('url', '')).strip()} ({str(item.get('belegt', '')).strip()})"
            for item in sources if isinstance(item, dict)),
        "dissens": "",
    }
    result.info = {
        "unit_id": unit_id,
        "snapshot_id": text_value(binding.get("snapshot_id")),
        "taxonomy_content_hash": text_value(binding.get("taxonomy_content_hash")),
        "mapping_input_hash": text_value(binding.get("mapping_input_hash")),
        "prompt_sha256": text_value(binding.get("prompt_sha256")),
        "prompt_version": text_value(binding.get("prompt_version")),
        "engine": text_value(record.get("engine")),
        "model": text_value(record.get("model")),
        "effort": text_value(record.get("effort")),
        "prompt_delivery": text_value(record.get("prompt_delivery")),
        "created_at": text_value(record.get("created_at")),
    }
    return result


def command_collect(args: argparse.Namespace) -> int:
    settings = load_label_settings()
    engine = resolve_engine(args, settings)

    sample = load_sample()
    taxonomy = load_taxonomy()
    corpus = load_corpus_units()
    contract = load_contract()

    rows: list[dict[str, str]] = []
    notes: list[dict[str, str]] = []
    infos: list[dict[str, str]] = []
    problems: list[Problem] = []
    missing: list[str] = []

    for item in sample:
        path = answer_path(engine, item.unit_id)
        record = read_json(path)
        if record is None:
            missing.append(item.unit_id)
            continue
        if not isinstance(record, dict) or record.get("answer_format") != ANSWER_FORMAT:
            problems.append(problem(
                "GS_ANSWER_FORMAT", paths.relative_to_base(path),
                f"Fassung {record.get('answer_format') if isinstance(record, dict) else None!r} "
                f"statt {ANSWER_FORMAT!r}.",
                "Eine fremde Fassung kann andere Felder tragen, als die "
                "Pruefung erwartet.",
                f"Unit mit 'run --unit-id {item.unit_id} --force' wiederholen."))
            continue

        task = load_task(item.unit_id)
        check_task_binding(task, taxonomy, contract)
        check_task_against_corpus(task, corpus)
        binding = record.get("binding") or {}
        if binding.get("user_prompt_sha256") != task["binding"]["user_prompt_sha256"]:
            problems.append(problem(
                "GS_SNAPSHOT_DRIFT", item.unit_id,
                "Die Antwort wurde gegen eine andere Anfrage erzeugt, als das "
                "heutige Aufgabenpaket stellt.",
                "Ein Label gegen einen anderen Prompt ist der Evaluation nicht "
                "zuzuordnen; der Unterschied faellt sonst nie auf.",
                f"'prepare' pruefen und die Unit mit 'run --unit-id "
                f"{item.unit_id} --force' wiederholen."))
            continue

        checked = check_answer(task, record, taxonomy)
        rows.extend(checked.rows)
        if checked.note:
            notes.append(checked.note)
        if checked.info:
            infos.append(checked.info)
        problems.extend(checked.problems)

    for unit_id in missing:
        problems.append(problem(
            "GS_UNIT_MISSING", unit_id,
            f"Zu dieser Unit liegt unter "
            f"{paths.relative_to_base(RUN_ROOT / engine)} keine Antwort.",
            "Das Gold-Set deckt die Stichprobe vollstaendig ab; eine fehlende "
            "Unit waere in der Evaluation eine stille Luecke.",
            f"'run --engine {engine} --unit-id {unit_id}' nachholen."))

    write_csv(labels_path(engine), LABEL_COLUMNS, rows)
    write_csv(notes_path(engine), NOTE_COLUMNS, notes)
    write_csv(info_path(engine), INFO_COLUMNS, infos)
    print(f"Gesammelt: {len(sample) - len(missing)} von {len(sample)} Units, "
          f"{len(rows)} Zeilen")
    print(f"  {paths.relative_to_base(labels_path(engine))}")
    print(f"  {paths.relative_to_base(notes_path(engine))}")
    print(f"  {paths.relative_to_base(info_path(engine))}")
    return report_problems(problems)


def report_problems(problems: Sequence[Problem], limit: int = 8) -> int:
    """Gibt Befunde aus und liefert den Exit-Code des Kommandos."""
    if not problems:
        print("Keine Befunde.")
        return 0
    print(f"\n{len(problems)} Befunde:")
    for found in problems[:limit]:
        print()
        print(found.render())
    if len(problems) > limit:
        print(f"\n... und {len(problems) - limit} weitere gleicher Art.")
    return 2


def info_path(engine: str) -> Path:
    return COLLECT_ROOT / f"info_{engine}.csv"


# -- Zusammenfuehren -------------------------------------------------------
def _by_unit(rows: Sequence[Mapping[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["unit_id"], []).append(dict(row))
    return grouped


def _decision_of(rows: Sequence[Mapping[str, str]]) -> str:
    if not rows:
        return "fehlt"
    if len(rows) == 1 and not rows[0]["industry_id"]:
        return "no_mapping"
    return "mapped"


def command_merge(args: argparse.Namespace) -> int:
    """Vergleicht zwei Engineläufe und schreibt den Dissens auf.

    Zusammengefuehrt wird nicht inhaltlich: welche Fassung gilt, entscheidet
    Bene. Der Merge liefert eine Basistabelle und daneben die Liste der
    Stellen, an denen die Engines auseinanderlaufen.
    """
    base_engine = args.base
    other_engine = args.against
    if base_engine == other_engine:
        block("GS_MERGE_SCOPE", base_engine,
              "Basis und Vergleich nennen dieselbe Engine.",
              "Ein Vergleich mit sich selbst zeigt nie Dissens und taeuschte "
              "Uebereinstimmung vor.",
              "Zwei verschiedene Engines angeben.")

    base = read_csv(labels_path(base_engine), LABEL_COLUMNS,
                    was=f"mit den Labels von {base_engine}")
    other = read_csv(labels_path(other_engine), LABEL_COLUMNS,
                     was=f"mit den Labels von {other_engine}")
    base_units = _by_unit(base)
    other_units = _by_unit(other)

    sample = load_sample()
    order = [item.unit_id for item in sample]
    only_base = sorted(set(base_units) - set(other_units))
    only_other = sorted(set(other_units) - set(base_units))

    lines = [
        f"# Dissens zwischen {base_engine} und {other_engine}",
        "",
        f"Erzeugt {datetime.now(timezone.utc).isoformat(timespec='seconds')}.",
        f"Basis ist {base_engine}; die Basistabelle uebernimmt dessen Zeilen "
        "unveraendert.",
        "",
    ]
    dissens: dict[str, str] = {}
    einig = 0
    for unit_id in order:
        mine = base_units.get(unit_id, [])
        theirs = other_units.get(unit_id, [])
        my_ids = {row["industry_id"] for row in mine if row["industry_id"]}
        their_ids = {row["industry_id"] for row in theirs if row["industry_id"]}
        my_decision = _decision_of(mine)
        their_decision = _decision_of(theirs)
        if my_decision == their_decision and my_ids == their_ids:
            einig += 1
            continue

        fehlend = sorted(their_ids - my_ids)
        ueberzaehlig = sorted(my_ids - their_ids)
        marks = []
        if my_decision != their_decision:
            marks.append(f"{base_engine}={my_decision} vs "
                         f"{other_engine}={their_decision}")
        if fehlend:
            marks.append(f"nur {other_engine}: {', '.join(fehlend)}")
        if ueberzaehlig:
            marks.append(f"nur {base_engine}: {', '.join(ueberzaehlig)}")
        dissens[unit_id] = "; ".join(marks)

        lines.append(f"## {unit_id}")
        lines.append("")
        lines.append(f"- Dissens: {dissens[unit_id]}")
        lines.append(f"- {base_engine}: {my_decision}, "
                     f"{', '.join(sorted(my_ids)) or 'keine Industry'}")
        lines.append(f"- {other_engine}: {their_decision}, "
                     f"{', '.join(sorted(their_ids)) or 'keine Industry'}")
        for row in theirs:
            if row["industry_id"] in fehlend:
                lines.append(f"  - {other_engine} begruendet {row['industry_id']}: "
                             f"{row['rationale_short']} (Beleg: "
                             f"{row['evidence_excerpt'][:120]})")
            if not row["industry_id"] and row["no_mapping_reason"]:
                lines.append(f"  - {other_engine} begruendet no_mapping: "
                             f"{row['no_mapping_reason']}")
        lines.append("")

    if only_base or only_other:
        lines.extend(["## Nicht in beiden Laeufen enthalten", ""])
        for unit_id in only_base:
            lines.append(f"- nur {base_engine}: {unit_id}")
        for unit_id in only_other:
            lines.append(f"- nur {other_engine}: {unit_id}")
        lines.append("")

    lines.insert(4, f"{einig} von {len(order)} Units einig, "
                    f"{len(dissens)} strittig.")
    MERGE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    MERGE_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    notes = read_csv(notes_path(base_engine), NOTE_COLUMNS,
                     was=f"mit den Notizen von {base_engine}")
    other_notes = {row["unit_id"]: row for row in
                   read_csv(notes_path(other_engine), NOTE_COLUMNS,
                            was=f"mit den Notizen von {other_engine}")}
    for note in notes:
        note["dissens"] = dissens.get(note["unit_id"], "")
        gegen = other_notes.get(note["unit_id"])
        if gegen and gegen.get("label_uncertain") not in ("", "nein"):
            note["dissens"] = (note["dissens"] + "; " if note["dissens"] else "") \
                + f"{other_engine} unsicher: {gegen.get('uncertainty_note', '')}"

    # Die Provenienz traegt beide Laeufe, nicht nur die Basis: der Bericht
    # zitiert Begruendungen der zweiten Engine, und ohne deren Zeilen liesse
    # sich spaeter nicht mehr sagen, mit welchem Modell sie entstanden sind.
    provenienz = [
        row
        for engine in (base_engine, other_engine)
        for row in read_csv(info_path(engine), INFO_COLUMNS,
                            was=f"mit der Provenienz von {engine}")
    ]
    rang = {unit_id: nummer for nummer, unit_id in enumerate(order)}
    provenienz.sort(key=lambda row: (rang.get(row["unit_id"], len(order)),
                                     row["engine"]))

    write_csv(labels_path("merged"), LABEL_COLUMNS, base)
    write_csv(notes_path("merged"), NOTE_COLUMNS, notes)
    write_csv(info_path("merged"), INFO_COLUMNS, provenienz)
    print(f"{einig} von {len(order)} Units einig, {len(dissens)} strittig.")
    print(f"  {paths.relative_to_base(MERGE_REPORT)}")
    print(f"  {paths.relative_to_base(labels_path('merged'))}")
    if only_base or only_other:
        print(f"Befund: {len(only_base) + len(only_other)} Unit(s) stehen nur "
              "in einem der beiden Laeufe.")
    # Dissens allein ist kein Befund, sondern das Ergebnis des Vergleichs;
    # Exit 2 bliebe sonst der Normalfall und truege keine Auskunft mehr.
    return 2 if only_base or only_other else 0


# -- Reviewdatei -----------------------------------------------------------
def _cell(sheet, row: int, column: int, value: Any) -> None:
    """Schreibt eine reine Textzelle.

    Ohne den erzwungenen Typ wuerde ein Wert mit fuehrendem ``=`` als Formel
    gespeichert; Excel zeigte dann ein Rechenergebnis, und das Ruecklesen
    laese einen anderen Wert als den geschriebenen. Derselbe Schutz wie im
    Reviewpfad des Matchers.
    """
    target = sheet.cell(row=row, column=column, value=text_value(value))
    target.data_type = "s"


def _write_sheet(sheet, columns: Sequence[str],
                 rows: Sequence[Mapping[str, Any]]) -> None:
    for index, name in enumerate(columns, start=1):
        _cell(sheet, 1, index, name)
    for line, row in enumerate(rows, start=2):
        for index, name in enumerate(columns, start=1):
            _cell(sheet, line, index, row.get(name, ""))
    sheet.freeze_panes = "B2"


#: Zusaetzliche Zeilen, die die Auswahlliste ueber den Datenbestand hinaus
#: abdeckt. 22 Units mit hoechstens fuenf Zuordnungen ergeben 110 Zeilen; die
#: Reserve traegt jede erlaubte Ergaenzung.
LIST_RESERVE_ROWS = 200


def _bind_industry_list(sheet, taxonomy_rows: int, data_rows: int) -> None:
    """Haengt die Auswahlliste der Industries an die Spalte ``industry_id``.

    Der Tippfehler ist der wahrscheinlichste Handfehler und faellt sonst erst
    beim Import auf. Die Liste warnt nur; sie sperrt nicht, damit Einfuegen aus
    der Zwischenablage moeglich bleibt.
    """
    if taxonomy_rows < 1 or data_rows < 1:
        return
    column = get_column_letter(LABEL_COLUMNS.index("industry_id") + 1)
    rule = DataValidation(
        type="list", allow_blank=True, errorStyle="warning",
        formula1=f"{SHEET_TAXONOMY}!$A$2:$A${taxonomy_rows + 1}",
        error="Die Industry steht nicht im freigegebenen Snapshot.",
        errorTitle="Unbekannte industry_id",
        prompt="Leer lassen heisst: diese Unit hat keine Zuordnung.",
        promptTitle="industry_id")
    sheet.add_data_validation(rule)
    # Reserve: Bene darf Zeilen anhaengen, und ohne sie endete die Liste nach
    # der letzten vorhandenen Zeile - genau dort, wo der Tippfehler entsteht.
    rule.add(f"{column}2:{column}{data_rows + 1 + LIST_RESERVE_ROWS}")


def jurisdiction_rows(sample: Sequence[SampleUnit],
                      corpus: Mapping[str, Any]) -> list[dict[str, str]]:
    """Lesehilfe: der Volltext, gegen den Bene die Zitate prueft."""
    rows = []
    for item in sample:
        unit = corpus.get(item.unit_id)
        rows.append({
            "unit_id": item.unit_id,
            "chamber": item.chamber,
            "committee_name": item.committee_name,
            "unit_name": item.unit_name,
            "input_basis": item.input_basis,
            "jurisdiction_text": getattr(unit, "evidence_text", "") if unit else "",
        })
    return rows


def lock_file(target: Path) -> Path:
    """Der Sperrvermerk, den LibreOffice neben eine geoeffnete Datei legt."""
    return target.with_name(f".~lock.{target.name}#")


def save_workbook(book: Workbook, target: Path) -> Path:
    """Schreibt eine Mappe unteilbar und sichert die bisherige Fassung.

    Ein abgebrochenes ``book.save`` liesse die Reviewdatei halb geschrieben
    zurueck; geschrieben wird deshalb daneben und erst am Ende umgehaengt.
    Ist die Datei in LibreOffice geoeffnet, wuerde das Programm die Aenderung
    beim naechsten Speichern ueberschreiben - darum vorher der Sperrvermerk.
    """
    lock = lock_file(target)
    if lock.exists():
        block("GS_XLSX_LOCKED", paths.relative_to_base(target),
              f"Der Sperrvermerk {lock.name} liegt daneben; die Datei ist "
              "vermutlich geoeffnet.",
              "Das geoeffnete Programm haelt seinen eigenen Stand und wuerde "
              "die zurueckgeschriebene Fassung beim Speichern verwerfen.",
              "Datei schliessen und den Aufruf wiederholen.", depth=3)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = target.with_suffix(f".{stamp}.bak{target.suffix}")
    shutil.copy2(target, backup)
    temp = target.with_suffix(f".{os.getpid()}.tmp{target.suffix}")
    try:
        book.save(temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return backup


def guard_target(target: Path, force: bool) -> Path | None:
    """Schuetzt eine vorhandene Reviewdatei vor dem Ueberschreiben.

    Die Datei traegt die Abnahmearbeit; ein zweiter Aufruf von ``to-xlsx``
    darf sie nicht stillschweigend durch die unbearbeitete Fassung ersetzen.
    Mit ``--force`` wird sie vorher neben sich gesichert.
    """
    if not target.exists():
        return None
    if not force:
        block("GS_XLSX_OVERWRITE", paths.relative_to_base(target),
              "Die Zieldatei ist bereits vorhanden.",
              "Sie kann bearbeitete Labels tragen; ein Ueberschreiben ohne "
              "Nachfrage vernichtet die gesamte Abnahmearbeit.",
              "Datei umbenennen oder den Aufruf mit '--force' wiederholen; "
              "'--force' sichert die vorhandene Fassung vorher weg.", depth=3)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = target.with_suffix(f".{stamp}.bak{target.suffix}")
    shutil.copy2(target, backup)
    return backup


def command_to_xlsx(args: argparse.Namespace) -> int:
    source = args.source
    labels = read_csv(labels_path(source), LABEL_COLUMNS,
                      was=f"mit den Labels von {source}")
    notes = read_csv(notes_path(source), NOTE_COLUMNS,
                     was=f"mit den Notizen von {source}")
    infos = read_csv(info_path(source), INFO_COLUMNS,
                     was=f"mit der Provenienz von {source}")
    sample = load_sample()
    taxonomy = load_taxonomy()
    corpus = load_corpus_units()

    taxonomy_rows = [{
        "industry_id": industry.industry_id,
        "industry_name": industry.industry_name,
        "sector_name": industry.sector_name,
        "description": industry.description,
    } for industry in taxonomy.industries]

    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_LABELS
    _write_sheet(sheet, LABEL_COLUMNS, labels)
    _write_sheet(book.create_sheet(SHEET_JURISDICTION), JURISDICTION_COLUMNS,
                 jurisdiction_rows(sample, corpus))
    _write_sheet(book.create_sheet(SHEET_TAXONOMY), TAXONOMY_COLUMNS, taxonomy_rows)
    _write_sheet(book.create_sheet(SHEET_NOTES), NOTE_COLUMNS, notes)
    _write_sheet(book.create_sheet(SHEET_INFO), INFO_COLUMNS, infos)
    _bind_industry_list(sheet, len(taxonomy_rows), len(labels))

    target = Path(args.out) if args.out else WORKBOOK_PATH
    gesichert = guard_target(target, bool(args.force))
    target.parent.mkdir(parents=True, exist_ok=True)
    book.save(target)
    if gesichert is not None:
        print(f"Gesichert: {paths.relative_to_base(gesichert)}")
    print(f"Geschrieben: {paths.relative_to_base(target)}")
    print(f"  {len(labels)} Zeilen im Blatt '{SHEET_LABELS}', "
          f"{len(taxonomy_rows)} Industries als Auswahlliste")
    print("Alle Zellen sind Textzellen; 'verify' liest die bearbeitete Datei "
          "zurueck und meldet umgeschriebene IDs.")
    return 0


# -- verify ----------------------------------------------------------------
def _sheet_matrix(sheet, columns: Sequence[str], name: str) -> list[dict[str, Any]]:
    """Liest ein Blatt zeilenweise und prueft seine Kopfzeile."""
    header = [text_value(cell.value).strip() for cell in sheet[1]] if sheet.max_row else []
    if header[:len(columns)] != list(columns):
        block("GS_XLSX_LAYOUT", name,
              f"Kopfzeile {header[:len(columns)]} statt {list(columns)}.",
              "Eine verschobene oder umbenannte Spalte wuerde spaltenweise "
              "falsch gelesen, ohne dass es auffiele.",
              "Blatt nicht umbauen; 'to-xlsx' erzeugt die Datei neu.", depth=3)
    rows = []
    for line in range(2, sheet.max_row + 1):
        row: dict[str, Any] = {"__zeile__": line}
        leer = True
        for index, column in enumerate(columns, start=1):
            cell = sheet.cell(row=line, column=index)
            row[column] = cell
            if text_value(cell.value).strip():
                leer = False
        if not leer:
            rows.append(row)
    return rows


#: Felder, deren Aenderung die Bilanz zeigt. ``unit_id`` und ``industry_id``
#: bilden den Schluessel und stehen deshalb nicht darunter.
BALANCE_FIELDS = ("confidence", "evidence_excerpt", "rationale_short",
                  "no_mapping_reason")


def _row_key(unit_id: str, industry_id: str, vorkommen: int) -> str:
    """Bezeichnung einer Zeile samt Nummer, falls sie mehrfach vorkommt."""
    return f"{unit_id}|{industry_id}" + (f"|#{vorkommen + 1}" if vorkommen else "")


def _grouped(werte: Sequence[Mapping[str, str]]
             ) -> dict[tuple[str, str], list[Mapping[str, str]]]:
    gruppen: dict[tuple[str, str], list[Mapping[str, str]]] = {}
    for row in werte:
        gruppen.setdefault((row["unit_id"], row["industry_id"]), []).append(row)
    return gruppen


def _pair_rows(alt: list[Mapping[str, str]], neu: list[Mapping[str, str]]
               ) -> list[tuple[Mapping[str, str] | None, Mapping[str, str] | None]]:
    """Paart die Zeilen einer Schluesselgruppe: erst gleiche, dann der Rest.

    Zwei Zeilen mit demselben ``(unit_id, industry_id)`` unterscheiden sich nur
    in den Bilanzfeldern. Wuerde stur nach Position gepaart, ergaebe schon ein
    Umsortieren zwei gemeldete Aenderungen. Unveraenderte Zeilen werden deshalb
    zuerst gegeneinander weggekuerzt.
    """
    offen_alt = list(alt)
    offen_neu = list(neu)
    for row in list(offen_neu):
        gleich = next((other for other in offen_alt
                       if all(other[column] == row[column]
                              for column in BALANCE_FIELDS)), None)
        if gleich is not None:
            offen_alt.remove(gleich)
            offen_neu.remove(row)
    return list(zip_longest(offen_alt, offen_neu))


def change_balance(rows: Sequence[Mapping[str, Any]], source: str
                   ) -> list[str]:
    """Vergleicht die Reviewdatei zurueck gegen ``labels_<source>.csv``.

    Bene darf jeden Wert aendern; die Bilanz ist deshalb Auskunft, kein
    Befund. Ohne sie bliebe unsichtbar, ob eine Zelle bewusst bearbeitet oder
    von der Tabellenkalkulation umgeschrieben wurde - und genau das ist bei
    ``evidence_excerpt`` und ``rationale_short`` sonst nirgends geprueft.
    """
    vorher = _grouped(read_csv(labels_path(source), LABEL_COLUMNS,
                               was=f"mit den Labels von {source}"))
    nachher = _grouped([
        {column: text_value(row[column].value).strip()
         for column in LABEL_COLUMNS}
        for row in rows
    ])

    zeilen: list[str] = []
    for paar in sorted(set(vorher) | set(nachher)):
        for nummer, (alt_row, neu_row) in enumerate(
                _pair_rows(vorher.get(paar, []), nachher.get(paar, []))):
            key = _row_key(paar[0], paar[1], nummer)
            if alt_row is None:
                zeilen.append(f"neu:      {key}")
                continue
            if neu_row is None:
                zeilen.append(f"entfernt: {key}")
                continue
            for column in BALANCE_FIELDS:
                if alt_row[column] != neu_row[column]:
                    zeilen.append(
                        f"geaendert {key} / {column}: "
                        f"{alt_row[column][:40]!r} -> {neu_row[column][:40]!r}")
    return zeilen


#: Zeichen, die im Blatt wie ein Bindestrich oder ein Leerzeichen aussehen.
#: NFKC allein genuegt nicht: es fasst Ligaturen und Breitformen zusammen,
#: laesst den typografischen Gedankenstrich aber stehen - und genau der kommt
#: beim Kopieren aus einem Browser in eine kebab-case-ID.
LOOKALIKES = str.maketrans({
    ch: "-" for ch in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
                      "\ufe58\ufe63\uff0d"
} | {ch: " " for ch in "\u00a0\u2007\u202f"})


def lookalike_form(text: str) -> str:
    """Vergleichsform, die optisch gleiche Zeichen zusammenfasst."""
    return _normalized(text.translate(LOOKALIKES)).casefold()


def check_edited_units(zuordnungen: Mapping[str, list[dict[str, Any]]],
                       decisions: Mapping[str, set[str]],
                       notes: Sequence[Mapping[str, Any]]) -> list[Problem]:
    """Prueft die bearbeiteten Zeilen erneut gegen den Fachvertrag.

    ``collect`` prueft die Antwort des Agenten, aber Bene darf danach jede
    Zeile aendern, loeschen und ergaenzen. Ohne diese zweite Pruefung koennte
    eine von Hand ergaenzte Zuordnung ohne Beleg, ueber der Anzahlgrenze oder
    mit leerem Pflichtfeld unbemerkt ins Gold-Set gelangen.
    """
    found: list[Problem] = []
    broad_flags = {
        text_value(row["unit_id"].value).strip(): (
            text_value(row["broad_scope"].value).strip().casefold() == "ja",
            text_value(row["broad_scope_reason"].value).strip())
        for row in notes if text_value(row["unit_id"].value).strip()
    }

    # Ueber ``decisions`` und nicht ueber ``zuordnungen``: eine Unit ohne
    # Zuordnung steht in ``zuordnungen`` gar nicht, und genau bei ihr ist die
    # Broad-Scope-Ausnahme unzulaessig - sie bliebe sonst ungeprueft.
    for unit_id in decisions:
        eintraege = zuordnungen.get(unit_id, [])
        task = load_task(unit_id)
        broad, broad_reason = broad_flags.get(unit_id, (False, ""))
        decision = ("mapped" if "mapped" in decisions.get(unit_id, set())
                    else "no_mapping")
        found.extend(check_broad_scope(task, unit_id, decision, eintraege,
                                       broad, broad_reason))

        for eintrag in eintraege:
            line = eintrag["__zeile__"]
            for feld, spalte in (("evidence_excerpt", "D"),
                                 ("rationale_short", "E")):
                if not visible_text(eintrag[feld]):
                    found.append(problem(
                        "GS_SCHEMA_INVALID", f"{SHEET_LABELS}!{spalte}{line}",
                        f"Das Pflichtfeld {feld!r} ist leer.",
                        "Eine Zuordnung ohne Beleg oder ohne Begruendung ist "
                        "als Referenz nicht nachvollziehbar.",
                        "Feld fuellen oder die Zeile loeschen."))
            beleg = eintrag["evidence_excerpt"]
            if beleg and not evidence_supported(task, beleg):
                quellen = ", ".join(label for label, _ in evidence_contexts(task))
                found.append(problem(
                    "GS_EVIDENCE_NOT_VERBATIM", f"{SHEET_LABELS}!D{line}",
                    f"Das Zitat {beleg[:60]!r} steht in keiner erlaubten "
                    f"Quelle ({quellen}).",
                    "Ein Beleg, den der Korpus nicht hergibt, traegt die "
                    "Zuordnung nicht - auch dann nicht, wenn er von Hand "
                    "eingetragen wurde.",
                    f"Zitat gegen das Blatt {SHEET_JURISDICTION!r} pruefen."))
    return found


def command_verify(args: argparse.Namespace) -> int:
    """Prueft die von Bene bearbeitete Reviewdatei.

    Die Wache gegen den ``packaged-foods``-Vorfall: Excel schreibt Werte still
    um, und ohne Rueckvergleich faellt das erst dem Import auf. Weicht ein Wert
    nur in der Gross- und Kleinschreibung ab, wird das ausdruecklich als
    Autokorrektur gemeldet und ist mit ``--fix-case`` behebbar.
    """
    target = Path(args.file) if args.file else WORKBOOK_PATH
    if not target.exists():
        block("GS_XLSX_MISSING", paths.relative_to_base(target),
              "Die Reviewdatei fehlt.",
              "Geprueft wird die bearbeitete Datei, nicht die CSV daneben.",
              "'to-xlsx' ausfuehren oder den Pfad mit --file angeben.")

    book = load_workbook(target, data_only=False)
    for name in (SHEET_LABELS, SHEET_TAXONOMY, SHEET_NOTES, SHEET_INFO):
        if name not in book.sheetnames:
            block("GS_XLSX_LAYOUT", paths.relative_to_base(target),
                  f"Das Blatt {name!r} fehlt; vorhanden sind "
                  f"{', '.join(book.sheetnames)}.",
                  "Ohne das Blatt fehlt der Pruefung ihre Bezugsgroesse.",
                  "Datei mit 'to-xlsx' neu erzeugen und die Aenderungen "
                  "uebertragen.")

    taxonomy = load_taxonomy()
    sample = load_sample()
    known = taxonomy.ids
    # Zwei Nachschlagewerke: die Schreibweise allein (Autokorrektur) und
    # zusaetzlich die Unicodeform. Ein aus dem Browser kopierter
    # Gedankenstrich oder ein geschuetztes Leerzeichen sieht im Blatt aus wie
    # die richtige ID und faellt ohne den zweiten Vergleich erst dem Import auf.
    lookup = {value.casefold(): value for value in known}
    unicode_lookup = {lookalike_form(value): value for value in known}
    problems: list[Problem] = []

    info_rows = _sheet_matrix(book[SHEET_INFO], INFO_COLUMNS, SHEET_INFO)
    hashes = {text_value(row["taxonomy_content_hash"].value).strip()
              for row in info_rows}
    fremd = sorted(value for value in hashes if value and value != taxonomy.content_hash)
    if fremd:
        problems.append(problem(
            "GS_SNAPSHOT_DRIFT", paths.relative_to_base(target),
            f"Die Datei nennt den Taxonomiehash {fremd[0][:12]}, freigegeben "
            f"ist {taxonomy.content_hash[:12]}.",
            "Die Labels stammen dann aus einer anderen Auswahlliste, als die "
            "Evaluation spaeter kennt.",
            "Taxonomie und Labels auf denselben Stand bringen: 'prepare', "
            "'run', 'collect', 'to-xlsx' wiederholen."))

    sheet = book[SHEET_LABELS]
    rows = _sheet_matrix(sheet, LABEL_COLUMNS, SHEET_LABELS)
    fixed = 0
    trimmed = 0
    zuordnungen: dict[str, list[dict[str, Any]]] = {}
    leerzeilen: dict[str, int] = {}
    seen: dict[str, set[str]] = {}
    decisions: dict[str, set[str]] = {}
    for row in rows:
        line = row["__zeile__"]
        unit_cell = row["unit_id"]
        unit_id = text_value(unit_cell.value).strip()
        for column in LABEL_COLUMNS:
            roh = text_value(row[column].value)
            if roh != roh.strip():
                if args.fix_case:
                    row[column].value = roh.strip()
                    row[column].data_type = "s"
                    trimmed += 1
                else:
                    problems.append(problem(
                        "GS_XLSX_WHITESPACE",
                        f"{SHEET_LABELS}!{get_column_letter(LABEL_COLUMNS.index(column) + 1)}{line}",
                        f"Der Wert {roh!r} traegt Leerraum am Rand.",
                        "Die Evaluation vergleicht zeichengenau; ein "
                        "eingefuegtes Leerzeichen macht aus einer richtigen "
                        "industry_id eine unbekannte, ohne dass es im Blatt "
                        "sichtbar waere.",
                        "'verify --fix-case' ausfuehren; es entfernt den "
                        "Randleerraum und schreibt die Datei zurueck."))
            if row[column].data_type == "f":
                problems.append(problem(
                    "GS_XLSX_FORMULA", f"{SHEET_LABELS}!{column}{line}",
                    f"Die Zelle enthaelt eine Formel: "
                    f"{text_value(row[column].value)[:60]}.",
                    "Ein Wert mit fuehrendem '=' wurde beim Speichern zur "
                    "Formel; zurueckgelesen wird dann ihr Ergebnis, nicht der "
                    "eingetragene Text.",
                    "Zelle als Text neu eintragen (fuehrendes Apostroph) und "
                    "erneut pruefen."))

        if unit_id not in {item.unit_id for item in sample}:
            problems.append(problem(
                "GS_UNIT_UNKNOWN", f"{SHEET_LABELS}!A{line}",
                f"Die unit_id {unit_id!r} steht nicht in der Stichprobe.",
                "Eine fremde Unit waere in der Evaluation nicht zuzuordnen.",
                f"Schreibweise gegen {paths.relative_to_base(SAMPLE_PATH)} "
                "pruefen."))
            continue

        industry_cell = row["industry_id"]
        industry_id = text_value(industry_cell.value).strip()
        reason = text_value(row["no_mapping_reason"].value).strip()
        decisions.setdefault(unit_id, set()).add("no_mapping" if not industry_id
                                                 else "mapped")

        if not industry_id:
            if not reason:
                problems.append(problem(
                    "GS_DECISION_CONFLICT", f"{SHEET_LABELS}!Zeile {line}",
                    "Die Zeile hat weder industry_id noch no_mapping_reason.",
                    "Eine leere Zeile ist kein Urteil; sie zaehlte in der "
                    "Evaluation als fehlende Unit.",
                    "Industry eintragen, Begruendung ergaenzen oder die Zeile "
                    "loeschen."))
            # Die Dublettenpruefung weiter unten sieht nur Zuordnungen; eine
            # Unit mit zwei no_mapping-Zeilen traege sonst zwei einander
            # widersprechende Begruendungen und bestuende trotzdem.
            elif unit_id in leerzeilen:
                problems.append(problem(
                    "GS_UNIT_DUPLICATE", f"{SHEET_LABELS}!Zeile {line}",
                    f"{unit_id} hat bereits in Zeile {leerzeilen[unit_id]} "
                    "eine no_mapping-Zeile.",
                    "Eine Unit hat genau eine no_mapping-Begruendung; zwei "
                    "liessen offen, welche das Gold-Set behauptet.",
                    "Eine der beiden Zeilen loeschen."))
            leerzeilen.setdefault(unit_id, line)
            continue
        if reason:
            problems.append(problem(
                "GS_DECISION_CONFLICT", f"{SHEET_LABELS}!Zeile {line}",
                f"{unit_id} traegt {industry_id} und zugleich eine "
                "no_mapping-Begruendung.",
                "Beides zugleich laesst offen, was das Gold-Set behauptet.",
                "Eines von beidem loeschen."))

        if industry_id not in known:
            match = lookup.get(industry_id.casefold())
            if match and args.fix_case:
                industry_cell.value = match
                industry_cell.data_type = "s"
                fixed += 1
                industry_id = match
            elif match:
                problems.append(problem(
                    "GS_INDUSTRY_CASE", f"{SHEET_LABELS}!B{line}",
                    f"{industry_id!r} statt {match!r} - Unterschied nur in der "
                    "Gross- und Kleinschreibung.",
                    "Das ist die Autokorrektur der Tabellenkalkulation; sie "
                    "hat im Lauf vom 2026-08-2x 'packaged-foods' zu "
                    "'Packaged-foods' gemacht und den Import abgelehnt.",
                    "'verify --fix-case' ausfuehren; es schreibt die "
                    "Schreibweise des Snapshots zurueck."))
            else:
                aehnlich = unicode_lookup.get(lookalike_form(industry_id))
                problems.append(problem(
                    "GS_INDUSTRY_UNKNOWN", f"{SHEET_LABELS}!B{line}",
                    f"{industry_id!r} steht nicht im Snapshot "
                    f"{taxonomy.snapshot_id}."
                    + (f" Zeichengleich bis auf die Unicodeform ist "
                       f"{aehnlich!r}." if aehnlich else ""),
                    "Die Evaluation vergleicht IDs zeichengenau; eine "
                    "unbekannte ID zaehlte als falsche Zuordnung."
                    + (" Hier weichen nur unsichtbare Zeichen ab - etwa ein "
                       "Gedankenstrich statt eines Bindestrichs."
                       if aehnlich else ""),
                    f"Wert aus der Auswahlliste des Blatts {SHEET_TAXONOMY!r} "
                    "waehlen."))

        if industry_id in seen.setdefault(unit_id, set()):
            problems.append(problem(
                "GS_UNIT_DUPLICATE", f"{unit_id} / {industry_id}",
                f"Die Zuordnung erscheint erneut in Zeile {line}.",
                "Eine doppelte Zuordnung zaehlte in der Evaluation doppelt.",
                "Zeile loeschen."))
        seen[unit_id].add(industry_id)

        confidence = text_value(row["confidence"].value).strip()
        if not confidence.isdigit() or not MIN_CONFIDENCE <= int(confidence) <= MAX_CONFIDENCE:
            problems.append(problem(
                "GS_SCHEMA_INVALID", f"{SHEET_LABELS}!C{line}",
                f"confidence={confidence!r} ist keine ganze Zahl zwischen "
                f"{MIN_CONFIDENCE} und {MAX_CONFIDENCE}.",
                "Die Rubrik ist vierstufig; ein anderer Wert ist in der "
                "Auswertung nicht interpretierbar.",
                "Wert korrigieren."))

        zuordnungen.setdefault(unit_id, []).append({
            "industry_id": industry_id,
            "confidence": int(confidence) if confidence.isdigit() else confidence,
            "evidence_excerpt": text_value(row["evidence_excerpt"].value).strip(),
            "rationale_short": text_value(row["rationale_short"].value).strip(),
            "__zeile__": line,
        })

    problems.extend(check_edited_units(zuordnungen, decisions,
                                       _sheet_matrix(book[SHEET_NOTES],
                                                     NOTE_COLUMNS, SHEET_NOTES)))

    for unit_id, kinds in decisions.items():
        if len(kinds) > 1:
            problems.append(problem(
                "GS_DECISION_CONFLICT", unit_id,
                "Die Unit hat Zuordnungszeilen und eine no_mapping-Zeile "
                "zugleich.",
                "Eine Unit hat entweder Zuordnungen oder genau eine "
                "no_mapping-Zeile.",
                "Die no_mapping-Zeile loeschen oder die Zuordnungen."))
    for item in sample:
        if item.unit_id not in decisions:
            problems.append(problem(
                "GS_UNIT_MISSING", item.unit_id,
                f"Die Unit hat im Blatt {SHEET_LABELS!r} keine Zeile.",
                "Das Gold-Set deckt die Stichprobe vollstaendig ab; eine "
                "fehlende Unit waere eine stille Luecke.",
                "Zeile ergaenzen: Industry eintragen oder no_mapping_reason "
                "fuellen."))

    if fixed or trimmed:
        gesichert = save_workbook(book, target)
        print(f"Gesichert: {paths.relative_to_base(gesichert)}")
        print(f"{fixed} Schreibweisen korrigiert, {trimmed} Zellen vom "
              f"Randleerraum befreit; {paths.relative_to_base(target)} "
              "zurueckgeschrieben.")
    print(f"Geprueft: {len(rows)} Zeilen, {len(decisions)} von {len(sample)} "
          "Units belegt.")

    bilanz = change_balance(rows, args.source)
    if bilanz:
        print(f"\nAenderungsbilanz gegen "
              f"{paths.relative_to_base(labels_path(args.source))}: "
              f"{len(bilanz)} Abweichungen")
        for zeile in bilanz[:20]:
            print(f"  {zeile}")
        if len(bilanz) > 20:
            print(f"  ... {len(bilanz) - 20} weitere")
    else:
        print(f"Aenderungsbilanz: keine Abweichung gegen "
              f"{paths.relative_to_base(labels_path(args.source))}.")
    return report_problems(problems)


# -- Trockenlauf -----------------------------------------------------------
#: Verzeichnis mit vorbereiteten Antworten. Ist es gesetzt, ersetzt ein Leser
#: aus Dateien die CLI. Der Modus ist fuer die Tests da: er prueft die Kette
#: von der Antwort bis zur Reviewdatei, ohne Abo-Kontingent zu verbrauchen.
FAKE_DIR_ENV = "GOLDSET_LABEL_FAKE_DIR"


def fake_dir() -> Path | None:
    raw = os.environ.get(FAKE_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def run_fake(root: Path, task: dict, config: EngineConfig) -> EngineResult:
    """Liest die Antwort aus einer Datei statt aus einer CLI."""
    path = root / f"{task['unit_id']}.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return EngineResult(
            answer=None, exit_code=1, duration_s=0.0, timed_out=False,
            stdout="", stderr=f"keine lesbare Antwort unter {path}")
    return EngineResult(
        answer=payload, exit_code=0, duration_s=0.0, timed_out=False,
        stdout=json.dumps(payload, ensure_ascii=False), stderr="",
        num_turns=1, cost_usd=0.0, session_id="fake")


def engine_runner(engine: str):
    """Der Aufrufweg der Engine - oder der Dateileser des Trockenlaufs."""
    root = fake_dir()
    if root is None:
        return ENGINE_RUNNERS[engine]
    if not root.is_dir():
        block("GS_FAKE_MODE", FAKE_DIR_ENV,
              f"{root} ist kein Verzeichnis.",
              "Die Umgebungsvariable schaltet die CLI ab; zeigt sie ins "
              "Leere, liefe der Lauf ohne Ergebnis und ohne Fehlermeldung.",
              "Variable auf ein Verzeichnis mit <unit_id>.json setzen oder "
              "loeschen.")
    print(f"Trockenlauf: Antworten aus {root}, keine CLI, kein Kontingent.")
    return lambda task, config, workdir: run_fake(root, task, config)


# -- CLI -------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="committee_matcher_goldset_label",
        description=(
            "Laesst die 22 Units der Gold-Set-Stichprobe von einem headless "
            "Agenten labeln. Die Labels sind ein Vorschlag; das Gold-Set "
            "entsteht erst durch die Abnahme."
        ),
    )
    actions = parser.add_subparsers(dest="action", required=True, metavar="AKTION")

    prepare = actions.add_parser(
        "prepare", help="Aufgabenpakete je Unit erzeugen (kostenfrei)")
    prepare.add_argument("--show", action="store_true",
                         help="Auszug des ersten Pakets ausgeben")
    prepare.set_defaults(handler=command_prepare)

    def add_engine_flags(target: argparse.ArgumentParser) -> None:
        # Modell und Denktiefe ohne Auswahlliste: die Werte gehen roh an die
        # CLI, damit kuenftige Modelle ohne Codeaenderung funktionieren.
        target.add_argument("--engine", choices=ENGINES,
                            help="CLI des Laufs; Default aus config/settings.py")
        target.add_argument("--model",
                            help="Modellname, roh an die CLI durchgereicht")
        target.add_argument("--effort",
                            help="Denktiefe, roh an die CLI durchgereicht")
        target.add_argument("--timeout", type=int, metavar="SEKUNDEN",
                            help="Zeitdeckel je Unit; Default aus "
                                 "config/settings.py")
        target.add_argument("--total-timeout", type=int, metavar="SEKUNDEN",
                            help="Zeitdeckel fuer den ganzen Satz; Default aus "
                                 "config/settings.py")

    preflight = actions.add_parser(
        "preflight", help="Engine, Modell und Denktiefe mit einem kurzen "
                          "Aufruf pruefen")
    add_engine_flags(preflight)
    preflight.set_defaults(handler=command_preflight)

    run = actions.add_parser(
        "run", help="Units labeln lassen (ein Lauf je Unit)")
    add_engine_flags(run)
    run.add_argument("--unit-id", action="append", default=[],
                     help="einzelne Unit-ID, mehrfach zulaessig; ohne Angabe "
                          "die ganze Stichprobe")
    run.add_argument("--force", action="store_true",
                     help="vorhandene Antworten neu erzeugen statt "
                          "ueberspringen")
    run.set_defaults(handler=command_run)

    collect = actions.add_parser(
        "collect", help="Rohantworten pruefen und als CSV sammeln (kostenfrei)")
    collect.add_argument("--engine", choices=ENGINES,
                         help="Lauf, der gesammelt wird; Default aus "
                              "config/settings.py")
    collect.set_defaults(handler=command_collect)

    merge = actions.add_parser(
        "merge", help="zwei Enginelaeufe vergleichen und den Dissens "
                      "aufschreiben")
    merge.add_argument("--base", choices=ENGINES, default="claude",
                       help="Lauf, dessen Zeilen die Basistabelle bilden")
    merge.add_argument("--against", choices=ENGINES, default="codex",
                       help="Lauf, gegen den verglichen wird")
    merge.set_defaults(handler=command_merge)

    to_xlsx = actions.add_parser(
        "to-xlsx", help="gesammelte Labels in die Reviewdatei schreiben")
    to_xlsx.add_argument("--source", default="merged",
                         help="Quelle der Zeilen: 'merged' oder ein "
                              "Enginename; Default 'merged'")
    to_xlsx.add_argument("--out", metavar="PFAD",
                         help="Zieldatei; Default "
                              f"{paths.relative_to_base(WORKBOOK_PATH)}")
    to_xlsx.add_argument("--force", action="store_true",
                         help="vorhandene Zieldatei ersetzen; die bisherige "
                              "Fassung wird vorher als '.bak.xlsx' gesichert")
    to_xlsx.set_defaults(handler=command_to_xlsx)

    verify = actions.add_parser(
        "verify", help="bearbeitete Reviewdatei gegen Snapshot und Vertrag "
                       "pruefen")
    verify.add_argument("--source", default="merged",
                        help="Zwischentabelle des Rueckvergleichs: 'merged' "
                             "oder ein Enginename; Default 'merged'")
    verify.add_argument("--file", metavar="PFAD",
                        help="zu pruefende Datei; Default "
                             f"{paths.relative_to_base(WORKBOOK_PATH)}")
    verify.add_argument("--fix-case", action="store_true",
                        help="Schreibweise bekannter industry_id "
                             "zurueckschreiben")
    verify.set_defaults(handler=command_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except GoldsetBlocked as blocked:
        print(blocked.problem.render(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
