"""Tests fuer die P0-Inputbaseline des Committee Matchers V2.

Pinnt den Inputvertrag aus ``organisation/committee_jurisdiction_input_template_codex.md``
sowie die im Plan eingefrorenen Corpus-Sollwerte: 46 Mapping-MDs, 225 Units,
8 ``name_only``-Units, vollstaendiges Rollenmanifest und unveraenderte
matcherbezogene Produktionspfade.

Alle Tests sind netzwerkfrei. Die schreibenden Faelle laufen ausschliesslich
auf einem synthetischen Corpus unter ``tmp_path``; der echte Inputroot wird nur
read-only geprueft.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Als Top-Level-Module importieren, damit der scriptinterne Import in
# committee_matcher_baseline dieselben Modulobjekte trifft (sonst existierten
# MigrationBlocked/Finding doppelt und pytest.raises schluege fehl).
import committee_matcher_baseline as baseline  # noqa: E402
import committee_matcher_migrate_inputs as migrate  # noqa: E402

INPUT_ROOT = migrate.DEFAULT_INPUT_ROOT
MD_DIR = INPUT_ROOT / "md"


# ---------------------------------------------------------------------------
# Realer Corpus - ausschliesslich lesend
# ---------------------------------------------------------------------------


def mapping_files() -> list[Path]:
    return migrate.discover_mapping_files(INPUT_ROOT)


def frontmatter_of(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    raw, _body, _ = migrate.split_frontmatter(text, path.name)
    return migrate.parse_frontmatter(raw, path.name)


def test_migration_is_idempotent():
    """Nach der Migration meldet --check keine ausstehende Aenderung mehr."""
    assert migrate.main(["--check"]) == 0


def test_mapping_file_count():
    assert len(mapping_files()) == migrate.EXPECTED_MAPPING_FILES


def test_no_legacy_field_remains():
    offenders = [p.name for p in mapping_files() if "input_basis" in frontmatter_of(p)]
    assert offenders == []


def test_no_legacy_marker_remains():
    offenders = []
    for path in mapping_files():
        text = path.read_text(encoding="utf-8")
        if any(migrate.LEGACY_MARKER_RE.match(line) for line in text.splitlines()):
            offenders.append(path.name)
    assert offenders == []


@pytest.mark.parametrize("path", mapping_files(), ids=lambda p: p.name)
def test_unit_input_basis_matches_h2_set(path: Path):
    """Subcommittee-Keys entsprechen exakt der H2-Menge ohne Kommentarabschnitt."""
    text = path.read_text(encoding="utf-8")
    raw, body, _ = migrate.split_frontmatter(text, path.name)
    data = migrate.parse_frontmatter(raw, path.name)
    sections = migrate.heading_sections(body, path.name)

    basis = data["unit_input_basis"]
    assert basis["committee"] == migrate.BASIS_JURISDICTION_TEXT
    subs = basis["subcommittees"] or {}
    assert list(subs.keys()) == list(sections.keys())
    assert all(v in migrate.CANONICAL_BASIS_VALUES for v in subs.values())


def test_name_only_count_is_frozen():
    total = 0
    for path in mapping_files():
        subs = frontmatter_of(path)["unit_input_basis"]["subcommittees"] or {}
        total += sum(1 for v in subs.values() if v == migrate.BASIS_NAME_ONLY)
    assert total == migrate.EXPECTED_NAME_ONLY_UNITS


def test_unit_soll_menge_ist_225():
    units, findings = baseline.derive_units(INPUT_ROOT)
    assert [f.code for f in findings] == []
    counts = {}
    for unit in units:
        counts[unit["unit_type"]] = counts.get(unit["unit_type"], 0) + 1
    assert counts[baseline.UNIT_TYPE_SUBCOMMITTEE] == migrate.EXPECTED_UNITS_SUBCOMMITTEE
    assert counts[baseline.UNIT_TYPE_PARENT_CORE] == migrate.EXPECTED_UNITS_PARENT_CORE
    assert counts[baseline.UNIT_TYPE_DIRECT] == migrate.EXPECTED_UNITS_DIRECT
    assert len(units) == migrate.EXPECTED_UNITS_TOTAL


def test_chamber_matches_filename():
    for path in mapping_files():
        chamber = frontmatter_of(path)["chamber"]
        assert chamber in migrate.VALID_CHAMBERS
        assert path.name.startswith(f"{chamber}_")


def test_foreign_instruction_markers_only_inside_comment_section():
    """Vorbedingung fuer das P3-Fremdanweisungsgate.

    Der Marker ``#userinput`` darf ausschliesslich im reservierten Abschnitt
    ``Zusaetzliche Kommentare`` vorkommen; ausserhalb blockiert er die Datei.
    """
    offenders = []
    for path in mapping_files():
        text = path.read_text(encoding="utf-8")
        _raw, body, _ = migrate.split_frontmatter(text, path.name)
        head, sep, _comments = body.partition(f"## {migrate.COMMENT_HEADING}")
        assert sep, f"{path.name} hat keinen Kommentarabschnitt"
        if "#userinput" in head:
            offenders.append(path.name)
    assert offenders == []


# ---------------------------------------------------------------------------
# Manifest und Schutzbaseline
# ---------------------------------------------------------------------------


def test_manifest_verify_is_clean():
    assert baseline.main(["manifest", "--verify"]) == 0


def test_manifest_covers_every_corpus_file_exactly_once():
    stored = yaml.safe_load(baseline.MANIFEST_PATH.read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in stored["files"]]
    assert len(paths) == len(set(paths))

    on_disk = {p.relative_to(INPUT_ROOT).as_posix() for p in baseline.iter_corpus_files(INPUT_ROOT)}
    assert set(paths) == on_disk
    assert baseline.MANIFEST_PATH.name not in on_disk


def test_manifest_role_counts_are_frozen():
    stored = yaml.safe_load(baseline.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert stored["role_counts"] == baseline.EXPECTED_ROLE_COUNTS
    assert stored["manifest_version"] == baseline.MANIFEST_VERSION
    assert stored["migration_version"] == migrate.MIGRATION_VERSION


def test_source_files_reference_only_referenceable_roles():
    _manifest, findings = baseline.build_manifest(INPUT_ROOT)
    assert [f.code for f in findings] == []


def test_protected_production_paths_unchanged():
    """Schutztest: V2 darf keinen matcherbezogenen Produktionspfad veraendern."""
    assert baseline.main(["protect", "--verify"]) == 0


def test_protected_baseline_covers_full_list():
    stored = yaml.safe_load(baseline.PROTECTED_BASELINE_PATH.read_text(encoding="utf-8"))
    assert {e["path"] for e in stored["paths"]} == set(baseline.PROTECTED_PRODUCTION_PATHS)


# ---------------------------------------------------------------------------
# Synthetischer Corpus - schreibende Migrationspfade
# ---------------------------------------------------------------------------


def write_md(directory: Path, name: str, frontmatter: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return path


BASE_FRONTMATTER = """schema_version: 1
congress: 119
chamber: house
committee: Committee on Example Affairs
committee_type: standing
source_url: null
retrieved_at: null
source_metadata_status: pending
source_files:
  - data/committee_matcher_inputs/txt/example.txt"""


@pytest.fixture()
def synthetic_root(tmp_path: Path) -> Path:
    root = tmp_path / "inputs"
    (root / "md").mkdir(parents=True)
    (root / "txt").mkdir(parents=True)
    return root


def test_apply_migrates_legacy_field_and_marker(synthetic_root: Path):
    write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER + "\ninput_basis: committee_direct",
        "# Committee jurisdiction\n\nJurisdiktionstext.\n\n"
        "## Subcommittee on Alpha\n\nAlpha-Text.\n\n"
        "## Subcommittee on Beta\n\n<!-- input_basis: name_only -->\nNur Name.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nKommentar.\n",
    )

    assert migrate.main(["--apply", "--input-root", str(synthetic_root)]) == 0
    text = (synthetic_root / "md" / "house_example.md").read_text(encoding="utf-8")
    data = migrate.parse_frontmatter(migrate.split_frontmatter(text, "x")[0], "x")

    assert "input_basis" not in data
    assert data["unit_input_basis"]["committee"] == migrate.BASIS_JURISDICTION_TEXT
    assert data["unit_input_basis"]["subcommittees"] == {
        "Subcommittee on Alpha": migrate.BASIS_JURISDICTION_TEXT,
        "Subcommittee on Beta": migrate.BASIS_NAME_ONLY,
    }
    assert "<!-- input_basis" not in text
    assert "Jurisdiktionstext." in text and "Kommentar." in text


def test_apply_is_idempotent_on_synthetic_corpus(synthetic_root: Path):
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER + "\ninput_basis: committee_direct",
        "# Committee jurisdiction\n\nText.\n\n"
        "## Subcommittee on Beta\n\n<!-- input_basis: name_only -->\nNur Name.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nKommentar.\n",
    )
    migrate.main(["--apply", "--input-root", str(synthetic_root)])
    first = path.read_text(encoding="utf-8")
    migrate.main(["--apply", "--input-root", str(synthetic_root)])
    assert path.read_text(encoding="utf-8") == first

    report = migrate.build_target(path, first, synthetic_root)
    assert report.changed is False
    assert report.unit_basis["Subcommittee on Beta"] == migrate.BASIS_NAME_ONLY


def test_comment_section_never_becomes_a_unit(synthetic_root: Path):
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER,
        "# Committee jurisdiction\n\nText.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nKommentar mit #userinput-Anweisung.\n",
    )
    report = migrate.build_target(path, path.read_text(encoding="utf-8"), synthetic_root)
    assert report.unit_basis == {}
    assert report.unit_count == 1  # nur committee_direct


def test_h3_inside_jurisdiction_is_allowed(synthetic_root: Path):
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER,
        "# Committee jurisdiction\n\n### Primary Rule X jurisdiction\n\nText.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nKommentar.\n",
    )
    report = migrate.build_target(path, path.read_text(encoding="utf-8"), synthetic_root)
    assert report.unit_basis == {}


def test_marker_conflict_blocks(synthetic_root: Path):
    """Bestehender Frontmatterwert und Marker widersprechen sich - kein Raten."""
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER
        + '\nunit_input_basis:\n  committee: jurisdiction_text\n'
        '  subcommittees:\n    "Subcommittee on Beta": jurisdiction_text',
        "# Committee jurisdiction\n\nText.\n\n"
        "## Subcommittee on Beta\n\n<!-- input_basis: name_only -->\nNur Name.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nKommentar.\n",
    )
    with pytest.raises(migrate.MigrationBlocked) as excinfo:
        migrate.build_target(path, path.read_text(encoding="utf-8"), synthetic_root)
    assert excinfo.value.finding.code == "MIG_BASIS_MARKER_CONFLICT"


def test_basis_key_mismatch_blocks(synthetic_root: Path):
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER
        + '\nunit_input_basis:\n  committee: jurisdiction_text\n'
        '  subcommittees:\n    "Subcommittee on Gamma": jurisdiction_text',
        "# Committee jurisdiction\n\nText.\n\n"
        "## Subcommittee on Beta\n\nText.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nKommentar.\n",
    )
    with pytest.raises(migrate.MigrationBlocked) as excinfo:
        migrate.build_target(path, path.read_text(encoding="utf-8"), synthetic_root)
    assert excinfo.value.finding.code == "MIG_BASIS_KEY_MISMATCH"


def test_duplicate_subcommittee_blocks(synthetic_root: Path):
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER,
        "# Committee jurisdiction\n\nText.\n\n"
        "## Subcommittee on Alpha\n\nA.\n\n## Subcommittee on Alpha\n\nB.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nKommentar.\n",
    )
    with pytest.raises(migrate.MigrationBlocked) as excinfo:
        migrate.build_target(path, path.read_text(encoding="utf-8"), synthetic_root)
    assert excinfo.value.finding.code == "MIG_DUPLICATE_SUBCOMMITTEE"


def test_section_after_comment_section_blocks(synthetic_root: Path):
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER,
        "# Committee jurisdiction\n\nText.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nKommentar.\n\n## Subcommittee on Alpha\n\nA.\n",
    )
    with pytest.raises(migrate.MigrationBlocked) as excinfo:
        migrate.build_target(path, path.read_text(encoding="utf-8"), synthetic_root)
    assert excinfo.value.finding.code == "MIG_SECTION_AFTER_COMMENTS"


def test_missing_h1_blocks(synthetic_root: Path):
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER,
        "## Subcommittee on Alpha\n\nA.\n",
    )
    with pytest.raises(migrate.MigrationBlocked) as excinfo:
        migrate.build_target(path, path.read_text(encoding="utf-8"), synthetic_root)
    assert excinfo.value.finding.code == "MIG_H1_CONTRACT"


def test_missing_frontmatter_key_blocks(synthetic_root: Path):
    reduced = "\n".join(
        line for line in BASE_FRONTMATTER.splitlines() if not line.startswith("committee_type:")
    )
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        reduced,
        f"# Committee jurisdiction\n\nText.\n\n## {migrate.COMMENT_HEADING}\n\nK.\n",
    )
    with pytest.raises(migrate.MigrationBlocked) as excinfo:
        migrate.build_target(path, path.read_text(encoding="utf-8"), synthetic_root)
    assert excinfo.value.finding.code == "MIG_FRONTMATTER_KEY_MISSING"


def test_long_subcommittee_name_round_trips(synthetic_root: Path):
    """Namen ueber 128 Zeichen bleiben normale Block-Keys, keine Explicit Keys."""
    long_name = "Subcommittee on " + "Very Long Policy Area, " * 8 + "and Related Matters"
    assert len(long_name) > 128
    path = write_md(
        synthetic_root / "md",
        "house_example.md",
        BASE_FRONTMATTER,
        f"# Committee jurisdiction\n\nText.\n\n## {long_name}\n\nText.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nK.\n",
    )
    migrate.main(["--apply", "--input-root", str(synthetic_root)])
    text = path.read_text(encoding="utf-8")
    assert "\n    ? " not in text
    data = migrate.parse_frontmatter(migrate.split_frontmatter(text, "x")[0], "x")
    assert list(data["unit_input_basis"]["subcommittees"]) == [long_name]


def test_unknown_corpus_file_blocks(synthetic_root: Path):
    (synthetic_root / "md" / "house_example.md").write_text(
        f"---\n{BASE_FRONTMATTER}\n---\n\n# Committee jurisdiction\n\nT.\n\n"
        f"## {migrate.COMMENT_HEADING}\n\nK.\n",
        encoding="utf-8",
    )
    (synthetic_root / "unerwartet.bin").write_bytes(b"x")
    _manifest, findings = baseline.build_manifest(synthetic_root)
    assert "MAN_UNKNOWN_FILE" in {f.code for f in findings}
