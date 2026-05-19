# Ticker- und Issuer-Architektur

> **Zweck:** Diese Doku erklärt kompakt, warum der Master mehrere
> Ticker-/Issuer-Spalten parallel enthält, welche Spalte für welchen
> Use-Case zuständig ist, und welche Design-Entscheidungen
> dahinterstehen. Sie soll auch Wochen nach Implementierung ohne
> Konversationskontext nachvollziehbar sein.

---

## 1. Übersicht

Ein Trade bezieht sich nicht nur auf ein abstraktes "Wertpapier" — er
hat in Wirklichkeit drei Identitäts-Ebenen:

| Ebene             | Was wird identifiziert?                                   | Beispiel                                            |
|-------------------|-----------------------------------------------------------|-----------------------------------------------------|
| **Listing-Level** | Wertpapier × Börse                                        | Nestlé an der Stuttgarter Börse (`NESN.SG`) ≠ Nestlé in der Schweiz (`NESN.SW`) |
| **Share-Class-Level** | Issuer × Aktienklasse (Börsen-übergreifend)           | Alphabet Class A (`GOOGL`) ≠ Alphabet Class C (`GOOG`) |
| **Issuer-Level**  | Unternehmen / Emittent                                    | Alphabet Inc. = GOOGL + GOOG + delistete Klassen    |

Verschiedene Pipeline-Stages und User-Use-Cases brauchen verschiedene
Ebenen:

- Asset-Risiko ist oft **klassen-spezifisch** (Voting-Rechte unterscheiden
  sich zwischen Class A und Class C).
- **Insider-Verhalten** dagegen ist Issuer-Level: Wenn drei Abgeordnete
  Alphabet kaufen, ist es egal welche Klasse — der Schwarm ist auf
  Alphabet, nicht auf GOOG vs. GOOGL.

Daher führt der Master **beide** Ebenen explizit als separate Spalten.

---

## 2. Spalten-Inventar

| Spalte             | Ebene         | Quelle                                   | Coverage           | Beispiel                                     |
|--------------------|---------------|------------------------------------------|--------------------|----------------------------------------------|
| `ticker`           | Listing       | Scraper (Quiver/Senate-Feed)             | 100% (Stocks)      | `GOOGL`, `BRK.A`, `NESN.SG`                  |
| `ticker_base`      | Share-Class   | `utils/ticker_classifier` (Offline-Regex) | 100% (Stocks)      | `GOOGL`, `BRK` (für `BRK.A`)                 |
| `ticker_figi`      | Listing       | OpenFIGI `/v3/mapping`                   | ~85%               | `GOOGL` (Bloomberg-Canonical)                |
| `figi`             | Listing       | OpenFIGI `/v3/mapping`                   | ~85%               | `BBG009S39JX6`                               |
| `share_class_figi` | Share-Class   | OpenFIGI shareClassFIGI                  | ~83% (P5)          | `BBG009S39JY5`                               |
| `issuer_ticker`    | Share-Class   | abgeleitet (P5.5): Fallback-Kette         | **100% garantiert** | `BBG009S39JY5` oder `GOOGL` (Fallback)       |
| `issuer_group_key` | **Issuer**    | abgeleitet (P5.6): SEC EDGAR CIK + Bypass | **100% garantiert** | `CIK:1652044`, `TICKER:SPY`, `SKIP:option`   |

### `ticker` (Listing-Level, Rohdaten)
Wie er vom Scraper kommt — inkl. Yahoo-Exchange-Suffix (`.SG`, `.MU`).
Wird im Dashboard angezeigt und für Audit-Trail im Master gehalten.

### `ticker_base` (Share-Class-Level, deterministisch)
Vom Offline-Klassifikator gestrippte Form: `BRK.A` → `BRK`, `EP$C` →
`EP`. Eine Aktienklasse hat genau einen `ticker_base`. Wird für yfinance
(Sektor/Industry) und als Fallback in den Issuer-Spalten verwendet.

### `figi` / `ticker_figi` / `share_class_figi` (Bloomberg-IDs)
Drei Granularitätsstufen aus der OpenFIGI-API:

- `figi` — Listing-Level (genauer als wir brauchen)
- `ticker_figi` — die Bloomberg-Schreibweise des Tickers
- `share_class_figi` — **Share-Class-Level** (alle Listings derselben
  Aktienklasse desselben Issuers haben denselben `share_class_figi`)

Wichtig: `share_class_figi` ist **kein Issuer-Level-Key**! GOOGL (Class A)
und GOOG (Class C) haben **unterschiedliche** `share_class_figi`-Werte,
obwohl der Emittent Alphabet Inc. ist.

Eine vierte FIGI-Variante (`composite_figi`) liefert die OpenFIGI-API
prinzipiell, wird im Projekt aber bewusst nicht genutzt: sie gruppiert
Listings derselben Aktienklasse über Composite-Märkte (Aktie X in NYSE +
Frankfurt + Tokio), löst aber auch nicht Cross-Class.

### `issuer_ticker` (P5.5 — Share-Class, robust)
Abgeleitete Spalte mit Fallback-Kette:
```
issuer_ticker = share_class_figi   (Erstkandidat)
              | ticker_base         (Fallback wenn FIGI leer)
              | ticker              (Last-Resort)
```
Garantiert nicht-leer, solange mindestens eine der drei Quellen
Werte hat (das ist bei jedem realen Trade der Fall). Wird für
**Suspicious-Asset-Aggregation (P4)** und **Sequenz-Signale
(P3)** als Grouping-Key verwendet — bewusst auf Share-Class-Level,
weil Asset-Risiko klassen-spezifisch sein kann.

### `issuer_group_key` (P5.6 — Issuer, Cross-Class)
Abgeleitete Spalte mit Issuer-Level-Auflösung über den SEC EDGAR
**CIK** (Central Index Key). Entscheidungsbaum:

```
1. ticker_type in {option, bond, treasury, crypto, when_issued,
                    invalid, misc, warrant, unit, ignored}
   → "SKIP:<type>"
2. ticker_type in {etf, foreign_stock}
   → "TICKER:<issuer_ticker>"        (Bypass: keine Cross-Class für ETFs/Foreign)
3. EDGAR-Treffer für Ticker?
   → "CIK:<cik>"                     (Cross-Class aufgelöst: GOOGL+GOOG → CIK:1652044)
4. Fallback
   → "TICKER:<ticker_base>"
```

Wird als **SWARM_GROUPING_KEY (P6)** genutzt — Schwarm-Erkennung
operiert auf Issuer-Level, weil das Insider-Signal dort sitzt.

---

## 3. Wann welche Spalte nutzen?

| Use-Case                                  | Spalte               | Begründung                                                       |
|-------------------------------------------|----------------------|------------------------------------------------------------------|
| Dashboard / Anzeige                       | `ticker`             | Was der User aus PTRs/News kennt                                 |
| Sektor/Industry-Lookup via yfinance       | `ticker_base`        | yfinance kennt keine Suffixe wie `.SG`                           |
| **Suspicious-Asset-Aggregation (P4)**     | `issuer_ticker`      | Asset-Risiko ist klassen-spezifisch (Voting-Rechte etc.)         |
| Sequenz-Signale (P3: contrarian/accumulative) | `issuer_ticker`  | Wenn dieselbe Person mehrfach handelt, ist Klasse meist konstant |
| **Schwarm-Erkennung (P6, implementiert ✅)** | `issuer_group_key`   | Insider-Signal ist Issuer-Level (GOOGL+GOOG = ein Alphabet-Schwarm). Implementierung: `signals/swarm_buy_signal.py`, `swarm_sell_signal.py`, `swarm_conflict_signal.py`, `swarm_paradox_signal.py` |
| Eindeutige Asset-ID für Reports           | `share_class_figi`   | Bloomberg-eindeutig (Fallback `ticker_base` wenn FIGI leer)      |

---

## 4. Fallback-Ketten visualisiert

```
issuer_ticker (P5.5):
   share_class_figi → ticker_base → ticker

issuer_group_key (P5.6):
   SKIP-Klasse?    → "SKIP:<type>"
   ETF/Foreign?    → "TICKER:<issuer_ticker>"
   EDGAR-Treffer?  → "CIK:<cik>"
   sonst           → "TICKER:<ticker_base>"
```

---

## 5. Design-Decision-Log

### D1. Warum funktioniert `share_class_figi` für ETFs und Foreign Stocks als Issuer-Key, aber nicht für US-Multi-Class-Stocks?

`share_class_figi` ist semantisch ein **Share-Class-Level**-Identifier.
Ob er als Issuer-Group-Key ausreicht, hängt davon ab, ob das Asset
überhaupt mehrere Aktienklassen hat:

| Asset-Typ        | Beispiel             | Aktienklassen-Struktur       | `share_class_figi` = Issuer? |
|------------------|----------------------|------------------------------|------------------------------|
| US Single-Class  | AAPL, MSFT, TSLA     | Eine Klasse pro Issuer       | ✅ 1:1 mit Issuer            |
| US Multi-Class   | GOOGL/GOOG, BRK.A/BRK.B | Mehrere Klassen, separate FIGIs | ❌ Cross-Class verloren     |
| ETF              | SPY, IVV, VOO        | Keine Aktienklassen (Fund)   | ✅ Fund-Level                |
| Foreign Stock    | NESN.SG, ASML        | Meist Single-Class           | ✅ in der Praxis             |

Die problematische Konstellation ist also **"mehrere Aktienklassen
desselben US-Issuers"** — eine schmale, aber wichtige Teilmenge mit
hohem Trading-Volumen (Mega-Caps wie Alphabet, Berkshire, Fox Corp).
Für alles andere reicht `share_class_figi` bereits aus.

### D2. Warum `ticker_base` als Fallback (statt `asset_name_figi` oder `share_class_figi`)?

Wenn EDGAR keinen CIK liefert, brauchen wir einen Fallback. Diskutiert:

| Fallback                  | Pro                                                              | Contra                                                                                              |
|---------------------------|------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| **`ticker_base`** (gewählt) | Deterministisch, immer verfügbar, lesbar (`TICKER:GOOGL`)        | Cross-Class wird nicht gelöst — irrelevant, weil Cross-Class im Fallback-Bucket extrem selten ist   |
| `share_class_figi`        | Bloomberg-eindeutig                                              | Kryptische ID schwer zu debuggen; fehlt oft genau dort, wo CIK fehlt                                |
| `asset_name_figi` normalisiert | Einziger Weg, Cross-Class im Fallback noch zu lösen          | False-Positives bei Subholdings; Normalisierungs-Code nicht-trivial; schwer reproduzierbar          |

**Gewählt: `ticker_base`** — einfach, robust, ausreichend. Cross-Class
löst der CIK-Pfad bereits ab; der Fallback ist für Edge-Cases
(delistete Aktien, sehr kleine Foreign Issuer).

### D3. Warum CIK statt LEI oder eigene Mapping-Tabelle?

- **CIK** (SEC EDGAR): kostenlos, eine JSON-Datei deckt ~12.000
  US-Listings ab, täglich aktualisiert, kein API-Key erforderlich.
- **LEI** (Legal Entity Identifier): kommerziell, kostenpflichtig.
- **Eigene Mapping-Tabelle**: laufender Pflegeaufwand, keine
  regulatorische Verankerung.

CIK ist der einzige stabile, kostenlose, regulatorisch verankerte
Issuer-Level-Identifier für US-Aktien.

### D4. Warum ETFs und Foreign Stocks bypassen den CIK-Lookup?

- **ETFs** haben CIKs, aber nur über ihren **Sponsor**. SPY → State
  Street CIK, IVV → BlackRock CIK. Würden wir CIK auch für ETFs setzen,
  würden ALLE State-Street-ETFs in einen Schwarm-Bucket fallen. Das ist
  semantisch falsch — ein ETF ist ein eigenständiges Produkt, nicht eine
  Aktienklasse des Sponsors.
- **Foreign Stocks** (direkt notiert, z. B. `NESN.SG`) sind oft nicht
  SEC-registriert und haben gar keinen CIK. ADRs sind die Ausnahme.

Lösung: Beide Typen bekommen **direkt** `TICKER:<issuer_ticker>` —
damit bleibt ihre Gruppierung auf Share-Class-Level, was korrekt ist.

### D5. Warum bekommen Optionen/Bonds/Cash ein eigenes `SKIP:`-Präfix?

Diese Asset-Klassen haben keine sinnvolle Issuer-Gruppierung:
- Optionen sind Derivate, deren Schwarm-Signal anders zu interpretieren wäre
- Bonds desselben Issuers haben unterschiedliche Risikoprofile (Laufzeit, Coupon)
- Cash-Konten haben keinen Issuer

Sie würden im Schwarm-Bucket nur Rauschen erzeugen. Explizit
`SKIP:<type>` macht den Ausschluss transparent (statt sie ins
`TICKER:`-Bucket zu mischen, wo sie schwer zu erkennen wären).

### D6. Warum strikter Abbruch bei SEC-API-Failure (kein Fallback auf veralteten Cache)?

Drei API-Failure-Szenarien:

| Szenario                                     | Cache-Zustand           | Verhalten             |
|----------------------------------------------|-------------------------|-----------------------|
| A — Erster Lauf, SEC down                    | Keine Datei             | **Abbruch**           |
| B — TTL frisch (≤7 Tage)                     | Cache OK                | Normaler Lauf         |
| C — TTL abgelaufen, SEC down, alter Cache da | Veraltet                | **Abbruch**           |

Begründung für strikte Abbruch-Logik:
- Sicherstellung, dass keine veralteten CIKs in den Master einfließen
- SEC-Ausfälle sind extrem selten (Government-Backbone)
- Bei wirklichem Bedarf, mit altem Cache weiterzulaufen, ist eine
  einmalige Code-Anpassung in `utils/issuer_group_mapper.py::_ensure_cache`
  ausreichend

### D7. Warum bleiben `issuer_ticker` und `issuer_group_key` parallel?

Saubere Trennung der Granularitäten:
- `issuer_ticker` (Share-Class) bleibt für klassen-spezifische
  Analysen — Suspicious-Asset-Aggregation und Sequenz-Signale operieren
  auf Klassen-Ebene, weil dieselbe Person typischerweise dieselbe Klasse
  weiterhandelt.
- `issuer_group_key` (Issuer) ist der korrekte Schwarm-Key — wenn drei
  Members Alphabet kaufen, soll der Schwarm erkannt werden, egal ob alle
  GOOGL oder gemischt GOOGL/GOOG.

Auf den ersten Blick scheint `issuer_group_key` ausreichend; tatsächlich
liefern aber beide Spalten unterschiedlichen Mehrwert.

---

## 6. Bekannte Limitationen

- **Delistete Multi-Class-Aktien** ohne aktuelles EDGAR-Listing: kein
  CIK-Hit → landen im `TICKER:`-Fallback → Cross-Class verloren. Sehr
  seltener Edge-Case.
- **Foreign Multi-Class** (z. B. Schweizer Doppelklassen-Aktien
  Stamm/Vorzug): keine Issuer-Auflösung. `TICKER:<issuer_ticker>` —
  Cross-Class verloren.
- **ETF-Sponsoren** werden bewusst NICHT gruppiert (siehe D4).

---

## 7. Code-Referenzen

| Komponente                          | Datei                              |
|-------------------------------------|-------------------------------------|
| Offline-Klassifikator (`ticker_base`) | `utils/ticker_classifier.py`        |
| OpenFIGI-Wrapper (`share_class_figi`) | `utils/ticker_normalizer.py`        |
| `issuer_ticker`-Resolver            | `utils/issuer_ticker.py`            |
| `issuer_group_key`-Mapper           | `utils/issuer_group_mapper.py`      |
| Pipeline-Integration                | `scripts/import_trades.py` Stages `tickeropenfigi` + `issuer_group` |
| SEC-Settings (URL, User-Agent, TTL) | `config/settings.py`, Sektion `SEC EDGAR (P5.6)` |
| EDGAR-Cache-Datei                   | `data/cache/edgar_cik_map.json` (TTL 7 Tage) |

---

## 8. Verifikation

```python
import pandas as pd
df = pd.read_csv('data/processed/trades_master.csv')

# Cross-Class-Check
googl = df[df['ticker'].isin(['GOOGL','GOOG'])][
    ['ticker','issuer_ticker','issuer_group_key']].drop_duplicates()
# Erwartung: unterschiedliche issuer_ticker, identischer issuer_group_key

# Präfix-Verteilung
df['issuer_group_key'].str.split(':').str[0].value_counts()
# Erwartung: CIK > 80%, TICKER ~15%, SKIP ~5%
```
