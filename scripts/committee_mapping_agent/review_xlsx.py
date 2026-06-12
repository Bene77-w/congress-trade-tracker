"""Review-xlsx-Export (3 Dateien, farbige Konfidenz) + Kommentar-Reimport.

Export:
  review_all.xlsx     — alle Zeilen
  review_low.xlsx     — nur confidence == low
  review_medium.xlsx  — nur confidence == medium
Konfidenz-Zelle eingefaerbt: low=rot, medium=gelb, high=gruen.
Die Spalte 'comment' bleibt leer -> hier traegt der User Anweisungen ein, die im
Finalize beachtet werden.

Reimport:
  Liest alle vorhandenen der 3 Dateien, sammelt 'comment' je mapping_id. Stehen
  fuer dieselbe mapping_id in verschiedenen Dateien WIDERSPRUECHLICHE Kommentare,
  ist das ein Fehler (kein stilles Ueberschreiben) -> ConflictError.
"""
import os
from typing import Any, Dict, List, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from . import config

COLUMNS = [
    "mapping_id", "Chamber", "Committee", "Subcommittee", "Sector", "Industry",
    "Sector_Rank", "confidence", "grounding_source", "review_reason",
    "review_flag", "comment",
]
COMMENT_COL_IDX = COLUMNS.index("comment") + 1  # 1-basiert
ID_COL_IDX = COLUMNS.index("mapping_id") + 1
CONF_COL_IDX = COLUMNS.index("confidence") + 1

_FILL = {
    "low": PatternFill("solid", fgColor="FFC7CE"),     # rot
    "medium": PatternFill("solid", fgColor="FFEB9C"),  # gelb
    "high": PatternFill("solid", fgColor="C6EFCE"),    # gruen
}
_NO_MAP_FILL = PatternFill("solid", fgColor="D9D9D9")  # grau: Einheit ohne Mapping

_FILES = {
    "all": "review_all.xlsx",
    "low": "review_low.xlsx",
    "medium": "review_medium.xlsx",
}


class ConflictError(RuntimeError):
    """Widerspruechliche Kommentare fuer dieselbe mapping_id."""


def _row_values(r: Dict[str, Any]) -> List[Any]:
    return [
        r["mapping_id"], r["chamber"], r["committee"], r["subcommittee"],
        r["sector"], r["industry"], r["rank"], r["confidence"],
        r.get("grounding_source", ""), r.get("review_reason", ""),
        r.get("review_flag", "ok"), r.get("comment", ""),
    ]


def _write_sheet(rows: List[Dict[str, Any]], path: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "review"
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append(_row_values(r))
        if str(r.get("review_flag")) == "no_mapping":
            fill = _NO_MAP_FILL
        else:
            fill = _FILL.get(str(r.get("confidence", "")).lower())
        if fill:
            ws.cell(row=ws.max_row, column=CONF_COL_IDX).fill = fill
    # Spaltenbreiten grob setzen (Lesbarkeit).
    widths = [12, 8, 24, 28, 18, 24, 6, 11, 40, 36, 18, 36]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    ws.freeze_panes = "A2"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)


def export_review(reviewed_rows: List[Dict[str, Any]],
                  review_dir: str = None) -> Dict[str, str]:
    """Schreibt die 3 Review-xlsx. Liefert {key: pfad}."""
    review_dir = review_dir or config.REVIEW_DIR
    os.makedirs(review_dir, exist_ok=True)
    paths = {k: os.path.join(review_dir, fn) for k, fn in _FILES.items()}

    _write_sheet(reviewed_rows, paths["all"])
    # Low-Datei = Aufmerksamkeits-Bucket: niedrige Konfidenz UND unmappte
    # Einheiten (Luecken), damit der Reviewer beides an einer Stelle sieht.
    _write_sheet([r for r in reviewed_rows
                  if str(r.get("confidence")).lower() == "low"
                  or str(r.get("review_flag")) == "no_mapping"],
                 paths["low"])
    _write_sheet([r for r in reviewed_rows if str(r.get("confidence")).lower() == "medium"],
                 paths["medium"])
    return paths


def _read_comments(path: str) -> Dict[str, str]:
    """{mapping_id -> comment} aus einer xlsx (nur nicht-leere Kommentare)."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    out: Dict[str, str] = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # Header
        if not row or len(row) < COMMENT_COL_IDX:
            continue
        mid = row[ID_COL_IDX - 1]
        comment = row[COMMENT_COL_IDX - 1]
        if mid and comment is not None and str(comment).strip():
            out[str(mid)] = str(comment).strip()
    wb.close()
    return out


def import_comments(review_dir: str = None) -> Tuple[Dict[str, str], List[str]]:
    """Sammelt Kommentare ueber alle vorhandenen Review-xlsx.

    Returns:
        (comments, conflicts) — comments: {mapping_id -> comment};
        conflicts: Liste menschenlesbarer Konfliktmeldungen (leer = ok).
    Wirft NICHT bei Konflikt, sondern liefert sie zurueck (Aufrufer entscheidet).
    """
    review_dir = review_dir or config.REVIEW_DIR
    merged: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    conflicts: List[str] = []

    for key, fn in _FILES.items():
        path = os.path.join(review_dir, fn)
        if not os.path.exists(path):
            continue
        for mid, comment in _read_comments(path).items():
            if mid in merged and merged[mid] != comment:
                conflicts.append(
                    f"mapping_id {mid}: widerspruechliche Kommentare in "
                    f"'{sources[mid]}' vs. '{fn}'."
                )
            else:
                merged[mid] = comment
                sources[mid] = fn
    return merged, conflicts
