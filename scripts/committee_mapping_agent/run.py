"""CLI-Orchestrator des Committee-Mapping-Agents.

Pipeline (jede Stufe nutzt/erzeugt Cache, ist also wiederaufsetzbar):

    enrich-taxonomy           # einmalig: Taxonomie-Beschreibungen erzeugen
    research   [--chamber X] [--committee Y] [--force]
    map        [--chamber X] [--committee Y] [--force]
    review     [--chamber X] [--committee Y] [--force]
    export-review             # 3 Review-xlsx schreiben
    # --- User editiert die xlsx (comment-Spalte) ---
    finalize                  # Kommentare einarbeiten -> Staging-CSV
    promote                   # Staging -> Master (mit Timestamp-Backup)

    validate   [--chamber X] [--committee Y]   # Diagnose: Mapping-Caches pruefen

Beispiel E2E-Probelauf (3 Committees):
    python -m scripts.committee_mapping_agent.run research  --chamber House --committee Budget
    python -m scripts.committee_mapping_agent.run map       --chamber House --committee Budget
    python -m scripts.committee_mapping_agent.run review    --chamber House --committee Budget
    python -m scripts.committee_mapping_agent.run export-review
"""
import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import (
    committees,
    config,
    finalize as finalize_mod,
    llm_client,
    mapping,
    research,
    review as review_mod,
    review_xlsx,
    taxonomy,
    validate as validate_mod,
)
from .rowid import COMMITTEE_KEY

logger = logging.getLogger("committee_mapping_agent")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _select(chamber: Optional[str], committee: Optional[str]
            ) -> Iterator[Tuple[str, str, List[str]]]:
    """Liefert (chamber, committee, subcommittees) gemaess Filter."""
    chambers = [chamber] if chamber else ["House", "Senate"]
    for ch in chambers:
        data = committees.load_committees(ch)
        for comm, subs in data.items():
            if committee and comm != committee:
                continue
            yield ch, comm, subs


def _units_to_map(subcommittees: List[str]) -> List[str]:
    """Committees MIT Subcommittees -> nur Subcommittee-Einheiten (Committee-
    Ebene entsteht im Loader durch Aggregation). Committees OHNE -> Committee-
    Ebene (__committee__) als Platzhalter."""
    return list(subcommittees) if subcommittees else [COMMITTEE_KEY]


def _run_parallel(
    label: str,
    tasks: List[Tuple],
    worker: Callable[..., Any],
    max_workers: int,
) -> List[Tuple[Tuple, str]]:
    """Fuehrt worker(*task) im Thread-Pool aus (I/O-bound API-Calls).

    Eine fehlgeschlagene Aufgabe bricht den Lauf NICHT ab — sie wird geloggt
    und gesammelt; die uebrigen laufen weiter (Re-Run holt dank Cache nur die
    Fehlenden nach). Liefert die Liste (task, fehlermeldung).
    """
    failures: List[Tuple[Tuple, str]] = []
    if not tasks:
        return failures
    # Client-Singleton VOR dem Pool initialisieren: vermeidet das Erzeugungs-
    # Race und laesst MissingAPIKeyError frueh + eindeutig hochkommen.
    llm_client.get_client()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker, *task): task for task in tasks}
        for fut in as_completed(futures):
            task = futures[fut]
            try:
                fut.result()
            except Exception as exc:  # Einzelfehler sammeln, Lauf fortsetzen
                logger.error("%s fehlgeschlagen (%s): %s", label,
                             " / ".join(str(p) for p in task[:3]), exc)
                failures.append((task, str(exc)))
    return failures


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_enrich_taxonomy(args: argparse.Namespace) -> int:
    logger.info("Erzeuge angereicherte Taxonomie ...")
    taxonomy.enrich()
    logger.info("Fertig -> %s", config.TAXONOMY_ENRICHED)
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    for ch, comm, subs in _select(args.chamber, args.committee):
        logger.info("Research: %s / %s (%d Subcommittees)", ch, comm, len(subs))
        research.research_committee(ch, comm, subs, force=args.force)
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    tax = taxonomy.load()
    tasks: List[Tuple] = []
    for ch, comm, subs in _select(args.chamber, args.committee):
        rpath = research.cache_path(ch, comm)
        if not os.path.exists(rpath):
            logger.warning("Map: kein Research-Cache fuer %s/%s -> uebersprungen "
                           "(erst 'research' laufen lassen).", ch, comm)
            continue
        with open(rpath, "r", encoding="utf-8") as fh:
            rdata = json.load(fh)
        jurisdiction = rdata.get("jurisdiction", {})
        for unit in _units_to_map(subs):
            tasks.append((ch, comm, unit, jurisdiction.get(unit, {})))

    def _work(ch: str, comm: str, unit: str, entry: Dict[str, Any]) -> None:
        logger.info("Map: %s / %s / %s", ch, comm, unit)
        mapping.map_unit(ch, comm, unit, entry, tax, force=args.force)

    failures = _run_parallel("Map", tasks, _work, config.MAP_CONCURRENCY)
    logger.info("Map fertig: %d Einheit(en) ok, %d fehlgeschlagen.",
                len(tasks) - len(failures), len(failures))
    return 1 if failures else 0


def cmd_review(args: argparse.Namespace) -> int:
    tax = taxonomy.load()
    tasks: List[Tuple] = []
    for ch, comm, subs in _select(args.chamber, args.committee):
        rpath = research.cache_path(ch, comm)
        if not os.path.exists(rpath):
            logger.warning("Review: kein Research-Cache fuer %s/%s.", ch, comm)
            continue
        with open(rpath, "r", encoding="utf-8") as fh:
            jurisdiction = json.load(fh).get("jurisdiction", {})
        mapping_results = []
        for unit in _units_to_map(subs):
            mpath = mapping.cache_path(ch, comm, unit)
            if os.path.exists(mpath):
                with open(mpath, "r", encoding="utf-8") as fh:
                    mapping_results.append(json.load(fh))
        if not mapping_results:
            logger.warning("Review: keine Mapping-Caches fuer %s/%s.", ch, comm)
            continue
        tasks.append((ch, comm, jurisdiction, mapping_results))

    def _work(ch: str, comm: str, jurisdiction: Dict[str, Any],
              mapping_results: List[Dict[str, Any]]) -> None:
        logger.info("Review: %s / %s", ch, comm)
        review_mod.review_committee(ch, comm, jurisdiction, mapping_results,
                                    tax, force=args.force)

    if not config.REVIEW_ENABLED:
        # Pass-Through: keine API-Calls -> sequentiell, ohne Client/Key.
        logger.info("Review deaktiviert (config.REVIEW_ENABLED=False) — "
                    "schreibe Pass-Through-Caches.")
        failures = []
        for task in tasks:
            try:
                _work(*task)
            except Exception as exc:
                logger.error("Review fehlgeschlagen (%s): %s",
                             " / ".join(str(p) for p in task[:2]), exc)
                failures.append((task, str(exc)))
    else:
        failures = _run_parallel("Review", tasks, _work,
                                 config.REVIEW_CONCURRENCY)
    logger.info("Review fertig: %d Committee(s) ok, %d fehlgeschlagen.",
                len(tasks) - len(failures), len(failures))
    return 1 if failures else 0


def _collect_reviewed_rows(chamber: Optional[str], committee: Optional[str]
                           ) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ch, comm, _subs in _select(chamber, committee):
        path = review_mod.cache_path(ch, comm)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            rows.extend(json.load(fh).get("reviewed_rows", []))
    return rows


def cmd_export_review(args: argparse.Namespace) -> int:
    rows = _collect_reviewed_rows(args.chamber, args.committee)
    if not rows:
        logger.warning("Keine Review-Caches gefunden — erst 'review' laufen lassen.")
        return 1
    paths = review_xlsx.export_review(rows)
    logger.info("Review-xlsx geschrieben (%d Zeilen):", len(rows))
    for key, p in paths.items():
        logger.info("  %-7s -> %s", key, p)
    logger.info("Trage Anweisungen in die 'comment'-Spalte ein, dann 'finalize'.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    tax = taxonomy.load()
    total, problems = 0, 0
    for ch, comm, subs in _select(args.chamber, args.committee):
        for unit in _units_to_map(subs):
            mpath = mapping.cache_path(ch, comm, unit)
            if not os.path.exists(mpath):
                continue
            with open(mpath, "r", encoding="utf-8") as fh:
                mdata = json.load(fh)
            _clean, msgs = validate_mod.validate_rows(
                mdata["rows"], tax, mdata.get("grounding_quality", "none"))
            total += 1
            if msgs:
                problems += 1
                logger.info("[%s/%s/%s] %d Hinweise:", ch, comm, unit, len(msgs))
                for m in msgs:
                    logger.info("    - %s", m)
    logger.info("Validate: %d Einheiten geprueft, %d mit Hinweisen.", total, problems)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    tax = taxonomy.load()
    comments, conflicts = review_xlsx.import_comments()
    if conflicts:
        logger.error("Kommentar-Konflikte zwischen den xlsx — Abbruch:")
        for c in conflicts:
            logger.error("  - %s", c)
        return 2
    rows = _collect_reviewed_rows(args.chamber, args.committee)
    if not rows:
        logger.warning("Keine Review-Caches gefunden.")
        return 1
    final, log = finalize_mod.finalize_rows(rows, comments, tax)
    for line in log:
        logger.info("  %s", line)
    path = finalize_mod.write_staging(final)
    logger.info("Staging-CSV geschrieben: %s (%d Roh-Zeilen, %d Kommentare).",
                path, len(rows), len(comments))
    logger.info("Pruefe die Staging-CSV, dann 'promote'.")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    backup = finalize_mod.promote()
    if backup:
        logger.info("Alter Master gesichert: %s", backup)
    logger.info("Master ersetzt: %s", config.MASTER_CSV)
    return 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="committee_mapping_agent",
        description="Committee->Sector/Industry Mapping-Agent (DeepSeek).",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def add_filter(sp):
        sp.add_argument("--chamber", choices=["House", "Senate"])
        sp.add_argument("--committee")
        sp.add_argument("--force", action="store_true",
                        help="Cache ignorieren und neu erzeugen.")

    sub.add_parser("enrich-taxonomy").set_defaults(func=cmd_enrich_taxonomy)
    add_filter(sub.add_parser("research")); sub.choices["research"].set_defaults(func=cmd_research)
    add_filter(sub.add_parser("map")); sub.choices["map"].set_defaults(func=cmd_map)
    add_filter(sub.add_parser("review")); sub.choices["review"].set_defaults(func=cmd_review)

    sp_exp = sub.add_parser("export-review")
    sp_exp.add_argument("--chamber", choices=["House", "Senate"])
    sp_exp.add_argument("--committee")
    sp_exp.set_defaults(func=cmd_export_review)

    sp_val = sub.add_parser("validate")
    sp_val.add_argument("--chamber", choices=["House", "Senate"])
    sp_val.add_argument("--committee")
    sp_val.set_defaults(func=cmd_validate)

    sp_fin = sub.add_parser("finalize")
    sp_fin.add_argument("--chamber", choices=["House", "Senate"])
    sp_fin.add_argument("--committee")
    sp_fin.set_defaults(func=cmd_finalize)

    sub.add_parser("promote").set_defaults(func=cmd_promote)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    try:
        return args.func(args)
    except llm_client.MissingAPIKeyError as exc:
        logger.error(str(exc))
        return 3


if __name__ == "__main__":
    sys.exit(main())
