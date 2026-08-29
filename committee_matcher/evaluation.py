"""Evaluationsrevisionen des Committee Matchers V2 (Plan P9).

Die Evaluation misst den Produktivpfad (``run plan|execute``) gegen das
Entwicklungs-Gold-Set. Damit das Ergebnis etwas ueber genau diesen Pfad
aussagt, friert ``evaluate export`` alle ergebnisbestimmenden Bedingungen
**vor** dem Labeln ein: Taxonomiestand, Stichprobe, Inputhashes, Prompt- und
Schemahashes, Routing, Kontextgrenzen, Variantenmatrix und Budget. Der Import
der Labels und der spaetere Prueflauf pruefen jeden dieser Werte nach; weicht
einer ab, ist es eine andere Evaluation und bekommt eine eigene Revision.

Zwei Abgrenzungen sind bewusst:

- Das Manifest entsteht aus ``workflow.build_manifest``, nicht aus einem
  zweiten Bauweg. Ein eigener Bauweg koennte andere Bedingungen einfrieren als
  der Lauf spaeter verwendet, und der Vergleich waere wertlos.
- ``batching.batch_size`` faellt aus dem Manifest heraus: die Batchgroesse ist
  in der Evaluation die Variante und steht in ``variants.batch_sizes``. Bliebe
  der Settingswert stehen, widerspraeche er jeder Variante ausser einer.

Keine Revision wird ueberschrieben, und weder Prompttext noch Zugangsdaten
gelangen in ein Artefakt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from . import paths, workflow
from .errors import ErrorCollector, MatcherError, Stage
from .models import MatcherUnit
from .status import ExitCode, WorkflowState

#: Revisionsverzeichnis der Evaluationen, neben ``runs`` und ``reviews``.
EVALUATIONS_DIR = "evaluations"
#: Ablage der Stichprobe und der Labels des Entwicklungs-Gold-Sets.
GOLDSET_DIR = "goldset"
SAMPLE_FILENAME = "sample_units.json"
SAMPLE_VERSION = "goldset-sample-v1"

EXPORT_MANIFEST_FILENAME = "export_manifest.json"
EXPORT_MANIFEST_VERSION = "evaluation-export-v1"
STATE_FILENAME = "evaluation_state.json"
STATE_VERSION = "evaluation-state-v1"

#: Kopffelder, die jede Stichprobenzeile tragen muss.
SAMPLE_UNIT_FIELDS = ("unit_id", "chamber", "committee_name", "unit_name",
                      "input_basis")


class EvaluationError(MatcherError):
    """Fehler im Evaluationspfad."""


def _blocked(code: str, obj: str, message: str, hint: str,
             exit_code: ExitCode = ExitCode.PREFLIGHT_BLOCKED) -> EvaluationError:
    collector = ErrorCollector()
    collector.blocker(code, Stage.EVALUATION, obj, message, hint,
                      exit_code=exit_code)
    return EvaluationError(collector.blockers, message)


def _reject(collector: ErrorCollector, code: str, obj: str, message: str,
            hint: str, exit_code: ExitCode = ExitCode.PREFLIGHT_BLOCKED) -> None:
    collector.blocker(code, Stage.EVALUATION, obj, message, hint,
                      exit_code=exit_code)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def evaluation_path(eval_id: str, *parts: str,
                    create_parents: bool = False) -> Path:
    return paths.artifact_path(EVALUATIONS_DIR, eval_id, *parts,
                               create_parents=create_parents)


def sample_path() -> Path:
    return paths.artifact_path(GOLDSET_DIR, SAMPLE_FILENAME)


# ---------------------------------------------------------------------------
# Stichprobe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SampleUnit:
    """Eine Unit der Gold-Set-Stichprobe.

    ``focus`` aus der Stichprobendatei wird bewusst nicht uebernommen: der
    Wert nennt die erwartete Fallabdeckung und duerfte nie in die Naehe eines
    Aufgabenpakets geraten.
    """

    unit_id: str
    chamber: str
    committee_name: str
    unit_name: str
    input_basis: str


@dataclass(frozen=True)
class GoldsetSample:
    """Die eingefrorene Stichprobe des Entwicklungs-Gold-Sets."""

    version: str
    expected_unit_count: int
    units: tuple[SampleUnit, ...]

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(unit.unit_id for unit in self.units)


def load_sample(collector: ErrorCollector) -> GoldsetSample | None:
    """Liest die Stichprobe und prueft ihren Vertrag.

    Gibt ``None`` zurueck, sobald die Datei fehlt oder nicht mehr die
    freigegebene Stichprobe beschreibt: gegen eine stillschweigend veraenderte
    Stichprobe zu messen waere schlimmer als gar nicht zu messen.
    """
    target = sample_path()
    if not target.exists():
        _reject(collector, "EVAL_SAMPLE_MISSING",
                paths.relative_to_base(target),
                "Die Stichprobe des Entwicklungs-Gold-Sets fehlt.",
                "Stichprobe aus organisation/committee_matcher_goldset_stichprobe.md "
                "wiederherstellen; ohne sie gibt es keine Evaluationsmenge.")
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _reject(collector, "EVAL_SAMPLE_UNREADABLE",
                paths.relative_to_base(target),
                f"Die Stichprobe ist nicht lesbar: {exc.__class__.__name__}.",
                "Datei sichten; die Evaluation liest sie unveraendert ein.")
        return None
    if not isinstance(payload, dict):
        _reject(collector, "EVAL_SAMPLE_CONTRACT",
                paths.relative_to_base(target),
                "Die Stichprobe ist kein JSON-Objekt.",
                "Datei gegen das Format 'goldset-sample-v1' pruefen.")
        return None
    if payload.get("sample_version") != SAMPLE_VERSION:
        _reject(collector, "EVAL_SAMPLE_CONTRACT",
                paths.relative_to_base(target),
                f"sample_version={payload.get('sample_version')!r} statt "
                f"{SAMPLE_VERSION!r}.",
                "Stichprobe der freigegebenen Version verwenden; eine andere "
                "Version beschreibt eine andere Evaluationsmenge.")
        return None

    rows = payload.get("units")
    if not isinstance(rows, list) or not rows:
        _reject(collector, "EVAL_SAMPLE_CONTRACT",
                paths.relative_to_base(target),
                "'units' fehlt oder ist leer.",
                "Stichprobe wiederherstellen; eine leere Menge misst nichts.")
        return None

    units: list[SampleUnit] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=1):
        obj = f"units[{position}]"
        if not isinstance(row, dict):
            _reject(collector, "EVAL_SAMPLE_CONTRACT", obj,
                    "Eintrag ist kein Objekt.",
                    "Stichprobe gegen das Format 'goldset-sample-v1' pruefen.")
            continue
        missing = [field for field in SAMPLE_UNIT_FIELDS if not row.get(field)]
        if missing:
            _reject(collector, "EVAL_SAMPLE_CONTRACT", obj,
                    f"Eintrag ohne {', '.join(missing)}.",
                    "Fehlende Kopffelder ergaenzen; sie belegen, welche Unit "
                    "gemeint ist.")
            continue
        unit_id = str(row["unit_id"]).strip()
        if unit_id in seen:
            _reject(collector, "EVAL_SAMPLE_DUPLICATE", unit_id,
                    "Diese Unit steht mehrfach in der Stichprobe.",
                    "Doppelten Eintrag entfernen; sonst zaehlte die Unit "
                    "doppelt in jede Metrik.")
            continue
        seen.add(unit_id)
        units.append(SampleUnit(
            unit_id=unit_id,
            chamber=str(row["chamber"]).strip(),
            committee_name=str(row["committee_name"]).strip(),
            unit_name=str(row["unit_name"]).strip(),
            input_basis=str(row["input_basis"]).strip(),
        ))

    expected = payload.get("expected_unit_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        _reject(collector, "EVAL_SAMPLE_CONTRACT",
                paths.relative_to_base(target),
                f"expected_unit_count={expected!r} ist keine positive Zahl.",
                "Sollzahl der Stichprobe eintragen; sie ist die Gegenprobe "
                "zur Zeilenzahl.")
        return None
    if expected != len(rows):
        _reject(collector, "EVAL_SAMPLE_COUNT",
                paths.relative_to_base(target),
                f"Die Stichprobe nennt {expected} Units, enthaelt aber "
                f"{len(rows)}.",
                "Stichprobe wiederherstellen; eine unbemerkt gekuerzte Menge "
                "verzerrt jede Metrik.")
        return None
    if collector.has_blockers():
        return None
    return GoldsetSample(version=SAMPLE_VERSION, expected_unit_count=expected,
                         units=tuple(units))


def select_units(collector: ErrorCollector, units: Sequence[MatcherUnit],
                 sample: GoldsetSample) -> tuple[MatcherUnit, ...] | None:
    """Bildet die Stichprobe auf die gebauten Units ab.

    Geprueft werden auch die Kopffelder: eine Unit-ID kann stabil bleiben,
    waehrend Kammer, Committee oder Name sich geaendert haben - dann meint die
    Stichprobe nicht mehr dieselbe Unit.
    """
    known = {unit.unit_id: unit for unit in units}
    selected: list[MatcherUnit] = []
    for entry in sample.units:
        unit = known.get(entry.unit_id)
        if unit is None:
            _reject(collector, "EVAL_SAMPLE_UNIT_UNKNOWN", entry.unit_id,
                    "Diese Unit der Stichprobe kommt in der Sollmenge nicht vor.",
                    "Stichprobe gegen 'input validate' abgleichen; eine "
                    "fehlende Unit macht die Evaluationsmenge unvollstaendig.")
            continue
        drift = [
            f"{name}: Stichprobe {expected!r}, Sollmenge {current!r}"
            for name, expected, current in (
                ("chamber", entry.chamber, unit.chamber),
                ("committee_name", entry.committee_name, unit.committee_name),
                ("unit_name", entry.unit_name, unit.unit_name),
                ("input_basis", entry.input_basis, str(unit.input_basis.value)),
            )
            if expected != current
        ]
        if drift:
            _reject(collector, "EVAL_SAMPLE_UNIT_DRIFT", entry.unit_id,
                    "Die Unit hat sich seit der Stichprobenziehung geaendert: "
                    + "; ".join(drift) + ".",
                    "Stichprobe neu ziehen und neu labeln; gegen eine "
                    "veraenderte Unit gemessene Labels sind ungueltig.")
            continue
        if not unit.mapping_input_hash:
            _reject(collector, "EVAL_UNIT_UNBOUND", entry.unit_id,
                    "Der Unit fehlt der mapping_input_hash.",
                    "'input validate' ausfuehren; ohne Inputhash ist die Unit "
                    "an keinen Textstand gebunden.")
            continue
        selected.append(unit)
    if collector.has_blockers():
        return None
    return tuple(selected)


# ---------------------------------------------------------------------------
# Revision
# ---------------------------------------------------------------------------

def revision_taken(eval_id: str) -> str | None:
    """Nennt den Grund, aus dem die Kennung nicht mehr frei ist, sonst ``None``.

    Geprueft wird auch die gleichnamige Runrevision: der Export legt seinen
    frischen Snapshot unter ``runs/<eval_id>/`` ab, und ein fremder Lauf
    duerfte dabei nichts verlieren.
    """
    if evaluation_path(eval_id, EXPORT_MANIFEST_FILENAME).exists():
        return "traegt bereits ein Exportmanifest"
    if workflow.manifest_path(eval_id).exists():
        return "ist bereits als Runrevision belegt"
    return None


def assert_revision_free(collector: ErrorCollector, eval_id: str) -> bool:
    """Blockiert eine belegte Kennung; ``True`` heisst 'darf schreiben'."""
    reason = revision_taken(eval_id)
    if reason is None:
        return True
    _reject(collector, "EVAL_REVISION_TAKEN", eval_id,
            f"Die Kennung {reason}.",
            "Neue --eval-id verwenden; eine Evaluationsrevision wird nie "
            "ueberschrieben.")
    return False


def build_export_manifest(*, eval_id: str, settings, provider_settings,
                          prompts_used, taxonomy_content_hash: str,
                          snapshot_id: str, input_manifest_hash: str,
                          targets: Sequence[MatcherUnit],
                          sample: GoldsetSample,
                          budget_usd: float,
                          json_schema: Mapping | None = None,
                          ) -> dict[str, object]:
    """Friert die Evaluationsbedingungen ein.

    Die Grundlage ist das Runmanifest: der Prueflauf entsteht spaeter aus
    denselben Feldern, und nur deshalb ist die Bindung pruefbar.
    """
    base = dict(workflow.build_manifest(
        run_id=eval_id,
        settings=settings,
        provider_settings=provider_settings,
        prompts_used=prompts_used,
        taxonomy_content_hash=taxonomy_content_hash,
        snapshot_id=snapshot_id,
        input_manifest_hash=input_manifest_hash,
        unit_count=len(targets),
        budget_usd=budget_usd,
        json_schema=json_schema,
        selection={"chamber": None, "committee": None,
                   "unit_ids": sorted(unit.unit_id for unit in targets)},
    ))
    base.pop("version", None)
    base.pop("run_id", None)
    batching = dict(base.get("batching") or {})
    batching.pop("batch_size", None)
    base["batching"] = batching
    offline = settings.offline
    return {
        "version": EXPORT_MANIFEST_VERSION,
        "eval_id": eval_id,
        "created_at": _now_iso(),
        "sample_version": sample.version,
        # Die Zuordnung Unit -> Textstand. Aendert sich ein Jurisdiktionstext
        # nach dem Labeln, faellt genau diese Unit auf und nicht der ganze Lauf.
        "mapping_input_hashes": {
            unit.unit_id: unit.mapping_input_hash for unit in
            sorted(targets, key=lambda item: item.unit_id)
        },
        "variants": {
            "batch_sizes": list(offline.evaluation_batch_sizes),
            "repetitions": offline.evaluation_repetitions,
            "max_repetitions": offline.evaluation_max_repetitions,
        },
        **base,
    }


def write_export_manifest(eval_id: str, manifest: Mapping[str, object]) -> Path:
    target = evaluation_path(eval_id, EXPORT_MANIFEST_FILENAME,
                             create_parents=True)
    return paths.write_text_atomic(
        target, json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n")


def load_export_manifest(eval_id: str) -> dict | None:
    """Liest das Exportmanifest; fehlt oder faellt es aus dem Vertrag, ``None``."""
    target = evaluation_path(eval_id, EXPORT_MANIFEST_FILENAME)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != EXPORT_MANIFEST_VERSION:
        return None
    if data.get("eval_id") != eval_id:
        return None
    return data


def require_export_manifest(eval_id: str) -> dict:
    """Wie ``load_export_manifest``, blockiert aber statt ``None`` zu liefern."""
    manifest = load_export_manifest(eval_id)
    if manifest is None:
        raise _blocked(
            "EVAL_EXPORT_MANIFEST_MISSING", eval_id,
            "Zur Evaluationsrevision fehlt ein brauchbares Exportmanifest.",
            f"'evaluate export --eval-id {eval_id}' ausfuehren; ohne Manifest "
            "ist die Herkunft der Labels nicht belegbar.")
    return manifest


def write_state(eval_id: str, state: WorkflowState,
                **fields: object) -> Path:
    """Haelt den Ausgang der Evaluationsrevision fest."""
    target = evaluation_path(eval_id, STATE_FILENAME, create_parents=True)
    payload = {
        "version": STATE_VERSION,
        "eval_id": eval_id,
        "state": state.value,
        "updated_at": _now_iso(),
        **fields,
    }
    return paths.write_text_atomic(
        target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_state(eval_id: str) -> dict | None:
    """Liest den Zustand der Revision; unbrauchbar wie fehlend behandelt."""
    target = evaluation_path(eval_id, STATE_FILENAME)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return None
    if data.get("eval_id") != eval_id:
        return None
    return data
