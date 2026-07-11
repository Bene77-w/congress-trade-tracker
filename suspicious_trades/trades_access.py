"""Read-only Zugriff auf den Trades-Master und den Staging-Reader.

Zwei Eingaenge liefern die Trades eines Laufs (Schalter ``TRADE_SOURCE``):

* ``staging`` (Primaerweg): liest die vom Control-UI uebernommenen Export-Dateien
  aus ``data/<package>/staged_trades/`` nach dem §5b-Kontrakt.
* ``window`` (mechanische Option): waehlt ein Zeitfenster direkt aus dem Master.

Der Master und der Kurs-Cache werden ausschliesslich lesend verwendet; diese
Schicht schreibt nie in die Haupt-Pipeline. Der einzige dokumentierte Fremd-Import
(``signals.committee_signal._direction_from_transaction_type``) wird bewusst
verzoegert geladen, damit reine Fensterlaeufe die Haupt-App nicht importieren.
"""

from __future__ import annotations

from datetime import date, timedelta
import logging
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

logger = logging.getLogger(__name__)


# §5b-Pflichtspalten des Staging-Exports; fehlt eine, bricht der Reader ab.
STAGING_REQUIRED_COLUMNS = (
    "trade_id",
    "name",
    "ticker",
    "transaction_type",
    "transaction_date",
    "disclosure_date",
    "amount",
)

# Taxonomie-Spalten des Masters (Schreibweise variiert historisch je Quelle).
_SECTOR_COLUMNS = ("sector_yf", "Sector_YF", "sector")
_INDUSTRY_COLUMNS = ("industry_yf", "Industry_YF", "industry")


class StagingSchemaError(ValueError):
    """Eine Staging-Datei verletzt den §5b-Pflichtspalten-Kontrakt."""


def _derive_direction(transaction_types: pd.Series) -> pd.Series:
    """Leitet ``direction`` (``'buy'/'sell'/'other'``) verzoegert geladen ab."""

    from signals.committee_signal import _direction_from_transaction_type

    return transaction_types.map(_direction_from_transaction_type)


def _ensure_direction(frame: pd.DataFrame) -> pd.DataFrame:
    """Ergaenzt oder fuellt ``direction`` aus ``transaction_type`` (String-Werte)."""

    if "direction" not in frame.columns:
        frame["direction"] = _derive_direction(frame["transaction_type"])
        return frame

    direction = frame["direction"].astype("object")
    missing = direction.isna() | (direction.astype(str).str.strip() == "")
    if missing.any():
        derived = _derive_direction(frame.loc[missing, "transaction_type"])
        frame.loc[missing, "direction"] = derived
    return frame


def staged_trades_dir(settings: Mapping[str, Any]) -> Path:
    """DATA_DIR-Unterordner mit den fuer den Lauf uebernommenen Exporten."""

    return Path(settings["DATA_DIR"]) / settings.get(
        "STAGED_TRADES_DIRNAME", "staged_trades"
    )


def read_staging_trades(settings: Mapping[str, Any]) -> pd.DataFrame:
    """Liest alle Staging-Exporte, validiert Pflichtspalten und dedupliziert.

    Jede Datei wird gegen ``STAGING_REQUIRED_COLUMNS`` geprueft (fehlt eine Spalte
    → :class:`StagingSchemaError` mit Datei- und Spaltennennung). Zusatzspalten
    bleiben erhalten. ``direction`` wird nachberechnet, falls leer/fehlend.
    Duplikate ueber mehrere Dateien werden per ``trade_id`` dedupliziert; der
    juengste ``export_ts`` gewinnt.
    """

    directory = staged_trades_dir(settings)
    files = sorted(directory.glob("*.csv")) if directory.is_dir() else []
    if not files:
        logger.warning("Kein Staging-Export in %s gefunden.", directory)
        return pd.DataFrame(columns=list(STAGING_REQUIRED_COLUMNS) + ["direction"])

    frames: list[pd.DataFrame] = []
    for path in files:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        missing = [c for c in STAGING_REQUIRED_COLUMNS if c not in frame.columns]
        if missing:
            raise StagingSchemaError(
                f"Staging-Datei {path.name} fehlen Pflichtspalten: "
                f"{', '.join(missing)}"
            )
        if "export_ts" not in frame.columns:
            frame["export_ts"] = ""
        frame["_source_file"] = path.name
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = _ensure_direction(combined)

    # Juengsten Export je trade_id behalten: stabil nach export_ts sortieren.
    combined["_ts_key"] = combined["export_ts"].fillna("").astype(str)
    combined = combined.sort_values("_ts_key", kind="stable")
    combined = combined.drop_duplicates(subset="trade_id", keep="last")
    combined = combined.drop(columns=["_ts_key"]).reset_index(drop=True)
    return combined


def _load_master(settings: Mapping[str, Any]) -> pd.DataFrame:
    """Laedt den Trades-Master read-only (leerer Frame, falls nicht vorhanden)."""

    path = Path(settings["TRADES_MASTER_PATH"])
    if not path.is_file():
        logger.warning("Trades-Master nicht gefunden: %s", path)
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_window_trades(
    settings: Mapping[str, Any],
    *,
    today: date | None = None,
) -> pd.DataFrame:
    """Mechanische Fensterauswahl aus dem Master ueber das konfigurierte Datumsfeld.

    Fenstergrenze: ``TRADE_DATE_FIELD`` in ``[today - TRADE_WINDOW_DAYS, today]``.
    """

    frame = _load_master(settings)
    if frame.empty:
        return frame

    field = str(settings.get("TRADE_DATE_FIELD", "disclosure_date"))
    if field not in frame.columns:
        raise KeyError(f"TRADE_DATE_FIELD '{field}' fehlt im Trades-Master.")

    anchor = today or date.today()
    window_days = int(settings.get("TRADE_WINDOW_DAYS", 14))
    start = anchor - timedelta(days=window_days)

    dates = pd.to_datetime(frame[field], errors="coerce").dt.date
    mask = dates.notna() & (dates >= start) & (dates <= anchor)
    selected = frame.loc[mask].copy().reset_index(drop=True)
    selected = _ensure_direction(selected)
    return selected


def load_trades(
    settings: Mapping[str, Any],
    *,
    today: date | None = None,
) -> pd.DataFrame:
    """Liefert die Trades des Laufs je ``TRADE_SOURCE`` (``staging``/``window``)."""

    source = str(settings.get("TRADE_SOURCE", "staging"))
    if source == "staging":
        return read_staging_trades(settings)
    if source == "window":
        return read_window_trades(settings, today=today)
    raise ValueError(f"Unbekannte TRADE_SOURCE '{source}'.")


def _first_present(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in frame.columns:
            return name
    return None


def taxonomy_vocabulary(settings: Mapping[str, Any]) -> dict[str, list[str]]:
    """Sortiertes Sektor-/Industry-Vokabular aus dem Master (leere Werte raus)."""

    frame = _load_master(settings)
    result: dict[str, list[str]] = {"sectors": [], "industries": []}
    if frame.empty:
        return result

    sector_col = _first_present(frame, _SECTOR_COLUMNS)
    industry_col = _first_present(frame, _INDUSTRY_COLUMNS)
    if sector_col is not None:
        values = frame[sector_col].astype(str).str.strip()
        result["sectors"] = sorted({v for v in values if v and v.lower() != "nan"})
    if industry_col is not None:
        values = frame[industry_col].astype(str).str.strip()
        result["industries"] = sorted({v for v in values if v and v.lower() != "nan"})
    return result


def member_aggregates(
    settings: Mapping[str, Any],
    *,
    trades: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Aggregiert Trade-Zahlen je Member (Anzahl, Kauf-/Verkaufs-Split, Ticker).

    Arbeitet auf den uebergebenen ``trades`` oder – ohne Argument – auf dem
    kompletten Master. Rein lesend; erzeugt keine Master-Spalten.
    """

    frame = trades if trades is not None else _load_master(settings)
    if frame is None or frame.empty or "name" not in frame.columns:
        return pd.DataFrame(
            columns=["name", "trades", "buys", "sells", "tickers"]
        )

    working = _ensure_direction(frame.copy())
    rows: list[dict[str, Any]] = []
    for name, group in working.groupby("name", sort=True):
        directions = group["direction"].astype(str)
        tickers = sorted(
            {
                t.strip().upper()
                for t in group.get("ticker", pd.Series(dtype=str)).astype(str)
                if t and t.strip() and t.strip().lower() != "nan"
            }
        )
        rows.append(
            {
                "name": name,
                "trades": int(len(group)),
                "buys": int((directions == "buy").sum()),
                "sells": int((directions == "sell").sum()),
                "tickers": tickers,
            }
        )
    return pd.DataFrame(rows, columns=["name", "trades", "buys", "sells", "tickers"])
