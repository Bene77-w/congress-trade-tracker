"""Pydantic-Datenvertraege des Committee Matchers V2.

Abgebildet sind die Vertraege aus Plan 2.1 bis 2.5. Alle Modelle sind
``frozen`` und ``extra="forbid"``: unbekannte Felder werden abgelehnt, statt
still mitgeschleppt zu werden. Das ist gleichzeitig die letzte Sperre gegen
Market-Cap-/Weight-Felder, die yfinance mitliefert (Plan 2.4) - der Adapter
verwirft sie am Rand, ``IndustryRecord`` wuerde sie zusaetzlich zurueckweisen.

Bewusst nicht hier: die kontextabhaengigen Mappingregeln (Unittyp-Obergrenze,
Existenz der Industry-ID im Snapshot, Evidence-Substring). Sie brauchen Unit-
und Snapshotkontext und gehoeren nach ``mapping_engine`` (P6). Modelle pruefen
nur, was aus dem Objekt selbst entscheidbar ist.

Ebenfalls nicht hier: Long-CSV- und Review-Workbook-Schema. Deren Feldliste
wird erst mit den Artefakten in P7/P8 eingefroren; sie jetzt zu raten wuerde
einen Vertrag vortaeuschen.
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .status import (
    CorpusRole,
    CoverageStatus,
    InputBasis,
    MappingDecision,
    MatchBasis,
    UnitKind,
    UnitStatus,
)

#: Maximale Zuordnungen je Unit; darueber ist das Ergebnis immer ungueltig.
MAX_ASSIGNMENTS = 5
#: Obergrenze ohne Broad-Scope-Ausnahme (Normalfall 1-2).
MAX_ASSIGNMENTS_REGULAR = 2
#: Mindestconfidence, wenn die Broad-Scope-Ausnahme greift.
BROAD_SCOPE_MIN_CONFIDENCE = 3
#: Units je Mappingcall (Plan 2.5).
MAX_UNITS_PER_BATCH = 3

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

NonEmptyStr = Annotated[str, Field(min_length=1)]
#: Zahlen- und Wahrheitsfelder des Fachvertrags werden strikt gelesen: ohne
#: ``strict`` akzeptiert Pydantic ``True`` als ``1`` und ``0`` als ``False``,
#: womit ein ungueltiges Modellergebnis die formale Pruefung passieren wuerde.
Confidence = Annotated[int, Field(ge=1, le=4, strict=True)]
StrictFlag = Annotated[bool, Field(strict=True)]
ByteCount = Annotated[int, Field(ge=0, strict=True)]


class MatcherModel(BaseModel):
    """Basismodell: unveraenderlich, keine unbekannten Felder."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CorpusFileEntry(MatcherModel):
    """Eine Datei des Inputcorpus mit Rolle und Hash (Plan 2.1)."""

    path: NonEmptyStr
    role: CorpusRole
    bytes: ByteCount
    sha256: NonEmptyStr

    @field_validator("sha256")
    @classmethod
    def _hex_digest(cls, value: str) -> str:
        if not SHA256_RE.match(value):
            raise ValueError("sha256 muss 64 hexadezimale Kleinbuchstaben sein")
        return value

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("path muss projektrelativ und traversalfrei sein")
        return value


class UnitInputBasisSpec(MatcherModel):
    """Frontmatterblock ``unit_input_basis`` einer Mapping-Markdown."""

    committee: InputBasis = InputBasis.JURISDICTION_TEXT
    subcommittees: dict[str, InputBasis] = Field(default_factory=dict)

    @field_validator("committee")
    @classmethod
    def _committee_basis(cls, value: InputBasis) -> InputBasis:
        if value is not InputBasis.JURISDICTION_TEXT:
            raise ValueError(
                "committee-Basis ist im Inputvertrag immer jurisdiction_text"
            )
        return value


class MatcherUnit(MatcherModel):
    """Eine Mappingunit (Plan 2.2).

    ``unit_id`` und ``mapping_input_hash`` werden in P3 kanonisch erzeugt;
    hier wird nur die Form gesichert. ``source_bytes_sha256`` haelt den Hash
    der exakten Quelldateibytes getrennt vom fachlichen Inputhash.
    """

    unit_id: NonEmptyStr
    kind: UnitKind
    chamber: NonEmptyStr
    committee_name: NonEmptyStr
    unit_name: NonEmptyStr
    input_basis: InputBasis
    source_path: NonEmptyStr
    #: Erlaubter Evidenzkontext dieser Unit nach ``normalize_text``. Bei
    #: ``name_only`` bleibt er leer: dann darf nur der Unitname belegen.
    evidence_text: str = ""
    parent_unit_id: str | None = None
    mapping_input_hash: str | None = None
    source_bytes_sha256: str | None = None

    @field_validator("unit_id")
    @classmethod
    def _id_form(cls, value: str) -> str:
        if value != value.strip() or any(ch.isspace() for ch in value):
            raise ValueError("unit_id enthaelt Whitespace")
        return value

    @field_validator("mapping_input_hash", "source_bytes_sha256")
    @classmethod
    def _optional_digest(cls, value: str | None) -> str | None:
        if value is not None and not SHA256_RE.match(value):
            raise ValueError("Hash muss 64 hexadezimale Kleinbuchstaben sein")
        return value

    @model_validator(mode="after")
    def _kind_consistency(self) -> "MatcherUnit":
        if self.kind is UnitKind.SUBCOMMITTEE and not self.parent_unit_id:
            raise ValueError("subcommittee benoetigt parent_unit_id")
        if self.kind is not UnitKind.SUBCOMMITTEE and self.parent_unit_id:
            raise ValueError("nur subcommittee traegt parent_unit_id")
        if self.kind is not UnitKind.SUBCOMMITTEE and self.input_basis is InputBasis.NAME_ONLY:
            raise ValueError("name_only ist nur fuer Subcommittees zulaessig")
        return self


class RosterCoverageRow(MatcherModel):
    """Eine Zeile der Roster-Coverage (Plan 2.3).

    Roster pruefen ausschliesslich Coverage und beeinflussen keine fachliche
    Mappingentscheidung.
    """

    chamber: NonEmptyStr
    committee_name: NonEmptyStr
    unit_name: NonEmptyStr
    coverage_status: CoverageStatus
    unit_id: str | None = None
    match_basis: MatchBasis | None = None
    hint: str | None = None
    fuzzy_suggestion: str | None = None

    @model_validator(mode="after")
    def _status_rules(self) -> "RosterCoverageRow":
        if self.coverage_status is CoverageStatus.MAPPED:
            if not self.unit_id:
                raise ValueError("mapped benoetigt eine unit_id")
            if self.match_basis not in (MatchBasis.EXACT, MatchBasis.ALIAS):
                raise ValueError("mapped verlangt match_basis exact oder alias")
        elif self.coverage_status is CoverageStatus.EXCLUDED:
            if self.unit_id is not None:
                raise ValueError("excluded verlangt unit_id null")
            if self.match_basis is not MatchBasis.MANUAL_EXCLUSION:
                raise ValueError("excluded verlangt match_basis manual_exclusion")
        else:
            if self.unit_id is not None or self.match_basis is not None:
                raise ValueError("missing verlangt unit_id und match_basis null")
            if not (self.hint or "").strip():
                raise ValueError("missing verlangt einen Handlungshinweis")
        return self


class IndustryRecord(MatcherModel):
    """Eine Industry des yfinance-Snapshots (Plan 2.4).

    Persistiert werden ausschliesslich Identitaet und Beschreibung. Market-Cap-
    und Weight-Felder werden am Adapterrand verworfen; ``extra="forbid"`` laesst
    sie hier auch nicht nachtraeglich herein.
    """

    industry_id: NonEmptyStr
    industry_name: NonEmptyStr
    sector_id: NonEmptyStr
    sector_name: NonEmptyStr
    description: NonEmptyStr


class TaxonomySnapshot(MatcherModel):
    """Eingefrorener Taxonomiestand eines Runs (Plan 2.4)."""

    snapshot_id: NonEmptyStr
    yfinance_version: NonEmptyStr
    taxonomy_content_hash: NonEmptyStr
    industries: tuple[IndustryRecord, ...]

    @field_validator("taxonomy_content_hash")
    @classmethod
    def _digest(cls, value: str) -> str:
        if not SHA256_RE.match(value):
            raise ValueError("taxonomy_content_hash muss ein SHA-256-Hexdigest sein")
        return value

    @field_validator("industries")
    @classmethod
    def _non_empty(cls, value: tuple[IndustryRecord, ...]) -> tuple[IndustryRecord, ...]:
        if not value:
            raise ValueError("Snapshot ohne Industries ist ungueltig")
        ids = [item.industry_id for item in value]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"doppelte industry_id: {', '.join(duplicates)}")
        return value

    def industry_ids(self) -> frozenset[str]:
        return frozenset(item.industry_id for item in self.industries)


class Assignment(MatcherModel):
    """Eine Unit-Industry-Zuordnung (Plan 2.5)."""

    industry_id: NonEmptyStr
    confidence: Confidence
    evidence_excerpt: NonEmptyStr
    rationale_short: NonEmptyStr

    @field_validator("evidence_excerpt", "rationale_short")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Feld darf nicht nur aus Whitespace bestehen")
        return value


class MappingUnitResult(MatcherModel):
    """Modell- oder Reviewergebnis einer Unit (Plan 2.5).

    Geprueft werden nur die aus dem Objekt selbst entscheidbaren Regeln. Die
    unittypabhaengige Obergrenze (``committee_parent_core`` und ``name_only``
    bleiben bei zwei) sowie Snapshot- und Evidence-Pruefungen laufen in P6
    gegen Unit und Snapshot.
    """

    unit_id: NonEmptyStr
    decision: MappingDecision
    no_mapping_reason: str | None = None
    broad_scope_exception: StrictFlag = False
    broad_scope_reason: str | None = None
    assignments: tuple[Assignment, ...] = ()

    @model_validator(mode="after")
    def _contract(self) -> "MappingUnitResult":
        count = len(self.assignments)
        if count > MAX_ASSIGNMENTS:
            raise ValueError(
                f"mehr als {MAX_ASSIGNMENTS} Zuordnungen sind immer ungueltig"
            )

        ids = [a.industry_id for a in self.assignments]
        if len(set(ids)) != len(ids):
            raise ValueError("industry_id darf je Unit nur einmal vorkommen")

        if self.decision is MappingDecision.NO_MAPPING:
            if count:
                raise ValueError("no_mapping besitzt keine Zuordnungen")
            if not (self.no_mapping_reason or "").strip():
                raise ValueError("no_mapping verlangt no_mapping_reason")
            if self.broad_scope_exception or self.broad_scope_reason:
                raise ValueError("no_mapping besitzt keine Broad-Scope-Ausnahme")
            return self

        if count == 0:
            raise ValueError("mapped verlangt mindestens eine Zuordnung")
        if self.no_mapping_reason is not None:
            raise ValueError("mapped besitzt keinen no_mapping_reason")

        if count > MAX_ASSIGNMENTS_REGULAR:
            if not self.broad_scope_exception:
                raise ValueError(
                    f"mehr als {MAX_ASSIGNMENTS_REGULAR} Zuordnungen verlangen "
                    "broad_scope_exception"
                )
            if not (self.broad_scope_reason or "").strip():
                raise ValueError("broad_scope_exception verlangt broad_scope_reason")
            weak = [a.industry_id for a in self.assignments
                    if a.confidence < BROAD_SCOPE_MIN_CONFIDENCE]
            if weak:
                raise ValueError(
                    "Broad-Scope-Ausnahme verlangt Confidence >= "
                    f"{BROAD_SCOPE_MIN_CONFIDENCE}: {', '.join(sorted(weak))}"
                )
            evidences = [a.evidence_excerpt.strip() for a in self.assignments]
            if len(set(evidences)) != len(evidences):
                raise ValueError("Broad-Scope-Ausnahme verlangt eigene Evidence je Industry")
            rationales = [a.rationale_short.strip() for a in self.assignments]
            if len(set(rationales)) != len(rationales):
                raise ValueError("Broad-Scope-Ausnahme verlangt eigene Rationale je Industry")
        elif self.broad_scope_exception or self.broad_scope_reason:
            raise ValueError(
                "Broad-Scope-Ausnahme ist erst ab "
                f"{MAX_ASSIGNMENTS_REGULAR + 1} Zuordnungen zulaessig"
            )
        return self


class MappingBatchResult(MatcherModel):
    """Antwort eines Mappingcalls ueber 1 bis 3 Units (Plan 2.5)."""

    requested_batch_id: NonEmptyStr
    units: tuple[MappingUnitResult, ...]

    @model_validator(mode="after")
    def _batch_rules(self) -> "MappingBatchResult":
        if not 1 <= len(self.units) <= MAX_UNITS_PER_BATCH:
            raise ValueError(f"ein Batch enthaelt 1 bis {MAX_UNITS_PER_BATCH} Units")
        ids = [unit.unit_id for unit in self.units]
        if len(set(ids)) != len(ids):
            raise ValueError("jede angeforderte Unit erscheint genau einmal")
        return self

    def unit_ids(self) -> frozenset[str]:
        return frozenset(unit.unit_id for unit in self.units)


class UnitCoverageRow(MatcherModel):
    """Eine Zeile der Unit-Coverage (Plan 2.8)."""

    unit_id: NonEmptyStr
    status: UnitStatus
    revision_id: str | None = None
    note: str | None = None
