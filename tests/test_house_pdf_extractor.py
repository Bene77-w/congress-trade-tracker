"""
Tests für house_pdf_extractor.py

Fokus auf die drei geänderten Bereiche:
  1. _parse_api_response  – row_index Extraktion + page_num Übergabe
  2. run() Dedup-Logik    – OCR-Artefakt vs. legitimer Mehrfachtrade
  3. save() Anti-Join     – Re-Import-Schutz ohne falsche Dedup
"""

import json
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from scrapers.house_pdf_extractor import HousePdfExtractor
from scrapers.house_scraper import CSV_COLUMNS, DEDUP_SUBSET


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def make_trade(
    asset_name="Apple Inc",
    tx_date="01/10/2025",
    page_num=1,
    row_index=1,
    tx_type="Sale",
    amount="$1,001 - $15,000",
    name="John Doe",
    ptr_link="http://example.com/doc",
):
    """Erzeugt ein Trade-Dict wie es _parse_api_response zurückgibt (mit Temp-Spalten)."""
    return {
        "name": name,
        "ticker": "",
        "asset_name": asset_name,
        "asset_type": "",
        "transaction_type": tx_type,
        "amount": amount,
        "transaction_date": tx_date,
        "owner": "Self",
        "subholding": "",
        "comment": "",
        "disclosure_date": "01/15/2025",
        "ptr_link": ptr_link,
        "_page_num": page_num,
        "_row_index": row_index,
    }


def make_csv_trade(asset_name="Apple Inc"):
    """Erzeugt ein Trade-Dict ohne Temp-Spalten (für save()-Tests)."""
    row = {col: "" for col in CSV_COLUMNS}
    row.update({
        "name": "John Doe",
        "asset_name": asset_name,
        "transaction_type": "Sale",
        "amount": "$1,001 - $15,000",
        "transaction_date": "01/10/2025",
        "ptr_link": "http://example.com/doc",
    })
    return row


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def extractor(tmp_path):
    """HousePdfExtractor ohne echten __init__ – output_path zeigt auf tmp_path."""
    e = HousePdfExtractor.__new__(HousePdfExtractor)
    e.model = "test-model"
    e._project_root = str(tmp_path)
    e._output_path = str(tmp_path / "house_trades.csv")
    e._fehler_path = str(tmp_path / "house_scraper_fehler.csv")
    e._done_path = str(tmp_path / "house_pdf_extractor_done.csv")
    e._http = MagicMock()
    e._anthropic = None
    return e


# ---------------------------------------------------------------------------
# 1. _parse_api_response
# ---------------------------------------------------------------------------

class TestParseApiResponse:
    """row_index Extraktion und page_num Weiterleitung."""

    @pytest.fixture
    def parser(self):
        return HousePdfExtractor.__new__(HousePdfExtractor)

    def _raw(self, transactions, is_ptf=True):
        return json.dumps({
            "is_transaction_page": is_ptf,
            "member_name": "John Doe",
            "notification_date": "01/15/2025",
            "transactions": transactions,
        })

    def _tx(self, **kwargs):
        base = {
            "owner": "Self",
            "asset_name": "Apple Inc",
            "transaction_type": "Sale",
            "transaction_date": "01/10/2025",
            "notification_date": "01/15/2025",
            "amount_code": "A",
            "comment": "",
        }
        base.update(kwargs)
        return base

    def test_row_index_from_api_response(self, parser):
        """row_index aus API-Antwort wird korrekt übernommen."""
        raw = self._raw([self._tx(row_index=5)])
        trades = parser._parse_api_response(
            raw, "John Doe", "01/15/2025", "http://x", page_num=2
        )
        assert trades[0]["_row_index"] == 5
        assert trades[0]["_page_num"] == 2

    def test_row_index_fallback_to_idx_plus_one(self, parser):
        """Wenn API kein row_index liefert → Fallback auf Listenposition (idx+1)."""
        raw = self._raw([self._tx(), self._tx()])  # kein row_index
        trades = parser._parse_api_response(
            raw, "John Doe", "", "http://x", page_num=1
        )
        assert trades[0]["_row_index"] == 1
        assert trades[1]["_row_index"] == 2

    def test_page_num_applied_to_all_trades_on_page(self, parser):
        """_page_num wird einheitlich auf alle Trades einer Seite gesetzt."""
        raw = self._raw([self._tx(row_index=1), self._tx(row_index=2)])
        trades = parser._parse_api_response(
            raw, "John Doe", "", "http://x", page_num=7
        )
        assert all(t["_page_num"] == 7 for t in trades)

    def test_non_ptr_page_returns_empty_list(self, parser):
        """is_transaction_page=false → leere Liste (kein Crash)."""
        raw = self._raw([], is_ptf=False)
        trades = parser._parse_api_response(raw, "", "", "http://x")
        assert trades == []

    def test_default_page_num_is_one(self, parser):
        """Ohne page_num Argument → _page_num=1 als Standardwert."""
        raw = self._raw([self._tx(row_index=1)])
        trades = parser._parse_api_response(raw, "John Doe", "", "http://x")
        assert trades[0]["_page_num"] == 1


# ---------------------------------------------------------------------------
# 2. run() Dedup-Logik
# ---------------------------------------------------------------------------

class TestRunDedup:
    """OCR-Artefakt (gleiche Seite + gleiche Zeile) vs. legitimer Mehrfachtrade."""

    def _run_batch(self, extractor, trades, tmp_path):
        """Ruft run() mit gemocktem _process_pdf auf (kein HTTP, keine API)."""
        fehler_csv = tmp_path / "fehler.csv"
        pd.DataFrame([{
            "doc_id": "1234",
            "year": 2025,
            "name": "John Doe",
            "disclosure_date": "01/15/2025",
            "ptr_link": "http://example.com/doc",
            "reason": "empty_text",
        }]).to_csv(fehler_csv, index=False)
        extractor._fehler_path = str(fehler_csv)

        with patch.object(extractor, "_init_anthropic"), \
             patch.object(extractor, "_process_pdf", return_value=trades), \
             patch("scrapers.house_pdf_extractor.time.sleep"):
            return extractor.run(dry_run=True)

    def test_ocr_duplicate_removed(self, extractor, tmp_path):
        """Gleiche Seite + gleiche Zeile → OCR-Artefakt → nur 1 Trade bleibt."""
        trades = [
            make_trade(page_num=1, row_index=3),
            make_trade(page_num=1, row_index=3),  # OCR-Dupe
        ]
        df = self._run_batch(extractor, trades, tmp_path)
        assert len(df) == 1

    def test_legitimate_identical_trades_on_different_rows_kept(self, extractor, tmp_path):
        """3 Apple-Sales auf verschiedenen Zeilen → alle 3 bleiben erhalten."""
        trades = [
            make_trade(page_num=1, row_index=1),
            make_trade(page_num=1, row_index=2),
            make_trade(page_num=1, row_index=3),
        ]
        df = self._run_batch(extractor, trades, tmp_path)
        assert len(df) == 3

    def test_identical_trades_on_different_pages_kept(self, extractor, tmp_path):
        """Gleicher Trade auf Seite 1 und Seite 2 → kein OCR-Duplikat."""
        trades = [
            make_trade(page_num=1, row_index=1),
            make_trade(page_num=2, row_index=1),  # andere Seite
        ]
        df = self._run_batch(extractor, trades, tmp_path)
        assert len(df) == 2

    def test_mixed_ocr_dupe_and_legitimate_trade(self, extractor, tmp_path):
        """1 OCR-Dupe (Zeile 1 doppelt) + 1 echter zweiter Trade (Zeile 2) → 2 bleiben."""
        trades = [
            make_trade(page_num=1, row_index=1),
            make_trade(page_num=1, row_index=1),  # OCR-Dupe von Zeile 1
            make_trade(page_num=1, row_index=2),  # echter zweiter Trade
        ]
        df = self._run_batch(extractor, trades, tmp_path)
        assert len(df) == 2

    def test_temp_columns_not_in_result(self, extractor, tmp_path):
        """_page_num und _row_index dürfen im Ergebnis-DataFrame nicht vorkommen."""
        trades = [make_trade(page_num=1, row_index=1)]
        df = self._run_batch(extractor, trades, tmp_path)
        assert "_page_num" not in df.columns
        assert "_row_index" not in df.columns

    def test_result_columns_match_csv_columns(self, extractor, tmp_path):
        """Ergebnis hat exakt die CSV_COLUMNS (Spaltenreihenfolge inklusive)."""
        trades = [make_trade(page_num=1, row_index=1)]
        df = self._run_batch(extractor, trades, tmp_path)
        assert list(df.columns) == CSV_COLUMNS

    def test_no_trades_returns_empty_dataframe(self, extractor, tmp_path):
        """Kein Trade extrahiert → leerer DataFrame (kein Crash)."""
        df = self._run_batch(extractor, [], tmp_path)
        assert df.empty

    def test_different_assets_not_deduped(self, extractor, tmp_path):
        """Apple und Tesla am selben Tag → verschiedene Assets → beide bleiben."""
        trades = [
            make_trade("Apple Inc", page_num=1, row_index=1),
            make_trade("Tesla Inc", page_num=1, row_index=2),
        ]
        df = self._run_batch(extractor, trades, tmp_path)
        assert len(df) == 2


# ---------------------------------------------------------------------------
# 3. save() Anti-Join
# ---------------------------------------------------------------------------

class TestSaveAntiJoin:
    """Anti-Join verhindert Re-Import, erlaubt legitime Mehrfachtrades."""

    def test_creates_new_file_when_none_exists(self, extractor):
        """Keine bestehende CSV → neue Datei wird erstellt."""
        df = pd.DataFrame([make_csv_trade()])
        extractor.save(df)
        result = pd.read_csv(extractor._output_path)
        assert len(result) == 1

    def test_new_trade_appended_to_existing_csv(self, extractor):
        """Neuer Trade wird an bestehende CSV angehängt."""
        pd.DataFrame([make_csv_trade("Existing Corp")]).to_csv(
            extractor._output_path, index=False
        )
        extractor.save(pd.DataFrame([make_csv_trade("New Corp")]))
        result = pd.read_csv(extractor._output_path)
        assert len(result) == 2

    def test_reimport_does_not_create_duplicate(self, extractor):
        """Re-Import desselben Trades (z.B. zweiter Extractor-Lauf) → kein Duplikat."""
        trade = make_csv_trade("Apple Inc")
        pd.DataFrame([trade]).to_csv(extractor._output_path, index=False)
        extractor.save(pd.DataFrame([trade]))
        result = pd.read_csv(extractor._output_path)
        assert len(result) == 1

    def test_three_identical_trades_all_saved_on_first_import(self, extractor):
        """Erstmaliger Import von 3 legitimen identischen Trades → alle 3 gespeichert."""
        # Leere CSV (kein Prior-Zustand)
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(extractor._output_path, index=False)
        trades = pd.DataFrame([
            make_csv_trade("Apple Inc"),
            make_csv_trade("Apple Inc"),
            make_csv_trade("Apple Inc"),
        ])
        extractor.save(trades)
        result = pd.read_csv(extractor._output_path)
        assert len(result) == 3

    def test_partial_reimport_only_adds_genuinely_new(self, extractor):
        """1 von 3 Trades bereits vorhanden → nur 2 neue werden hinzugefügt."""
        pd.DataFrame([make_csv_trade("Apple Inc")]).to_csv(
            extractor._output_path, index=False
        )
        trades = pd.DataFrame([
            make_csv_trade("Apple Inc"),   # bereits vorhanden → übersprungen
            make_csv_trade("Tesla Inc"),   # neu
            make_csv_trade("Google Inc"),  # neu
        ])
        extractor.save(trades)
        result = pd.read_csv(extractor._output_path)
        assert len(result) == 3  # 1 bestehend + 2 neue

    def test_returns_output_path(self, extractor):
        """save() gibt immer den Pfad zur CSV zurück."""
        df = pd.DataFrame([make_csv_trade()])
        path = extractor.save(df)
        assert path == extractor._output_path

    def test_empty_dataframe_returns_early(self, extractor):
        """Leerer DataFrame → keine Datei erstellt, Pfad trotzdem zurückgegeben."""
        path = extractor.save(pd.DataFrame(columns=CSV_COLUMNS))
        assert path == extractor._output_path
        import os
        assert not os.path.isfile(extractor._output_path)
