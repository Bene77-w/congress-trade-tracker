"""Pfadwache des Committee Matchers V2.

Der Matcher liest die Inputbaseline ausschliesslich read-only und schreibt
ausschliesslich unter ``data/committee_matcher_v2/`` (Plan 2.1 und 2.8). Diese
Wache setzt beides technisch durch, statt sich auf Disziplin zu verlassen:

- ``resolve_within`` loest jeden vom Input oder von der CLI kommenden Pfad
  gegen eine erlaubte Wurzel auf und blockiert absolute Pfade, ``..``-Segmente
  und Symlink-Ausbrueche;
- ``assert_readable`` erlaubt Lesen nur unter den bekannten Lesewurzeln;
- ``assert_writable`` erlaubt Schreiben nur unter der Artefaktwurzel und
  blockiert den Inputroot sowie jeden Pfad aus der P0-Schutzbaseline.

Die Schutzbaseline stammt aus ``data/config/committee_matcher_protected_baseline.yaml``
und ist bewusst matcherbezogen; die globale Liste aus ``reset_pipeline.py``
wird nicht als Matcher-Scope verwendet. Fehlt die Baseline, blockiert die
Wache (fail closed) statt ungeschuetzt zu schreiben.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from .errors import Finding, MatcherError, Severity, Stage
from .status import ExitCode

BASE_DIR = Path(__file__).resolve().parent.parent

#: Kanonische Inputbaseline (read-only).
INPUT_ROOT = BASE_DIR / "data" / "committee_matcher_inputs"
#: Einzige Schreibwurzel fuer generierte Artefakte.
ARTIFACT_ROOT = BASE_DIR / "data" / "committee_matcher_v2"
#: Versionierte Kontrollinputs (Roster-Regeln, Schutzbaseline).
CONFIG_ROOT = BASE_DIR / "data" / "config"
#: Editierbare Systemprompts; die Dateien entstehen in P5.
PROMPT_ROOT = CONFIG_ROOT / "committee_matcher_prompts"
#: Produktive Roster-Quellen (read-only, Plan 2.3).
MAPPING_ROOT = BASE_DIR / "data" / "mappings"

PROTECTED_BASELINE_PATH = CONFIG_ROOT / "committee_matcher_protected_baseline.yaml"
CORPUS_MANIFEST_PATH = INPUT_ROOT / "corpus_manifest.yaml"
ROSTER_RULES_PATH = CONFIG_ROOT / "committee_matcher_roster_rules.yaml"

#: Wurzeln, aus denen der Matcher lesen darf.
READ_ROOTS: tuple[Path, ...] = (
    INPUT_ROOT,
    ARTIFACT_ROOT,
    CONFIG_ROOT,
    MAPPING_ROOT,
)


class PathGuardError(MatcherError):
    """Blockierter Pfadzugriff; traegt den zugehoerigen Befund."""


def _blocked(code: str, obj: str, message: str, hint: str) -> PathGuardError:
    finding = Finding(
        severity=Severity.BLOCKER,
        code=code,
        stage=Stage.INPUT,
        obj=obj,
        message=message,
        hint=hint,
        exit_code=ExitCode.PREFLIGHT_BLOCKED,
    )
    return PathGuardError([finding], f"{code}: {obj}")


def relative_to_base(path: Path) -> str:
    """Projektrelativer Pfad fuer Berichte; ausserhalb liegende bleiben absolut."""
    try:
        return str(Path(path).resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def _resolved(path: Path) -> Path:
    """Loest Symlinks auf, ohne Existenz zu verlangen."""
    return Path(path).expanduser().resolve()


def is_within(root: Path, candidate: Path) -> bool:
    """Prueft Enthaltensein nach Symlinkaufloesung beider Seiten."""
    resolved_root = _resolved(root)
    resolved = _resolved(candidate)
    return resolved == resolved_root or resolved_root in resolved.parents


def resolve_within(root: Path, *parts: str | Path, must_exist: bool = False) -> Path:
    """Loest ``parts`` relativ zu ``root`` auf und blockiert jeden Ausbruch.

    Blockiert absolute Segmente, ``..``-Segmente, leere Segmente und Pfade, die
    nach Symlinkaufloesung ausserhalb von ``root`` liegen.
    """
    root_resolved = _resolved(root)
    relative = Path()
    for part in parts:
        candidate = Path(part)
        if candidate.is_absolute() or (candidate.drive or candidate.root):
            raise _blocked(
                "PATH_ABSOLUTE",
                str(part),
                "Absoluter Pfad in einer relativen Pfadangabe.",
                f"Pfad relativ zu {relative_to_base(root_resolved)} angeben.",
            )
        for segment in candidate.parts:
            if segment in ("..", ""):
                raise _blocked(
                    "PATH_TRAVERSAL",
                    str(part),
                    "Pfadtraversal in einer Pfadangabe.",
                    "Segment '..' entfernen; Pfade bleiben unterhalb der Wurzel.",
                )
        relative = relative / candidate

    target = (root_resolved / relative).resolve()
    if not is_within(root_resolved, target):
        raise _blocked(
            "PATH_ESCAPE",
            relative_to_base(target),
            "Pfad liegt nach Symlinkaufloesung ausserhalb der erlaubten Wurzel.",
            f"Nur Pfade unterhalb von {relative_to_base(root_resolved)} verwenden.",
        )
    if must_exist and not target.exists():
        raise _blocked(
            "PATH_MISSING",
            relative_to_base(target),
            "Erwarteter Pfad existiert nicht.",
            "Pfad pruefen oder die vorgelagerte Stage erneut ausfuehren.",
        )
    return target


def assert_readable(path: Path) -> Path:
    """Erlaubt Lesen nur unterhalb der bekannten Lesewurzeln."""
    target = _resolved(path)
    if not any(is_within(root, target) for root in READ_ROOTS):
        raise _blocked(
            "PATH_READ_DENIED",
            relative_to_base(target),
            "Lesezugriff ausserhalb der erlaubten Wurzeln.",
            "Nur Inputbaseline, Artefakte, data/config und data/mappings lesen.",
        )
    return target


#: Kopffelder, die die P0-Schutzbaseline tragen muss. Eine syntaktisch gueltige,
#: aber fremde oder leere Datei darf die Schutzmenge nicht stillschweigend
#: entleeren - sonst waere fail closed nur noch auf dem Papier vorhanden.
PROTECTED_BASELINE_VERSION = "protected-paths-v1"
PROTECTED_BASELINE_SCOPE = "committee_matcher_v2"
_BASELINE_ENTRY_FIELDS = ("path", "bytes", "sha256")


def _baseline_malformed(baseline: Path, detail: str) -> PathGuardError:
    return _blocked(
        "PATH_BASELINE_MALFORMED",
        relative_to_base(baseline),
        f"Schutzbaseline ist unbrauchbar: {detail}",
        "Baseline mit scripts/committee_matcher_baseline.py protect --write neu erzeugen.",
    )


@lru_cache(maxsize=4)
def _protected_paths_cached(baseline: Path, fingerprint: tuple[float, int]) -> frozenset[Path]:
    """Cache je Baselinedatei; ``fingerprint`` aus mtime und Groesse invalidiert."""
    del fingerprint  # nur Cacheschluessel
    data = yaml.safe_load(baseline.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise _baseline_malformed(baseline, "kein YAML-Mapping.")
    if data.get("baseline_version") != PROTECTED_BASELINE_VERSION:
        raise _baseline_malformed(
            baseline,
            f"baseline_version={data.get('baseline_version')!r} statt "
            f"{PROTECTED_BASELINE_VERSION!r}.",
        )
    if data.get("scope") != PROTECTED_BASELINE_SCOPE:
        raise _baseline_malformed(
            baseline, f"scope={data.get('scope')!r} statt {PROTECTED_BASELINE_SCOPE!r}."
        )
    entries = data.get("paths")
    if not isinstance(entries, list) or not entries:
        raise _baseline_malformed(baseline, "'paths' fehlt oder ist leer.")

    resolved: set[Path] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise _baseline_malformed(baseline, "Eintrag ist kein Mapping.")
        missing = [field for field in _BASELINE_ENTRY_FIELDS if not entry.get(field)]
        if missing:
            raise _baseline_malformed(
                baseline, f"Eintrag ohne {', '.join(missing)}."
            )
        raw = str(entry["path"])
        if raw.startswith("/") or ".." in Path(raw).parts:
            raise _baseline_malformed(baseline, f"unzulaessiger Pfad {raw!r}.")
        resolved.add((BASE_DIR / raw).resolve())
    return frozenset(resolved)


def protected_paths() -> frozenset[Path]:
    """Matcherbezogene Produktionspfade aus der P0-Schutzbaseline.

    Fehlt die Baseline, blockiert der Zugriff: ohne Schutzliste darf der
    Matcher nicht schreiben.
    """
    if not PROTECTED_BASELINE_PATH.exists():
        raise _blocked(
            "PATH_BASELINE_MISSING",
            relative_to_base(PROTECTED_BASELINE_PATH),
            "Schutzbaseline der Produktionspfade fehlt.",
            "scripts/committee_matcher_baseline.py protect --write ausfuehren.",
        )
    stat = PROTECTED_BASELINE_PATH.stat()
    try:
        return _protected_paths_cached(
            PROTECTED_BASELINE_PATH, (stat.st_mtime, stat.st_size)
        )
    except yaml.YAMLError as exc:
        raise _blocked(
            "PATH_BASELINE_MALFORMED",
            relative_to_base(PROTECTED_BASELINE_PATH),
            f"Schutzbaseline ist kein gueltiges YAML ({exc.__class__.__name__}).",
            "Baseline mit scripts/committee_matcher_baseline.py protect --write neu erzeugen.",
        ) from exc


def assert_writable(path: Path) -> Path:
    """Erlaubt Schreiben nur unterhalb der Artefaktwurzel.

    Der Inputroot und jeder Pfad der Schutzbaseline sind ausgeschlossen, auch
    wenn ein Aufrufer sie versehentlich als Ziel uebergibt.
    """
    target = _resolved(path)
    if is_within(INPUT_ROOT, target):
        raise _blocked(
            "PATH_INPUT_READONLY",
            relative_to_base(target),
            "Schreibzugriff auf die Inputbaseline.",
            "Inputs sind read-only; Artefakte unter data/committee_matcher_v2/ ablegen.",
        )
    for protected in protected_paths():
        if target == protected or is_within(protected, target):
            raise _blocked(
                "PATH_PROTECTED",
                relative_to_base(target),
                "Schreibzugriff auf einen geschuetzten Produktionspfad.",
                "V2 schreibt nie in den Produktionsmaster; Promotion ist ausserhalb des Scopes.",
            )
    if not is_within(ARTIFACT_ROOT, target):
        raise _blocked(
            "PATH_WRITE_DENIED",
            relative_to_base(target),
            "Schreibzugriff ausserhalb der Artefaktwurzel.",
            f"Nur unterhalb von {relative_to_base(ARTIFACT_ROOT)} schreiben.",
        )

    raw = Path(path)
    if raw.is_symlink():
        raise _blocked(
            "PATH_SYMLINK_TARGET",
            relative_to_base(raw),
            "Schreibziel ist ein Symlink.",
            "Symlink entfernen; Artefakte werden immer als echte Datei geschrieben.",
        )
    if raw.exists() and not raw.is_dir() and raw.stat().st_nlink > 1:
        raise _blocked(
            "PATH_HARDLINK_TARGET",
            relative_to_base(raw),
            "Schreibziel ist ein Hardlink auf eine weitere Datei.",
            "Datei entfernen; ein Hardlink wuerde die Schreibgrenze umgehen.",
        )
    return target


def artifact_path(*parts: str | Path, create_parents: bool = False) -> Path:
    """Liefert einen geprueften Artefaktpfad; legt Elternordner nur auf Wunsch an."""
    target = assert_writable(resolve_within(ARTIFACT_ROOT, *parts))
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def write_text_atomic(path: Path, text: str) -> Path:
    """Schreibt ``text`` geprueft und atomar.

    Einziger Schreibweg des Pakets: die Pfadwache laeuft unmittelbar vor dem
    Schreiben, die Temporaerdatei liegt im selben Verzeichnis (gleiches
    Dateisystem, damit ``os.replace`` atomar bleibt) und ein vorhandenes
    Symlink- oder Hardlinkziel blockiert. Damit bleibt zwischen Pruefung und
    Schreiben kein Fenster fuer einen untergeschobenen Pfad.
    """
    target = assert_writable(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.is_symlink():
        raise _blocked(
            "PATH_SYMLINK_TARGET",
            relative_to_base(temporary),
            "Temporaeres Schreibziel ist ein Symlink.",
            "Datei entfernen; der Schreibweg folgt keinen Symlinks.",
        )
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, target)
    return target


def input_path(*parts: str | Path, must_exist: bool = True) -> Path:
    """Liefert einen geprueften Lesepfad unterhalb der Inputbaseline."""
    return assert_readable(resolve_within(INPUT_ROOT, *parts, must_exist=must_exist))
