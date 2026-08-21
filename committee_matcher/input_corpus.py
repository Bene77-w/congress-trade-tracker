"""Strikter Inputparser des Committee Matchers V2 (Plan 2.1).

Zustaendig fuer genau eine Aufgabe: aus dem eingefrorenen Corpus unter
``data/committee_matcher_inputs/`` gepruefte Dokumente machen. Der Parser
korrigiert nichts durch Raten - jede Abweichung vom Inputvertrag
(``organisation/committee_jurisdiction_input_template_codex.md``) wird als
Befund mit Pfad und Zeile gemeldet.

Gepruefte Regeln:

- das Corpusmanifest deckt jede regulaere Datei genau einmal ab; Hash, Groesse
  und Rolle muessen stimmen, unbekannte Dateien blockieren;
- Datei- und Frontmattergroesse bleiben unter den Settinggrenzen;
- Frontmatter wird mit ``yaml.safe_load`` gelesen und muss die Pflichtfelder
  tragen; Kammer und Dateiname duerfen sich nicht widersprechen;
- ``source_files`` referenziert ausschliesslich ``raw_source`` oder
  ``source_only`` innerhalb des Inputroots, Pfadtraversal blockiert;
- der Body traegt genau eine H1 ``# Committee jurisdiction``; H2-Abschnitte sind
  eindeutig und stehen vor ``## Zusaetzliche Kommentare``;
- der Kommentarabschnitt ist kein Mappinginput; eindeutig markierte
  Fremdanweisungen ausserhalb davon blockieren mit Pfad und Zeile.

Erzeugung von Manifest und Schutzbaseline bleibt beim P0-Werkzeug
``scripts/committee_matcher_baseline.py``; hier wird ausschliesslich gelesen
und geprueft.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from . import paths
from .errors import ErrorCollector, Stage
# ``normalize_text`` gehoert zum Datenvertrag und liegt deshalb in ``models``;
# der Reexport haelt den Inputparser als gewohnten Fundort.
from .models import normalize_text
from .settings_view import OfflineSettings
from .status import CorpusRole, InputBasis

#: Wortlaut der Pflicht-H1 im Body.
JURISDICTION_HEADING = "Committee jurisdiction"
#: Abschnitt, der niemals Mappinginput ist.
COMMENT_HEADING = "Zusätzliche Kommentare"

#: Sammelquellen ohne eigene Units.
INPUT_ROOT_PREFIX = "data/committee_matcher_inputs/"
SOURCE_ONLY_MD = frozenset(
    {
        "house-rule-x-committee-jurisdictions.md",
        "senate-rule-xxv-committee-jurisdictions.md",
    }
)

#: Pflichtfelder des Frontmatters.
REQUIRED_FIELDS = ("schema_version", "congress", "chamber", "committee", "committee_type")

#: Rollen, die eine Mapping-MD in 'source_files' referenzieren darf.
REFERENCEABLE_ROLES = (CorpusRole.RAW_SOURCE, CorpusRole.SOURCE_ONLY)

#: Zulaessige Werte von 'source_metadata_status'.
SOURCE_METADATA_STATUS = ("complete", "pending")

#: Zulaessige Kammern; der Dateiname muss dazu passen.
CHAMBERS = ("house", "senate", "joint")

#: Eindeutig markierte Fremdanweisungen. Ausserhalb des Kommentarabschnitts
#: blockieren sie, innerhalb sind sie zulaessig und kein Mappinginput.
FOREIGN_INSTRUCTION_MARKERS = ("#userinput",)

H1_RE = re.compile(r"^# (.+?)\s*$", re.MULTILINE)
H2_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Section:
    """Ein H2-Abschnitt des Bodys."""

    name: str
    text: str
    line: int


@dataclass(frozen=True)
class CommitteeDocument:
    """Ein geprueftes Mapping-Markdown."""

    path: Path
    rel_path: str
    chamber: str
    committee: str
    committee_type: str
    source_files: tuple[str, ...]
    committee_basis: InputBasis
    subcommittee_basis: dict[str, InputBasis]
    preamble: str
    sections: tuple[Section, ...] = ()
    source_bytes_sha256: str = ""

    @property
    def has_subcommittees(self) -> bool:
        return bool(self.sections)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class CorpusLoadResult:
    """Ergebnis eines Ladelaufs."""

    documents: tuple[CommitteeDocument, ...] = ()
    manifest_files: int = 0
    input_root: Path = paths.INPUT_ROOT
    skipped: tuple[str, ...] = field(default_factory=tuple)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def verify_manifest(
    collector: ErrorCollector, input_root: Path = paths.INPUT_ROOT
) -> dict[str, CorpusRole]:
    """Prueft das Corpusmanifest gegen die Platte und liefert die Rollenkarte.

    Erzeugt wird das Manifest vom P0-Werkzeug; hier wird nur verifiziert, damit
    kein Lauf auf einem verschobenen Corpus arbeitet.
    """
    manifest_path = input_root / "corpus_manifest.yaml"
    if not manifest_path.exists():
        collector.blocker(
            "INP_MANIFEST_MISSING", Stage.INPUT, _relative(manifest_path, input_root),
            "Corpusmanifest fehlt.",
            "scripts/committee_matcher_baseline.py manifest --write ausfuehren.",
        )
        return {}
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        collector.blocker(
            "INP_MANIFEST_MALFORMED", Stage.INPUT, _relative(manifest_path, input_root),
            f"Corpusmanifest ist kein gueltiges YAML ({exc.__class__.__name__}).",
            "Manifest neu erzeugen.",
        )
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        collector.blocker(
            "INP_MANIFEST_MALFORMED", Stage.INPUT, _relative(manifest_path, input_root),
            "Corpusmanifest hat kein 'files'-Feld.",
            "Manifest neu erzeugen.",
        )
        return {}

    roles: dict[str, CorpusRole] = {}
    seen: set[str] = set()
    for entry in data["files"]:
        if not isinstance(entry, dict) or not entry.get("path"):
            collector.blocker(
                "INP_MANIFEST_MALFORMED", Stage.INPUT, _relative(manifest_path, input_root),
                "Manifesteintrag ohne 'path'.",
                "Manifest neu erzeugen.",
            )
            continue
        rel = str(entry["path"])
        if rel in seen:
            collector.blocker(
                "INP_MANIFEST_DUPLICATE", Stage.INPUT, rel,
                "Datei ist mehrfach manifestiert.",
                "Manifest neu erzeugen.",
            )
            continue
        seen.add(rel)
        try:
            role = CorpusRole(str(entry.get("role")))
        except ValueError:
            collector.blocker(
                "INP_MANIFEST_ROLE", Stage.INPUT, rel,
                f"Unbekannte Rolle {entry.get('role')!r}.",
                "Rolle im Manifest korrigieren.",
            )
            continue
        try:
            target = paths.resolve_within(input_root, rel)
        except paths.PathGuardError as exc:
            collector.extend(exc.findings)
            continue
        if not target.exists():
            collector.blocker(
                "INP_MANIFEST_STALE", Stage.INPUT, rel,
                "Manifestierte Datei fehlt auf der Platte.",
                "Datei wiederherstellen oder Manifest neu erzeugen.",
            )
            continue
        raw = target.read_bytes()
        if entry.get("bytes") != len(raw):
            collector.blocker(
                "INP_MANIFEST_SIZE", Stage.INPUT, rel,
                f"Datei hat {len(raw)} Bytes statt manifestierter "
                f"{entry.get('bytes')!r}.",
                "Aenderung pruefen; danach Manifest bewusst neu erzeugen.",
            )
            continue
        if sha256_bytes(raw) != str(entry.get("sha256")):
            collector.blocker(
                "INP_MANIFEST_DRIFT", Stage.INPUT, rel,
                "Datei weicht vom manifestierten SHA-256 ab.",
                "Aenderung pruefen; danach Manifest bewusst neu erzeugen.",
            )
            continue
        roles[rel] = role

    for path in sorted(input_root.rglob("*")):
        if not path.is_file() or path == manifest_path:
            continue
        rel = _relative(path, input_root)
        if rel not in seen:
            collector.blocker(
                "INP_UNKNOWN_FILE", Stage.INPUT, rel,
                "Datei liegt im Inputroot, ist aber nicht manifestiert.",
                "Datei entfernen oder bewusst ins Manifest aufnehmen.",
            )
    return roles


def _check_source_metadata(collector: ErrorCollector, rel_path: str, data: dict) -> None:
    """Prueft den Provenienzteil des Frontmatters (Inputvertrag).

    ``pending`` erlaubt fehlende Provenienz, ``complete`` verlangt URL **und**
    Abrufzeit. Nichts wird ergaenzt oder geraten - eine erfundene Quelle waere
    schlimmer als eine fehlende.
    """
    status = data.get("source_metadata_status")
    if status not in SOURCE_METADATA_STATUS:
        collector.blocker(
            "INP_SOURCE_STATUS", Stage.INPUT, rel_path,
            f"'source_metadata_status' ist {status!r}.",
            f"Zulaessig sind {', '.join(SOURCE_METADATA_STATUS)}.", line=2,
        )
        return

    url = data.get("source_url")
    retrieved = data.get("retrieved_at")
    if url is not None and (not isinstance(url, str) or not url.startswith(("http://", "https://"))):
        collector.blocker(
            "INP_SOURCE_URL", Stage.INPUT, rel_path,
            f"'source_url' ist keine http(s)-URL: {url!r}.",
            "Quell-URL korrigieren oder auf null setzen.", line=2,
        )
    if retrieved is not None and not _is_iso_timestamp(retrieved):
        collector.blocker(
            "INP_RETRIEVED_AT", Stage.INPUT, rel_path,
            f"'retrieved_at' ist kein ISO-8601-Zeitpunkt: {retrieved!r}.",
            "Abrufzeit als ISO-8601 schreiben oder auf null setzen.", line=2,
        )
    if status == "complete" and (url is None or retrieved is None):
        collector.blocker(
            "INP_SOURCE_METADATA_INCOMPLETE", Stage.INPUT, rel_path,
            "Status 'complete' ohne 'source_url' und 'retrieved_at'.",
            "Provenienz vervollstaendigen oder Status auf 'pending' setzen.", line=2,
        )


def _is_iso_timestamp(value: object) -> bool:
    if isinstance(value, (datetime, date)):
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _check_source_files(
    collector: ErrorCollector,
    rel_path: str,
    values: object,
    roles: dict[str, CorpusRole],
    input_root: Path,
) -> tuple[str, ...]:
    if not isinstance(values, list) or not values:
        collector.blocker(
            "INP_SOURCE_FILES", Stage.INPUT, rel_path,
            "'source_files' fehlt oder ist leer.",
            "Mindestens eine Quelldatei referenzieren.",
        )
        return ()
    resolved: list[str] = []
    for value in values:
        raw = str(value).strip()
        # Vertrag: 'source_files' sind projektwurzel-relativ und tragen den
        # Inputroot-Praefix. Aufgeloest wird gegen den (ggf. injizierten)
        # Inputroot, damit Tests und Kopien des Corpus dieselbe Pruefung
        # durchlaufen wie die Produktionsablage.
        if not raw.startswith(INPUT_ROOT_PREFIX):
            collector.blocker(
                "INP_SOURCE_OUTSIDE_ROOT", Stage.INPUT, rel_path,
                f"Referenz {raw!r} traegt nicht den Praefix {INPUT_ROOT_PREFIX!r}.",
                "Quelldateien projektwurzel-relativ unterhalb des Inputroots referenzieren.",
            )
            continue
        try:
            target = paths.resolve_within(
                input_root, raw[len(INPUT_ROOT_PREFIX):], must_exist=True
            )
        except paths.PathGuardError as exc:
            collector.extend(exc.findings)
            continue
        ref = _relative(target, input_root)
        role = roles.get(ref)
        if role is None:
            collector.blocker(
                "INP_SOURCE_UNKNOWN", Stage.INPUT, rel_path,
                f"Referenz {ref!r} ist nicht manifestiert.",
                "Quelldatei ins Manifest aufnehmen.",
            )
            continue
        if role not in REFERENCEABLE_ROLES:
            collector.blocker(
                "INP_SOURCE_ROLE", Stage.INPUT, rel_path,
                f"Referenz {ref!r} hat die Rolle {role.value}.",
                "'source_files' darf nur raw_source oder source_only referenzieren.",
            )
            continue
        resolved.append(ref)
    return tuple(resolved)


def _check_evidence_length(
    collector: ErrorCollector,
    rel_path: str,
    preamble: str,
    sections: tuple[Section, ...],
    sub_basis: dict[str, InputBasis],
    settings: OfflineSettings,
) -> None:
    """Meldet zu kurzen Jurisdiktionstext als Warnung.

    Ein als ``jurisdiction_text`` deklarierter Abschnitt mit wenigen Zeichen ist
    faktisch namensgleich - typisch fuer eine abgeschnittene Quelle. Der Lauf
    bricht nicht ab (Exit 2), weil der Text inhaltlich richtig sein kann; die
    Entscheidung liegt bei der Pflege des Corpus.
    """
    minimum = settings.min_evidence_chars
    if minimum <= 0:
        return
    length = len(normalize_text(preamble))
    if length < minimum:
        collector.warning(
            "INP_EVIDENCE_TOO_SHORT", Stage.INPUT, rel_path,
            f"Committee-Text hat nur {length} Zeichen (Minimum {minimum}).",
            "Quelltext pruefen; sonst bewusst auf name_only umstellen.", line=1,
        )
    for section in sections:
        if sub_basis.get(section.name) is not InputBasis.JURISDICTION_TEXT:
            continue
        length = len(normalize_text(section.text))
        if length < minimum:
            collector.warning(
                "INP_EVIDENCE_TOO_SHORT", Stage.INPUT, f"{rel_path}#{section.name}",
                f"Jurisdiktionstext hat nur {length} Zeichen (Minimum {minimum}).",
                "Quelltext pruefen; sonst bewusst auf name_only umstellen.",
                line=section.line,
            )


def _split_frontmatter(
    collector: ErrorCollector, rel_path: str, text: str, settings: OfflineSettings
) -> tuple[str, str, int] | None:
    """Liefert Frontmatter, Body und die Zeilenzahl vor dem Body."""
    if not text.startswith("---\n"):
        collector.blocker(
            "INP_NO_FRONTMATTER", Stage.INPUT, rel_path,
            "Datei beginnt nicht mit einem YAML-Frontmatterblock.",
            "Frontmatter mit '---' in Zeile 1 ergaenzen.", line=1,
        )
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        collector.blocker(
            "INP_UNCLOSED_FRONTMATTER", Stage.INPUT, rel_path,
            "Frontmatterblock ist nicht mit '---' geschlossen.",
            "Abschlusszeile '---' ergaenzen.", line=1,
        )
        return None
    frontmatter = text[4 : end + 1]
    if len(frontmatter.encode("utf-8")) > settings.max_frontmatter_bytes:
        collector.blocker(
            "INP_FRONTMATTER_TOO_LARGE", Stage.INPUT, rel_path,
            f"Frontmatter ueberschreitet {settings.max_frontmatter_bytes} Bytes.",
            "Frontmatter kuerzen oder Grenze in config/settings.py anheben.", line=1,
        )
        return None
    body = text[end + 5 :]
    return frontmatter, body, text[: end + 5].count("\n")


def _parse_basis(
    collector: ErrorCollector, rel_path: str, raw: object, section_names: list[str]
) -> tuple[InputBasis, dict[str, InputBasis]]:
    if not isinstance(raw, dict):
        collector.blocker(
            "INP_BASIS_MISSING", Stage.INPUT, rel_path,
            "'unit_input_basis' fehlt oder ist kein Mapping.",
            "Migration scripts/committee_matcher_migrate_inputs.py --apply ausfuehren.",
        )
        return InputBasis.JURISDICTION_TEXT, {}

    committee_raw = str(raw.get("committee", ""))
    if committee_raw != InputBasis.JURISDICTION_TEXT.value:
        collector.blocker(
            "INP_BASIS_COMMITTEE", Stage.INPUT, rel_path,
            f"unit_input_basis.committee={committee_raw!r} statt 'jurisdiction_text'.",
            "Committee-Basis ist im Inputvertrag immer jurisdiction_text.",
        )

    sub_raw = raw.get("subcommittees")
    if sub_raw in (None, ""):
        sub_raw = {}
    if not isinstance(sub_raw, dict):
        collector.blocker(
            "INP_BASIS_SUBCOMMITTEES", Stage.INPUT, rel_path,
            "'unit_input_basis.subcommittees' ist kein Mapping.",
            "Block gemaess Inputvertrag schreiben.",
        )
        return InputBasis.JURISDICTION_TEXT, {}

    basis: dict[str, InputBasis] = {}
    for name, value in sub_raw.items():
        try:
            basis[str(name)] = InputBasis(str(value))
        except ValueError:
            collector.blocker(
                "INP_BASIS_VALUE", Stage.INPUT, rel_path,
                f"Unbekannte Basis {value!r} fuer {name!r}.",
                "Zulaessig sind jurisdiction_text und name_only.",
            )
    missing = [name for name in section_names if name not in basis]
    extra = [name for name in basis if name not in section_names]
    if missing:
        collector.blocker(
            "INP_BASIS_KEY_MISMATCH", Stage.INPUT, rel_path,
            f"Ohne Basiseintrag: {', '.join(missing[:3])}.",
            "Fuer jeden H2-Abschnitt einen Basiswert fuehren.",
        )
    if extra:
        collector.blocker(
            "INP_BASIS_KEY_MISMATCH", Stage.INPUT, rel_path,
            f"Basiseintrag ohne Abschnitt: {', '.join(extra[:3])}.",
            "Verwaiste Basiseintraege entfernen.",
        )
    return InputBasis.JURISDICTION_TEXT, basis


def _foreign_instruction_findings(
    collector: ErrorCollector, rel_path: str, body: str, comment_start: int | None,
    line_offset: int = 0,
) -> None:
    """Blockiert markierte Fremdanweisungen ausserhalb des Kommentarabschnitts."""
    limit = comment_start if comment_start is not None else len(body)
    scope = body[:limit]
    lowered = scope.lower()
    for marker in FOREIGN_INSTRUCTION_MARKERS:
        start = 0
        while True:
            found = lowered.find(marker, start)
            if found == -1:
                break
            line = line_offset + scope.count("\n", 0, found) + 1
            collector.blocker(
                "INP_FOREIGN_INSTRUCTION", Stage.INPUT, rel_path,
                f"Fremdanweisungsmarker {marker!r} ausserhalb "
                f"von '## {COMMENT_HEADING}'.",
                f"Anweisung in den Abschnitt '{COMMENT_HEADING}' verschieben.",
                line=line,
            )
            start = found + len(marker)


def _parse_body(
    collector: ErrorCollector, rel_path: str, body: str, line_offset: int = 0
) -> tuple[str, tuple[Section, ...]] | None:
    """Zerlegt den Body; ``line_offset`` ist die Zeilenzahl des Frontmatters,
    damit jede Fundstelle die Zeile in der Datei nennt, nicht im Body."""
    headings = H1_RE.findall(body)
    if headings != [JURISDICTION_HEADING]:
        collector.blocker(
            "INP_H1_CONTRACT", Stage.INPUT, rel_path,
            f"Erwartet genau eine H1 '# {JURISDICTION_HEADING}', gefunden {headings}.",
            "H1 auf den Vertragswortlaut bringen.",
        )
        return None

    h1 = H1_RE.search(body)
    assert h1 is not None
    matches = list(H2_RE.finditer(body))
    comment_start: int | None = None
    sections: list[Section] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        name = match.group(1)
        line = line_offset + body.count("\n", 0, match.start()) + 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        if name == COMMENT_HEADING:
            comment_start = match.start()
            continue
        if comment_start is not None:
            collector.blocker(
                "INP_SECTION_AFTER_COMMENTS", Stage.INPUT, rel_path,
                f"Abschnitt {name!r} steht hinter '{COMMENT_HEADING}'.",
                f"'## {COMMENT_HEADING}' als letzten Abschnitt fuehren.", line=line,
            )
            return None
        if name in seen:
            collector.blocker(
                "INP_DUPLICATE_SECTION", Stage.INPUT, rel_path,
                f"Abschnitt {name!r} kommt mehrfach vor.",
                "Doppelte H2-Ueberschrift eindeutig benennen.", line=line,
            )
            return None
        seen.add(name)
        sections.append(Section(name=name, text=body[match.end() : end], line=line))

    first = matches[0].start() if matches else len(body)
    preamble = body[h1.end() : first]
    _foreign_instruction_findings(
        collector, rel_path, body, comment_start, line_offset
    )
    return preamble, tuple(sections)


def _check_orphan_sources(
    collector: ErrorCollector,
    roles: dict[str, CorpusRole],
    documents: "list[CommitteeDocument]",
) -> None:
    """Jede referenzierbare Quelle muss von mindestens einer Mapping-MD genutzt werden.

    Eine verwaiste Quelle heisst: entweder fehlt eine Referenz im Frontmatter
    oder die Datei gehoert nicht in den Corpus. Beides ist eine bewusste
    Entscheidung und darf nicht still bleiben.
    """
    referenced = {ref for document in documents for ref in document.source_files}
    orphans = sorted(
        rel for rel, role in roles.items()
        if role in REFERENCEABLE_ROLES and rel not in referenced
    )
    for rel in orphans:
        collector.blocker(
            "INP_SOURCE_ORPHAN", Stage.INPUT, rel,
            "Referenzierbare Quelldatei wird von keiner Mapping-MD referenziert.",
            "'source_files' der zugehoerigen Mapping-MD ergaenzen oder Datei "
            "aus dem Corpus nehmen.",
        )


def load_documents(
    collector: ErrorCollector,
    settings: OfflineSettings,
    input_root: Path = paths.INPUT_ROOT,
) -> CorpusLoadResult:
    """Laedt und prueft alle ``mapping_input``-Dokumente des Corpus."""
    roles = verify_manifest(collector, input_root)
    documents: list[CommitteeDocument] = []
    skipped: list[str] = []

    for rel, role in sorted(roles.items()):
        if role is not CorpusRole.MAPPING_INPUT:
            skipped.append(rel)
            continue
        path = input_root / rel
        raw = path.read_bytes()
        if len(raw) > settings.max_file_bytes:
            collector.blocker(
                "INP_FILE_TOO_LARGE", Stage.INPUT, rel,
                f"Datei ueberschreitet {settings.max_file_bytes} Bytes.",
                "Datei kuerzen oder Grenze in config/settings.py anheben.",
            )
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            collector.blocker(
                "INP_ENCODING", Stage.INPUT, rel,
                f"Datei ist nicht UTF-8-dekodierbar (Byte {exc.start}).",
                "Datei als UTF-8 speichern.",
            )
            continue

        split = _split_frontmatter(collector, rel, text, settings)
        if split is None:
            continue
        frontmatter, body, body_line_offset = split
        try:
            data = yaml.safe_load(frontmatter)
        except yaml.YAMLError as exc:
            collector.blocker(
                "INP_FRONTMATTER_YAML", Stage.INPUT, rel,
                f"Frontmatter ist kein gueltiges YAML ({exc.__class__.__name__}).",
                "YAML-Syntax korrigieren.", line=2,
            )
            continue
        if not isinstance(data, dict):
            collector.blocker(
                "INP_FRONTMATTER_TYPE", Stage.INPUT, rel,
                "Frontmatter ist kein Mapping.",
                "Frontmatter als 'key: value'-Mapping schreiben.", line=2,
            )
            continue

        missing = [f for f in REQUIRED_FIELDS if not str(data.get(f, "")).strip()]
        if missing:
            collector.blocker(
                "INP_FIELD_MISSING", Stage.INPUT, rel,
                f"Pflichtfelder fehlen: {', '.join(missing)}.",
                "Frontmatter gemaess Inputvertrag vervollstaendigen.", line=2,
            )
            continue

        _check_source_metadata(collector, rel, data)

        chamber = str(data["chamber"]).strip()
        if chamber not in CHAMBERS:
            collector.blocker(
                "INP_CHAMBER_UNKNOWN", Stage.INPUT, rel,
                f"Unbekannte Kammer {chamber!r}.",
                f"Zulaessig sind {', '.join(CHAMBERS)}.", line=2,
            )
            continue
        stem = Path(rel).name
        if not stem.startswith(f"{chamber}_"):
            collector.blocker(
                "INP_CHAMBER_MISMATCH", Stage.INPUT, rel,
                f"Kammer {chamber!r} passt nicht zum Dateinamen.",
                "Widerspruechliche Identitaet aufloesen; der Parser raet nicht.", line=2,
            )
            continue

        parsed = _parse_body(collector, rel, body, body_line_offset)
        if parsed is None:
            continue
        preamble, sections = parsed

        if not normalize_text(preamble):
            collector.blocker(
                "INP_COMMITTEE_TEXT_MISSING", Stage.INPUT, rel,
                "Kein Jurisdiktionstext auf Committee-Ebene.",
                "Committee-Text unter der H1 ergaenzen; die Basis ist "
                "vertraglich jurisdiction_text.",
            )
            continue

        committee_basis, sub_basis = _parse_basis(
            collector, rel, data.get("unit_input_basis"), [s.name for s in sections]
        )
        _check_evidence_length(
            collector, rel, preamble, sections, sub_basis, settings
        )
        source_files = _check_source_files(
            collector, rel, data.get("source_files"), roles, input_root
        )

        documents.append(
            CommitteeDocument(
                path=path,
                rel_path=rel,
                chamber=chamber,
                committee=str(data["committee"]).strip(),
                committee_type=str(data["committee_type"]).strip(),
                source_files=source_files,
                committee_basis=committee_basis,
                subcommittee_basis=sub_basis,
                preamble=preamble,
                sections=sections,
                source_bytes_sha256=sha256_bytes(raw),
            )
        )

    _check_orphan_sources(collector, roles, documents)

    return CorpusLoadResult(
        documents=tuple(documents),
        manifest_files=len(roles),
        input_root=input_root,
        skipped=tuple(skipped),
    )
