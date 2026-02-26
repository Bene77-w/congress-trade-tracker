"""
Congress Trade Tracker - House of Representatives Financial Disclosures Scraper
Scrapes Periodic Transaction Reports (PTRs) from https://disclosures-clerk.house.gov/

Downloads yearly ZIP files containing XML filing metadata, filters for PTR filings
by active Members (Hon. prefix, FilingType=P), then downloads and parses individual
PDF reports using pdfplumber.

Supports delta-loading, rate limiting, retry logic, and CLI arguments.
"""

import argparse
import io
import logging
import os
import re
import sys
import time
import zipfile
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree

import pandas as pd
import pdfplumber
import requests

# Projekt-Root in sys.path einfuegen
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
BASE_URL = "https://disclosures-clerk.house.gov"
ZIP_URL_TEMPLATE = BASE_URL + "/public_disc/financial-pdfs/{year}FD.zip"
PDF_URL_TEMPLATE = BASE_URL + "/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

# CSV-Spalten (identisch mit senate_trades.csv)
CSV_COLUMNS = [
    "name",
    "ticker",
    "asset_name",
    "asset_type",
    "transaction_type",
    "amount",
    "transaction_date",
    "owner",
    "subholding",
    "comment",
    "disclosure_date",
    "ptr_link",
]

# Dedup-Spalten: alle ausser disclosure_date (analog zu senate_scraper.py)
DEDUP_SUBSET = [c for c in CSV_COLUMNS if c != "disclosure_date"]

# Owner-Code Mapping
OWNER_CODES = {
    "SP": "Spouse",
    "JT": "Joint",
    "DC": "Dependent Child",
}

# Asset-Type-Code Mapping
ASSET_TYPE_CODES = {
    "ST": "Stock",
    "OP": "Stock Option",
    "GS": "Government Security",
    "OT": "Other",
    "CS": "Corporate Security",
    "DC": "Defined Contribution Plan",
    "EF": "Exchange-Traded Fund",
    "MF": "Mutual Fund",
}

# Transaction-Type-Code Mapping
TRANSACTION_TYPE_CODES = {
    "P": "Purchase",
    "S": "Sale",
    "E": "Exchange",
}

# Regex Patterns (compiled for performance)
RE_TICKER = re.compile(r"\(([A-Z][A-Z0-9.]*)\)")
RE_ASSET_TYPE = re.compile(r"\[(ST|OP|GS|OT|CS|DC|EF|MF)\]")
RE_TRANSACTION_TYPE = re.compile(r"(?<!\w)(P|S|E)(?!\w)")
RE_SALE_PARTIAL = re.compile(r"S\s*\(partial\)", re.IGNORECASE)
RE_SALE_FULL = re.compile(r"S\s*\(full\)", re.IGNORECASE)
RE_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")
RE_AMOUNT_RANGE = re.compile(r"\$[\d,]+\s*-\s*\$[\d,]+")
RE_AMOUNT_SINGLE = re.compile(r"\$[\d,]+")
# Pattern to detect lines that look like transaction-type markers followed by a date
RE_TX_START = re.compile(
    r"(?:^|\s)(?:SP|JT|DC)?\s*\S+.*?"
    r"(?:P|S|E|S\s*\(partial\)|S\s*\(full\))\s+\d{2}/\d{2}/\d{4}"
)


class HouseScraper:
    """
    Scraper fuer House of Representatives Periodic Transaction Reports.

    Workflow:
        1. _download_filing_list(year) - ZIP herunterladen, XML parsen, PTR-Filings filtern
        2. _get_existing_doc_ids()     - Bereits vorhandene DocIDs aus CSV lesen (Delta)
        3. _download_pdf(doc_id, year) - Einzelne PDF herunterladen
        4. _parse_ptr_pdf(pdf_bytes)   - PDF-Text extrahieren und Transaktionen parsen
        5. save(df)                    - Ergebnisse als CSV speichern (Append + Dedup)
    """

    def __init__(self):
        self._http = requests.Session()
        self._http.headers.update({
            "User-Agent": settings.SCRAPER_USER_AGENT,
        })
        self._project_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        self._output_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "house_trades.csv"
        )
        self._stats = {
            "filings_found": 0,
            "filings_skipped_delta": 0,
            "pdfs_downloaded": 0,
            "pdfs_failed": 0,
            "trades_parsed": 0,
        }

    # ------------------------------------------------------------------
    # Oeffentliche API
    # ------------------------------------------------------------------

    def run(self, years: Optional[list[int]] = None, delta: bool = True) -> pd.DataFrame:
        """
        Haupteinstiegspunkt: Lade Filings fuer die angegebenen Jahre, parse PDFs.

        Args:
            years:  Liste der Jahre (default: aktuelles + vorheriges Jahr).
            delta:  Wenn True, nur neue DocIDs verarbeiten.

        Returns:
            pd.DataFrame mit allen gescrapten Trades.
        """
        if years is None:
            current_year = datetime.now().year
            years = [current_year, current_year - 1]

        logger.info(f"=== House Scraper gestartet: Jahre={years}, Delta={delta} ===")

        existing_doc_ids = set()
        if delta:
            existing_doc_ids = self._get_existing_doc_ids()
            logger.info(f"Delta-Modus: {len(existing_doc_ids)} bestehende DocIDs geladen.")

        all_trades = []

        for year in years:
            logger.info(f"--- Verarbeite Jahr {year} ---")
            try:
                filings = self._download_filing_list(year)
            except Exception:
                logger.exception(f"Fehler beim Download der Filing-Liste fuer {year}")
                continue

            self._stats["filings_found"] += len(filings)
            logger.info(f"  {len(filings)} PTR-Filings gefunden fuer {year}.")

            for _, filing in filings.iterrows():
                doc_id = str(filing["DocID"])

                if delta and doc_id in existing_doc_ids:
                    self._stats["filings_skipped_delta"] += 1
                    continue

                try:
                    pdf_bytes = self._download_pdf(doc_id, year)
                    self._stats["pdfs_downloaded"] += 1
                except Exception:
                    logger.warning(f"  PDF-Download fehlgeschlagen: DocID={doc_id}")
                    self._stats["pdfs_failed"] += 1
                    continue

                try:
                    parsed = self._parse_ptr_pdf(pdf_bytes)
                except Exception:
                    logger.warning(f"  PDF-Parsing fehlgeschlagen: DocID={doc_id}")
                    self._stats["pdfs_failed"] += 1
                    continue

                filing_date = str(filing.get("FilingDate", ""))
                member_name = self._build_member_name(
                    str(filing.get("First", "")),
                    str(filing.get("Last", "")),
                )

                transactions = self._normalize_transactions(
                    parsed, filing_date, doc_id, year, member_name
                )
                all_trades.extend(transactions)
                self._stats["trades_parsed"] += len(transactions)

                # Rate Limiting
                time.sleep(settings.SCRAPER_RATE_LIMIT_SECONDS)

        df = pd.DataFrame(all_trades, columns=CSV_COLUMNS)
        if not df.empty:
            before = len(df)
            df[DEDUP_SUBSET] = df[DEDUP_SUBSET].fillna("")
            df.drop_duplicates(subset=DEDUP_SUBSET, keep="first", inplace=True)
            after = len(df)
            if before != after:
                logger.info(f"  {before - after} Duplikate entfernt.")

        logger.info(
            f"=== House Scraper abgeschlossen: {len(df)} Trades aus "
            f"{self._stats['pdfs_downloaded']} PDFs ==="
        )
        return df

    def save(self, df: Optional[pd.DataFrame] = None) -> str:
        """
        Speichert die Ergebnisse in data/raw/house_trades.csv.
        Haengt neue Zeilen an eine bestehende Datei an (Append-Modus).

        Returns:
            Pfad zur gespeicherten Datei.
        """
        if df is None:
            df = pd.DataFrame(columns=CSV_COLUMNS)

        if df.empty:
            logger.warning("Keine Trades zum Speichern vorhanden.")
            return self._output_path

        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)

        file_exists = os.path.isfile(self._output_path)
        if file_exists:
            existing = pd.read_csv(self._output_path)
            # Fehlende Spalten ergaenzen (Migration: alte CSVs ohne neue Spalten wie subholding)
            for col in DEDUP_SUBSET:
                if col not in existing.columns:
                    existing[col] = ""
            combined = pd.concat([existing, df], ignore_index=True)
            combined[DEDUP_SUBSET] = combined[DEDUP_SUBSET].fillna("")
            combined.drop_duplicates(subset=DEDUP_SUBSET, keep="last", inplace=True)
            combined.to_csv(self._output_path, index=False)
            logger.info(
                f"Daten angehaengt und dedupliziert -> {self._output_path} "
                f"({len(combined)} Zeilen gesamt)"
            )
        else:
            df.to_csv(self._output_path, index=False)
            logger.info(
                f"Neue Datei erstellt -> {self._output_path} ({len(df)} Zeilen)"
            )

        return self._output_path

    def get_summary(self, df: Optional[pd.DataFrame] = None) -> str:
        """Gibt eine lesbare Zusammenfassung der Scraping-Ergebnisse zurueck."""
        if df is None:
            df = pd.DataFrame(columns=CSV_COLUMNS)

        lines = [
            "=" * 60,
            "  HOUSE SCRAPER - ZUSAMMENFASSUNG",
            "=" * 60,
            f"  Filings gefunden:      {self._stats['filings_found']}",
            f"  Delta uebersprungen:   {self._stats['filings_skipped_delta']}",
            f"  PDFs heruntergeladen:  {self._stats['pdfs_downloaded']}",
            f"  PDFs fehlgeschlagen:   {self._stats['pdfs_failed']}",
            f"  Trades geparst:        {self._stats['trades_parsed']}",
            f"  Trades im DataFrame:   {len(df)}",
        ]

        if not df.empty:
            unique_members = df["name"].nunique()
            lines.append(f"  Eindeutige Members:    {unique_members}")

            top = df["name"].value_counts().head(5)
            lines.append("")
            lines.append("  TOP 5 aktivste Members:")
            for name, count in top.items():
                lines.append(f"    - {name}: {count} Trades")

            types = df["transaction_type"].value_counts()
            lines.append("")
            lines.append("  Transaktionstypen:")
            for t, c in types.items():
                lines.append(f"    - {t}: {c}")

        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Interne Methoden
    # ------------------------------------------------------------------

    def _download_filing_list(self, year: int) -> pd.DataFrame:
        """
        Laedt die ZIP-Datei fuer ein Jahr herunter, extrahiert die XML-Datei
        und filtert auf PTR-Filings von aktiven Members.

        Returns:
            pd.DataFrame mit Spalten: First, Last, FilingType, StateDst,
                                       Year, FilingDate, DocID
        """
        url = ZIP_URL_TEMPLATE.format(year=year)
        logger.info(f"  Lade Filing-Liste: {url}")

        response = self._request_with_retry(url)
        response.raise_for_status()

        filings = []
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            # Suche die XML-Datei im ZIP
            xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                logger.warning(f"  Keine XML-Datei im ZIP fuer {year} gefunden.")
                return pd.DataFrame()

            xml_name = xml_names[0]
            logger.info(f"  XML-Datei gefunden: {xml_name}")

            with zf.open(xml_name) as xml_file:
                # BOM handling: utf-8-sig
                raw_bytes = xml_file.read()
                xml_text = raw_bytes.decode("utf-8-sig")
                root = ElementTree.fromstring(xml_text)

            for member in root.iter("Member"):
                prefix = self._xml_text(member, "Prefix")
                filing_type = self._xml_text(member, "FilingType")

                # Filter: Nur PTR (FilingType=P) von aktiven Members (Prefix=Hon.)
                if filing_type != "P" or prefix != "Hon.":
                    continue

                filings.append({
                    "First": self._xml_text(member, "First"),
                    "Last": self._xml_text(member, "Last"),
                    "FilingType": filing_type,
                    "StateDst": self._xml_text(member, "StateDst"),
                    "Year": self._xml_text(member, "Year"),
                    "FilingDate": self._xml_text(member, "FilingDate"),
                    "DocID": self._xml_text(member, "DocID"),
                })

        df = pd.DataFrame(filings)
        logger.info(f"  {len(df)} PTR-Filings nach Filterung (Hon. + P).")
        return df

    def _get_existing_doc_ids(self) -> set:
        """
        Liest vorhandene DocIDs aus der bestehenden house_trades.csv.
        Extrahiert die DocID aus der ptr_link-Spalte.

        Returns:
            Set von DocID-Strings.
        """
        if not os.path.isfile(self._output_path):
            return set()

        try:
            df = pd.read_csv(self._output_path)
            if "ptr_link" not in df.columns or df.empty:
                return set()

            # DocID aus ptr_link extrahieren: .../ptr-pdfs/YYYY/DOCID.pdf
            doc_ids = set()
            for link in df["ptr_link"].dropna():
                match = re.search(r"/(\d+)\.pdf", str(link))
                if match:
                    doc_ids.add(match.group(1))

            logger.info(f"  {len(doc_ids)} bestehende DocIDs aus CSV gelesen.")
            return doc_ids
        except Exception:
            logger.exception("Fehler beim Lesen bestehender DocIDs")
            return set()

    def _download_pdf(self, doc_id: str, year: int) -> bytes:
        """
        Laedt eine einzelne PDF-Datei herunter.

        Returns:
            PDF-Inhalt als bytes.
        """
        url = PDF_URL_TEMPLATE.format(year=year, doc_id=doc_id)
        logger.debug(f"  Lade PDF: {url}")

        response = self._request_with_retry(url)
        response.raise_for_status()
        return response.content

    def _parse_ptr_pdf(self, pdf_bytes: bytes) -> dict:
        """
        Extrahiert Textinhalte aus einer PTR-PDF mit pdfplumber.

        Returns:
            dict mit:
                - 'raw_text': Vollstaendiger extrahierter Text
                - 'name': Name des Members (aus PDF-Header)
                - 'notification_date': Datum der Meldung (disclosure_date)
                - 'transactions': Liste von Transaction-Dicts
        """
        result = {
            "raw_text": "",
            "name": "",
            "notification_date": "",
            "transactions": [],
        }

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            full_text = ""
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"

        # Bug fix: pdfplumber may extract null bytes (\x00) from real PDFs
        full_text = full_text.replace("\x00", "")

        result["raw_text"] = full_text

        if not full_text.strip():
            logger.warning("  PDF-Text ist leer.")
            return result

        # Name extrahieren (z.B. "Name: Hon. Robert B. Aderholt")
        name_match = re.search(r"Name:\s*(?:Hon\.\s*)?(.+)", full_text)
        if name_match:
            result["name"] = name_match.group(1).strip()

        # Notification Date extrahieren
        notif_match = re.search(
            r"(?:Notification\s+Date|Filing\s+Date):\s*(\d{2}/\d{2}/\d{4})",
            full_text,
            re.IGNORECASE,
        )
        if notif_match:
            result["notification_date"] = notif_match.group(1)
        else:
            # Fallback: extract date from "Digitally Signed:" line (real PDFs)
            signed_match = re.search(
                r"Digitally\s+Signed:.*?,\s*(\d{2}/\d{2}/\d{4})",
                full_text,
                re.IGNORECASE,
            )
            if signed_match:
                result["notification_date"] = signed_match.group(1)

        # Transaktionen parsen
        result["transactions"] = self._extract_transactions_from_text(full_text)

        return result

    def _extract_transactions_from_text(self, text: str) -> list[dict]:
        """
        Parst Transaktionszeilen aus dem PDF-Volltext.

        Die Transaktionsdaten koennen sich ueber mehrere Zeilen erstrecken.
        Strategie: Zeilen in logische Bloecke zusammenfuehren die jeweils
        eine Transaktion darstellen.

        Eine neue Transaktion wird erkannt wenn eine Zeile sowohl einen
        Transaktionstyp (P/S/E/S(partial)/S(full)) als auch mindestens ein
        Datum (MM/DD/YYYY) enthaelt.

        Returns:
            Liste von Dicts mit Rohfeldern pro Transaktion.
        """
        lines = text.split("\n")

        # Finde den Bereich ab "Transactions" oder nach dem table header "$200?"
        # Real PDFs have null-byte-stripped "T" instead of "Transactions",
        # so we also look for the table header ending with "$200?"
        start_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.search(r"transactions", stripped, re.IGNORECASE):
                start_idx = i + 1
                break
            if re.search(r"\$200\s*\?", stripped):
                start_idx = i + 1
                break

        # Sammle Transaktionszeilen ab dem Start-Index
        transaction_lines = lines[start_idx:]

        # Header-Muster die ignoriert werden sollen
        header_patterns = re.compile(
            r"^(Filing\s+ID|Name:|Status:|State/District:|Filer|"
            r"Subholding\s+Of|Description|Cap\.\s+Gains|---)",
            re.IGNORECASE,
        )

        # Lines to skip entirely (noise from real PDFs)
        skip_patterns = re.compile(
            r"^("
            r"F\s+S:|"                           # Filing Status: New
            r"\*\s*For\s+the\s+complete|"         # "* For the complete list..."
            r"I\s+P\s+O\b|"                       # IPO section header
            r"Yes\s+No|"                          # Yes/No checkboxes
            r"C\s+S\b|"                            # Certification & Signature
            r"I\s+CERTIFY|"                       # Certification text
            r"Digitally\s+Signed:|"               # Digital signature line
            r"I\s+V\s+D\b|"                       # Investment Vehicles Disclosure
            r"L:\s*US|"                           # Location: US
            r"P\s+T\s+R\b|"                       # PTR header
            r"F\s+I\b|"                            # FI header
            r"Clerk\s+of\s+the\s+House|"          # Clerk header
            r"ID\s+Owner\s+Asset|"                # Table column header
            r"Owner\s+Asset\s+Transaction|"       # Table column header variant
            r"Type\s+Date|"                       # Table column header continuation
            r"\$200\s*\?$|"                       # Seitenumbruch-Wiederholungsheader "Cap. Gains > $200?"
            r"T$"                                 # Lone "T" (null-byte-stripped "Transactions")
            r")",
            re.IGNORECASE,
        )

        # Logische Bloecke zusammenfuehren
        blocks = []
        current_block = ""

        for line in transaction_lines:
            line = line.strip()
            if not line:
                continue

            # Ignoriere Header-Zeilen und Footer
            if header_patterns.match(line):
                continue

            # Ignoriere zusaetzliche Rauschzeilen aus echten PDFs
            if skip_patterns.match(line):
                # I V D markiert den Beginn der Investment Vehicles Disclosure-Sektion —
                # alle nachfolgenden Zeilen sind nicht-transaktionaler Content.
                if re.match(r"I\s+V\s+D\b", line, re.IGNORECASE):
                    break
                continue

            # Pruefen ob diese Zeile den Start einer neuen Transaktion ist
            has_date = bool(RE_DATE.search(line))
            has_tx_type = bool(
                RE_SALE_PARTIAL.search(line)
                or RE_SALE_FULL.search(line)
                or RE_TRANSACTION_TYPE.search(line)
            )

            if has_date and has_tx_type:
                # Vorherigen Block abschliessen (falls vorhanden)
                if current_block:
                    blocks.append(current_block.strip())
                current_block = line
            else:
                # Fortsetzung des aktuellen Blocks (z.B. umgebrochene Zeile)
                if current_block:
                    current_block += " " + line
                # Zeilen vor dem ersten Transaktionsblock werden ignoriert

        # Letzten Block abschliessen
        if current_block.strip():
            blocks.append(current_block.strip())

        # Jeden Block parsen
        transactions = []
        for block in blocks:
            tx = self._parse_transaction_block(block)
            if tx is not None:
                transactions.append(tx)

        return transactions

    def _parse_transaction_block(self, block: str) -> Optional[dict]:
        """
        Parst einen zusammengefuehrten Textblock zu einer Transaktion.

        Returns:
            Dict mit Rohfeldern oder None wenn nicht parsebar.
        """
        # Owner-Code am Anfang erkennen (SP, JT, DC)
        owner = "Self"
        owner_match = re.match(r"^(SP|JT|DC)\s+", block)
        if owner_match:
            owner = OWNER_CODES.get(owner_match.group(1), "Self")
            block = block[owner_match.end():]

        # Ticker extrahieren
        ticker_match = RE_TICKER.search(block)
        ticker = ticker_match.group(1) if ticker_match else ""

        # Asset Type extrahieren
        asset_type_match = RE_ASSET_TYPE.search(block)
        asset_type_code = asset_type_match.group(1) if asset_type_match else ""
        asset_type = ASSET_TYPE_CODES.get(asset_type_code, asset_type_code)

        # Transaction Type extrahieren (prueffe partial/full sale zuerst)
        if RE_SALE_PARTIAL.search(block):
            transaction_type = "Sale (Partial)"
        elif RE_SALE_FULL.search(block):
            transaction_type = "Sale (Full)"
        else:
            # Suche alleinstehenden P/S/E der vor einem Datum steht
            # Pattern: tx_code gefolgt von Datum
            tx_before_date = re.search(
                r"(?<!\w)(P|S|E)\s+\d{2}/\d{2}/\d{4}", block
            )
            if tx_before_date:
                tx_code = tx_before_date.group(1)
                transaction_type = TRANSACTION_TYPE_CODES.get(tx_code, tx_code)
            else:
                # Fallback: irgendein alleinstehender P/S/E
                tx_match = RE_TRANSACTION_TYPE.search(block)
                if tx_match:
                    transaction_type = TRANSACTION_TYPE_CODES.get(
                        tx_match.group(1), tx_match.group(1)
                    )
                else:
                    return None  # Kein Transaktionstyp gefunden

        # Dates extrahieren (transaction_date, notification/filing_date)
        dates = RE_DATE.findall(block)
        transaction_date = dates[0] if len(dates) >= 1 else ""
        notification_date = dates[1] if len(dates) >= 2 else ""

        # Amount extrahieren
        # Strip asset type codes like [ST], [GS] from the block before searching
        # for amounts, because line wrapping can put them between "$15,001 -" and "$50,000"
        amount_search_text = RE_ASSET_TYPE.sub("", block)
        # Also collapse multiple spaces after stripping
        amount_search_text = re.sub(r"\s+", " ", amount_search_text)

        amount_match = RE_AMOUNT_RANGE.search(amount_search_text)
        if amount_match:
            amount = amount_match.group(0)
        else:
            # Fallback: zwei einzelne Dollar-Betraege als Range interpretieren
            single_amounts = RE_AMOUNT_SINGLE.findall(amount_search_text)
            if len(single_amounts) >= 2:
                amount = f"{single_amounts[0]} - {single_amounts[1]}"
            elif single_amounts:
                amount = single_amounts[0]
            else:
                amount = ""

        # Asset Name extrahieren:
        # Alles vor dem Transaktionstyp-Code (P/S/E) der vor dem Datum steht
        asset_name = self._extract_asset_name(block)

        # Subholding extrahieren (Depot/Trust-Name aus "S O: <Name>"-Zeile)
        subholding_match = re.search(r"S\s+O:\s*(.+)", block)
        subholding = subholding_match.group(1).strip() if subholding_match else ""

        return {
            "ticker": ticker,
            "asset_name": asset_name,
            "asset_type": asset_type,
            "transaction_type": transaction_type,
            "transaction_date": transaction_date,
            "notification_date": notification_date,
            "amount": amount,
            "owner": owner,
            "subholding": subholding,
        }

    def _extract_asset_name(self, block: str) -> str:
        """
        Extrahiert den Asset-Namen aus einem Transaktionsblock.

        Strategie: Nimm den Text vor dem Transaktionstyp-Code und entferne
        Ticker, Asset-Type-Code und sonstige Metadaten.
        """
        # Entferne Owner-Prefix
        work = re.sub(r"^(SP|JT|DC)\s+", "", block)

        # Finde die Position des Transaktionstyps vor dem Datum
        # Sale (partial) / Sale (full) zuerst
        split_match = (
            RE_SALE_PARTIAL.search(work)
            or RE_SALE_FULL.search(work)
        )
        if not split_match:
            # Alleinstehenden P/S/E vor Datum suchen
            split_match = re.search(r"(?<!\w)(P|S|E)\s+\d{2}/\d{2}/\d{4}", work)

        if split_match:
            asset_part = work[:split_match.start()]
        else:
            # Fallback: alles vor dem ersten Datum
            date_match = RE_DATE.search(work)
            if date_match:
                asset_part = work[:date_match.start()]
            else:
                asset_part = work

        # Entferne Ticker in Klammern: (SCHW), (NVDA), etc.
        asset_part = RE_TICKER.sub("", asset_part)

        # Entferne Asset-Type-Code: [ST], [OP], [GS], etc.
        asset_part = RE_ASSET_TYPE.sub("", asset_part)

        # Entferne uebrige Dollar-Betraege (falls vorhanden)
        asset_part = RE_AMOUNT_RANGE.sub("", asset_part)
        asset_part = RE_AMOUNT_SINGLE.sub("", asset_part)

        # Entferne Datumsangaben (falls vorhanden)
        asset_part = RE_DATE.sub("", asset_part)

        # Bereinigung: mehrfache Leerzeichen, Sonderzeichen am Rand
        asset_part = re.sub(r"\s+", " ", asset_part).strip()
        asset_part = asset_part.strip(" -,.")

        return asset_part

    def _normalize_transactions(
        self,
        parsed: dict,
        filing_date: str,
        doc_id: str,
        year: int,
        member_name_from_xml: str,
    ) -> list[dict]:
        """
        Wandelt geparste Transaktionen in das finale CSV-Format um.

        Args:
            parsed:              Output von _parse_ptr_pdf()
            filing_date:         FilingDate aus XML
            doc_id:              DocID aus XML
            year:                Filing Year
            member_name_from_xml: Name aus XML (First Last)

        Returns:
            Liste von Dicts mit CSV_COLUMNS-Schlueseln.
        """
        pdf_url = PDF_URL_TEMPLATE.format(year=year, doc_id=doc_id)

        # Name: bevorzuge XML-Name (konsistenter), Fallback auf PDF-Name
        name = member_name_from_xml or parsed.get("name", "")
        name = name.strip()

        # Disclosure Date: bevorzuge PDF notification_date, Fallback auf XML filing_date
        disclosure_date = parsed.get("notification_date", "") or filing_date

        rows = []
        for tx in parsed.get("transactions", []):
            row = {
                "name": name,
                "ticker": tx.get("ticker", ""),
                "asset_name": tx.get("asset_name", ""),
                "asset_type": tx.get("asset_type", ""),
                "transaction_type": tx.get("transaction_type", ""),
                "amount": tx.get("amount", ""),
                "transaction_date": tx.get("transaction_date", ""),
                "owner": tx.get("owner", "Self"),
                "subholding": tx.get("subholding", ""),
                "comment": "",
                "disclosure_date": disclosure_date,
                "ptr_link": pdf_url,
            }
            rows.append(row)

        return rows

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _request_with_retry(self, url: str) -> requests.Response:
        """HTTP-GET mit Retry-Logik."""
        last_exc = None
        for attempt in range(1, settings.SCRAPER_RETRY_ATTEMPTS + 1):
            try:
                response = self._http.get(url, timeout=30)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                last_exc = e
                if attempt < settings.SCRAPER_RETRY_ATTEMPTS:
                    logger.warning(
                        f"  Versuch {attempt}/{settings.SCRAPER_RETRY_ATTEMPTS} "
                        f"fehlgeschlagen fuer {url}: {e} "
                        f"- retry in {settings.SCRAPER_RETRY_DELAY_SECONDS}s"
                    )
                    time.sleep(settings.SCRAPER_RETRY_DELAY_SECONDS)
                else:
                    raise last_exc

    def _build_member_name(self, first: str, last: str) -> str:
        """Baut den Member-Namen aus Vor- und Nachname (ohne Hon. Prefix)."""
        first = first.strip()
        last = last.strip()
        if first and last:
            return f"{first} {last}"
        return first or last

    @staticmethod
    def _xml_text(parent, tag: str) -> str:
        """Sicheres Auslesen eines XML-Textelements."""
        elem = parent.find(tag)
        if elem is not None and elem.text:
            return elem.text.strip()
        return ""


# ---------------------------------------------------------------------------
# Logging-Setup
# ---------------------------------------------------------------------------

def _setup_logging():
    """Konfiguriert Logging: Konsole + Datei (logs/house_scraper.log)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(project_root, settings.LOGS_PATH)
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "house_scraper.log")

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(level=logging.INFO, format=log_format)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(file_handler)

    logger.info(f"Log-Datei: {log_file}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="House of Representatives Trade Scraper"
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Einzelnes Jahr zum Scrapen (default: aktuelles + vorheriges Jahr)",
    )
    parser.add_argument(
        "--no-delta",
        action="store_true",
        help="Kein Delta-Modus: alle Filings neu verarbeiten",
    )
    parser.add_argument(
        "--doc-id",
        type=str,
        default=None,
        help="Einzelne DocID zum Testen (erfordert --year)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur parsen und anzeigen, nicht speichern",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Nur Filing-Liste herunterladen und anzeigen (keine PDFs)",
    )
    args = parser.parse_args()

    _setup_logging()
    scraper = HouseScraper()

    # --doc-id: Einzelne PDF testen
    if args.doc_id:
        if not args.year:
            print("FEHLER: --doc-id erfordert --year")
            sys.exit(1)

        logger.info(f"Einzeltest: DocID={args.doc_id}, Year={args.year}")
        try:
            pdf_bytes = scraper._download_pdf(args.doc_id, args.year)
            parsed = scraper._parse_ptr_pdf(pdf_bytes)

            print(f"\nPDF URL: {PDF_URL_TEMPLATE.format(year=args.year, doc_id=args.doc_id)}")
            print(f"Name (aus PDF): {parsed['name']}")
            print(f"Notification Date: {parsed['notification_date']}")
            print(f"Transaktionen: {len(parsed['transactions'])}")
            print("-" * 60)

            for i, tx in enumerate(parsed["transactions"], 1):
                print(f"\n  Transaction {i}:")
                for k, v in tx.items():
                    print(f"    {k}: {v}")

            if not args.dry_run:
                transactions = scraper._normalize_transactions(
                    parsed, "", args.doc_id, args.year, parsed.get("name", "")
                )
                df = pd.DataFrame(transactions, columns=CSV_COLUMNS)
                if not df.empty:
                    path = scraper.save(df)
                    print(f"\nGespeichert unter: {path}")

        except Exception as e:
            logger.exception(f"Fehler bei Einzeltest: {e}")
            sys.exit(1)
        return

    # --list-only: Nur Filing-Liste anzeigen
    if args.list_only:
        current_year = datetime.now().year
        years = [args.year] if args.year else [current_year, current_year - 1]

        for year in years:
            try:
                filings = scraper._download_filing_list(year)
                print(f"\n=== {year}: {len(filings)} PTR-Filings ===")
                if not filings.empty:
                    print(filings.to_string(index=False))
            except Exception as e:
                print(f"Fehler fuer {year}: {e}")
        return

    # Standard-Modus
    years = [args.year] if args.year else None
    delta = not args.no_delta

    df = scraper.run(years=years, delta=delta)
    print(scraper.get_summary(df))

    if not df.empty:
        if args.dry_run:
            print("\n[DRY-RUN] Daten werden nicht gespeichert.")
            print("\nErste 10 Zeilen:")
            print(df.head(10).to_string(index=False))
        else:
            path = scraper.save(df)
            print(f"\nGespeichert unter: {path}")
            print("\nErste 10 Zeilen:")
            print(df.head(10).to_string(index=False))
    else:
        print("\nKeine Trades gefunden.")


if __name__ == "__main__":
    main()
