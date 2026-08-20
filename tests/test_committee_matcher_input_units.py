"""Tests fuer P3 des Committee Matchers V2: Parser, Units, Roster-Coverage.

Gepinnt werden die Vertraege aus Plan 2.1 bis 2.3 sowie die beiden
versionierten Kanonisierungen ``unit-id-v1`` und ``mapping-input-v1``
einschliesslich Golden-Hashes. Aenderungen an der Kanonisierung muessen diese
Tests brechen - genau dafuer sind sie da.

Alle Tests sind netzwerkfrei. Der echte Corpus wird ausschliesslich read-only
geprueft; jeder schreibende Fall laeuft auf einem synthetischen Corpus unter
``tmp_path``.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from committee_matcher import input_corpus, paths, roster_coverage, units  # noqa: E402
from committee_matcher.errors import ErrorCollector, Severity  # noqa: E402
from committee_matcher.settings_view import load_settings  # noqa: E402
from committee_matcher.status import (  # noqa: E402
    CoverageStatus,
    InputBasis,
    MatchBasis,
    UnitKind,
)

OFFLINE = load_settings().offline


def codes(collector: ErrorCollector, severity: Severity | None = None) -> set[str]:
    return {
        f.code
        for f in collector.findings
        if severity is None or f.severity is severity
    }


# ---------------------------------------------------------------------------
# Kanonisierung unit-id-v1 (Golden)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,slug",
    [
        ("Committee on Ways and Means", "ways-and-means"),
        ("Subcommittee on Social Security", "social-security"),
        ("Permanent Subcommittee on Investigations", "investigations"),
        ("Permanent Select Committee on Intelligence", "intelligence"),
        ("Joint Committee of Congress on the Library", "library"),
        ("Committee on the Judiciary", "judiciary"),
        ("Committee on Veterans' Affairs", "veterans-affairs"),
        ("Committee on Science, Space, and Technology", "science-space-and-technology"),
        ("Health, Education, Labor & Pensions", "health-education-labor-and-pensions"),
        ("Subcommittee on   Energy – Natural Resources", "energy-natural-resources"),
        ("Ausschuss für Äußeres", "ausschuss-fur-ausseres"),
        ("Subcommittee on Taxation and IRS Oversight", "taxation-and-irs-oversight"),
    ],
)
def test_slugify_is_frozen(name, slug):
    assert units.slugify(name) == slug


def test_slug_strips_repeated_office_prefixes():
    assert units.strip_office_prefix("Committee on the Budget") == "Budget"
    assert units.slugify("Subcommittee on Committee on Oversight") == "oversight"


def test_match_key_keeps_word_boundaries():
    assert units.match_key("Committee on Ways And Means") == "ways and means"
    assert units.match_key("Ways and Means") == units.match_key("WAYS AND MEANS")


def test_unit_id_composition_is_frozen():
    assert units.committee_unit_id("house", "Committee on Agriculture") == "house--agriculture"
    assert (
        units.subcommittee_unit_id("senate", "Committee on Finance", "Subcommittee on Health Care")
        == "senate--finance--health-care"
    )


def test_unit_id_separator_cannot_appear_inside_a_slug():
    assert units.ID_SEPARATOR not in units.slugify("Ways -- and -- Means")


def test_normalize_text_collapses_whitespace_and_normalizes_unicode():
    assert input_corpus.normalize_text("  a\r\n\r\n  b\t c  ") == "a b c"
    assert input_corpus.normalize_text("ﬁ") == "fi"


def test_mapping_input_hash_is_frozen():
    """Golden-Hash: aendert sich die Serialisierung, bricht der Cachevertrag."""
    digest = units.mapping_input_hash(
        unit_id="house--agriculture--forestry",
        kind=UnitKind.SUBCOMMITTEE,
        chamber="house",
        committee_name="Committee on Agriculture",
        unit_name="Subcommittee on Forestry",
        input_basis=InputBasis.JURISDICTION_TEXT,
        evidence_text="Forestry   and\nhorticulture.",
        parent_committee_name="Committee on Agriculture",
        parent_context_text="Landwirtschaft   insgesamt.",
    )
    payload = {
        "version": "mapping-input-v1",
        "unit_id": "house--agriculture--forestry",
        "kind": "subcommittee",
        "chamber": "house",
        "committee_name": "Committee on Agriculture",
        "unit_name": "Subcommittee on Forestry",
        "input_basis": "jurisdiction_text",
        "evidence_text": "Forestry and horticulture.",
        "parent_committee_name": "Committee on Agriculture",
        "parent_context_text": "Landwirtschaft insgesamt.",
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert digest == expected


def test_mapping_input_hash_ignores_cosmetic_whitespace():
    def digest(text: str) -> str:
        return units.mapping_input_hash(
            unit_id="u", kind=UnitKind.COMMITTEE_DIRECT, chamber="joint",
            committee_name="C", unit_name="C",
            input_basis=InputBasis.JURISDICTION_TEXT,
            evidence_text=text, parent_committee_name=None,
        )

    assert digest("a b") == digest("a\n\n   b\r\n")
    assert digest("a b") != digest("a c")


def test_mapping_input_hash_separates_units_and_basis():
    common = dict(
        kind=UnitKind.SUBCOMMITTEE, chamber="house", committee_name="C",
        unit_name="S", evidence_text="text", parent_committee_name="C",
    )
    a = units.mapping_input_hash(unit_id="a", input_basis=InputBasis.JURISDICTION_TEXT, **common)
    b = units.mapping_input_hash(unit_id="b", input_basis=InputBasis.JURISDICTION_TEXT, **common)
    c = units.mapping_input_hash(unit_id="a", input_basis=InputBasis.NAME_ONLY, **common)
    assert len({a, b, c}) == 3


# ---------------------------------------------------------------------------
# Echter Corpus (read-only)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_corpus():
    collector = ErrorCollector()
    result = input_corpus.load_documents(collector, OFFLINE)
    built = units.build_units(collector, result.documents)
    return collector, result, built


def test_real_corpus_parses_without_blockers(real_corpus):
    collector, result, _ = real_corpus
    assert not collector.has_blockers()
    assert result.manifest_files == 95
    assert len(result.documents) == 46
    assert sum(len(d.sections) for d in result.documents) == 179
    assert sum(1 for d in result.documents if not d.has_subcommittees) == 15


def test_real_corpus_yields_the_frozen_unit_set(real_corpus):
    _, _, built = real_corpus
    counts: dict[str, int] = {}
    for unit in built:
        counts[unit.kind.value] = counts.get(unit.kind.value, 0) + 1
    assert counts == {"subcommittee": 179, "committee_parent_core": 31, "committee_direct": 15}
    assert len(built) == 225
    assert len({u.unit_id for u in built}) == 225


def test_unit_ids_match_the_frozen_baseline_exactly(real_corpus):
    _, _, built = real_corpus
    collector = ErrorCollector()
    assert units.check_against_baseline(collector, built) is True
    assert collector.findings == []


def test_frozen_baseline_file_carries_its_versions():
    data = yaml.safe_load(units.UNIT_ID_BASELINE_PATH.read_text(encoding="utf-8"))
    assert data["canonicalization_version"] == units.UNIT_ID_VERSION
    assert data["mapping_input_version"] == units.MAPPING_INPUT_VERSION
    assert data["unit_count"] == 225
    assert len(data["units"]) == 225


def test_mapping_input_hashes_are_unique_per_unit(real_corpus):
    _, _, built = real_corpus
    assert len({u.mapping_input_hash for u in built}) == len(built)


def test_name_only_units_carry_no_evidence_text(real_corpus):
    _, _, built = real_corpus
    name_only = [u for u in built if u.input_basis is InputBasis.NAME_ONLY]
    assert len(name_only) == 8
    assert all(u.evidence_text == "" for u in name_only)
    assert all(u.kind is UnitKind.SUBCOMMITTEE for u in name_only)


def test_parent_core_uses_its_own_text_and_never_child_content(real_corpus):
    """Parent-Core traegt genau die Committee-Praeambel - nie aggregierten Kindtext."""
    _, result, built = real_corpus
    by_source = {d.rel_path: d for d in result.documents}
    parents = [u for u in built if u.kind is UnitKind.COMMITTEE_PARENT_CORE]
    assert parents
    for unit in parents:
        document = by_source[unit.source_path]
        assert unit.evidence_text == input_corpus.normalize_text(document.preamble)
        for section in document.sections:
            text = input_corpus.normalize_text(section.text)
            if len(text) < 60:  # zu kurz fuer eine belastbare Substringaussage
                continue
            assert text not in unit.evidence_text


def test_short_jurisdiction_text_is_reported_as_a_warning(real_corpus):
    """Die zwei abgeschnittenen Appropriations-Abschnitte bleiben sichtbar."""
    collector, _, _ = real_corpus
    short = [f for f in collector.findings if f.code == "INP_EVIDENCE_TOO_SHORT"]
    assert {f.severity for f in short} == {Severity.WARNUNG}
    assert {f.obj for f in short} == {
        "md/house_appropriations.md#Subcommittee on Transportation, Housing and "
        "Urban Development, and Related Agencies",
        "md/senate_appropriations.md#Subcommittee on Department of Homeland Security",
    }


def test_every_subcommittee_points_at_its_parent(real_corpus):
    _, _, built = real_corpus
    ids = {u.unit_id for u in built}
    for unit in built:
        if unit.kind is UnitKind.SUBCOMMITTEE:
            assert unit.parent_unit_id in ids
        else:
            assert unit.parent_unit_id is None


def test_source_bytes_hash_is_separate_from_the_mapping_input_hash(real_corpus):
    _, _, built = real_corpus
    for unit in built:
        assert unit.source_bytes_sha256 is not None
        assert unit.source_bytes_sha256 != unit.mapping_input_hash


# ---------------------------------------------------------------------------
# Synthetischer Corpus (Negativpfade)
# ---------------------------------------------------------------------------

FRONTMATTER = """---
schema_version: 1
congress: 119
chamber: {chamber}
committee: {committee}
committee_type: standing
source_url: null
retrieved_at: null
source_metadata_status: pending
source_files:
  - {source}
unit_input_basis:
  committee: jurisdiction_text
  subcommittees:{basis}
---
"""


def make_markdown(*, chamber="house", committee="Committee on Testing",
                  sections=(), basis=None, body_extra="", comments="",
                  heading="Committee jurisdiction", preamble=("Zustaendig fuer Tests, Pruefverfahren und die "
                            "Kontrolle synthetischer Vorgaenge."),
                  source="data/committee_matcher_inputs/txt/house_testing.txt"):
    basis = basis if basis is not None else {name: "jurisdiction_text" for name in sections}
    if basis:
        rendered = "\n" + "\n".join(f'    "{k}": {v}' for k, v in basis.items())
    else:
        rendered = " {}"
    text = FRONTMATTER.format(
        chamber=chamber, committee=committee, source=source, basis=rendered
    )
    text += f"\n# {heading}\n\n{preamble}\n"
    for name in sections:
        text += (f"\n## {name}\n\nZustaendig fuer {name}, dessen Aufsicht "
                 f"sowie alle daran anschliessenden Pruefverfahren.\n")
    text += body_extra
    if comments:
        text += f"\n## {input_corpus.COMMENT_HEADING}\n\n{comments}\n"
    return text


def build_corpus(tmp_path: Path, files: dict[str, str], roles: dict[str, str] | None = None) -> Path:
    """Legt einen synthetischen Inputroot mit passendem Manifest an."""
    root = tmp_path / "inputs"
    (root / "md").mkdir(parents=True)
    (root / "txt").mkdir(parents=True)
    (root / "txt" / "house_testing.txt").write_text("Quelle.\n", encoding="utf-8")

    for name, text in files.items():
        (root / "md" / name).write_text(text, encoding="utf-8")

    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        default = "raw_source" if rel.startswith("txt/") else "mapping_input"
        entries.append(
            {
                "path": rel,
                "role": (roles or {}).get(rel, default),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (root / "corpus_manifest.yaml").write_text(
        yaml.safe_dump({"manifest_version": "corpus-manifest-v1", "files": entries},
                       sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return root


def load(root: Path, settings=OFFLINE):
    collector = ErrorCollector()
    result = input_corpus.load_documents(collector, settings, root)
    return collector, result


def test_synthetic_corpus_parses_and_builds_units(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown(sections=("Subcommittee on A", "Subcommittee on B"))})
    collector, result = load(root)
    assert collector.findings == []
    built = units.build_units(collector, result.documents)
    assert [u.unit_id for u in built] == [
        "house--testing", "house--testing--a", "house--testing--b"
    ]
    assert [u.kind for u in built] == [
        UnitKind.COMMITTEE_PARENT_CORE, UnitKind.SUBCOMMITTEE, UnitKind.SUBCOMMITTEE
    ]


def test_document_without_sections_becomes_committee_direct(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    collector, result = load(root)
    built = units.build_units(collector, result.documents)
    assert [u.kind for u in built] == [UnitKind.COMMITTEE_DIRECT]
    assert built[0].parent_unit_id is None


def test_unknown_file_in_the_input_root_blocks(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    (root / "md" / "geschmuggelt.md").write_text("x", encoding="utf-8")
    collector, _ = load(root)
    assert "INP_UNKNOWN_FILE" in codes(collector)


def test_content_drift_against_the_manifest_blocks(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    original = (root / "md" / "house_testing.md").read_text(encoding="utf-8")
    drifted = original.replace("Zustaendig fuer Tests", "Zustaendig fuer Texte", 1)
    assert len(drifted) == len(original)  # Groesse gleich -> nur der Hash entscheidet
    (root / "md" / "house_testing.md").write_text(drifted, encoding="utf-8")
    collector, result = load(root)
    assert "INP_MANIFEST_DRIFT" in codes(collector)
    assert result.documents == ()


def test_missing_manifest_blocks(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    (root / "corpus_manifest.yaml").unlink()
    collector, _ = load(root)
    assert "INP_MANIFEST_MISSING" in codes(collector)


def test_stale_manifest_entry_blocks(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    (root / "md" / "house_testing.md").unlink()
    collector, _ = load(root)
    assert "INP_MANIFEST_STALE" in codes(collector)


def test_foreign_instruction_outside_the_comment_section_blocks_with_line(tmp_path):
    text = make_markdown(sections=("Subcommittee on A",), body_extra="\nBitte #userinput beachten.\n")
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, _ = load(root)
    finding = next(f for f in collector.findings if f.code == "INP_FOREIGN_INSTRUCTION")
    assert finding.line and finding.line > 1
    assert "house_testing.md" in finding.obj


def test_foreign_instruction_inside_the_comment_section_is_allowed(tmp_path):
    text = make_markdown(comments="#userinput: bitte spaeter pruefen.")
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, result = load(root)
    assert collector.findings == []
    assert len(result.documents) == 1


def test_comment_section_is_never_a_unit(tmp_path):
    text = make_markdown(sections=("Subcommittee on A",), comments="Hinweis.")
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, result = load(root)
    built = units.build_units(collector, result.documents)
    assert all(input_corpus.COMMENT_HEADING not in u.unit_name for u in built)
    assert len(built) == 2


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"heading": "Falsche Ueberschrift"}, "INP_H1_CONTRACT"),
        ({"sections": ("Subcommittee on A", "Subcommittee on A")}, "INP_DUPLICATE_SECTION"),
        ({"chamber": "senate"}, "INP_CHAMBER_MISMATCH"),
        ({"preamble": "   "}, "INP_COMMITTEE_TEXT_MISSING"),
    ],
)
def test_structural_contract_violations_block(tmp_path, kwargs, code):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown(**kwargs)})
    collector, result = load(root)
    assert code in codes(collector)
    assert result.documents == ()


def test_section_after_the_comment_section_blocks(tmp_path):
    text = make_markdown(comments="Hinweis.") + "\n## Subcommittee on Spaet\n\nText.\n"
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, _ = load(root)
    assert "INP_SECTION_AFTER_COMMENTS" in codes(collector)


def test_missing_frontmatter_field_blocks(tmp_path):
    text = make_markdown().replace("committee_type: standing\n", "")
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, _ = load(root)
    assert "INP_FIELD_MISSING" in codes(collector)


def test_broken_frontmatter_blocks(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": "kein frontmatter\n"})
    collector, _ = load(root)
    assert "INP_NO_FRONTMATTER" in codes(collector)


def test_unclosed_frontmatter_blocks(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": "---\nchamber: house\n"})
    collector, _ = load(root)
    assert "INP_UNCLOSED_FRONTMATTER" in codes(collector)


def test_basis_key_mismatch_blocks(tmp_path):
    text = make_markdown(sections=("Subcommittee on A",), basis={"Subcommittee on B": "name_only"})
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, _ = load(root)
    assert "INP_BASIS_KEY_MISMATCH" in codes(collector)


def test_unknown_basis_value_blocks(tmp_path):
    text = make_markdown(sections=("Subcommittee on A",), basis={"Subcommittee on A": "erfunden"})
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, _ = load(root)
    assert "INP_BASIS_VALUE" in codes(collector)


def test_source_files_may_only_reference_sources(tmp_path):
    root = build_corpus(
        tmp_path,
        {
            "house_testing.md": make_markdown(source="data/committee_matcher_inputs/md/house_other.md"),
            "house_other.md": make_markdown(committee="Committee on Other"),
        },
    )
    collector, _ = load(root)
    assert {"INP_SOURCE_UNKNOWN", "INP_SOURCE_ROLE"} & codes(collector)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("data/committee_matcher_inputs/../../../etc/passwd", "PATH_TRAVERSAL"),
        ("../../../etc/passwd", "INP_SOURCE_OUTSIDE_ROOT"),
        ("/etc/passwd", "INP_SOURCE_OUTSIDE_ROOT"),
        ("data/committee_matcher_inputs/txt/fehlt.txt", "PATH_MISSING"),
    ],
)
def test_source_file_references_are_guarded(tmp_path, source, expected):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown(source=source)})
    collector, _ = load(root)
    assert expected in codes(collector)


def test_short_evidence_warning_is_configurable(tmp_path):
    from dataclasses import replace

    root = build_corpus(tmp_path, {"house_testing.md": make_markdown(preamble="Kurz.")})
    collector, _ = load(root)
    assert "INP_EVIDENCE_TOO_SHORT" in codes(collector, Severity.WARNUNG)
    assert not collector.has_blockers()

    collector, _ = load(root, replace(OFFLINE, min_evidence_chars=0))
    assert collector.findings == []


def test_file_size_limits_block(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    from dataclasses import replace

    collector, _ = load(root, replace(OFFLINE, max_file_bytes=10_000_000, max_frontmatter_bytes=200))
    assert "INP_FRONTMATTER_TOO_LARGE" in codes(collector)
    size = (root / "md" / "house_testing.md").stat().st_size
    collector, _ = load(root, replace(OFFLINE, max_file_bytes=size - 1))
    assert "INP_FILE_TOO_LARGE" in codes(collector)


def test_colliding_unit_ids_block_instead_of_being_renumbered(tmp_path):
    root = build_corpus(
        tmp_path,
        {
            "house_a.md": make_markdown(committee="Committee on Testing"),
            "house_b.md": make_markdown(committee="Committee on the Testing"),
        },
    )
    collector, result = load(root)
    built = units.build_units(collector, result.documents)
    assert "UNIT_ID_COLLISION" in codes(collector)
    assert len(built) == 1


# ---------------------------------------------------------------------------
# Eingefrorene Sollmenge
# ---------------------------------------------------------------------------

def _baseline_file(tmp_path: Path, ids: list[str], version: str | None = None,
                   unit_count: int | None = None) -> Path:
    path = tmp_path / "unit_ids.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "canonicalization_version": version or units.UNIT_ID_VERSION,
                "mapping_input_version": units.MAPPING_INPUT_VERSION,
                "unit_count": unit_count if unit_count is not None else len(ids),
                **({"unit_counts": {"committee_direct": len(ids)}} if ids else {}),
                "units": [{"unit_id": i, "kind": "committee_direct", "chamber": "house"} for i in ids],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _unit_list(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    collector, result = load(root)
    return units.build_units(collector, result.documents)


def test_baseline_set_equality_detects_both_directions(tmp_path):
    built = _unit_list(tmp_path)
    collector = ErrorCollector()
    assert units.check_against_baseline(
        collector, built, _baseline_file(tmp_path, ["house--testing", "house--fehlt"])
    ) is False
    assert "UNIT_MISSING" in codes(collector)

    collector = ErrorCollector()
    assert units.check_against_baseline(collector, built, _baseline_file(tmp_path, [])) is False
    assert "UNIT_UNEXPECTED" in codes(collector)


def test_baseline_version_and_absence_block(tmp_path):
    built = _unit_list(tmp_path)
    collector = ErrorCollector()
    units.check_against_baseline(
        collector, built, _baseline_file(tmp_path, ["house--testing"], version="unit-id-v0")
    )
    assert "UNIT_BASELINE_VERSION" in codes(collector)

    collector = ErrorCollector()
    units.check_against_baseline(collector, built, tmp_path / "fehlt.yaml")
    assert "UNIT_BASELINE_MISSING" in codes(collector)


def test_baseline_with_duplicate_ids_blocks(tmp_path):
    built = _unit_list(tmp_path)
    collector = ErrorCollector()
    units.check_against_baseline(
        collector, built,
        _baseline_file(tmp_path, ["house--testing", "house--testing"], unit_count=1),
    )
    assert "UNIT_BASELINE_DUPLICATE" in codes(collector)


# ---------------------------------------------------------------------------
# Roster-Coverage
# ---------------------------------------------------------------------------

def _rules_file(tmp_path: Path, aliases=(), exclusions=(), version=None) -> Path:
    path = tmp_path / "roster_rules.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "rules_version": version or roster_coverage.ROSTER_RULES_VERSION,
                "aliases": list(aliases),
                "exclusions": list(exclusions),
            },
            sort_keys=False, allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_units(tmp_path):
    root = build_corpus(
        tmp_path,
        {
            "house_testing.md": make_markdown(sections=("Subcommittee on Trade",)),
            "joint_library.md": make_markdown(chamber="joint", committee="Joint Committee of Congress on the Library"),
        },
    )
    collector, result = load(root)
    return units.build_units(collector, result.documents)


def _entry(chamber, committee, sub=None, members=1):
    return roster_coverage.RosterEntry(
        chamber=chamber, committee=committee, subcommittee=sub, members=members
    )


def test_exact_match_uses_the_canonical_key(sample_units):
    collector = ErrorCollector()
    rows = roster_coverage.build_coverage(
        collector, sample_units,
        [_entry("house", "Testing"), _entry("house", "Testing", "Trade")],
        roster_coverage.RosterRules({}, {}),
    )
    assert [r.coverage_status for r in rows] == [CoverageStatus.MAPPED, CoverageStatus.MAPPED]
    assert [r.unit_id for r in rows] == ["house--testing", "house--testing--trade"]
    assert all(r.match_basis is MatchBasis.EXACT for r in rows)


def test_missing_entry_carries_a_hint_and_no_unit(sample_units):
    collector = ErrorCollector()
    rows = roster_coverage.build_coverage(
        collector, sample_units, [_entry("house", "Voellig Unbekannt")],
        roster_coverage.RosterRules({}, {}),
    )
    assert rows[0].coverage_status is CoverageStatus.MISSING
    assert rows[0].unit_id is None and rows[0].match_basis is None
    assert "roster_rules" in (rows[0].hint or "")


def test_alias_maps_and_exclusion_excludes(sample_units, tmp_path):
    collector = ErrorCollector()
    rules = roster_coverage.load_rules(
        collector,
        _rules_file(
            tmp_path,
            aliases=[{"chamber": "house", "committee": "W&M", "unit_id": "house--testing",
                      "reason": "Rosterschreibweise"}],
            exclusions=[{"chamber": "senate", "committee": "Caucus on Something",
                         "reason": "Kein Committee"}],
        ),
    )
    assert collector.findings == []
    rows = roster_coverage.build_coverage(
        collector, sample_units,
        [_entry("house", "W&M"), _entry("senate", "Caucus on Something")],
        rules,
    )
    assert rows[0].match_basis is MatchBasis.ALIAS and rows[0].unit_id == "house--testing"
    assert rows[1].coverage_status is CoverageStatus.EXCLUDED
    assert rows[1].match_basis is MatchBasis.MANUAL_EXCLUSION
    assert rows[1].unit_id is None


def test_alias_to_an_unknown_unit_blocks(sample_units, tmp_path):
    collector = ErrorCollector()
    rules = roster_coverage.load_rules(
        collector,
        _rules_file(tmp_path, aliases=[{"chamber": "house", "committee": "X",
                                        "unit_id": "house--gibt-es-nicht", "reason": "Test"}]),
    )
    roster_coverage.build_coverage(collector, sample_units, [_entry("house", "X")], rules)
    assert "ROS_ALIAS_UNKNOWN" in codes(collector)


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"aliases": [{"chamber": "house", "committee": "X", "unit_id": "house--testing"}]}, "ROS_RULES_REASON"),
        ({"aliases": [{"chamber": "house", "committee": "X", "reason": "ohne Ziel"}]}, "ROS_RULES_ENTRY"),
        ({"version": "roster-rules-v0"}, "ROS_RULES_VERSION"),
    ],
)
def test_incomplete_rules_block(tmp_path, kwargs, code):
    collector = ErrorCollector()
    roster_coverage.load_rules(collector, _rules_file(tmp_path, **kwargs))
    assert code in codes(collector)


def test_missing_rules_file_warns_but_continues(tmp_path):
    collector = ErrorCollector()
    rules = roster_coverage.load_rules(collector, tmp_path / "fehlt.yaml")
    assert rules.aliases == {} and rules.exclusions == {}
    assert "ROS_RULES_MISSING" in codes(collector, Severity.WARNUNG)
    assert not collector.has_blockers()


def test_joint_committee_from_both_chambers_maps_to_one_unit(sample_units):
    collector = ErrorCollector()
    rows = roster_coverage.build_coverage(
        collector, sample_units,
        [_entry("house", "Joint Committee of Congress on the Library"),
         _entry("senate", "Joint Committee on the Library")],
        roster_coverage.RosterRules({}, {}),
    )
    assert {r.unit_id for r in rows} == {"joint--library"}
    assert [r.chamber for r in rows] == ["house", "senate"]


def test_fuzzy_only_suggests_and_never_maps(sample_units):
    collector = ErrorCollector()
    rows = roster_coverage.build_coverage(
        collector, sample_units, [_entry("house", "Testng")],
        roster_coverage.RosterRules({}, {}),
    )
    assert rows[0].coverage_status is CoverageStatus.MISSING
    assert rows[0].unit_id is None
    assert rows[0].fuzzy_suggestion == "testing"


def test_real_roster_coverage_is_complete_and_deduplicated(real_corpus):
    _, _, built = real_corpus
    collector = ErrorCollector()
    rows = roster_coverage.run_coverage(collector, built)
    assert not collector.has_blockers()
    assert len(rows) >= 200
    mapped = [r for r in rows if r.coverage_status is CoverageStatus.MAPPED]
    assert len(mapped) / len(rows) > 0.95
    joint = [r for r in rows if r.unit_id and r.unit_id.startswith("joint--")]
    assert {r.chamber for r in joint} == {"house", "senate"}
    assert all(r.unit_id in {u.unit_id for u in built} for r in mapped)


def test_roster_files_are_only_read(real_corpus):
    for path in roster_coverage.roster_paths().values():
        assert path.exists()
        with pytest.raises(paths.PathGuardError):
            paths.assert_writable(path)


def test_shipped_roster_rules_file_is_valid():
    collector = ErrorCollector()
    roster_coverage.load_rules(collector, roster_coverage.ROSTER_RULES_PATH)
    assert not collector.has_blockers()


# ---------------------------------------------------------------------------
# CLI-Einstiege der Phase
# ---------------------------------------------------------------------------

def test_cli_input_validate_writes_units_and_report(tmp_path, monkeypatch, capsys):
    from committee_matcher import cli

    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path)
    code = cli.main(["input", "validate"])
    capsys.readouterr()
    payload = json.loads((tmp_path / "reports" / "input_validate" / "units.json").read_text("utf-8"))
    assert payload["unit_id_version"] == units.UNIT_ID_VERSION
    assert len(payload["units"]) == 225
    report = json.loads((tmp_path / "reports" / "input_validate" / "errors.json").read_text("utf-8"))
    assert {f["code"] for f in report["findings"]} == {"INP_EVIDENCE_TOO_SHORT"}
    assert code == 2  # nur Warnungen, kein Blocker


def test_cli_roster_validate_reports_open_coverage(tmp_path, monkeypatch, capsys):
    from committee_matcher import cli

    monkeypatch.setattr(paths, "ARTIFACT_ROOT", tmp_path)
    code = cli.main(["roster", "validate"])
    out = capsys.readouterr().out
    rows = json.loads(
        (tmp_path / "reports" / "roster_validate" / "roster_coverage.json").read_text("utf-8")
    )["rows"]
    assert len(rows) >= 200
    assert "Coverage:" in out
    assert code == 2


# ---------------------------------------------------------------------------
# Nachgezogene Reviewauflagen (P3)
# ---------------------------------------------------------------------------

def test_subcommittees_carry_parent_context_separate_from_evidence(real_corpus):
    """Parent-Kontext ist Kontext, nie Beleg - auch bei name_only."""
    _, result, built = real_corpus
    by_source = {d.rel_path: d for d in result.documents}
    subs = [u for u in built if u.kind is UnitKind.SUBCOMMITTEE]
    assert subs
    for unit in subs:
        document = by_source[unit.source_path]
        assert unit.parent_committee_name == document.committee
        assert unit.parent_context_text == input_corpus.normalize_text(document.preamble)
        if unit.input_basis is InputBasis.NAME_ONLY:
            assert unit.evidence_text == ""
    for unit in built:
        if unit.kind is not UnitKind.SUBCOMMITTEE:
            assert unit.parent_committee_name is None
            assert unit.parent_context_text == ""


def test_parent_text_change_invalidates_the_child_hash(tmp_path):
    def hashes(preamble: str, slot: str) -> dict[str, str]:
        root = build_corpus(
            tmp_path / slot,
            {"house_testing.md": make_markdown(sections=("Subcommittee on A",), preamble=preamble)},
        )
        collector, result = load(root)
        return {u.unit_id: u.mapping_input_hash for u in units.build_units(collector, result.documents)}

    before = hashes("Zustaendig fuer Aufsicht, Pruefung und Kontrolle der Vorgaenge.", "a")
    after = hashes("Zustaendig fuer Aufsicht, Pruefung und Kontrolle der Verfahren.", "b")
    assert before["house--testing--a"] != after["house--testing--a"]


@pytest.mark.parametrize(
    "frontmatter_patch,code",
    [
        ({"source_metadata_status": "erfunden"}, "INP_SOURCE_STATUS"),
        ({"source_url": "ftp://example.org"}, "INP_SOURCE_URL"),
        ({"retrieved_at": "gestern"}, "INP_RETRIEVED_AT"),
        ({"source_metadata_status": "complete"}, "INP_SOURCE_METADATA_INCOMPLETE"),
    ],
)
def test_source_metadata_contract_is_enforced(tmp_path, frontmatter_patch, code):
    text = make_markdown()
    for key, value in frontmatter_patch.items():
        text = text.replace(f"{key}: null\n", f"{key}: {value}\n")
        text = text.replace(f"{key}: pending\n", f"{key}: {value}\n")
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, _ = load(root)
    assert code in codes(collector)


def test_complete_metadata_with_url_and_timestamp_passes(tmp_path):
    text = (
        make_markdown()
        .replace("source_url: null\n", "source_url: https://example.org/rules\n")
        .replace("retrieved_at: null\n", "retrieved_at: 2026-08-20T10:00:00+00:00\n")
        .replace("source_metadata_status: pending\n", "source_metadata_status: complete\n")
    )
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, result = load(root)
    assert collector.findings == []
    assert len(result.documents) == 1


def test_invalid_utf8_is_reported_instead_of_crashing(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    target = root / "md" / "house_testing.md"
    raw = target.read_bytes().replace(b"Tests", b"Te\xffts")
    target.write_bytes(raw)
    manifest = yaml.safe_load((root / "corpus_manifest.yaml").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "md/house_testing.md":
            entry["bytes"] = len(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
    (root / "corpus_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    collector, result = load(root)
    assert "INP_ENCODING" in codes(collector)
    assert result.documents == ()


def test_size_drift_blocks_even_when_the_hash_field_is_stale(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    target = root / "md" / "house_testing.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nnoch ein Satz.\n", encoding="utf-8")
    collector, _ = load(root)
    assert "INP_MANIFEST_SIZE" in codes(collector)


def test_unreferenced_source_file_blocks(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown()})
    collector, _ = load(root)
    assert collector.findings == []

    (root / "txt" / "verwaist.txt").write_text("Ungenutzte Quelle.\n", encoding="utf-8")
    root = build_corpus(
        tmp_path / "zweit", {"house_testing.md": make_markdown()}
    )
    (root / "txt" / "verwaist.txt").write_text("Ungenutzte Quelle.\n", encoding="utf-8")
    entry_path = root / "corpus_manifest.yaml"
    manifest = yaml.safe_load(entry_path.read_text(encoding="utf-8"))
    data = (root / "txt" / "verwaist.txt").read_bytes()
    manifest["files"].append({
        "path": "txt/verwaist.txt", "role": "raw_source",
        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    })
    entry_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    collector, _ = load(root)
    assert "INP_SOURCE_ORPHAN" in codes(collector)


def test_findings_report_the_line_in_the_file_not_in_the_body(tmp_path):
    text = make_markdown(sections=("Subcommittee on A",), body_extra="\nBitte #userinput beachten.\n")
    root = build_corpus(tmp_path, {"house_testing.md": text})
    collector, _ = load(root)
    lines = text.splitlines()
    expected = next(i for i, line in enumerate(lines, start=1) if "#userinput" in line)
    finding = next(f for f in collector.findings if f.code == "INP_FOREIGN_INSTRUCTION")
    assert finding.line == expected


def test_baseline_metadata_inconsistencies_block(tmp_path):
    built = _unit_list(tmp_path)
    collector = ErrorCollector()
    units.check_against_baseline(
        collector, built, _baseline_file(tmp_path, ["house--testing"], unit_count=99)
    )
    assert "UNIT_BASELINE_COUNT" in codes(collector)

    path = _baseline_file(tmp_path, ["house--testing"])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["mapping_input_version"] = "mapping-input-v0"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    collector = ErrorCollector()
    units.check_against_baseline(collector, built, path)
    assert "UNIT_BASELINE_VERSION" in codes(collector)


def test_baseline_kind_mismatch_blocks(tmp_path):
    root = build_corpus(tmp_path, {"house_testing.md": make_markdown(sections=("Subcommittee on A",))})
    collector, result = load(root)
    built = units.build_units(collector, result.documents)
    path = tmp_path / "unit_ids.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "canonicalization_version": units.UNIT_ID_VERSION,
                "mapping_input_version": units.MAPPING_INPUT_VERSION,
                "unit_count": 2,
                "units": [
                    {"unit_id": "house--testing", "kind": "committee_direct", "chamber": "house"},
                    {"unit_id": "house--testing--a", "kind": "subcommittee", "chamber": "house"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    collector = ErrorCollector()
    assert units.check_against_baseline(collector, built, path) is False
    assert "UNIT_BASELINE_MISMATCH" in codes(collector)


def test_units_without_roster_occurrence_are_reported(sample_units):
    collector = ErrorCollector()
    rows = roster_coverage.build_coverage(
        collector, sample_units, [_entry("house", "Testing")],
        roster_coverage.RosterRules({}, {}),
    )
    without = roster_coverage.report_gaps(collector, sample_units, rows)
    assert set(without) == {"house--testing--trade", "joint--library"}
    codes_seen = codes(collector, Severity.WARNUNG)
    assert "ROS_UNIT_WITHOUT_ROSTER" in codes_seen
    assert not collector.has_blockers()
