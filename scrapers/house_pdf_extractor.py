"""
Congress Trade Tracker - House PDF Extractor
=============================================
Extraktion von House PTR Paper Forms (Hand Delivered) via Anthropic Vision API.

Diese PDFs haben keinen Textlayer (gescannte Papierformulare) und koennen
nicht mit pdfplumber geparst werden. Stattdessen:
    1. Liest house_scraper_fehler.csv (mit reason="empty_text")
    2. Laedt PDF per HTTP herunter (oeffentlich zugaenglich, kein Auth noetig)
    3. Konvertiert PDF-Seiten zu Bildern via pdfplumber
    4. Rotiert Bilder falls noetig (Portrait = rotiertes Landscape-Formular)
    5. Sendet jedes Bild an Anthropic Vision API fuer OCR + Extraktion
    6. Speichert Ergebnisse in house_trades.csv (Append + Dedup)

Verwendung:
    # Alle fehler-PDFs mit reason="empty_text" verarbeiten:
    python scrapers/house_pdf_extractor.py

    # Einzelne DocID verarbeiten:
    python scrapers/house_pdf_extractor.py --doc-id 8221228 --year 2025

    # Trockenlauf (kein Speichern):
    python scrapers/house_pdf_extractor.py --dry-run

    # Anderes Modell:
    python scrapers/house_pdf_extractor.py --model claude-sonnet-4-6
"""

import argparse
import base64
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import pdfplumber
import requests
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings
from scrapers.house_scraper import CSV_COLUMNS, DEDUP_SUBSET, PDF_URL_TEMPLATE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
AMOUNT_RANGES = {
    "A": "$1,001 - $15,000",
    "B": "$15,001 - $50,000",
    "C": "$50,001 - $100,000",
    "D": "$100,001 - $250,000",
    "E": "$250,001 - $500,000",
    "F": "$500,001 - $1,000,000",
    "G": "$1,000,001 - $5,000,000",
    "H": "$5,000,001 - $25,000,000",
    "I": "$25,000,001 - $50,000,000",
    "J": "Over $50,000,000",
    "K": "$1,000,001+",
}

# Owner-Langformen (konsistent mit house_trades.csv)
OWNER_LONG = {
    "Self": "Self",
    "S": "Self",
    "Spouse": "Spouse",
    "SP": "Spouse",
    "Joint": "Joint",
    "JT": "Joint",
    "Dependent Child": "Dependent Child",
    "DC": "Dependent Child",
}

EXTRACTION_SYSTEM_PROMPT = """You are a precise data extractor for US House of Representatives Periodic Transaction Report (PTR) paper forms.
These are scanned paper forms showing financial transactions by US House Members.
Always respond with valid JSON only. No explanations, no markdown fences."""

EXTRACTION_USER_PROMPT = """Extract ALL transactions from this House PTR paper form image.

IMPORTANT: These forms come in 3 variants:
1. ROTATED HANDWRITTEN: 90° rotated, handwritten entries, 1 trade per page
2. LANDSCAPE TABLE: Normal orientation, typed entries, many trades per page (e.g. McCaul)
3. HYBRID: 90° rotated but typed entries, multiple trades per page (e.g. Fleischmann)

The form columns are:
- SP/JT/DC: Owner code (SP=Spouse, JT=Joint, DC=Dependent Child, blank=Self)
- FULL ASSET NAME: Complete name (NOT ticker symbol)
- TYPE OF TRANSACTION: Checkmark in Purchase/Sale/Partial Sale/Exchange column
- DATE OF TRANSACTION: MM/DD/YY or MM/DD/YYYY
- DATE NOTIFIED: MM/DD/YY or MM/DD/YYYY
- AMOUNT OF TRANSACTION: Letter A through K (checkbox), each mapping to a dollar range:
  A=$1,001-$15,000, B=$15,001-$50,000, C=$50,001-$100,000, D=$100,001-$250,000,
  E=$250,001-$500,000, F=$500,001-$1,000,000, G=$1,000,001-$5,000,000,
  H=$5,000,001-$25,000,000, I=$25,000,001-$50,000,000, J=Over $50,000,000,
  K=Spouse/DC over $1,000,000

CRITICAL RULES:
- Extract EVERY transaction row, do not skip any
- For the AMOUNT field: Look for the checked letter (A-K) and return that letter as amount_code
- For TYPE B/C forms with many trades: The amount checkboxes may be in a grid at the bottom,
  where each COLUMN corresponds to a transaction ROW above. Match them carefully by position.
- Owner codes: "SP" or "Spouse" → "Spouse", "JT" or "Joint" → "Joint",
  "DC" or "Dependent Child" → "Dependent Child", blank → "Self"
- Transaction types: "Purchase", "Sale", "Sale (Partial)", "Exchange"
- Normalize all dates to MM/DD/YYYY (convert 2-digit years: 25 → 2025, etc.)
- The ticker field is ALWAYS empty (paper forms use full names, not tickers)
- SKIP the pre-printed example/instruction rows. These appear on EVERY form as grey/light
  printed text showing how to fill it out (e.g. "Mega Corp, Common Stock" with date "08/14/2012",
  or "IBM Corp."). They are NOT real transactions. Real transactions are handwritten or typed
  ON TOP OF the form. If ONLY pre-printed example text is visible with no actual filled-in
  data, return an empty transactions array.
- Skip completely empty rows
- row_index: Assign each transaction a sequential 1-based row number from its physical
  position on the form. If an OCR artifact causes you to read the same form row twice,
  return it only ONCE. If a filer genuinely filled in multiple rows that look identical
  (same asset, same date, same amount on different lines), return ALL of them — each
  with its distinct row_index.
- If the page says "Nothing to report" → return empty transactions array
- If it's a blank continuation page with no filled data → return empty transactions array
- FILER NOTES at the bottom: Extract any notes and put them in the "comment" field
  of the corresponding transaction (match by NOTE NUMBER if present)

Return JSON:
{
  "is_transaction_page": true,
  "member_name": "FIRST LAST" or "" if not visible,
  "notification_date": "MM/DD/YYYY" or "" if not visible,
  "transactions": [
    {
      "row_index": 1,
      "owner": "Self" or "Spouse" or "Joint" or "Dependent Child",
      "asset_name": "Full Company or Fund Name",
      "transaction_type": "Purchase" or "Sale" or "Sale (Partial)" or "Exchange",
      "transaction_date": "MM/DD/YYYY",
      "notification_date": "MM/DD/YYYY",
      "amount_code": "A" through "K",
      "comment": ""
    }
  ]
}

Set is_transaction_page=false for cover letters or non-PTR pages.
Return empty transactions array if "Nothing to report" or no filled rows."""


class HousePdfExtractor:
    """
    Extrahiert Transaktionen aus House PTR Paper Forms via Anthropic Vision API.

    Die Paper Forms sind gescannte PDFs ohne Textlayer. Der Extractor:
    - Laedt PDFs per HTTP (oeffentlich zugaenglich)
    - Konvertiert Seiten zu Bildern via pdfplumber
    - Rotiert bei Bedarf (Portrait → Landscape)
    - Sendet jedes Bild an Claude Vision fuer strukturierte Datenextraktion
    - Speichert in house_trades.csv (Append + Dedup, identisches Schema)
    """

    def __init__(self, model: Optional[str] = None):
        """
        Args:
            model: Anthropic-Modell fuer Vision-Extraktion.
                   Standard: settings.PDF_EXTRACTOR_MODEL
        """
        self.model = model or settings.PDF_EXTRACTOR_MODEL
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._output_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "house_trades.csv"
        )
        self._fehler_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "house_scraper_fehler.csv"
        )
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": settings.SCRAPER_USER_AGENT})
        self._anthropic = None  # lazy init

    # ------------------------------------------------------------------
    # Oeffentliche API
    # ------------------------------------------------------------------

    def run(
        self,
        input_csv: Optional[str] = None,
        single_doc_id: Optional[str] = None,
        year: Optional[int] = None,
        dry_run: bool = False,
    ) -> pd.DataFrame:
        """
        Haupteinstiegspunkt. Verarbeitet PDFs aus house_scraper_fehler.csv
        oder eine einzelne DocID.

        Args:
            input_csv:     Pfad zur Input-CSV (Standard: house_scraper_fehler.csv)
            single_doc_id: Einzelne DocID verarbeiten (erfordert year)
            year:          Jahr fuer die DocID
            dry_run:       Wenn True, nur extrahieren, nicht speichern

        Returns:
            pd.DataFrame mit allen extrahierten Trades.
        """
        if single_doc_id:
            if not year:
                raise ValueError("--doc-id erfordert --year")
            rows = [{
                "doc_id": single_doc_id,
                "year": year,
                "name": "UNKNOWN",
                "disclosure_date": "",
                "ptr_link": PDF_URL_TEMPLATE.format(year=year, doc_id=single_doc_id),
                "reason": "empty_text",
            }]
        else:
            csv_path = input_csv or self._fehler_path
            if not os.path.isfile(csv_path):
                logger.error(f"Input-CSV nicht gefunden: {csv_path}")
                return pd.DataFrame(columns=CSV_COLUMNS)
            df_input = pd.read_csv(csv_path)
            # Nur empty_text PDFs verarbeiten (download_error etc. ueberspringen)
            df_input = df_input[df_input["reason"] == "empty_text"]
            rows = df_input.to_dict("records")

        if not rows:
            logger.info("Keine PDFs zum Verarbeiten gefunden.")
            return pd.DataFrame(columns=CSV_COLUMNS)

        self._init_anthropic()
        logger.info(
            f"=== House PDF Extractor gestartet: {len(rows)} PDFs "
            f"(Modell: {self.model}) ==="
        )
        all_trades: list[dict] = []

        for i, row in enumerate(rows, 1):
            doc_id = str(row.get("doc_id", ""))
            pdf_year = int(row.get("year", year or 2025))
            member_name = str(row.get("name", "UNKNOWN"))
            disclosure_date = str(row.get("disclosure_date", ""))
            ptr_link = str(row.get(
                "ptr_link",
                PDF_URL_TEMPLATE.format(year=pdf_year, doc_id=doc_id),
            ))

            logger.info(
                f"[{i}/{len(rows)}] DocID={doc_id} | {member_name} | {disclosure_date}"
            )
            try:
                trades = self._process_pdf(
                    doc_id, pdf_year, member_name, disclosure_date, ptr_link
                )
                all_trades.extend(trades)
                logger.info(f"  >> {len(trades)} Trades extrahiert")
            except Exception:
                logger.exception(f"  >> Fehler bei DocID={doc_id}")

            time.sleep(settings.SCRAPER_RATE_LIMIT_SECONDS)

        df = pd.DataFrame(all_trades)
        if not df.empty:
            before = len(df)
            # Intra-Batch-Dedup: nur echte OCR-Duplikate entfernen (gleicher Inhalt
            # UND gleiche Seite + Zeile = dieselbe Formularzeile 2x gelesen).
            # Legitime Mehrfachtrades mit identischem Inhalt auf VERSCHIEDENEN Zeilen
            # bleiben erhalten, weil ihre _row_index unterschiedlich ist.
            _intra_dedup = DEDUP_SUBSET + ["_page_num", "_row_index"]
            df[_intra_dedup] = df[_intra_dedup].fillna("")
            dupes = df[df.duplicated(subset=_intra_dedup, keep=False)]
            if not dupes.empty:
                logger.info(f"  OCR-Duplikate ({len(dupes)} Zeilen entfernt):")
                for _, row in dupes.iterrows():
                    logger.info(
                        f"    DUPE | {row['name']} | {row['asset_name']} | "
                        f"{row['transaction_type']} | {row['amount']} | "
                        f"{row['transaction_date']} | Seite {row['_page_num']} "
                        f"Zeile {row['_row_index']}"
                    )
            df.drop_duplicates(subset=_intra_dedup, keep="first", inplace=True)
            df.drop(columns=["_page_num", "_row_index"], inplace=True)
            if "csv_upload_time" not in df.columns:
                df["csv_upload_time"] = ""
            df = df[CSV_COLUMNS]
            logger.info(
                f"=== Gesamt: {len(df)} Trades "
                f"({before - len(df)} OCR-Duplikate entfernt) ==="
            )

        if not dry_run and not df.empty:
            self.save(df)

        return df

    def save(self, df: pd.DataFrame) -> str:
        """
        Speichert Trades in house_trades.csv (Append + Dedup).
        Gleiche Logik wie HouseScraper.save().

        Returns:
            Pfad zur gespeicherten Datei.
        """
        if df.empty:
            logger.warning("Keine Trades zum Speichern.")
            return self._output_path

        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)

        # Neue Zeilen mit aktuellem Upload-Zeitstempel versehen
        upload_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df["csv_upload_time"] = upload_time

        if os.path.isfile(self._output_path):
            existing = pd.read_csv(self._output_path)
            for col in DEDUP_SUBSET:
                if col not in existing.columns:
                    existing[col] = ""
            # Anti-Join: nur Zeilen anfügen, die noch nicht in der CSV sind.
            # Kein drop_duplicates auf combined, da intra-Batch-Dedup bereits in run()
            # erledigt wurde — legitime Mehrfachtrades würden sonst doch entfernt.
            existing_keys = set(
                existing[DEDUP_SUBSET].fillna("").apply(tuple, axis=1)
            )
            new_mask = ~df[DEDUP_SUBSET].fillna("").apply(tuple, axis=1).isin(
                existing_keys
            )
            new_rows = df[new_mask]
            if new_rows.empty:
                logger.info(
                    f"Keine neuen Trades (alle bereits in {self._output_path})."
                )
                return self._output_path
            combined = pd.concat([existing, new_rows], ignore_index=True)
            combined.to_csv(self._output_path, index=False)
            logger.info(
                f"Daten angehaengt >> {self._output_path} "
                f"({len(new_rows)} neue Zeilen, {len(combined)} gesamt)"
            )
        else:
            df.to_csv(self._output_path, index=False)
            logger.info(
                f"Neue Datei erstellt >> {self._output_path} ({len(df)} Zeilen)"
            )

        return self._output_path

    # ------------------------------------------------------------------
    # Interne Methoden - PDF Download & Image Extraction
    # ------------------------------------------------------------------

    def _process_pdf(
        self,
        doc_id: str,
        year: int,
        member_name: str,
        disclosure_date: str,
        ptr_link: str,
    ) -> list[dict]:
        """Laedt PDF, extrahiert Seiten als Bilder, sendet an Vision API."""
        pdf_bytes = self._download_pdf(doc_id, year)
        page_images = self._pdf_to_images(pdf_bytes)

        if not page_images:
            logger.warning(f"  Keine Bilder aus PDF extrahiert (DocID={doc_id})")
            return []

        logger.info(f"  {len(page_images)} Seiten extrahiert.")
        all_trades: list[dict] = []

        for page_num, image in enumerate(page_images, start=1):
            logger.debug(f"  Verarbeite Seite {page_num}")
            try:
                trades = self._extract_from_image(
                    image, member_name, disclosure_date, ptr_link, page_num
                )
                all_trades.extend(trades)
            except Exception:
                logger.exception(f"  Fehler auf Seite {page_num} (DocID={doc_id})")

            time.sleep(settings.PDF_EXTRACTOR_PAGE_PAUSE_SECONDS)

        return all_trades

    def _download_pdf(self, doc_id: str, year: int) -> bytes:
        """Laedt eine einzelne PDF-Datei per HTTP herunter."""
        url = PDF_URL_TEMPLATE.format(year=year, doc_id=doc_id)
        logger.debug(f"  Lade PDF: {url}")

        last_exc = None
        for attempt in range(1, settings.SCRAPER_RETRY_ATTEMPTS + 1):
            try:
                response = self._http.get(url, timeout=30)
                response.raise_for_status()
                return response.content
            except requests.RequestException as e:
                last_exc = e
                if attempt < settings.SCRAPER_RETRY_ATTEMPTS:
                    logger.warning(
                        f"  Download-Fehler Versuch {attempt}/"
                        f"{settings.SCRAPER_RETRY_ATTEMPTS}: {e}"
                    )
                    time.sleep(settings.SCRAPER_RETRY_DELAY_SECONDS)
                else:
                    raise last_exc

    def _pdf_to_images(self, pdf_bytes: bytes) -> list[Image.Image]:
        """
        Extrahiert Seiten aus einem PDF als PIL Images.

        Rotation: Wenn page.width < page.height (Portrait), ist die Seite ein
        rotiertes Landscape-Formular → 90° gegen Uhrzeigersinn drehen.
        """
        images = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                # Seite zu Bild rendern (200 DPI fuer gute OCR-Qualitaet)
                pil_image = page.to_image(resolution=200).original

                # Rotation: Portrait-orientierte Seite = rotiertes Landscape-Formular
                if page.width < page.height:
                    pil_image = pil_image.rotate(90, expand=True)
                    logger.debug(
                        f"  Seite rotiert (Portrait→Landscape): "
                        f"{page.width}x{page.height}"
                    )

                images.append(pil_image)

        return images

    # ------------------------------------------------------------------
    # Interne Methoden - Vision API
    # ------------------------------------------------------------------

    def _init_anthropic(self):
        """Initialisiert Anthropic Client (lazy)."""
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY Umgebungsvariable nicht gesetzt. "
                    "Bitte in .env oder Systemumgebung setzen."
                )
            self._anthropic = anthropic.Anthropic(api_key=api_key)
            logger.info(f"Anthropic SDK initialisiert (Modell: {self.model})")
        except ImportError:
            raise ImportError(
                "anthropic Paket nicht installiert. "
                "Bitte 'pip install anthropic' ausfuehren."
            )

    def _image_to_base64(self, image: Image.Image) -> str:
        """Konvertiert PIL Image zu Base64-kodiertem JPEG."""
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        return base64.standard_b64encode(buf.getvalue()).decode("utf-8")

    def _extract_from_image(
        self,
        image: Image.Image,
        member_name: str,
        disclosure_date: str,
        ptr_link: str,
        page_num: int,
    ) -> list[dict]:
        """Sendet ein Seitenbild an die Vision API und gibt Transaktionen zurueck."""
        image_b64 = self._image_to_base64(image)

        for attempt in range(1, settings.SCRAPER_RETRY_ATTEMPTS + 1):
            try:
                response = self._anthropic.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=EXTRACTION_SYSTEM_PROMPT,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": image_b64,
                                    },
                                },
                                {"type": "text", "text": EXTRACTION_USER_PROMPT},
                            ],
                        }
                    ],
                )
                raw_text = response.content[0].text.strip()
                return self._parse_api_response(
                    raw_text, member_name, disclosure_date, ptr_link, page_num
                )

            except Exception as e:
                logger.warning(
                    f"  API-Fehler Seite {page_num}, Versuch "
                    f"{attempt}/{settings.SCRAPER_RETRY_ATTEMPTS}: {e}"
                )
                if attempt < settings.SCRAPER_RETRY_ATTEMPTS:
                    time.sleep(settings.SCRAPER_RETRY_DELAY_SECONDS)
                else:
                    raise

        return []

    def _parse_api_response(
        self,
        raw_text: str,
        member_name: str,
        disclosure_date: str,
        ptr_link: str,
        page_num: int = 1,
    ) -> list[dict]:
        """Parst die JSON-Antwort der Vision API und normalisiert auf CSV_COLUMNS."""
        # JSON-Fences entfernen falls vorhanden
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"  JSON-Parse-Fehler: {e}\n  Raw: {raw_text[:300]}")
            return []

        if not data.get("is_transaction_page", True):
            logger.debug("  Seite ist kein PTR-Formular (Cover Letter oder leer).")
            return []

        # Name aus API-Antwort als Fallback
        api_name = str(data.get("member_name", "")).strip()
        # disclosure_date: bevorzuge CSV-Wert, Fallback auf API
        api_notif_date = str(data.get("notification_date", "")).strip()

        transactions: list = data.get("transactions", [])
        trades: list[dict] = []

        for idx, tx in enumerate(transactions):
            if not isinstance(tx, dict):
                continue

            tx_type = str(tx.get("transaction_type", "")).strip()
            # Normalisiere Transaction Type
            tx_type_lower = tx_type.lower()
            if "partial" in tx_type_lower:
                tx_type = "Sale (Partial)"
            elif tx_type_lower == "sale":
                tx_type = "Sale"
            elif tx_type_lower == "purchase":
                tx_type = "Purchase"
            elif tx_type_lower == "exchange":
                tx_type = "Exchange"
            else:
                logger.debug(f"  Unbekannter transaction_type: {tx_type!r} - uebersprungen.")
                continue

            # Amount aus amount_code
            amount = ""
            amount_code = str(tx.get("amount_code", "")).strip().upper()
            if amount_code in AMOUNT_RANGES:
                amount = AMOUNT_RANGES[amount_code]

            # Dates normalisieren
            tx_date = self._normalize_date(
                str(tx.get("transaction_date", "")).strip()
            )
            tx_notif_date = self._normalize_date(
                str(tx.get("notification_date", "")).strip()
            )

            # Owner normalisieren auf Langform
            raw_owner = str(tx.get("owner", "")).strip()
            owner = OWNER_LONG.get(raw_owner, raw_owner)
            if not owner:
                owner = "Self"

            asset_name = str(tx.get("asset_name", "")).strip()
            comment = str(tx.get("comment", "")).strip()

            # Subholding-Header überspringen: kein Datum + kein Amount = kein echter Trade
            if not tx_date and not amount_code:
                logger.debug(
                    f"  Subholding-Header übersprungen: {asset_name!r} "
                    f"(transaction_type={tx_type!r}, kein Datum/Amount)"
                )
                continue

            # Name: bevorzuge member_name aus CSV, dann API, dann UNKNOWN
            name = member_name.strip() if member_name and member_name != "UNKNOWN" else ""
            if not name:
                name = api_name or "UNKNOWN"

            # Disclosure date: bevorzuge CSV, dann per-transaction, dann page-level API
            disc_date = disclosure_date.strip() if disclosure_date else ""
            if not disc_date:
                disc_date = tx_notif_date or api_notif_date

            trades.append({
                "name": name,
                "ticker": "",  # Papierformular hat keine Ticker
                "asset_name": asset_name,
                "asset_type": "",  # Papierformular hat kein Asset-Type-Feld
                "transaction_type": tx_type,
                "amount": amount,
                "transaction_date": tx_date,
                "owner": owner,
                "subholding": "",  # Nur aus Filer Notes ableitbar, nicht strukturiert
                "comment": comment,
                "disclosure_date": disc_date,
                "ptr_link": ptr_link,
                "csv_upload_time": "",
                "_page_num": page_num,
                "_row_index": int(tx.get("row_index", idx + 1)),
            })

        return trades

    def _normalize_date(self, date_str: str) -> str:
        """Normalisiert Datum auf MM/DD/YYYY (konvertiert 2-stellige Jahre)."""
        if not date_str:
            return ""
        # MM/DD/YYYY
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", date_str)
        if m:
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
        # MM/DD/YY
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2})$", date_str)
        if m:
            yy = int(m.group(3))
            full_year = 2000 + yy if yy < 50 else 1900 + yy
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{full_year}"
        # MM-DD-YY (McCaul uses dashes sometimes)
        m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{2,4})$", date_str)
        if m:
            yy = int(m.group(3))
            if yy < 100:
                yy = 2000 + yy if yy < 50 else 1900 + yy
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{yy}"
        logger.debug(f"  Datum konnte nicht normalisiert werden: {date_str!r}")
        return date_str


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _setup_logging():
    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "house_pdf_extractor.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def main():
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="House PDF Extractor - OCR via Claude Vision API"
    )
    parser.add_argument(
        "--input-csv",
        help="Pfad zur Input-CSV (Standard: data/raw/house_scraper_fehler.csv)",
    )
    parser.add_argument(
        "--doc-id",
        type=str,
        help="Einzelne DocID verarbeiten (erfordert --year)",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Jahr fuer --doc-id (z.B. 2025)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Anthropic-Modell (Standard: {settings.PDF_EXTRACTOR_MODEL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extrahieren ohne Speichern",
    )
    args = parser.parse_args()

    extractor = HousePdfExtractor(model=args.model)
    df = extractor.run(
        input_csv=args.input_csv,
        single_doc_id=args.doc_id,
        year=args.year,
        dry_run=args.dry_run,
    )

    print(f"\n>>> Extraktion abgeschlossen: {len(df)} Trades")
    if not df.empty:
        print(
            df[["name", "asset_name", "transaction_type", "amount", "transaction_date"]]
            .to_string(index=False)
        )


if __name__ == "__main__":
    main()
