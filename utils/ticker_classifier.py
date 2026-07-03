"""
Offline-Klassifikator für Congress-Trade-Tickers.

Spezifikation: rebuild_pitch.md §5.
Deterministisch, null-safe, ohne Netzwerk- oder Daten-Abhängigkeiten.

Aufrufform:
    classification = classify(raw_ticker, company_name="")

Der Entscheidungsbaum in §5.3 ist verbindlich — die Reihenfolge der
Regeln ist Teil der Spezifikation. Spezifischere Regeln werden vor
generischeren geprüft. Änderungen an der Reihenfolge brechen die
Semantik und sind ohne neue Pitch-Version verboten (Anti-Pattern §14).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 5.1 Öffentliche Enums und Ergebnistyp
# ---------------------------------------------------------------------------


class TickerType(str, Enum):
    STOCK              = "stock"
    FOREIGN_STOCK      = "foreign_stock"
    SHARECLASS         = "shareclass"
    ETF                = "etf"                  # NUR von Enricher setzbar
    PREFERRED          = "preferred"
    WARRANT            = "warrant"
    OPTION             = "option"
    BOND               = "bond"
    TREASURY           = "treasury"
    CRYPTO             = "crypto"
    UNIT               = "unit"
    WHEN_ISSUED        = "when_issued"
    MISC               = "misc"
    NUMERIC_SUFFIX     = "numeric_suffix"
    LONG_ALPHA_SUSPECT = "long_alpha_suspect"
    INVALID            = "invalid"
    UNKNOWN            = "unknown"
    IGNORED            = "ignored"            # NUR via ignored_tickers.csv setzbar


class ApiStrategy(str, Enum):
    SKIP            = "skip"
    FIGI_MAPPING    = "figi_mapping"
    FIGI_SEARCH     = "figi_search"
    CUSIP_LOOKUP    = "cusip_lookup"
    YFINANCE_DIRECT = "yfinance_direct"
    MANUAL_REVIEW   = "manual_review"


class Confidence(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


@dataclass(frozen=True)
class TickerClassification:
    raw_ticker: str
    company_name: str
    ticker_base: str
    ticker_type: TickerType
    api_strategy: ApiStrategy
    exch_code: Optional[str]
    confidence: Confidence
    classification_reason: str
    manual_review: bool
    series_letter: Optional[str]
    status_suffix: Optional[str]
    # Nur für PREFERRED gesetzt: Common-Stamm-Ticker (z.B. "BAC" für "BAC-PRK").
    # Sektor-Stage nutzt ihn für den yfinance-Lookup der Stammaktie.
    parent_ticker: Optional[str] = None


# ---------------------------------------------------------------------------
# 5.2 Konstanten — 1:1 aus Pitch, NICHT verändern
# ---------------------------------------------------------------------------

# Yahoo Finance Suffix-Liste (92 Einträge)
YAHOO_EXCHANGE_SUFFIXES = frozenset({
    "CBT", "CME", "NYB", "CMX", "NYM", "BA", "SA", "CN", "NE", "TO", "V", "SN", "CL",
    "MX", "CR", "VI", "BR", "PR", "CO", "TL", "HE", "PA", "BE", "BM", "DU", "F", "HM",
    "HA", "MU", "SG", "DE", "AT", "BD", "IC", "IR", "TI", "MI", "RG", "VS", "AS", "OL",
    "WA", "LS", "RO", "MC", "ST", "SW", "IS", "L", "IL", "AQ", "XC", "XD", "NX", "AX",
    "XA", "SS", "SZ", "HK", "BO", "NS", "JK", "T", "KL", "NZ", "PS", "SI", "KS", "KQ",
    "TWO", "TW", "BK", "VN", "CA", "TA", "KW", "QA", "SAU", "JO", "AE", "REGA",
})

# Yahoo → OpenFIGI Composite-Code Mapping (25 Einträge)
YAHOO_TO_OPENFIGI_COMPOSITE = {
    "T":  "JP",
    "TO": "CN",
    "V":  "CN",
    "AX": "AU",
    "SW": "SW",
    "L":  "LN",
    "DE": "GR",
    "F":  "GR",
    "PA": "FP",
    "AS": "NA",
    "MI": "IM",
    "MC": "SM",
    "ST": "SS",
    "HK": "HK",
    "SS": "CH",
    "SZ": "CH",
    "BO": "IN",
    "NS": "IN",
    "MX": "MM",
    "BR": "BB",
    "SA": "BZ",
    "JK": "IJ",
    "BK": "TB",
    "TA": "IT",
    "JO": "SJ",
    "VI": "AV",
}

# Bekannter Datenmüll (12 Einträge) — manueller Augenschein der Unknowns
KNOWN_DATA_GARBAGE = frozenset({
    "BELGIUM", "CARDONA", "CORPORAT", "HAMILTO", "ICAPITAL",
    "HOSPIC", "CMLPSX", "COLPAL", "ETRADE", "CTECBX",
    "TYPE", "SYMBOL",
})

# ---------------------------------------------------------------------------
# Regex-Patterns (geschlossene Liste, einzeln unit-testbar)
# ---------------------------------------------------------------------------

# Structural validation: erstes Zeichen muss [A-Z0-9] sein
_RE_STARTS_WITH_ALNUM = re.compile(r'^[A-Z0-9]')

# Options (OCC-Format): Root + 6-stelliges Datum + C/P + 8-stelliger Strike
_RE_OPTION         = re.compile(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$')

# Preferred-Stock-Notationen
_RE_PREF_DOLLAR    = re.compile(r'^([A-Z]{1,6})\$([A-Z])$')
_RE_PREF_DASH_P_X  = re.compile(r'^([A-Z]+)-P-([A-Z])(?:-[A-Z]+)?$')
_RE_PREF_DASH_PX   = re.compile(r'^([A-Z]+)-P([A-Z])$')
_RE_PREF_DASH_P    = re.compile(r'^([A-Z]+)-P$')
_RE_PREF_DOT_PR    = re.compile(r'^([A-Z]+)\.PR([A-Z])?$')
_RE_PREF_DOT_PX    = re.compile(r'^([A-Z]+)\.P([A-Z])$')  # Guard gegen Exchange-Suffix

# T-Bills: "T" gefolgt von 8-9 alphanumerischen Zeichen (9-10 insgesamt).
# Ziffern-Pflicht (Lookahead): echte T-Bill-IDs sind T+CUSIP und enthalten immer
# Ziffern — ohne den Guard wuerden 9-10-buchstabige Woerter mit T (TRANSPORT,
# TELEFONICA) still als Treasury/SKIP klassifiziert statt ins Manual-Review
# (LONG_ALPHA_SUSPECT) zu laufen.
_RE_TBILL          = re.compile(r'^T(?=.*\d)[A-Z0-9]{8,9}$')

# CUSIP: 9 alnum-Zeichen mit mindestens einer Ziffer
_RE_CUSIP          = re.compile(r'^(?=.*\d)[A-Z0-9]{9}$')

# Crypto: Spotpair mit USD/USDT/EUR/BTC/ETH
_RE_CRYPTO         = re.compile(r'^[A-Z0-9]{2,10}-(?:USD|USDT|EUR|BTC|ETH)$')

# Test-Ticker
_RE_TEST           = re.compile(r'^[A-Z]?TEST(?:-[A-Z])?$')

# Dot-Suffix Share-Class vs. International
_RE_DOT_GENERAL    = re.compile(r'^([A-Z0-9]+)\.([A-Z]+)$')

# Dash-Letter (Share-Class ohne Preferred-Match)
_RE_DASH_LETTER    = re.compile(r'^([A-Z]+)-([A-Z])$')

# Numerischer Suffix (Quiver-spezifisch: AGN1, PYPL1, …)
_RE_NUMERIC_SUFFIX = re.compile(r'^([A-Z]+)(\d+)$')

# Standard US-Ticker (1-5 Großbuchstaben)
_RE_US_STANDARD    = re.compile(r'^[A-Z]{1,5}$')

# Long-Alpha: ≥6 Großbuchstaben, Datenmüll-Verdacht
_RE_LONG_ALPHA     = re.compile(r'^[A-Z]{6,}$')


# ---------------------------------------------------------------------------
# Ergebnis-Helper
# ---------------------------------------------------------------------------


def _preferred_base(root: str, series_letter: Optional[str]) -> str:
    """Bildet den Bloomberg-Preferred-Base: '<root> PR<series>'.

    Für PREF_DASH_P (z.B. 'O-P') ist series_letter None → Resultat 'O PR'
    ohne trailing Space (leerer String anstelle des Buchstabens).
    """
    return f"{root} PR{series_letter or ''}"


def _make_invalid(raw_ticker: str, company_name: str, reason: str) -> TickerClassification:
    return TickerClassification(
        raw_ticker=raw_ticker,
        company_name=company_name,
        ticker_base=raw_ticker,
        ticker_type=TickerType.INVALID,
        api_strategy=ApiStrategy.SKIP,
        exch_code=None,
        confidence=Confidence.HIGH,
        classification_reason=reason,
        manual_review=False,
        series_letter=None,
        status_suffix=None,
    )


# ---------------------------------------------------------------------------
# Muni-Override (asset_name-getrieben)
# ---------------------------------------------------------------------------

# Quiver-Notationen wie BLOC-U, ALIN-P-E, T-P-A, STT-P-E-CL matchen den
# UNIT-/PREFERRED-Pattern, sind aber Muni-Bonds (siehe asset_name).
# Ohne Override liefern OpenFIGI-Anfragen nur Müll-Treffer (zufällige Munis).
# Wortgrenzen-Match (\b): reiner Substring-Match wuerde z. B. "AUTH" in
# "AUTHENTIC EQUITY ACQUISITION CORP UNITS" treffen und echte SPAC-Units
# still als Muni-Bond klassifizieren (SKIP, kein Review-Flag).
_MUNI_MARKERS = (
    "CNTY", "COUNTY",
    "SCH DIST", "SCHDIST",
    "INDPT", "AUTH",
    "REV BDS", "REVBDS",
    "ULTD TAX", "TAXSCHOOL",
    "STATE COLLEGE",
)
_RE_MUNI_MARKERS = re.compile(
    r'\b(?:' + '|'.join(re.escape(m) for m in _MUNI_MARKERS) + r')\b'
)


def _muni_override(cls: TickerClassification) -> TickerClassification:
    """UNIT/PREFERRED + Muni-Marker (ganzes Wort) in company_name → BOND/SKIP."""
    if cls.ticker_type not in (TickerType.UNIT, TickerType.PREFERRED):
        return cls
    name_upper = (cls.company_name or "").upper()
    if not _RE_MUNI_MARKERS.search(name_upper):
        return cls
    return dataclasses.replace(
        cls,
        ticker_type=TickerType.BOND,
        api_strategy=ApiStrategy.SKIP,
        confidence=Confidence.HIGH,
        classification_reason="MUNI_DETECTED",
        manual_review=False,
    )


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------


def classify(raw_ticker: str,
             company_name: str = "",
             *,
             _skip_status_suffix: bool = False) -> TickerClassification:
    """Wrapper: ruft _classify_inner und wendet danach _muni_override an."""
    cls = _classify_inner(raw_ticker, company_name,
                          _skip_status_suffix=_skip_status_suffix)
    return _muni_override(cls)


def _classify_inner(raw_ticker: str,
                    company_name: str = "",
                    *,
                    _skip_status_suffix: bool = False) -> TickerClassification:
    """Klassifiziert einen Ticker nach dem §5.3-Entscheidungsbaum.

    Args:
        raw_ticker: Rohwert aus Quiver/Scraper. None/leer → INVALID.
        company_name: Optional, wird nur durchgereicht.
        _skip_status_suffix: Interne Rekursionsbremse für -CL-Abschnitt.
            Top-Level-Aufrufer dürfen das Kwarg nicht setzen.

    Returns:
        TickerClassification. Deterministisch, mehrfach-aufrufsicher.
    """
    # ------- Null-Safe-Normalisierung -------------------------------------
    company_name = company_name or ""
    if raw_ticker is None:
        return _make_invalid("", company_name, "INVALID_EMPTY")
    raw = str(raw_ticker).strip()
    if not raw:
        return _make_invalid(raw_ticker if isinstance(raw_ticker, str) else "",
                             company_name, "INVALID_EMPTY")
    # Hochsprache: Quiver liefert Ticker bereits Upper-Case,
    # wir sind defensiv und normalisieren trotzdem.
    raw = raw.upper()

    # ------- Schritt 1: INVALID-Check -------------------------------------
    if not _RE_STARTS_WITH_ALNUM.match(raw):
        return _make_invalid(raw, company_name, "INVALID_NON_ALNUM_PREFIX")

    if raw in KNOWN_DATA_GARBAGE:
        return _make_invalid(raw, company_name, "INVALID_KNOWN_GARBAGE")

    # ------- Schritt 2: Status-Suffix-Check -------------------------------
    if not _skip_status_suffix:
        if raw.endswith("-CL") and len(raw) > 3:
            inner = classify(raw[:-3], company_name, _skip_status_suffix=True)
            return dataclasses.replace(
                inner,
                raw_ticker=raw,
                status_suffix="CL",
                classification_reason=inner.classification_reason + "+status_cl",
            )

        for wi_suffix in (".WI", "-WI"):
            if raw.endswith(wi_suffix):
                return TickerClassification(
                    raw_ticker=raw, company_name=company_name,
                    ticker_base=raw[:-3],
                    ticker_type=TickerType.WHEN_ISSUED,
                    api_strategy=ApiStrategy.SKIP,
                    exch_code=None, confidence=Confidence.HIGH,
                    classification_reason="WHEN_ISSUED_" + wi_suffix.lstrip(".-"),
                    manual_review=False,
                    series_letter=None, status_suffix="WI",
                )
        for wd_suffix in (".WD", "-WD"):
            if raw.endswith(wd_suffix):
                return TickerClassification(
                    raw_ticker=raw, company_name=company_name,
                    ticker_base=raw[:-3],
                    ticker_type=TickerType.WHEN_ISSUED,
                    api_strategy=ApiStrategy.SKIP,
                    exch_code=None, confidence=Confidence.HIGH,
                    classification_reason="WHEN_ISSUED_" + wd_suffix.lstrip(".-"),
                    manual_review=False,
                    series_letter=None, status_suffix="WD",
                )

    # ------- Schritt 3: Warrants ------------------------------------------
    # Reihenfolge: längere/eindeutigere Suffixe zuerst, damit '.W' nicht
    # vorzeitig '.WI'/'.WD' frisst (wurden oben schon abgefangen).
    warrant_suffixes = ("-WT", " WS", " W", ".W", "-W")
    for suf in warrant_suffixes:
        if raw.endswith(suf) and len(raw) > len(suf):
            base = raw[:-len(suf)].rstrip(" ")
            return TickerClassification(
                raw_ticker=raw, company_name=company_name,
                ticker_base=base,
                ticker_type=TickerType.WARRANT,
                api_strategy=ApiStrategy.FIGI_SEARCH,
                exch_code=None, confidence=Confidence.HIGH,
                classification_reason="WARRANT_" + suf.strip().lstrip(".-"),
                manual_review=False,
                series_letter=None, status_suffix=None,
            )

    # ------- Schritt 4: Units (SPACs) -------------------------------------
    for u_suffix in (".U", "-U"):
        if raw.endswith(u_suffix) and len(raw) > 2:
            return TickerClassification(
                raw_ticker=raw, company_name=company_name,
                ticker_base=raw[:-2],
                ticker_type=TickerType.UNIT,
                api_strategy=ApiStrategy.FIGI_SEARCH,
                exch_code=None, confidence=Confidence.MEDIUM,
                classification_reason="UNIT_" + u_suffix.lstrip(".-"),
                manual_review=False,
                series_letter=None, status_suffix=None,
            )

    # ------- Schritt 5: Misc-Code Z (Nasdaq Miscellaneous) ----------------
    if raw.endswith(".Z") and len(raw) > 2:
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw[:-2],
            ticker_type=TickerType.MISC,
            api_strategy=ApiStrategy.FIGI_SEARCH,
            exch_code=None, confidence=Confidence.LOW,
            classification_reason="MISC_Z",
            manual_review=False,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 6: Options (OCC-Format) ------------------------------
    if _RE_OPTION.match(raw):
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.OPTION,
            api_strategy=ApiStrategy.SKIP,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="OPTION_OCC",
            manual_review=False,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 7: Preferred Stocks ----------------------------------
    m = _RE_PREF_DOLLAR.match(raw)
    if m:
        root, series = m.group(1), m.group(2)
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=_preferred_base(root, series),
            ticker_type=TickerType.PREFERRED,
            api_strategy=ApiStrategy.FIGI_MAPPING,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="PREFERRED_DOLLAR",
            manual_review=False,
            series_letter=series, status_suffix=None,
            parent_ticker=root,
        )

    m = _RE_PREF_DASH_P_X.match(raw)
    if m:
        root, series = m.group(1), m.group(2)
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=_preferred_base(root, series),
            ticker_type=TickerType.PREFERRED,
            api_strategy=ApiStrategy.FIGI_MAPPING,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="PREFERRED_DASH_P_X",
            manual_review=False,
            series_letter=series, status_suffix=None,
            parent_ticker=root,
        )

    m = _RE_PREF_DASH_PX.match(raw)
    if m:
        root, series = m.group(1), m.group(2)
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=_preferred_base(root, series),
            ticker_type=TickerType.PREFERRED,
            api_strategy=ApiStrategy.FIGI_MAPPING,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="PREFERRED_DASH_PX",
            manual_review=False,
            series_letter=series, status_suffix=None,
            parent_ticker=root,
        )

    m = _RE_PREF_DASH_P.match(raw)
    if m:
        root = m.group(1)
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=_preferred_base(root, None),
            ticker_type=TickerType.PREFERRED,
            api_strategy=ApiStrategy.FIGI_MAPPING,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="PREFERRED_DASH_P",
            manual_review=False,
            series_letter=None, status_suffix=None,
            parent_ticker=root,
        )

    m = _RE_PREF_DOT_PR.match(raw)
    if m:
        root, series = m.group(1), m.group(2)  # series kann None sein ('.PR')
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=_preferred_base(root, series),
            ticker_type=TickerType.PREFERRED,
            api_strategy=ApiStrategy.FIGI_MAPPING,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="PREFERRED_DOT_PR",
            manual_review=False,
            series_letter=series, status_suffix=None,
            parent_ticker=root,
        )

    m = _RE_PREF_DOT_PX.match(raw)
    if m:
        # Guard: volles 2-Zeichen-Suffix (P + letter) darf nicht in der
        # Yahoo-Exchange-Liste stehen (sonst ist es ein Foreign-Suffix wie PA).
        root, series_char = m.group(1), m.group(2)
        full_suffix = "P" + series_char
        if full_suffix not in YAHOO_EXCHANGE_SUFFIXES:
            return TickerClassification(
                raw_ticker=raw, company_name=company_name,
                ticker_base=_preferred_base(root, series_char),
                ticker_type=TickerType.PREFERRED,
                api_strategy=ApiStrategy.FIGI_MAPPING,
                exch_code=None, confidence=Confidence.MEDIUM,
                classification_reason="PREFERRED_DOT_PX",
                manual_review=False,
                series_letter=series_char, status_suffix=None,
                parent_ticker=root,
            )
        # Fällt durch zu Schritt 12 (Dot-Suffix-Disambiguierung)

    # ------- Schritt 8: T-Bills -------------------------------------------
    if _RE_TBILL.match(raw):
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.TREASURY,
            api_strategy=ApiStrategy.SKIP,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="TREASURY_TBILL",
            manual_review=False,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 9: CUSIP ---------------------------------------------
    if _RE_CUSIP.match(raw):
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.BOND,
            api_strategy=ApiStrategy.CUSIP_LOOKUP,
            exch_code=None, confidence=Confidence.MEDIUM,
            classification_reason="BOND_CUSIP",
            manual_review=False,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 10: Crypto -------------------------------------------
    if _RE_CRYPTO.match(raw):
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.CRYPTO,
            api_strategy=ApiStrategy.SKIP,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="CRYPTO_SPOT",
            manual_review=False,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 11: Test-Tickers -------------------------------------
    if _RE_TEST.match(raw):
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.INVALID,
            api_strategy=ApiStrategy.SKIP,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="INVALID_TEST_TICKER",
            manual_review=False,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 12: Dot-Suffix-Disambiguierung -----------------------
    m = _RE_DOT_GENERAL.match(raw)
    if m:
        root, suffix = m.group(1), m.group(2)
        if suffix in YAHOO_EXCHANGE_SUFFIXES:
            exch_code = YAHOO_TO_OPENFIGI_COMPOSITE.get(suffix)
            return TickerClassification(
                raw_ticker=raw, company_name=company_name,
                ticker_base=raw,
                ticker_type=TickerType.FOREIGN_STOCK,
                api_strategy=ApiStrategy.FIGI_MAPPING,
                exch_code=exch_code, confidence=Confidence.HIGH,
                classification_reason="FOREIGN_STOCK_DOT_SUFFIX",
                manual_review=False,
                series_letter=None, status_suffix=None,
            )
        if len(suffix) == 1:
            return TickerClassification(
                raw_ticker=raw, company_name=company_name,
                ticker_base=f"{root}-{suffix}",
                ticker_type=TickerType.SHARECLASS,
                api_strategy=ApiStrategy.YFINANCE_DIRECT,
                exch_code=None, confidence=Confidence.HIGH,
                classification_reason="SHARECLASS_DOT_SINGLE_LETTER",
                manual_review=False,
                series_letter=suffix, status_suffix=None,
            )
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.UNKNOWN,
            api_strategy=ApiStrategy.MANUAL_REVIEW,
            exch_code=None, confidence=Confidence.LOW,
            classification_reason="UNKNOWN_DOT_SUFFIX",
            manual_review=True,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 13: Dash-Letter (Share-Class) ------------------------
    m = _RE_DASH_LETTER.match(raw)
    if m:
        root, letter = m.group(1), m.group(2)
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.SHARECLASS,
            api_strategy=ApiStrategy.YFINANCE_DIRECT,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="SHARECLASS_DASH_LETTER",
            manual_review=False,
            series_letter=letter, status_suffix=None,
        )

    # ------- Schritt 14: Numerischer Suffix -------------------------------
    m = _RE_NUMERIC_SUFFIX.match(raw)
    if m:
        alpha = m.group(1)
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=alpha,
            ticker_type=TickerType.NUMERIC_SUFFIX,
            api_strategy=ApiStrategy.YFINANCE_DIRECT,
            exch_code=None, confidence=Confidence.MEDIUM,
            classification_reason="NUMERIC_SUFFIX",
            manual_review=True,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 15: Standard US-Ticker -------------------------------
    if _RE_US_STANDARD.match(raw):
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.STOCK,
            api_strategy=ApiStrategy.YFINANCE_DIRECT,
            exch_code=None, confidence=Confidence.HIGH,
            classification_reason="STOCK_US_STANDARD",
            manual_review=False,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 16: Long-Alpha (Datenmüll-Verdacht) ------------------
    if _RE_LONG_ALPHA.match(raw):
        return TickerClassification(
            raw_ticker=raw, company_name=company_name,
            ticker_base=raw,
            ticker_type=TickerType.LONG_ALPHA_SUSPECT,
            api_strategy=ApiStrategy.MANUAL_REVIEW,
            exch_code=None, confidence=Confidence.LOW,
            classification_reason="LONG_ALPHA_SUSPECT",
            manual_review=True,
            series_letter=None, status_suffix=None,
        )

    # ------- Schritt 17: Fallback -----------------------------------------
    return TickerClassification(
        raw_ticker=raw, company_name=company_name,
        ticker_base=raw,
        ticker_type=TickerType.UNKNOWN,
        api_strategy=ApiStrategy.MANUAL_REVIEW,
        exch_code=None, confidence=Confidence.LOW,
        classification_reason="UNKNOWN_FALLBACK",
        manual_review=True,
        series_letter=None, status_suffix=None,
    )
