# Pitch: Filter-Workflow für Folge-Agent

**Datum**: 2026-05-15
**Adressat**: Nächster Agent, der an Modul 7 (Filter-Logik) weiterarbeitet
**Zweck**: Schneller Einstieg ohne Re-Exploration des Codes.

---

## Worum geht's

Modul 7 (Filter-Logik) ist halbfertig. Drei Filter laufen, vier fehlen. Du wirst die fehlenden Filter implementieren — danach folgt Modul 8 (Scoring).

## Wo wir vorher standen (zwei abgeschlossene Vorarbeiten)

Vor der eigentlichen Filter-Arbeit gab es zwei größere Refactorings, die du kennen musst, weil sie das Daten-Schema und die Spaltennamen geändert haben:

### 1. `figi_search`-Fix (Anfang 2026-05-14)
- **Problem**: 99,9 % der OpenFIGI-Spalten im Master waren leer, die wenigen Treffer waren oft Falsch-Matches (Bonds als Preferred, Munis als Unit etc.).
- **Lösung**:
  - Classifier (`utils/ticker_classifier.py`) hat jetzt einen **Muni-Override** (asset_name-Marker → BOND/SKIP) und routet When-Issued zu SKIP statt zu Search.
  - Preferred-Pfade laufen jetzt über `FIGI_MAPPING` mit Bloomberg-Composite-Form (z. B. `WRB-PG → WRB/PG`).
  - Search-Body (`utils/ticker_normalizer.py:_process_search`) hat `marketSecDes`-Filter + Result-Validierung gegen Whitelist statt blindem `data_list[0]`.
  - `_clean_query_name` schneidet Suffixe wie `CMN`, `DEPOSITARY SHARES`, `CORPORATION` ab.
- **Ergebnis**: Falsch-Matches → 0, Preferred-FIGI-Coverage 47 %, alle Treffer mit `market_sector_figi='Pfd'` plausibilisiert.
- **Tests**: 86 Classifier-Tests, neue Klasse `TestMuniOverride`.

### 2. Spalten-Refactoring `_figi` / `_yf` (Ende 2026-05-14)
- **Problem**: Spaltennamen waren mehrdeutig (`market_sector` ≠ `Ticker_Sector`, `security_type` ≠ `ticker_type`), Konvention inkonsistent.
- **Lösung** (drei Ebenen: CSV, Python-Dataclasses, SQLite-DB):
  - OpenFIGI-Spalten: `canonical_ticker → ticker_figi`, `canonical_name → name_figi`, `security_type → security_type_figi`, `market_sector → market_sector_figi`. (`figi` selbst bleibt.)
  - yfinance-Spalten: `Ticker_Sector → sector_yf`, `Ticker_Industry → industry_yf`, `quote_type → quote_type_yf`.
  - SQLite-Tabelle `ticker_normalizer` per DROP+CREATE migriert (Backup unter `data/cache/ticker_type_cache.db.bak_rename_*`).
- **Konsequenz für dich**: Alle Filter-Module müssen die **neuen Spaltennamen** lesen. `Ticker_Sector` existiert nicht mehr — verwende `sector_yf`.

---

## Aktueller Filter-Stand (Stand 2026-05-15)

| Filter | Code | Master-Output |
|---|---|---|
| 0 Asset-Klasse | ✅ `filters/asset_class_filter.py` | `asset_class_excluded` |
| 1 Volumen | ⏳ **fehlt** | — |
| 2 Schwarm | ⏳ **fehlt** | — |
| 3 Committee | ✅ `filters/committee_filter.py` | `Insider_Sectors`, `Relevant_Sectors`, `Relevance_Score` |
| 4 Vote-Timing | ✅ `filters/vote_timing_filter.py` | `Vote_Timing_Hit`, `Vote_Timing_Hits`, `Vote_Timing_Hit_Count` |
| 5 Contrarian | ⏸️ braucht Modul 5 (Kursdaten) | — |
| 6 Long/Short | ⏳ **fehlt** | — |
| 7 Position+ | ⏳ **fehlt** | — |

**Master-Datei**: `data/processed/trades_master_complete.csv` (111.986 Trades).
**Pipeline-Skript**: `scripts/import_trades.py` mit 8 Stages, Filter-Stage heißt `_stage_filter` (aufgerufen via `--stage_filter`).

---

## Empfohlene Reihenfolge

1. **Filter 1 (Volumen, ~1 h)** — kleinster Hebel, einfachste Logik. Schwellen liegen in `settings.VOLUME_THRESHOLDS`. Liest `amount`-Spalte. Schreibt `Volume_Score` (oder ähnlich).
2. **Filter 7 (Position vergrößert, ~3 h)** — pro Member+Ticker ≥ `POSITION_INCREASE_MIN_BUYS` Käufe im `POSITION_INCREASE_MONTHS`-Fenster. Vorbild für Aggregations-Logik.
3. **Filter 2 (Schwarm, ~5 h)** — größter Score-Beitrag (30 %). Cross-Member-Auswertung: ≥ `SWARM_MIN_POLITICIANS` Politiker handeln denselben Ticker innerhalb `SWARM_DAYS`.
4. **Filter 6 (Long/Short, ~2 h)** — pro Member+Ticker Richtungswechsel innerhalb `LONG_SHORT_MONTHS`.

Danach: **Modul 8 Scoring** (Verzeichnis `scoring/` neu anlegen, gewichtete Summe aus `settings.WEIGHTS`, Klassifikation per `SCORE_THRESHOLDS`).

---

## Konventionen (verbindlich)

1. **Alle Schwellen aus `config/settings.py`** — niemals hardcoden (CLAUDE.md Regel 1).
2. **Spaltennamen**: externe-API-Felder haben Provider-Suffix. Filter-Code liest `sector_yf` / `industry_yf` (nicht `Ticker_Sector`).
3. **Vor Code-Änderung Implementierungsplan zeigen** und auf User-OK warten (CLAUDE.md Regel 2). Bei Filter-Modulen sinnvoll: Plan-Datei mit Input-Spalten, Output-Spalten, Edge-Cases (z. B. fehlende `amount`).
4. **Dokumentation mitpflegen**: Bei jedem neuen Filter mindestens eine von `DATA_STRUCTURE.md`, `Pipelines.md`, `FILTER_PARAMETER.md` aktualisieren (CLAUDE.md Regel 5).
5. **Filter-Stage-Output testen**: pytest für neue Filter im Stil von `tests/test_ticker_classifier.py` (Parametrized-Cases, gemockte DataFrames).

---

## Wichtige Files (Quick-Reference)

| Pfad | Zweck |
|---|---|
| `scripts/import_trades.py` | Pipeline-Orchestrator. Filter-Stage = `_stage_filter` / `_apply_filter_logic` (ab Z. 926). Neue Filter dort einreihen. |
| `config/settings.py` | Alle Schwellen, Gewichte, Pfade. `WEIGHTS`, `VOLUME_THRESHOLDS`, `SWARM_DAYS`, etc. |
| `filters/asset_class_filter.py` | Vorbild für einfache Filter-Klasse mit `apply(df) -> df`. |
| `filters/committee_filter.py` | Vorbild für komplexe Filter mit Mapping-Lookups. |
| `filters/vote_timing_filter.py` | Vorbild für Filter mit externer Datenquelle (JSON-Files). |
| `organisation/FILTER_PARAMETER.md` | Detaillierte Filter-Spec (Schwellen, Gewichte). Eben aktualisiert (2026-05-15). |
| `organisation/DATA_STRUCTURE.md` | Alle CSV-Spalten dokumentiert. Schau in den Bereich "trades_master_complete.csv". |
| `organisation/Pipelines.md` | Stage-Ablauf. Filter-Stage = Stage [7]. |
| `data/processed/trades_master_complete.csv` | Aktueller Datensatz, 111k Trades, mit neuen `_figi`/`_yf`-Spalten. |

---

## Was DU NICHT machen solltest

- Nicht die `_figi` / `_yf`-Spalten zurück umbenennen — die Konvention ist neu und absichtlich.
- Nicht den Cache `data/cache/ticker_type_cache.db` löschen (kostet ~5 Min API-Calls beim nächsten Re-Fill, nicht kritisch, aber unnötig).
- Filter 5 nicht in Angriff nehmen — blockiert durch fehlende Kursdaten (Modul 5 übersprungen).
- Filter 4 nicht „verbessern" — das läuft. Nice-to-have wäre echte Roll-Call-Extraktion (Modul 6), aber das ist eine eigene Initiative.

---

## Letzter Verifikations-Lauf (vor Übergabe)

```
pytest tests/test_ticker_*.py    →  98 passed
python scripts/import_trades.py  →  ok (Full Rebuild, vom User durchgeführt)
```

Übergabezustand: alle Stages laufen, neuer Master geschrieben, Doku synchronisiert. Du kannst direkt mit Filter 1 starten.
