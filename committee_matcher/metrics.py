"""Metrikkern der Evaluation (Plan P9c).

Reine Funktionen ueber (Goldlabels, Laufergebnisse): keine Datei, kein Netz,
kein Zustand. Der Prueflauf in P9d fuettert sie mit dem, was er ohnehin
gelesen hat, und schreibt nur das Ergebnis.

Zwei Festlegungen ziehen sich durch alle Metriken:

- Gerechnet wird ueber **Unit-Industry-Paare**, nicht ueber Units. Eine Unit
  mit zwei richtigen und einer falschen Zuordnung ist kein
  Alles-oder-nichts-Fall.
- Eine Division durch null liefert ``None`` und nicht ``0.0``. Der Unterschied
  zwischen "es gab nichts zu messen" und "es war alles falsch" entscheidet
  ueber die Freigabe des Modells.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Mapping, Sequence

from .models import (
    BROAD_SCOPE_MIN_CONFIDENCE,
    MAX_ASSIGNMENTS_REGULAR,
    is_research_evidence,
)

DECISION_MAPPED = "mapped"
DECISION_NO_MAPPING = "no_mapping"


def ratio(numerator: float, denominator: float) -> float | None:
    """Anteil, oder ``None``, wenn es keine Grundmenge gibt."""
    if not denominator:
        return None
    return numerator / denominator


@dataclass(frozen=True)
class UnitOutcome:
    """Das Ergebnis einer Unit - aus dem Gold-Set oder aus einem Lauf.

    Beide Seiten tragen dieselbe Form, damit der Vergleich keine Sonderfaelle
    braucht. ``confidences`` bleibt leer, wo keine vergeben wurde.
    """

    unit_id: str
    decision: str
    industries: frozenset[str]
    confidences: Mapping[str, str]
    broad_scope_exception: bool = False
    evidences: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_assignments(cls, unit_id: str,
                         assignments: Sequence[Mapping[str, object]], *,
                         decision: str | None = None,
                         broad_scope_exception: bool = False) -> "UnitOutcome":
        """Baut das Ergebnis aus Zeilen mit ``industry_id`` und ``confidence``.

        So lesen es beide Seiten: die Long-CSV des Laufs und die Labelzeilen
        des Gold-Sets tragen dieselben zwei Felder.
        """
        industries: set[str] = set()
        confidences: dict[str, str] = {}
        evidences: dict[str, str] = {}
        for row in assignments:
            industry_id = str(row.get("industry_id") or "").strip()
            if not industry_id:
                continue
            industries.add(industry_id)
            confidences[industry_id] = str(row.get("confidence") or "").strip()
            evidences[industry_id] = str(row.get("evidence_excerpt") or "").strip()
        return cls(
            unit_id=unit_id,
            decision=decision or (DECISION_MAPPED if industries
                                  else DECISION_NO_MAPPING),
            industries=frozenset(industries),
            confidences=confidences,
            broad_scope_exception=broad_scope_exception,
            evidences=evidences,
        )


def _aligned(gold: Mapping[str, UnitOutcome],
             run: Mapping[str, UnitOutcome]) -> tuple[str, ...]:
    """Units, die beide Seiten kennen; gemessen wird nur auf ihnen.

    Eine Unit, die nur eine Seite kennt, ist kein Treffer und kein Fehler,
    sondern eine Luecke im Vergleich - sie faellt ueber die Anzahlen auf.
    """
    return tuple(sorted(set(gold) & set(run)))


# ---------------------------------------------------------------------------
# Precision, Recall, F1
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PairMetrics:
    """Trefferbilanz ueber Unit-Industry-Paare."""

    units: int
    gold_pairs: int
    run_pairs: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    false_discovery_rate: float | None
    recall: float | None
    f1: float | None


def pair_metrics(gold: Mapping[str, UnitOutcome],
                 run: Mapping[str, UnitOutcome]) -> PairMetrics:
    """Primaermetrik: Precision beziehungsweise FDR ueber die Paarmenge."""
    units = _aligned(gold, run)
    true_positives = false_positives = false_negatives = 0
    gold_pairs = run_pairs = 0
    for unit_id in units:
        expected = gold[unit_id].industries
        actual = run[unit_id].industries
        gold_pairs += len(expected)
        run_pairs += len(actual)
        true_positives += len(expected & actual)
        false_positives += len(actual - expected)
        false_negatives += len(expected - actual)

    precision = ratio(true_positives, true_positives + false_positives)
    recall = ratio(true_positives, true_positives + false_negatives)
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return PairMetrics(
        units=len(units), gold_pairs=gold_pairs, run_pairs=run_pairs,
        true_positives=true_positives, false_positives=false_positives,
        false_negatives=false_negatives, precision=precision,
        false_discovery_rate=None if precision is None else 1 - precision,
        recall=recall, f1=f1,
    )


# ---------------------------------------------------------------------------
# Entscheidung mapped / no_mapping
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionMetrics:
    """Wie oft der Lauf ueberhaupt richtig entschieden hat.

    Beide Fehlrichtungen stehen getrennt: eine uebersehene Zuordnung und eine
    erfundene sind verschiedene Fehler mit verschiedenen Folgen.
    """

    units: int
    agreements: int
    accuracy: float | None
    #: Gold ``mapped``, Lauf ``no_mapping``.
    missed_mappings: int
    #: Gold ``no_mapping``, Lauf ``mapped``.
    spurious_mappings: int


def decision_metrics(gold: Mapping[str, UnitOutcome],
                     run: Mapping[str, UnitOutcome]) -> DecisionMetrics:
    units = _aligned(gold, run)
    agreements = missed = spurious = 0
    for unit_id in units:
        expected, actual = gold[unit_id].decision, run[unit_id].decision
        if expected == actual:
            agreements += 1
        elif expected == DECISION_MAPPED:
            missed += 1
        else:
            spurious += 1
    return DecisionMetrics(
        units=len(units), agreements=agreements,
        accuracy=ratio(agreements, len(units)),
        missed_mappings=missed, spurious_mappings=spurious,
    )


# ---------------------------------------------------------------------------
# Stabilitaet ueber die Wiederholungen einer Variante
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StabilityMetrics:
    """Wie verlaesslich derselbe Lauf dasselbe Ergebnis liefert."""

    units: int
    repetitions: int
    identical_units: int
    identical_share: float | None
    mean_jaccard: float | None


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Ueberschneidung zweier Zuordnungsmengen.

    Zwei leere Mengen gelten als identisch: zwei Wiederholungen, die beide
    ``no_mapping`` sagen, sind das stabilste Ergebnis ueberhaupt.
    """
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def stability_metrics(runs: Sequence[Mapping[str, UnitOutcome]]) -> StabilityMetrics:
    """Vergleicht die Wiederholungen einer Variante untereinander.

    Mit einer einzigen Wiederholung ist Stabilitaet nicht gemessen, sondern
    unbekannt; beide Kennzahlen sind dann ``None``.
    """
    if not runs:
        return StabilityMetrics(0, 0, 0, None, None)
    shared = set(runs[0])
    for run in runs[1:]:
        shared &= set(run)
    units = tuple(sorted(shared))
    if len(runs) < 2:
        return StabilityMetrics(len(units), len(runs), 0, None, None)

    identical = 0
    scores: list[float] = []
    for unit_id in units:
        sets = [run[unit_id].industries for run in runs]
        if all(item == sets[0] for item in sets):
            identical += 1
        scores.extend(jaccard(left, right) for left, right in combinations(sets, 2))
    return StabilityMetrics(
        units=len(units), repetitions=len(runs), identical_units=identical,
        identical_share=ratio(identical, len(units)),
        mean_jaccard=ratio(sum(scores), len(scores)),
    )


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfidenceMetrics:
    """Verteilung der Confidence des Laufs und ihre Treffsicherheit.

    Gemessen wird die Confidence des **Laufs**: sie soll aussagen, wie sehr
    seiner Zuordnung zu trauen ist. Stimmt die Reihenfolge nicht, ist die
    Angabe wertlos, auch wenn die Precision insgesamt stimmt.
    """

    distribution: Mapping[str, int]
    precision: Mapping[str, float | None]


def confidence_metrics(gold: Mapping[str, UnitOutcome],
                       run: Mapping[str, UnitOutcome]) -> ConfidenceMetrics:
    counts: dict[str, int] = {}
    hits: dict[str, int] = {}
    for unit_id in _aligned(gold, run):
        expected = gold[unit_id].industries
        for industry_id in sorted(run[unit_id].industries):
            level = run[unit_id].confidences.get(industry_id, "") or "ohne"
            counts[level] = counts.get(level, 0) + 1
            if industry_id in expected:
                hits[level] = hits.get(level, 0) + 1
    return ConfidenceMetrics(
        distribution=dict(sorted(counts.items())),
        precision={level: ratio(hits.get(level, 0), count)
                   for level, count in sorted(counts.items())},
    )


# ---------------------------------------------------------------------------
# Broad Scope
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BroadScopeMetrics:
    """Verstoesse gegen die Broad-Scope-Regel des Modellvertrags."""

    units_over_limit: int
    #: Mehr als ``MAX_ASSIGNMENTS_REGULAR`` Zuordnungen ohne gesetzte Ausnahme.
    without_exception: int
    #: Ausnahme gesetzt, aber eine Zuordnung traegt zu wenig Confidence.
    exception_below_threshold: int


def broad_scope_metrics(run: Mapping[str, UnitOutcome]) -> BroadScopeMetrics:
    over_limit = without_exception = below_threshold = 0
    for outcome in run.values():
        if len(outcome.industries) <= MAX_ASSIGNMENTS_REGULAR:
            continue
        over_limit += 1
        if not outcome.broad_scope_exception:
            without_exception += 1
            continue
        weak = [industry_id for industry_id in outcome.industries
                if not _at_least(outcome.confidences.get(industry_id, ""),
                                 BROAD_SCOPE_MIN_CONFIDENCE)]
        if weak:
            below_threshold += 1
    return BroadScopeMetrics(
        units_over_limit=over_limit, without_exception=without_exception,
        exception_below_threshold=below_threshold,
    )


# ---------------------------------------------------------------------------
# Recherche-Evidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchMetrics:
    """Getrennte Bilanz rein recherchegestuetzter Laufzuordnungen."""

    assignments: int
    units: int
    true_positives: int
    false_positives: int
    precision: float | None


def research_metrics(gold: Mapping[str, UnitOutcome],
                     run: Mapping[str, UnitOutcome]) -> ResearchMetrics:
    """Misst nur Zuordnungen, deren Evidence genau der Sentinel ist."""
    assignments = true_positives = false_positives = 0
    units: set[str] = set()
    for unit_id in _aligned(gold, run):
        researched = {
            industry_id for industry_id in run[unit_id].industries
            if is_research_evidence(run[unit_id].evidences.get(industry_id, ""))
        }
        if researched:
            units.add(unit_id)
        assignments += len(researched)
        true_positives += len(researched & gold[unit_id].industries)
        false_positives += len(researched - gold[unit_id].industries)
    return ResearchMetrics(
        assignments=assignments,
        units=len(units),
        true_positives=true_positives,
        false_positives=false_positives,
        precision=ratio(true_positives, assignments),
    )


def _at_least(value: str, threshold: int) -> bool:
    """Erreicht die Confidence den Schwellwert? Fehlende zaehlt als zu wenig."""
    try:
        return int(value) >= threshold
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Gesamtergebnis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunCost:
    """Was der gemessene Lauf gekostet und gebraucht hat."""

    spent_usd: float
    calls: int
    unknown_calls: int
    duration_s: float | None
    usd_per_unit: float | None


def run_cost(*, spent_usd: float, calls: int, unknown_calls: int,
             duration_s: float | None, units: int) -> RunCost:
    return RunCost(spent_usd=spent_usd, calls=calls, unknown_calls=unknown_calls,
                   duration_s=duration_s, usd_per_unit=ratio(spent_usd, units))


@dataclass(frozen=True)
class VariantMetrics:
    """Alle Kennzahlen einer Variante."""

    batch_size: int
    repetitions: int
    pairs: PairMetrics
    decisions: DecisionMetrics
    stability: StabilityMetrics
    confidence: ConfidenceMetrics
    broad_scope: BroadScopeMetrics
    research: ResearchMetrics
    cost: RunCost | None
    #: Units, die das Gold-Set kennt und der Lauf nicht, und umgekehrt.
    missing_in_run: tuple[str, ...]
    unexpected_in_run: tuple[str, ...]


def variant_metrics(*, batch_size: int, gold: Mapping[str, UnitOutcome],
                    runs: Sequence[Mapping[str, UnitOutcome]],
                    cost: RunCost | None = None) -> VariantMetrics:
    """Bewertet eine Variante ueber alle ihre Wiederholungen.

    Die Trefferbilanz rechnet gegen die **erste** Wiederholung: sie ist der
    Lauf, den ein normaler Einsatz erzeugt haette. Die uebrigen tragen die
    Stabilitaet bei, nicht die Precision - sonst waere die Primaermetrik ein
    Mittelwert ueber Wiederholungen, die es im Betrieb nicht gibt.
    """
    first: Mapping[str, UnitOutcome] = runs[0] if runs else {}
    return VariantMetrics(
        batch_size=batch_size,
        repetitions=len(runs),
        pairs=pair_metrics(gold, first),
        decisions=decision_metrics(gold, first),
        stability=stability_metrics(runs),
        confidence=confidence_metrics(gold, first),
        broad_scope=broad_scope_metrics(first),
        research=research_metrics(gold, first),
        cost=cost,
        missing_in_run=tuple(sorted(set(gold) - set(first))),
        unexpected_in_run=tuple(sorted(set(first) - set(gold))),
    )
