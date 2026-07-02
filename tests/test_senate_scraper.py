"""
Unit-Tests fuer scrapers/senate_scraper.py

Verwendet pytest + unittest.mock -- kein echter Browser, kein Playwright-Start.
"""

import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

# Projekt-Root in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scrapers.senate_scraper import SenateScraper, CSV_COLUMNS, SENATE_SESSION_START


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def scraper(tmp_path):
    """SenateScraper mit output_path in tmp_path."""
    s = SenateScraper(headless=True)
    s._output_path = str(tmp_path / "senate_trades.csv")
    return s


@pytest.fixture
def sample_trades():
    """Liste von Trade-Dicts (wie sie in scraper.trades stehen)."""
    return [
        {
            "name": "John Smith",
            "ticker": "AAPL",
            "asset_name": "Apple Inc",
            "asset_type": "Stock",
            "transaction_type": "Purchase",
            "amount": "$1,001 - $15,000",
            "transaction_date": "01/15/2025",
            "owner": "Self",
            "comment": "",
            "disclosure_date": "02/01/2025",
            "ptr_link": "https://efdsearch.senate.gov/search/view/ptr/123/",
        },
        {
            "name": "Jane Doe",
            "ticker": "MSFT",
            "asset_name": "Microsoft Corp",
            "asset_type": "Stock",
            "transaction_type": "Sale (Full)",
            "amount": "$15,001 - $50,000",
            "transaction_date": "01/20/2025",
            "owner": "Joint",
            "comment": "Partial sale",
            "disclosure_date": "02/10/2025",
            "ptr_link": "https://efdsearch.senate.gov/search/view/ptr/456/",
        },
    ]


@pytest.fixture
def sample_df(sample_trades):
    """DataFrame aus sample_trades."""
    return pd.DataFrame(sample_trades, columns=CSV_COLUMNS)


def _make_mock_cell(text: str) -> MagicMock:
    """Erzeugt ein Mock-Objekt das inner_text() zurueckgibt."""
    cell = MagicMock()
    cell.inner_text.return_value = text
    return cell


# ============================================================================
# 1. Initialisierung
# ============================================================================

class TestInit:
    def test_default_headless(self):
        s = SenateScraper()
        assert s.headless is True

    def test_headless_false(self):
        s = SenateScraper(headless=False)
        assert s.headless is False

    def test_trades_initially_empty(self):
        s = SenateScraper()
        assert s.trades == []

    def test_pdf_skipped_initially_empty(self):
        s = SenateScraper()
        assert s.pdf_skipped == []

    def test_visited_urls_initially_empty(self):
        s = SenateScraper()
        assert s._visited_urls == set()

    def test_output_path_contains_senate_trades_csv(self):
        s = SenateScraper()
        # Normalisiere Pfad-Separatoren fuer plattformuebergreifende Kompatibilitaet
        normalized = s._output_path.replace("\\", "/")
        assert normalized.endswith("data/raw/senate_trades.csv")


# ============================================================================
# 2. save() Methode
# ============================================================================

class TestSave:
    def test_empty_df_returns_path_without_writing(self, scraper):
        """Leerer DataFrame -> Warning, keine Datei erstellt."""
        empty_df = pd.DataFrame(columns=CSV_COLUMNS)
        result = scraper.save(empty_df)
        assert result == scraper._output_path
        assert not os.path.isfile(scraper._output_path)

    def test_new_file_created(self, scraper, sample_df):
        """Neuer DataFrame wird als neue Datei gespeichert."""
        path = scraper.save(sample_df)
        assert os.path.isfile(path)
        saved = pd.read_csv(path)
        assert len(saved) == 2
        assert list(saved.columns) == CSV_COLUMNS

    def test_append_mode(self, scraper, sample_trades, sample_df):
        """Neue Daten werden an bestehende Datei angehaengt."""
        # Erste Datei schreiben
        scraper.save(sample_df)

        # Neuen Trade erstellen
        new_trade = sample_trades[0].copy()
        new_trade["name"] = "Bob Builder"
        new_trade["ticker"] = "TSLA"
        new_trade["ptr_link"] = "https://efdsearch.senate.gov/search/view/ptr/789/"
        new_df = pd.DataFrame([new_trade], columns=CSV_COLUMNS)

        scraper.save(new_df)
        saved = pd.read_csv(scraper._output_path)
        assert len(saved) == 3

    def test_deduplication(self, scraper, sample_df):
        """Duplikate werden beim Append entfernt."""
        scraper.save(sample_df)
        # Nochmal die gleichen Daten speichern
        scraper.save(sample_df)
        saved = pd.read_csv(scraper._output_path)
        assert len(saved) == 2  # keine Duplikate

    def test_dedup_keeps_last(self, scraper, sample_trades):
        """Bei identischen Trades (inkl. comment) wird 'last' behalten."""
        df1 = pd.DataFrame([sample_trades[0]], columns=CSV_COLUMNS)
        scraper.save(df1)

        # Gleichen Trade mit neuem disclosure_date erneut speichern
        updated = sample_trades[0].copy()
        updated["disclosure_date"] = "03/01/2025"
        df2 = pd.DataFrame([updated], columns=CSV_COLUMNS)
        scraper.save(df2)

        saved = pd.read_csv(scraper._output_path)
        assert len(saved) == 1
        # disclosure_date ist NICHT im Dedup-Subset → last wird behalten
        assert saved.iloc[0]["disclosure_date"] == "03/01/2025"

    def test_different_comment_not_deduplicated(self, scraper, sample_trades):
        """Trades mit unterschiedlichem comment sind NICHT identisch (comment ist im Dedup-Subset)."""
        df1 = pd.DataFrame([sample_trades[0]], columns=CSV_COLUMNS)
        scraper.save(df1)

        updated = sample_trades[0].copy()
        updated["comment"] = "Aktualisiert"
        df2 = pd.DataFrame([updated], columns=CSV_COLUMNS)
        scraper.save(df2)

        saved = pd.read_csv(scraper._output_path)
        assert len(saved) == 2

    def test_save_uses_self_trades_when_no_df(self, scraper, sample_trades):
        """save() ohne Parameter baut DataFrame aus self.trades."""
        scraper.trades = sample_trades
        scraper.save()
        saved = pd.read_csv(scraper._output_path)
        assert len(saved) == 2


# ============================================================================
# 3. _get_last_disclosure_date()
# ============================================================================

class TestGetLastDisclosureDate:
    def test_no_file_returns_none(self, scraper):
        """Keine CSV-Datei vorhanden -> None."""
        result = scraper._get_last_disclosure_date()
        assert result is None

    def test_empty_csv_returns_none(self, scraper):
        """Leere CSV (nur Header) -> None."""
        empty_df = pd.DataFrame(columns=CSV_COLUMNS)
        empty_df.to_csv(scraper._output_path, index=False)
        result = scraper._get_last_disclosure_date()
        assert result is None

    def test_csv_with_data_returns_latest_date(self, scraper):
        """CSV mit Daten -> neuestes disclosure_date im MM/DD/YYYY Format."""
        data = pd.DataFrame({
            "name": ["A", "B"],
            "ticker": ["X", "Y"],
            "asset_name": ["", ""],
            "asset_type": ["", ""],
            "transaction_type": ["Purchase", "Purchase"],
            "amount": ["", ""],
            "transaction_date": ["01/01/2025", "01/05/2025"],
            "owner": ["", ""],
            "comment": ["", ""],
            "disclosure_date": ["01/10/2025", "02/15/2025"],
            "ptr_link": ["", ""],
        })
        data.to_csv(scraper._output_path, index=False)
        result = scraper._get_last_disclosure_date()
        # 1 Tag Sicherheitspuffer: 02/15 - 1 Tag = 02/14
        assert result == "02/14/2025"

    def test_corrupted_csv_returns_none(self, scraper):
        """Fehlerhafte CSV -> None (Exception wird gefangen)."""
        with open(scraper._output_path, "w") as f:
            f.write("this,is,not,valid\ngarbage\x00data")
        result = scraper._get_last_disclosure_date()
        # Sollte None zurueckgeben, nicht crashen
        assert result is None

    def test_csv_without_disclosure_date_column_returns_none(self, scraper):
        """CSV ohne disclosure_date Spalte -> None."""
        data = pd.DataFrame({"name": ["A"], "ticker": ["X"]})
        data.to_csv(scraper._output_path, index=False)
        result = scraper._get_last_disclosure_date()
        assert result is None


# ============================================================================
# 4. get_summary()
# ============================================================================

class TestGetSummary:
    def test_empty_df(self, scraper):
        """Leerer DataFrame -> Zusammenfassung mit 0 Trades."""
        empty_df = pd.DataFrame(columns=CSV_COLUMNS)
        summary = scraper.get_summary(empty_df)
        assert "Trades gefunden:       0" in summary
        assert "PDFs übersprungen:     0" in summary

    def test_summary_with_trades(self, scraper, sample_df):
        """DataFrame mit Trades -> korrekte Statistiken."""
        scraper.pdf_skipped = [{"name": "X"}]
        summary = scraper.get_summary(sample_df)
        assert "Trades gefunden:       2" in summary
        assert "PDFs übersprungen:     1" in summary
        assert "Eindeutige Senatoren:  2" in summary
        assert "John Smith" in summary
        assert "Jane Doe" in summary
        assert "Purchase" in summary
        assert "Sale (Full)" in summary

    def test_summary_uses_self_trades_when_no_df(self, scraper, sample_trades):
        """get_summary() ohne Parameter baut DataFrame aus self.trades."""
        scraper.trades = sample_trades
        summary = scraper.get_summary()
        assert "Trades gefunden:       2" in summary

    def test_summary_top5_limit(self, scraper):
        """Nur Top 5 Senatoren werden angezeigt."""
        trades = []
        for i in range(7):
            t = {col: "" for col in CSV_COLUMNS}
            t["name"] = f"Senator_{i}"
            t["transaction_type"] = "Purchase"
            trades.append(t)
        df = pd.DataFrame(trades, columns=CSV_COLUMNS)
        summary = scraper.get_summary(df)
        # Alle 7 Senatoren haben je 1 Trade, head(5) zeigt nur 5
        senator_lines = [l for l in summary.split("\n") if "Senator_" in l]
        assert len(senator_lines) == 5


# ============================================================================
# 5. _extract_transactions() Logik
# ============================================================================

class TestExtractTransactions:
    def _make_transaction_row(self, ticker="AAPL", tx_date="01/15/2025",
                               owner="Self", asset_name="Apple Inc",
                               asset_type="Stock", tx_type="Purchase",
                               amount="$1,001 - $15,000", comment=""):
        """Erzeugt eine Mock-Row mit 9 Zellen (Spalte 0 = Zeilennummer).

        cells[4] (Asset Name) bekommt explizite locator()-Unterstützung:
        count()=0 bedeutet kein div.text-muted vorhanden (normales Aktien-Verhalten).
        """
        # cells[4] braucht locator()-Unterstützung für den Bond-Metadaten-Fix
        # .all() gibt leere Liste zurück → kein div.text-muted (Standard: Aktien)
        asset_name_cell = MagicMock()
        asset_name_cell.inner_text.return_value = asset_name
        bond_meta_locator = MagicMock()
        bond_meta_locator.all.return_value = []
        asset_name_cell.locator.return_value = bond_meta_locator

        cells = [
            _make_mock_cell("1"),        # 0: #
            _make_mock_cell(tx_date),    # 1: Transaction Date
            _make_mock_cell(owner),      # 2: Owner
            _make_mock_cell(ticker),     # 3: Ticker
            asset_name_cell,             # 4: Asset Name (mit locator-Unterstützung)
            _make_mock_cell(asset_type), # 5: Asset Type
            _make_mock_cell(tx_type),    # 6: Type
            _make_mock_cell(amount),     # 7: Amount
            _make_mock_cell(comment),    # 8: Comment
        ]
        row = MagicMock()
        row.locator.return_value.all.return_value = cells
        return row

    def test_basic_extraction(self, scraper):
        """Normale Transaktion wird korrekt extrahiert."""
        row = self._make_transaction_row()
        mock_detail_page = MagicMock()
        mock_detail_page.locator.return_value.all.return_value = [row]

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_detail_page

        scraper._extract_transactions(
            mock_context, "John Smith", "02/01/2025",
            "https://efdsearch.senate.gov/search/view/ptr/123/"
        )

        assert len(scraper.trades) == 1
        trade = scraper.trades[0]
        assert trade["name"] == "John Smith"
        assert trade["ticker"] == "AAPL"
        assert trade["asset_name"] == "Apple Inc"
        assert trade["transaction_type"] == "Purchase"
        assert trade["disclosure_date"] == "02/01/2025"
        assert trade["ptr_link"] == "https://efdsearch.senate.gov/search/view/ptr/123/"

    def test_ticker_double_dash_becomes_empty(self, scraper):
        """Ticker '--' wird zu leerem String."""
        row = self._make_transaction_row(ticker="--")
        mock_detail_page = MagicMock()
        mock_detail_page.locator.return_value.all.return_value = [row]

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_detail_page

        scraper._extract_transactions(
            mock_context, "Jane Doe", "02/01/2025",
            "https://efdsearch.senate.gov/search/view/ptr/456/"
        )

        assert scraper.trades[0]["ticker"] == ""

    def test_less_than_9_cells_skipped(self, scraper):
        """Zeile mit weniger als 9 Zellen wird uebersprungen."""
        short_cells = [_make_mock_cell("x") for _ in range(5)]
        short_row = MagicMock()
        short_row.locator.return_value.all.return_value = short_cells

        mock_detail_page = MagicMock()
        mock_detail_page.locator.return_value.all.return_value = [short_row]

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_detail_page

        scraper._extract_transactions(
            mock_context, "Test", "01/01/2025", "http://example.com"
        )

        assert len(scraper.trades) == 0

    def test_multiple_transactions(self, scraper):
        """Mehrere Transaktionszeilen werden alle extrahiert."""
        rows = [
            self._make_transaction_row(ticker="AAPL"),
            self._make_transaction_row(ticker="MSFT"),
            self._make_transaction_row(ticker="GOOG"),
        ]
        mock_detail_page = MagicMock()
        mock_detail_page.locator.return_value.all.return_value = rows

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_detail_page

        scraper._extract_transactions(
            mock_context, "Multi Trader", "02/01/2025", "http://example.com/ptr/1"
        )

        assert len(scraper.trades) == 3
        tickers = [t["ticker"] for t in scraper.trades]
        assert tickers == ["AAPL", "MSFT", "GOOG"]

    @patch("scrapers.senate_scraper.time.sleep")
    def test_detail_page_is_closed(self, mock_sleep, scraper):
        """Detail-Page wird immer geschlossen (auch bei Fehler mit Retry)."""
        mock_detail_page = MagicMock()
        mock_detail_page.goto.side_effect = Exception("timeout")

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_detail_page

        with pytest.raises(Exception):
            scraper._extract_transactions(
                mock_context, "Test", "01/01/2025", "http://example.com"
            )

        # Bei 3 Retry-Versuchen wird die Seite 3x geschlossen
        from config.settings import SCRAPER_RETRY_ATTEMPTS
        assert mock_detail_page.close.call_count == SCRAPER_RETRY_ATTEMPTS


# ============================================================================
# 6. _scrape_results() und _fetch_all_report_links() Logik
# ============================================================================

def _make_report(name="John Doe", disclosure_date="02/01/2025",
                 href="/search/view/ptr/123/"):
    """Erzeugt ein Report-Dict wie es _fetch_all_report_links() zurueckgibt."""
    return {"name": name, "disclosure_date": disclosure_date, "href": href}


class TestScrapeResults:
    """_scrape_results() verwendet _fetch_all_report_links() (gemockt) als Datenquelle."""

    @patch.object(SenateScraper, "_extract_transactions")
    @patch("scrapers.senate_scraper.time.sleep")
    def test_pdf_links_skipped(self, mock_sleep, mock_extract, scraper):
        """PDF-Links (/search/view/paper/) werden uebersprungen."""
        reports = [_make_report("John Smith", href="/search/view/paper/123/")]
        page, context = MagicMock(), MagicMock()
        with patch.object(scraper, "_fetch_all_report_links", return_value=reports):
            scraper._scrape_results(page, context, "01/01/2025")

        mock_extract.assert_not_called()
        assert len(scraper.pdf_skipped) == 1
        assert scraper.pdf_skipped[0]["name"] == "John Smith"

    @patch.object(SenateScraper, "_extract_transactions")
    @patch("scrapers.senate_scraper.time.sleep")
    def test_ptr_links_processed(self, mock_sleep, mock_extract, scraper):
        """PTR-Links (/search/view/ptr/) werden verarbeitet."""
        reports = [_make_report("Jane Doe", href="/search/view/ptr/456/")]
        page, context = MagicMock(), MagicMock()
        with patch.object(scraper, "_fetch_all_report_links", return_value=reports):
            scraper._scrape_results(page, context, "01/01/2025")

        mock_extract.assert_called_once()
        args = mock_extract.call_args[0]
        assert args[1] == "Jane Doe"
        assert "ptr/456" in args[3]

    @patch.object(SenateScraper, "_extract_transactions")
    @patch("scrapers.senate_scraper.time.sleep")
    def test_visited_urls_not_reprocessed(self, mock_sleep, mock_extract, scraper):
        """Bereits besuchte URLs werden nicht doppelt verarbeitet."""
        full_url = "https://efdsearch.senate.gov/search/view/ptr/456/"
        scraper._visited_urls.add(full_url)
        reports = [_make_report(href="/search/view/ptr/456/")]
        page, context = MagicMock(), MagicMock()
        with patch.object(scraper, "_fetch_all_report_links", return_value=reports):
            scraper._scrape_results(page, context, "01/01/2025")

        mock_extract.assert_not_called()

    @patch.object(SenateScraper, "_extract_transactions")
    @patch("scrapers.senate_scraper.time.sleep")
    def test_empty_reports_no_extract(self, mock_sleep, mock_extract, scraper):
        """Leere Report-Liste -> kein _extract_transactions."""
        page, context = MagicMock(), MagicMock()
        with patch.object(scraper, "_fetch_all_report_links", return_value=[]):
            scraper._scrape_results(page, context, "01/01/2025")

        mock_extract.assert_not_called()

    @patch.object(SenateScraper, "_extract_transactions")
    @patch("scrapers.senate_scraper.time.sleep")
    def test_rate_limiting_called(self, mock_sleep, mock_extract, scraper):
        """time.sleep wird nach jedem PTR-Report aufgerufen."""
        reports = [_make_report()]
        page, context = MagicMock(), MagicMock()
        with patch.object(scraper, "_fetch_all_report_links", return_value=reports):
            scraper._scrape_results(page, context, "01/01/2025")

        from config.settings import SCRAPER_RATE_LIMIT_SECONDS
        mock_sleep.assert_called_with(SCRAPER_RATE_LIMIT_SECONDS)

    @patch.object(SenateScraper, "_extract_transactions")
    @patch("scrapers.senate_scraper.time.sleep")
    def test_unknown_link_type_skipped(self, mock_sleep, mock_extract, scraper):
        """Unbekannter Link-Typ (weder ptr noch paper) wird uebersprungen."""
        reports = [_make_report(href="/search/view/annual/999/")]
        page, context = MagicMock(), MagicMock()
        with patch.object(scraper, "_fetch_all_report_links", return_value=reports):
            scraper._scrape_results(page, context, "01/01/2025")

        mock_extract.assert_not_called()
        assert len(scraper.pdf_skipped) == 0

    @patch.object(SenateScraper, "_extract_transactions")
    @patch("scrapers.senate_scraper.time.sleep")
    def test_absolute_href_used_as_is(self, mock_sleep, mock_extract, scraper):
        """Absolute URLs werden nicht nochmal mit Base-URL prefixed."""
        abs_url = "https://efdsearch.senate.gov/search/view/ptr/789/"
        reports = [_make_report(href=abs_url)]
        page, context = MagicMock(), MagicMock()
        with patch.object(scraper, "_fetch_all_report_links", return_value=reports):
            scraper._scrape_results(page, context, "01/01/2025")

        args = mock_extract.call_args[0]
        assert args[3] == abs_url

    @patch.object(SenateScraper, "_extract_transactions")
    @patch("scrapers.senate_scraper.time.sleep")
    def test_empty_href_logged_and_skipped(self, mock_sleep, mock_extract, scraper, caplog):
        """Leerer href wird als WARNING geloggt und uebersprungen."""
        import logging
        reports = [_make_report(href="")]
        page, context = MagicMock(), MagicMock()
        with patch.object(scraper, "_fetch_all_report_links", return_value=reports):
            with caplog.at_level(logging.WARNING, logger="scrapers.senate_scraper"):
                scraper._scrape_results(page, context, "01/01/2025")

        mock_extract.assert_not_called()
        assert any("Leerer href" in r.message for r in caplog.records)


class TestFetchAllReportLinks:
    """Tests fuer _fetch_all_report_links() – parst DataTables-API-Response."""

    def _make_api_response(self, rows):
        return {"recordsTotal": len(rows), "recordsFiltered": len(rows), "data": rows}

    def test_parses_single_ptr_row(self, scraper):
        """Einzelne PTR-Zeile wird korrekt geparst."""
        rows = [["John", "Doe", "John Doe",
                 '<a href="/search/view/ptr/123/">PTR</a>', "02/01/2025"]]
        api_resp = self._make_api_response(rows)
        page = MagicMock()
        page.evaluate.return_value = api_resp

        result = scraper._fetch_all_report_links(page, "01/01/2025", "01/31/2025")

        assert len(result) == 1
        assert result[0]["name"] == "John Doe"
        assert result[0]["href"] == "/search/view/ptr/123/"
        assert result[0]["disclosure_date"] == "02/01/2025"

    def test_html_stripped_from_names(self, scraper):
        """HTML-Tags werden aus Namen entfernt."""
        rows = [["<b>Jane</b>", "<b>Smith</b>", "",
                 '<a href="/search/view/ptr/1/">PTR</a>', "01/15/2025"]]
        page = MagicMock()
        page.evaluate.return_value = self._make_api_response(rows)

        result = scraper._fetch_all_report_links(page, "01/01/2025", None)

        assert result[0]["name"] == "Jane Smith"

    def test_empty_data_returns_empty_list(self, scraper):
        """Leere data-Liste ergibt leere Ergebnisliste."""
        page = MagicMock()
        page.evaluate.return_value = {"recordsTotal": 0, "recordsFiltered": 0, "data": []}

        result = scraper._fetch_all_report_links(page, "01/01/2025", None)

        assert result == []

    def test_short_row_skipped(self, scraper):
        """Zeile mit weniger als 5 Elementen wird uebersprungen."""
        rows = [["John", "Doe"]]  # nur 2 Elemente
        page = MagicMock()
        page.evaluate.return_value = self._make_api_response(rows)

        result = scraper._fetch_all_report_links(page, "01/01/2025", None)

        assert result == []

    def test_multiple_rows_parsed(self, scraper):
        """Mehrere Zeilen werden alle geparst."""
        rows = [
            ["Jane", "A", "", '<a href="/search/view/ptr/1/">PTR</a>', "01/10/2025"],
            ["Bob", "B", "", '<a href="/search/view/paper/2/">PDF</a>', "01/20/2025"],
        ]
        page = MagicMock()
        page.evaluate.return_value = self._make_api_response(rows)

        result = scraper._fetch_all_report_links(page, "01/01/2025", "01/31/2025")

        assert len(result) == 2
        assert result[0]["name"] == "Jane A"
        assert result[1]["href"] == "/search/view/paper/2/"


# ============================================================================
# 7. Delta-Logik im run()
# ============================================================================

class TestRunDeltaLogic:
    @patch("playwright.sync_api.sync_playwright")
    def test_delta_true_no_csv_uses_session_start(self, mock_pw, scraper):
        """delta=True ohne bestehende CSV -> SENATE_SESSION_START."""
        # Mock Playwright chain
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_pw.return_value.__enter__ = MagicMock(
            return_value=MagicMock(chromium=MagicMock(launch=MagicMock(return_value=mock_browser)))
        )
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(scraper, "_accept_agreement"), \
             patch.object(scraper, "_scrape_results") as mock_scrape:
            scraper.run(from_date=None, delta=True)

        # _scrape_results sollte mit SENATE_SESSION_START als from_date aufgerufen worden sein
        call_args = mock_scrape.call_args[0]
        assert call_args[2] == SENATE_SESSION_START

    @patch("playwright.sync_api.sync_playwright")
    def test_delta_true_with_csv_uses_csv_date(self, mock_pw, scraper):
        """delta=True mit bestehender CSV -> Datum aus CSV."""
        # CSV mit Daten erstellen
        data = pd.DataFrame({col: ["test"] for col in CSV_COLUMNS})
        data["disclosure_date"] = "03/15/2025"
        data.to_csv(scraper._output_path, index=False)

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_pw.return_value.__enter__ = MagicMock(
            return_value=MagicMock(chromium=MagicMock(launch=MagicMock(return_value=mock_browser)))
        )
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(scraper, "_accept_agreement"), \
             patch.object(scraper, "_scrape_results") as mock_scrape:
            scraper.run(from_date=None, delta=True)

        call_args = mock_scrape.call_args[0]
        # 1 Tag Sicherheitspuffer: 03/15 - 1 Tag = 03/14
        assert call_args[2] == "03/14/2025"

    @patch("playwright.sync_api.sync_playwright")
    def test_delta_false_uses_given_from_date(self, mock_pw, scraper):
        """delta=False -> Verwendet uebergebenes from_date."""
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_pw.return_value.__enter__ = MagicMock(
            return_value=MagicMock(chromium=MagicMock(launch=MagicMock(return_value=mock_browser)))
        )
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(scraper, "_accept_agreement"), \
             patch.object(scraper, "_scrape_results") as mock_scrape:
            scraper.run(from_date="06/01/2025", delta=False)

        call_args = mock_scrape.call_args[0]
        assert call_args[2] == "06/01/2025"

    @patch("playwright.sync_api.sync_playwright")
    def test_delta_false_no_from_date_uses_session_start(self, mock_pw, scraper):
        """delta=False, from_date=None -> SENATE_SESSION_START (Fallback)."""
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_pw.return_value.__enter__ = MagicMock(
            return_value=MagicMock(chromium=MagicMock(launch=MagicMock(return_value=mock_browser)))
        )
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(scraper, "_accept_agreement"), \
             patch.object(scraper, "_scrape_results") as mock_scrape:
            scraper.run(from_date=None, delta=False)

        call_args = mock_scrape.call_args[0]
        assert call_args[2] == SENATE_SESSION_START

    @patch("playwright.sync_api.sync_playwright")
    def test_run_returns_dataframe(self, mock_pw, scraper):
        """run() gibt einen DataFrame mit CSV_COLUMNS zurueck."""
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_pw.return_value.__enter__ = MagicMock(
            return_value=MagicMock(chromium=MagicMock(launch=MagicMock(return_value=mock_browser)))
        )
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(scraper, "_accept_agreement"), \
             patch.object(scraper, "_scrape_results"):
            result = scraper.run(from_date="01/01/2025", delta=False)

        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == CSV_COLUMNS

    @patch("playwright.sync_api.sync_playwright")
    def test_run_closes_browser_on_error(self, mock_pw, scraper):
        """Browser wird auch bei Fehler geschlossen (finally-Block)."""
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_pw.return_value.__enter__ = MagicMock(
            return_value=MagicMock(chromium=MagicMock(launch=MagicMock(return_value=mock_browser)))
        )
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(scraper, "_accept_agreement", side_effect=Exception("boom")):
            with pytest.raises(Exception, match="boom"):
                scraper.run(from_date="01/01/2025", delta=False)

        mock_browser.close.assert_called_once()


# ============================================================================
# 8. Edge Cases
# ============================================================================

class TestEdgeCases:
    def test_csv_columns_constant(self):
        """CSV_COLUMNS hat die erwarteten 12 Spalten."""
        assert len(CSV_COLUMNS) == 12
        assert "name" in CSV_COLUMNS
        assert "ticker" in CSV_COLUMNS
        assert "ptr_link" in CSV_COLUMNS

    def test_senate_session_start_format(self):
        """SENATE_SESSION_START ist ein gueltiges Datum."""
        dt = datetime.strptime(SENATE_SESSION_START, "%m/%d/%Y")
        assert dt.year == 2025

    @patch.object(SenateScraper, "_extract_transactions", side_effect=Exception("Network error"))
    @patch("scrapers.senate_scraper.time.sleep")
    def test_extract_error_does_not_stop_scraping(self, mock_sleep, mock_extract, scraper):
        """Fehler bei _extract_transactions stoppt nicht den gesamten Scrape-Vorgang."""
        # Zwei PTR-Reports: erste wirft Exception, zweite sollte trotzdem versucht werden
        fake_reports = [
            {"name": "John Doe", "disclosure_date": "02/01/2025",
             "href": "/search/view/ptr/1/"},
            {"name": "Jane Smith", "disclosure_date": "02/05/2025",
             "href": "/search/view/ptr/2/"},
        ]

        page = MagicMock()
        context = MagicMock()

        with patch.object(scraper, "_fetch_all_report_links", return_value=fake_reports):
            # Sollte NICHT crashen trotz Exception im ersten _extract_transactions
            scraper._scrape_results(page, context, "01/01/2025", "02/28/2025")

        # Beide PTRs wurden versucht
        assert mock_extract.call_count == 2

    def test_save_creates_directory(self, scraper, sample_df, tmp_path):
        """save() erstellt das Zielverzeichnis falls es nicht existiert."""
        scraper._output_path = str(tmp_path / "new_subdir" / "senate_trades.csv")
        scraper.save(sample_df)
        assert os.path.isfile(scraper._output_path)


# ============================================================================
# 9. Neue Tests fuer Bug-Fixes
# ============================================================================

class TestStateReset:
    """Tests fuer Fix 1: State-Reset bei doppeltem run()."""

    @patch("playwright.sync_api.sync_playwright")
    def test_run_resets_state_on_second_call(self, mock_pw, scraper):
        """Zweiter run()-Aufruf resettet trades/pdf_skipped/visited_urls."""
        # Ersten Lauf simulieren - trades manuell setzen
        scraper.trades = [{"name": "old"}]
        scraper.pdf_skipped = [{"name": "old_pdf"}]
        scraper._visited_urls = {"http://old.com"}

        # Mock Playwright chain
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_pw.return_value.__enter__ = MagicMock(
            return_value=MagicMock(chromium=MagicMock(launch=MagicMock(return_value=mock_browser)))
        )
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(scraper, "_accept_agreement"), \
             patch.object(scraper, "_scrape_results"):
            scraper.run(from_date="01/01/2025", delta=False)

        # Nach run(): State sollte zurueckgesetzt sein (nur neue Trades)
        assert scraper.trades == []
        assert scraper.pdf_skipped == []
        assert scraper._visited_urls == set()


class TestRetryLogic:
    """Tests fuer Fix 2: Retry-Logik in _extract_transactions()."""

    @patch("scrapers.senate_scraper.time.sleep")
    def test_extract_transactions_retries_on_failure(self, mock_sleep, scraper):
        """_extract_transactions retried bei Netzwerk-Fehler."""
        # Mock: 1. und 2. Versuch schlagen fehl, 3. klappt
        fail_page_1 = MagicMock()
        fail_page_1.goto.side_effect = Exception("timeout")

        fail_page_2 = MagicMock()
        fail_page_2.goto.side_effect = Exception("timeout")

        success_page = MagicMock()
        # Leere Tabelle fuer den Erfolgsfall
        success_page.locator.return_value.all.return_value = []

        mock_context = MagicMock()
        mock_context.new_page.side_effect = [fail_page_1, fail_page_2, success_page]

        # Sollte nicht crashen - 3. Versuch klappt
        scraper._extract_transactions(
            mock_context, "Test Senator", "01/01/2025", "http://example.com/ptr/1/"
        )

        # 3 Seiten erstellt
        assert mock_context.new_page.call_count == 3
        # 2 fehlgeschlagene Seiten geschlossen + 1 erfolgreiche geschlossen
        assert fail_page_1.close.call_count == 1
        assert fail_page_2.close.call_count == 1
        assert success_page.close.call_count == 1
        # sleep wurde 2x aufgerufen (zwischen Retries)
        from config.settings import SCRAPER_RETRY_DELAY_SECONDS
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(SCRAPER_RETRY_DELAY_SECONDS)


class TestNaTHandling:
    """Tests fuer Fix 4: NaT-Handling in _get_last_disclosure_date()."""

    def test_get_last_disclosure_date_with_nat_values(self, scraper):
        """CSV mit unparsebaren Daten -> NaT wird ignoriert, gueltiges Datum zurueckgegeben."""
        data = pd.DataFrame({
            "name": ["A", "B", "C"],
            "ticker": ["X", "Y", "Z"],
            "asset_name": ["", "", ""],
            "asset_type": ["", "", ""],
            "transaction_type": ["Purchase", "Purchase", "Purchase"],
            "amount": ["", "", ""],
            "transaction_date": ["01/01/2025", "01/05/2025", "invalid"],
            "owner": ["", "", ""],
            "comment": ["", "", ""],
            "disclosure_date": ["01/10/2025", "02/15/2025", "not_a_date"],
            "ptr_link": ["", "", ""],
        })
        data.to_csv(scraper._output_path, index=False)
        result = scraper._get_last_disclosure_date()
        # NaT fuer "not_a_date" wird ignoriert, 02/15 - 1 Tag = 02/14
        assert result == "02/14/2025"

    def test_get_last_disclosure_date_all_nat_returns_none(self, scraper):
        """CSV mit nur unparsebaren Daten -> None."""
        data = pd.DataFrame({
            "name": ["A"],
            "ticker": ["X"],
            "asset_name": [""],
            "asset_type": [""],
            "transaction_type": ["Purchase"],
            "amount": [""],
            "transaction_date": ["invalid"],
            "owner": [""],
            "comment": [""],
            "disclosure_date": ["not_a_date"],
            "ptr_link": [""],
        })
        data.to_csv(scraper._output_path, index=False)
        result = scraper._get_last_disclosure_date()
        assert result is None


class TestDeltaSafetyBuffer:
    """Tests fuer Fix 3: Delta-Datum Sicherheitspuffer."""

    def test_get_last_disclosure_date_subtracts_one_day(self, scraper):
        """Delta-Datum wird um 1 Tag zurueckgesetzt."""
        data = pd.DataFrame({
            "name": ["A"],
            "ticker": ["X"],
            "asset_name": [""],
            "asset_type": [""],
            "transaction_type": ["Purchase"],
            "amount": [""],
            "transaction_date": ["01/01/2025"],
            "owner": [""],
            "comment": [""],
            "disclosure_date": ["03/01/2025"],
            "ptr_link": [""],
        })
        data.to_csv(scraper._output_path, index=False)
        result = scraper._get_last_disclosure_date()
        # 03/01/2025 - 1 Tag = 02/28/2025
        assert result == "02/28/2025"




class TestOwnerDedup:
    """Tests fuer Fix 5: Owner im Dedup-Subset."""

    def test_dedup_considers_owner(self, scraper, sample_trades):
        """Trades mit gleichem Ticker aber unterschiedlichem Owner werden NICHT dedupliziert."""
        # Erster Trade: Owner=Self
        trade1 = sample_trades[0].copy()
        trade1["owner"] = "Self"

        # Zweiter Trade: gleiche Daten, aber Owner=Spouse
        trade2 = sample_trades[0].copy()
        trade2["owner"] = "Spouse"

        df1 = pd.DataFrame([trade1], columns=CSV_COLUMNS)
        df2 = pd.DataFrame([trade2], columns=CSV_COLUMNS)

        scraper.save(df1)
        scraper.save(df2)

        saved = pd.read_csv(scraper._output_path)
        # Beide Trades muessen erhalten bleiben (unterschiedlicher Owner)
        assert len(saved) == 2
        owners = set(saved["owner"].tolist())
        assert owners == {"Self", "Spouse"}


# ============================================================================
# 10. Bond-Metadaten Fix
# ============================================================================

class TestBondMetadataExtraction:
    """Tests fuer Fix: Bond-Metadaten (Rate/Coupon, Matures) aus asset_name herauslösen."""

    def _make_bond_row(self, asset_name_full, bond_meta_texts, comment="--"):
        """Erzeugt eine Mock-Row für einen Bond mit div.text-muted in Zelle 4.

        Args:
            bond_meta_texts: str oder list[str]. Bei str wird ein einzelnes Element erzeugt,
                bei list werden mehrere div.text-muted Elemente simuliert.
        """
        if isinstance(bond_meta_texts, str):
            bond_meta_texts = [bond_meta_texts]
        asset_name_cell = MagicMock()
        asset_name_cell.inner_text.return_value = asset_name_full
        # Jedes div.text-muted wird als eigenes Mock-Element simuliert
        bond_meta_elements = []
        for text in bond_meta_texts:
            el = MagicMock()
            el.inner_text.return_value = text
            bond_meta_elements.append(el)
        bond_meta_locator = MagicMock()
        bond_meta_locator.all.return_value = bond_meta_elements
        asset_name_cell.locator.return_value = bond_meta_locator

        cells = [
            _make_mock_cell("1"),                      # 0: #
            _make_mock_cell("01/15/2025"),             # 1: Transaction Date
            _make_mock_cell("Self"),                   # 2: Owner
            _make_mock_cell("--"),                     # 3: Ticker (Bonds haben oft keinen)
            asset_name_cell,                           # 4: Asset Name (Bond mit Metadaten)
            _make_mock_cell("Municipal Security"),     # 5: Asset Type
            _make_mock_cell("Purchase"),               # 6: Type
            _make_mock_cell("$1,001 - $15,000"),       # 7: Amount
            _make_mock_cell(comment),                  # 8: Comment
        ]
        row = MagicMock()
        row.locator.return_value.all.return_value = cells
        return row

    def test_extract_transactions_bond_asset_name(self, scraper):
        """Bond: asset_name enthält nur den Hauptnamen, comment enthält die Metadaten."""
        row = self._make_bond_row(
            asset_name_full="BETHLEHEM PA AREA SCH DIST GO Rate/Coupon: 5% Matures: 07/15/2025",
            bond_meta_texts="Rate/Coupon: 5% Matures: 07/15/2025",
            comment="--",
        )
        mock_detail_page = MagicMock()
        mock_detail_page.locator.return_value.all.return_value = [row]

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_detail_page

        scraper._extract_transactions(
            mock_context, "Test Senator", "02/01/2025",
            "https://efdsearch.senate.gov/search/view/ptr/123/"
        )

        assert len(scraper.trades) == 1
        trade = scraper.trades[0]
        assert trade["asset_name"] == "BETHLEHEM PA AREA SCH DIST GO"
        assert trade["comment"] == "Rate/Coupon: 5% Matures: 07/15/2025"

    def test_extract_transactions_bond_with_existing_comment(self, scraper):
        """Bond mit echtem Kommentar: Metadaten werden mit ' | ' angehängt."""
        row = self._make_bond_row(
            asset_name_full="BETHLEHEM PA AREA SCH DIST GO Rate/Coupon: 5% Matures: 07/15/2025",
            bond_meta_texts="Rate/Coupon: 5% Matures: 07/15/2025",
            comment="Existing comment",
        )
        mock_detail_page = MagicMock()
        mock_detail_page.locator.return_value.all.return_value = [row]

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_detail_page

        scraper._extract_transactions(
            mock_context, "Test Senator", "02/01/2025",
            "https://efdsearch.senate.gov/search/view/ptr/123/"
        )

        assert len(scraper.trades) == 1
        trade = scraper.trades[0]
        assert trade["asset_name"] == "BETHLEHEM PA AREA SCH DIST GO"
        assert trade["comment"] == "Existing comment | Rate/Coupon: 5% Matures: 07/15/2025"

    def test_extract_transactions_multiple_text_muted_divs(self, scraper):
        """Asset-Typ 'Other' mit 2 div.text-muted (Company + Description) crasht nicht."""
        row = self._make_bond_row(
            asset_name_full=(
                "Contour Venture Partners V, LP "
                "Company: Contour Venture Partners V, LP (Princeton, NJ) "
                "Description: Limited partnership for venture investments"
            ),
            bond_meta_texts=[
                "Company: Contour Venture Partners V, LP (Princeton, NJ)",
                "Description: Limited partnership for venture investments",
            ],
            comment="--",
        )
        mock_detail_page = MagicMock()
        mock_detail_page.locator.return_value.all.return_value = [row]

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_detail_page

        scraper._extract_transactions(
            mock_context, "Test Senator", "02/01/2025",
            "https://efdsearch.senate.gov/search/view/ptr/123/"
        )

        assert len(scraper.trades) == 1
        trade = scraper.trades[0]
        assert trade["asset_name"] == "Contour Venture Partners V, LP"
        assert "Company: Contour Venture Partners V, LP (Princeton, NJ)" in trade["comment"]
        assert "Description: Limited partnership for venture investments" in trade["comment"]


# ============================================================================
# 11. Senate-Dedup-Subset enthält 'owner'
# ============================================================================

class TestDedupSubsetIncludesOwner:
    """Self/Spouse-Erhalt: 'owner' bleibt im effektiven Dedup-Subset des Senate-Scrapers."""

    def test_dedup_subset_includes_owner(self):
        """owner muss im Dedup-Subset bleiben, sonst kollabieren Self/Spouse-Trades."""
        dedup_subset = [c for c in CSV_COLUMNS
                        if c not in ("disclosure_date", "csv_upload_time")]
        assert "owner" in dedup_subset


# ============================================================================
# 12. Report-Block-Sortierung
# ============================================================================

class TestSortByReport:
    """Tests fuer _sort_by_report(): Reports absteigend nach disclosure_date,
    Zeilen innerhalb eines Reports in originaler Reihenfolge."""

    def _make_trade(self, ptr_link, disclosure_date, ticker, tx_date="01/01/2025"):
        t = {col: "" for col in CSV_COLUMNS}
        t["ptr_link"] = ptr_link
        t["disclosure_date"] = disclosure_date
        t["ticker"] = ticker
        t["transaction_date"] = tx_date
        t["name"] = "Test Senator"
        t["transaction_type"] = "Purchase"
        t["amount"] = "$1,001 - $15,000"
        return t

    def test_reports_sorted_descending_by_disclosure_date(self, scraper):
        """Neuester Report steht als erster Block in der CSV."""
        trades = [
            self._make_trade("http://ptr/old/", "01/15/2025", "AAPL"),
            self._make_trade("http://ptr/old/", "01/15/2025", "MSFT"),
            self._make_trade("http://ptr/new/", "03/01/2025", "GOOG"),
            self._make_trade("http://ptr/new/", "03/01/2025", "TSLA"),
        ]
        df = pd.DataFrame(trades, columns=CSV_COLUMNS)
        result = scraper._sort_by_report(df)

        # Erster Block: neuer Report (03/01/2025)
        assert result.iloc[0]["ptr_link"] == "http://ptr/new/"
        assert result.iloc[1]["ptr_link"] == "http://ptr/new/"
        # Zweiter Block: alter Report (01/15/2025)
        assert result.iloc[2]["ptr_link"] == "http://ptr/old/"
        assert result.iloc[3]["ptr_link"] == "http://ptr/old/"

    def test_intra_report_order_preserved(self, scraper):
        """Reihenfolge der Trades innerhalb eines Reports bleibt erhalten."""
        trades = [
            self._make_trade("http://ptr/1/", "02/01/2025", "FIRST"),
            self._make_trade("http://ptr/1/", "02/01/2025", "SECOND"),
            self._make_trade("http://ptr/1/", "02/01/2025", "THIRD"),
        ]
        df = pd.DataFrame(trades, columns=CSV_COLUMNS)
        result = scraper._sort_by_report(df)

        assert list(result["ticker"]) == ["FIRST", "SECOND", "THIRD"]

    def test_empty_df_returned_unchanged(self, scraper):
        """Leerer DataFrame wird unverändert zurückgegeben."""
        df = pd.DataFrame(columns=CSV_COLUMNS)
        result = scraper._sort_by_report(df)
        assert result.empty

    def test_save_writes_sorted_csv(self, scraper):
        """save() schreibt CSV mit Reports absteigend nach disclosure_date."""
        trades = [
            self._make_trade("http://ptr/old/", "01/10/2025", "AAPL"),
            self._make_trade("http://ptr/new/", "03/05/2025", "GOOG"),
            self._make_trade("http://ptr/mid/", "02/01/2025", "MSFT"),
        ]
        df = pd.DataFrame(trades, columns=CSV_COLUMNS)
        scraper.save(df)

        saved = pd.read_csv(scraper._output_path)
        # Absteigend: 03/05 → 02/01 → 01/10
        assert saved.iloc[0]["ptr_link"] == "http://ptr/new/"
        assert saved.iloc[1]["ptr_link"] == "http://ptr/mid/"
        assert saved.iloc[2]["ptr_link"] == "http://ptr/old/"
