"""Tests für utils.ticker_classifier.

Spezifikations-Quelle: rebuild_pitch.md §5.
Der Entscheidungsbaum §5.3 ist verbindlich; Tests sind strukturiert
entlang der Regel-Gruppen.
"""

import os

import pytest

from utils.ticker_classifier import (
    ApiStrategy,
    Confidence,
    KNOWN_DATA_GARBAGE,
    TickerType,
    YAHOO_EXCHANGE_SUFFIXES,
    YAHOO_TO_OPENFIGI_COMPOSITE,
    classify,
)


# ---------------------------------------------------------------------------
# Regel 15: Standard US-Ticker
# ---------------------------------------------------------------------------


class TestStandardStock:
    @pytest.mark.parametrize("ticker", ["AAPL", "NVDA", "MSFT", "T", "A"])
    def test_standard_us_tickers_are_stock(self, ticker):
        c = classify(ticker)
        assert c.ticker_type == TickerType.STOCK
        assert c.ticker_base == ticker
        assert c.api_strategy == ApiStrategy.YFINANCE_DIRECT
        assert c.confidence == Confidence.HIGH
        assert c.manual_review is False

    def test_single_letter_t_not_treasury(self):
        """Regressions-Wächter: T ist zu kurz für T-Bill-Pattern."""
        assert classify("T").ticker_type == TickerType.STOCK


# ---------------------------------------------------------------------------
# Regel 7: Preferred Stocks in allen Notationen
# ---------------------------------------------------------------------------


class TestPreferred:
    @pytest.mark.parametrize("ticker,expected_base,expected_series", [
        ("EP$C",     "EP PRC",    "C"),
        ("AAM-PA",   "AAM PRA",   "A"),
        ("ALIN-P-E", "ALIN PRE",  "E"),
        ("NEE.PRI",  "NEE PRI",   "I"),
        ("BAC.PL",   "BAC PRL",   "L"),
        ("O-P",      "O PR",      None),
    ])
    def test_preferred_bloomberg_normalization(self, ticker, expected_base, expected_series):
        c = classify(ticker)
        assert c.ticker_type == TickerType.PREFERRED
        assert c.ticker_base == expected_base
        assert c.series_letter == expected_series
        assert c.api_strategy == ApiStrategy.FIGI_MAPPING

    def test_status_suffix_cl_is_stripped_and_recorded(self):
        """STT-P-E-CL: '-CL' abschneiden, Rest klassifizieren, CL bewahren."""
        c = classify("STT-P-E-CL")
        assert c.ticker_type == TickerType.PREFERRED
        assert c.ticker_base == "STT PRE"
        assert c.series_letter == "E"
        assert c.status_suffix == "CL"
        assert "status_cl" in c.classification_reason

    @pytest.mark.parametrize("ticker,expected_parent", [
        ("EP$C",     "EP"),
        ("AAM-PA",   "AAM"),
        ("ALIN-P-E", "ALIN"),
        ("NEE.PRI",  "NEE"),
        ("BAC.PL",   "BAC"),
        ("O-P",      "O"),
        ("CFG-P-E",  "CFG"),
        ("T$A",      "T"),
    ])
    def test_preferred_parent_ticker(self, ticker, expected_parent):
        """parent_ticker liefert den Common-Stamm fuer yfinance-Lookup."""
        c = classify(ticker)
        assert c.parent_ticker == expected_parent

    def test_non_preferred_has_no_parent_ticker(self):
        """Nicht-Preferred haben parent_ticker=None (Default)."""
        assert classify("AAPL").parent_ticker is None
        assert classify("BRK.B").parent_ticker is None
        assert classify("SAP.DE").parent_ticker is None


# ---------------------------------------------------------------------------
# Regeln 12/13: Share-Classes (Dot und Dash)
# ---------------------------------------------------------------------------


class TestShareClass:
    @pytest.mark.parametrize("ticker,expected_base", [
        ("BRK.A", "BRK-A"),
        ("BRK.B", "BRK-B"),
        ("LGF.A", "LGF-A"),
        ("AKO-A", "AKO-A"),
    ])
    def test_shareclass_normalization_to_dash(self, ticker, expected_base):
        c = classify(ticker)
        assert c.ticker_type == TickerType.SHARECLASS
        assert c.ticker_base == expected_base
        assert c.api_strategy == ApiStrategy.YFINANCE_DIRECT
        assert c.manual_review is False


# ---------------------------------------------------------------------------
# Regel 12: International (Dot + Yahoo-Suffix)
# ---------------------------------------------------------------------------


class TestInternational:
    @pytest.mark.parametrize("ticker,expected_exch", [
        ("SAP.DE",  "GR"),
        ("VOD.L",   "LN"),
        ("7203.T",  "JP"),
        ("SBUX.SW", "SW"),
        ("IBM.MX",  "MM"),
    ])
    def test_foreign_stock_mapping(self, ticker, expected_exch):
        c = classify(ticker)
        assert c.ticker_type == TickerType.FOREIGN_STOCK
        assert c.exch_code == expected_exch
        assert c.api_strategy == ApiStrategy.FIGI_MAPPING

    def test_frankfurt_f_suffix_is_international_not_shareclass(self):
        c = classify("XYZ.F")
        assert c.ticker_type == TickerType.FOREIGN_STOCK
        assert c.exch_code == "GR"


# ---------------------------------------------------------------------------
# Regel 14: Numerischer Suffix (Quiver-spezifisch)
# ---------------------------------------------------------------------------


class TestNumericSuffix:
    @pytest.mark.parametrize("ticker,expected_base", [
        ("AGN1",  "AGN"),
        ("PYPL1", "PYPL"),
        ("XYZ9",  "XYZ"),
    ])
    def test_numeric_suffix(self, ticker, expected_base):
        c = classify(ticker)
        assert c.ticker_type == TickerType.NUMERIC_SUFFIX
        assert c.ticker_base == expected_base
        assert c.manual_review is True
        assert c.api_strategy == ApiStrategy.YFINANCE_DIRECT


# ---------------------------------------------------------------------------
# Disambiguierung — die kritischen Kreuzungen im Entscheidungsbaum
# ---------------------------------------------------------------------------


class TestDisambiguation:
    def test_dot_F_is_intl(self):
        """F (Frankfurt Floor) in YAHOO → FOREIGN_STOCK, nicht SHARECLASS."""
        c = classify("BMW.F")
        assert c.ticker_type == TickerType.FOREIGN_STOCK
        assert c.exch_code == "GR"

    def test_dot_single_letter_not_in_whitelist_is_shareclass(self):
        """Single-Letter-Suffix, das nicht Exchange-Suffix ist, → SHARECLASS."""
        c = classify("BRK.B")  # B ist kein Yahoo-Suffix
        assert c.ticker_type == TickerType.SHARECLASS
        assert c.ticker_base == "BRK-B"

    def test_dot_PL_is_preferred_not_warsaw(self):
        """BAC.PL: 'PL' ist kein Yahoo-Suffix → PREFERRED via DOT_PX-Pattern."""
        c = classify("BAC.PL")
        assert c.ticker_type == TickerType.PREFERRED
        assert c.ticker_base == "BAC PRL"

    def test_dot_PA_is_foreign_paris_not_preferred(self):
        """X.PA: 'PA' IST in Yahoo (Paris) → PREF_DOT_PX-Guard hält stand."""
        c = classify("AIR.PA")
        assert c.ticker_type == TickerType.FOREIGN_STOCK
        assert c.exch_code == "FP"

    def test_multichar_unknown_dot_suffix(self):
        """2+-Zeichen-Suffix weder Exchange noch Preferred → UNKNOWN."""
        c = classify("BUNT.RP")
        assert c.ticker_type == TickerType.UNKNOWN
        assert c.manual_review is True

    def test_dash_letter_without_p_is_shareclass(self):
        """AKO-A: nicht -P, nicht -PA, nicht -P-A → DASH_LETTER-Share-Class."""
        c = classify("AKO-A")
        assert c.ticker_type == TickerType.SHARECLASS
        assert c.ticker_base == "AKO-A"


# ---------------------------------------------------------------------------
# Sonderformen: T-Bills, Options, Crypto, Warrants, Units, Misc, WI/WD
# ---------------------------------------------------------------------------


class TestSpecialForms:
    def test_tbill(self):
        # 'T' + 8-9 alphanumerische Zeichen
        c = classify("T1234567A8")
        assert c.ticker_type == TickerType.TREASURY
        assert c.api_strategy == ApiStrategy.SKIP

    def test_option_occ_format(self):
        # Root(4) + Datum(6) + C + Strike(8) = 19 Zeichen
        c = classify("MSFT240115C00400000")
        assert c.ticker_type == TickerType.OPTION
        assert c.api_strategy == ApiStrategy.SKIP

    def test_crypto_pair(self):
        c = classify("BTC-USD")
        assert c.ticker_type == TickerType.CRYPTO
        assert c.api_strategy == ApiStrategy.SKIP

    def test_warrant_dash_wt(self):
        c = classify("SPAC-WT")
        assert c.ticker_type == TickerType.WARRANT

    def test_warrant_dot_w(self):
        c = classify("SPAC.W")
        assert c.ticker_type == TickerType.WARRANT

    def test_unit_dot_u(self):
        c = classify("SPAC.U")
        assert c.ticker_type == TickerType.UNIT

    def test_misc_dot_z(self):
        c = classify("FOO.Z")
        assert c.ticker_type == TickerType.MISC

    def test_when_issued_wi(self):
        c = classify("XYZ.WI")
        assert c.ticker_type == TickerType.WHEN_ISSUED
        assert c.status_suffix == "WI"
        assert c.api_strategy == ApiStrategy.SKIP

    def test_when_issued_wd(self):
        c = classify("XYZ-WD")
        assert c.ticker_type == TickerType.WHEN_ISSUED
        assert c.status_suffix == "WD"
        assert c.api_strategy == ApiStrategy.SKIP

    def test_cusip_bond(self):
        # 9 alnum mit ≥1 Ziffer
        c = classify("91282CJW3")
        assert c.ticker_type == TickerType.BOND
        assert c.api_strategy == ApiStrategy.CUSIP_LOOKUP


# ---------------------------------------------------------------------------
# Corner-Cases: leer, None, Whitespace, KNOWN_DATA_GARBAGE, Long-Alpha
# ---------------------------------------------------------------------------


class TestCornerCases:
    def test_empty_string(self):
        c = classify("")
        assert c.ticker_type == TickerType.INVALID
        assert c.api_strategy == ApiStrategy.SKIP
        assert c.manual_review is False

    def test_none(self):
        c = classify(None)
        assert c.ticker_type == TickerType.INVALID
        assert c.manual_review is False

    def test_whitespace_only(self):
        c = classify("   ")
        assert c.ticker_type == TickerType.INVALID

    def test_starts_with_dot_is_invalid(self):
        c = classify(".BE")
        assert c.ticker_type == TickerType.INVALID
        assert c.classification_reason == "INVALID_NON_ALNUM_PREFIX"

    @pytest.mark.parametrize("garbage", sorted(KNOWN_DATA_GARBAGE))
    def test_known_data_garbage_is_invalid(self, garbage):
        c = classify(garbage)
        assert c.ticker_type == TickerType.INVALID
        assert c.classification_reason == "INVALID_KNOWN_GARBAGE"
        assert c.manual_review is False

    def test_long_alpha_suspect(self):
        # 6+ Buchstaben, nicht in KNOWN_DATA_GARBAGE
        c = classify("ABCDEFG")
        assert c.ticker_type == TickerType.LONG_ALPHA_SUSPECT
        assert c.manual_review is True
        assert c.api_strategy == ApiStrategy.MANUAL_REVIEW


# ---------------------------------------------------------------------------
# Determinismus und null-safe Wiederholbarkeit
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize("ticker", ["AAPL", "BRK.B", "EP$C", "BUNT.RP", "", None])
    def test_same_input_same_output(self, ticker):
        a = classify(ticker, "Apple Inc")
        b = classify(ticker, "Apple Inc")
        assert a == b


# ---------------------------------------------------------------------------
# Konsistenz-Checks der Konstanten
# ---------------------------------------------------------------------------


class TestConstants:
    def test_yahoo_to_openfigi_is_subset_of_yahoo_suffixes(self):
        missing = set(YAHOO_TO_OPENFIGI_COMPOSITE) - YAHOO_EXCHANGE_SUFFIXES
        assert not missing, f"Codes nicht in YAHOO_EXCHANGE_SUFFIXES: {missing}"

    def test_expected_suffix_counts(self):
        """Zählungen schützen vor versehentlichem Entfernen von Einträgen.

        Hinweis: Pitch §5.2 kommentiert '92 / 25 / 12', die abgedruckte
        Liste selbst enthält aber 81 / 26 / 12. Wir verpflichten uns auf
        die Listen (Quelle der Wahrheit), nicht auf den Kommentar.
        """
        assert len(YAHOO_EXCHANGE_SUFFIXES) == 81
        assert len(YAHOO_TO_OPENFIGI_COMPOSITE) == 26
        assert len(KNOWN_DATA_GARBAGE) == 12


# ---------------------------------------------------------------------------
# Regression gegen unbekannte Tickers (wird vom User befüllt)
# ---------------------------------------------------------------------------


class TestRegression:
    FIXTURE_PATH = os.path.join(
        os.path.dirname(__file__), "fixtures", "all_unknown_tickers.txt"
    )

    def _load_tickers(self):
        if not os.path.isfile(self.FIXTURE_PATH):
            return []
        with open(self.FIXTURE_PATH, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]

    def test_fixture_exists(self):
        """Datei muss existieren (leer mit Kommentar ist OK)."""
        assert os.path.isfile(self.FIXTURE_PATH), (
            f"Fixture fehlt: {self.FIXTURE_PATH}"
        )

    def test_no_ticker_reaches_fallback(self):
        """Kein Ticker darf beim Fallback (Schritt 17) landen.

        Bewusste UNKNOWN-Ergebnisse aus konkreten Regeln (z.B.
        UNKNOWN_DOT_SUFFIX für BUNT.RP nach Pitch §5.3 Schritt 12) sind
        erlaubt — sie werden über manual_review=True in die Review-CSV
        geschrieben. Nur UNKNOWN_FALLBACK zeigt, dass ein Ticker durch
        alle 16 Regeln gefallen ist, und das ist ein Regel-Lücken-Bug.
        """
        tickers = self._load_tickers()
        if not tickers:
            pytest.skip(
                "Fixture ist leer. User muss all_unknown_tickers.txt befüllen, "
                "damit dieser Regressions-Test echte Arbeit leistet."
            )
        fell_through = [
            t for t in tickers
            if classify(t).classification_reason == "UNKNOWN_FALLBACK"
        ]
        assert not fell_through, (
            f"{len(fell_through)} Tickers erreichen den Fallback (Regel-Lücke!), "
            f"z.B.: {fell_through[:20]}"
        )


# ---------------------------------------------------------------------------
# Muni-Override: UNIT/PREFERRED-Pattern + Muni-Marker in asset_name → BOND/SKIP
# ---------------------------------------------------------------------------


class TestMuniOverride:
    @pytest.mark.parametrize("ticker,asset_name", [
        ("BLOC-U",     "BULLOCH CNTY GA DEV AUTH"),
        ("BLOC-U",     "BLOUNT CNTY TENN PUBBLDG AUTH ARS REVBDS GOVT PUB IMPT"),
        ("ALIN-P-E",   "ALVIN TEX INDPT SCHDIST VAR RT ULTD TAXSCHOOLHOUSE BDS"),
        ("T-P-A",      "TAMPA FLA HOSP REV BDS H"),
        ("STT-P-E-CL", "STATE COLLEGE PA AREA"),
    ])
    def test_muni_override_triggers(self, ticker, asset_name):
        c = classify(ticker, asset_name)
        assert c.ticker_type == TickerType.BOND
        assert c.api_strategy == ApiStrategy.SKIP
        assert c.classification_reason.startswith("MUNI_DETECTED")
        assert c.manual_review is False

    @pytest.mark.parametrize("ticker,asset_name,expected_type", [
        ("WRB-PG", "W.R. BERKLEY CORPORATION CMN",       TickerType.PREFERRED),
        ("EP$C",   "EL PASO CORPORATION PREFERRED STOCK", TickerType.PREFERRED),
        ("AAM-PA", "APOLLO GLOBAL MANAGEMENT INC CMN",    TickerType.PREFERRED),
        ("GE-W",   "GENERAL ELEC CO",                     TickerType.WARRANT),
    ])
    def test_no_override_for_real_corporate_tickers(self, ticker, asset_name, expected_type):
        c = classify(ticker, asset_name)
        assert c.ticker_type == expected_type
        assert c.classification_reason != "MUNI_DETECTED"

    def test_no_override_without_asset_name(self):
        c = classify("BLOC-U")
        assert c.ticker_type == TickerType.UNIT
        assert c.classification_reason == "UNIT_U"

    def test_no_override_for_stock(self):
        c = classify("AAPL", "BULLOCH CNTY GA DEV AUTH")
        assert c.ticker_type == TickerType.STOCK
        assert c.classification_reason == "STOCK_US_STANDARD"
