"""
Congress Trade Tracker - House PDF Extractor v2
================================================
Verbesserte Extraktion via img2table Grid-Erkennung + Pixel-Density Amount-Detection.

Aenderungen gegenueber v1:
    - OpenCV morphologische Linienerkennung fuer Tabellenstruktur → Zell-Bounding-Boxes
    - Amount-Spalten (A-K) via Pixel-Density statt Vision API
    - Vision API nur noch fuer Text-Felder, pro 5-Zeilen-Batch (statt ganze Seite)
    - Grid-Detection auf Raw-Graustufenbild (kein Deskew/CLAHE — zerstoert Gitterlinien)
    - Fallback auf ganzes Seitenbild wenn Grid nicht erkannt wird (Terminal-Ausgabe)

Verwendung:
    python scrapers/house_pdf_extractor_v2.py
    python scrapers/house_pdf_extractor_v2.py --doc-id 8221228 --year 2025
    python scrapers/house_pdf_extractor_v2.py --dry-run
    python scrapers/house_pdf_extractor_v2.py --model claude-sonnet-4-6
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

import cv2
import numpy as np
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

AMOUNT_LETTERS = list(AMOUNT_RANGES.keys())  # A-K in order

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

# --- Settings mit Defaults (falls nicht in settings.py) ---
PDF_EXTRACTOR_DPI = getattr(settings, "PDF_EXTRACTOR_DPI", 300)
AMOUNT_PIXEL_THRESHOLD = getattr(settings, "PDF_EXTRACTOR_AMOUNT_PIXEL_THRESHOLD", 50)
ROW_BATCH_SIZE = getattr(settings, "PDF_EXTRACTOR_ROW_BATCH_SIZE", 5)
USE_GRID_DETECTION = getattr(settings, "PDF_EXTRACTOR_USE_GRID_DETECTION", True)

# --- Vision API Prompts (vereinfacht: nur Text-Felder, kein Amount) ---

ROW_BATCH_SYSTEM_PROMPT = """You are a precise data extractor for US House of Representatives \
Periodic Transaction Report (PTR) paper forms. Always respond with valid JSON only. \
No explanations, no markdown fences."""

ROW_BATCH_USER_PROMPT = """Extract the transactions from this cropped table image.
The image shows {n_rows} transaction row(s) from a House PTR paper form.

For EACH row, extract:
- owner: "Self", "Spouse", "Joint", or "Dependent Child" (SP=Spouse, JT=Joint, DC=Dependent Child, blank=Self)
- asset_name: Full company/fund name (NOT ticker symbol)
- transaction_type: "Purchase", "Sale", "Sale (Partial)", or "Exchange"
- transaction_date: MM/DD/YYYY
- notification_date: MM/DD/YYYY
- comment: Any filer notes for this row
- readable: true if the row text is clearly legible, false if blurry/illegible/uncertain

CRITICAL DATE RULES:
- All 2-digit years MUST be interpreted as 20XX, NEVER 19XX.
  Examples: "25" → 2025, "26" → 2026, "24" → 2024
- These are recent financial disclosures. No date should be before 2010.
- Output all dates as MM/DD/YYYY with 4-digit year.

Do NOT extract the amount — it is handled separately.
Skip pre-printed example rows (grey text like "Mega Corp", "IBM Corp.").
Skip completely empty rows.

Return JSON:
{{
  "transactions": [
    {{
      "row_index": 1,
      "owner": "Self",
      "asset_name": "Full Company Name",
      "transaction_type": "Purchase",
      "transaction_date": "MM/DD/YYYY",
      "notification_date": "MM/DD/YYYY",
      "comment": "",
      "readable": true
    }}
  ]
}}"""

# Fallback-Prompt (ganzes Seitenbild, wie v1 aber ohne form_variant)
FALLBACK_SYSTEM_PROMPT = """You are a precise data extractor for US House of Representatives \
Periodic Transaction Report (PTR) paper forms. These are scanned paper forms showing \
financial transactions by US House Members. Always respond with valid JSON only. \
No explanations, no markdown fences."""

FALLBACK_USER_PROMPT = """Extract ALL transactions from this House PTR paper form image.

These forms come in several variants:
1. ROTATED HANDWRITTEN: 90 rotated, handwritten entries, 1 trade per page
2. LANDSCAPE TABLE: Normal orientation, typed entries, many trades per page
3. HYBRID: 90 rotated but typed entries, multiple trades per page

The form columns are:
- SP/JT/DC: Owner code (SP=Spouse, JT=Joint, DC=Dependent Child, blank=Self)
- FULL ASSET NAME: Complete name (NOT ticker symbol)
- TYPE OF TRANSACTION: Checkmark in Purchase/Sale/Partial Sale/Exchange column
- DATE OF TRANSACTION: MM/DD/YY or MM/DD/YYYY
- DATE NOTIFIED: MM/DD/YY or MM/DD/YYYY
- AMOUNT OF TRANSACTION: Letter code A through K.
  A=$1,001-$15,000, B=$15,001-$50,000, C=$50,001-$100,000,
  D=$100,001-$250,000, E=$250,001-$500,000, F=$500,001-$1,000,000,
  G=$1,000,001-$5,000,000, H=$5,000,001-$25,000,000, I=$25,000,001-$50,000,000,
  J=Over $50,000,000, K=Spouse/DC Asset over $1,000,000

CRITICAL RULES:
- Extract EVERY transaction row, do not skip any
- For AMOUNT: Find the "x" in the A-K columns. Return both the letter as "amount_code" \
AND the dollar range as "amount".
- Owner codes: "SP"/"Spouse" -> "Spouse", "JT"/"Joint" -> "Joint", \
"DC"/"Dependent Child" -> "Dependent Child", blank -> "Self"
- Transaction types: "Purchase", "Sale", "Sale (Partial)", "Exchange"
- The ticker field is ALWAYS empty
- SKIP pre-printed example rows (grey text like "Mega Corp", "IBM Corp.")
- Skip completely empty rows
- If "Nothing to report" -> return empty transactions array

CRITICAL DATE RULES:
- All 2-digit years MUST be interpreted as 20XX, NEVER 19XX.
  Examples: "25" → 2025, "26" → 2026, "24" → 2024
- These are recent financial disclosures. No date should be before 2010.
- Output all dates as MM/DD/YYYY with 4-digit year.

QUALITY FLAG:
- For each transaction, set "readable": true if text is clearly legible, false if blurry/illegible/uncertain.

Return JSON:
{{
  "is_transaction_page": true,
  "member_name": "FIRST LAST" or "" if not visible,
  "notification_date": "MM/DD/YYYY" or "",
  "transactions": [
    {{
      "row_index": 1,
      "owner": "Self",
      "asset_name": "Full Company Name",
      "transaction_type": "Purchase",
      "transaction_date": "MM/DD/YYYY",
      "notification_date": "MM/DD/YYYY",
      "amount_code": "A",
      "amount": "$1,001 - $15,000",
      "comment": "",
      "readable": true
    }}
  ]
}}

Set is_transaction_page=false for cover letters or non-PTR pages."""


class HousePdfExtractorV2:
    """
    Verbesserte Extraktion via img2table + Pixel-Density + Vision API pro Zeilen-Batch.
    """

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.PDF_EXTRACTOR_MODEL
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._output_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "house_trades.csv"
        )
        self._fehler_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "house_scraper_fehler.csv"
        )
        self._done_path = os.path.join(
            self._project_root, settings.LOGS_PATH, "house_pdf_extractor_done.csv"
        )
        self._uncertain_path = os.path.join(
            self._project_root, settings.LOGS_PATH, "ocr_uncertain.csv"
        )
        self._uncertain_rows: list[dict] = []
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": settings.SCRAPER_USER_AGENT})
        self._anthropic = None

    # ------------------------------------------------------------------
    # Public API (identisch mit v1)
    # ------------------------------------------------------------------

    def _load_done_doc_ids(self) -> set:
        if not os.path.isfile(self._done_path):
            return set()
        df = pd.read_csv(self._done_path)
        return set(df["doc_id"].astype(str))

    def _save_done_doc_ids(self, doc_ids: list[str]):
        if not doc_ids:
            return
        new_df = pd.DataFrame({
            "doc_id": doc_ids,
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        if os.path.isfile(self._done_path):
            existing = pd.read_csv(self._done_path)
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined.drop_duplicates(subset=["doc_id"], keep="last", inplace=True)
        else:
            combined = new_df
        os.makedirs(os.path.dirname(self._done_path), exist_ok=True)
        combined.to_csv(self._done_path, index=False)
        logger.info(f"Done-CSV aktualisiert: {len(doc_ids)} DocIDs -> {self._done_path}")

    def run(
        self,
        input_csv: Optional[str] = None,
        single_doc_id: Optional[str] = None,
        year: Optional[int] = None,
        dry_run: bool = False,
        no_delta: bool = False,
    ) -> pd.DataFrame:
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
            df_input = df_input[df_input["reason"] == "empty_text"]

            if not no_delta:
                done_ids = self._load_done_doc_ids()
                if done_ids:
                    before = len(df_input)
                    df_input = df_input[~df_input["doc_id"].astype(str).isin(done_ids)]
                    skipped = before - len(df_input)
                    if skipped > 0:
                        logger.info(
                            f"Delta-Check: {skipped} bereits verarbeitete PDFs uebersprungen"
                        )

            rows = df_input.to_dict("records")

        if not rows:
            logger.info("Keine PDFs zum Verarbeiten gefunden.")
            return pd.DataFrame(columns=CSV_COLUMNS)

        self._init_anthropic()
        logger.info(
            f"=== House PDF Extractor v2 gestartet: {len(rows)} PDFs "
            f"(Modell: {self.model}, Grid: {USE_GRID_DETECTION}, "
            f"BatchSize: {ROW_BATCH_SIZE}, DPI: {PDF_EXTRACTOR_DPI}) ==="
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
            _intra_dedup = DEDUP_SUBSET + ["_page_num", "_row_index"]
            df[_intra_dedup] = df[_intra_dedup].fillna("")
            df.drop_duplicates(subset=_intra_dedup, keep="first", inplace=True)

            # Uncertain-Trades ausfiltern (readable == False)
            if "_readable" in df.columns:
                uncertain_mask = df["_readable"].eq(False)
                uncertain_df = df[uncertain_mask].copy()
                if not uncertain_df.empty:
                    uncertain_df["source"] = "extractor"
                    uncertain_df["rejection_reason"] = "low_confidence"
                    uncertain_df.drop(
                        columns=["_page_num", "_row_index", "_readable"],
                        errors="ignore",
                        inplace=True,
                    )
                    self._uncertain_rows.extend(
                        uncertain_df.to_dict("records")
                    )
                    logger.info(
                        f"  {len(uncertain_df)} unsichere Trades -> "
                        f"ocr_uncertain.csv"
                    )
                df = df[~uncertain_mask].copy()

            df.drop(
                columns=["_page_num", "_row_index", "_readable"],
                errors="ignore",
                inplace=True,
            )
            if "csv_upload_time" not in df.columns:
                df["csv_upload_time"] = ""
            df = df[CSV_COLUMNS]
            logger.info(
                f"=== Gesamt: {len(df)} Trades "
                f"({before - len(df)} OCR-Duplikate entfernt) ==="
            )

        processed_ids = [str(r.get("doc_id", "")) for r in rows]
        if not dry_run and processed_ids:
            self._save_done_doc_ids(processed_ids)

        if not dry_run and not df.empty:
            self.save(df)

        # Unsichere Trades speichern
        if not dry_run and self._uncertain_rows:
            self._save_uncertain_trades()

        return df

    def save(self, df: pd.DataFrame) -> str:
        if df.empty:
            logger.warning("Keine Trades zum Speichern.")
            return self._output_path

        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)
        upload_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df["csv_upload_time"] = upload_time

        if os.path.isfile(self._output_path):
            existing = pd.read_csv(self._output_path)
            for col in DEDUP_SUBSET:
                if col not in existing.columns:
                    existing[col] = ""
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

    def _save_uncertain_trades(self):
        """Speichert unsichere Trades (readable=false) in logs/ocr_uncertain.csv."""
        if not self._uncertain_rows:
            return

        new_df = pd.DataFrame(self._uncertain_rows)
        os.makedirs(os.path.dirname(self._uncertain_path), exist_ok=True)

        if os.path.isfile(self._uncertain_path):
            existing = pd.read_csv(self._uncertain_path)
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined.to_csv(self._uncertain_path, index=False)
        logger.info(
            f"Unsichere Trades gespeichert >> {self._uncertain_path} "
            f"({len(new_df)} neue, {len(combined)} gesamt)"
        )

    # ------------------------------------------------------------------
    # PDF Download & Image Extraction
    # ------------------------------------------------------------------

    def _process_pdf(
        self,
        doc_id: str,
        year: int,
        member_name: str,
        disclosure_date: str,
        ptr_link: str,
    ) -> list[dict]:
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
                if USE_GRID_DETECTION:
                    trades = self._extract_with_grid(
                        image, member_name, disclosure_date, ptr_link, page_num
                    )
                else:
                    trades = self._extract_fallback(
                        image, member_name, disclosure_date, ptr_link, page_num
                    )
                all_trades.extend(trades)
            except Exception:
                logger.exception(f"  Fehler auf Seite {page_num} (DocID={doc_id})")

            time.sleep(settings.PDF_EXTRACTOR_PAGE_PAUSE_SECONDS)

        return all_trades

    def _download_pdf(self, doc_id: str, year: int) -> bytes:
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
        images = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                pil_image = page.to_image(resolution=PDF_EXTRACTOR_DPI).original

                if page.width < page.height:
                    pil_image = pil_image.rotate(90, expand=True)
                    logger.debug(
                        f"  Seite rotiert (Portrait->Landscape): "
                        f"{page.width}x{page.height}"
                    )

                images.append(pil_image)

        return images


    # ------------------------------------------------------------------
    # Grid-Erkennung via OpenCV
    # ------------------------------------------------------------------

    def _detect_table_grid(self, gray: np.ndarray):
        """
        Erkennt Tabellenstruktur via OpenCV (Linien-Erkennung).

        Returns:
            dict mit 'rows' (list of (y1, y2)) und 'cols' (list of (x1, x2))
            oder None wenn keine Tabelle erkannt.
        """
        h, w = gray.shape[:2]

        # Binaerbild: invertiert (Text/Linien = weiss)
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

        # Horizontale Linien erkennen
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 8, 40), 1))
        h_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel, iterations=2)

        # Vertikale Linien erkennen
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 8, 40)))
        v_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel, iterations=2)

        # Y-Positionen der horizontalen Linien extrahieren
        h_positions = self._extract_line_positions(h_lines, axis="horizontal")
        v_positions = self._extract_line_positions(v_lines, axis="vertical")

        if len(h_positions) < 3 or len(v_positions) < 3:
            return None

        # Zeilen: Bereiche zwischen aufeinanderfolgenden H-Linien
        rows = []
        for i in range(len(h_positions) - 1):
            y1, y2 = h_positions[i], h_positions[i + 1]
            if y2 - y1 > 10:  # Min. Zeilenhoehe
                rows.append((y1, y2))

        # Spalten: Bereiche zwischen aufeinanderfolgenden V-Linien
        cols = []
        for i in range(len(v_positions) - 1):
            x1, x2 = v_positions[i], v_positions[i + 1]
            if x2 - x1 > 5:  # Min. Spaltenbreite
                cols.append((x1, x2))

        if len(rows) < 2 or len(cols) < 3:
            return None

        return {"rows": rows, "cols": cols}

    def _extract_line_positions(self, line_mask: np.ndarray, axis: str) -> list[int]:
        """Extrahiert sortierte Positionen aus einer Linien-Maske."""
        if axis == "horizontal":
            projection = np.sum(line_mask, axis=1)  # Summiere pro Zeile
        else:
            projection = np.sum(line_mask, axis=0)  # Summiere pro Spalte

        # Positionen mit genug Pixeln = Linien
        threshold = projection.max() * 0.3 if projection.max() > 0 else 0
        line_indices = np.where(projection > threshold)[0]

        if len(line_indices) == 0:
            return []

        # Cluster naher Positionen (Linien koennen 2-3 Pixel breit sein)
        positions = []
        cluster_start = line_indices[0]
        for i in range(1, len(line_indices)):
            if line_indices[i] - line_indices[i - 1] > 5:
                positions.append((cluster_start + line_indices[i - 1]) // 2)
                cluster_start = line_indices[i]
        positions.append((cluster_start + line_indices[-1]) // 2)

        return positions

    def _classify_columns(self, grid: dict):
        """
        Klassifiziert Spalten in Text-Spalten und Amount-Spalten (A-K).

        PTR-Tabellen haben immer genau 11 Amount-Spalten (A-K) rechts.
        Bei fehlenden vertikalen Linien koennen Amount-Spalten zusammenwachsen
        (z.B. 19 statt 20 Spalten). In dem Fall werden zu breite Spalten im
        Amount-Bereich aufgeteilt.

        Returns:
            (text_col_indices, amount_col_indices) - Listen von Spaltenindizes
        """
        cols = grid["cols"]
        n_cols = len(cols)

        if n_cols < 12:
            return list(range(n_cols)), []

        # Erwartete Text-Spalten: Owner, Asset, TxType-Checkboxen, Dates etc.
        # Typisch 7-9 Text-Spalten, dann 11 Amount-Spalten = 18-20 gesamt.
        # Wir nehmen die letzten Spalten als Amount-Bereich.

        # Ideale Anzahl Text-Spalten schaetzen: n_cols - 11
        n_text = max(n_cols - 11, 3)  # Mindestens 3 Text-Spalten

        # Amount-Bereich: alles ab n_text
        amount_zone_cols = list(range(n_text, n_cols))

        # Wenn wir genau 11 Amount-Spalten haben → perfekt
        if len(amount_zone_cols) == 11:
            return list(range(n_text)), amount_zone_cols

        # Wenn weniger als 11: Einige Spalten sind zusammengewachsen.
        # Den Amount-Bereich in genau 11 gleich breite Unterabschnitte aufteilen.
        if len(amount_zone_cols) < 11:
            amount_x1 = cols[amount_zone_cols[0]][0]
            amount_x2 = cols[amount_zone_cols[-1]][1]
            total_width = amount_x2 - amount_x1
            step = total_width / 11

            # Ersetze die Amount-Spalten im Grid durch 11 gleichmaessige
            new_amount_cols = []
            for i in range(11):
                ax1 = int(amount_x1 + i * step)
                ax2 = int(amount_x1 + (i + 1) * step)
                new_amount_cols.append((ax1, ax2))

            # Grid aktualisieren: Text-Spalten behalten, Amount ersetzen
            grid["cols"] = [cols[i] for i in range(n_text)] + new_amount_cols
            amount_indices = list(range(n_text, n_text + 11))
            return list(range(n_text)), amount_indices

        # Mehr als 11 → nehme die letzten 11
        amount_cols = list(range(n_cols - 11, n_cols))
        text_cols = list(range(n_cols - 11))
        return text_cols, amount_cols

    # ------------------------------------------------------------------
    # Pixel-Density Amount Detection
    # ------------------------------------------------------------------

    def _detect_amount_for_row(
        self,
        gray_image: np.ndarray,
        row_bbox: tuple[int, int],
        grid_cols: list[tuple[int, int]],
        amount_col_indices: list[int],
    ) -> str:
        """
        Erkennt den Amount-Code (A-K) fuer eine Tabellenzeile via Pixel-Density.

        Args:
            gray_image: Preprocessed Graustufen-Bild (gesamte Seite)
            row_bbox: (y1, y2) der Zeile
            grid_cols: Liste von (x1, x2) fuer alle Spalten
            amount_col_indices: Indizes der Amount-Spalten

        Returns:
            Amount-Range-String (z.B. "$1,001 - $15,000") oder ""
        """
        if not amount_col_indices:
            return ""

        y1, y2 = row_bbox
        pixel_counts = []

        for col_idx in amount_col_indices:
            if col_idx >= len(grid_cols):
                pixel_counts.append(0)
                continue

            x1, x2 = grid_cols[col_idx]

            # Rand abschneiden (Gitterlinien nicht mitzaehlen)
            margin = 3
            cx1 = min(x1 + margin, x2)
            cy1 = min(y1 + margin, y2)
            cx2 = max(x2 - margin, cx1)
            cy2 = max(y2 - margin, cy1)

            cell_crop = gray_image[cy1:cy2, cx1:cx2]
            if cell_crop.size == 0:
                pixel_counts.append(0)
                continue

            # Threshold: dunkle Pixel zaehlen
            _, binary = cv2.threshold(cell_crop, 150, 255, cv2.THRESH_BINARY_INV)
            dark_pixels = cv2.countNonZero(binary)
            pixel_counts.append(dark_pixels)

        if not pixel_counts or max(pixel_counts) < AMOUNT_PIXEL_THRESHOLD:
            return ""

        # Hoechster Wert = markierte Spalte
        max_idx = pixel_counts.index(max(pixel_counts))

        # Index in Amount-Spalten -> Buchstabe A-K
        if max_idx < len(AMOUNT_LETTERS):
            letter = AMOUNT_LETTERS[max_idx]
            return AMOUNT_RANGES.get(letter, "")

        return ""

    # ------------------------------------------------------------------
    # Grid-basierte Extraktion (Hauptpfad)
    # ------------------------------------------------------------------

    def _extract_with_grid(
        self,
        image: Image.Image,
        member_name: str,
        disclosure_date: str,
        ptr_link: str,
        page_num: int,
    ) -> list[dict]:
        """Extrahiert Trades via Grid-Erkennung + Pixel-Density + API-Batches."""

        # 1. Roh-Graustufenbild fuer Grid-Erkennung (KEIN Deskew/CLAHE —
        #    warpAffine verschmiert duenne Gitterlinien und zerstoert die Erkennung)
        img_array_raw = np.array(image)
        if len(img_array_raw.shape) == 3:
            gray = cv2.cvtColor(img_array_raw, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array_raw

        # 2. Grid erkennen (auf Roh-Graustufen)
        grid = self._detect_table_grid(gray)
        if grid is None:
            logger.info(
                f"  Seite {page_num}: Keine Tabelle erkannt -> FALLBACK (ganzes Bild)"
            )
            return self._extract_fallback(
                image, member_name, disclosure_date, ptr_link, page_num
            )

        rows = grid["rows"]
        cols = grid["cols"]

        # 3. Spalten klassifizieren
        text_cols, amount_cols = self._classify_columns(grid)

        logger.info(
            f"  Seite {page_num}: Tabelle erkannt ({len(rows)} Zeilen, "
            f"{len(cols)} Spalten, davon {len(amount_cols)} Amount)"
        )

        if not amount_cols:
            logger.info(
                f"  Seite {page_num}: Keine Amount-Spalten erkannt -> FALLBACK"
            )
            return self._extract_fallback(
                image, member_name, disclosure_date, ptr_link, page_num
            )

        # 4. Amount fuer jede Zeile via Pixel-Density
        amounts_by_row: dict[int, str] = {}
        for row_idx, row_bbox in enumerate(rows):
            amount = self._detect_amount_for_row(gray, row_bbox, cols, amount_cols)
            amounts_by_row[row_idx] = amount

        # 5. Text-Spalten in Zeilen-Batches an Vision API senden
        img_array = np.array(image)
        trades: list[dict] = []

        # Text-Bereich X-Grenzen (links von den Amount-Spalten)
        text_x1 = cols[text_cols[0]][0] if text_cols else 0
        text_x2 = cols[text_cols[-1]][1] if text_cols else img_array.shape[1]

        # In Batches aufteilen
        for batch_start in range(0, len(rows), ROW_BATCH_SIZE):
            batch_end = min(batch_start + ROW_BATCH_SIZE, len(rows))
            batch_rows = rows[batch_start:batch_end]

            # Gesamt-Bounding-Box fuer den Batch (nur Text-Spalten)
            by1 = batch_rows[0][0]
            by2 = batch_rows[-1][1]

            # Padding
            padding = 10
            bx1 = max(0, text_x1 - padding)
            by1 = max(0, by1 - padding)
            bx2 = min(img_array.shape[1], text_x2 + padding)
            by2 = min(img_array.shape[0], by2 + padding)

            # Crop
            batch_crop = img_array[by1:by2, bx1:bx2]
            if batch_crop.size == 0:
                continue

            batch_image = Image.fromarray(batch_crop)
            n_rows_in_batch = batch_end - batch_start

            # Vision API aufrufen
            try:
                api_trades = self._call_vision_api_batch(batch_image, n_rows_in_batch)
            except Exception:
                logger.exception(
                    f"  API-Fehler Seite {page_num}, Batch {batch_start}-{batch_end}"
                )
                api_trades = []

            # Trades zusammenfuehren: API-Text + CV-Amount
            for api_idx, tx in enumerate(api_trades):
                global_row_idx = batch_start + api_idx
                amount = amounts_by_row.get(global_row_idx, "")

                trade = self._build_trade_dict(
                    tx=tx,
                    amount=amount,
                    member_name=member_name,
                    disclosure_date=disclosure_date,
                    ptr_link=ptr_link,
                    page_num=page_num,
                    row_idx=global_row_idx + 1,
                )
                if trade:
                    trades.append(trade)

        detected_amounts = sum(1 for a in amounts_by_row.values() if a)
        logger.info(
            f"  Seite {page_num}: {len(trades)} Trades, "
            f"Amounts erkannt: {detected_amounts}/{len(rows)}"
        )
        return trades

    # ------------------------------------------------------------------
    # Vision API
    # ------------------------------------------------------------------

    def _init_anthropic(self):
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY Umgebungsvariable nicht gesetzt."
                )
            self._anthropic = anthropic.Anthropic(api_key=api_key)
            logger.info(f"Anthropic SDK initialisiert (Modell: {self.model})")
        except ImportError:
            raise ImportError(
                "anthropic Paket nicht installiert. "
                "Bitte 'pip install anthropic' ausfuehren."
            )

    def _image_to_base64(self, image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        return base64.standard_b64encode(buf.getvalue()).decode("utf-8")

    def _call_vision_api_batch(
        self, batch_image: Image.Image, n_rows: int
    ) -> list[dict]:
        """Sendet ein Zeilen-Batch-Bild an die Vision API."""
        image_b64 = self._image_to_base64(batch_image)
        prompt = ROW_BATCH_USER_PROMPT.format(n_rows=n_rows)

        for attempt in range(1, settings.SCRAPER_RETRY_ATTEMPTS + 1):
            try:
                response = self._anthropic.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=ROW_BATCH_SYSTEM_PROMPT,
                    messages=[{
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
                            {"type": "text", "text": prompt},
                        ],
                    }],
                )
                raw_text = response.content[0].text.strip()
                return self._parse_batch_response(raw_text)

            except Exception as e:
                logger.warning(
                    f"  API-Fehler Batch, Versuch "
                    f"{attempt}/{settings.SCRAPER_RETRY_ATTEMPTS}: {e}"
                )
                if attempt < settings.SCRAPER_RETRY_ATTEMPTS:
                    time.sleep(settings.SCRAPER_RETRY_DELAY_SECONDS)
                else:
                    raise

        return []

    def _parse_batch_response(self, raw_text: str) -> list[dict]:
        """Parst JSON-Antwort eines Zeilen-Batches."""
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
        cleaned = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"  JSON-Parse-Fehler (Batch): {e}\n  Raw: {raw_text[:300]}")
            return []

        return data.get("transactions", [])

    # ------------------------------------------------------------------
    # Fallback: ganzes Seitenbild (wie v1)
    # ------------------------------------------------------------------

    def _extract_fallback(
        self,
        image: Image.Image,
        member_name: str,
        disclosure_date: str,
        ptr_link: str,
        page_num: int,
    ) -> list[dict]:
        """Fallback: ganzes Seitenbild an Vision API (wie v1)."""
        image_b64 = self._image_to_base64(image)

        for attempt in range(1, settings.SCRAPER_RETRY_ATTEMPTS + 1):
            try:
                response = self._anthropic.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=FALLBACK_SYSTEM_PROMPT,
                    messages=[{
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
                            {"type": "text", "text": FALLBACK_USER_PROMPT},
                        ],
                    }],
                )
                raw_text = response.content[0].text.strip()
                return self._parse_fallback_response(
                    raw_text, member_name, disclosure_date, ptr_link, page_num
                )

            except Exception as e:
                logger.warning(
                    f"  API-Fehler Seite {page_num} (Fallback), Versuch "
                    f"{attempt}/{settings.SCRAPER_RETRY_ATTEMPTS}: {e}"
                )
                if attempt < settings.SCRAPER_RETRY_ATTEMPTS:
                    time.sleep(settings.SCRAPER_RETRY_DELAY_SECONDS)
                else:
                    raise

        return []

    def _parse_fallback_response(
        self,
        raw_text: str,
        member_name: str,
        disclosure_date: str,
        ptr_link: str,
        page_num: int,
    ) -> list[dict]:
        """Parst Fallback-Antwort (identisch mit v1-Logik)."""
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
        cleaned = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"  JSON-Parse-Fehler: {e}\n  Raw: {raw_text[:300]}")
            return []

        if not data.get("is_transaction_page", True):
            logger.debug("  Seite ist kein PTR-Formular.")
            return []

        api_name = str(data.get("member_name", "")).strip()
        api_notif_date = str(data.get("notification_date", "")).strip()
        transactions = data.get("transactions", [])
        trades: list[dict] = []

        for idx, tx in enumerate(transactions):
            if not isinstance(tx, dict):
                continue

            amount = str(tx.get("amount", "")).strip()
            amount_code = str(tx.get("amount_code", "")).strip().upper()
            if not amount and amount_code in AMOUNT_RANGES:
                amount = AMOUNT_RANGES[amount_code]

            trade = self._build_trade_dict(
                tx=tx,
                amount=amount,
                member_name=member_name if member_name != "UNKNOWN" else api_name,
                disclosure_date=disclosure_date or api_notif_date,
                ptr_link=ptr_link,
                page_num=page_num,
                row_idx=int(tx.get("row_index", idx + 1)),
            )
            if trade:
                trades.append(trade)

        return trades

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _build_trade_dict(
        self,
        tx: dict,
        amount: str,
        member_name: str,
        disclosure_date: str,
        ptr_link: str,
        page_num: int,
        row_idx: int,
    ) -> Optional[dict]:
        """Baut ein Trade-Dict aus API-Antwort + CV-Amount."""
        tx_type = str(tx.get("transaction_type", "")).strip()
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
            return None

        tx_date = self._normalize_date(str(tx.get("transaction_date", "")).strip())
        tx_notif_date = self._normalize_date(
            str(tx.get("notification_date", "")).strip()
        )

        raw_owner = str(tx.get("owner", "")).strip()
        owner = OWNER_LONG.get(raw_owner, raw_owner)
        if not owner:
            owner = "Self"

        asset_name = str(tx.get("asset_name", "")).strip()
        comment = str(tx.get("comment", "")).strip()

        # Subholding-Header ueberspringen
        if not tx_date and not amount:
            return None

        name = member_name.strip() if member_name else "UNKNOWN"
        disc_date = disclosure_date.strip() if disclosure_date else tx_notif_date

        if not amount:
            logger.warning(
                f"  Kein Amount Seite {page_num} Zeile {row_idx}: "
                f"{asset_name[:40]!r}"
            )

        return {
            "name": name,
            "ticker": "",
            "asset_name": asset_name,
            "asset_type": "",
            "transaction_type": tx_type,
            "amount": amount,
            "transaction_date": tx_date,
            "owner": owner,
            "subholding": "",
            "comment": comment,
            "disclosure_date": disc_date,
            "ptr_link": ptr_link,
            "csv_upload_time": "",
            "_page_num": page_num,
            "_row_index": row_idx,
            "_readable": tx.get("readable", True),
        }

    def _normalize_date(self, date_str: str) -> str:
        if not date_str:
            return ""
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", date_str)
        if m:
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2})$", date_str)
        if m:
            yy = int(m.group(3))
            full_year = 2000 + yy if yy < 50 else 1900 + yy
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{full_year}"
        m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{2,4})$", date_str)
        if m:
            yy = int(m.group(3))
            if yy < 100:
                yy = 2000 + yy if yy < 50 else 1900 + yy
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{yy}"
        return date_str


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _setup_logging():
    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "house_pdf_extractor_v2.log")

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
        description="House PDF Extractor v2 - Grid Detection + Pixel-Density + Vision API"
    )
    parser.add_argument("--input-csv", help="Pfad zur Input-CSV")
    parser.add_argument("--doc-id", type=str, help="Einzelne DocID (erfordert --year)")
    parser.add_argument("--year", type=int, help="Jahr fuer --doc-id")
    parser.add_argument("--model", default=None, help="Anthropic-Modell")
    parser.add_argument("--dry-run", action="store_true", help="Nicht speichern")
    parser.add_argument("--no-delta", action="store_true", help="Alle PDFs verarbeiten")
    args = parser.parse_args()

    extractor = HousePdfExtractorV2(model=args.model)
    df = extractor.run(
        input_csv=args.input_csv,
        single_doc_id=args.doc_id,
        year=args.year,
        dry_run=args.dry_run,
        no_delta=args.no_delta,
    )

    n_uncertain = len(extractor._uncertain_rows)
    print(f"\n{'=' * 55}")
    print(f"  Neu extrahiert:  {len(df):>8,} Trades")
    if n_uncertain > 0:
        print(f"  Unsicher (skip): {n_uncertain:>8,} Trades -> ocr_uncertain.csv")

    if not args.dry_run:
        output_path = extractor._output_path
        if os.path.isfile(output_path):
            size_bytes = os.path.getsize(output_path)
            size_mb = size_bytes / (1024 * 1024)
            with open(output_path, encoding="utf-8", errors="replace") as f:
                total_rows = sum(1 for _ in f) - 1
            print(f"  CSV gesamt:      {total_rows:>8,} Zeilen  ({size_mb:.1f} MB)")
            print(f"  Datei:           {output_path}")
    else:
        print("  (dry-run: keine Datei geschrieben)")
    print(f"{'=' * 55}\n")

    if not df.empty:
        print(
            df[["name", "asset_name", "transaction_type", "amount", "transaction_date"]]
            .to_string(index=False)
        )


if __name__ == "__main__":
    main()
