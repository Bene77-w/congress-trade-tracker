"""
Quiver-Auto-Download-Scraper
============================

Headless Credential-Login bei Quiver Quantitative + Download der
Congress-Trading-XLSX. Legt die Datei atomar an genau den Pfad, den die
Import-Pipeline (`scripts/import_trades.py`) liest:
    data/raw/congress-trading-all.xlsx   (= settings.QUIVER_TRADES_FILE)

Gedacht fuer den cron-Betrieb (taeglich 03:00), daher:
  - kein manueller Eingriff, kein fixes sleep (verifizierbare Wartebedingungen)
  - saubere Trennung zweier Fehlerklassen + aussagekraeftige Exit-Codes:
      0  Erfolg
      1  generischer/unerwarteter Fehler
      2  Login fehlgeschlagen (Credentials/2FA)        -> QuiverLoginError
      3  eingeloggt, aber Download fehlgeschlagen       -> QuiverDownloadError
         (haeufigste Ursache: abgelaufenes Quiver-Abo)

Credentials kommen ausschliesslich aus der .env (QUIVER_EMAIL/QUIVER_PASSWORD),
alle uebrigen Parameter (URLs, Timeouts, Selektoren) aus config/settings.py.
"""

import logging
import os
import sys

# Projekt-Root in sys.path einfuegen (Repo-Root fuer die Imports)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

logger = logging.getLogger(__name__)

# Exit-Codes (siehe Modul-Docstring)
EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_LOGIN = 2
EXIT_DOWNLOAD = 3

# Zielpfade (aus config abgeleitet)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RAW_DIR = os.path.join(_PROJECT_ROOT, settings.DATA_RAW_PATH)
QUIVER_TRADES_PATH = os.path.join(_RAW_DIR, settings.QUIVER_TRADES_FILE)
# Temp-Datei fuer atomaren Download (versteckt, gleicher Ordner => os.replace ist atomar)
_TEMP_PATH = os.path.join(_RAW_DIR, "." + settings.QUIVER_TRADES_FILE + ".tmp")


# ---------------------------------------------------------------------------
# Fehlerklassen
# ---------------------------------------------------------------------------

class QuiverLoginError(Exception):
    """Login fehlgeschlagen (falsche Credentials oder 2FA-Challenge)."""


class QuiverDownloadError(Exception):
    """Eingeloggt, aber XLSX-Download fehlgeschlagen (vermutlich Abo abgelaufen)."""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging():
    """Konfiguriert Logging: Konsole + Datei (logs/quiver_scraper.log)."""
    log_dir = os.path.join(_PROJECT_ROOT, settings.LOGS_PATH)
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "quiver_scraper.log")

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=log_format)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(file_handler)

    logger.info(f"Log-Datei: {log_file}")


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

def _require_credentials():
    """Stellt sicher, dass E-Mail + Passwort gesetzt sind."""
    if not settings.QUIVER_EMAIL or not settings.QUIVER_PASSWORD:
        raise QuiverLoginError(
            "QUIVER_EMAIL / QUIVER_PASSWORD nicht gesetzt — bitte in .env eintragen."
        )


def _require_selectors():
    """Stellt sicher, dass alle benoetigten Selektoren konfiguriert sind.

    Optional (nicht erzwungen): Cookie-Reject + 2FA — beide best-effort.
    """
    required = {
        "QUIVER_EMAIL_SELECTOR": settings.QUIVER_EMAIL_SELECTOR,
        "QUIVER_PASSWORD_SELECTOR": settings.QUIVER_PASSWORD_SELECTOR,
        "QUIVER_SUBMIT_SELECTOR": settings.QUIVER_SUBMIT_SELECTOR,
        "QUIVER_POST_LOGIN_SELECTOR": settings.QUIVER_POST_LOGIN_SELECTOR,
        "QUIVER_DOWNLOAD_SELECTOR": settings.QUIVER_DOWNLOAD_SELECTOR,
    }
    fehlend = [name for name, wert in required.items() if not wert]
    if fehlend:
        raise QuiverLoginError(
            "Selektoren nicht konfiguriert (in config/settings.py eintragen, "
            f"Werte aus `playwright codegen`): {', '.join(fehlend)}"
        )


def _try_dismiss_cookie(page):
    """Klickt den Cookie-Reject-Button, falls vorhanden (best-effort, kein Abbruch)."""
    sel = settings.QUIVER_COOKIE_REJECT_SELECTOR
    if not sel:
        return
    try:
        page.locator(sel).first.click(timeout=5_000)
        logger.info("Cookie-Banner abgelehnt.")
    except Exception:
        logger.info("Kein Cookie-Banner sichtbar — weiter.")


# ---------------------------------------------------------------------------
# Kern: Login + Download
# ---------------------------------------------------------------------------

def download_quiver(headless: bool = True, dry_run: bool = False) -> str:
    """
    Loggt sich headless bei Quiver ein und laedt die Congress-Trading-XLSX.

    Returns: Pfad der bereitgestellten Datei.
    Raises:  QuiverLoginError / QuiverDownloadError je nach Fehlerklasse.
    """
    # Lazy-Import — Playwright erst zur Laufzeit laden.
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    _require_credentials()
    _require_selectors()

    timeout = settings.QUIVER_SCRAPER_TIMEOUT_MS
    dl_timeout = settings.QUIVER_SCRAPER_DOWNLOAD_TIMEOUT_MS

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            accept_downloads=True,
            user_agent=settings.QUIVER_USER_AGENT,  # nicht als HeadlessChrome auftreten
            viewport={"width": settings.QUIVER_VIEWPORT_WIDTH,
                      "height": settings.QUIVER_VIEWPORT_HEIGHT},
        )
        page = context.new_page()
        try:
            # --- 1) Login --------------------------------------------------
            logger.info(f"Login-Seite laden: {settings.QUIVER_LOGIN_URL}")
            page.goto(settings.QUIVER_LOGIN_URL, wait_until="networkidle",
                      timeout=timeout)

            # Cookie-Banner ggf. wegklicken (ueberlagert sonst die Login-Felder).
            _try_dismiss_cookie(page)

            logger.info("Credentials eintragen und absenden ...")
            page.fill(settings.QUIVER_EMAIL_SELECTOR, settings.QUIVER_EMAIL)
            page.fill(settings.QUIVER_PASSWORD_SELECTOR, settings.QUIVER_PASSWORD)
            page.locator(settings.QUIVER_SUBMIT_SELECTOR).first.click()

            # --- 2) Auth-Verifikation (kein sleep) -------------------------
            # Auf ein abo-UNABHAENGIGES Element warten (Hamburger-Menue), das nur im
            # eingeloggten Zustand existiert. So ist ein abgelaufenes Abo KEIN
            # Login-Fehler, sondern faellt erst beim Download (Code 3) auf.
            try:
                page.wait_for_selector(settings.QUIVER_POST_LOGIN_SELECTOR,
                                       timeout=timeout)
            except PlaywrightTimeoutError:
                # 2FA gesondert behandeln: Weg-2-Login (E-Mail+Passwort) kann
                # eine 2FA-Challenge nicht aufloesen -> eindeutig fehlschlagen.
                if (settings.QUIVER_TWOFA_SELECTOR
                        and page.locator(settings.QUIVER_TWOFA_SELECTOR).count() > 0):
                    raise QuiverLoginError(
                        "2FA-Challenge erkannt — automatischer Login (E-Mail+Passwort) "
                        "nicht moeglich. 2FA fuer dieses Konto deaktivieren."
                    )
                raise QuiverLoginError(
                    "Login fehlgeschlagen — Post-Login-Element nicht gefunden "
                    "(falsche Credentials oder geaenderte Login-Seite?)."
                )

            logger.info("Login erfolgreich (eingeloggter Zustand verifiziert).")

            # --- 3) Download (ab hier: Auth OK => Fehler = DownloadError) ---
            # Direkt zur Export-Seite navigieren und den Congress-Download (href-basiert)
            # ausloesen. Die Export-Seite ist Premium: fehlt der Link bei abgelaufenem Abo,
            # schlaegt der Klick fehl -> korrekt als Download-/Abo-Fehler (Code 3).
            logger.info(f"Export-Seite laden: {settings.QUIVER_EXPORT_URL}")
            try:
                page.goto(settings.QUIVER_EXPORT_URL, wait_until="networkidle",
                          timeout=timeout)
                with page.expect_download(timeout=dl_timeout) as download_info:
                    page.locator(settings.QUIVER_DOWNLOAD_SELECTOR).first.click()
                download = download_info.value
                download.save_as(_TEMP_PATH)
            except PlaywrightTimeoutError as exc:
                raise QuiverDownloadError(
                    "XLSX-Download fehlgeschlagen (Timeout/Export-Link nicht gefunden) "
                    "— wahrscheinlich abgelaufenes Quiver-Abo."
                ) from exc

            # Plausibilitaets-Check: nicht-leere Datei?
            if not os.path.exists(_TEMP_PATH) or os.path.getsize(_TEMP_PATH) == 0:
                raise QuiverDownloadError(
                    "Heruntergeladene XLSX ist leer/fehlt — vermutlich abgelaufenes Abo."
                )

            groesse = os.path.getsize(_TEMP_PATH)
            logger.info(f"Download abgeschlossen: {groesse} Bytes (Temp: {_TEMP_PATH})")

            # --- 4) Bereitstellen ------------------------------------------
            if dry_run:
                logger.info(f"[DRY-RUN] kein os.replace — Temp-Datei bleibt: {_TEMP_PATH}")
                return _TEMP_PATH

            # Atomar ersetzen (Temp -> Zielpfad). os.replace ist auf demselben
            # Dateisystem atomar -> keine korrupte Teildatei fuer die Pipeline.
            os.replace(_TEMP_PATH, QUIVER_TRADES_PATH)
            logger.info(f"XLSX bereitgestellt (atomar ersetzt): {QUIVER_TRADES_PATH}")
            return QUIVER_TRADES_PATH

        finally:
            browser.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Quiver-Auto-Download-Scraper")
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Browser sichtbar starten (Debug / Selektoren-Abgleich).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Download in Temp-Datei, aber Zieldatei NICHT ersetzen.",
    )
    args = parser.parse_args()

    _setup_logging()

    headless = settings.QUIVER_SCRAPER_HEADLESS and not args.no_headless

    try:
        pfad = download_quiver(headless=headless, dry_run=args.dry_run)
        logger.info(f"Fertig. Datei: {pfad}")
        return EXIT_OK
    except QuiverLoginError as exc:
        logger.error(f"[LOGIN-FEHLER] {exc}")
        return EXIT_LOGIN
    except QuiverDownloadError as exc:
        logger.error(f"[DOWNLOAD-FEHLER] {exc}")
        return EXIT_DOWNLOAD
    except Exception:
        logger.exception("[UNERWARTETER FEHLER]")
        return EXIT_GENERIC


if __name__ == "__main__":
    sys.exit(main())
