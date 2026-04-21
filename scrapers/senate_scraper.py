"""
Congress Trade Tracker - Senate Financial Disclosures Scraper
Scrapes Periodic Transaction Reports (PTRs) from https://efdsearch.senate.gov/search/

Uses Playwright for browser automation (headless Chrome).
Supports delta-loading, pagination, rate limiting, and random User-Agent rotation.
"""

import csv
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

# Projekt-Root in sys.path einfügen
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
BASE_URL = "https://efdsearch.senate.gov/search/"
SENATE_SESSION_START = "01/03/2025"  # Beginn des 119. Kongresses

# Zufällige User-Agents für Rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Spalten für senate_scraper_fehler.csv
FEHLER_COLUMNS = ["name", "disclosure_date", "ptr_link", "reason"]

# CSV-Spalten (Reihenfolge für die Ausgabedatei)
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
    "csv_upload_time",
]


class SenateScraper:
    """
    Playwright-basierter Scraper für Senate Periodic Transaction Reports.

    Workflow:
        1. accept_agreement()  – Nutzungsbedingungen akzeptieren
        2. search_reports()    – Suchformular ausfüllen und absenden
        3. scrape_results()    – Ergebnistabelle paginieren und Reports öffnen
        4. save()              – Ergebnisse als CSV speichern
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.trades: list[dict] = []
        self.pdf_skipped: list[dict] = []
        self._fehler: list[dict] = []
        self._visited_urls: set[str] = set()  # verhindert doppeltes Scrapen gleicher PTR-URLs
        self._project_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        self._output_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "senate_trades.csv"
        )
        self._fehler_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "senate_scraper_fehler.csv"
        )

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def run(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        delta: bool = True,
        num_runs: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Haupteinstiegspunkt.

        Nutzt den direkten DataTables-API-Endpoint (/search/report/data/ mit length=-1),
        um alle PTR-Links in einem einzigen HTTP-Request zu holen – keine Pagination,
        keine server-seitige Nicht-Determinismus.

        Args:
            from_date:  Startdatum MM/DD/YYYY. Wenn None → Delta-Logik oder Session-Start.
            to_date:    Enddatum MM/DD/YYYY (optional, leer = heute).
            delta:      Wenn True, wird das letzte disclosure_date aus der CSV gelesen.
            num_runs:   Anzahl Durchläufe für optionale Redundanz (Standard: 1).

        Returns:
            pd.DataFrame mit allen gescrapten Trades (dedupliziert).
        """
        if from_date is None:
            if delta:
                from_date = self._get_last_disclosure_date()
            if from_date is None:
                from_date = SENATE_SESSION_START

        total_runs = num_runs or settings.SENATE_SCRAPE_RUNS
        logger.info(f"=== Senate Scraper gestartet: {total_runs} Run(s) ===")

        all_trades = []
        for run_idx in range(1, total_runs + 1):
            run_trades = self._single_run(from_date, to_date, run_idx, total_runs)
            all_trades.extend(run_trades)
            if run_idx < total_runs:
                time.sleep(settings.SENATE_SCRAPE_RUN_PAUSE_SECONDS)

        df = pd.DataFrame(all_trades, columns=CSV_COLUMNS)
        before = len(df)
        dedup_subset = [c for c in CSV_COLUMNS if c not in ("disclosure_date", "csv_upload_time")]
        if not df.empty:
            dup_mask = df.duplicated(subset=dedup_subset, keep="first")
            if dup_mask.any():
                logger.info(f"=== {dup_mask.sum()} Duplikate gefunden (werden entfernt) ===")
                for _, row in df[dup_mask].iterrows():
                    logger.info(
                        f"  DEDUP: {row['name']} | {row['asset_name']} | "
                        f"{row['transaction_type']} | {row['amount']} | "
                        f"{row['transaction_date']} | ptr={row['ptr_link']}"
                    )
            df.drop_duplicates(subset=dedup_subset, keep="first", inplace=True)
        after = len(df)
        logger.info(
            f"=== Gesamt: {after} unique Trades aus {before} total "
            f"({total_runs} Run(s), {before - after} Duplikate entfernt) ==="
        )
        return df

    def _single_run(
        self,
        from_date: str,
        to_date: Optional[str],
        run_idx: int,
        total_runs: int,
    ) -> list[dict]:
        """Führt einen einzelnen Scrape-Durchlauf durch."""
        from playwright.sync_api import sync_playwright

        self.trades = []
        self.pdf_skipped = []
        self._visited_urls = set()

        logger.info(f"=== Run {run_idx}/{total_runs}: {from_date} – {to_date or 'heute'} ===")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            try:
                self._accept_agreement(page)
                self._scrape_results(page, context, from_date, to_date)
            except Exception:
                logger.exception(f"Fehler während Run {run_idx}")
                raise
            finally:
                browser.close()

        logger.info(
            f"Run {run_idx}/{total_runs} abgeschlossen: {len(self.trades)} Trades, "
            f"{len(self.pdf_skipped)} PDFs uebersprungen"
        )
        return list(self.trades)

    def save(self, df: Optional[pd.DataFrame] = None) -> str:
        """
        Speichert die Ergebnisse in data/raw/senate_trades.csv.
        Hängt neue Zeilen an eine bestehende Datei an (Append-Modus).

        Die CSV wird nach Report-Blöcken sortiert: alle Trades eines PTR stehen
        zusammen, Reports sind absteigend nach disclosure_date geordnet.

        Returns:
            Pfad zur gespeicherten Datei.
        """
        if df is None:
            df = pd.DataFrame(self.trades, columns=CSV_COLUMNS)

        if df.empty:
            logger.warning("Keine Trades zum Speichern vorhanden.")
            return self._output_path

        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)

        # Neue Zeilen mit aktuellem Upload-Zeitstempel versehen
        upload_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df["csv_upload_time"] = upload_time

        file_exists = os.path.isfile(self._output_path)
        if file_exists:
            existing = pd.read_csv(self._output_path)
            combined = pd.concat([existing, df], ignore_index=True)
            # NaN → "" normalisieren (CSV-Roundtrip wandelt leere Strings in NaN um,
            # was den Dedup-Vergleich bricht: NaN != "" → false negative)
            dedup_subset = [c for c in CSV_COLUMNS if c not in ("disclosure_date", "csv_upload_time")]
            combined[dedup_subset] = combined[dedup_subset].fillna("")
            # Duplikate entfernen (alle relevanten Felder müssen übereinstimmen)
            combined.drop_duplicates(
                subset=dedup_subset,
                keep="last",
                inplace=True,
            )
            combined = self._sort_by_report(combined)
            combined.to_csv(self._output_path, index=False)
            logger.info(
                f"Daten angehängt und dedupliziert → {self._output_path} "
                f"({len(combined)} Zeilen gesamt)"
            )
        else:
            df = self._sort_by_report(df)
            df.to_csv(self._output_path, index=False)
            logger.info(
                f"Neue Datei erstellt → {self._output_path} ({len(df)} Zeilen)"
            )

        return self._output_path

    def save_pdf_skipped(self) -> str:
        """
        Speichert alle übersprungenen PDF-Reports in data/raw/senate_pdf_skipped.csv.
        Hängt neue Einträge an (Append-Modus), dedupliziert auf ptr_link.

        Returns:
            Pfad zur gespeicherten Datei.
        """
        pdf_path = os.path.join(
            self._project_root, settings.DATA_RAW_PATH, "senate_pdf_skipped.csv"
        )

        if not self.pdf_skipped:
            logger.info("Keine übersprungenen PDFs vorhanden.")
            return pdf_path

        df_new = pd.DataFrame(self.pdf_skipped)
        # Vollständige URL sicherstellen
        df_new["ptr_link"] = df_new["href"].apply(
            lambda h: h if h.startswith("http") else f"https://efdsearch.senate.gov{h}"
        )
        df_new["csv_upload_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df_new = df_new[["name", "disclosure_date", "ptr_link", "csv_upload_time"]]

        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

        if os.path.isfile(pdf_path):
            existing = pd.read_csv(pdf_path)
            combined = pd.concat([existing, df_new], ignore_index=True)
            combined.drop_duplicates(subset=["ptr_link"], keep="last", inplace=True)
            combined.to_csv(pdf_path, index=False)
            logger.info(
                f"PDF-Liste aktualisiert → {pdf_path} ({len(combined)} Einträge gesamt)"
            )
        else:
            df_new.drop_duplicates(subset=["ptr_link"], inplace=True)
            df_new.to_csv(pdf_path, index=False)
            logger.info(
                f"PDF-Liste erstellt → {pdf_path} ({len(df_new)} Einträge)"
            )

        return pdf_path

    def save_fehler(self) -> str:
        """
        Speichert echte Scraping-Fehler in data/raw/senate_scraper_fehler.csv.
        Hängt neue Einträge an (Append-Modus), dedupliziert auf ptr_link.

        Erfasste Fehlertypen:
            empty_href        – Report ohne href in der API-Antwort
            unknown_link_type – href weder /view/paper/ noch /view/ptr/
            extract_error     – Exception beim Parsen des HTML-Reports

        Returns:
            Pfad zur gespeicherten Datei.
        """
        if not self._fehler:
            logger.info("Keine Fehler in dieser Session.")
            return self._fehler_path

        df_new = pd.DataFrame(self._fehler, columns=FEHLER_COLUMNS)
        os.makedirs(os.path.dirname(self._fehler_path), exist_ok=True)

        if os.path.isfile(self._fehler_path):
            existing = pd.read_csv(self._fehler_path)
            for col in FEHLER_COLUMNS:
                if col not in existing.columns:
                    existing[col] = ""
            combined = pd.concat([existing, df_new], ignore_index=True)
            combined.drop_duplicates(subset=["ptr_link"], keep="last", inplace=True)
            combined.to_csv(self._fehler_path, index=False)
        else:
            df_new.to_csv(self._fehler_path, index=False)

        logger.info(
            f"Fehler gespeichert → {self._fehler_path} ({len(df_new)} neu)"
        )
        return self._fehler_path

    # ------------------------------------------------------------------
    # Interne Methoden
    # ------------------------------------------------------------------

    def _accept_agreement(self, page):
        """Lädt die Startseite und akzeptiert die Nutzungsbedingungen."""
        logger.info(f"Lade Startseite: {BASE_URL}")
        page.goto(BASE_URL, wait_until="networkidle")

        # Checkbox "I understand …" anklicken
        checkbox = page.locator("input#agree_statement")
        if checkbox.count() > 0:
            checkbox.click()
            logger.info("Nutzungsbedingungen-Checkbox gesetzt.")
            # „Agree"-Button klicken (kann Navigation auslösen)
            page.locator("button.btn-primary").first.click()
            # Warten bis die Suchseite geladen ist (nicht auf Checkbox-State)
            page.wait_for_url("**/search/**", timeout=30_000)
            page.wait_for_load_state("networkidle")
            logger.info("Nutzungsbedingungen akzeptiert.")
        else:
            logger.info("Keine Nutzungsbedingungen-Hürde gefunden – übersprungen.")

    def _scrape_results(self, page, context, from_date: str, to_date: Optional[str] = None):
        """Holt alle PTR-Links via direktem DataTables-API-Call (length=-1), dann extrahiert
        Transaktionen aus jedem HTML-Report.

        Kein Pagination-DOM, keine Race Conditions, vollständig deterministisch.
        """
        reports = self._fetch_all_report_links(page, from_date, to_date)
        logger.info(
            f"API liefert {len(reports)} Reports für {from_date} – {to_date or 'heute'}."
        )

        for report in reports:
            href = report["href"]
            senator_name = report["name"]
            disclosure_date = report["disclosure_date"]

            if not href:
                logger.warning(f"  Leerer href für {senator_name} – übersprungen")
                self._record_fehler(senator_name, disclosure_date, "", "empty_href")
                continue

            # PDF-Reports erkennen und überspringen
            if "/search/view/paper/" in href:
                self.pdf_skipped.append({
                    "name": senator_name,
                    "disclosure_date": disclosure_date,
                    "href": href,
                })
                logger.info(f"  PDF_SKIPPED: {senator_name}")
                continue

            full_url = (
                href if href.startswith("http")
                else f"https://efdsearch.senate.gov{href}"
            )

            # Nur elektronische PTRs verarbeiten
            if "/search/view/ptr/" not in href:
                logger.debug(f"  Unbekannter Link-Typ übersprungen: {href}")
                self._record_fehler(senator_name, disclosure_date, full_url, "unknown_link_type")
                continue

            # URL bereits verarbeitet? Überspringen (verhindert Duplikate bei Multi-Run).
            if full_url in self._visited_urls:
                logger.debug(f"  Bereits verarbeitet, überspringe: {full_url}")
                continue
            self._visited_urls.add(full_url)

            # Report öffnen und Transaktionen extrahieren
            try:
                self._extract_transactions(context, senator_name, disclosure_date, full_url)
            except Exception:
                logger.exception(
                    f"  Fehler beim Verarbeiten des Reports von {senator_name}: {full_url}"
                )
                self._record_fehler(senator_name, disclosure_date, full_url, "extract_error")

            time.sleep(settings.SCRAPER_RATE_LIMIT_SECONDS)

        logger.info(
            f"_scrape_results abgeschlossen: {len(self.trades)} Trades aus {len(reports)} Reports."
        )

    def _fetch_all_report_links(
        self, page, from_date: str, to_date: Optional[str]
    ) -> list[dict]:
        """Einzelner POST an /search/report/data/ mit length=-1 — liefert alle PTRs auf einmal.

        Nutzt page.evaluate() um fetch() im Browser-Kontext auszuführen, so dass die
        Session-Cookies (inkl. CSRF-Token) automatisch mitgeschickt werden.

        Returns:
            Liste von Dicts mit 'name', 'disclosure_date', 'href'.
        """
        end_date_str = f"{to_date} 00:00:00" if to_date else ""

        raw_data = page.evaluate(
            """
            async ({fromDate, toDate}) => {
                const csrfToken =
                    (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';

                const params = new URLSearchParams();
                params.append('draw', '1');
                // Spalten 0-4 konfigurieren (Pflichtfelder für DataTables)
                [0, 1, 2, 3, 4].forEach(i => {
                    params.append('columns[' + i + '][data]', String(i));
                    params.append('columns[' + i + '][searchable]', 'true');
                    params.append('columns[' + i + '][orderable]', i !== 2 && i !== 3 ? 'true' : 'false');
                    params.append('columns[' + i + '][search][value]', '');
                    params.append('columns[' + i + '][search][regex]', 'false');
                });
                params.append('order[0][column]', '4');
                params.append('order[0][dir]', 'desc');
                params.append('order[1][column]', '1');
                params.append('order[1][dir]', 'asc');
                params.append('start', '0');
                params.append('length', '-1');   // ALLE Eintraege in einem Request
                params.append('search[value]', '');
                params.append('search[regex]', 'false');
                params.append('report_types', '[11]');  // 11 = Periodic Transaction Report
                params.append('filer_types', '[]');
                params.append('submitted_start_date', fromDate);
                params.append('submitted_end_date', toDate);
                params.append('candidate_state', '');
                params.append('senator_state', '');
                params.append('office_id', '');
                params.append('first_name', '');
                params.append('last_name', '');

                const resp = await fetch('/search/report/data/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        'X-CSRFToken': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: params.toString(),
                    credentials: 'include'
                });

                if (!resp.ok) {
                    throw new Error('HTTP ' + resp.status + ': ' + resp.statusText);
                }
                return await resp.json();
            }
            """,
            {"fromDate": f"{from_date} 00:00:00", "toDate": end_date_str},
        )

        total = raw_data.get("recordsTotal", 0)
        filtered = raw_data.get("recordsFiltered", 0)
        rows = raw_data.get("data", [])
        logger.info(
            f"DataTables API: recordsTotal={total}, recordsFiltered={filtered}, "
            f"rows_received={len(rows)}"
        )

        reports = []
        for row in rows:
            # row = [first_name, last_name, full_name_html, link_html, disclosure_date]
            if len(row) < 5:
                continue

            first_name = re.sub(r"<[^>]+>", "", str(row[0])).strip()
            last_name = re.sub(r"<[^>]+>", "", str(row[1])).strip()
            senator_name = f"{first_name} {last_name}".strip()

            # href aus HTML-String extrahieren (z.B. <a href="/search/view/ptr/...">)
            link_html = str(row[3])
            href_match = re.search(r'href=["\']([^"\']+)["\']', link_html)
            href = href_match.group(1) if href_match else ""

            disclosure_date = re.sub(r"<[^>]+>", "", str(row[4])).strip()

            reports.append({
                "name": senator_name,
                "disclosure_date": disclosure_date,
                "href": href,
            })

        return reports

    def _extract_transactions(
        self,
        context,
        senator_name: str,
        disclosure_date: str,
        ptr_url: str,
    ):
        """Öffnet einen PTR-Bericht in einem neuen Tab und liest die Transaktionen."""
        logger.info(f"  Öffne Report: {senator_name} → {ptr_url}")

        for attempt in range(1, settings.SCRAPER_RETRY_ATTEMPTS + 1):
            detail_page = context.new_page()
            try:
                detail_page.goto(ptr_url, wait_until="networkidle", timeout=30_000)
                detail_page.wait_for_selector("table.table-striped", timeout=10_000)

                rows = detail_page.locator("table.table-striped tbody tr").all()
                logger.info(f"  → {len(rows)} Transaktionszeilen gefunden.")

                # Alle Whitespace-Varianten (newlines, tabs, etc) durch einfaches Leerzeichen ersetzen
                def clean_text(text: str) -> str:
                    return re.sub(r"\s+", " ", text).strip()

                for row in rows:
                    cells = row.locator("td").all()
                    if len(cells) < 9:
                        continue

                    # Spalten-Mapping (basierend auf Browser-Analyse)
                    # 0: #, 1: Transaction Date, 2: Owner, 3: Ticker,
                    # 4: Asset Name, 5: Asset Type, 6: Type, 7: Amount, 8: Comment
                    ticker_raw = cells[3].inner_text().strip()
                    ticker = ticker_raw if ticker_raw != "--" else ""

                    # Bond-Metadaten aus asset_name herauslösen (Municipal Securities, Corporate Bonds).
                    # HTML-Struktur: <td>ASSET NAME<div class="text-muted">Rate/Coupon: X% Matures: ...</div></td>
                    # inner_text() auf die gesamte Zelle liefert beides zusammengefasst → trennen.
                    # .all() statt .inner_text() weil Asset-Typ "Other" 2+ div.text-muted haben kann
                    # (Company + Description) → Playwright Strict Mode crasht bei .inner_text() auf Mehrzahl.
                    bond_meta_els = cells[4].locator("div.text-muted").all()
                    if bond_meta_els:
                        bond_meta_text = " ".join(
                            clean_text(el.inner_text()) for el in bond_meta_els
                        )
                        full_asset_text = clean_text(cells[4].inner_text())
                        asset_name = full_asset_text.replace(bond_meta_text, "").strip(" ,")
                    else:
                        asset_name = clean_text(cells[4].inner_text())
                        bond_meta_text = ""

                    original_comment = clean_text(cells[8].inner_text())
                    if bond_meta_text:
                        comment_val = (
                            bond_meta_text if original_comment in ("--", "")
                            else f"{original_comment} | {bond_meta_text}"
                        )
                    else:
                        comment_val = original_comment

                    trade = {
                        "name": clean_text(senator_name),
                        "ticker": clean_text(ticker),
                        "asset_name": asset_name,
                        "asset_type": clean_text(cells[5].inner_text()),
                        "transaction_type": clean_text(cells[6].inner_text()),
                        "amount": clean_text(cells[7].inner_text()),
                        "transaction_date": clean_text(cells[1].inner_text()),
                        "owner": clean_text(cells[2].inner_text()),
                        "comment": comment_val,
                        "disclosure_date": clean_text(disclosure_date),
                        "ptr_link": ptr_url,
                        "csv_upload_time": "",
                    }
                    self.trades.append(trade)

                # Erfolg - Seite schliessen und Schleife verlassen
                detail_page.close()
                return

            except Exception:
                detail_page.close()
                if attempt < settings.SCRAPER_RETRY_ATTEMPTS:
                    logger.warning(
                        f"  Versuch {attempt}/{settings.SCRAPER_RETRY_ATTEMPTS} fehlgeschlagen "
                        f"fuer {senator_name} - retry in {settings.SCRAPER_RETRY_DELAY_SECONDS}s"
                    )
                    time.sleep(settings.SCRAPER_RETRY_DELAY_SECONDS)
                else:
                    raise

    def _record_fehler(
        self, name: str, disclosure_date: str, ptr_link: str, reason: str
    ) -> None:
        """Erfasst einen Scraping-Fehler für späteres Speichern via save_fehler()."""
        self._fehler.append({
            "name": name,
            "disclosure_date": disclosure_date,
            "ptr_link": ptr_link,
            "reason": reason,
        })

    # ------------------------------------------------------------------
    # Sortierung
    # ------------------------------------------------------------------

    @staticmethod
    def _sort_by_report(df: pd.DataFrame) -> pd.DataFrame:
        """
        Sortiert den DataFrame so, dass alle Trades eines PTR-Reports zusammenstehen
        und die Report-Blöcke absteigend nach disclosure_date geordnet sind.
        Die originale Reihenfolge der Zeilen innerhalb eines Reports bleibt erhalten.
        """
        if df.empty:
            return df

        df = df.copy()
        df["_row_order"] = range(len(df))

        # Disclosure-Datum pro ptr_link ermitteln (alle Zeilen eines Reports haben dasselbe)
        report_dates = (
            df.groupby("ptr_link", sort=False)["disclosure_date"]
            .first()
            .reset_index()
        )
        report_dates["_report_date_parsed"] = pd.to_datetime(
            report_dates["disclosure_date"], format="%m/%d/%Y", errors="coerce"
        )

        df = df.merge(
            report_dates[["ptr_link", "_report_date_parsed"]], on="ptr_link", how="left"
        )
        df = df.sort_values(
            ["_report_date_parsed", "_row_order"], ascending=[False, True]
        )
        df = df.drop(columns=["_row_order", "_report_date_parsed"])
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Delta-Logik
    # ------------------------------------------------------------------

    def _get_last_disclosure_date(self) -> Optional[str]:
        """
        Liest das neueste disclosure_date aus der bestehenden CSV.
        Gibt das Datum im Format MM/DD/YYYY zurück, oder None.
        """
        if not os.path.isfile(self._output_path):
            logger.info(
                "Keine bestehende senate_trades.csv gefunden – lade ab Session-Start."
            )
            return None

        try:
            df = pd.read_csv(self._output_path)
            if "disclosure_date" not in df.columns or df.empty:
                return None

            # Daten parsen um das neueste zu finden
            try:
                dates = pd.to_datetime(df["disclosure_date"], format="%m/%d/%Y", errors="coerce")
            except (ValueError, TypeError):
                dates = pd.to_datetime(df["disclosure_date"], format="mixed", dayfirst=False, errors="coerce")

            # NaT-Werte entfernen
            dates = dates.dropna()
            if dates.empty:
                return None

            latest = dates.max()
            # 1 Tag Sicherheitspuffer um keine Trades zu verpassen
            latest = latest - pd.Timedelta(days=1)
            result = latest.strftime("%m/%d/%Y")
            logger.info(f"Delta-Modus: Letztes disclosure_date = {result}")
            return result
        except Exception:
            logger.exception("Fehler beim Lesen der bestehenden CSV für Delta-Logik")
            return None

    # ------------------------------------------------------------------
    # Statistiken
    # ------------------------------------------------------------------

    def get_summary(self, df: Optional[pd.DataFrame] = None) -> str:
        """Gibt eine lesbare Zusammenfassung der Scraping-Ergebnisse zurück."""
        if df is None:
            df = pd.DataFrame(self.trades, columns=CSV_COLUMNS)

        lines = [
            "=" * 60,
            "  SENATE SCRAPER – ZUSAMMENFASSUNG",
            "=" * 60,
            f"  Trades gefunden:       {len(df)}",
            f"  PDFs übersprungen:     {len(self.pdf_skipped)}",
            f"  Fehler (Scraping):     {len(self._fehler)}",
        ]

        if not df.empty:
            unique_senators = df["name"].nunique()
            lines.append(f"  Eindeutige Senatoren:  {unique_senators}")

            # Top 5 aktivste Senatoren
            top = df["name"].value_counts().head(5)
            lines.append("")
            lines.append("  TOP 5 aktivste Senatoren:")
            for name, count in top.items():
                lines.append(f"    • {name}: {count} Trades")

            # Transaktionstypen
            types = df["transaction_type"].value_counts()
            lines.append("")
            lines.append("  Transaktionstypen:")
            for t, c in types.items():
                lines.append(f"    • {t}: {c}")

        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test-Funktion
# ---------------------------------------------------------------------------

def _setup_logging():
    """Konfiguriert Logging: Konsole + Datei (logs/senate_scraper.log)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(project_root, settings.LOGS_PATH)
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "senate_scraper.log")

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # Root-Logger konfigurieren
    logging.basicConfig(level=logging.INFO, format=log_format)

    # FileHandler hinzufügen (append-Modus)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(file_handler)

    logger.info(f"Log-Datei: {log_file}")


def test_scraper(days: int = 7, num_runs: Optional[int] = None, dry_run: bool = False):
    """Testlauf: Scrape der letzten X Tage (default: 7)."""
    _setup_logging()

    scraper = SenateScraper(headless=True)

    # Letzten X Tage als Testlauf
    from_date = (datetime.now() - timedelta(days=days)).strftime("%m/%d/%Y")
    to_date = datetime.now().strftime("%m/%d/%Y")

    logger.info(f"Testlauf: letzte {days} Tage ({from_date} bis {to_date})")
    df = scraper.run(
        from_date=from_date, to_date=to_date, delta=False,
        num_runs=num_runs,
    )

    # Zusammenfassung ausgeben
    print(scraper.get_summary(df))

    if not df.empty:
        if dry_run:
            print("\n[DRY-RUN] Daten werden nicht gespeichert.")
            print("\nErste 10 Zeilen:")
            print(df.head(10).to_string(index=False))
        else:
            path = scraper.save(df)
            print(f"\nGespeichert unter: {path}")
            print("\nErste 10 Zeilen:")
            print(df.head(10).to_string(index=False))
            pdf_path = scraper.save_pdf_skipped()
            if scraper.pdf_skipped:
                print(f"\nPDF-Liste gespeichert: {pdf_path} ({len(scraper.pdf_skipped)} Einträge)")
            scraper.save_fehler()
    else:
        print("\nKeine Trades im Zeitraum gefunden.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Senate Trade Scraper")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Anzahl Tage zurück für den Testlauf (default: 7)",
    )
    parser.add_argument(
        "--from-date",
        type=str,
        default=None,
        help="Explizites Startdatum MM/DD/YYYY (überschreibt --days)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help=f"Anzahl Runs (default: {settings.SENATE_SCRAPE_RUNS} aus settings)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur scrapen und anzeigen, nicht speichern",
    )
    args = parser.parse_args()

    if args.from_date:
        # Modus: explizites Datum
        _setup_logging()
        scraper = SenateScraper(headless=True)
        df = scraper.run(
            from_date=args.from_date,
            delta=False,
            num_runs=args.runs,
        )
        print(scraper.get_summary(df))
        if not df.empty:
            if args.dry_run:
                print("\n[DRY-RUN] Daten werden nicht gespeichert.")
                print("\nErste 10 Zeilen:")
                print(df.head(10).to_string(index=False))
            else:
                path = scraper.save(df)
                print(f"\nGespeichert unter: {path}")
                pdf_path = scraper.save_pdf_skipped()
                if scraper.pdf_skipped:
                    print(f"PDF-Liste gespeichert: {pdf_path} ({len(scraper.pdf_skipped)} Einträge)")
                scraper.save_fehler()
    else:
        test_scraper(days=args.days, num_runs=args.runs, dry_run=args.dry_run)

