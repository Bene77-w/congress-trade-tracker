# Walkthrough: Modul 2 – House Trade Scraper

## Überblick

Requests/pdfplumber-basierter Scraper (`scrapers/house_scraper.py`) für Periodic Transaction Reports (PTRs) von https://disclosures-clerk.house.gov/.

### Architektur (finale Version)

```
run()
 ├── _get_existing_doc_ids()          # Delta: bekannte DocIDs aus CSV lesen
 └── _download_filing_list(year) × N  # ZIP herunterladen, XML parsen, filtern
      └── _download_pdf(doc_id, year) × M
           └── _parse_ptr_pdf(pdf_bytes)
                └── _extract_transactions_from_text(text)
                     └── _parse_transaction_block(block) × K
                          └── _extract_asset_name(block)
           └── _normalize_transactions(parsed, ...)

save(df)   # Append + Dedup in house_trades.csv
```

### Features

| Feature | Details |
|---|---|
| **ZIP-Download** | `{year}FD.zip` von disclosures-clerk.house.gov → enthält eine XML-Datei mit allen Filings des Jahres |
| **XML-Filterung** | Nur `FilingType=P` (PTR) + `Prefix=Hon.` → ausschließlich aktive Members, keine Staff/Officers |
| **PDF-Parsing** | pdfplumber (kein OCR) – extrahiert Text, bereinigt Null-Bytes (`\x00`) |
| **Block-Parser** | Mehrzeilige Transaktionen werden zu logischen Blöcken zusammengeführt; Trigger: Zeile mit Tx-Typ + Datum |
| **Skip-Patterns** | `F S:` (Filing Status), `S O:` (Subholding Of), `I P O`, `I V D`, Zertifizierungszeilen etc. werden ignoriert |
| **Delta-Modus** | Liest bekannte DocIDs aus `ptr_link`-Spalte der bestehenden CSV; überspringt bereits vorhandene Filings |
| **Deduplication** | `DEDUP_SUBSET` (alle CSV-Spalten außer `disclosure_date`) + `fillna("")` gegen NaN-Bug; `run()` → `keep=first`, `save()` → `keep=last` |
| **Rate Limiting** | 1s Pause zwischen PDF-Downloads (`SCRAPER_RATE_LIMIT_SECONDS`) |
| **Retry-Logik** | 3 Versuche mit 5s Pause (`SCRAPER_RETRY_ATTEMPTS`, `SCRAPER_RETRY_DELAY_SECONDS`) |
| **Logging** | Konsole + `logs/house_scraper.log` (UTF-8, Append) |

### Filing Type Kontext

Die House-Disclosure-ZIP enthält alle Filing-Typen des Jahres. Der Scraper filtert auf:

| Code | Bezeichnung (Webseite) | Bedeutung |
|------|------------------------|-----------|
| **P** ✅ | PTR Original | Periodic Transaction Report – einzelne Trade-Meldung (STOCK Act) |
| O | FD Original | Jährliche Vermögensoffenlegung (kein Einzeltrade-Detail) |
| A | FD Amendment | Korrektur einer FD |
| H | New Filer | Erstmeldung bei Amtsantritt |
| X | Extension | Fristverlängerung |
| T | Termination | Abschluss-FD beim Ausscheiden |
| E | Term. Exemption | Befreiung von Termination-FD |
| B | Blind Trust | Qualifizierter Blind Trust |
| G | Gift Waiver | Befreiung von Geschenke-Meldepflicht |
| C | *(Staff FD)* | Jährliche FD von Mitarbeitern/Officers |
| D | *(Staff Term.)* | Terminations-FD von Staff |
| W | *(Withdraw)* | Rücknahme (unter $5.000 Schwellenwert) |

→ Nur **P** enthält Einzeltrades. Alle anderen Typen werden durch `if filing_type != "P" or prefix != "Hon."` herausgefiltert.

### DocID-Ranges

DocIDs verraten den Filing-Typ (2024-Daten):
- `200xxxxx` → PTR (FilingType=P)
- `100xxxxx` → FD Original/New Filer (O, H)
- `300xxxxx` → Extension (X)
- `400xxxxx` → Staff Termination (D)
- `820xxxxx` → Ältere Einträge (Mixed)
- `79xxxx` → Withdraw (W)

### Output-Spalten

`name`, `ticker`, `asset_name`, `asset_type`, `transaction_type`, `amount`, `transaction_date`, `owner`, `comment`, `disclosure_date`, `ptr_link`

---

## CLI-Aufruf

```powershell
cd "C:\Users\benedikt\OneDrive\bmb_trdprg\congress_tracker"

# Standard (aktuelles + vorheriges Jahr, Delta-Modus):
.\venv\Scripts\python.exe scrapers\house_scraper.py

# Einzelnes Jahr:
.\venv\Scripts\python.exe scrapers\house_scraper.py --year 2024

# Kein Delta (alle Filings neu verarbeiten):
.\venv\Scripts\python.exe scrapers\house_scraper.py --year 2024 --no-delta

# Einzelne DocID testen (dry-run):
.\venv\Scripts\python.exe scrapers\house_scraper.py --doc-id 20024436 --year 2024 --dry-run

# Nur Filing-Liste anzeigen (kein PDF-Download):
.\venv\Scripts\python.exe scrapers\house_scraper.py --year 2024 --list-only
```

### Python API

```python
from scrapers.house_scraper import HouseScraper

scraper = HouseScraper()
df = scraper.run(years=[2024], delta=True)   # Produktion: nur neue Filings
scraper.save(df)
```

---

## Settings (`config/settings.py`)

```python
SCRAPER_RATE_LIMIT_SECONDS = 1.0     # Pause zwischen PDF-Downloads
SCRAPER_RETRY_ATTEMPTS = 3           # Wiederholungsversuche bei HTTP-Fehler
SCRAPER_RETRY_DELAY_SECONDS = 5.0    # Wartezeit zwischen Retries
SCRAPER_USER_AGENT = 'CongressTrader/1.0 (Python)'

DUPLICATE_CHECK_COLUMNS = [
    'name', 'ticker', 'asset_name', 'asset_type', 'transaction_type',
    'amount', 'transaction_date', 'owner', 'comment', 'ptr_link',
]  # Muss mit DEDUP_SUBSET in house_scraper.py übereinstimmen (Test prüft das)
```

---

## PDF-Parsing: Wie es funktioniert

### Problem: Kein strukturiertes Format

House PTR-PDFs sind scanned/digitale PDFs ohne maschinenlesbare Tabellen. pdfplumber extrahiert Fließtext, der Transaktionen über mehrere Zeilen verteilt.

### Schritt 1: Block-Erkennung

Eine neue Transaktion beginnt wenn eine Zeile **gleichzeitig** enthält:
- Transaktionstyp: `P`, `S`, `E`, `S (partial)`, `S (full)`
- Datum: `MM/DD/YYYY`

```
"Apple Inc. (AAPL) [ST] P 01/15/2024 $15,001 - $50,000"  → neuer Block-Start
"$50,000"                                                   → Fortsetzung (Zeilenumbruch)
```

### Schritt 2: Noise-Filterung

Vor der Block-Erkennung werden irrelevante Zeilen via `skip_patterns` herausgefiltert:

| Pattern | Beispiel | Grund |
|---------|---------|-------|
| `F S:` | `F S: New` | Filing Status (nicht Transaktion) |
| `S O:` | `S O: Fidelity Trust` | Subholding Of (Depot/Trust-Name) |
| `I P O` | `I P O` | IPO-Abschnittsheader |
| `I V D` | `I V D` | Investment Vehicles Disclosure Header |
| `I CERTIFY` | `I CERTIFY that...` | Zertifizierungstext |
| `Digitally Signed:` | `Digitally Signed: John, 03/15/2024` | Signaturzeile (aber Datum als Fallback!) |
| `T$` | `T` | Null-Byte-bereinigtes `Transactions`-Wort |

### Schritt 3: Felder extrahieren

Pro Block werden extrahiert:

| Feld | Methode |
|------|---------|
| **Owner** | Prefix `SP`/`JT`/`DC` am Zeilenanfang |
| **Ticker** | Regex `\([A-Z][A-Z0-9.]*\)` – z.B. `(AAPL)` |
| **Asset Type** | Regex `\[(ST\|OP\|GS\|OT\|CS\|DC\|EF\|MF)\]` |
| **Transaction Type** | `S (partial)` > `S (full)` > `P/S/E vor Datum` > Fallback RE |
| **Transaction Date** | Erstes `MM/DD/YYYY` im Block |
| **Notification Date** | Zweites `MM/DD/YYYY` im Block (Fallback: Digitally Signed) |
| **Amount** | `$X - $Y` Range; Fallback: zwei Einzelbeträge; dann ein Einzelbetrag |
| **Asset Name** | Alles vor dem Tx-Typ-Token, bereinigt um Ticker/AssetType/Beträge |

### Bekannte Limitierungen

- **`Class P shares` False Positive**: `Class P shares` kann fälschlich als Tx-Typ `P` erkannt werden (Fallback-Regex). Seltener Randfäll, dokumentiert in `TestEdgeCases`.
- **Null-Bytes**: Echte PDFs enthalten manchmal `\x00`-Bytes → werden beim Laden bereinigt.
- **Subholding-Fix ausstehend**: `S O:`-Zeilen (Subholding Of = Depot/Trust-Name) werden aktuell als Rauschen gefiltert statt als eigene Spalte gespeichert → führt zu falscher Dedup bei identischen Trades aus verschiedenen Trusts. Plan: `C:\Users\benedikt\.claude\plans\soft-swimming-parasol.md`

---

## Deduplication

### Warum zwei Stufen?

```
run()   → drop_duplicates(keep="first")  # In-Session: doppelte Parsing-Ergebnisse
save()  → drop_duplicates(keep="last")   # Cross-Session: neue Daten überschreiben alte
```

### NaN-Bug (behoben)

`pd.read_csv()` liest leere Felder als `NaN`. `NaN != NaN` in pandas → zwei Zeilen mit leerem Ticker gelten als *unterschiedlich* → Duplikate bei jedem `save()`.

**Fix:** `df[DEDUP_SUBSET] = df[DEDUP_SUBSET].fillna("")` vor jedem `drop_duplicates()`.

### DEDUP_SUBSET

```python
DEDUP_SUBSET = [c for c in CSV_COLUMNS if c != "disclosure_date"]
# = ['name', 'ticker', 'asset_name', 'asset_type', 'transaction_type',
#    'amount', 'transaction_date', 'owner', 'comment', 'ptr_link']
```

`disclosure_date` wird ausgeschlossen weil zwei identische Trades mit unterschiedlichem Meldedatum (Amendment) als Duplikate gelten sollen.

---

## Entwicklungsgeschichte & Bugfixes

### Phase 1 – Ausgangsproblem: Playwright nicht notwendig

Die House-Seite liefert alle PTR-Metadaten als jährliche ZIP-Datei (XML), keine dynamische Web-App nötig. → Einfacher requests-Stack ohne Browser.

### Phase 2 – Null-Byte Bug in echten PDFs

**Problem:** `pdfplumber.open()` lieferte bei manchen echten PDFs Text mit `\x00`-Bytes. Diese erschienen als leere Strings in Regex-Matches und verhinderten die Block-Erkennung.

**Fix:** `full_text = full_text.replace("\x00", "")` direkt nach der Textextraktion.

### Phase 3 – Notification Date Fallback

**Problem:** Viele echte PDFs enthalten keine `Notification Date:` Zeile. Das Datum erscheint nur in der `Digitally Signed:` Zeile.

**Fix:**
```python
signed_match = re.search(
    r"Digitally\s+Signed:.*?,\s*(\d{2}/\d{2}/\d{4})",
    full_text, re.IGNORECASE,
)
```

### Phase 4 – Amount Split Across Lines

**Problem:** Bei langen Asset-Namen wird die Amount-Range auf zwei Zeilen umbrochen:
```
iShares Core S&P 500 ETF (IVV)
[EF] P 03/15/2024 $15,001 -
$50,000
```

**Fix:** `RE_ASSET_TYPE.sub("", block)` entfernt `[EF]`-Token vor der Amount-Suche, danach `re.sub(r"\s+", " ", ...)` kollabiert Leerzeichen. Die Range `$15,001 - $50,000` wird dann korrekt gefunden.

### Phase 5 – NaN-Bug + Hardcoded Dedup-Subset (behoben Feb 2026)

**Problem 1:** `save()` fehlte `fillna("")` → Trades mit leeren Feldern wurden bei jedem Speichern dupliziert.

**Problem 2:** Hardcodiertes 7-Spalten-Subset `["name", "ticker", "transaction_date", ...]` fehlte `asset_name`, `asset_type`, `comment` → verschiedene Assets mit gleichem Ticker wurden fälschlich dedupliziert.

**Fix:** `DEDUP_SUBSET` Konstante + `fillna("")` in `run()` und `save()`.

---

## Tests (`tests/test_house_scraper.py`)

125 Tests, alle grün. Klassen:

| Klasse | Testet |
|---|---|
| `TestRegexPatterns` | Alle kompilierten Regex: Ticker, AssetType, TxType, Amount, Date |
| `TestParseTransactionBlock` | Vollständiges Block-Parsing: Owner, Ticker, Typ, Datum, Amount, Asset |
| `TestExtractAssetName` | Asset-Name-Bereinigung: Ticker/Type/Datum/Amount entfernen |
| `TestExtractTransactionsFromText` | Block-Zusammenführung: Header-Skip, Multiline, leerer Text |
| `TestParsePtrPdf` | End-to-End PDF-Parsing: Name, Notification Date, Transactions |
| `TestNormalizeTransactions` | CSV-Mapping: Name-Präferenz (XML > PDF), Disclosure Date Fallback |
| `TestDownloadFilingList` | ZIP/XML-Parsing: FilingType-Filter, Prefix-Filter, leere XML |
| `TestGetExistingDocIds` | Delta-Logik: DocID-Extraktion aus ptr_link, fehlende Datei |
| `TestBuildMemberName` | Name-Zusammensetzung aus First/Last |
| `TestXmlText` | Sicheres XML-Auslesen (missing/empty tags) |
| `TestSave` | Append-Modus, Dedup, NaN-Roundtrip-Bug, asset_name-Unterschied, DEDUP_SUBSET-Sync |
| `TestGetSummary` | Summary-Text mit/ohne Daten |
| `TestEdgeCases` | `Class P shares` False Positive, `Filing ID` ignoriert, Multiline-Block |
| `TestRealPdfText` | Regressionstests mit echten PDF-Texten (Aderholt, Allen, null bytes, S O: Zeilen) |
| `TestRunIntegration` | `run()` mit gemocktem HTTP: Delta-Skip, PDF-Fehler, vollständige Pipeline |
| `TestRequestWithRetry` | Retry-Logik: Sofort-Erfolg, Retry-dann-Erfolg, alle Versuche gescheitert |

---

## Diagnose

```powershell
# Einzelne PDF inspizieren (ohne zu speichern):
.\venv\Scripts\python.exe scrapers\house_scraper.py --doc-id 20024436 --year 2024 --dry-run

# Alle PTR-Filings eines Jahres auflisten:
.\venv\Scripts\python.exe scrapers\house_scraper.py --year 2024 --list-only

# Vollständiger Neu-Import (kein Delta):
.\venv\Scripts\python.exe scrapers\house_scraper.py --year 2024 --no-delta --dry-run
```

---

## Companion: House PDF Extractor (`scrapers/house_pdf_extractor.py`)

Companion-Tool für PDFs die der House Scraper nicht parsen kann (`reason="empty_text"` in `house_scraper_fehler.csv`). Das sind gescannte Papierformulare ("HAND DELIVERED") ohne Textlayer — OCR via Anthropic Vision API.

### Architektur

```
run()
 ├── Liest house_scraper_fehler.csv (filtert reason="empty_text")
 └── _process_pdf(doc_id, year, ...) × N
      ├── _download_pdf()         # HTTP-Download (gleiche URL wie House Scraper)
      ├── _pdf_to_images()        # pdfplumber → PIL Images (200 DPI)
      │    └── rotate(90°)        # Portrait-Seiten (width < height) = rotierte Landscape-Forms
      └── _extract_from_image()  # Anthropic Vision API → JSON
           └── _parse_api_response()  # Normalisierung → CSV_COLUMNS

save(df)   # Append + Dedup in house_trades.csv (identisches Schema wie HouseScraper)
```

### Unterstützte Formulartypen (getestet Feb 2026)

| Typ | Beschreibung | Getestete DocID | Ergebnis |
|-----|-------------|-----------------|----------|
| A | Rotated Handwritten (90° gedreht, handschriftlich, 1 Trade/Seite) | 8220764 (Adrian Smith) | ✅ 1 Trade |
| A+Note | Typ A mit Filer Notes (Trade-Details in Fußnote) | 8220747 (Gus Bilirakis) | ✅ 1 Trade |
| B | Landscape Table (maschinengetippt, viele Trades/Seite) | 8221228 (McCaul, 9 Seiten) | ✅ ~202 Trades |
| B | Landscape "Nothing to report" | 8221310 (Rogers) | ✅ 0 Trades |
| C | Hybrid (90° gedreht + maschinengetippt) | 9115670 (Fleischmann) | ✅ 12 Trades |

### Deduplication

Verwendet **denselben `DEDUP_SUBSET`** wie der House Scraper (Import aus `house_scraper.py`):
- Alle 11 Spalten außer `disclosure_date`
- Zwei-Stufen-Dedup: `run()` → `keep="first"` (innerhalb einer Session), `save()` → `keep="last"` (Cross-Session Append)
- Vor dem `drop_duplicates()` werden alle duplizierten Zeilen mit Name/Asset/Typ/Betrag/Datum geloggt (INFO-Level)

### Subholding-Header-Filter (Fix Feb 2026)

Seiten mit komplexen Subholding-Strukturen (z.B. McCaul Seite 9: "LINDA MAYS MCCAUL PARTNERS II LP") können fälschlich als Trade erkannt werden. Filter in `_parse_api_response()`:

```python
# Kein Datum + kein Amount-Code = kein echter Trade (nur Subholding-Header)
if not tx_date and not amount_code:
    continue
```

### Amount-Buchstaben → Dollar-Ranges

Papierformulare verwenden Checkboxen A-K statt Betragsfelder:
```
A=$1,001-$15,000  B=$15,001-$50,000  C=$50,001-$100,000  D=$100,001-$250,000
E=$250,001-$500,000  F=$500,001-$1M  G=$1M-$5M  H=$5M-$25M  I=$25M-$50M
J=Über $50M  K=Spouse/DC über $1M
```

### Bekannte Eigenheiten

- **Nicht-deterministisch**: Vision API (haiku) gibt bei mehreren Läufen leicht unterschiedliche Ergebnisse zurück (±5 Trades bei McCaul). Dedup fängt OCR-Doppellesungen ab.
- **Kein Ticker, kein Asset Type**: Papierformulare verlangen explizit "full name, not ticker symbol". Beide Felder bleiben leer.
- **Rotation**: `page.width < page.height` → 90° gegen Uhrzeigersinn (Portrait-PDFs sind rotierte Landscape-Formulare)
- **Datumsformat**: McCaul verwendet manchmal `MM-DD-YY` statt `MM/DD/YY` → wird in `_normalize_date()` abgefangen
