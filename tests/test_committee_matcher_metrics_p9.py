"""Tests des Metrikkerns der Evaluation (P9c).

Reine Rechentests: keine Datei, kein Netz, kein CLI. Jede Zahl ist von Hand
nachgerechnet und steht als Kommentar am Fall.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import metrics  # noqa: E402


def outcome(unit_id: str, *pairs: tuple[str, str],
            broad_scope_exception: bool = False) -> metrics.UnitOutcome:
    """Ein Ergebnis aus Paaren (industry_id, confidence)."""
    return metrics.UnitOutcome.from_assignments(
        unit_id,
        [{"industry_id": industry_id, "confidence": confidence}
         for industry_id, confidence in pairs],
        broad_scope_exception=broad_scope_exception)


def indexed(*outcomes: metrics.UnitOutcome) -> dict[str, metrics.UnitOutcome]:
    return {item.unit_id: item for item in outcomes}


# ---------------------------------------------------------------------------
# Grundrechnung
# ---------------------------------------------------------------------------

def test_a_ratio_without_a_base_is_unknown_not_zero():
    # Der Unterschied entscheidet ueber die Freigabe: 'nichts gemessen' ist
    # kein 'alles falsch'.
    assert metrics.ratio(0, 0) is None
    assert metrics.ratio(0, 4) == 0.0
    assert metrics.ratio(3, 4) == 0.75


def test_pairs_are_counted_per_assignment_not_per_unit():
    # Unit A: gold {x, y}, Lauf {x, z} -> 1 TP, 1 FP, 1 FN
    # Unit B: gold {x},    Lauf {x}    -> 1 TP
    # Summe: TP 2, FP 1, FN 1
    gold = indexed(outcome("a", ("x", "4"), ("y", "3")), outcome("b", ("x", "4")))
    run = indexed(outcome("a", ("x", "4"), ("z", "2")), outcome("b", ("x", "3")))
    result = metrics.pair_metrics(gold, run)
    assert (result.true_positives, result.false_positives,
            result.false_negatives) == (2, 1, 1)
    assert result.precision == pytest.approx(2 / 3)
    assert result.false_discovery_rate == pytest.approx(1 / 3)
    assert result.recall == pytest.approx(2 / 3)
    assert result.f1 == pytest.approx(2 / 3)
    assert (result.gold_pairs, result.run_pairs, result.units) == (3, 3, 2)


def test_a_run_without_any_assignment_has_no_precision():
    # Ohne eine einzige Zuordnung des Laufs gibt es nichts, dessen Richtigkeit
    # sich messen liesse - Precision ist unbekannt, Recall ist 0.
    gold = indexed(outcome("a", ("x", "4")))
    run = indexed(outcome("a"))
    result = metrics.pair_metrics(gold, run)
    assert result.precision is None
    assert result.false_discovery_rate is None
    assert result.recall == 0.0
    assert result.f1 is None


def test_a_completely_empty_comparison_stays_unknown():
    gold = indexed(outcome("a"))
    run = indexed(outcome("a"))
    result = metrics.pair_metrics(gold, run)
    assert result.precision is None and result.recall is None and result.f1 is None
    assert result.units == 1


def test_only_units_both_sides_know_are_compared():
    gold = indexed(outcome("a", ("x", "4")), outcome("nur-gold", ("y", "4")))
    run = indexed(outcome("a", ("x", "4")), outcome("nur-lauf", ("z", "4")))
    result = metrics.pair_metrics(gold, run)
    assert result.units == 1
    assert (result.true_positives, result.false_positives) == (1, 0)


# ---------------------------------------------------------------------------
# Entscheidung
# ---------------------------------------------------------------------------

def test_both_directions_of_a_wrong_decision_are_reported_separately():
    # a: beide mapped -> Treffer; b: gold mapped, Lauf leer -> verpasst;
    # c: gold leer, Lauf mapped -> erfunden; d: beide leer -> Treffer.
    gold = indexed(outcome("a", ("x", "4")), outcome("b", ("y", "4")),
                   outcome("c"), outcome("d"))
    run = indexed(outcome("a", ("x", "4")), outcome("b"),
                  outcome("c", ("z", "2")), outcome("d"))
    result = metrics.decision_metrics(gold, run)
    assert result.agreements == 2
    assert result.accuracy == 0.5
    assert result.missed_mappings == 1
    assert result.spurious_mappings == 1


def test_decision_accuracy_without_units_is_unknown():
    assert metrics.decision_metrics({}, {}).accuracy is None


# ---------------------------------------------------------------------------
# Stabilitaet
# ---------------------------------------------------------------------------

def test_a_single_repetition_says_nothing_about_stability():
    run = indexed(outcome("a", ("x", "4")))
    result = metrics.stability_metrics([run])
    assert result.repetitions == 1
    assert result.identical_share is None
    assert result.mean_jaccard is None


def test_stability_counts_identical_units_and_averages_the_overlap():
    # Unit a: dreimal {x}          -> identisch, Jaccard 1, 1, 1
    # Unit b: {x,y}, {x}, {x}      -> nicht identisch,
    #         Jaccard 1/2, 1/2, 1
    # Anteil identisch 1/2; mittlerer Jaccard (3 + 2) / 6 = 5/6
    first = indexed(outcome("a", ("x", "4")), outcome("b", ("x", "4"), ("y", "3")))
    second = indexed(outcome("a", ("x", "4")), outcome("b", ("x", "4")))
    third = indexed(outcome("a", ("x", "4")), outcome("b", ("x", "4")))
    result = metrics.stability_metrics([first, second, third])
    assert result.repetitions == 3
    assert result.identical_units == 1
    assert result.identical_share == 0.5
    assert result.mean_jaccard == pytest.approx(5 / 6)


def test_two_empty_results_count_as_the_most_stable_case():
    assert metrics.jaccard(frozenset(), frozenset()) == 1.0
    empty = indexed(outcome("a"))
    result = metrics.stability_metrics([empty, empty])
    assert result.identical_share == 1.0
    assert result.mean_jaccard == 1.0


def test_stability_only_uses_units_every_repetition_delivered():
    first = indexed(outcome("a", ("x", "4")), outcome("b", ("y", "4")))
    second = indexed(outcome("a", ("x", "4")))
    assert metrics.stability_metrics([first, second]).units == 1


def test_stability_without_any_run_is_unknown():
    result = metrics.stability_metrics([])
    assert result.units == 0 and result.identical_share is None


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

def test_confidence_is_measured_per_level():
    # Stufe 4: zwei Zuordnungen, eine richtig -> 0.5
    # Stufe 2: eine Zuordnung, falsch         -> 0.0
    # ohne:    eine Zuordnung, richtig        -> 1.0
    gold = indexed(outcome("a", ("x", "4"), ("w", "4")))
    run = indexed(outcome("a", ("x", "4"), ("y", "4"), ("z", "2"), ("w", "")))
    result = metrics.confidence_metrics(gold, run)
    assert result.distribution == {"2": 1, "4": 2, "ohne": 1}
    assert result.precision["4"] == 0.5
    assert result.precision["2"] == 0.0
    assert result.precision["ohne"] == 1.0


def test_a_run_without_assignments_has_an_empty_confidence_profile():
    result = metrics.confidence_metrics(indexed(outcome("a", ("x", "4"))),
                                        indexed(outcome("a")))
    assert result.distribution == {}
    assert result.precision == {}


# ---------------------------------------------------------------------------
# Broad Scope
# ---------------------------------------------------------------------------

def test_more_than_two_assignments_without_an_exception_is_a_violation():
    run = indexed(outcome("a", ("x", "4"), ("y", "4"), ("z", "4")))
    result = metrics.broad_scope_metrics(run)
    assert result.units_over_limit == 1
    assert result.without_exception == 1
    assert result.exception_below_threshold == 0


def test_an_exception_with_weak_confidence_is_a_violation():
    run = indexed(outcome("a", ("x", "4"), ("y", "4"), ("z", "2"),
                          broad_scope_exception=True))
    result = metrics.broad_scope_metrics(run)
    assert result.units_over_limit == 1
    assert result.without_exception == 0
    assert result.exception_below_threshold == 1


def test_a_missing_confidence_does_not_carry_an_exception():
    run = indexed(outcome("a", ("x", "4"), ("y", "4"), ("z", ""),
                          broad_scope_exception=True))
    assert metrics.broad_scope_metrics(run).exception_below_threshold == 1


def test_a_valid_exception_is_no_violation():
    run = indexed(outcome("a", ("x", "4"), ("y", "3"), ("z", "3"),
                          broad_scope_exception=True))
    result = metrics.broad_scope_metrics(run)
    assert result.units_over_limit == 1
    assert (result.without_exception, result.exception_below_threshold) == (0, 0)


def test_two_assignments_are_never_a_broad_scope_case():
    run = indexed(outcome("a", ("x", "4"), ("y", "4")))
    assert metrics.broad_scope_metrics(run).units_over_limit == 0


# ---------------------------------------------------------------------------
# Kosten und Gesamtergebnis
# ---------------------------------------------------------------------------

def test_cost_per_unit_needs_units():
    assert metrics.run_cost(spent_usd=1.0, calls=2, unknown_calls=0,
                            duration_s=10.0, units=0).usd_per_unit is None
    assert metrics.run_cost(spent_usd=1.0, calls=2, unknown_calls=0,
                            duration_s=10.0, units=4).usd_per_unit == 0.25


def test_a_variant_measures_precision_on_the_first_repetition():
    # Die zweite Wiederholung ist schlechter; sie darf die Primaermetrik nicht
    # verwaessern, denn im Betrieb laeuft nur eine.
    gold = indexed(outcome("a", ("x", "4")))
    first = indexed(outcome("a", ("x", "4")))
    second = indexed(outcome("a", ("y", "4")))
    result = metrics.variant_metrics(batch_size=3, gold=gold, runs=[first, second])
    assert result.pairs.precision == 1.0
    assert result.repetitions == 2
    assert result.stability.identical_share == 0.0
    assert result.stability.mean_jaccard == 0.0
    assert result.batch_size == 3


def test_a_variant_names_the_units_that_did_not_line_up():
    gold = indexed(outcome("a", ("x", "4")), outcome("b", ("y", "4")))
    run = indexed(outcome("a", ("x", "4")), outcome("c", ("z", "4")))
    result = metrics.variant_metrics(batch_size=1, gold=gold, runs=[run])
    assert result.missing_in_run == ("b",)
    assert result.unexpected_in_run == ("c",)


def test_a_variant_without_any_run_stays_unknown():
    result = metrics.variant_metrics(batch_size=1, gold=indexed(outcome("a")),
                                     runs=[])
    assert result.pairs.units == 0
    assert result.pairs.precision is None
    assert result.decisions.accuracy is None
    assert result.stability.identical_share is None
    assert result.missing_in_run == ("a",)
