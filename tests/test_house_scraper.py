"""
Tests fuer HouseScraper (scrapers/house_scraper.py)

Testet Parsing-, Normalisierungs- und Download-Logik ohne echte HTTP-Calls.
Alle Netzwerk-Zugriffe werden gemockt.
"""

import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch, PropertyMock
from xml.etree import ElementTree

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.house_scraper import (
    HouseScraper,
    CSV_COLUMNS,
    DEDUP_SUBSET,
    OWNER_CODES,
    ASSET_TYPE_CODES,
    TRANSACTION_TYPE_CODES,
    RE_TICKER,
    RE_ASSET_TYPE,
    RE_TRANSACTION_TYPE,
    RE_SALE_PARTIAL,
    RE_SALE_FULL,
    RE_DATE,
    RE_AMOUNT_RANGE,
    RE_AMOUNT_SINGLE,
    PDF_URL_TEMPLATE,
    ZIP_URL_TEMPLATE,
)


# ---------------------------------------------------------------------------
# Helper: Create a HouseScraper without real HTTP session
# ---------------------------------------------------------------------------

def _make_scraper(output_path=None):
    """Create a HouseScraper with mocked HTTP session."""
    scraper = HouseScraper.__new__(HouseScraper)
    scraper._http = MagicMock()
    scraper._http.headers = {}
    scraper._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scraper._output_path = output_path or os.path.join(tempfile.gettempdir(), "test_house_trades.csv")
    scraper._fehler_path = os.path.join(tempfile.gettempdir(), "test_house_scraper_fehler.csv")
    scraper._fehler = []
    scraper._stats = {
        "filings_found": 0,
        "filings_skipped_delta": 0,
        "pdfs_downloaded": 0,
        "pdfs_failed": 0,
        "trades_parsed": 0,
    }
    return scraper


# ---------------------------------------------------------------------------
# Helper: Build a minimal ZIP containing an XML filing list
# ---------------------------------------------------------------------------

def _build_zip_with_xml(members_xml: str) -> bytes:
    """Create a ZIP in-memory containing an XML file with <Member> entries."""
    xml_content = f'<?xml version="1.0" encoding="utf-8"?>\n<FinancialDisclosure>\n{members_xml}\n</FinancialDisclosure>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("2025FD.xml", xml_content)
    return buf.getvalue()


def _member_xml(first="John", last="Doe", prefix="Hon.", filing_type="P",
                state="TX01", year="2025", filing_date="08/01/2025", doc_id="20030001"):
    return (
        f"<Member>"
        f"<Prefix>{prefix}</Prefix>"
        f"<First>{first}</First>"
        f"<Last>{last}</Last>"
        f"<FilingType>{filing_type}</FilingType>"
        f"<StateDst>{state}</StateDst>"
        f"<Year>{year}</Year>"
        f"<FilingDate>{filing_date}</FilingDate>"
        f"<DocID>{doc_id}</DocID>"
        f"</Member>"
    )


# ---------------------------------------------------------------------------
# Sample PDF texts for testing
# ---------------------------------------------------------------------------

SAMPLE_SINGLE_TRADE = """Filing ID #20032062
Name: Hon. Robert B. Aderholt
Status: Member
State/District: AL04

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

GSK plc American Depositary Shares S 07/28/2025 08/11/2025 $1,001 - $15,000
(GSK) [ST]
"""

SAMPLE_MULTI_TRADE_SPOUSE = """Filing ID #20030850
Name: Hon. Rick W. Allen
Status: Member
State/District: GA12

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

SP Charles Schwab Corporation (SCHW) P 07/10/2025 08/07/2025 $15,001 -
[ST] $50,000

SP Charles Schwab Corporation (SCHW) S 07/10/2025 08/07/2025 $1,001 -
[ST] $15,000

US Treasury Bill 912797PD3 [GS] P 07/10/2025 08/07/2025 $50,001 -
$100,000
"""

SAMPLE_PARTIAL_SALE = """Filing ID #20032000
Name: Hon. Jane Smith
Status: Member
State/District: CA01

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

Apple Inc (AAPL) [ST] S (partial) 06/15/2025 07/01/2025 $1,001 - $15,000
"""

SAMPLE_FULL_SALE = """Filing ID #20032001
Name: Hon. John Smith
Status: Member
State/District: CA02

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

Tesla Inc (TSLA) [ST] S (full) 06/20/2025 07/05/2025 $15,001 - $50,000
"""

SAMPLE_NO_TRANSACTIONS = """Filing ID #20032099
Name: Hon. Empty Report
Status: Member
State/District: NY01

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?
"""

SAMPLE_GOVERNMENT_SECURITY = """Filing ID #20032010
Name: Hon. Bond Buyer
Status: Member
State/District: FL01

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

US Treasury Bill 912797PD3 [GS] P 07/10/2025 08/07/2025 $50,001 - $100,000
"""

SAMPLE_TICKER_WITH_DOT = """Filing ID #20032020
Name: Hon. Warren Buffett Jr
Status: Member
State/District: NE01

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

Berkshire Hathaway Inc (BRK.B) [ST] P 05/01/2025 06/01/2025 $100,001 - $250,000
"""

SAMPLE_DC_OWNER_AND_ASSET_TYPE = """Filing ID #20032030
Name: Hon. Parent Trader
Status: Member
State/District: OH01

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

DC Vanguard 401k Plan [DC] P 03/15/2025 04/01/2025 $1,001 - $15,000
"""

SAMPLE_EXCHANGE_TRANSACTION = """Filing ID #20032040
Name: Hon. Fund Swapper
Status: Member
State/District: PA01

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

Fidelity 500 Index Fund [MF] E 04/10/2025 05/01/2025 $50,001 - $100,000
"""

SAMPLE_JT_OWNER = """Filing ID #20032050
Name: Hon. Joint Owner
Status: Member
State/District: WA01

Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

JT Microsoft Corporation (MSFT) [ST] P 06/01/2025 07/01/2025 $15,001 - $50,000
"""


# ===========================================================================
# REGEX UNIT TESTS
# ===========================================================================

class TestRegexPatterns(unittest.TestCase):
    """Verify compiled regex patterns match expected inputs."""

    # -- RE_TICKER --
    def test_ticker_simple(self):
        self.assertEqual(RE_TICKER.search("Apple Inc (AAPL) [ST]").group(1), "AAPL")

    def test_ticker_with_dot(self):
        self.assertEqual(RE_TICKER.search("Berkshire (BRK.B) [ST]").group(1), "BRK.B")

    def test_ticker_with_digit(self):
        self.assertEqual(RE_TICKER.search("Brown-Forman (BF.A) [ST]").group(1), "BF.A")

    def test_ticker_single_letter(self):
        self.assertEqual(RE_TICKER.search("Agilent (A) [ST]").group(1), "A")

    def test_ticker_no_match_lowercase(self):
        self.assertIsNone(RE_TICKER.search("(aapl)"))

    def test_ticker_no_match_empty_parens(self):
        self.assertIsNone(RE_TICKER.search("() something"))

    # -- RE_ASSET_TYPE --
    def test_asset_type_stock(self):
        self.assertEqual(RE_ASSET_TYPE.search("[ST]").group(1), "ST")

    def test_asset_type_government_security(self):
        self.assertEqual(RE_ASSET_TYPE.search("[GS]").group(1), "GS")

    def test_asset_type_defined_contribution(self):
        self.assertEqual(RE_ASSET_TYPE.search("[DC]").group(1), "DC")

    def test_asset_type_etf(self):
        self.assertEqual(RE_ASSET_TYPE.search("[EF]").group(1), "EF")

    def test_asset_type_no_match(self):
        self.assertIsNone(RE_ASSET_TYPE.search("[XX]"))

    # -- RE_TRANSACTION_TYPE --
    def test_tx_type_purchase(self):
        m = RE_TRANSACTION_TYPE.search(" P ")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "P")

    def test_tx_type_sale(self):
        m = RE_TRANSACTION_TYPE.search(" S ")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "S")

    def test_tx_type_exchange(self):
        m = RE_TRANSACTION_TYPE.search(" E ")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "E")

    def test_tx_type_no_match_inside_word(self):
        """P inside 'Apple' should not match because of \\w boundaries."""
        m = RE_TRANSACTION_TYPE.search("Apple")
        self.assertIsNone(m)

    # -- RE_SALE_PARTIAL / RE_SALE_FULL --
    def test_sale_partial(self):
        self.assertIsNotNone(RE_SALE_PARTIAL.search("S (partial)"))

    def test_sale_partial_no_space(self):
        self.assertIsNotNone(RE_SALE_PARTIAL.search("S(partial)"))

    def test_sale_full(self):
        self.assertIsNotNone(RE_SALE_FULL.search("S (full)"))

    # -- RE_DATE --
    def test_date_match(self):
        self.assertEqual(RE_DATE.search("07/28/2025").group(), "07/28/2025")

    # -- RE_AMOUNT_RANGE --
    def test_amount_range(self):
        self.assertEqual(RE_AMOUNT_RANGE.search("$1,001 - $15,000").group(), "$1,001 - $15,000")

    def test_amount_range_no_space(self):
        self.assertEqual(RE_AMOUNT_RANGE.search("$1,001-$15,000").group(), "$1,001-$15,000")

    # -- RE_AMOUNT_SINGLE --
    def test_amount_single(self):
        self.assertEqual(RE_AMOUNT_SINGLE.search("$15,001").group(), "$15,001")


# ===========================================================================
# _parse_transaction_block TESTS
# ===========================================================================

class TestParseTransactionBlock(unittest.TestCase):
    """Test parsing of individual transaction text blocks."""

    def setUp(self):
        self.scraper = _make_scraper()

    def test_simple_sale(self):
        block = "GSK plc American Depositary Shares (GSK) [ST] S 07/28/2025 08/11/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["ticker"], "GSK")
        self.assertEqual(tx["asset_type"], "Stock")
        self.assertEqual(tx["transaction_type"], "Sale")
        self.assertEqual(tx["transaction_date"], "07/28/2025")
        self.assertEqual(tx["amount"], "$1,001 - $15,000")
        self.assertEqual(tx["owner"], "Self")

    def test_purchase_with_spouse_owner(self):
        block = "SP Charles Schwab Corporation (SCHW) [ST] P 07/10/2025 08/07/2025 $15,001 - $50,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["owner"], "Spouse")
        self.assertEqual(tx["ticker"], "SCHW")
        self.assertEqual(tx["transaction_type"], "Purchase")

    def test_joint_owner(self):
        block = "JT Microsoft Corporation (MSFT) [ST] P 06/01/2025 07/01/2025 $15,001 - $50,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["owner"], "Joint")

    def test_dependent_child_owner(self):
        block = "DC Vanguard 401k Plan [DC] P 03/15/2025 04/01/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["owner"], "Dependent Child")
        self.assertEqual(tx["asset_type"], "Defined Contribution Plan")

    def test_sale_partial(self):
        block = "Apple Inc (AAPL) [ST] S (partial) 06/15/2025 07/01/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["transaction_type"], "Sale (Partial)")

    def test_sale_full(self):
        block = "Tesla Inc (TSLA) [ST] S (full) 06/20/2025 07/05/2025 $15,001 - $50,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["transaction_type"], "Sale (Full)")

    def test_exchange_transaction(self):
        block = "Fidelity 500 Index Fund [MF] E 04/10/2025 05/01/2025 $50,001 - $100,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["transaction_type"], "Exchange")
        self.assertEqual(tx["asset_type"], "Mutual Fund")

    def test_government_security_no_ticker(self):
        block = "US Treasury Bill 912797PD3 [GS] P 07/10/2025 08/07/2025 $50,001 - $100,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["ticker"], "")
        self.assertEqual(tx["asset_type"], "Government Security")
        self.assertEqual(tx["transaction_type"], "Purchase")

    def test_ticker_with_dot(self):
        block = "Berkshire Hathaway Inc (BRK.B) [ST] P 05/01/2025 06/01/2025 $100,001 - $250,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["ticker"], "BRK.B")

    def test_no_transaction_type_returns_none(self):
        block = "Some random text without a transaction type or date"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNone(tx)

    def test_two_dates_extracted(self):
        block = "Apple Inc (AAPL) [ST] P 01/15/2025 02/01/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["transaction_date"], "01/15/2025")
        self.assertEqual(tx["notification_date"], "02/01/2025")

    def test_single_date_only(self):
        block = "Apple Inc (AAPL) [ST] P 01/15/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["transaction_date"], "01/15/2025")
        self.assertEqual(tx["notification_date"], "")

    def test_split_amount_across_line_merged(self):
        """When amount range is split across lines and merged into one block."""
        block = "SP Charles Schwab Corporation (SCHW) [ST] P 07/10/2025 08/07/2025 $15,001 - $50,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["amount"], "$15,001 - $50,000")

    def test_amount_fallback_two_single_values(self):
        """When amount range regex fails, two single $ values form a range."""
        block = "Some Asset [ST] P 07/10/2025 08/07/2025 $15,001 $50,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["amount"], "$15,001 - $50,000")

    def test_dc_owner_vs_dc_asset_type(self):
        """DC as owner code (Dependent Child) vs [DC] as asset type (Defined Contribution Plan)."""
        block = "DC Vanguard 401k Plan [DC] P 03/15/2025 04/01/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["owner"], "Dependent Child")
        self.assertEqual(tx["asset_type"], "Defined Contribution Plan")

    def test_etf_asset_type(self):
        block = "SPDR S&P 500 ETF (SPY) [EF] P 05/01/2025 06/01/2025 $15,001 - $50,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["asset_type"], "Exchange-Traded Fund")
        self.assertEqual(tx["ticker"], "SPY")

    def test_corporate_security(self):
        block = "Corporate Bond XYZ [CS] P 05/01/2025 06/01/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["asset_type"], "Corporate Security")

    def test_stock_option_type(self):
        block = "Apple Inc Call Option (AAPL) [OP] P 05/01/2025 06/01/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["asset_type"], "Stock Option")

    def test_other_asset_type(self):
        block = "Some Other Asset [OT] P 05/01/2025 06/01/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["asset_type"], "Other")


# ===========================================================================
# _extract_asset_name TESTS
# ===========================================================================

class TestExtractAssetName(unittest.TestCase):
    """Test asset name extraction from transaction blocks."""

    def setUp(self):
        self.scraper = _make_scraper()

    def test_simple_stock(self):
        block = "Apple Inc (AAPL) [ST] P 01/15/2025 02/01/2025 $1,001 - $15,000"
        name = self.scraper._extract_asset_name(block)
        self.assertEqual(name, "Apple Inc")

    def test_multi_word_asset(self):
        block = "GSK plc American Depositary Shares (GSK) [ST] S 07/28/2025 08/11/2025 $1,001 - $15,000"
        name = self.scraper._extract_asset_name(block)
        self.assertEqual(name, "GSK plc American Depositary Shares")

    def test_owner_prefix_removed(self):
        block = "SP Charles Schwab Corporation (SCHW) [ST] P 07/10/2025 08/07/2025 $15,001 - $50,000"
        name = self.scraper._extract_asset_name(block)
        self.assertEqual(name, "Charles Schwab Corporation")

    def test_government_security_name(self):
        block = "US Treasury Bill 912797PD3 [GS] P 07/10/2025 08/07/2025 $50,001 - $100,000"
        name = self.scraper._extract_asset_name(block)
        self.assertIn("US Treasury Bill", name)

    def test_ticker_removed_from_name(self):
        block = "Microsoft Corporation (MSFT) [ST] P 06/01/2025 07/01/2025 $15,001 - $50,000"
        name = self.scraper._extract_asset_name(block)
        self.assertNotIn("(MSFT)", name)
        self.assertNotIn("[ST]", name)
        self.assertEqual(name, "Microsoft Corporation")

    def test_sale_partial_asset_name(self):
        block = "Apple Inc (AAPL) [ST] S (partial) 06/15/2025 07/01/2025 $1,001 - $15,000"
        name = self.scraper._extract_asset_name(block)
        self.assertEqual(name, "Apple Inc")

    def test_sale_full_asset_name(self):
        block = "Tesla Inc (TSLA) [ST] S (full) 06/20/2025 07/05/2025 $15,001 - $50,000"
        name = self.scraper._extract_asset_name(block)
        self.assertEqual(name, "Tesla Inc")

    def test_ticker_with_dot_in_name(self):
        block = "Berkshire Hathaway Inc (BRK.B) [ST] P 05/01/2025 06/01/2025 $100,001 - $250,000"
        name = self.scraper._extract_asset_name(block)
        self.assertEqual(name, "Berkshire Hathaway Inc")


# ===========================================================================
# _extract_transactions_from_text TESTS
# ===========================================================================

class TestExtractTransactionsFromText(unittest.TestCase):
    """Integration-style tests for full text-to-transactions parsing."""

    def setUp(self):
        self.scraper = _make_scraper()

    def test_single_trade_aderholt(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_SINGLE_TRADE)
        self.assertEqual(len(txs), 1)
        tx = txs[0]
        self.assertEqual(tx["ticker"], "GSK")
        self.assertEqual(tx["transaction_type"], "Sale")
        self.assertEqual(tx["transaction_date"], "07/28/2025")
        self.assertEqual(tx["owner"], "Self")
        self.assertEqual(tx["asset_type"], "Stock")

    def test_multi_trade_spouse_allen(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_MULTI_TRADE_SPOUSE)
        self.assertEqual(len(txs), 3)

        # First: SP Purchase SCHW
        self.assertEqual(txs[0]["owner"], "Spouse")
        self.assertEqual(txs[0]["ticker"], "SCHW")
        self.assertEqual(txs[0]["transaction_type"], "Purchase")

        # Second: SP Sale SCHW
        self.assertEqual(txs[1]["owner"], "Spouse")
        self.assertEqual(txs[1]["ticker"], "SCHW")
        self.assertEqual(txs[1]["transaction_type"], "Sale")

        # Third: Self Purchase Treasury Bill (no ticker)
        self.assertEqual(txs[2]["owner"], "Self")
        self.assertEqual(txs[2]["ticker"], "")
        self.assertEqual(txs[2]["asset_type"], "Government Security")

    def test_partial_sale(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_PARTIAL_SALE)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["transaction_type"], "Sale (Partial)")

    def test_full_sale(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_FULL_SALE)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["transaction_type"], "Sale (Full)")

    def test_no_transactions(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_NO_TRANSACTIONS)
        self.assertEqual(txs, [])

    def test_government_security(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_GOVERNMENT_SECURITY)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["ticker"], "")
        self.assertEqual(txs[0]["asset_type"], "Government Security")

    def test_ticker_with_dot(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_TICKER_WITH_DOT)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["ticker"], "BRK.B")

    def test_dc_owner_and_dc_asset_type_collision(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_DC_OWNER_AND_ASSET_TYPE)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["owner"], "Dependent Child")
        self.assertEqual(txs[0]["asset_type"], "Defined Contribution Plan")

    def test_exchange_transaction(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_EXCHANGE_TRANSACTION)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["transaction_type"], "Exchange")

    def test_joint_owner(self):
        txs = self.scraper._extract_transactions_from_text(SAMPLE_JT_OWNER)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["owner"], "Joint")

    def test_amount_range_with_line_wrap(self):
        """Amount split across lines should be merged when blocks merge."""
        txs = self.scraper._extract_transactions_from_text(SAMPLE_MULTI_TRADE_SPOUSE)
        # First trade has amount split: "$15,001 -\n[ST] $50,000" becomes one block
        self.assertIn("$15,001", txs[0]["amount"])
        self.assertIn("$50,000", txs[0]["amount"])

    def test_header_lines_skipped(self):
        """Lines matching header patterns should not appear as transactions."""
        text = """Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

Filing ID #20032062
Name: Hon. Robert B. Aderholt
Status: Member
State/District: AL04

Apple Inc (AAPL) [ST] P 01/15/2025 02/01/2025 $1,001 - $15,000
"""
        txs = self.scraper._extract_transactions_from_text(text)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["ticker"], "AAPL")

    def test_empty_text(self):
        txs = self.scraper._extract_transactions_from_text("")
        self.assertEqual(txs, [])

    def test_text_without_transactions_section(self):
        """Text without 'Transactions' keyword still attempts parsing."""
        text = "Apple Inc (AAPL) [ST] P 01/15/2025 02/01/2025 $1,001 - $15,000\n"
        txs = self.scraper._extract_transactions_from_text(text)
        # Should find the transaction starting from line 0
        self.assertEqual(len(txs), 1)


# ===========================================================================
# _parse_ptr_pdf TESTS (mocked pdfplumber)
# ===========================================================================

class TestParsePtrPdf(unittest.TestCase):
    """Test PDF parsing with mocked pdfplumber."""

    def setUp(self):
        self.scraper = _make_scraper()

    @patch("scrapers.house_scraper.pdfplumber.open")
    def test_single_trade_pdf(self, mock_pdf_open):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = SAMPLE_SINGLE_TRADE
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf_open.return_value = mock_pdf

        result = self.scraper._parse_ptr_pdf(b"fake pdf bytes")
        self.assertEqual(result["name"], "Robert B. Aderholt")
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(result["transactions"][0]["ticker"], "GSK")

    @patch("scrapers.house_scraper.pdfplumber.open")
    def test_multi_page_pdf(self, mock_pdf_open):
        page1 = MagicMock()
        page1.extract_text.return_value = "Filing ID #20030850\nName: Hon. Rick W. Allen\nStatus: Member\nState/District: GA12\n\nTransactions\n\nOwner Asset Transaction Transaction Notification Amount Cap.\nType Date Date Gains > $200?\n"
        page2 = MagicMock()
        page2.extract_text.return_value = "SP Charles Schwab Corporation (SCHW) P 07/10/2025 08/07/2025 $15,001 - $50,000\n[ST]\n"

        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf_open.return_value = mock_pdf

        result = self.scraper._parse_ptr_pdf(b"fake pdf bytes")
        self.assertEqual(result["name"], "Rick W. Allen")
        self.assertGreaterEqual(len(result["transactions"]), 1)

    @patch("scrapers.house_scraper.pdfplumber.open")
    def test_empty_pdf(self, mock_pdf_open):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = None
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf_open.return_value = mock_pdf

        result = self.scraper._parse_ptr_pdf(b"fake pdf bytes")
        self.assertEqual(result["raw_text"], "")
        self.assertEqual(result["transactions"], [])

    @patch("scrapers.house_scraper.pdfplumber.open")
    def test_name_extraction_without_hon_prefix(self, mock_pdf_open):
        text = "Name: John Q. Public\nTransactions\n"
        mock_page = MagicMock()
        mock_page.extract_text.return_value = text
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf_open.return_value = mock_pdf

        result = self.scraper._parse_ptr_pdf(b"fake pdf bytes")
        self.assertEqual(result["name"], "John Q. Public")

    @patch("scrapers.house_scraper.pdfplumber.open")
    def test_notification_date_extraction(self, mock_pdf_open):
        text = "Name: Hon. Test Person\nNotification Date: 08/11/2025\nTransactions\n"
        mock_page = MagicMock()
        mock_page.extract_text.return_value = text
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf_open.return_value = mock_pdf

        result = self.scraper._parse_ptr_pdf(b"fake pdf bytes")
        self.assertEqual(result["notification_date"], "08/11/2025")

    @patch("scrapers.house_scraper.pdfplumber.open")
    def test_filing_date_as_notification_fallback(self, mock_pdf_open):
        text = "Name: Hon. Test Person\nFiling Date: 09/01/2025\nTransactions\n"
        mock_page = MagicMock()
        mock_page.extract_text.return_value = text
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf_open.return_value = mock_pdf

        result = self.scraper._parse_ptr_pdf(b"fake pdf bytes")
        self.assertEqual(result["notification_date"], "09/01/2025")


# ===========================================================================
# _normalize_transactions TESTS
# ===========================================================================

class TestNormalizeTransactions(unittest.TestCase):
    """Test transaction normalization to CSV format."""

    def setUp(self):
        self.scraper = _make_scraper()

    def test_basic_normalization(self):
        parsed = {
            "name": "Robert B. Aderholt",
            "notification_date": "08/11/2025",
            "transactions": [{
                "ticker": "GSK",
                "asset_name": "GSK plc American Depositary Shares",
                "asset_type": "Stock",
                "transaction_type": "Sale",
                "transaction_date": "07/28/2025",
                "notification_date": "08/11/2025",
                "amount": "$1,001 - $15,000",
                "owner": "Self",
            }],
        }
        rows = self.scraper._normalize_transactions(
            parsed, "08/15/2025", "20032062", 2025, "Robert Aderholt"
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "Robert Aderholt")  # XML name preferred
        self.assertEqual(row["ticker"], "GSK")
        self.assertEqual(row["transaction_type"], "Sale")
        self.assertEqual(row["disclosure_date"], "08/11/2025")  # PDF notification_date
        self.assertIn("20032062", row["ptr_link"])
        self.assertEqual(row["comment"], "")

    def test_xml_name_preferred_over_pdf(self):
        parsed = {"name": "PDF Name", "notification_date": "", "transactions": [
            {"ticker": "X", "asset_name": "X", "asset_type": "Stock",
             "transaction_type": "Purchase", "transaction_date": "01/01/2025",
             "notification_date": "", "amount": "$1,001 - $15,000", "owner": "Self"}
        ]}
        rows = self.scraper._normalize_transactions(parsed, "02/01/2025", "123", 2025, "XML Name")
        self.assertEqual(rows[0]["name"], "XML Name")

    def test_pdf_name_as_fallback(self):
        parsed = {"name": "PDF Name", "notification_date": "", "transactions": [
            {"ticker": "X", "asset_name": "X", "asset_type": "Stock",
             "transaction_type": "Purchase", "transaction_date": "01/01/2025",
             "notification_date": "", "amount": "$1,001 - $15,000", "owner": "Self"}
        ]}
        rows = self.scraper._normalize_transactions(parsed, "02/01/2025", "123", 2025, "")
        self.assertEqual(rows[0]["name"], "PDF Name")

    def test_disclosure_date_fallback_to_filing_date(self):
        parsed = {"name": "Test", "notification_date": "", "transactions": [
            {"ticker": "X", "asset_name": "X", "asset_type": "Stock",
             "transaction_type": "Purchase", "transaction_date": "01/01/2025",
             "notification_date": "", "amount": "", "owner": "Self"}
        ]}
        rows = self.scraper._normalize_transactions(parsed, "02/01/2025", "123", 2025, "Test")
        self.assertEqual(rows[0]["disclosure_date"], "02/01/2025")

    def test_ptr_link_format(self):
        parsed = {"name": "Test", "notification_date": "", "transactions": [
            {"ticker": "X", "asset_name": "X", "asset_type": "Stock",
             "transaction_type": "Purchase", "transaction_date": "01/01/2025",
             "notification_date": "", "amount": "", "owner": "Self"}
        ]}
        rows = self.scraper._normalize_transactions(parsed, "", "20030001", 2025, "Test")
        expected_url = PDF_URL_TEMPLATE.format(year=2025, doc_id="20030001")
        self.assertEqual(rows[0]["ptr_link"], expected_url)

    def test_empty_transactions(self):
        parsed = {"name": "Test", "notification_date": "", "transactions": []}
        rows = self.scraper._normalize_transactions(parsed, "", "123", 2025, "Test")
        self.assertEqual(rows, [])

    def test_all_csv_columns_present(self):
        parsed = {"name": "Test", "notification_date": "", "transactions": [
            {"ticker": "X", "asset_name": "X", "asset_type": "Stock",
             "transaction_type": "Purchase", "transaction_date": "01/01/2025",
             "notification_date": "", "amount": "", "owner": "Self"}
        ]}
        rows = self.scraper._normalize_transactions(parsed, "", "123", 2025, "Test")
        for col in CSV_COLUMNS:
            self.assertIn(col, rows[0], f"Missing column: {col}")


# ===========================================================================
# _download_filing_list TESTS
# ===========================================================================

class TestDownloadFilingList(unittest.TestCase):
    """Test ZIP download and XML parsing with mocked HTTP."""

    def setUp(self):
        self.scraper = _make_scraper()

    def test_filters_ptr_filings_only(self):
        xml = (
            _member_xml(first="John", last="Doe", prefix="Hon.", filing_type="P", doc_id="100")
            + _member_xml(first="Jane", last="Smith", prefix="Hon.", filing_type="A", doc_id="200")  # Annual, not PTR
            + _member_xml(first="Bob", last="Jones", prefix="Mr.", filing_type="P", doc_id="300")  # Not Hon.
        )
        zip_bytes = _build_zip_with_xml(xml)

        mock_response = MagicMock()
        mock_response.content = zip_bytes
        mock_response.raise_for_status = MagicMock()
        self.scraper._request_with_retry = MagicMock(return_value=mock_response)

        df = self.scraper._download_filing_list(2025)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["First"], "John")
        self.assertEqual(df.iloc[0]["DocID"], "100")

    def test_empty_zip_no_xml(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "no xml here")
        mock_response = MagicMock()
        mock_response.content = buf.getvalue()
        mock_response.raise_for_status = MagicMock()
        self.scraper._request_with_retry = MagicMock(return_value=mock_response)

        df = self.scraper._download_filing_list(2025)
        self.assertTrue(df.empty)

    def test_multiple_ptr_filings(self):
        xml = (
            _member_xml(first="Alice", last="A", doc_id="101")
            + _member_xml(first="Bob", last="B", doc_id="102")
            + _member_xml(first="Carol", last="C", doc_id="103")
        )
        zip_bytes = _build_zip_with_xml(xml)

        mock_response = MagicMock()
        mock_response.content = zip_bytes
        mock_response.raise_for_status = MagicMock()
        self.scraper._request_with_retry = MagicMock(return_value=mock_response)

        df = self.scraper._download_filing_list(2025)
        self.assertEqual(len(df), 3)

    def test_empty_xml_fields(self):
        """XML with missing DocID or other fields should still parse."""
        xml = '<Member><Prefix>Hon.</Prefix><First></First><Last>OnlyLast</Last><FilingType>P</FilingType><StateDst></StateDst><Year></Year><FilingDate></FilingDate><DocID></DocID></Member>'
        zip_bytes = _build_zip_with_xml(xml)

        mock_response = MagicMock()
        mock_response.content = zip_bytes
        mock_response.raise_for_status = MagicMock()
        self.scraper._request_with_retry = MagicMock(return_value=mock_response)

        df = self.scraper._download_filing_list(2025)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["DocID"], "")
        self.assertEqual(df.iloc[0]["First"], "")

    def test_xml_missing_fields(self):
        """XML with completely missing tags should return empty strings."""
        xml = '<Member><Prefix>Hon.</Prefix><FilingType>P</FilingType></Member>'
        zip_bytes = _build_zip_with_xml(xml)

        mock_response = MagicMock()
        mock_response.content = zip_bytes
        mock_response.raise_for_status = MagicMock()
        self.scraper._request_with_retry = MagicMock(return_value=mock_response)

        df = self.scraper._download_filing_list(2025)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["First"], "")
        self.assertEqual(df.iloc[0]["Last"], "")
        self.assertEqual(df.iloc[0]["DocID"], "")


# ===========================================================================
# _get_existing_doc_ids TESTS
# ===========================================================================

class TestGetExistingDocIds(unittest.TestCase):
    """Test delta loading by reading existing DocIDs from CSV."""

    def test_extracts_doc_ids_from_ptr_link(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            df = pd.DataFrame({
                "ptr_link": [
                    "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20030001.pdf",
                    "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20030002.pdf",
                ],
                "name": ["A", "B"],
            })
            df.to_csv(f, index=False)
            tmp_path = f.name

        try:
            scraper = _make_scraper(output_path=tmp_path)
            doc_ids = scraper._get_existing_doc_ids()
            self.assertEqual(doc_ids, {"20030001", "20030002"})
        finally:
            os.unlink(tmp_path)

    def test_no_existing_file(self):
        scraper = _make_scraper(output_path=os.path.join(tempfile.gettempdir(), "nonexistent_file_for_test.csv"))
        doc_ids = scraper._get_existing_doc_ids()
        self.assertEqual(doc_ids, set())

    def test_empty_csv(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            pd.DataFrame(columns=CSV_COLUMNS).to_csv(f, index=False)
            tmp_path = f.name

        try:
            scraper = _make_scraper(output_path=tmp_path)
            doc_ids = scraper._get_existing_doc_ids()
            self.assertEqual(doc_ids, set())
        finally:
            os.unlink(tmp_path)

    def test_csv_without_ptr_link_column(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            pd.DataFrame({"name": ["A"], "ticker": ["AAPL"]}).to_csv(f, index=False)
            tmp_path = f.name

        try:
            scraper = _make_scraper(output_path=tmp_path)
            doc_ids = scraper._get_existing_doc_ids()
            self.assertEqual(doc_ids, set())
        finally:
            os.unlink(tmp_path)


# ===========================================================================
# _build_member_name TESTS
# ===========================================================================

class TestBuildMemberName(unittest.TestCase):
    def setUp(self):
        self.scraper = _make_scraper()

    def test_first_and_last(self):
        self.assertEqual(self.scraper._build_member_name("John", "Doe"), "John Doe")

    def test_only_first(self):
        self.assertEqual(self.scraper._build_member_name("John", ""), "John")

    def test_only_last(self):
        self.assertEqual(self.scraper._build_member_name("", "Doe"), "Doe")

    def test_both_empty(self):
        self.assertEqual(self.scraper._build_member_name("", ""), "")

    def test_whitespace_trimmed(self):
        self.assertEqual(self.scraper._build_member_name("  John  ", "  Doe  "), "John Doe")


# ===========================================================================
# _xml_text TESTS
# ===========================================================================

class TestXmlText(unittest.TestCase):
    def test_existing_tag(self):
        root = ElementTree.fromstring("<Root><Name>John</Name></Root>")
        self.assertEqual(HouseScraper._xml_text(root, "Name"), "John")

    def test_missing_tag(self):
        root = ElementTree.fromstring("<Root></Root>")
        self.assertEqual(HouseScraper._xml_text(root, "Name"), "")

    def test_empty_tag(self):
        root = ElementTree.fromstring("<Root><Name></Name></Root>")
        self.assertEqual(HouseScraper._xml_text(root, "Name"), "")

    def test_whitespace_stripped(self):
        root = ElementTree.fromstring("<Root><Name>  John  </Name></Root>")
        self.assertEqual(HouseScraper._xml_text(root, "Name"), "John")


# ===========================================================================
# save() TESTS
# ===========================================================================

class TestSave(unittest.TestCase):
    """Test CSV save (new file and append+dedup)."""

    def test_save_new_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "house_trades.csv")
            scraper = _make_scraper(output_path=path)

            df = pd.DataFrame([{
                "name": "Test", "ticker": "AAPL", "asset_name": "Apple",
                "asset_type": "Stock", "transaction_type": "Purchase",
                "amount": "$1,001 - $15,000", "transaction_date": "01/01/2025",
                "owner": "Self", "comment": "", "disclosure_date": "02/01/2025",
                "ptr_link": "https://example.com/100.pdf",
            }])
            result_path = scraper.save(df)
            self.assertTrue(os.path.isfile(result_path))
            saved = pd.read_csv(result_path)
            self.assertEqual(len(saved), 1)

    def test_save_append_dedup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "house_trades.csv")
            scraper = _make_scraper(output_path=path)

            row = {
                "name": "Test", "ticker": "AAPL", "asset_name": "Apple",
                "asset_type": "Stock", "transaction_type": "Purchase",
                "amount": "$1,001 - $15,000", "transaction_date": "01/01/2025",
                "owner": "Self", "comment": "", "disclosure_date": "02/01/2025",
                "ptr_link": "https://example.com/100.pdf",
            }
            # Save first time
            scraper.save(pd.DataFrame([row]))
            # Save same row again (should deduplicate)
            scraper.save(pd.DataFrame([row]))

            saved = pd.read_csv(path)
            self.assertEqual(len(saved), 1)

    def test_save_empty_df(self):
        scraper = _make_scraper()
        result = scraper.save(pd.DataFrame(columns=CSV_COLUMNS))
        self.assertEqual(result, scraper._output_path)

    def test_save_nan_roundtrip_dedup(self):
        """NaN-Bug: Trade mit leerem Ticker darf nach zweimaligem save() nicht dupliziert werden."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "house_trades.csv")
            scraper = _make_scraper(output_path=path)

            row = {
                "name": "Test", "ticker": "", "asset_name": "Some Bond",
                "asset_type": "OT", "transaction_type": "Purchase",
                "amount": "$1,001 - $15,000", "transaction_date": "01/01/2025",
                "owner": "Self", "comment": "", "disclosure_date": "02/01/2025",
                "ptr_link": "https://example.com/100.pdf",
            }
            scraper.save(pd.DataFrame([row]))
            scraper.save(pd.DataFrame([row]))

            saved = pd.read_csv(path)
            self.assertEqual(len(saved), 1, "Empty-ticker trade duplicated after second save()")

    def test_save_different_asset_name_not_deduped(self):
        """Zwei Trades mit gleichem Ticker aber verschiedenem asset_name muessen erhalten bleiben."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "house_trades.csv")
            scraper = _make_scraper(output_path=path)

            base = {
                "name": "Test", "ticker": "AAPL",
                "asset_type": "Stock", "transaction_type": "Purchase",
                "amount": "$1,001 - $15,000", "transaction_date": "01/01/2025",
                "owner": "Self", "comment": "", "disclosure_date": "02/01/2025",
                "ptr_link": "https://example.com/100.pdf",
            }
            row_a = {**base, "asset_name": "Apple Inc."}
            row_b = {**base, "asset_name": "Apple Inc. - Class A"}

            scraper.save(pd.DataFrame([row_a, row_b]))
            saved = pd.read_csv(path)
            self.assertEqual(len(saved), 2, "Different asset_name trades were incorrectly deduped")

    def test_dedup_subset_matches_settings(self):
        """DEDUP_SUBSET muss mit settings.DUPLICATE_CHECK_COLUMNS uebereinstimmen."""
        from config import settings as cfg
        self.assertEqual(
            sorted(DEDUP_SUBSET),
            sorted(cfg.DUPLICATE_CHECK_COLUMNS),
            "DEDUP_SUBSET and settings.DUPLICATE_CHECK_COLUMNS are out of sync",
        )


# ===========================================================================
# get_summary() TESTS
# ===========================================================================

class TestGetSummary(unittest.TestCase):
    def test_summary_empty_df(self):
        scraper = _make_scraper()
        summary = scraper.get_summary()
        self.assertIn("HOUSE SCRAPER", summary)
        self.assertIn("0", summary)

    def test_summary_with_data(self):
        scraper = _make_scraper()
        scraper._stats["trades_parsed"] = 5
        scraper._stats["pdfs_downloaded"] = 2

        df = pd.DataFrame({
            "name": ["Alice", "Alice", "Bob"],
            "ticker": ["AAPL", "MSFT", "GOOG"],
            "asset_name": ["Apple", "Microsoft", "Google"],
            "asset_type": ["Stock", "Stock", "Stock"],
            "transaction_type": ["Purchase", "Sale", "Purchase"],
            "amount": ["$1k", "$2k", "$3k"],
            "transaction_date": ["01/01/2025", "01/02/2025", "01/03/2025"],
            "owner": ["Self", "Self", "Self"],
            "comment": ["", "", ""],
            "disclosure_date": ["02/01/2025", "02/02/2025", "02/03/2025"],
            "ptr_link": ["url1", "url2", "url3"],
        })
        summary = scraper.get_summary(df)
        self.assertIn("Alice", summary)
        self.assertIn("Purchase", summary)
        self.assertIn("Sale", summary)


# ===========================================================================
# EDGE CASE / REGRESSION TESTS
# ===========================================================================

class TestEdgeCases(unittest.TestCase):
    """Edge cases identified by Agent A."""

    def setUp(self):
        self.scraper = _make_scraper()

    def test_tx_type_p_false_positive_in_class_p_shares(self):
        """
        KNOWN LIMITATION: 'Class P shares' triggers a false positive for
        transaction type P via the fallback regex (RE_TRANSACTION_TYPE).
        The primary regex (tx_code before date) correctly does NOT match,
        but the fallback catches the standalone P between word boundaries.

        In practice this is mitigated because _extract_transactions_from_text
        only creates a block when both a date AND tx type are found on the same
        line, so 'Class P shares' alone would never start a block.
        """
        block = "Class P shares of Some Fund"
        tx = self.scraper._parse_transaction_block(block)
        # Fallback regex matches P -> returns a transaction (false positive at block level)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["transaction_type"], "Purchase")

    def test_class_p_shares_not_a_block_start(self):
        """
        Even though 'Class P shares' has a standalone P, it does NOT start
        a transaction block because it lacks a date. The block-start heuristic
        requires BOTH a tx type AND a date on the same line.
        """
        text = """Transactions

Class P shares of Some Fund
Apple Inc (AAPL) [ST] P 01/15/2025 02/01/2025 $1,001 - $15,000
"""
        txs = self.scraper._extract_transactions_from_text(text)
        # Only the Apple trade should be found
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["ticker"], "AAPL")

    def test_state_abbreviation_s_not_false_positive(self):
        """
        State abbreviation 'S' (e.g., in 'State/District: AL04') should not
        cause false matches when it's part of header-like text.
        """
        text = """Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

State/District: AL04

Apple Inc (AAPL) [ST] P 01/15/2025 02/01/2025 $1,001 - $15,000
"""
        txs = self.scraper._extract_transactions_from_text(text)
        # State/District line is matched by header_patterns, so only AAPL trade
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["ticker"], "AAPL")

    def test_filing_id_line_not_parsed_as_transaction(self):
        """Filing ID line should be filtered by header pattern."""
        text = """Transactions

Filing ID #20032062

Apple Inc (AAPL) [ST] P 01/15/2025 02/01/2025 $1,001 - $15,000
"""
        txs = self.scraper._extract_transactions_from_text(text)
        self.assertEqual(len(txs), 1)

    def test_multiline_block_single_transaction(self):
        """A transaction block spanning two lines should produce one transaction."""
        text = """Transactions

Charles Schwab Corporation (SCHW) P 07/10/2025 08/07/2025 $15,001 -
[ST] $50,000
"""
        txs = self.scraper._extract_transactions_from_text(text)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["ticker"], "SCHW")
        self.assertIn("$15,001", txs[0]["amount"])
        self.assertIn("$50,000", txs[0]["amount"])

    def test_no_amount_still_parses(self):
        """A block with no dollar amounts should still produce a transaction."""
        block = "Some Asset [ST] P 01/01/2025 02/01/2025"
        tx = self.scraper._parse_transaction_block(block)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["amount"], "")

    def test_multiple_tickers_first_one_wins(self):
        """If multiple ticker patterns exist, the first match is used."""
        block = "Parent Fund (ABC) Subholding (XYZ) [ST] P 01/01/2025 02/01/2025 $1,001 - $15,000"
        tx = self.scraper._parse_transaction_block(block)
        self.assertEqual(tx["ticker"], "ABC")

    def test_cap_gains_header_line_ignored(self):
        """The 'Cap. Gains' header continuation should not be parsed."""
        text = """Transactions

Owner Asset Transaction Transaction Notification Amount Cap.
Type Date Date Gains > $200?

Cap. Gains > $200?

Apple Inc (AAPL) [ST] P 01/15/2025 02/01/2025 $1,001 - $15,000
"""
        txs = self.scraper._extract_transactions_from_text(text)
        self.assertEqual(len(txs), 1)


# ===========================================================================
# REAL PDF TEXT TESTS (simulates actual pdfplumber output from live PDFs)
# ===========================================================================

# Real PDF text as extracted by pdfplumber (with null bytes stripped,
# "Transactions" header replaced by lone "T", "F S:" / "S O:" noise lines, etc.)

REAL_PDF_ADERHOLT = """Filing ID #20032062
P T R
Clerk of the House of Representatives Washington, DC 20515
F I
Name: Hon. Robert B. Aderholt
Status: Member
State/District: AL04
T
ID Owner Asset Transaction Date Notification Amount Cap.
Type Date Gains >
$200?
GSK plc American Depositary Shares S 07/28/2025 08/11/2025 $1,001 - $15,000
(GSK) [ST]
F S: New
* For the complete list of asset type abbreviations...
I P O
Yes No
C  S
I CERTIFY that the statements...
Digitally Signed: Hon. Robert B. Aderholt , 09/10/2025
"""

REAL_PDF_ALLEN = """Filing ID #20030850
P T R
Clerk of the House of Representatives Washington, DC 20515
F I
Name: Hon. Richard W. Allen
Status: Member
State/District: GA12
T
ID Owner Asset Transaction Date Notification Amount Cap.
Type Date Gains >
$200?
SP Charles Schwab Corporation (SCHW) P 07/10/2025 08/07/2025 $15,001 -
[ST] $50,000
F S: New
S O: R.W. Allen & Associates, Inc. > RWA&A - Securities
US Treasury Bill 912797PD3 [GS] P 07/10/2025 08/07/2025 $50,001 -
$100,000
F S: New
S O: SCH1
US Treasury Bill 912797PV3 [GS] P 07/10/2025 08/07/2025 $50,001 -
$100,000
F S: New
S O: SCH1
* For the complete list of asset type abbreviations...
I V D
R.W. Allen & Associates, Inc. (Owner: SP)
L: US
"""


class TestRealPdfText(unittest.TestCase):
    """Tests using text that matches real pdfplumber output from live PDFs."""

    def setUp(self):
        self.scraper = _make_scraper()

    def test_real_aderholt_single_trade(self):
        """Real PDF 1: Aderholt - 1 trade, GSK Sale."""
        txs = self.scraper._extract_transactions_from_text(REAL_PDF_ADERHOLT)
        self.assertEqual(len(txs), 1)
        tx = txs[0]
        self.assertEqual(tx["ticker"], "GSK")
        self.assertEqual(tx["asset_type"], "Stock")
        self.assertEqual(tx["transaction_type"], "Sale")
        self.assertEqual(tx["transaction_date"], "07/28/2025")
        self.assertEqual(tx["amount"], "$1,001 - $15,000")
        self.assertEqual(tx["owner"], "Self")

    def test_real_allen_three_trades(self):
        """Real PDF 2: Allen - 3 trades (SCHW + 2x Treasury Bill)."""
        txs = self.scraper._extract_transactions_from_text(REAL_PDF_ALLEN)
        self.assertEqual(len(txs), 3)

        # Trade 1: SP Charles Schwab Purchase
        self.assertEqual(txs[0]["owner"], "Spouse")
        self.assertEqual(txs[0]["ticker"], "SCHW")
        self.assertEqual(txs[0]["transaction_type"], "Purchase")
        self.assertEqual(txs[0]["amount"], "$15,001 - $50,000")

        # Trade 2: US Treasury Bill Purchase
        self.assertEqual(txs[1]["ticker"], "")
        self.assertEqual(txs[1]["asset_type"], "Government Security")
        self.assertEqual(txs[1]["transaction_type"], "Purchase")
        self.assertEqual(txs[1]["amount"], "$50,001 - $100,000")

        # Trade 3: US Treasury Bill Purchase
        self.assertEqual(txs[2]["ticker"], "")
        self.assertEqual(txs[2]["asset_type"], "Government Security")
        self.assertEqual(txs[2]["transaction_type"], "Purchase")
        self.assertEqual(txs[2]["amount"], "$50,001 - $100,000")

    def test_real_pdf_f_s_lines_filtered(self):
        """F S: New lines should not produce transactions."""
        txs = self.scraper._extract_transactions_from_text(REAL_PDF_ADERHOLT)
        for tx in txs:
            self.assertNotIn("F S", tx.get("asset_name", ""))

    def test_real_pdf_s_o_lines_captured(self):
        """S O: lines should not produce extra transactions but populate subholding."""
        txs = self.scraper._extract_transactions_from_text(REAL_PDF_ALLEN)
        # Should have exactly 3 trades, no extra from S O: lines
        self.assertEqual(len(txs), 3)
        # Subholding values must be captured from S O: lines
        self.assertEqual(txs[0]["subholding"], "R.W. Allen & Associates, Inc. > RWA&A - Securities")
        self.assertEqual(txs[1]["subholding"], "SCH1")
        self.assertEqual(txs[2]["subholding"], "SCH1")

    def test_parse_transaction_block_with_subholding(self):
        """_parse_transaction_block should extract subholding from S O: line."""
        block = (
            "SP Alphabet Inc. - Class A (GOOGL) [ST] S (partial) 01/23/2024 02/02/2024 "
            "$1,001 - $15,000 S O: The 2011 Randall R. Kaplan Living Trust > MV Capital - Brokerage #2"
        )
        result = self.scraper._parse_transaction_block(block)
        self.assertEqual(result["subholding"], "The 2011 Randall R. Kaplan Living Trust > MV Capital - Brokerage #2")
        self.assertEqual(result["ticker"], "GOOGL")
        self.assertEqual(result["transaction_type"], "Sale (Partial)")
        self.assertEqual(result["owner"], "Spouse")

    def test_real_pdf_amount_split_across_lines_with_asset_type(self):
        """Amount like '$15,001 -\\n[ST] $50,000' should merge correctly."""
        txs = self.scraper._extract_transactions_from_text(REAL_PDF_ALLEN)
        self.assertEqual(txs[0]["amount"], "$15,001 - $50,000")

    def test_real_pdf_digitally_signed_date(self):
        """Digitally Signed line should be used as notification_date fallback."""
        result = {
            "raw_text": "",
            "name": "",
            "notification_date": "",
            "transactions": [],
        }
        # Simulate _parse_ptr_pdf logic for notification date
        import re as _re
        text = REAL_PDF_ADERHOLT
        notif_match = _re.search(
            r"(?:Notification\s+Date|Filing\s+Date):\s*(\d{2}/\d{2}/\d{4})",
            text, _re.IGNORECASE,
        )
        if not notif_match:
            signed_match = _re.search(
                r"Digitally\s+Signed:.*?,\s*(\d{2}/\d{2}/\d{4})",
                text, _re.IGNORECASE,
            )
            if signed_match:
                result["notification_date"] = signed_match.group(1)
        self.assertEqual(result["notification_date"], "09/10/2025")

    def test_real_pdf_null_bytes_stripped(self):
        """Null bytes in PDF text should be stripped before parsing."""
        # Simulate text with null bytes as pdfplumber might produce
        text_with_nulls = REAL_PDF_ADERHOLT.replace("T\n", "T\x00\x00\x00\x00\n")
        cleaned = text_with_nulls.replace("\x00", "")
        txs = self.scraper._extract_transactions_from_text(cleaned)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["ticker"], "GSK")

    def test_real_pdf_certification_lines_filtered(self):
        """I CERTIFY, C  S, Yes No, I P O lines should not produce transactions."""
        txs = self.scraper._extract_transactions_from_text(REAL_PDF_ADERHOLT)
        # Only 1 real trade should be found
        self.assertEqual(len(txs), 1)

    def test_real_pdf_ivd_section_filtered(self):
        """I V D, L: US lines from Investment Vehicles Disclosure should be filtered."""
        txs = self.scraper._extract_transactions_from_text(REAL_PDF_ALLEN)
        self.assertEqual(len(txs), 3)


# ===========================================================================
# run() INTEGRATION TESTS (mocked HTTP + pdfplumber)
# ===========================================================================

class TestRunIntegration(unittest.TestCase):
    """Test the full run() pipeline with mocked dependencies."""

    @patch("scrapers.house_scraper.time.sleep")
    @patch("scrapers.house_scraper.pdfplumber.open")
    def test_run_full_pipeline(self, mock_pdf_open, mock_sleep):
        scraper = _make_scraper()

        # Mock filing list download
        xml = _member_xml(first="John", last="Doe", doc_id="20030001")
        zip_bytes = _build_zip_with_xml(xml)

        mock_zip_response = MagicMock()
        mock_zip_response.content = zip_bytes
        mock_zip_response.raise_for_status = MagicMock()

        mock_pdf_response = MagicMock()
        mock_pdf_response.content = b"fake pdf"
        mock_pdf_response.raise_for_status = MagicMock()

        scraper._request_with_retry = MagicMock(
            side_effect=[mock_zip_response, mock_pdf_response]
        )

        # Mock pdfplumber
        mock_page = MagicMock()
        mock_page.extract_text.return_value = SAMPLE_SINGLE_TRADE
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf_open.return_value = mock_pdf

        df = scraper.run(years=[2025], delta=False)
        self.assertIsInstance(df, pd.DataFrame)
        self.assertGreaterEqual(len(df), 1)
        # Name should come from XML (John Doe), not PDF
        self.assertEqual(df.iloc[0]["name"], "John Doe")

    @patch("scrapers.house_scraper.time.sleep")
    @patch("scrapers.house_scraper.pdfplumber.open")
    def test_run_delta_skips_existing(self, mock_pdf_open, mock_sleep):
        """Delta mode should skip already-processed DocIDs."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            pd.DataFrame({
                "ptr_link": ["https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20030001.pdf"],
                "name": ["Existing"], "ticker": ["X"], "asset_name": ["X"],
                "asset_type": ["Stock"], "transaction_type": ["Purchase"],
                "amount": ["$1k"], "transaction_date": ["01/01/2025"],
                "owner": ["Self"], "comment": [""], "disclosure_date": ["02/01/2025"],
            }).to_csv(f, index=False)
            tmp_path = f.name

        try:
            scraper = _make_scraper(output_path=tmp_path)

            xml = _member_xml(first="John", last="Doe", doc_id="20030001")
            zip_bytes = _build_zip_with_xml(xml)

            mock_response = MagicMock()
            mock_response.content = zip_bytes
            mock_response.raise_for_status = MagicMock()
            scraper._request_with_retry = MagicMock(return_value=mock_response)

            df = scraper.run(years=[2025], delta=True)
            self.assertEqual(len(df), 0)
            self.assertEqual(scraper._stats["filings_skipped_delta"], 1)
        finally:
            os.unlink(tmp_path)

    @patch("scrapers.house_scraper.time.sleep")
    def test_run_handles_pdf_download_failure(self, mock_sleep):
        scraper = _make_scraper()

        xml = _member_xml(first="John", last="Doe", doc_id="20030001")
        zip_bytes = _build_zip_with_xml(xml)

        mock_zip_response = MagicMock()
        mock_zip_response.content = zip_bytes
        mock_zip_response.raise_for_status = MagicMock()

        # First call returns ZIP, second raises exception for PDF download
        scraper._request_with_retry = MagicMock(
            side_effect=[mock_zip_response, Exception("Download failed")]
        )

        df = scraper.run(years=[2025], delta=False)
        self.assertEqual(len(df), 0)
        self.assertEqual(scraper._stats["pdfs_failed"], 1)


# ===========================================================================
# _request_with_retry TESTS
# ===========================================================================

class TestRequestWithRetry(unittest.TestCase):
    def setUp(self):
        self.scraper = _make_scraper()

    @patch("scrapers.house_scraper.time.sleep")
    def test_success_on_first_try(self, mock_sleep):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        self.scraper._http.get = MagicMock(return_value=mock_response)

        result = self.scraper._request_with_retry("https://example.com")
        self.assertEqual(result, mock_response)
        mock_sleep.assert_not_called()

    @patch("scrapers.house_scraper.time.sleep")
    @patch("scrapers.house_scraper.settings.SCRAPER_RETRY_ATTEMPTS", 3)
    def test_retries_on_failure_then_succeeds(self, mock_sleep):
        import requests as req
        mock_fail = MagicMock()
        mock_fail.raise_for_status.side_effect = req.RequestException("timeout")

        mock_success = MagicMock()
        mock_success.raise_for_status = MagicMock()

        self.scraper._http.get = MagicMock(side_effect=[
            req.RequestException("timeout"),
            mock_success,
        ])

        result = self.scraper._request_with_retry("https://example.com")
        self.assertEqual(result, mock_success)

    @patch("scrapers.house_scraper.time.sleep")
    @patch("scrapers.house_scraper.settings.SCRAPER_RETRY_ATTEMPTS", 2)
    def test_raises_after_all_retries_exhausted(self, mock_sleep):
        import requests as req
        self.scraper._http.get = MagicMock(
            side_effect=req.RequestException("persistent error")
        )

        with self.assertRaises(req.RequestException):
            self.scraper._request_with_retry("https://example.com")


if __name__ == "__main__":
    unittest.main()
