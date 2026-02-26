# Congress Trade Tracker - Projektstatus

## Projektbeschreibung
Python + Streamlit App die US-Kongress-Mitglieder Stock-Trades trackt, analysiert und Trading-Empfehlungen erstellt.

## Wichtige Regeln
1. **Alle Filter-Parameter muessen konfigurierbar sein** (in config/settings.py)
2. **Immer erst Implementierungsplan zeigen**, dann auf "OK" warten bevor Code geschrieben wird
3. **transaction_date verwenden**, nicht disclosure_date (45-Tage Meldefrist)
4. **Neue Dateien dokumentieren**: Bei Erstellung oder Verwendung neuer projektrelevanter Dateien (Daten, Config, Scripts) → in `organisation/DATA_STRUCTURE.md` aufnehmen (Struktur, Spalten, Zweck, Beispiele)

---

## Modul-Status

| Modul | Status | Beschreibung |
|-------|--------|--------------|
| 1 | FERTIG | Projektstruktur |
| 2 | FERTIG | House Trades Scraping (ZIP + PDF-Parsing, 122 Tests) |
| 3 | FERTIG | Senate Trades Scraping (Playwright + DataTables API) |
| 4 | FERTIG | Ausschuss-Sektor-Mapping (CommitteeMapper + CommitteeFilter implementiert) |
| 5 | ÜBERSPRUNGEN | Kursdaten (Yahoo Finance) - vorerst übersprungen |
| 6 | ÜBERSPRUNGEN | Abstimmungsdaten - vorerst übersprungen |
| 7 | OFFEN | Filter-Logik (Filter 1, 2, 3, 6, 7) |
| 8 | OFFEN | Scoring-System |
| 9 | OFFEN | Streamlit Dashboard |
| 10 | OFFEN | Email-Benachrichtigungen |
| 11 | OFFEN | Automatisierung & Scheduler |

---

## Wichtige Dateien

### Scripts
- `scripts/import_trades.py` - Haupt-Import-Pipeline: liest senate_trades.csv + house_trades.csv (nach jedem Scraper-Lauf ausfuehren)

### Utils
- `utils/name_matcher.py` - Fuzzy-Matching mit rapidfuzz (80% Threshold)

### Config
- `config/settings.py` - Alle konfigurierbaren Parameter
- `data/config/name_aliases.csv` - Manuelle Namenskorrekturen

### Daten
- `data/raw/house_member&(sub)committees.csv` - House Mitglieder mit Committees (aus PDF extrahiert)
- `data/raw/senate_member&(sub)committees.csv` - Senate Mitglieder mit Committees (aus PDF extrahiert)
- `data/raw/member&(sub)committees_structure.md` - Strukturdefinition für House/Senate Committee CSVs
- `data/raw/house_trades.csv` - House PTR Trades (via house_scraper.py)
- `data/raw/senate_trades.csv` - Senate PTR Trades (via senate_scraper.py)
- `data/raw/congress_trading_data_quiver.xlsx` - Quiver Trade-Daten (legacy, nicht mehr als Import-Quelle genutzt)
- `data/raw/yf_sectors_with_industries.csv` - Yahoo Finance Sektoren (11) mit Industries (176) **[CSV, empfohlen]**
- `data/raw/yf_secotors_with_industries.xlsx` - Originaldatei (hierarchisch strukturiert)
- `data/raw/subcommittee_sector_industry_mapping.csv` - Subcommittee→Sektor/Industry (861 Zeilen, normalisiert) **[CSV, empfohlen]**
- `data/raw/subcommittee_sector_industry_mapping_v3.xlsx` - Originaldatei (6 Sheets: Industry_Mapping, Region_Mapping, etc.)
- `data/processed/trades_master.csv` - Alle Trades mit Committee-Spalte
- `data/processed/unmatched_names.csv` - Namen ohne Match

### Datenstrukturen (xlsx & CSV)

#### Yahoo Finance Sektoren/Industries
- **xlsx**: `data/raw/yf_secotors_with_industries.xlsx` (hierarchisch organisiert)
- **CSV**: `data/raw/yf_sectors_with_industries.csv` (flache Struktur)
- **Struktur**: 2 Spalten (`Industry`, `Market Weight`)
  - Sektoren haben `100.00%` Market Weight (z.B. Technology, Financial Services)
  - Industries darunter haben prozentuale Anteile (z.B. Semiconductors 36.04%)
- **Inhalt**: 11 Haupt-Sektoren, ~176 Industries

#### Subcommittee → Sektor/Industry Mapping
- **xlsx**: `data/raw/subcommittee_sector_industry_mapping_v3.xlsx` (6 Sheets)
- **CSV (empfohlen)**: `data/raw/subcommittee_sector_industry_mapping.csv` (Industry_Mapping Sheet)
- **Struktur**: 7 Spalten (`Chamber`, `Committee`, `Subcommittee`, `Sector`, `Industry`, `Sector_Rank`, `Konfidenz`)
- **Inhalt**: 861 Zeilen (180 Subcommittees, normalisiert)
- **Weitere Sheets im xlsx**:
  - `Overview`: Breite Ansicht (bis zu 6 Sektoren pro Subcommittee in einer Zeile)
  - `Region_Mapping`: 238 Zeilen (USA, Global, Asia, etc.)
  - `Korrekturen`: Audit Trail (36 Korrekturen)
  - `Statistik`: Validierungsstatistiken
  - `Manuelle Prüfung`: 6 Foreign Affairs Committees

**CSV-Generierung**: `python scripts/convert_xlsx_to_csv.py` (aus aktiviertem venv)

---

## Workflow fuer Import (nach jedem Scraper-Lauf)

```bash
cd congress_tracker
.\venv\Scripts\activate

# 1. Scraper ausfuehren (erzeugt/aktualisiert data/raw/house_trades.csv + senate_trades.csv):
python scrapers/house_scraper.py
python scrapers/senate_scraper.py

# 2. Import ausfuehren:
python scripts/import_trades.py

# 2a. (Optional) CSV-Versionen aktualisieren falls xlsx-Dateien geaendert wurden:
python scripts/convert_xlsx_to_csv.py
```

**Hinweis:** Member-Listen (CSVs) werden direkt aus PDF extrahiert und liegen bereits in `data/raw/`. Bei neuen Wahlen: Neue PDFs extrahieren und CSVs ersetzen.

---

## Letzte Ergebnisse (24.01.2026)

- Total Trades: 108.982
- Erfolgreich gematcht: 89.561 (82%)
- Nicht gefunden: 19.421 (18%) - meist ehemalige Politiker
- Eindeutige unmatched Namen: 122

---

## Modul 4 - Committee-Sektor-Mapping (ABGESCHLOSSEN 20.02.2026)

**Implementierte Komponenten:**
- ✅ `config/committee_mapping.py` - CommitteeMapper lädt Mapping-CSV (861 Zeilen, 143 Subcommittees)
- ✅ `filters/committee_filter.py` - CommitteeParser + CommitteeFilter (Relevanz-Scoring 0-100)
- ✅ `config/settings.py` - Scoring Weights (Rank, Confidence, Role)
- ✅ `scripts/import_trades.py` - Integration in enrich_with_committees()
- ✅ Neue Spalten in trades_master.csv: Member_Committees_Full, Relevant_Sectors, Relevance_Score, Confidence_Level

**Scoring-Formel**: `score = base_score(rank) × confidence_mult × role_mult` (max 100)
- Rank Weights: 1=100%, 2=75%, 3=50%, 4=25%
- Confidence: Hoch=1.0x, Mittel=0.7x, Niedrig=0.4x
- Role: Alle Ex Officio (Chairman, Ranking Member, Vice Chairman) = 1.5x

**Tests**: ✅ CommitteeMapper + CommitteeFilter erfolgreich getestet

---

## Naechster Schritt

**Modul 7: Filter-Logik (Filter 1, 2, 6, 7)**
- Filter 5 (Contrarian) und Filter 6 (Timing vor Abstimmung) vorerst übersprungen
- Implementierung der verbleibenden Filter:
  - Filter 1: Volumen (config bereits in settings.py)
  - Filter 2: Schwarm-Verhalten (config bereits in settings.py)
  - Filter 3: Committee (✅ bereits implementiert)
  - Filter 6: Long↔Short Wechsel (config bereits in settings.py)
  - Filter 7: Position vergrößert (config bereits in settings.py)

---

## Vollstaendige Uebersicht

kompakte übersicht reicht in der regel, bei rückfragen frage user, siehe: README.md

detallierte Modulübersicht, Siehe: `Aufgaben_Uebersicht` im Projektordner

**Datenstruktur-Dokumentation**: Siehe `organisation/DATA_STRUCTURE.md` - Vollständige Übersicht aller Dateien, Spalten-Strukturen, Formate und Verwendungszwecke

