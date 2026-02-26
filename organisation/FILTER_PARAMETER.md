# Multiplikatoren & Punktesysteme für alle Filter

**Erstellt:** 2026-02-20
**Quelle:** `config/settings.py`, `Aufgaben_Uebersicht`, `CLAUDE.md`

---

## ✅ BEREITS IMPLEMENTIERT (in config/settings.py)

### **Filter 1: Handelsvolumen**
**Status:** ✅ Vollständig implementiert

| Betragsbereich | Punkte | Implementiert |
|----------------|--------|---------------|
| < $15.000 | 0 | ✅ `VOLUME_THRESHOLDS[0] = 15_000` |
| $15k - $50k | 1 | ✅ `VOLUME_THRESHOLDS[1] = 50_000` |
| $50k - $100k | 2 | ✅ `VOLUME_THRESHOLDS[2] = 100_000` |
| $100k - $250k | 3 | ✅ `VOLUME_THRESHOLDS[3] = 250_000` |
| > $250k | 4 | ✅ `VOLUME_THRESHOLDS[4] = float('inf')` |

---

### **Filter 2: Schwarm-Verhalten**
**Status:** ✅ Vollständig implementiert

| Parameter | Wert | Implementiert |
|-----------|------|---------------|
| Zeitraum | 21 Tage | ✅ `SWARM_DAYS = 21` |
| Mindestanzahl Politiker | 3 | ✅ `SWARM_MIN_POLITICIANS = 3` |

---

### **Filter 3: Committee-Sector Mapping**
**Status:** ✅ Vollständig implementiert (Modul 4 abgeschlossen)

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
**Status:** ⏸️ ÜBERSPRUNGEN (vorerst)

| Parameter | Wert | Implementiert |
|-----------|------|---------------|
| Zeitfenster MIN | 60 Tage VOR Abstimmung | ✅ `TIMING_DAYS_MIN = 60` |
| Zeitfenster MAX | 90 Tage VOR Abstimmung | ✅ `TIMING_DAYS_MAX = 90` |

**Hinweis:** Parameter definiert, aber Filter-Logik vorerst nicht implementiert.

---

### **Filter 5: Contrarian**
**Status:** ⏸️ ÜBERSPRUNGEN (vorerst)

| Parameter | Wert | Implementiert |
|-----------|------|---------------|
| Kauf-Signal | Aktie -15% (12 Monate) | ✅ `CONTRARIAN_BUY_THRESHOLD = -0.15` |
| Verkauf-Signal | Aktie +30% (12 Monate) | ✅ `CONTRARIAN_SELL_THRESHOLD = 0.30` |
| Betrachtungszeitraum | 12 Monate | ✅ `CONTRARIAN_LOOKBACK_MONTHS = 12` |

**Hinweis:** Parameter definiert, aber Filter-Logik vorerst nicht implementiert (benötigt Modul 5: Kursdaten).

---

### **Filter 6: Long/Short Wechsel**
**Status:** ✅ Vollständig implementiert

| Parameter | Wert | Implementiert |
|-----------|------|---------------|
| Zeitraum | 6 Monate | ✅ `LONG_SHORT_MONTHS = 6` |

---

### **Filter 7: Position vergrößert**
**Status:** ✅ Vollständig implementiert

| Parameter | Wert | Implementiert |
|-----------|------|---------------|
| Zeitraum | 4 Monate | ✅ `POSITION_INCREASE_MONTHS = 4` |
| Mindestanzahl Käufe | 2 | ✅ `POSITION_INCREASE_MIN_BUYS = 2` |

---

## **GESAMT-SCORING**

### **Gewichtungen** (Summe = 100%)
**Status:** ✅ Vollständig implementiert (aktualisiert 20.02.2026)

| Filter | Gewichtung | Änderung | Implementiert | Status |
|--------|-----------|----------|---------------|--------|
| Filter 1 - Volumen | 15% | - | ✅ `WEIGHTS['volume'] = 0.15` | Config ✅ / Code ⏳ |
| Filter 2 - Schwarm | 30% | ⬆️ +5% | ✅ `WEIGHTS['swarm'] = 0.30` | Config ✅ / Code ⏳ |
| Filter 3 - Ausschuss | 30% | ⬆️ +5% | ✅ `WEIGHTS['committee'] = 0.30` | Config ✅ / Code ✅ |
| Filter 4 - Timing | -- | ⏸️ entfernt | ❌ Nicht in WEIGHTS | ⏸️ Übersprungen |
| Filter 5 - Contrarian | -- | ⏸️ entfernt | ❌ Nicht in WEIGHTS | ⏸️ Übersprungen |
| Filter 6 - Long/Short | 15% | - | ✅ `WEIGHTS['long_short'] = 0.15` | Config ✅ / Code ⏳ |
| Filter 7 - Position vergrößert | 10% | - | ✅ `WEIGHTS['position_increase'] = 0.10` | Config ✅ / Code ⏳ |
| **SUMME** | **100%** | | | |

**Hinweis:** Filter 4 und 5 vorerst übersprungen. Die 10% Gewichtung wurde auf Filter 2 und Filter 3 umverteilt (je +5%).

---

### **Score-Schwellen & Empfehlungen**
**Status:** ✅ Vollständig implementiert

| Score | Empfehlung | Implementiert |
|-------|------------|---------------|
| 75-100 | ⭐⭐⭐ Starke Empfehlung | ✅ `SCORE_THRESHOLDS['strong'] = 75` |
| 50-74 | ⭐⭐ Interessant | ✅ `SCORE_THRESHOLDS['interesting'] = 50` |
| 30-49 | ⭐ Beobachten | ✅ `SCORE_THRESHOLDS['watch'] = 30` |
| 0-29 | Kein Signal | ✅ `SCORE_THRESHOLDS['none'] = 0` |

---

### **Email-Alert Schwelle**
**Status:** ✅ Vollständig implementiert

| Parameter | Wert | Implementiert |
|-----------|------|---------------|
| Email-Alert ab Score | 50 | ✅ `EMAIL_ALERT_THRESHOLD = 50` |

---

## 🔍 ZUSAMMENFASSUNG

### **Implementierungsstatus**

**✅ Parameter vollständig definiert:** Alle 7 Filter in `config/settings.py`

**✅ Filter-Code implementiert:**
- ✅ **Filter 3** (Committee): `CommitteeMapper` + `CommitteeFilter` vollständig

**⏳ Filter-Code ausstehend:**
- ⏳ **Filter 1** (Volumen): Parameter ✅, Code-Implementierung ausstehend
- ⏳ **Filter 2** (Schwarm): Parameter ✅, Code-Implementierung ausstehend
- ⏳ **Filter 6** (Long/Short): Parameter ✅, Code-Implementierung ausstehend
- ⏳ **Filter 7** (Position vergrößert): Parameter ✅, Code-Implementierung ausstehend

**⏸️ Filter übersprungen:**
- ⏸️ **Filter 4** (Timing): Vorerst übersprungen (benötigt Modul 6: Abstimmungsdaten)
- ⏸️ **Filter 5** (Contrarian): Vorerst übersprungen (benötigt Modul 5: Kursdaten)

---

## 📋 NÄCHSTE SCHRITTE

**Modul 7: Filter-Logik implementieren**

Für die 4 verbleibenden Filter:
1. `filters/volume_filter.py` - Filter 1
2. `filters/swarm_filter.py` - Filter 2
3. `filters/direction_change_filter.py` - Filter 6
4. `filters/position_increase_filter.py` - Filter 7
5. `filters/all_filters.py` - Kombination aller Filter

**Modul 8: Scoring-System**
- `scoring/calculator.py` - Gewichtete Berechnung
- `scoring/recommendation.py` - Score → Empfehlung
- `scoring/aggregator.py` - Ticker-Aggregation

---

**Letzte Aktualisierung:** 2026-02-20
