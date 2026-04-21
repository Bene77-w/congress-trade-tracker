"""
Congress Trade Tracker - Senate PDF Extractor
=============================================
Extraktion von Senate PTR Paper Forms via Anthropic Vision API (Claude Haiku).

Workflow:
    1. Liest senate_pdf_skipped.csv (mit PTR Paper Links)
    2. Oeffnet die Print-URL via Playwright (Session-Cookie fuer Auth)
    3. Extrahiert GIF-URLs aus dem DOM
    4. Laedt GIF-Bilder herunter (requests, da oeffentlich zugaenglich)
    5. Sendet jedes Bild an Anthropic Vision API fuer OCR + Extraktion
    6. Speichert Ergebnisse in senate_trades.csv (Append + Dedup)

Verwendung:
    # Alle PDFs aus senate_pdf_skipped.csv verarbeiten:
    python scrapers/senate_pdf_extractor.py

    # Nur eine bestimmte PTR-URL:
    python scrapers/senate_pdf_extractor.py --url https://efdsearch.senate.gov/search/view/paper/068274e1-.../

    # Trockenlauf (kein Speichern):
    python scrapers/senate_pdf_extractor.py --dry-run

    # Anderes Modell:
    python scrapers/senate_pdf_extractor.py --model claude-opus-4-6
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
import requests
from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
BASE_URL = "https://efdsearch.senate.gov/search/"
PRINT_URL_TEMPLATE = "https://efdsearch.senate.gov/search/print/paper/{uuid}/"
EFD_MEDIA_HOST = "efd-media-public.senate.gov"

CSV_COLUMNS = [
    "name",
    "ticker",
    "asset_name",
    "asset_type",
    "transaction_type",
    "amount",
    "transaction_date",
    "owner",
    "comment",
    "disclosure_date",
    "ptr_link",
]

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
}

# Dedup-Spalten: alle ausser disclosure_date (inkl. asset_name!)
DEDUP_SUBSET = [c for c in CSV_COLUMNS if c != "disclosure_date"]

EXTRACTION_SYSTEM_PROMPT = """You are a precise data extractor for US Senate Periodic Transaction Report (PTR) forms.
These forms show financial transactions by US Senators and must be extracted with perfect accuracy.
Always respond with valid JSON only. No explanations, no markdown fences."""

EXTRACTION_USER_PROMPT = """Extract transactions from this Senate PTR form image.

The form columns are:
- Identification of Assets: [footnote_marker][owner_code] Asset Name
  - owner_code: (S)=Self, (SP)=Spouse, (DC)=Dependent Child, (J)=Joint
  - ticker: appears as e.g. "(STOCK) (AAPL)" after the name
- Transaction Type: checkmark (X) in "Purchase", "Sale", or "Exchange" column
- Transaction Date: MM/DD/YY or MM/DD/YYYY
- Amount: checkmark (X) in column A-J

CRITICAL RULE - TWO TYPES OF ROWS:

Type A - PARENT FUND ROW: asset name ends with a colon ":"
  - The politician DIRECTLY holds a stake in this fund
  - These rows have NO checkmarks - transaction details come from the first child row below
  - Set is_direct=true, asset_type="FUND", ticker=""
  - asset_name = fund name WITHOUT the trailing colon
  - Use transaction_type/date/amount from the FIRST child row beneath this parent

Type B - CHILD ROW: indented below a parent fund row, has its own checkmarks
  - These are investments made BY the parent fund, NOT by the politician
  - Set is_direct=false
  - Still extract all fields normally

Also:
- SKIP the printed "Example:" rows at the top of PTR pages (sample form instructions like
  "IBM Corp. (stock) NYSE") — these appear BEFORE the numbered rows and are NOT real trades
- Skip completely empty rows
- row_index: Assign each transaction a sequential 1-based row number from its physical
  position on the form. If an OCR artifact causes you to read the same form row twice,
  return it only ONCE. If a filer genuinely filled in multiple rows that look identical
  (same asset, same date, same amount on different lines), return ALL of them — each
  with its distinct row_index.
- Normalize dates to MM/DD/YYYY
- Transaction_type title case: "Purchase", "Sale", or "Exchange"
- Extract footnotes; put referenced footnote text in "comment"

Return JSON:
{
  "is_ptr_page": true/false,
  "transactions": [
    {
      "row_index": 1,
      "is_direct": true/false,
      "owner": "(S)" or "(SP)" or "(DC)" or "(J)" or "",
      "asset_name": "Fund or Company Name",
      "asset_type": "STOCK" or "FUND" or "Municipal Security" or "Bond" etc,
      "ticker": "AAPL" or "",
      "transaction_type": "Purchase",
      "transaction_date": "01/15/2026",
      "amount_code": "A",
      "amount": "$1,001 - $15,000",
      "comment": ""
    }
  ],
  "footnotes": {"1": "text", "*": "text"}
}

Set is_ptr_page=false for cover letters. Return empty transactions array if no rows.
"""


class SenatePdfExtractor:
    """
    Extrahiert Transaktionen aus Senate PTR Paper Forms via Anthropic Vision API.

    Die PTR Paper Forms werden als GIF-Bilder bereitgestellt. Der Extractor:
    - Nutzt Playwright fuer Session-Auth (EFD agreement) und GIF-URL-Extraktion
    - Laedt GIF-Bilder per requests (oeffentlich zugaenglich)
    - Sendet jedes Bild an Claude Vision fuer strukturierte Datenextraktion
    - Speichert in senate_trades.csv (Append + Dedup)
    """

    def __init__(
        self,
        headless: bool = True,
        model: str = "claude-haiku-4-5-20251001",
    ):
        """
        Args:
            headless: Playwright im Headless-Modus.
            model:    Anthropic-Modell fuer Vision-Extraktion.
        """
        self.headless = headless
        self.model = model
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._output_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "senate_trades.csv"
        )
        self._skipped_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "senate_pdf_skipped.csv"
        )
        self._done_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "senate_pdf_extractor_done.csv"
        )
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        self._anthropic = None  # lazy init

    # ------------------------------------------------------------------
    # Oeffentliche API
    # ------------------------------------------------------------------

    def _load_done_ptr_links(self) -> set:
        """Laedt bereits verarbeitete ptr_links aus der Done-CSV."""
        if not os.path.isfile(self._done_path):
            return set()
        df = pd.read_csv(self._done_path)
        return set(df["ptr_link"].astype(str))

    def _save_done_ptr_links(self, ptr_links: list[str]):
        """Haengt verarbeitete ptr_links an die Done-CSV an."""
        if not ptr_links:
            return
        new_df = pd.DataFrame({
            "ptr_link": ptr_links,
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        if os.path.isfile(self._done_path):
            existing = pd.read_csv(self._done_path)
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined.drop_duplicates(subset=["ptr_link"], keep="last", inplace=True)
        else:
            combined = new_df
        os.makedirs(os.path.dirname(self._done_path), exist_ok=True)
        combined.to_csv(self._done_path, index=False)
        logger.info(f"Done-CSV aktualisiert: {len(ptr_links)} Links → {self._done_path}")

    def run(
        self,
        input_csv: Optional[str] = None,
        single_url: Optional[str] = None,
        dry_run: bool = False,
        no_delta: bool = False,
    ) -> pd.DataFrame:
        """
        Haupteinstiegspunkt. Verarbeitet alle PDFs aus senate_pdf_skipped.csv
        oder eine einzelne URL.

        Args:
            input_csv:  Pfad zur Input-CSV (Standard: senate_pdf_skipped.csv)
            single_url: Verarbeite nur diese eine PTR-URL (fuer Tests)
            dry_run:    Wenn True, nur extrahieren, nicht speichern
            no_delta:   Wenn True, alle PDFs verarbeiten (Delta-Check ueberspringen)

        Returns:
            pd.DataFrame mit allen extrahierten Trades.
        """
        if single_url:
            rows = [{"name": "UNKNOWN", "disclosure_date": "", "ptr_link": single_url}]
        else:
            csv_path = input_csv or self._skipped_path
            if not os.path.isfile(csv_path):
                logger.error(f"Input-CSV nicht gefunden: {csv_path}")
                return pd.DataFrame(columns=CSV_COLUMNS)
            df_input = pd.read_csv(csv_path)

            # Delta-Check: bereits verarbeitete ptr_links ueberspringen
            if not no_delta:
                done_links = self._load_done_ptr_links()
                if done_links:
                    before = len(df_input)
                    df_input = df_input[~df_input["ptr_link"].astype(str).isin(done_links)]
                    skipped = before - len(df_input)
                    if skipped > 0:
                        logger.info(f"Delta-Check: {skipped} bereits verarbeitete PDFs uebersprungen")

            rows = df_input.to_dict("records")

        if not rows:
            logger.info("Keine PDFs zum Verarbeiten gefunden.")
            return pd.DataFrame(columns=CSV_COLUMNS)

        self._init_anthropic()
        logger.info(f"=== Senate PDF Extractor gestartet: {len(rows)} PDFs (Modell: {self.model}) ===")
        all_trades: list[dict] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            self._accept_agreement(page)

            for i, row in enumerate(rows, 1):
                ptr_link = row.get("ptr_link", "")
                senator_name = row.get("name", "UNKNOWN")
                disclosure_date = row.get("disclosure_date", "")

                logger.info(f"[{i}/{len(rows)}] Verarbeite: {senator_name} - {disclosure_date}")
                try:
                    trades = self._process_pdf(
                        page, ptr_link, senator_name, disclosure_date
                    )
                    all_trades.extend(trades)
                    logger.info(f"  >> {len(trades)} Trades extrahiert aus {ptr_link}")
                except Exception:
                    logger.exception(f"  >> Fehler bei {ptr_link}")

                time.sleep(settings.SCRAPER_RATE_LIMIT_SECONDS)

            browser.close()

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
            df = df[CSV_COLUMNS]
            logger.info(
                f"=== Gesamt: {len(df)} Trades ({before - len(df)} OCR-Duplikate entfernt) ==="
            )

        # Verarbeitete ptr_links tracken (auch bei 0 Trades)
        processed_links = [str(r.get("ptr_link", "")) for r in rows]
        if not dry_run and processed_links:
            self._save_done_ptr_links(processed_links)

        if not dry_run and not df.empty:
            self.save(df)

        return df

    def save(self, df: pd.DataFrame) -> str:
        """
        Speichert Trades in senate_trades.csv (Append + Dedup).

        Returns:
            Pfad zur gespeicherten Datei.
        """
        if df.empty:
            logger.warning("Keine Trades zum Speichern.")
            return self._output_path

        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)

        if os.path.isfile(self._output_path):
            existing = pd.read_csv(self._output_path)
            for col in DEDUP_SUBSET:
                if col not in existing.columns:
                    existing[col] = ""
            # Anti-Join: nur Zeilen anfügen, die noch nicht in der CSV sind.
            # Kein drop_duplicates auf combined — intra-Batch-Dedup wurde bereits
            # in run() erledigt, legitime Mehrfachtrades würden sonst entfernt.
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
    # Interne Methoden - Playwright / HTTP
    # ------------------------------------------------------------------

    def _accept_agreement(self, page):
        """Laedt EFD-Startseite und akzeptiert Nutzungsbedingungen."""
        logger.info(f"Lade Startseite: {BASE_URL}")
        page.goto(BASE_URL, wait_until="networkidle")

        checkbox = page.locator("input#agree_statement")
        if checkbox.count() > 0:
            checkbox.click()
            page.locator("button.btn-primary").first.click()
            page.wait_for_url("**/search/**", timeout=30_000)
            page.wait_for_load_state("networkidle")
            logger.info("Nutzungsbedingungen akzeptiert.")
        else:
            logger.info("Keine Nutzungsbedingungen-Huerde gefunden.")

    def _process_pdf(
        self,
        page,
        ptr_link: str,
        senator_name: str,
        disclosure_date: str,
    ) -> list[dict]:
        """Verarbeitet ein einzelnes PDF: GIF-URLs extrahieren, Vision-API, Trades."""
        uuid = self._extract_uuid(ptr_link)
        if not uuid:
            logger.warning(f"Kein UUID in URL gefunden: {ptr_link}")
            return []

        gif_urls = self._get_gif_urls(page, uuid)
        if not gif_urls:
            logger.warning(f"Keine GIF-URLs gefunden fuer UUID {uuid}")
            return []

        logger.info(f"  {len(gif_urls)} Seiten gefunden.")
        all_trades: list[dict] = []

        # Seite 1 = Cover Letter -> ueberspringen
        for page_num, gif_url in enumerate(gif_urls, start=1):
            if page_num == 1:
                logger.debug("  Seite 1 (Cover Letter) uebersprungen.")
                continue

            logger.debug(f"  Verarbeite Seite {page_num}: {gif_url}")
            try:
                image = self._download_gif(gif_url)
                trades = self._extract_from_image(
                    image, senator_name, disclosure_date, ptr_link, page_num
                )
                all_trades.extend(trades)
            except Exception:
                logger.exception(f"  Fehler auf Seite {page_num}: {gif_url}")

            time.sleep(settings.PDF_EXTRACTOR_PAGE_PAUSE_SECONDS)

        return all_trades

    def _extract_uuid(self, ptr_link: str) -> Optional[str]:
        """Extrahiert UUID aus einer PTR-View-URL."""
        match = re.search(
            r"/paper/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
            ptr_link,
        )
        return match.group(1) if match else None

    def _get_gif_urls(self, page, uuid: str) -> list[str]:
        """
        Oeffnet die Print-URL und extrahiert alle GIF-URLs aus dem DOM.
        Die Print-URL rendert alle Seiten als <img>-Tags auf einer HTML-Seite.
        """
        print_url = PRINT_URL_TEMPLATE.format(uuid=uuid)
        logger.debug(f"  Navigiere zu Print-URL: {print_url}")

        page.goto(print_url, wait_until="networkidle", timeout=30_000)

        gif_urls = page.evaluate("""
            () => Array.from(document.querySelectorAll('img'))
                .map(img => img.src)
                .filter(src => src.includes('efd-media-public'))
        """)

        return gif_urls or []

    def _download_gif(self, gif_url: str) -> Image.Image:
        """Laedt ein GIF von efd-media-public herunter und gibt ein PIL Image zurueck."""
        resp = self._http.get(gif_url, timeout=30)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")

    # ------------------------------------------------------------------
    # Interne Methoden - Vision API
    # ------------------------------------------------------------------

    def _init_anthropic(self):
        """Initialisiert Anthropic Client."""
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
        senator_name: str,
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
                    raw_text, senator_name, disclosure_date, ptr_link, page_num
                )

            except Exception as e:
                logger.warning(
                    f"  API-Fehler Seite {page_num}, Versuch {attempt}/{settings.SCRAPER_RETRY_ATTEMPTS}: {e}"
                )
                if attempt < settings.SCRAPER_RETRY_ATTEMPTS:
                    time.sleep(settings.SCRAPER_RETRY_DELAY_SECONDS)
                else:
                    raise

        return []

    def _parse_api_response(
        self,
        raw_text: str,
        senator_name: str,
        disclosure_date: str,
        ptr_link: str,
        page_num: int = 1,
    ) -> list[dict]:
        """Parst die JSON-Antwort der Vision API und normalisiert auf CSV_COLUMNS."""
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"  JSON-Parse-Fehler: {e}\n  Raw: {raw_text[:300]}")
            return []

        if not data.get("is_ptr_page", True):
            logger.debug("  Seite ist kein PTR-Formular (Cover Letter oder leer).")
            return []

        transactions: list = data.get("transactions", [])
        trades: list[dict] = []

        for idx, tx in enumerate(transactions):
            if not isinstance(tx, dict):
                continue

            # Child-Zeilen (Investitionen des Fonds, nicht des Politikers) ueberspringen
            if not tx.get("is_direct", True):
                continue

            tx_type = str(tx.get("transaction_type", "")).strip().title()
            if tx_type not in ("Purchase", "Sale", "Exchange"):
                logger.debug(f"  Unbekannter transaction_type: {tx_type!r} - uebersprungen.")
                continue

            amount = ""
            amount_code = str(tx.get("amount_code", "")).strip().upper()
            if amount_code in AMOUNT_RANGES:
                amount = AMOUNT_RANGES[amount_code]
            elif tx.get("amount"):
                amount = str(tx["amount"]).strip()

            tx_date = self._normalize_date(str(tx.get("transaction_date", "")).strip())
            owner = str(tx.get("owner", "")).strip()
            asset_name = str(tx.get("asset_name", "")).strip()
            asset_type = str(tx.get("asset_type", "")).strip()
            ticker = str(tx.get("ticker", "")).strip()
            comment = str(tx.get("comment", "")).strip()
            name = senator_name.strip().upper() if senator_name else "UNKNOWN"

            trades.append(
                {
                    "name": name,
                    "ticker": ticker if ticker else "",
                    "asset_name": asset_name,
                    "asset_type": asset_type,
                    "transaction_type": tx_type,
                    "amount": amount,
                    "transaction_date": tx_date,
                    "owner": owner,
                    "comment": comment,
                    "disclosure_date": disclosure_date,
                    "ptr_link": ptr_link,
                    "_page_num": page_num,
                    "_row_index": int(tx.get("row_index", idx + 1)),
                }
            )

        return trades

    def _normalize_date(self, date_str: str) -> str:
        """Normalisiert Datum auf MM/DD/YYYY."""
        if not date_str:
            return ""
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", date_str)
        if m:
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2})$", date_str)
        if m:
            year = int(m.group(3))
            full_year = 2000 + year if year < 50 else 1900 + year
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{full_year}"
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
    log_file = os.path.join(log_dir, "senate_pdf_extractor.log")

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
        description="Senate PDF Extractor - OCR via Claude Vision API"
    )
    parser.add_argument(
        "--input-csv",
        help="Pfad zur Input-CSV (Standard: data/raw/senate_pdf_skipped.csv)",
    )
    parser.add_argument(
        "--url",
        help="Einzelne PTR-URL verarbeiten (fuer Tests)",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Anthropic-Modell (Standard: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extrahieren ohne Speichern",
    )
    parser.add_argument(
        "--no-delta",
        action="store_true",
        help="Alle PDFs verarbeiten (Done-CSV ignorieren)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Playwright im Headless-Modus (Standard: True)",
    )
    args = parser.parse_args()

    extractor = SenatePdfExtractor(
        headless=args.headless,
        model=args.model,
    )
    df = extractor.run(
        input_csv=args.input_csv,
        single_url=args.url,
        dry_run=args.dry_run,
        no_delta=args.no_delta,
    )

    print(f"\n{'=' * 55}")
    print(f"  Neu extrahiert:  {len(df):>8,} Trades")

    if not args.dry_run:
        output_path = extractor._output_path
        if os.path.isfile(output_path):
            size_bytes = os.path.getsize(output_path)
            size_mb = size_bytes / (1024 * 1024)
            with open(output_path, encoding="utf-8", errors="replace") as f:
                total_rows = sum(1 for _ in f) - 1  # Kopfzeile abziehen
            print(f"  CSV gesamt:      {total_rows:>8,} Zeilen  ({size_mb:.1f} MB)")
            print(f"  Datei:           {output_path}")
    else:
        print("  (dry-run: keine Datei geschrieben)")
    print(f"{'=' * 55}\n")

    if not df.empty:
        print(df[["name", "asset_name", "transaction_type", "amount", "transaction_date"]].to_string(index=False))


if __name__ == "__main__":
    main()
