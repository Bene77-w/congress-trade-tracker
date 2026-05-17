# Multiplikatoren & Punktesysteme für alle Filter

**Erstellt:** 2026-02-20
**Letzte Aktualisierung:** 2026-05-15 (Status-Korrektur — Trennung Parameter ↔ Filter-Code)
**Quelle:** `config/settings.py`, `Aufgaben_Uebersicht`, `CLAUDE.md`

> **Lesehilfe**: „Parameter implementiert" = Konstanten/Schwellen liegen in `config/settings.py`.
> „Filter-Code implementiert" = es existiert ein Modul in `filters/`, das die Logik berechnet und die Ergebnis-Spalten in den Master schreibt. Beide Zustände sind unabhängig.

---

## Filter-Übersicht

### **Filter 0: Asset-Klasse** (nicht in der historischen 7-Filter-Liste)
**Status:** Parameter ✅ / Filter-Code ✅ — aktiv in Pipeline (`filters/asset_class_filter.py`)

Schließt Nicht-Aktien (ETF, Option, Bond, Crypto, Unit, Warrant, when_issued, …) vom Scoring aus. Schreibt `asset_class_excluded` in den Master. Behält per Default `{stock, foreign_stock, shareclass}` (`settings.FILTER_0_KEEP_TYPES`).

---

### **Filter 1: Handelsvolumen**
**Status:** Parameter ✅ / Filter-Code ⏳ ausstehend

| Betragsbereich | Punkte | Settings |
|----------------|--------|----------|
| < $15.000 | 0 | `VOLUME_THRESHOLDS[0] = 15_000` |
| $15k - $50k | 1 | `VOLUME_THRESHOLDS[1] = 50_000` |
| $50k - $100k | 2 | `VOLUME_THRESHOLDS[2] = 100_000` |
| $100k - $250k | 3 | `VOLUME_THRESHOLDS[3] = 250_000` |
| > $250k | 4 | `VOLUME_THRESHOLDS[4] = float('inf')` |

Erwartetes Modul: `filters/volume_filter.py`. Liest `amount`-Spalte aus Master.

---

### **Filter 2: Schwarm-Verhalten**
**Status:** Parameter ✅ / Filter-Code ⏳ ausstehend

| Parameter | Wert | Settings |
|-----------|------|----------|
| Zeitraum | 21 Tage | `SWARM_DAYS = 21` |
| Mindestanzahl Politiker | 3 | `SWARM_MIN_POLITICIANS = 3` |

Erwartetes Modul: `filters/swarm_filter.py`. Cross-Member-Auswertung pro Ticker × Zeitfenster.

---

### **Filter 3: Committee-Sector Mapping**
**Status:** Parameter ✅ / Filter-Code ✅ — aktiv (`filters/committee_filter.py`, Modul 4)

Schreibt `Insider_Sectors`, `Relevant_Sectors`, `Relevance_Score` in den Master.
Match-Vergleich gegen `sector_yf` / `industry_yf` (umbenannt 2026-05-14, vorher `Ticker_Sector`/`Ticker_Industry`).

#### **Sector_Rank Weights** (Base Score)
| Rank | Bedeutung | Weight | Implementiert |
|------|-----------|--------|---------------|
| Rank 1 | Primärer Sektor | 100% | ✅ `COMMITTEE_RANK_WEIGHTS[1] = 1.00` |
| Rank 2 | Sekundärer Sektor | 75% | ✅ `COMMITTEE_RANK_WEIGHTS[2] = 0.75` |
| Rank 3 | Tertiärer Sektor | 50% | ✅ `COMMITTEE_RANK_WEIGHTS[3] = 0.50` |
| Rank 4+ | Weitere Sektoren | 25% | ✅ `COMMITTEE_RANK_WEIGHTS[4] = 0.25` |

#### **Confidence Multiplier**
| Konfidenz | Multiplier | Implementiert |
|-----------|------------|---------------|
| Hoch | 1.0x | ✅ `COMMITTEE_CONFIDENCE_WEIGHTS['Hoch'] = 1.0` |
| Mittel | 0.7x | ✅ `COMMITTEE_CONFIDENCE_WEIGHTS['Mittel'] = 0.7` |
| Niedrig | 0.4x | ✅ `COMMITTEE_CONFIDENCE_WEIGHTS['Niedrig'] = 0.4` |

#### **Role Multiplier**
| Rolle | Multiplier | Implementiert |
|-------|------------|---------------|
| Chairman | 1.5x | ✅ `COMMITTEE_ROLE_WEIGHTS['Chairman'] = 1.5` |
| Ranking Member | 1.5x | ✅ `COMMITTEE_ROLE_WEIGHTS['Ranking Member'] = 1.5` |
| Vice Chairman | 1.5x | ✅ `COMMITTEE_ROLE_WEIGHTS['Vice Chairman'] = 1.5` |
| Cochairman | 1.5x | ✅ `COMMITTEE_ROLE_WEIGHTS['Cochairman'] = 1.5` |
| EXOfficio | 1.5x | ✅ `COMMITTEE_ROLE_WEIGHTS['EXOfficio'] = 1.5` |
| Regular Member | 1.0x | ✅ `COMMITTEE_ROLE_WEIGHTS[''] = 1.0` |

**Scoring-Formel:**
```python
sector_score = base_score(rank) × confidence_mult × role_mult
# Beispiel: Rank 1, Hoch, Chairman = 100 × 1.0 × 1.5 = 150 (capped at 100)
```

**Threshold:**
- ✅ `COMMITTEE_RELEVANCE_THRESHOLD = 30` (Minimum Score für Relevanz)

---

### **Filter 4: Timing vor Abstimmung**
**Status:** Parameter ✅ / Filter-Code ✅ — aktiv (`filters/vote_timing_filter.py`)

| Parameter | Wert | Settings |
|-----------|------|----------|
| Zeitfenster MIN | 60 Tage VOR Abstimmung | `TIMING_DAYS_MIN = 60` |
| Zeitfenster MAX | 90 Tage VOR Abstimmung | `TIMING_DAYS_MAX = 90` |

**Datenquelle**: `data/congress_api/bill_actions.json` + `bill_subjects.json` (gefetcht via `scrapers/fetch_congress_data.py`). Pre-Stage-Hook hält die Files frisch (TTL = `settings.VOTE_DATA_TTL_HOURS`). Aktuelle Coverage: ~5 % der Trades mit Timing-Hit (siehe letzter Run-Log). Verbesserung der Vote-Daten (echte Roll-Calls statt nur Bill-Actions) steht in Modul 6 noch offen, blockiert aber das Signal nicht.

---

### **Filter 5: Contrarian**
**Status:** ⏸️ ÜBERSPRUNGEN (benötigt Modul 5: Kursdaten)

| Parameter | Wert | Implementiert |
|-----------|------|---------------|
| Kauf-Signal | Aktie -15% (12 Monate) | ✅ `CONTRARIAN_BUY_THRESHOLD = -0.15` |
| Verkauf-Signal | Aktie +30% (12 Monate) | ✅ `CONTRARIAN_SELL_THRESHOLD = 0.30` |
| Betrachtungszeitraum | 12 Monate | ✅ `CONTRARIAN_LOOKBACK_MONTHS = 12` |

**Hinweis:** Parameter definiert, Filter-Logik nicht implementiert.

---

### **Filter 6: Long/Short Wechsel**
**Status:** Parameter ✅ / Filter-Code ⏳ ausstehend

| Parameter | Wert | Settings |
|-----------|------|----------|
| Zeitraum | 6 Monate | `LONG_SHORT_MONTHS = 6` |

Erwartetes Modul: `filters/direction_change_filter.py`. Pro Member+Ticker prüfen, ob `transaction_type` innerhalb des Fensters von Purchase auf Sale (oder umgekehrt) wechselt.

---

### **Filter 7: Position vergrößert**
**Status:** Parameter ✅ / Filter-Code ⏳ ausstehend

| Parameter | Wert | Settings |
|-----------|------|----------|
| Zeitraum | 4 Monate | `POSITION_INCREASE_MONTHS = 4` |
| Mindestanzahl Käufe | 2 | `POSITION_INCREASE_MIN_BUYS = 2` |

Erwartetes Modul: `filters/position_increase_filter.py`. Pro Member+Ticker ≥2 Käufe im Fenster.

---

## **GESAMT-SCORING (Modul 8)**

> **Status auf einen Blick**: Alle Gewichte/Schwellen liegen als Konstanten in `config/settings.py` (`WEIGHTS`, `SCORE_THRESHOLDS`, `EMAIL_ALERT_THRESHOLD`). Das eigentliche **Scoring-Modul (`scoring/`) existiert noch nicht** — gewichtete Summe, Score-Klassifikation, Aggregation sind nicht implementiert.

### **Gewichtungen** (Summe = 100%)

| Filter | Gewicht | Settings | Filter-Code |
|--------|---------|----------|-------------|
| Filter 1 - Volumen | 15 % | `WEIGHTS['volume'] = 0.15` | ⏳ |
| Filter 2 - Schwarm | 30 % | `WEIGHTS['swarm'] = 0.30` | ⏳ |
| Filter 3 - Ausschuss | 30 % | `WEIGHTS['committee'] = 0.30` | ✅ aktiv |
| Filter 4 - Timing | — | nicht in `WEIGHTS` | ✅ aktiv (zählt als Auto-Flag, nicht im gewichteten Score) |
| Filter 5 - Contrarian | — | nicht in `WEIGHTS` | ⏸️ benötigt Modul 5 |
| Filter 6 - Long/Short | 15 % | `WEIGHTS['long_short'] = 0.15` | ⏳ |
| Filter 7 - Position vergrößert | 10 % | `WEIGHTS['position_increase'] = 0.10` | ⏳ |
| **SUMME** | **100 %** | | |

### **Score-Schwellen & Empfehlungen**

| Score | Empfehlung | Settings |
|-------|------------|----------|
| 75-100 | ⭐⭐⭐ Starke Empfehlung | `SCORE_THRESHOLDS['strong'] = 75` |
| 50-74  | ⭐⭐ Interessant         | `SCORE_THRESHOLDS['interesting'] = 50` |
| 30-49  | ⭐ Beobachten           | `SCORE_THRESHOLDS['watch'] = 30` |
| 0-29   | Kein Signal             | `SCORE_THRESHOLDS['none'] = 0` |

### **Email-Alert Schwelle**

| Parameter | Wert | Settings |
|-----------|------|----------|
| Email-Alert ab Score | 50 | `EMAIL_ALERT_THRESHOLD = 50` |

---

## 🔍 IMPLEMENTIERUNGSSTATUS (Stand 2026-05-15)

### Parameter in `config/settings.py`
Alle Filter (0–7) plus Scoring-Gewichte, Score-Schwellen und Email-Alert: **vollständig definiert**.

### Filter-Code in `filters/`
| Filter | Modul | Status | Master-Output |
|--------|-------|--------|---------------|
| Filter 0 — Asset-Klasse   | `asset_class_filter.py`  | ✅ aktiv     | `asset_class_excluded` |
| Filter 1 — Volumen        | `volume_filter.py`       | ⏳ fehlt     | — |
| Filter 2 — Schwarm        | `swarm_filter.py`        | ⏳ fehlt     | — |
| Filter 3 — Committee      | `committee_filter.py`    | ✅ aktiv     | `Insider_Sectors`, `Relevant_Sectors`, `Relevance_Score` |
| Filter 4 — Vote-Timing    | `vote_timing_filter.py`  | ✅ aktiv     | `Vote_Timing_Hit`, `Vote_Timing_Hits`, `Vote_Timing_Hit_Count` |
| Filter 5 — Contrarian     | —                        | ⏸️ blockiert | — (braucht Modul 5: Kursdaten) |
| Filter 6 — Long/Short     | `direction_change_filter.py` | ⏳ fehlt | — |
| Filter 7 — Position+      | `position_increase_filter.py` | ⏳ fehlt | — |

### Scoring (Modul 8)
- `scoring/calculator.py` — gewichtete Berechnung pro Trade — ⏳ fehlt
- `scoring/recommendation.py` — Score → Sterne-Empfehlung — ⏳ fehlt
- `scoring/aggregator.py` — Aggregation pro Ticker — ⏳ fehlt

---

## 📋 NÄCHSTE SCHRITTE

**Modul 7 vervollständigen — Empfohlene Reihenfolge:**
1. Filter 1 (Volumen) — kleinster Hebel, einfachste Logik, gutes Ramp-up
2. Filter 7 (Position vergrößert) — Member+Ticker-Aggregation, Vorbild für Filter 2/6
3. Filter 2 (Schwarm) — größter Score-Beitrag, Cross-Member-Window-Logik
4. Filter 6 (Long/Short) — analog zu Filter 7, andere Bedingung

**Danach Modul 8 (Scoring):**
- Gewichtete Summe der Filter-Punkte mit `settings.WEIGHTS`
- Sterne-Klassifikation per `SCORE_THRESHOLDS`
- Filter 3 als Auto-Flag-Spalte beibehalten (zählt nicht in gewichteten Score)

**Konvention (seit 2026-05-14)**: Filter-Code, der externe-API-Spalten liest, nutzt die neuen Namen mit Provider-Suffix — `sector_yf` / `industry_yf` (yfinance), `market_sector_figi` / `security_type_figi` (OpenFIGI). Nie hardcoden — Schwellen/Gewichte stets aus `config/settings.py`.
