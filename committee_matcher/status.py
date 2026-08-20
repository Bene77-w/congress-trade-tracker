"""Zustaende, Aufzaehlungen und Exit-Codes des Committee Matchers V2.

Alle Werte sind Fachvertrag aus Abschnitt 2.2, 2.3, 2.5, 2.6 und 2.7 des
Implementierungsplans. Sie sind bewusst an einer Stelle definiert, damit
Workflow, Artefakte, CLI und Tests dieselbe Menge verwenden.

Workflowstatus und fachlicher Unitstatus werden getrennt gefuehrt:
``WorkflowState`` beschreibt den Lauf, ``UnitStatus`` das Ergebnis einer Unit.
``WorkflowState.OUTCOME_UNKNOWN`` ist niemals ein Mappingurteil.
"""
from __future__ import annotations

from enum import Enum, IntEnum


class ExitCode(IntEnum):
    """Verbindliche Exit-Codes (Plan 2.7).

    ``1`` bleibt unbelegt: ein nicht abgefangener Python-Fehler wuerde sonst
    wie ein fachlicher Zustand aussehen.
    """

    SUCCESS = 0
    SUCCESS_WITH_WARNINGS = 2
    FAILED_MAPPING_UNITS = 3
    PREFLIGHT_BLOCKED = 4
    AWAITING_TAXONOMY_APPROVAL = 5
    COST_BLOCKED = 6
    REVIEW_IMPORT_REJECTED = 7
    OUTCOME_UNKNOWN = 8
    PROVIDER_ERROR = 9
    INTERNAL_ERROR = 10
    USER_ABORT = 130


#: Reihenfolge fuer den Fall, dass mehrere Blocker unterschiedliche Exit-Codes
#: verlangen: der erste Treffer gewinnt. Interne Fehler und Providerprobleme
#: schlagen fachliche Zustaende, weil sie den Lauf grundsaetzlich entwerten.
EXIT_CODE_PRIORITY: tuple[ExitCode, ...] = (
    ExitCode.INTERNAL_ERROR,
    ExitCode.USER_ABORT,
    ExitCode.PROVIDER_ERROR,
    ExitCode.OUTCOME_UNKNOWN,
    ExitCode.COST_BLOCKED,
    ExitCode.REVIEW_IMPORT_REJECTED,
    ExitCode.PREFLIGHT_BLOCKED,
    ExitCode.AWAITING_TAXONOMY_APPROVAL,
    ExitCode.FAILED_MAPPING_UNITS,
    ExitCode.SUCCESS_WITH_WARNINGS,
    ExitCode.SUCCESS,
)


class WorkflowState(str, Enum):
    """Laufzustand eines Runs (Plan 2.6)."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_TAXONOMY_APPROVAL = "awaiting_taxonomy_approval"
    AWAITING_DEVELOPMENT_GOLDSET = "awaiting_development_goldset"
    AWAITING_HOLDOUT = "awaiting_holdout"
    AWAITING_MANUAL_REVIEW = "awaiting_manual_review"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    COMPLETED = "completed"


#: Zustaende, die auf eine bewusste Entscheidung von Bene warten. Sie sind
#: erwartete Uebergaben und kein Fehlerfall.
HUMAN_HANDOFF_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.AWAITING_TAXONOMY_APPROVAL,
        WorkflowState.AWAITING_DEVELOPMENT_GOLDSET,
        WorkflowState.AWAITING_HOLDOUT,
        WorkflowState.AWAITING_MANUAL_REVIEW,
    }
)

#: Zustaende, aus denen derselbe Run nicht mehr von allein weiterlaeuft.
TERMINAL_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.FAILED,
        WorkflowState.OUTCOME_UNKNOWN,
        WorkflowState.COMPLETED,
    }
)

#: Exit-Code, mit dem ein Lauf den jeweiligen Endzustand meldet.
EXIT_CODE_FOR_STATE: dict[WorkflowState, ExitCode] = {
    WorkflowState.PENDING: ExitCode.SUCCESS,
    WorkflowState.RUNNING: ExitCode.INTERNAL_ERROR,
    WorkflowState.AWAITING_TAXONOMY_APPROVAL: ExitCode.AWAITING_TAXONOMY_APPROVAL,
    WorkflowState.AWAITING_DEVELOPMENT_GOLDSET: ExitCode.SUCCESS,
    WorkflowState.AWAITING_HOLDOUT: ExitCode.SUCCESS,
    WorkflowState.AWAITING_MANUAL_REVIEW: ExitCode.SUCCESS,
    WorkflowState.FAILED: ExitCode.FAILED_MAPPING_UNITS,
    WorkflowState.OUTCOME_UNKNOWN: ExitCode.OUTCOME_UNKNOWN,
    WorkflowState.COMPLETED: ExitCode.SUCCESS,
}


class UnitStatus(str, Enum):
    """Fachlicher Status einer Unit in der Unit-Coverage (Plan 2.8)."""

    NOT_DECIDED = "not_decided"
    MAPPED = "mapped"
    NO_MAPPING = "no_mapping"
    FAILED = "failed"


class MappingDecision(str, Enum):
    """Modell- bzw. Reviewurteil einer Unit (Plan 2.5)."""

    MAPPED = "mapped"
    NO_MAPPING = "no_mapping"


class UnitKind(str, Enum):
    """Unittypen (Plan 2.2)."""

    SUBCOMMITTEE = "subcommittee"
    COMMITTEE_PARENT_CORE = "committee_parent_core"
    COMMITTEE_DIRECT = "committee_direct"


class InputBasis(str, Enum):
    """Evidenzbasis einer Unit (Inputvertrag ``unit_input_basis``)."""

    JURISDICTION_TEXT = "jurisdiction_text"
    NAME_ONLY = "name_only"


class CorpusRole(str, Enum):
    """Rolle einer Datei unter dem Inputroot (Plan 2.1)."""

    MAPPING_INPUT = "mapping_input"
    SOURCE_ONLY = "source_only"
    RAW_SOURCE = "raw_source"
    ADMINISTRATIVE_EXCLUDED = "administrative_excluded"


class CoverageStatus(str, Enum):
    """Roster-Coverage-Status (Plan 2.3)."""

    MAPPED = "mapped"
    EXCLUDED = "excluded"
    MISSING = "missing"


class MatchBasis(str, Enum):
    """Wie eine Rosterzeile ihrer Unit zugeordnet wurde (Plan 2.3)."""

    EXACT = "exact"
    ALIAS = "alias"
    MANUAL_EXCLUSION = "manual_exclusion"


class AssignmentSource(str, Enum):
    """Effektive Provenienz einer Zuordnung (Plan 2.8)."""

    MODEL = "model"
    MANUAL = "manual"


#: Unitstatus, die ein abgeschlossenes fachliches Urteil darstellen.
DECIDED_UNIT_STATUS: frozenset[UnitStatus] = frozenset(
    {UnitStatus.MAPPED, UnitStatus.NO_MAPPING}
)


def is_mapping_verdict(state: WorkflowState) -> bool:
    """Sagt, ob ``state`` ein fachliches Mappingurteil erlaubt.

    ``outcome_unknown`` ist ausdruecklich kein Urteil: der Call kann beim
    Provider erfolgreich gewesen sein, das Ergebnis ist aber unbekannt.
    """
    return state is WorkflowState.COMPLETED


def exit_code_for_state(state: WorkflowState) -> ExitCode:
    """Exit-Code eines Endzustands; unbekannte Zustaende gelten als intern."""
    return EXIT_CODE_FOR_STATE.get(state, ExitCode.INTERNAL_ERROR)


def worst_exit_code(codes: "list[ExitCode] | tuple[ExitCode, ...]") -> ExitCode:
    """Waehlt aus mehreren Exit-Codes den nach ``EXIT_CODE_PRIORITY`` ersten."""
    if not codes:
        return ExitCode.SUCCESS
    ranked = {code: index for index, code in enumerate(EXIT_CODE_PRIORITY)}
    return min(codes, key=lambda code: ranked.get(code, len(ranked)))
