"""recommendations.csv-Schema, Mark-to-Market und eval_until-Logik (kein LLM).

Die Preisquelle ist ausschliesslich der bestehende Kurs-Cache der Haupt-Pipeline
(``PRICE_CACHE_PATH``). Der Zugriff erfolgt read-only ueber die Lade-Helfer aus
``scoring.post_trade_returns`` (``_load_price_cache`` + ``_build_price_lookup``);
es findet kein eigener yfinance-Fetch statt (User-Entscheid 2026-07-10). Fehlt ein
Ticker im Cache, wird die Zeile als ``price_missing`` markiert und gewarnt.

Der Import der Haupt-Pipeline-Helfer ist bewusst verzoegert (``_load_price_lookup``)
und bildet den einzigen Monkeypatch-Punkt der Tests.
"""

from __future__ import annotations

from datetime import date, timedelta
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# Bindendes Ledger-Schema (Plan §2 Punkt 6 + §5e Punkt 3), feste Reihenfolge.
RECOMMENDATION_COLUMNS = [
    "rec_id",
    "ticker",
    "direction",
    "issued_date",
    "rationale",
    "dossier_path",
    "rank",
    "eval_until",
    "ref_price_at_issue",
    "hypothetical_return_pct",
    "price_as_of",
    "eval_status",
    "user_status",
]

# Bewertungsstatus einer Empfehlungszeile.
STATUS_TRACKING = "tracking"
STATUS_FINAL = "final"
STATUS_PRICE_MISSING = "price_missing"


def recommendations_path(settings: Mapping[str, Any]) -> Path:
    """Pfad des Empfehlungs-Ledgers innerhalb der package-eigenen DATA_DIR."""

    return Path(settings["DATA_DIR"]) / settings.get(
        "RECOMMENDATIONS_FILENAME", "recommendations.csv"
    )


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _direction_sign(direction: Any) -> int:
    """String-Direction (``'buy'/'sell'/'other'``) → +1/-1 (Verkauf = -1)."""

    return -1 if str(direction).strip().lower() == "sell" else 1


def _load_price_lookup(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only Kurs-Lookup ueber die Haupt-Pipeline-Helfer (verzoegert geladen)."""

    from scoring.post_trade_returns import _build_price_lookup, _load_price_cache

    cache = _load_price_cache(str(settings["PRICE_CACHE_PATH"]))
    return _build_price_lookup(cache)


class PriceSource:
    """Duenner read-only Wrapper um das ``_build_price_lookup``-Kurs-Dict.

    Enthaelt ``{ticker: (dates[datetime64[ns]], closes[float64])}``. ``from_settings``
    laedt es ueber die Haupt-Pipeline-Helfer; Tests injizieren ein Fixture-Lookup.
    """

    def __init__(self, lookup: Mapping[str, Any] | None = None) -> None:
        self.lookup: dict[str, Any] = dict(lookup or {})

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> "PriceSource":
        return cls(_load_price_lookup(settings))

    def _entry(self, ticker: Any) -> tuple[np.ndarray, np.ndarray] | None:
        if ticker is None:
            return None
        try:
            if pd.isna(ticker):
                return None
        except (TypeError, ValueError):
            pass
        key = str(ticker).strip().upper()
        if not key:
            return None
        entry = self.lookup.get(key)
        return entry if entry else None

    def close_at_or_after(self, ticker: Any, when: date) -> float | None:
        """Erster Close am/nach ``when`` (mirror von ``_price_at_or_after_fast``)."""

        entry = self._entry(ticker)
        if not entry:
            return None
        dates, closes = entry
        target = np.datetime64(pd.Timestamp(when).normalize().to_datetime64())
        pos = int(np.searchsorted(dates, target, side="left"))
        if pos >= len(dates):
            return None
        val = closes[pos]
        return None if np.isnan(val) else float(val)

    def close_at_or_before(
        self, ticker: Any, when: date
    ) -> tuple[float, date] | None:
        """Juengster Close am/vor ``when`` samt zugehoerigem Handelsdatum."""

        entry = self._entry(ticker)
        if not entry:
            return None
        dates, closes = entry
        target = np.datetime64(pd.Timestamp(when).normalize().to_datetime64())
        pos = int(np.searchsorted(dates, target, side="right")) - 1
        if pos < 0:
            return None
        val = closes[pos]
        if np.isnan(val):
            return None
        return float(val), pd.Timestamp(dates[pos]).date()


def compute_eval_until(
    issued_date: date,
    settings: Mapping[str, Any],
    *,
    event_outcome_date: date | None = None,
) -> date:
    """Bestimmt das Bewertungs-Ende nach ``EVAL_MODE`` und deckelt es hart.

    ``horizon`` → ``issued_date + EVAL_HORIZON_DAYS``. ``outcome`` → bis
    ``EVAL_OUTCOME_BUFFER_DAYS`` nach dem Outcome, ersatzweise ``EVAL_MAX_DAYS``.
    In beiden Faellen nie ueber ``issued_date + EVAL_MAX_DAYS`` hinaus.
    """

    mode = str(settings.get("EVAL_MODE", "outcome"))
    max_days = int(settings.get("EVAL_MAX_DAYS", 180))
    hard_cap = issued_date + timedelta(days=max_days)

    if mode == "horizon":
        horizon = int(settings.get("EVAL_HORIZON_DAYS", 90))
        return min(issued_date + timedelta(days=horizon), hard_cap)

    if event_outcome_date is not None:
        buffer_days = int(settings.get("EVAL_OUTCOME_BUFFER_DAYS", 5))
        return min(event_outcome_date + timedelta(days=buffer_days), hard_cap)
    return hard_cap


def _mark_to_market(
    price_source: PriceSource,
    *,
    ticker: Any,
    direction: Any,
    issued_date: date,
    eval_until: date,
    today: date,
) -> dict[str, Any]:
    """Berechnet ref_price, hypothetischen Return, price_as_of und eval_status."""

    ref_price = price_source.close_at_or_after(ticker, issued_date)
    if ref_price is None or ref_price == 0.0:
        logger.warning(
            "Ticker %r fehlt im Kurs-Cache (kein ref_price); eval_status=%s.",
            ticker,
            STATUS_PRICE_MISSING,
        )
        return {
            "ref_price_at_issue": np.nan,
            "hypothetical_return_pct": np.nan,
            "price_as_of": "",
            "eval_status": STATUS_PRICE_MISSING,
        }

    frozen = today >= eval_until
    as_of = min(today, eval_until)
    current = price_source.close_at_or_before(ticker, as_of)
    if current is None:
        return {
            "ref_price_at_issue": float(ref_price),
            "hypothetical_return_pct": np.nan,
            "price_as_of": "",
            "eval_status": STATUS_FINAL if frozen else STATUS_TRACKING,
        }

    current_price, price_as_of = current
    sign = _direction_sign(direction)
    return_pct = sign * (current_price / ref_price - 1.0) * 100.0
    return {
        "ref_price_at_issue": float(ref_price),
        "hypothetical_return_pct": float(return_pct),
        "price_as_of": price_as_of.isoformat(),
        "eval_status": STATUS_FINAL if frozen else STATUS_TRACKING,
    }


def load_ledger(settings: Mapping[str, Any]) -> pd.DataFrame:
    """Liest das Empfehlungs-Ledger (leerer, schema-treuer Frame falls fehlend)."""

    path = recommendations_path(settings)
    if not path.is_file():
        return pd.DataFrame(columns=RECOMMENDATION_COLUMNS)
    frame = pd.read_csv(path)
    for column in RECOMMENDATION_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame[RECOMMENDATION_COLUMNS]


def _write_ledger(settings: Mapping[str, Any], frame: pd.DataFrame) -> Path:
    path = recommendations_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = frame[RECOMMENDATION_COLUMNS]
    tmp = path.with_suffix(path.suffix + ".tmp")
    ordered.to_csv(tmp, index=False)
    tmp.replace(path)
    return path


def add_recommendations(
    settings: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    today: date | None = None,
    run_id: str | None = None,
    price_source: PriceSource | None = None,
) -> pd.DataFrame:
    """Uebernimmt gerankte Empfehlungskandidaten als schema-valide Ledger-Zeilen.

    Erwartete Kandidatenfelder: ``ticker, direction`` (Pflicht); optional
    ``rationale, dossier_path, rank, issued_date, event_outcome_date, rec_id,
    user_status``. Fuer jede Zeile werden ``eval_until`` und die erste
    Mark-to-Market-Bewertung berechnet. Rueckgabe = die neu angehaengten Zeilen.
    """

    anchor = today or date.today()
    prices = price_source or PriceSource.from_settings(settings)
    ledger = load_ledger(settings)
    existing_ids = set(ledger["rec_id"].astype(str)) if not ledger.empty else set()

    new_rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        ticker = candidate.get("ticker")
        direction = str(candidate.get("direction", "")).strip().lower() or "other"
        issued_date = _to_date(candidate.get("issued_date")) or anchor
        outcome_date = _to_date(candidate.get("event_outcome_date"))
        eval_until = compute_eval_until(
            issued_date, settings, event_outcome_date=outcome_date
        )
        rec_id = str(candidate.get("rec_id") or "").strip()
        if not rec_id or rec_id in existing_ids:
            stem = run_id or anchor.strftime("%Y%m%d")
            rec_id = f"rec-{stem}-{index:03d}"
        existing_ids.add(rec_id)

        marks = _mark_to_market(
            prices,
            ticker=ticker,
            direction=direction,
            issued_date=issued_date,
            eval_until=eval_until,
            today=anchor,
        )
        new_rows.append(
            {
                "rec_id": rec_id,
                "ticker": "" if ticker is None else str(ticker).strip().upper(),
                "direction": direction,
                "issued_date": issued_date.isoformat(),
                "rationale": str(candidate.get("rationale", "") or ""),
                "dossier_path": str(candidate.get("dossier_path", "") or ""),
                "rank": candidate.get("rank"),
                "eval_until": eval_until.isoformat(),
                "user_status": str(candidate.get("user_status", "") or ""),
                **marks,
            }
        )

    new_frame = pd.DataFrame(new_rows, columns=RECOMMENDATION_COLUMNS)
    combined = pd.concat([ledger, new_frame], ignore_index=True, sort=False)
    _write_ledger(settings, combined)
    return new_frame


def update_tracking(
    settings: Mapping[str, Any],
    *,
    today: date | None = None,
    price_source: PriceSource | None = None,
) -> int:
    """Rechnet alle ``tracking``-Zeilen neu (Mark-to-Market bzw. Einfrieren).

    ``price_missing`` und ``final`` bleiben unberuehrt. Rueckgabe = Anzahl der
    aktualisierten Zeilen.
    """

    anchor = today or date.today()
    ledger = load_ledger(settings)
    if ledger.empty:
        return 0

    prices = price_source or PriceSource.from_settings(settings)
    updated = 0
    for idx in ledger.index:
        if str(ledger.at[idx, "eval_status"]) != STATUS_TRACKING:
            continue
        issued_date = _to_date(ledger.at[idx, "issued_date"])
        eval_until = _to_date(ledger.at[idx, "eval_until"])
        if issued_date is None or eval_until is None:
            continue
        marks = _mark_to_market(
            prices,
            ticker=ledger.at[idx, "ticker"],
            direction=ledger.at[idx, "direction"],
            issued_date=issued_date,
            eval_until=eval_until,
            today=anchor,
        )
        for key, value in marks.items():
            ledger.at[idx, key] = value
        updated += 1

    if updated:
        _write_ledger(settings, ledger)
    return updated
