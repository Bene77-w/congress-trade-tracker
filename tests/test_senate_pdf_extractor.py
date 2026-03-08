"""
Tests für SenatePdfExtractor

Testet die Parsing- und Normalisierungs-Logik ohne echte API-Calls oder Browser.
Alle Anthropic und Playwright Calls werden gemockt.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.senate_pdf_extractor import SenatePdfExtractor, AMOUNT_RANGES


# ---------------------------------------------------------------------------
# Hilfs-Fixture: typische API-Antwort
# ---------------------------------------------------------------------------

def _make_api_response(transactions: list, is_ptr_page: bool = True) -> str:
    """Erstellt eine gemockte API-Antwort als JSON-String."""
    return json.dumps({
        "is_ptr_page": is_ptr_page,
        "transactions": transactions,
        "footnotes": {},
    })


def _stock_tx(
    asset_name="Apple Inc",
    ticker="AAPL",
    tx_type="Purchase",
    tx_date="01/15/2026",
    amount_code="B",
    owner="(S)",
    asset_type="STOCK",
):
    return {
        "owner": owner,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "ticker": ticker,
        "transaction_type": tx_type,
        "transaction_date": tx_date,
        "amount_code": amount_code,
        "amount": AMOUNT_RANGES.get(amount_code, ""),
        "parent_fund": "",
        "comment": "",
    }


# ---------------------------------------------------------------------------
# Tests für _extract_uuid
# ---------------------------------------------------------------------------

class TestExtractUuid(unittest.TestCase):
    def setUp(self):
        self.extractor = SenatePdfExtractor.__new__(SenatePdfExtractor)

    def test_valid_url(self):
        url = "https://efdsearch.senate.gov/search/view/paper/068274e1-4b7a-4453-a242-563dde4c10d8/"
        self.assertEqual(self.extractor._extract_uuid(url), "068274e1-4b7a-4453-a242-563dde4c10d8")

    def test_url_without_uuid(self):
        self.assertIsNone(self.extractor._extract_uuid("https://example.com/no-uuid/"))

    def test_empty_string(self):
        self.assertIsNone(self.extractor._extract_uuid(""))


# ---------------------------------------------------------------------------
# Tests für _normalize_date
# ---------------------------------------------------------------------------

class TestNormalizeDate(unittest.TestCase):
    def setUp(self):
        self.extractor = SenatePdfExtractor.__new__(SenatePdfExtractor)

    def test_four_digit_year(self):
        self.assertEqual(self.extractor._normalize_date("1/15/2026"), "01/15/2026")

    def test_already_padded(self):
        self.assertEqual(self.extractor._normalize_date("01/15/2026"), "01/15/2026")

    def test_two_digit_year_2000s(self):
        self.assertEqual(self.extractor._normalize_date("1/9/26"), "01/09/2026")

    def test_two_digit_year_1900s(self):
        # Year 51 → 1951
        self.assertEqual(self.extractor._normalize_date("3/5/51"), "03/05/1951")

    def test_empty_string(self):
        self.assertEqual(self.extractor._normalize_date(""), "")

    def test_unknown_format(self):
        # Unbekanntes Format → unverändert zurückgeben
        self.assertEqual(self.extractor._normalize_date("2026-01-15"), "2026-01-15")


# ---------------------------------------------------------------------------
# Tests für _parse_api_response
# ---------------------------------------------------------------------------

class TestParseApiResponse(unittest.TestCase):
    def setUp(self):
        self.extractor = SenatePdfExtractor.__new__(SenatePdfExtractor)

    def _call(self, raw_json, senator_name="JOHN DOE", disclosure_date="02/01/2026",
              ptr_link="https://efdsearch.senate.gov/search/view/paper/abc/"):
        return self.extractor._parse_api_response(raw_json, senator_name, disclosure_date, ptr_link)

    def test_single_stock_purchase(self):
        raw = _make_api_response([_stock_tx()])
        result = self._call(raw)
        self.assertEqual(len(result), 1)
        r = result[0]
        self.assertEqual(r["name"], "JOHN DOE")
        self.assertEqual(r["ticker"], "AAPL")
        self.assertEqual(r["asset_name"], "Apple Inc")
        self.assertEqual(r["transaction_type"], "Purchase")
        self.assertEqual(r["amount"], "$15,001 - $50,000")
        self.assertEqual(r["transaction_date"], "01/15/2026")
        self.assertEqual(r["owner"], "(S)")
        self.assertEqual(r["disclosure_date"], "02/01/2026")

    def test_sale_transaction(self):
        raw = _make_api_response([_stock_tx(tx_type="Sale")])
        result = self._call(raw)
        self.assertEqual(result[0]["transaction_type"], "Sale")

    def test_exchange_transaction(self):
        raw = _make_api_response([_stock_tx(tx_type="Exchange")])
        result = self._call(raw)
        self.assertEqual(result[0]["transaction_type"], "Exchange")

    def test_cover_letter_returns_empty(self):
        raw = json.dumps({"is_ptr_page": False, "transactions": [], "footnotes": {}})
        result = self._call(raw)
        self.assertEqual(result, [])

    def test_empty_transactions(self):
        raw = _make_api_response([])
        result = self._call(raw)
        self.assertEqual(result, [])

    def test_invalid_json_returns_empty(self):
        result = self._call("not valid json at all")
        self.assertEqual(result, [])

    def test_markdown_fences_stripped(self):
        """JSON mit Markdown-Fences (manchmal von LLMs hinzugefügt)."""
        raw = "```json\n" + _make_api_response([_stock_tx()]) + "\n```"
        result = self._call(raw)
        self.assertEqual(len(result), 1)

    def test_unknown_transaction_type_skipped(self):
        tx = _stock_tx()
        tx["transaction_type"] = "Donation"
        raw = _make_api_response([tx])
        result = self._call(raw)
        self.assertEqual(result, [])

    def test_amount_code_mapped_correctly(self):
        for code, expected_range in AMOUNT_RANGES.items():
            tx = _stock_tx(amount_code=code)
            raw = _make_api_response([tx])
            result = self._call(raw)
            self.assertEqual(result[0]["amount"], expected_range, f"Amount code {code} mapping failed")

    def test_amount_fallback_when_no_code(self):
        tx = _stock_tx()
        tx["amount_code"] = ""
        tx["amount"] = "$1,001 – $15,000"
        raw = _make_api_response([tx])
        result = self._call(raw)
        self.assertEqual(result[0]["amount"], "$1,001 – $15,000")

    def test_empty_ticker_kept_empty(self):
        tx = _stock_tx(ticker="")
        raw = _make_api_response([tx])
        result = self._call(raw)
        self.assertEqual(result[0]["ticker"], "")

    def test_two_digit_date_normalized(self):
        tx = _stock_tx(tx_date="1/9/26")
        raw = _make_api_response([tx])
        result = self._call(raw)
        self.assertEqual(result[0]["transaction_date"], "01/09/2026")

    def test_senator_name_uppercased(self):
        raw = _make_api_response([_stock_tx()])
        result = self._call(raw, senator_name="Richard Blumenthal")
        self.assertEqual(result[0]["name"], "RICHARD BLUMENTHAL")

    def test_multiple_transactions(self):
        transactions = [
            _stock_tx(asset_name="Apple Inc", ticker="AAPL"),
            _stock_tx(asset_name="Tesla Inc", ticker="TSLA", tx_type="Sale"),
        ]
        raw = _make_api_response(transactions)
        result = self._call(raw)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["asset_name"], "Apple Inc")
        self.assertEqual(result[1]["asset_name"], "Tesla Inc")

    def test_ptr_link_preserved(self):
        link = "https://efdsearch.senate.gov/search/view/paper/test-uuid/"
        raw = _make_api_response([_stock_tx()])
        result = self._call(raw, ptr_link=link)
        self.assertEqual(result[0]["ptr_link"], link)

    def test_spouse_owner(self):
        tx = _stock_tx(owner="(SP)")
        raw = _make_api_response([tx])
        result = self._call(raw)
        self.assertEqual(result[0]["owner"], "(SP)")

    def test_comment_preserved(self):
        tx = _stock_tx()
        tx["comment"] = "Shares distributed from fund XYZ"
        raw = _make_api_response([tx])
        result = self._call(raw)
        self.assertEqual(result[0]["comment"], "Shares distributed from fund XYZ")

    def test_non_dict_transaction_skipped(self):
        raw = json.dumps({
            "is_ptr_page": True,
            "transactions": ["not a dict", None, 42],
            "footnotes": {},
        })
        result = self._call(raw)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Hilfsfunktion: Playwright-Mock
# ---------------------------------------------------------------------------

def _make_mock_playwright():
    """Erstellt einen vollständigen Context-Manager-Mock für sync_playwright()."""
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_instance = MagicMock()
    mock_instance.__enter__ = MagicMock(return_value=mock_instance)
    mock_instance.__exit__ = MagicMock(return_value=False)
    mock_instance.chromium.launch.return_value = mock_browser
    return mock_instance


# ---------------------------------------------------------------------------
# Tests für run() – mockt Playwright und Anthropic
# ---------------------------------------------------------------------------

class TestRun(unittest.TestCase):
    """Testet run() mit gemockten externen Abhängigkeiten."""

    _UUID1 = "068274e1-4b7a-4453-a242-563dde4c10d8"
    _UUID2 = "13a0fc2a-bbbb-4c1b-b380-d315e198d2a3"

    def _make_extractor(self):
        extractor = SenatePdfExtractor.__new__(SenatePdfExtractor)
        extractor.headless = True
        extractor.model = "claude-haiku-4-5-20251001"
        extractor._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        extractor._output_path = "/tmp/test_senate_trades.csv"
        extractor._skipped_path = os.path.join(
            extractor._project_root, "data", "raw", "senate_pdf_skipped.csv"
        )
        extractor._http = MagicMock()
        extractor._anthropic = None
        return extractor

    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._init_anthropic")
    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._accept_agreement")
    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._process_pdf")
    @patch("scrapers.senate_pdf_extractor.sync_playwright")
    def test_run_processes_all_rows(self, mock_pw, mock_process, mock_agree, mock_init):
        """run() verarbeitet alle Zeilen der Input-CSV."""
        import pandas as pd
        import tempfile

        extractor = self._make_extractor()

        # Minimale Input-CSV
        df_input = pd.DataFrame([
            {"name": "JOHN DOE", "disclosure_date": "02/01/2026",
             "ptr_link": "https://efdsearch.senate.gov/search/view/paper/068274e1-4b7a-4453-a242-563dde4c10d8/"},
            {"name": "JANE SMITH", "disclosure_date": "02/05/2026",
             "ptr_link": "https://efdsearch.senate.gov/search/view/paper/13a0fc2a-bbbb-4c1b-b380-d315e198d2a3/"},
        ])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            df_input.to_csv(f, index=False)
            tmp_csv = f.name

        mock_pw.return_value = _make_mock_playwright()
        mock_process.return_value = []

        try:
            df = extractor.run(input_csv=tmp_csv, dry_run=True)
            self.assertEqual(mock_process.call_count, 2)
            self.assertIsInstance(df, pd.DataFrame)
        finally:
            os.unlink(tmp_csv)

    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._init_anthropic")
    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._accept_agreement")
    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._process_pdf")
    @patch("scrapers.senate_pdf_extractor.sync_playwright")
    def test_run_deduplicates_results(self, mock_pw, mock_process, mock_agree, mock_init):
        """run() entfernt doppelte Trades."""
        import pandas as pd
        import tempfile

        extractor = self._make_extractor()

        trade = {
            "name": "JOHN DOE", "ticker": "AAPL", "asset_name": "Apple Inc",
            "asset_type": "STOCK", "transaction_type": "Purchase",
            "amount": "$15,001 - $50,000", "transaction_date": "01/15/2026",
            "owner": "(S)", "comment": "", "disclosure_date": "02/01/2026",
            "ptr_link": f"https://efdsearch.senate.gov/search/view/paper/{self._UUID1}/",
            # Temp-Felder wie _parse_api_response sie setzt (gleiche Seite+Zeile = OCR-Dupe)
            "_page_num": 1, "_row_index": 1,
        }
        mock_process.return_value = [trade, trade]  # OCR-Duplikat (gleiche Seite+Zeile)

        df_input = pd.DataFrame([{
            "name": "JOHN DOE", "disclosure_date": "02/01/2026",
            "ptr_link": f"https://efdsearch.senate.gov/search/view/paper/{self._UUID1}/"
        }])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            df_input.to_csv(f, index=False)
            tmp_csv = f.name

        mock_pw.return_value = _make_mock_playwright()

        try:
            df = extractor.run(input_csv=tmp_csv, dry_run=True)
            self.assertEqual(len(df), 1)  # Duplikat entfernt
        finally:
            os.unlink(tmp_csv)


# ---------------------------------------------------------------------------
# Tests für _get_gif_urls
# ---------------------------------------------------------------------------

class TestGetGifUrls(unittest.TestCase):
    def setUp(self):
        self.extractor = SenatePdfExtractor.__new__(SenatePdfExtractor)

    def test_extracts_gif_urls_from_playwright(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            "https://efd-media-public.senate.gov/media/2026/2/000/000/000000025.gif",
            "https://efd-media-public.senate.gov/media/2026/2/000/000/000000026.gif",
        ]
        result = self.extractor._get_gif_urls(mock_page, "test-uuid")
        self.assertEqual(len(result), 2)
        mock_page.goto.assert_called_once()

    def test_returns_empty_list_when_no_images(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = None
        result = self.extractor._get_gif_urls(mock_page, "test-uuid")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Tests für _process_pdf – Seite 1 überspringen
# ---------------------------------------------------------------------------

class TestProcessPdf(unittest.TestCase):
    def setUp(self):
        self.extractor = SenatePdfExtractor.__new__(SenatePdfExtractor)
        self.extractor._http = MagicMock()
        self.extractor.model = "claude-haiku-4-5-20251001"

    VALID_UUID = "068274e1-4b7a-4453-a242-563dde4c10d8"
    VALID_URL = f"https://efdsearch.senate.gov/search/view/paper/{VALID_UUID}/"

    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._get_gif_urls")
    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._download_gif")
    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._extract_from_image")
    def test_cover_letter_page_skipped(self, mock_extract, mock_download, mock_gif_urls):
        """Seite 1 (Cover Letter) wird immer übersprungen."""
        mock_gif_urls.return_value = [
            "https://efd-media-public.senate.gov/media/2026/2/000/000/001.gif",  # Seite 1 (Cover)
            "https://efd-media-public.senate.gov/media/2026/2/000/000/002.gif",  # Seite 2 (PTR)
        ]
        mock_download.return_value = MagicMock()
        mock_extract.return_value = []

        self.extractor._process_pdf(
            MagicMock(), self.VALID_URL, "JOHN DOE", "02/01/2026"
        )

        # _extract_from_image sollte nur 1x aufgerufen werden (Seite 2, nicht Seite 1)
        self.assertEqual(mock_extract.call_count, 1)
        # page_num ist das 5. positionale Argument (image, senator_name, disclosure_date, ptr_link, page_num)
        self.assertEqual(mock_extract.call_args.args[4], 2)

    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._get_gif_urls")
    def test_no_gif_urls_returns_empty(self, mock_gif_urls):
        mock_gif_urls.return_value = []
        result = self.extractor._process_pdf(
            MagicMock(), self.VALID_URL, "JOHN DOE", "02/01/2026"
        )
        self.assertEqual(result, [])

    @patch("scrapers.senate_pdf_extractor.SenatePdfExtractor._get_gif_urls")
    def test_invalid_url_returns_empty(self, mock_gif_urls):
        result = self.extractor._process_pdf(
            MagicMock(), "https://example.com/not-a-ptr/",
            "JOHN DOE", "02/01/2026"
        )
        mock_gif_urls.assert_not_called()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
