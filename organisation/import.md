# Modul: Filter 4 – Vote Timing Pipeline (Congress.gov API)

## Kontext & Ziel

Dieses Modul implementiert **Filter 4 – Vote Timing**: Erkennt Trades, die kurz vor/nach relevanten Abstimmungen getätigt wurden, über die Congress.gov API.

Die Pipeline ergänzt die bestehende `trades_master.csv` und integriert sich nahtlos in `scripts/import_trades.py`.

---

## Datenquelle

| Quelle | Zweck | Kosten |
|--------|-------|--------|
| `api.congress.gov/v3` | Bill-Daten, Vote-Dates, Policy Areas | Kostenlos, API-Key nötig (api.data.gov) |

---

## Neue Dateien (bitte in `organisation/DATA_STRUCTURE.md` aufnehmen)

```
config/
  settings.py                    ← Parameter ergänzen (bestehende Datei)
  vote_sector_mapping.py         ← NEU: 32 Policy Areas → YF-Sektoren

data/
  raw/
    congress_votes.csv           ← NEU: gecachte Vote-Daten aus API

filters/
  vote_timing_filter.py          ← NEU: Filter 4

scripts/
  fetch_congress_votes.py        ← NEU: API-Fetcher (pollt täglich)
```

---

## Parameter in `config/settings.py` (bitte ergänzen)

```python
# ── Filter 4: Vote Timing ──────────────────────────────────────────
CONGRESS_API_KEY = ""                  # api.data.gov Key (aus .env laden)
VOTE_TIMING_WINDOW_DAYS_BEFORE = 30   # Trade X Tage VOR Abstimmung
VOTE_TIMING_WINDOW_DAYS_AFTER  = 5    # Trade X Tage NACH Abstimmung
VOTE_TIMING_SCORE              = 25   # Bonus-Score wenn Treffer
VOTE_MIN_POLICY_CONFIDENCE     = 0.7  # Min. Konfidenz für Sektor-Match
```

---

## Vote Timing Filter (Filter 4)

### Wie die Congress.gov API funktioniert

```
GET https://api.congress.gov/v3/bill
    ?fromDateTime=2024-01-01T00:00:00Z
    &sort=updateDate
    &limit=250
    &api_key=KEY

→ Bill-Liste mit: introducedDate, policyArea.name, latestAction.actionDate
→ Pro Bill: GET /v3/bill/{congress}/{type}/{number}/actions
→ Actions filtern nach type="Floor" + recordedVotes vorhanden
→ actionDate = Vote-Datum ✓
```

### Policy Area → Sektor-Mapping (in `config/vote_sector_mapping.py`)

```python
POLICY_AREA_TO_SECTOR = {
    "Health":                              "Healthcare",
    "Energy":                              "Energy",
    "Science, Technology, Communications": "Technology",
    "Finance and Financial Sector":        "Financial Services",
    "Armed Forces and National Security":  "Industrials",   # Defense
    "Transportation and Public Works":     "Industrials",
    "Agriculture and Food":                "Consumer Defensive",
    "Environmental Protection":            "Utilities",
    "Commerce":                            "Consumer Cyclical",
    "Economics and Public Finance":        "Financial Services",
    "Housing and Community Development":   "Real Estate",
    # ... alle 32 Policy Areas mappen
}
```

### Neue Spalten in `trades_master.csv`

| Spalte | Typ | Beschreibung |
|--------|-----|--------------|
| `Vote_Timing_Hit` | bool | Trade im Zeitfenster einer relevanten Abstimmung |
| `Vote_Date` | date | Datum der nächsten relevanten Abstimmung |
| `Vote_Bill_Title` | str | Kurzname des Bills |
| `Vote_Policy_Area` | str | Policy Area des Bills (original aus API) |
| `Vote_Timing_Score` | int | Scoring-Bonus (0 oder VOTE_TIMING_SCORE) |
| `Days_To_Vote` | int | Abstand Trade→Vote (negativ = vor Abstimmung) |

### Implementierungslogik (`filters/vote_timing_filter.py`)

```python
def apply_vote_timing_filter(trades_df, votes_df):
    """
    Für jeden Trade:
    1. Sektor des Trades ermitteln (aus Relevant_Sectors Spalte)
    2. Alle Votes mit passendem Sektor suchen
    3. Zeitfenster prüfen: 
       transaction_date ZWISCHEN (vote_date - BEFORE) UND (vote_date + AFTER)
    4. Bei Treffer: Vote_Timing_Hit=True, Score addieren
    """
```

**Wichtig:** `transaction_date` verwenden (nicht disclosure_date) — konsistent mit restlichem Projekt.

## Integration in bestehende Pipeline

```bash
# Erweiterter Workflow nach diesem Modul:

# 1. (Täglich via Scheduler) Vote-Daten aus API cachen
python scripts/fetch_congress_votes.py

# 2. Scraper wie gehabt
python scrapers/house_scraper.py
python scrapers/senate_scraper.py

# 3. Import (jetzt inkl. Vote-Timing-Filter)
python scripts/import_trades.py
```

---

## Implementierungsreihenfolge

1. `config/vote_sector_mapping.py` — alle 32 Policy Areas mappen
2. `scripts/fetch_congress_votes.py` — API-Fetcher mit lokalem Cache (`congress_votes.csv`)
3. `filters/vote_timing_filter.py` — Filter-Logik mit konfigurierbarem Zeitfenster
4. Integration in `scripts/import_trades.py` — neue Spalten hinzufügen
5. `organisation/DATA_STRUCTURE.md` — neue Dateien dokumentieren

---

## Offene Fragen vor Start

- **API-Key:** Bereits bei api.data.gov registriert oder soll das Skript einen Hinweis ausgeben?
- **Senate Votes:** Congress.gov API hat keinen Senate-Vote-Endpoint → Senate-Votes aus XML-URLs in Bill-Actions parsen (etwas aufwändiger) oder zunächst nur House-Votes implementieren?