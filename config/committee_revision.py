"""Die aktive Mappingrevision der Pipeline (Integrationsplan P2).

`reviews/<id>/` ist kein Pfad, sondern eine Menge: der Matcher schreibt jede
abgenommene Revision unter ihrer eigenen ID, und die Pipeline muss sagen
koennen, welche davon gilt. Das leistet `COMMITTEE_MAPPING_REVIEW_ID` in
`config/settings.py`; dieses Modul loest die ID zu einem geprueften Satz
Zuordnungen auf.

Geprueft wird, was die Revision unbrauchbar machen wuerde, und nur das:

* `import_manifest.json` vorhanden - es ist der Abschlussmarker des Imports.
  Fehlt es, ist die Revision unvollstaendig geschrieben, egal wie viele
  CSV-Zeilen daneben liegen.
* `review_status == "reviewed"` - eine offene Revision ist ein Zwischenstand.
* Zeilenzahl gegen `assignment_count` - Manifest und CSV muessen denselben
  Stand meinen; eine von Hand geloeschte Zeile faellt sonst nicht auf.
* jede `industry_id` gegen die **freigegebene Baseline**, nicht gegen den
  Taxonomiehash. Der Hash aendert sich mit jedem Refresh und wuerde die eben
  freigegebene Revision beim naechsten Lauf abweisen - die Schutzmassnahme
  legte Filter 3 lahm. Die Frage, die zaehlt, ist: kennt die Baseline noch
  jede Industry, auf die sich das Mapping beruft?

Keine Hartsperre gegen ein Probeartefakt. Ein Testlauf ueber fuenf Ausschuesse
traegt `scope: test-fullrun` und wird beim Laden als solcher ausgewiesen -
sichtbar, aber nicht verboten: gegen ihn entsteht der Adapter, bevor der
Fullrun sein Ergebnis liefert.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from committee_matcher import paths, review, taxonomy_snapshot
from committee_matcher.errors import ErrorCollector

logger = logging.getLogger(__name__)

#: Name des Settings, das die geltende Revision benennt. Als Konstante
#: gefuehrt, weil die Fehlermeldungen darauf zeigen: wer sie liest, soll die
#: Stelle kennen, an der sich der Wert aendern laesst.
REVIEW_ID_SETTING = "COMMITTEE_MAPPING_REVIEW_ID"


#: Umfang, den ein Mapping fuer die Pipeline haben muss. Alles andere ist ein
#: Probelauf und wird beim Laden benannt.
SCOPE_FULLRUN = "fullrun"

#: Entscheidung, die eine Zeile zu einer Zuordnung macht. Zeilen ohne
#: Zuordnung stehen in `reviewed_unit_coverage.csv`, nicht hier.
DECISION_MAPPED = "mapped"


class RevisionError(RuntimeError):
    """Die aktive Revision ist nicht benutzbar.

    Traegt den Fehlercode als Feld, damit die Pipeline ihn ausweisen kann,
    ohne den Text zu zerlegen.
    """

    def __init__(self, code: str, obj: str, was: str, abhilfe: str) -> None:
        self.code = code
        self.obj = obj
        self.was = was
        self.abhilfe = abhilfe
        super().__init__(f"[{code}] {obj}: {was} Abhilfe: {abhilfe}")


@dataclass(frozen=True)
class Assignment:
    """Eine geprüfte Zuordnung Unit -> Industry.

    Bewusst schmal: der Adapter braucht Schluessel, Ziel und Konfidenz. Die
    uebrigen 26 Spalten der Artefaktzeile sind Provenienz und gehoeren in die
    Revision, nicht in den Lookup der Pipeline.
    """

    unit_id: str
    chamber: str
    committee_name: str
    unit_name: str
    unit_kind: str
    sector_id: str
    sector_name: str
    industry_id: str
    industry_name: str
    confidence: int


@dataclass(frozen=True)
class Revision:
    """Eine vollstaendige, gegen die Baseline geprüfte Mappingrevision."""

    review_id: str
    path: Path
    scope: str
    snapshot_id: str
    unit_count: int
    assignments: tuple[Assignment, ...]

    @property
    def is_fullrun(self) -> bool:
        return self.scope == SCOPE_FULLRUN

    @property
    def summary(self) -> str:
        """Einzeiler fuer das Terminal - die Unitzahl steht vorn.

        Ein Lauf, der `5 Units` meldet, ist damit als Probeartefakt zu
        erkennen, ohne dass jemand ins Manifest sieht.
        """
        umfang = "" if self.is_fullrun else f", scope {self.scope}"
        return (f"Mappingrevision {self.review_id}: {self.unit_count} Units, "
                f"{len(self.assignments)} Zuordnungen, Snapshot "
                f"{self.snapshot_id}{umfang}")


def revisions_root() -> Path:
    """Verzeichnis, unter dem alle Revisionen liegen.

    Funktion statt Konstante: eine Konstante waere beim Import an die
    Artefaktwurzel gebunden und zeigte weiter dorthin, wenn ein Test sie
    ersetzt.
    """
    return paths.ARTIFACT_ROOT / review.REVIEWS_DIR


def revision_path(review_id: str) -> Path:
    """Verzeichnis der Revision. Der Pfad wird abgeleitet, nicht konfiguriert."""
    return review.review_path(review_id)


def active_review_id(settings: Any) -> str:
    """Die konfigurierte Revision; leer ist kein gueltiger Wert."""
    return str(getattr(settings, REVIEW_ID_SETTING, "") or "").strip()


def _manifest(review_id: str) -> Mapping[str, Any]:
    pfad = revision_path(review_id) / review.IMPORT_MANIFEST_FILENAME
    if not pfad.exists():
        # Getrennt vom fehlenden Verzeichnis gemeldet: liegen CSVs ohne
        # Manifest, ist ein Import abgebrochen - eine andere Lage als eine
        # Revision, die es nie gab, und eine andere Abhilfe.
        if not revision_path(review_id).exists():
            raise RevisionError(
                "MAP_REVISION_MISSING", review_id,
                f"Es gibt keine Revision unter "
                f"{paths.relative_to_base(revision_path(review_id))}.",
                f"Vorhandene Revisionen unter "
                f"{paths.relative_to_base(revisions_root())} pruefen und "
                f"{REVIEW_ID_SETTING} in config/settings.py setzen.")
        raise RevisionError(
            "MAP_MANIFEST_MISSING", review_id,
            f"Die Revision hat kein {review.IMPORT_MANIFEST_FILENAME}.",
            "Das Manifest ist der Abschlussmarker des Imports; die Revision "
            "ist unvollstaendig. Import mit 'submapper.py finish "
            "--review-id <id>' wiederholen.")
    try:
        document = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as fehler:
        raise RevisionError(
            "MAP_MANIFEST_INVALID", review_id,
            f"{paths.relative_to_base(pfad)} ist nicht lesbar: {fehler}.",
            "Manifest aus derselben Programmversion erzeugen; von Hand "
            "geaenderte Manifeste werden nicht gelesen.") from fehler
    if not isinstance(document, dict):
        raise RevisionError(
            "MAP_MANIFEST_INVALID", review_id,
            f"{paths.relative_to_base(pfad)} enthaelt kein JSON-Objekt.",
            "Manifest aus derselben Programmversion erzeugen; von Hand "
            "geaenderte Manifeste werden nicht gelesen.")
    return document


def _pflichtfeld(manifest: Mapping[str, Any], feld: str, review_id: str) -> Any:
    if feld not in manifest:
        raise RevisionError(
            "MAP_MANIFEST_INVALID", review_id,
            f"Dem Manifest fehlt das Feld {feld!r}.",
            "Manifest aus derselben Programmversion erzeugen; von Hand "
            "geaenderte Manifeste werden nicht gelesen.")
    return manifest[feld]


def _zeilen(review_id: str) -> tuple[dict[str, str], ...]:
    pfad = revision_path(review_id) / review.REVIEWED_ASSIGNMENTS_FILENAME
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as fehler:
        raise RevisionError(
            "MAP_ARTIFACT_UNREADABLE", review_id,
            f"{paths.relative_to_base(pfad)} ist nicht lesbar: {fehler}.",
            "Die Revision muss vollstaendig geschrieben sein; Import "
            "wiederholen.") from fehler
    # ``newline=""`` haelt eingebettete Zeilenumbrueche zusammen: die
    # Begruendungen sind Fliesstext und enthalten sie.
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != review.REVIEWED_COLUMNS:
        raise RevisionError(
            "MAP_ARTIFACT_CONTRACT", review_id,
            "Die Spalten von "
            f"{paths.relative_to_base(pfad)} weichen vom Vertrag ab.",
            "Artefakt aus derselben Programmversion erzeugen; von Hand "
            "geaenderte CSVs werden nicht gelesen.")
    return tuple(dict(row) for row in reader)


def _confidence(wert: str, unit_id: str, review_id: str) -> int:
    try:
        stufe = int(str(wert).strip())
    except (TypeError, ValueError) as fehler:
        raise RevisionError(
            "MAP_CONFIDENCE_INVALID", review_id,
            f"{unit_id} traegt die Konfidenz {wert!r}, die keine Zahl ist.",
            "Die Konfidenz steuert den Scorebeitrag; die Revision ist "
            "unbrauchbar. Import wiederholen.") from fehler
    return stufe


def load_revision(review_id: str) -> Revision:
    """Laedt eine Revision und prueft sie gegen die freigegebene Baseline."""
    if not review_id:
        raise RevisionError(
            "MAP_REVISION_UNSET", REVIEW_ID_SETTING,
            "Es ist keine Mappingrevision gesetzt.",
            f"{REVIEW_ID_SETTING} in config/settings.py auf die ID der "
            "geltenden Revision setzen.")
    manifest = _manifest(review_id)
    status = _pflichtfeld(manifest, "review_status", review_id)
    if status != review.REVIEW_STATUS_REVIEWED:
        raise RevisionError(
            "MAP_NOT_REVIEWED", review_id,
            f"Die Revision steht auf {status!r} statt "
            f"{review.REVIEW_STATUS_REVIEWED!r}.",
            "Reviewmappe abnehmen und den Import abschliessen; ein offener "
            "Stand ist kein Mapping.")

    zeilen = _zeilen(review_id)
    erwartet = _pflichtfeld(manifest, "assignment_count", review_id)
    if len(zeilen) != erwartet:
        raise RevisionError(
            "MAP_COUNT_MISMATCH", review_id,
            f"Das Manifest nennt {erwartet!r} Zuordnungen, die Datei "
            f"enthaelt {len(zeilen)}.",
            "Manifest und CSV stammen aus verschiedenen Staenden; Import "
            "wiederholen statt eine der beiden Zahlen anzupassen.")

    collector = ErrorCollector()
    snapshot = taxonomy_snapshot.approved_snapshot(collector)
    if snapshot is None:
        befund = collector.blockers[0] if collector.blockers else None
        raise RevisionError(
            befund.code if befund else "MAP_TAXONOMY_MISSING", review_id,
            (befund.message if befund
             else "Es ist keine Taxonomie freigegeben."),
            (befund.hint if befund
             else "'snapshot build' und 'snapshot approve' ausfuehren."))
    bekannt = snapshot.industry_ids()

    zuordnungen: list[Assignment] = []
    unbekannt: set[str] = set()
    for zeile in zeilen:
        if zeile.get("decision") != DECISION_MAPPED:
            continue
        industry_id = zeile.get("industry_id", "")
        if industry_id not in bekannt:
            unbekannt.add(industry_id)
            continue
        zuordnungen.append(Assignment(
            unit_id=zeile["unit_id"], chamber=zeile["chamber"],
            committee_name=zeile["committee_name"],
            unit_name=zeile["unit_name"], unit_kind=zeile["unit_kind"],
            sector_id=zeile["sector_id"], sector_name=zeile["sector_name"],
            industry_id=industry_id, industry_name=zeile["industry_name"],
            confidence=_confidence(zeile.get("confidence", ""),
                                   zeile["unit_id"], review_id)))
    if unbekannt:
        # Alle auf einmal, nicht die erste: wer die Baseline nachzieht, will
        # wissen, was ihm insgesamt fehlt.
        raise RevisionError(
            "MAP_INDUSTRY_UNKNOWN", review_id,
            f"Die freigegebene Taxonomie {snapshot.snapshot_id} kennt "
            f"{len(unbekannt)} Industries der Revision nicht: "
            + ", ".join(sorted(unbekannt)[:5])
            + (" ..." if len(unbekannt) > 5 else "") + ".",
            "Entweder die Taxonomie auf den Stand der Revision zuruecknehmen "
            "oder das Mapping neu erzeugen; ein Mapping auf unbekannte "
            "Industries trifft keinen Trade.")

    revision = Revision(
        review_id=review_id, path=revision_path(review_id),
        scope=str(manifest.get("scope", SCOPE_FULLRUN)),
        snapshot_id=str(_pflichtfeld(manifest, "snapshot_id", review_id)),
        unit_count=int(_pflichtfeld(manifest, "unit_count", review_id)),
        assignments=tuple(zuordnungen))
    if revision.is_fullrun:
        logger.info(revision.summary)
    else:
        # Kein Fehler, aber auch nichts, was unbemerkt bleiben darf: fuenf von
        # 225 Ausschuessen ergeben ein Mapping, das fast jeden Trade verfehlt.
        logger.warning("%s - Probeartefakt, kein Mapping fuer die Pipeline.",
                       revision.summary)
    return revision


def load_active_revision(settings: Any) -> Revision:
    """Die Revision, die laut ``config/settings.py`` gilt."""
    return load_revision(active_review_id(settings))
