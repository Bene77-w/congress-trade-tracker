"""
Senate Scraper Diagnose-Tool
Verifiziert die identifizierten Fehlerquellen bei der DataTables-Pagination.

Tests:
    1. Pagination-Timing: Misst die echte DataTables-Renderzeit
    2. Konsistenz-Check: Scrapt 3x denselben Zeitraum, vergleicht Ergebnisse
    3. Row-Count-Validierung: Prüft DataTables-Info vs. tatsächliche Zeilen

Aufruf:
    python scripts/diagnose_scraper.py --test timing
    python scripts/diagnose_scraper.py --test consistency
    python scripts/diagnose_scraper.py --test rowcount
    python scripts/diagnose_scraper.py --test all
"""

import logging
import os
import re
import random
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def setup_logging():
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=log_format)


def accept_agreement(page):
    """Akzeptiert die Nutzungsbedingungen."""
    page.goto("https://efdsearch.senate.gov/search/", wait_until="networkidle")
    checkbox = page.locator("input#agree_statement")
    if checkbox.count() > 0:
        checkbox.click()
        page.locator("button.btn-primary").first.click()
        page.wait_for_url("**/search/**", timeout=30_000)
        page.wait_for_load_state("networkidle")
        logger.info("Agreement akzeptiert.")


def search_reports(page, from_date: str, to_date: str = None):
    """Füllt die Suche aus und startet sie."""
    page.locator("input[value='11']").check()
    from_input = page.locator("input#fromDate")
    from_input.fill("")
    from_input.fill(from_date)
    if to_date:
        to_input = page.locator("input#toDate")
        to_input.fill("")
        to_input.fill(to_date)
    page.locator("button.btn-primary", has_text="Search Reports").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("table#filedReports tbody tr", timeout=15_000)
    logger.info("Suchergebnisse geladen.")


def get_datatables_info(page) -> dict:
    """Parst die DataTables-Info 'Showing X to Y of Z entries'."""
    try:
        info_el = page.locator("#filedReports_info")
        if info_el.count() > 0:
            info_text = info_el.inner_text().strip()
            match = re.search(
                r"Showing\s+(\d+)\s+to\s+(\d+)\s+of\s+([\d,]+)\s+entries", info_text
            )
            if match:
                return {
                    "from": int(match.group(1)),
                    "to": int(match.group(2)),
                    "total": int(match.group(3).replace(",", "")),
                    "raw": info_text,
                }
    except Exception:
        pass
    return {}


def get_first_row_text(page) -> str:
    """Liest den Text der ersten Tabellenzeile."""
    try:
        first_row = page.locator("table#filedReports tbody tr").first
        return first_row.inner_text().strip()[:100]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Test 1: Pagination-Timing
# ---------------------------------------------------------------------------
def test_pagination_timing(num_pages: int = 5):
    """Misst die echte DataTables-Renderzeit nach Pagination-Click."""
    from playwright.sync_api import sync_playwright

    print("\n" + "=" * 60)
    print("  TEST 1: PAGINATION-TIMING")
    print("=" * 60)

    from_date = "01/03/2025"
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        accept_agreement(page)
        search_reports(page, from_date)

        info = get_datatables_info(page)
        print(f"\n  DataTables-Info: {info.get('raw', 'N/A')}")
        print(f"  Total Entries: {info.get('total', 'N/A')}")

        for i in range(num_pages):
            page_num = i + 1
            next_btn = page.locator("a#filedReports_next")
            next_classes = next_btn.get_attribute("class") or ""
            if "disabled" in next_classes:
                print(f"\n  Letzte Seite bei Seite {page_num}.")
                break

            # Fingerprint vor dem Click
            old_text = get_first_row_text(page)
            old_info = get_datatables_info(page)

            # Click und sofort Zeitmessung starten
            start = time.time()
            next_btn.click()

            # Poll alle 100ms bis DOM sich ändert
            render_time = None
            poll_interval = 0.1  # 100ms
            max_wait = 15.0  # 15 Sekunden max

            while (time.time() - start) < max_wait:
                time.sleep(poll_interval)
                new_text = get_first_row_text(page)
                if new_text and new_text != old_text:
                    render_time = time.time() - start
                    break

            new_info = get_datatables_info(page)
            rows = page.locator("table#filedReports tbody tr").all()
            valid_rows = [r for r in rows if r.locator("td").count() >= 5]

            result = {
                "page": page_num + 1,
                "render_time_ms": int(render_time * 1000) if render_time else "TIMEOUT",
                "rows_found": len(valid_rows),
                "dt_info": new_info.get("raw", "N/A"),
                "would_fail_1500ms": render_time is not None and render_time > 1.5,
            }
            results.append(result)

            status = "⚠ >1500ms!" if result["would_fail_1500ms"] else "OK"
            if render_time is None:
                status = "❌ TIMEOUT"

            print(
                f"\n  Seite {page_num} → {page_num + 1}: "
                f"Renderzeit = {result['render_time_ms']}ms | "
                f"Zeilen = {len(valid_rows)} | {status}"
            )
            print(f"    DataTables: {new_info.get('raw', 'N/A')}")

        browser.close()

    # Zusammenfassung
    print("\n" + "-" * 60)
    print("  ERGEBNIS:")
    times = [r["render_time_ms"] for r in results if isinstance(r["render_time_ms"], int)]
    if times:
        print(f"  Min Renderzeit:  {min(times)}ms")
        print(f"  Max Renderzeit:  {max(times)}ms")
        print(f"  Durchschnitt:    {sum(times) // len(times)}ms")
        failures = sum(1 for t in times if t > 1500)
        print(f"  Würden bei 1500ms-Wait fehlschlagen: {failures}/{len(times)}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Test 2: Konsistenz-Check
# ---------------------------------------------------------------------------
def test_consistency(runs: int = 3, days: int = 30):
    """Scrapt denselben Zeitraum mehrfach und vergleicht die Ergebnisse."""
    from playwright.sync_api import sync_playwright

    print("\n" + "=" * 60)
    print(f"  TEST 2: KONSISTENZ-CHECK ({runs} Läufe, letzte {days} Tage)")
    print("=" * 60)

    from_date = (datetime.now() - timedelta(days=days)).strftime("%m/%d/%Y")
    to_date = datetime.now().strftime("%m/%d/%Y")
    all_runs = []

    for run_num in range(1, runs + 1):
        print(f"\n  --- Run {run_num}/{runs} ---")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            accept_agreement(page)
            search_reports(page, from_date, to_date)

            dt_info = get_datatables_info(page)
            total_expected = dt_info.get("total", "?")

            run_data = {
                "run": run_num,
                "dt_total": total_expected,
                "pages": [],
                "senators": set(),
                "total_rows": 0,
            }

            current_page = 1
            while True:
                rows = page.locator("table#filedReports tbody tr").all()
                valid_rows = [r for r in rows if r.locator("td").count() >= 5]

                page_senators = []
                for row in valid_rows:
                    cells = row.locator("td").all()
                    name = f"{cells[0].inner_text().strip()} {cells[1].inner_text().strip()}"
                    page_senators.append(name)
                    run_data["senators"].add(name)

                run_data["pages"].append(
                    {"page": current_page, "rows": len(valid_rows), "senators": page_senators}
                )
                run_data["total_rows"] += len(valid_rows)

                print(
                    f"    Seite {current_page}: {len(valid_rows)} Zeilen "
                    # dict.fromkeys statt set: dedupliziert UND ist slicebar
                    # (set(...)[:3] wirft TypeError)
                    f"({', '.join(list(dict.fromkeys(page_senators))[:3])}...)"
                )

                # Nächste Seite
                next_btn = page.locator("a#filedReports_next")
                if next_btn.count() == 0:
                    break
                next_classes = next_btn.get_attribute("class") or ""
                if "disabled" in next_classes:
                    break

                # Smart Wait: Info-Text als Fingerprint (arg= ist keyword-only in Playwright)
                old_info = get_datatables_info(page).get("raw", "")
                next_btn.click()
                try:
                    page.wait_for_function(
                        """(oldInfo) => {
                            const el = document.getElementById('filedReports_info');
                            if (!el) return false;
                            return el.textContent.trim() !== oldInfo;
                        }""",
                        arg=old_info,
                        timeout=10_000,
                    )
                except Exception:
                    logger.warning(f"    Seite {current_page + 1}: Timeout beim Laden!")
                    page.wait_for_timeout(3000)

                current_page += 1

            print(
                f"  Run {run_num}: {run_data['total_rows']} Zeilen, "
                f"{len(run_data['pages'])} Seiten, "
                f"{len(run_data['senators'])} Senatoren "
                f"(DataTables: {total_expected})"
            )
            all_runs.append(run_data)
            browser.close()

        # Pause zwischen Runs
        if run_num < runs:
            time.sleep(3)

    # Vergleich
    print("\n" + "-" * 60)
    print("  VERGLEICH:")
    for r in all_runs:
        print(f"  Run {r['run']}: {r['total_rows']} Zeilen, DT-Total: {r['dt_total']}")

    counts = [r["total_rows"] for r in all_runs]
    if len(set(counts)) == 1:
        print(f"\n  ✓ KONSISTENT: Alle Runs haben {counts[0]} Zeilen.")
    else:
        print(f"\n  ✗ INKONSISTENT: {min(counts)} - {max(counts)} Zeilen (Δ {max(counts) - min(counts)})")

    # Fehlende Senatoren
    all_senators = set()
    for r in all_runs:
        all_senators |= r["senators"]
    for r in all_runs:
        missing = all_senators - r["senators"]
        if missing:
            print(f"  Run {r['run']} fehlen: {missing}")

    print("=" * 60)


# ---------------------------------------------------------------------------
# Test 3: Row-Count-Validierung
# ---------------------------------------------------------------------------
def test_row_count_validation():
    """Prüft DataTables-Info vs. tatsächliche Zeilen pro Seite."""
    from playwright.sync_api import sync_playwright

    print("\n" + "=" * 60)
    print("  TEST 3: ROW-COUNT-VALIDIERUNG")
    print("=" * 60)

    from_date = "01/03/2025"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        accept_agreement(page)
        search_reports(page, from_date)

        dt_info = get_datatables_info(page)
        total_expected = dt_info.get("total", 0)
        print(f"\n  DataTables Total: {total_expected}")

        total_actual = 0
        current_page = 1
        discrepancies = []

        while True:
            dt_page_info = get_datatables_info(page)
            expected_on_page = (
                dt_page_info.get("to", 0) - dt_page_info.get("from", 0) + 1
                if dt_page_info
                else "?"
            )

            rows = page.locator("table#filedReports tbody tr").all()
            all_tr = len(rows)
            valid_rows = [r for r in rows if r.locator("td").count() >= 5]
            valid_count = len(valid_rows)

            status = "✓" if valid_count == expected_on_page else "✗ DISKREPANZ"
            if valid_count != expected_on_page:
                discrepancies.append(
                    {
                        "page": current_page,
                        "expected": expected_on_page,
                        "actual": valid_count,
                        "all_tr": all_tr,
                    }
                )

            print(
                f"  Seite {current_page}: {valid_count} valide / {all_tr} total <tr> "
                f"(DT erwartet: {expected_on_page}) {status}"
            )
            print(f"    Info: {dt_page_info.get('raw', 'N/A')}")

            total_actual += valid_count

            # Nächste Seite (mit Smart Wait)
            next_btn = page.locator("a#filedReports_next")
            if next_btn.count() == 0:
                break
            next_classes = next_btn.get_attribute("class") or ""
            if "disabled" in next_classes:
                break

            old_info = get_datatables_info(page).get("raw", "")
            next_btn.click()

            try:
                page.wait_for_function(
                    """(oldInfo) => {
                        const el = document.getElementById('filedReports_info');
                        if (!el) return false;
                        return el.textContent.trim() !== oldInfo;
                    }""",
                    arg=old_info,
                    timeout=10_000,
                )
            except Exception:
                logger.warning(f"  Timeout beim Laden von Seite {current_page + 1}!")
                page.wait_for_timeout(3000)

            current_page += 1

        browser.close()

    # Zusammenfassung
    print("\n" + "-" * 60)
    print(f"  Total: {total_actual} Zeilen (erwartet: {total_expected})")
    if discrepancies:
        print(f"  ✗ {len(discrepancies)} Diskrepanzen:")
        for d in discrepancies:
            print(
                f"    Seite {d['page']}: {d['actual']} statt {d['expected']} "
                f"({d['all_tr']} <tr> total)"
            )
    else:
        print("  ✓ Keine Diskrepanzen!")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    setup_logging()

    parser = argparse.ArgumentParser(description="Senate Scraper Diagnose")
    parser.add_argument(
        "--test",
        choices=["timing", "consistency", "rowcount", "all"],
        default="all",
        help="Welchen Test ausführen (default: all)",
    )
    parser.add_argument("--pages", type=int, default=5, help="Seitenwechsel für Timing-Test")
    parser.add_argument("--runs", type=int, default=3, help="Anzahl Runs für Konsistenz")
    parser.add_argument("--days", type=int, default=30, help="Tage zurück für Konsistenz")
    args = parser.parse_args()

    if args.test in ("timing", "all"):
        test_pagination_timing(num_pages=args.pages)
    if args.test in ("consistency", "all"):
        test_consistency(runs=args.runs, days=args.days)
    if args.test in ("rowcount", "all"):
        test_row_count_validation()
