"""
Granularer Reset-Lauf fuer Logs / Caches / Stage-Files / Processed.

Default-Verhalten: Move nach archive/reset_pipeline/reset_<ts>/ (reversibel).
Mit --purge: echtes rm (irreversibel, doppelte Confirmation).

Schutzmechanik:
  - Whitelist statt Blacklist (TARGETS-dict ist explizit)
  - PROTECTED_FILES als zweite Schicht: kuratierte/manuelle Daten werden
    selbst dann nicht angefasst, wenn ein TARGETS-Eintrag sie versehentlich
    matchen wuerde.

Aufruf-Beispiele:
  python scripts/reset_pipeline.py --logs --dry-run
  python scripts/reset_pipeline.py --cache_reset
  python scripts/reset_pipeline.py --rebuild --dry-run   # cache-unabhaengiger import_trades-Rebuild
  python scripts/reset_pipeline.py --all
  python scripts/reset_pipeline.py --processed --purge
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import settings


# ---------------------------------------------------------------------------
# Pfad-Resolution
# ---------------------------------------------------------------------------

DATA_CACHE = BASE_DIR / settings.DATA_CACHE_PATH
DATA_PROCESSED = BASE_DIR / settings.DATA_PROCESSED_PATH
DATA_MAPPINGS = BASE_DIR / settings.DATA_MAPPINGS_PATH
DATA_CONGRESS_API = BASE_DIR / settings.DATA_CONGRESS_API_PATH
LOGS = BASE_DIR / settings.LOGS_PATH
ARCHIVE_RESET_ROOT = BASE_DIR / "archive" / "reset_pipeline"


# ---------------------------------------------------------------------------
# Whitelist: was darf weg
# ---------------------------------------------------------------------------
# Eintraege koennen Globs (mit '*') oder konkrete Pfade sein.
# Verzeichnisse als Path-Objekt → werden komplett verschoben.

TARGETS: dict[str, list[Path | str]] = {
    "logs": [
        str(LOGS / "*.log"),
        LOGS / "debug_tesseract",
    ],
    "stage-files": [
        str(DATA_PROCESSED / "*_stage.csv"),
        DATA_PROCESSED / "pipeline_state.json",
        DATA_PROCESSED / "duplicate_inspection_sample.csv",
        # Resume-Checkpoint des Member-Enrichments: ein stale Checkpoint nach
        # einem Abbruch laesst die Pipeline einen Teil-Lauf fortsetzen statt neu
        # zu starten -> fuer einen frischen Rebuild zwingend entfernen.
        DATA_PROCESSED / ".enrichment_checkpoint.pkl",
    ],
    "cache_reset": [
        # Gesamtes Cache-Verzeichnis (Wildcard, zukunftssicher): deckt
        # ticker_type_cache.db (OpenFIGI), edgar_cik_map.json (SEC EDGAR) und
        # price_history.parquet (Post-Trade-Preise) sowie jeden kuenftigen
        # Cache automatisch ab. .gitkeep ist via PROTECTED_FILES geschuetzt.
        str(DATA_CACHE / "*"),
        # yfinance-Sektor-Caches liegen unter data/mappings/, nicht data/cache/:
        DATA_MAPPINGS / settings.TICKER_INDUSTRY_CACHE_FILE,
        DATA_MAPPINGS / settings.TICKER_0_CACHE_FILE,
    ],
    "processed": [
        DATA_PROCESSED / settings.TRADES_MASTER_CSV,
        DATA_PROCESSED / settings.TRADES_MASTER_COMPLETE_CSV,
        DATA_PROCESSED / settings.MANUAL_REVIEW_CSV,
        DATA_PROCESSED / "unmatched_names.csv",
        DATA_PROCESSED / "unmatched_preferred.csv",
        DATA_PROCESSED / "delisted_trades.csv",
        DATA_PROCESSED / "ticker_liste_index.csv",
        # Output von scripts/analyse_event_study.py — ohne Reset ueberlebt er
        # jeden --rebuild als Stale-Artefakt.
        DATA_PROCESSED / "ar_outliers.csv",
        # Suspicious-Persistenz: suspicious_events.csv ist append-only und wird
        # von import_trades.py NIE getruncatet (full+delta nur append+dedup) ->
        # fuer einen sauberen Rebuild zwingend zuruecksetzen. Die beiden Views
        # werden daraus materialisiert und der Konsistenz halber mitentfernt.
        DATA_PROCESSED / settings.SUSPICIOUS_EVENTS_FILE,
        DATA_PROCESSED / settings.SUSPICIOUS_ASSETS_FILE,
        DATA_PROCESSED / settings.SUSPICIOUS_MEMBERS_FILE,
    ],
    "congress-api": [
        DATA_CONGRESS_API / "bills_list.json",
        DATA_CONGRESS_API / "bill_details.json",
        DATA_CONGRESS_API / "bill_actions.json",
        DATA_CONGRESS_API / "bill_committees.json",
        DATA_CONGRESS_API / "bill_summaries.json",
        DATA_CONGRESS_API / "bill_subjects.json",
        DATA_CONGRESS_API / "fetch_checkpoint.json",
        DATA_CONGRESS_API / "fetch_log.json",
    ],
}


# ---------------------------------------------------------------------------
# Defense-in-Depth: niemals anfassen
# ---------------------------------------------------------------------------

PROTECTED_FILES: frozenset[Path] = frozenset(
    p.resolve()
    for p in [
        DATA_MAPPINGS / settings.NAME_ALIASES_FILE,
        DATA_MAPPINGS / settings.HOUSE_COMMITTEES_FILE,
        DATA_MAPPINGS / settings.SENATE_COMMITTEES_FILE,
        DATA_MAPPINGS / settings.COMMITTEE_MAPPING_FILE,
        DATA_MAPPINGS / "subcommittee_region_mapping.csv",
        DATA_MAPPINGS / "subcommittee_sector_industry_mapping.xlsx",
        DATA_MAPPINGS / "yf_sectors_with_industries.json",
        DATA_MAPPINGS / "yf_sectors_with_industries.xlsx",
        DATA_MAPPINGS / "legislative_subject_mapping_complete.csv",
        LOGS / "house_pdf_extractor_done.csv",
        # Verzeichnis-Platzhalter: darf von der data/cache/*-Wildcard nicht
        # mitgeloescht werden (sonst verschwindet das versionierte Cache-Dir).
        DATA_CACHE / ".gitkeep",
    ]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _rel(path: Path) -> str:
    """Pfad relativ zu BASE_DIR (fuer Anzeige)."""
    try:
        return str(path.resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def _human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def _entry_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                fp = Path(root) / f
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
        return total
    return 0


def _entry_descr(path: Path) -> str:
    if path.is_dir():
        n = sum(1 for _ in path.rglob("*") if _.is_file())
        return f"(Verzeichnis, {n} Files, {_human_size(_entry_size(path))})"
    if path.is_file():
        return _human_size(_entry_size(path))
    return "(nicht vorhanden)"


def resolve_targets(flag: str) -> list[Path]:
    """Globs auflösen, nur existente Pfade zurückgeben."""
    out: list[Path] = []
    for entry in TARGETS[flag]:
        if isinstance(entry, str) and "*" in entry:
            parent = Path(entry).parent
            pattern = Path(entry).name
            if parent.exists():
                out.extend(sorted(parent.glob(pattern)))
        else:
            p = Path(entry)
            if p.exists():
                out.append(p)
    return out


def filter_protected(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """Liefert (allowed, blocked)."""
    allowed: list[Path] = []
    blocked: list[Path] = []
    for p in paths:
        if p.resolve() in PROTECTED_FILES:
            blocked.append(p)
        else:
            allowed.append(p)
    return allowed, blocked


# ---------------------------------------------------------------------------
# Aktionen
# ---------------------------------------------------------------------------

def archive_move(
    paths: list[Path], archive_root: Path
) -> tuple[int, int, list[tuple[Path, Path]]]:
    """Verschiebt jeden Pfad nach archive_root unter Beibehaltung der Struktur.

    Returns: (n_ok, n_err, manifest_entries)
    """
    if not _is_under(archive_root, BASE_DIR):
        raise RuntimeError(
            f"archive_root {archive_root} liegt nicht unter BASE_DIR {BASE_DIR}"
        )
    archive_root.mkdir(parents=True, exist_ok=True)

    manifest: list[tuple[Path, Path]] = []
    n_ok = 0
    n_err = 0
    total = len(paths)
    for i, src in enumerate(paths, start=1):
        try:
            rel = src.resolve().relative_to(BASE_DIR)
        except ValueError:
            print(f"      [X] {_rel(src)}: ausserhalb BASE_DIR — SKIP")
            n_err += 1
            continue
        dst = archive_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        size = _entry_size(src)
        descr = "Verzeichnis" if src.is_dir() else _human_size(size)
        print(f"      [{i}/{total}] {_rel(src)}")
        print(f"            -> {_rel(dst)}")
        try:
            t0 = datetime.now()
            shutil.move(str(src), str(dst))
            dt = (datetime.now() - t0).total_seconds()
            print(f"            [OK] {descr} in {dt:.2f}s")
            n_ok += 1
            manifest.append((src, dst))
        except Exception as exc:
            print(f"            [X] FEHLER: {exc}")
            n_err += 1
            # Abbruch bei Fehler — User soll sehen was passiert ist
            raise

    return n_ok, n_err, manifest


def purge_rm(paths: list[Path]) -> tuple[int, int]:
    n_ok = 0
    n_err = 0
    total = len(paths)
    for i, p in enumerate(paths, start=1):
        size = _entry_size(p)
        descr = "Verzeichnis" if p.is_dir() else _human_size(size)
        print(f"      [{i}/{total}] {_rel(p)}  ({descr})")
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            print(f"            [OK] geloescht")
            n_ok += 1
        except Exception as exc:
            print(f"            [X] FEHLER: {exc}")
            n_err += 1
            raise
    return n_ok, n_err


def write_manifest(archive_root: Path, manifest: list[tuple[Path, Path]],
                   active_flags: list[str]) -> None:
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest_path = archive_root / "_manifest.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with manifest_path.open("w", encoding="utf-8") as f:
        f.write(f"# reset_pipeline.py manifest\n")
        f.write(f"# timestamp: {ts}\n")
        f.write(f"# flags:     {' '.join(active_flags)}\n")
        f.write(f"# entries:   {len(manifest)}\n")
        f.write("# -- src -> dst (relativ zu BASE_DIR) --\n")
        for src, dst in manifest:
            f.write(f"{_rel(src)}  ->  {_rel(dst)}\n")
    print(f"\n[Manifest] {_rel(manifest_path)} geschrieben")


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

def _confirm_simple(label: str) -> bool:
    ans = input(f">>> {label} [j/N] ").strip().lower()
    return ans == "j"


def _confirm_typed(label: str, expected: str) -> bool:
    ans = input(f">>> {label} (tippe '{expected}'): ").strip()
    return ans == expected


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def print_preview(by_flag: dict[str, tuple[list[Path], list[Path]]],
                  archive_root: Path, dry_run: bool, purge: bool) -> int:
    """Druckt Vorschau, returnt Gesamt-Bytes."""
    total_bytes = 0
    print("\n[1/2] Sammle Targets ...")
    for flag, (allowed, blocked) in by_flag.items():
        allowed_size = sum(_entry_size(p) for p in allowed)
        total_bytes += allowed_size
        print(f"      [OK] Flag --{flag}: {len(allowed)} Eintraege, "
              f"{len(blocked)} protected (gefiltert)")
    print(f"      Gesamt: {sum(len(a) for a, _ in by_flag.values())} Eintraege, "
          f"{_human_size(total_bytes)}")

    print("\n[2/2] Vorschau:")
    for flag, (allowed, blocked) in by_flag.items():
        if not allowed and not blocked:
            continue
        print(f"      [{flag.upper()}]")
        for p in allowed:
            print(f"        {_rel(p):<55s} {_entry_descr(p)}")
        for p in blocked:
            print(f"        [SKIP] {_rel(p):<48s} (protected, bleibt)")

    if dry_run:
        print("\n[DRY-RUN] Keine Aktion ausgefuehrt.")
    else:
        action = "PURGE (rm)" if purge else f"MOVE -> {_rel(archive_root)}/"
        print(f"\n      Aktion: {action}")
    return total_bytes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--logs", action="store_true",
                        help="logs/*.log + logs/debug_tesseract/")
    parser.add_argument("--stage-files", action="store_true",
                        help="*_stage.csv, pipeline_state.json, duplicate_inspection_sample.csv, .enrichment_checkpoint.pkl")
    parser.add_argument("--cache_reset", action="store_true",
                        help="gesamtes data/cache/* (OpenFIGI-DB, EDGAR-CIK, Post-Trade-Preise) + ticker_industry/0_cache.json")
    parser.add_argument("--processed", action="store_true",
                        help="trades_master*.csv + manual_review + unmatched* + delisted* + suspicious_* (DESTRUKTIV)")
    parser.add_argument("--congress-api", action="store_true",
                        help="data/congress_api/*.json (Bill-Daten + Checkpoint, ~7-8h Re-Fetch noetig, DESTRUKTIV)")
    parser.add_argument("--rebuild", action="store_true",
                        help="cache-unabhaengiger import_trades-Rebuild: stage-files + cache_reset + processed (DESTRUKTIV)")
    parser.add_argument("--all", action="store_true",
                        help="alle fuenf oben kombiniert (inkl. logs + congress-api)")
    parser.add_argument("--dry-run", action="store_true",
                        help="keine Aktion, nur Vorschau")
    parser.add_argument("--purge", action="store_true",
                        help="echtes rm statt Move-to-Archive (irreversibel)")
    args = parser.parse_args()

    active_flags: list[str] = []
    if args.all:
        active_flags = ["logs", "stage-files", "cache_reset", "processed", "congress-api"]
    elif args.rebuild:
        # Cache-unabhaengiger import_trades-Rebuild: alle generierten Caches +
        # Stage-Dateien + Processed (inkl. append-only suspicious_events.csv),
        # OHNE logs/congress-api — Letztere gehoeren nicht zur import_trades-Pipeline.
        active_flags = ["stage-files", "cache_reset", "processed"]
    else:
        if args.logs:
            active_flags.append("logs")
        if args.stage_files:
            active_flags.append("stage-files")
        if args.cache_reset:
            active_flags.append("cache_reset")
        if args.processed:
            active_flags.append("processed")
        if args.congress_api:
            active_flags.append("congress-api")

    if not active_flags:
        parser.print_help()
        print("\n[X] Mindestens ein Flag erforderlich "
              "(--logs / --stage-files / --cache_reset / --processed / --congress-api / --rebuild / --all)")
        return 1

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_root = ARCHIVE_RESET_ROOT / f"reset_{ts}"

    # Header
    print("=" * 60)
    print(f"  reset_pipeline.py — Reset-Lauf {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print(f"  Modus: DRY-RUN (keine Aktion)")
    elif args.purge:
        print(f"  Modus: PURGE (echtes rm, irreversibel)")
    else:
        print(f"  Modus: MOVE -> {_rel(archive_root)}/")
    print(f"  Aktive Flags: {' '.join('--' + f for f in active_flags)}")
    print("=" * 60)

    # Sammle pro Flag (allowed, blocked)
    by_flag: dict[str, tuple[list[Path], list[Path]]] = {}
    for flag in active_flags:
        resolved = resolve_targets(flag)
        allowed, blocked = filter_protected(resolved)
        by_flag[flag] = (allowed, blocked)

    print_preview(by_flag, archive_root, args.dry_run, args.purge)

    if args.dry_run:
        return 0

    all_paths: list[Path] = [p for allowed, _ in by_flag.values() for p in allowed]
    if not all_paths:
        print("\n[i] Nichts zu tun — keine passenden Dateien gefunden.")
        return 0

    # Confirmation-Stufen
    needs_typed = ("processed" in active_flags) or ("congress-api" in active_flags) or args.all
    if needs_typed:
        if not _confirm_typed("Bestaetigung erforderlich", "JA"):
            print("[X] Abgebrochen.")
            return 1
    else:
        if not _confirm_simple("Bestaetigung erforderlich: durchfuehren?"):
            print("[X] Abgebrochen.")
            return 1

    if args.purge:
        if not _confirm_typed("Wirklich PURGE (kein Archiv, kein Undo)?", "PURGE"):
            print("[X] Abgebrochen.")
            return 1

    # Aktion
    print("\n[Aktion] " + ("Loesche" if args.purge else "Verschiebe") + " Dateien ...")
    n_ok = 0
    n_err = 0
    manifest: list[tuple[Path, Path]] = []
    try:
        if args.purge:
            n_ok, n_err = purge_rm(all_paths)
        else:
            n_ok, n_err, manifest = archive_move(all_paths, archive_root)
            write_manifest(archive_root, manifest, active_flags)
    except Exception as exc:
        print(f"\n[X] Lauf abgebrochen: {exc}")
        print(f"    Bisher OK: {n_ok}, Fehler: {n_err}")
        if not args.purge and manifest:
            write_manifest(archive_root, manifest, active_flags)
        return 2

    # Zusammenfassung
    blocked_total = sum(len(b) for _, b in by_flag.values())
    print("\n" + "=" * 60)
    print("  Zusammenfassung")
    print("=" * 60)
    action_word = "Geloescht" if args.purge else "Verschoben"
    print(f"  {action_word}:        {n_ok} / {len(all_paths)}")
    print(f"  Uebersprungen:    {blocked_total}  (protected)")
    print(f"  Fehler:           {n_err}")
    if not args.purge:
        print(f"  Archiv:           {_rel(archive_root)}/")
        print(f"  Manifest:         _manifest.txt ({len(manifest)} Eintraege)")
    print("=" * 60)
    print("  [OK] Reset abgeschlossen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
