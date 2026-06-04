# Scraper Problems - Vollstaendige Uebersicht

Stand: 2026-03-21

---

## 1. House Scraper (`scrapers/house_scraper.py`)

### 1.1 Gescannte PDFs ohne Textlayer — AKTIV
- **Symptom:** 58 PDFs als `empty_text` in `house_scraper_fehler.csv` geflaggt
- **Ursache:** Handschriftliche Paper-PTR-Formulare (Bild-Scans ohne eingebetteten Text). pdfplumber findet keinen Text.
- **Betroffene Members:** 14 — Rohit Khanna (12 PDFs), Harold Rogers (12), Michael McCaul (12), Adrian Smith (3), Chuck Fleischmann (4), Vicente Gonzalez (3), Brad Sherman (3), Ann Wagner (2), Keith Self (2), Nicole Malliotakis (2), Gus Bilirakis (1), Jamie Raskin (1), Lisa McClain (1), Scott Peters (1 Download-Fehler)
- **Geschaetzte Trades:** ~8.200 Trades betroffen
- **Status:** Teilweise geloest durch `house_pdf_extractor_v2.py` (Grid-OCR). Extrahierte v2-Trades sind aktuell in `data/raw/house_pdf.csv` gespeichert (6.829 Trades), noch nicht in `trades_master.csv` integriert.

### 1.2 Fehlende Ticker — DESIGNBEDINGT
- **Symptom:** 51% der House-Trades haben keinen Ticker
- **Ursache:** Paper-PTR-Formulare fragen explizit nach "full name, NOT ticker symbol". Der Scraper (und OCR) kann keinen Ticker extrahieren, der nicht im Formular steht.
- **Workaround:** Asset-Name-Matching als Fallback. Ticker-Enrichment wuerde separate Lookup-Tabelle erfordern (nicht implementiert).

### 1.3 Netzwerkfehler — INTERMITTIEREND
- **Symptom:** DNS-Fehler "Failed to resolve 'disclosures-clerk.house.gov'"
- **Ursache:** Netzwerk/DNS-Ausfaelle waehrend des Scraper-Laufs
- **Status:** Retry-Logik greift (3 Versuche, 5s Pause). Kein Datenverlust.

### 1.4 Subholding-Parsing — GELOEST (Feb 2026)
- **Symptom:** 241 echte Trades als Duplikate entfernt (S O:-Zeilen weggefiltert)
- **Fix:** `subholding`-Spalte eingefuehrt, S O:-Zeilen werden geparst, IVD-Section bricht loop ab
- **Verifiziert:** DocID 20024436 → 93 Trades (vorher 57)

### 1.5 Self/Spouse Dedup — GELOEST (Feb 2026)
- **Symptom:** Self- und Spouse-Trades derselben Aktie faelschlich als Duplikate entfernt
- **Fix:** `owner` zu `DUPLICATE_CHECK_COLUMNS` hinzugefuegt

---

## 2. Senate Scraper (`scrapers/senate_scraper.py`)

### 2.1 Fehlende Ticker — DESIGNBEDINGT
- **Symptom:** 37% der Senate-Trades haben keinen Ticker (464 von 1.244)
- **Ursache:** Senate e-Filing-System erfordert keinen Ticker, nur Asset-Namen
- **Status:** Design-Limitation, kein Bug

### 2.2 Bond-Metadaten im Asset-Name — GELOEST (Feb 2026)
- **Symptom:** Rate/Coupon/Maturity-Daten waren im `asset_name`-Feld statt in `comment`
- **Beispiel:** "PARAMOUNT GLOBAL BOND 6.875% Matures: 04/30/2036" komplett in asset_name
- **Fix:** `div.text-muted`-Selektor trennt Metadaten → `comment`-Spalte
- **Ort:** `senate_scraper.py`, `_extract_transactions()`

### 2.3 Self/Spouse Dedup — GELOEST (Feb 2026)
- **Identisch mit House-Problem 1.5**

### 2.4 Paper-PTRs (Richard Blumenthal) — IRRELEVANT
- **Symptom:** 24 Paper-PTR-Links in `senate_pdf_skipped.csv` (alle Blumenthal, Jan-Feb 2026)
- **Ursache:** Einziger Senator mit Paper-PTRs investiert ausschliesslich in Fonds
- **Status:** `senate_pdf_extractor.py` funktional, aber irrelevant fuer Trading-Signale

---

## 3. House PDF Extractor v2 (`scrapers/house_pdf_extractor_v2.py`)

### 3.1 Amount-NaN bei Typ D (Broker Statements) — AKTIV, HOCH
- **Symptom:** 37.8% der Typ-D-Trades haben keinen Amount (NaN)
- **Ursache:** Grid-Detection + Pixel-Density versagt bei rotierten Broker-Statements (Khanna) mit dichten, ueberlappenden Tabellen. Checkbox-Zellen zu klein/unscharf fuer zuverlaessige Pixel-Zaehlung.
- **Betroffene Trades:** ~2.500 von 6.622 Typ-D-Trades
- **Fehlerraten nach Formulartyp:**

| Typ | Trades | Amount NaN | Truncated | Merged |
|-----|--------|-----------|-----------|--------|
| A (handschriftlich) | 202 | 16.8% | 0 | 0 |
| B (Landscape-Tabelle) | 1.329 | 12.2% | 0 | 0 |
| C (Hybrid rotiert) | 77 | 0.0% | 0 | 0 |
| D (Broker Statement) | 6.622 | 37.8% | 23 | 65 |

### 3.2 Vision API Halluzinationen — AKTIV, HOCH
- **Symptom:** Erfundene Asset-Namen ("Fruits Services New Smalbarge Account", "Gambling Building Company") und falsche Jahres-Interpretation (1925 statt 2025)
- **Ursache:** Bei schlechter Scan-Qualitaet oder ungewoehnlichem Layout erfindet das Vision-Modell Inhalte
- **Betroffenes Beispiel:** PDF 8221212 (Lisa McClain) — 50 von 165 Trades mit halluzinierten Asset-Namen und 1925-Daten
- **Gegenmassnahmen implementiert:**
  - Prompt-Regel: "2-stellige Jahre IMMER als 20XX, NIEMALS 19XX"
  - `readable`-Flag pro Zeile: unsichere Trades → `logs/ocr_uncertain.csv`
  - Datum-Validierung in `import_trades.py`: `transaction_date > disclosure_date` → rejected

### 3.3 Bond-Maturity als Transaction-Date — AKTIV
- **Symptom:** Faelligkeitsdaten von Anleihen (2029, 2032, 2055) landen als `transaction_date`
- **Ursache:** Auf Paper-Formularen steht oft nur das Maturity-Date, nicht das Kaufdatum. OCR kann beides nicht unterscheiden.
- **Betroffene Members:** Rohit Khanna, Sheri Biggs, Julia Letlow, Ann Wagner, Rob Bresnahan
- **Gegenmassnahme:** Datum-Validierung in `import_trades.py` (`TRADE_DATE_WINDOW_AFTER_DAYS = 0` → jede transaction_date nach disclosure_date wird rejected)

### 3.4 Grid-Detection Fallback — EDGE CASE
- **Symptom:** Tabelle nicht erkannt → Fallback auf ganzes Seitenbild (wie v1)
- **Ursache:** Morphologische Linienerkennung findet bei stark rotierten/verzerrten Seiten kein Grid
- **Status:** Fallback funktioniert, wird im Terminal geloggt. Qualitaet des Fallback ist geringer als Grid-Pfad.

### 3.5 JSON-Parse-Fehler — INTERMITTIEREND
- **Symptom:** Vision API gibt unvollstaendiges/kaputtes JSON zurueck
- **Ursache:** Modell-Halluzination oder Token-Limit erreicht
- **Status:** Exception-Handling vorhanden, betroffene Zeile wird uebersprungen. Selten (<1%).

### 3.6 Fehlende Transaction-Dates — AKTIV
- **Symptom:** 363 OCR-Trades hatten kein `transaction_date` (Feld leer)
- **Ursache:** Handschrift unleserlich, Datumsfeld nicht gefuellt, oder OCR konnte Datum nicht extrahieren
- **Gegenmassnahme:** Fallback in `import_trades.py` → `disclosure_date` wird als Ersatz eingesetzt
- **Betroffene Members:** Rohit Khanna (Mehrheit), Bilirakis, Fleischmann u.a.

---

## 4. House PDF Extractor v1 (`scrapers/house_pdf_extractor.py`) — LEGACY

### 4.1 Ganzseitige Vision-API — DESIGNPROBLEM
- **Symptom:** Merge- und Truncation-Fehler bei dichten Tabellen (65 merged, 23 truncated)
- **Ursache:** v1 schickt die gesamte Seite an die Vision API statt einzelne Zeilen-Batches
- **Status:** ERSETZT durch v2. v1 existiert noch als Fallback, v2 ist empfohlen.

### 4.2 Name-Halluzinationen — DESIGNPROBLEM (v1-spezifisch)
- **Symptom:** Vision API erfindet Member-Namen ("Rishi Sharma" statt "Rohit Khanna", "UNKNOWN")
- **Ursache:** v1 liest den Namen aus dem Bild (Vision API). v2 verwendet den Namen aus XML-Metadata.
- **Auswirkung:** v1-Trades konnten nie in trades_master importiert werden (NameMatcher fand keinen Match)
- **Status:** GELOEST in v2 — Name kommt aus `house_scraper_fehler.csv` (XML-Quelle, 100% Match-Rate)

---

## 5. Senate PDF Extractor (`scrapers/senate_pdf_extractor.py`)

### 5.1 Vision API unzuverlaessig fuer Senate-Papers — IRRELEVANT
- **Symptom:** Fehlerhaftes Lesen gescannter Senate-PDFs
- **Ursache:** GIF-Bilder aus DOM extrahiert (andere Struktur als House-PDFs)
- **Status:** Irrelevant — einziger betroffener Senator (Richard Blumenthal) handelt nur Fonds

---

## 6. Datenqualitaet (trades_master.csv)

### 6.1 Ticker-Reuse / Delisting → falsche Kurshistorie im Preis-Cache — OFFEN
- **Symptom:** Delistete/wiederverwendete Ticker werden im `data/cache/price_history.parquet`-Cache mit der Kurshistorie eines NEUEN, anderen Unternehmens desselben Symbols gematcht
- **Belege:** Ticker `NYC` (Baseline $1,77 → ausgewiesener Return +466 %), Ticker `PW` (Baseline $0,53 → +302 %)
- **Erkennungsmerkmal:** Extrem niedrige Baseline-Preise + absurd hohe Post-Trade-Returns (Dezimalstellen-Bereich)
- **Ursache:** yfinance liefert für wiederverwendete Symbole die Historie des NEUEN Unternehmens, nicht des Ursprungs-Tickers zum Zeitpunkt des Trades
- **Auswirkung:** Verfälscht einzelne Post-Trade-Returns in der Streamlit-App (Asset- und Member-Aggregate, Post-Trade-Performance-Tab). **NICHT im Scoring enthalten** (post_return_* sind display-only)
- **Status:** OFFEN, nicht gefixt (bewusst zurückgestellt, Priorität niedrig)

### 6.2 Fehlende Ticker (kombiniert) — DESIGNBEDINGT
- **Anzahl:** ~49% aller Trades ohne Ticker
- **Aufschluesselung:** House 51% (Paper-Formulare), Senate 37% (e-Filing erlaubt kein Ticker)
- **Auswirkung:** Ticker-basierte Analyse (Filter, Scoring) nicht moeglich fuer diese Trades

### 6.2 Asset-Name-Varianten — MINOR
- **Symptom:** Gleiche Aktie mit leicht unterschiedlichen Namen ("GE Vernova LLC" vs "GE Vermova LLC")
- **Ursache:** OCR-Fehler bei handschriftlichen Formularen, Tippfehler in Original-Filings
- **Haeufigkeit:** ~2-5 pro Formulartyp, sehr geringer Prozentsatz

### 6.3 Late-Filer (>180 Tage Gap) — DATENREALITAET
- **Symptom:** 851 Trades mit >180 Tage Abstand zwischen transaction_date und disclosure_date
- **Ursache:** Politiker melden oft deutlich spaeter als die gesetzlichen 45 Tage
- **Beispiele:** Markwayne Mullin (952 Tage), Rick Scott (434 Tage), Julia Letlow, Valerie Hoyle
- **Status:** Kein Bug — reale Verzoegerungen. Datum-Validierung mit `WINDOW_BEFORE = 180` lehnt diese ab. Fenster kann in `settings.py` angepasst werden.

---

## Zusammenfassung: Offene Probleme nach Prioritaet

| Prio | Problem | Betroffene Trades | Komponente |
|------|---------|-------------------|------------|
| HOCH | Amount-NaN Typ D (37.8%) | ~2.500 | house_pdf_extractor_v2 |
| HOCH | Vision API Halluzinationen | ~50-100 | house_pdf_extractor_v2 |
| MITTEL | Bond-Maturity als Transaction-Date | ~100 | house_pdf_extractor_v2 |
| MITTEL | Fehlende Ticker (49%) | ~8.000 | Alle Scraper (Design) |
| NIEDRIG | Grid-Detection Fallback | Edge Cases | house_pdf_extractor_v2 |
| NIEDRIG | Late-Filer (>180 Tage) | ~850 | Datenrealitaet |
| IRRELEVANT | Senate Paper-PTRs | 24 PDFs | senate_pdf_extractor |
