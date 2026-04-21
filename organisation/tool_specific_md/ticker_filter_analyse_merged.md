# Ticker-Filteranalyse: yfinance-Enrichment für trades_master.csv

**Datum:** 03.04.2026  
**Kontext:** Automatisches Enrichment der `trades_master.csv` mit yfinance-Industry-Daten über Ticker-Symbole. Voraussetzung: Bereinigung nicht-standardisierter Ticker-Einträge aus zwei getrennten Spezial-Listen.

---

## Überblick

Diese Analyse deckt zwei separate Teilmengen der Ticker-Daten ab — **Teil 1** mit ~130 Einträgen und **Teil 2** mit 113 Einträgen — und identifiziert alle Einträge, die **keinen regulären US-Common-Stock-Ticker** darstellen und daher vor dem yfinance-Lookup ausgeschlossen werden müssen.

| Teilmenge | Analysierte Einträge | Valide Standard-Ticker | Zu filternde Einträge |
|-----------|---------------------|------------------------|----------------------|
| **Teil 1** | ~130 | 3 (`BRK-A`, `STZ-B`, `LEN-B`) | ~127 |
| **Teil 2** | 113 | 0 (alle zu filtern) | 113 |
| **Gesamt** | ~243 | 3 | ~240 |

---

## Valide Standard-Ticker (Whitelist)

Nur **3 Einträge** aus Teil 1 sind trotz ungewöhnlichem Format als reguläre Aktienticker über yfinance nutzbar:

| Ticker | Name | Klasse |
|--------|------|--------|
| `BRK-A` | Berkshire Hathaway | Class A |
| `STZ-B` | Constellation Brands | Class B |
| `LEN-B` | Lennar | Class B |

> **Grenzfall aus Teil 2 — `LGF-A`:**  
> **Lions Gate Entertainment Class A** ist ein offiziell gültiger NYSE-Ticker. Das `-A` ist fester Bestandteil des Symbols (analog zu `BRK-B`), keine Preferred-Stock-Kennzeichnung.  
> - **Nicht filtern**  
> - Der Null-Wert im Datensatz ist vermutlich ein yfinance-Lookup-Problem (Bindestrich-Handling)  
> - **Empfehlung:** Separat debuggen — z.B. `yf.Ticker("LGF-A")` vs. `yf.Ticker("LGF.A")`

---

## Filterkategorien (konsolidiert)

Die folgenden Kategorien kombinieren die Erkenntnisse aus beiden Teilmengen. Wo eine Kategorie in beiden Teilen vorkommt, sind die Beispiele zusammengeführt.

---

### 1. Trailing Digit — Veraltete / delisted / übernommene Ticker

In PTR-Daten wird ein angehängtes `1` oder `2` für Symbole verwendet, die durch Merger, Übernahme oder Delisting nicht mehr aktiv handelbar sind.

**Erkennungsmuster (Teil 1):** Standard-Ticker + abschließende Ziffer `1` oder `2`  
**Erkennungsmuster (Teil 2):** identisch

**Beispiele aus Teil 1:**
```
JASN1  DD1    ANFI1  AMH1   AIDA1  ACTV1
BARZ1  TWX1   SPRI1  ASAM1  PUMP1  EFAX1
FTI1   PLD1   TE1    PDLB1  DTV1   AGN1
UCI1   BH1    UNTD1  ION1   AMR1   CNE1
ALTR1  UST1   WFC1   PLI1   WES2   GLG2
```

**Beispiele aus Teil 2:**
`LB1` · `GSP1` · `KMI1` · `TRON1` · `MR1` · `MFCB1` · `USBC1` · `NUAN1` · `ICOR2` · `FDC1` · `WB1` · `DL1` · `SINT1` · `PYPL1` · `EYE1` · `DNB1` · `ETW1` · `TMCI1` · `CTRL1` · `MTCH1` · `DG1` · `AIC1` · `VLRX1` · `RCM1` · `AMP1` · `ADT1` · `TX1` · `BAC1`

---

### 2. Optionsscheine / Warrants

Eigene Wertpapiergattung (kein regulärer Stock).

**Erkennungsmuster (Teil 1):** `-W`, `-WSA`, `w` (lowercase Suffix)  
**Erkennungsmuster (Teil 2):** `-W`, `-WD`, `-WS*`, Varianten ohne Bindestrich

**Beispiele aus Teil 1:**
```
KMB-W   MSG-W   APY-W   APTV-W   FTVw   GM-WSA
```

**Beispiele aus Teil 2:**
`GTX-W` · `KMI-W` · `TAK-W` · `LTHM-W` · `VTR-W` · `HPQ-W` · `GE-W` · `LLY-WD` · `STI-WSB` · `BACWSA` · `C-WS-A`

---

### 3. Vorzugsaktien / Preferred Stocks

Bevorzugte Aktienklassen mit Dividendenvorrang; eigene ISIN und Tickersuffix.

**Erkennungsmuster (Teil 1):** `-PA`, `-PB`, `-PC`, `-PD`, `-PE`, `-PJ`, `-PL`, `-PO`, `-PG`, `.PRI`, `-P-A`, `-P-D`, `-P-E`, `-P-E-CL`  
**Erkennungsmuster (Teil 2):** `-P*`, `-PC`, `PA` (mit Leerzeichen)

**Beispiele aus Teil 1:**
```
ATH-PE   FRC-PL   AAM-PA   SEAL-P-A   SCHW-PJ
RF-PE    NEE.PRI  SEAL-PB  ALIN-P-E   CMS-PC
VNO-PO   WRB-PG   STT-P-E-CL  T-P-A  RC-P-D
```

**Beispiele aus Teil 2:**
`SACH-P-A` · `CFG-P-E` · `ATH-PC` · `ET-PC` · `SHO-P-I` · `O-P` · `DUK PA`

---

### 4. Optionen

**Erkennungsmuster:** Langer alphanumerischer String mit eingebettetem Verfallsdatum und P/C-Typ

**Beispiele (aus Teil 1):**
```
SPY160219P00180000
```

---

### 5. Bezugsrechte / Rights

Kurzlebige Bezugsrechte im Rahmen von Kapitalerhöhungen.

**Erkennungsmuster (Teil 2):** `-R`-Suffix

**Beispiele:**
`TTM-R`

---

### 6. CUSIP-Nummern (US-Anleihen / Staatsanleihen / Corporate Bonds)

9-stellige alphanumerische US-Wertpapierkennzahlen für Anleihen.

**Erkennungsmuster (Teil 1):** 9-stellige alphanumerische Codes, häufig beginnend mit `912`, `914`, `370`, `400`  
**Erkennungsmuster (Teil 2):** identisch

**Beispiele aus Teil 1:**
```
912796U49   912796XQ7   912796SH3   912796U31   912796YJ2
912796X87   912796QX0   912796YH6   912797KJ5
91282CFN6   91282CJP7   91282CFK2   91282CGH8
914476PV2   37045XEF9   40056FN84
```

**Beispiele aus Teil 2:**
`91282CLG4` · `91282CLC3` · `91282CJS1` · `91282CEG2` · `91282CDR9` · `91282CFG1` · `36966R5P7` · `784532JF1` · `912797LL9` · `912796ZP7` · `912796ZN2` · `912796CR8` · `912796WX3` · `912796QV4` · `40055AY43` · `605699PU5` · `506120KP8`

---

### 7. Anleihen mit Fälligkeitsdatum

**Erkennungsmuster:** `DUE MM/DD/YY` oder reines Datum `MM/DD/YY`

**Beispiele (aus Teil 1):**
```
"DUE 12/01/27"   "DUE 12/01/29"   "DUE 07/01/27"
"DUE 04/01/27"   "DUE 08/01/25"   "DUE 10/01/203"
"07/01/36"       "07/01/28"
```

---

### 8. T-Bill / Geldmarkt Laufzeitbeschreibungen

**Erkennungsmuster:** Textuelle Beschreibungen mit WEEK, MONTH, MATURE

**Beispiele (aus Teil 1):**
```
"8.WEEK, MATURE"   "4.WEEK, MATURE"   "13.WEEK MATURE"
"13.WEEK, MATURE"  "4.WEEK MATURE"    "3.MONTH, MATURE"
"1.MONTH, MATURE"  "6.MONTH"          "MATURIT"
```

---

### 9. Investmentfonds / Mutual Funds

**Erkennungsmuster (Teil 1):** `SYMBOL:`-Präfix oder 5+-Buchstaben mit `X`-Endung (FINRA-Konvention)  
**Erkennungsmuster (Teil 2):** `X`-Suffix (US-Fondskennzeichnung gemäß SEC)

**Beispiele aus Teil 1:**
```
"SYMBOL: OGVXX"   "SYMBOL: CWGIX"   "SYMBOL: AGTHX"
"SYMBOL: AEPGX"   QREARX            QCSTIX
QCGRIX            CMLPSX
```

**Beispiele aus Teil 2:**
`CTECBX` · `QCGLIX`

> **Hinweis zur `X`-Regel:** Das Mutual-Fund-Muster (`X$`) kann False Positives erzeugen (z.B. `XYZ`). Empfehlung: nur in Kombination mit Mindestlänge ≥ 5 Zeichen anwenden oder gegen eine bekannte Fondsliste prüfen.

---

### 10. Börsenpräfix-Formate

**Erkennungsmuster:** `EXCHANGE: TICKER` — kein Standardformat für yfinance

**Beispiele (aus Teil 1):**
```
"NYSE: COV"   "NASDAQ:WLTW"   "CRYPTO:BTC"
```

---

### 11. Auslandsnotierungen (Fremdbörsensuffix)

Symbole auf Primärbörse außerhalb der USA, kein US-Listing.

**Erkennungsmuster (Teil 1):** `.XX`-Suffix mit Länder-/Börsenkürzel  
**Erkennungsmuster (Teil 2):** identisch, zusätzlich Ticker ohne Suffix aber auf Auslandsbörse

**Beispiele aus Teil 1:**
```
1AMT.MI   (Mailand)
FL4.SG    (Singapur)
3V64.TI   (Borsa Italiana)
```

**Beispiele aus Teil 2:**

| Ticker | Börse / Herkunft |
|--------|--------------------|
| `0QZI.IL` | Tel Aviv (Israel) |
| `LM09.SG` | Singapur |
| `HEN3` | Xetra (Henkel, Deutschland) |
| `COLPAL` | BSE (Colgate-Palmolive India) |
| `OSRN DE` | Deutschland |
| `RDS-A` | Veraltet (jetzt `SHEL`) |
| `AKO-A` | NYSE ADR chilenischer Emissor |

---

### 12. SEDOL-Codes (Britisches Wertpapieridentifikationssystem)

UK-basierte 7-stellige Wertpapierkennzahlen (Stock Exchange Daily Official List); keine US-Börsennotierung.

**Erkennungsmuster (Teil 1):** `B` + 6 alphanumerische Zeichen (7-stellig)  
**Erkennungsmuster (Teil 2):** identisch + rein numerische 7-stellige Fremdcodes

**Beispiele aus Teil 1:**
```
B1WSP14   B923935   BN7Q3G8   B13WZ26   BOT4J94
```

**Beispiele aus Teil 2:**
`B1W8P14` · `B5NC0D0` · `B0T4J94` · `BLM7FC2` · `4491235` · `6897143`

---

### 13. Reine Zahlenwerte / numerische IDs

**Erkennungsmuster:** Ausschließlich Ziffern

**Beispiele (aus Teil 1):**
```
44   2   6320058   3305779   5522714   4824778
```

---

### 14. Index-Ticker

**Erkennungsmuster:** `^`-Präfix

**Beispiele:**
- Teil 1: `^MWE`
- Teil 2: `^RGP`

---

### 15. SPAC Unit-Securities

**Erkennungsmuster:** `-U`-Suffix (Unit = Aktie + Warrant gebündelt)

**Beispiele (aus Teil 1):**
```
BLOC-U
```

---

### 16. Kryptowährungen

**Erkennungsmuster:** `CRYPTO:`-Präfix oder Krypto-Eigenname

**Beispiele:**
- Teil 1: `RIPPLE` *(Hinweis: `CRYPTO:BTC` bereits in Kategorie 10 erfasst)*
- Teil 2: `BITCOIN` · `SOLANA` *(Hinweis: `TRON1` in Kategorie 1 erfasst)*

---

### 17. Sonderzeichen / Nicht-Standard-Format

Symbole mit Zeichen außerhalb regulärer Ticker-Konventionen (nur in Teil 2).

| Ticker | Grund |
|--------|-------|
| `SOLV#` | Hash-Suffix — ungültig |
| `Qw` | Kleinbuchstaben — kein offizielles Symbol |
| `U 3%25CD` | URL-encoded (`%25` = `%`) — kein Ticker |
| `CTEST-O` | Vermutlich Testeintrag oder Rights-Symbol |

---

### 18. Fehlerhafte / Test-Einträge

**Erkennungsmuster (Teil 1):** `_failed`-Suffix, `ATEST`-Präfix

**Beispiele (aus Teil 1):**
```
LENB_failed   ATEST-A
```

---

### 19. Freitext / Sonstige Nicht-Ticker

Diverse Einträge ohne erkennbares Tickerformat.

**Aus Teil 1:**

| Eintrag | Grund |
|---------|-------|
| `BELGIUM` | Ländername |
| `"GLAS FUNDS"` | Fondsname mit Leerzeichen |
| `"GLAS FUNDS, LP"` | Fondsname mit Leerzeichen und Komma |
| `ETRADE` | Brokermarkenname (kein Ticker; war `ETFC`) |
| `ICAPITAL` | Private Investmentplattform, nicht börsennotiert |
| `HAMILTO` | Abgeschnittener Eigenname |
| `CORPORAT` | Abgeschnittenes Wort |
| `GUA-CL` | Unklares Klassen-Suffix, kein Standardformat |

**Aus Teil 2:**
`INVESTMEN` · `FUND 2 LTH` · `FUND 2` · `PART INTEREST` · `12/01/37` · `TREASURY INFLATIO` · `ONE SHARE FOR ONE SHARE` · `DUE 07/01/25` · `DUE 10/01/37` · `DUE 7/01/20` · `WMT, SBUX, BRKB AND AAPL` · `CARDONA` · `NASDAQ` · `STATE OF` · `6.MONTH, MATURE` · `MATURE` · `MONTGOMERY, AL .2, SCHAUMBERG, IL .1` · `NORTHWESTER` · `SYMBOL: AIVSX` · `SYMBOL` · `DBA AXIOM MATH` · `3%` · `HOSPIC` · `D/B/A ELLIOTT` · `CTFS PARTN` · `T+A`

---

## Zusammenfassung nach Kategorien

| # | Kategorie | Teil 1 (ca.) | Teil 2 (ca.) | Gesamt (ca.) |
|---|-----------|:------------:|:------------:|:------------:|
| 1 | Delisted / Trailing Digit | 30 | 28 | 58 |
| 2 | Warrants | 6 | 11 | 17 |
| 3 | Vorzugsaktien (Preferred) | 15 | 7 | 22 |
| 4 | Optionen | 1 | 0 | 1 |
| 5 | Bezugsrechte (Rights) | 0 | 1 | 1 |
| 6 | CUSIPs (Anleihen) | 16 | 17 | 33 |
| 7 | Anleihen mit Fälligkeitsdatum | 8 | — | 8 |
| 8 | T-Bill Beschreibungen | 9 | — | 9 |
| 9 | Investmentfonds | 8 | 2 | 10 |
| 10 | Börsenpräfix-Formate | 3 | 0 | 3 |
| 11 | Auslandsnotierungen | 3 | 7 | 10 |
| 12 | SEDOL-Codes | 5 | 6 | 11 |
| 13 | Reine Zahlen | 6 | — | 6 |
| 14 | Index-Ticker | 1 | 1 | 2 |
| 15 | SPAC Units | 1 | 0 | 1 |
| 16 | Kryptowährungen | 1 | 2 | 3 |
| 17 | Sonderzeichen / Nicht-Standard | 0 | 4 | 4 |
| 18 | Fehlerhafte / Test-Einträge | 2 | 0 | 2 |
| 19 | Freitext / Sonstiges | 8 | 26 | 34 |
|   | **Valide Ticker (Whitelist)** | **3** | **0** | **3** |
|   | **Gesamt analysiert** | **~130** | **~113** | **~243** |

Die häufigste Kategorie insgesamt sind **delisted Ticker mit Trailing Digit** (~58), gefolgt von **CUSIPs** (~33), **Freitext-Einträge** (~34) und **Vorzugsaktien** (~22).

---

## Filter-Implementierung (Python) — Konsolidierte Version

Die nachfolgende Implementierung vereint die Erkenntnisse aus beiden Teilen. Sie kombiniert die schärferen Patterns aus Teil 1 mit den zusätzlichen Patterns aus Teil 2.

```python
import re

# Valide Ticker, die trotz ungewöhnlichem Format behalten werden
WHITELIST = {"BRK-A", "STZ-B", "LEN-B"}

# Grenzfall: separat debuggen (yf.Ticker("LGF-A") vs. yf.Ticker("LGF.A"))
BORDERLINE = {"LGF-A"}

FILTER_PATTERNS = [
    # --- Strukturelle Muster ---
    r'.*-P[A-Z]$',               # Preferred Stocks (-PA, -PB, ...)
    r'.*-P-[A-Z](-[A-Z]+)?$',   # Preferred mit Bindestrich (-P-A, -P-E-CL)
    r'.*\.PRI$',                 # Preferred (.PRI)
    r'\s+PA$',                   # Preferred mit Leerzeichen (z.B. "DUK PA")
    r'.*-W[DS]?[A-Z]*$',        # Warrants (-W, -WD, -WSA, -WSB)
    r'WS[AB]?$',                 # Warrant subscriptions ohne Bindestrich
    r'.*w$',                     # Warrants lowercase suffix
    r'.*-R$',                    # Rights / Bezugsrechte
    r'.*-U$',                    # SPAC Units

    # --- Wertpapierkennzahlen ---
    r'^9\d{8}[A-Z0-9]+$',       # US Treasury CUSIPs (Präfix 912...)
    r'^[0-9A-Z]{8}\d$',         # CUSIP-Format (9-stellig, alphanumerisch)
    r'^[0-9A-Z]{8,10}$',        # CUSIP-ähnliche Codes (rein alphanumerisch, 8-10 Zeichen)
    r'^B[A-Z0-9]{6}$',          # SEDOL-Codes (B + 6 alphanumerisch)
    r'^\d{7}$',                  # Numerische Fremdcodes (7-stellig)
    r'^\d+$',                   # Reine Zahlen

    # --- Textuelle Beschreibungen / Freitext ---
    r'.*\d{1,2}/\d{2}/\d{2,4}.*',  # Fälligkeitsdaten (MM/DD/YY)
    r'.*(WEEK|MONTH|MATUR).*',  # T-Bill Beschreibungen
    r'^\s*SYMBOL\s*:',          # Fondsbezeichner mit Präfix
    r'^[A-Z]{5,}X$',            # Mutual Funds (5+ Buchstaben, X-Endung)
    r'^(NYSE|NASDAQ|CRYPTO)\s*:',  # Börsenpräfixe

    # --- Auslandsnotierungen ---
    r'.*\.(MI|SG|TI|DE|PA|IL|L)$',  # Auslandsbörsensuffix

    # --- Indices & Krypto ---
    r'^\^',                     # Index-Ticker
    r'^(BITCOIN|SOLANA|RIPPLE)$',   # Krypto-Eigennamen

    # --- Sonderzeichen & Formatfehler ---
    r'#$',                      # Hash-Suffix
    r' .*',                    # Leerzeichen im Ticker
    r'.*_.*',                   # Unterstriche im Ticker
    r'.*,.*',                   # Komma im Ticker
    r'.*[/].*',                 # Slash im Ticker
    r'.*%.*',                   # Prozentzeichen (URL-encoded etc.)
    r'.*\+.*',                  # Plus-Zeichen

    # --- Delisted Ticker ---
    r'.*\d$',                   # Numerisches Suffix (delisted; 1 oder 2)

    # --- Lange alphanumerische Strings (Optionen) ---
    r'^[A-Z]{1,5}\d{6}[PC]\d{8}$',  # Options-Format (z.B. SPY160219P00180000)
]

def is_valid_ticker(ticker: str) -> bool:
    """Gibt True zurück wenn der Ticker für yfinance-Lookup geeignet ist."""
    if not ticker or not isinstance(ticker, str):
        return False
    ticker = ticker.strip()
    if ticker in WHITELIST:
        return True
    if ticker in BORDERLINE:
        # Separat debuggen: yf.Ticker("LGF-A") vs. yf.Ticker("LGF.A")
        return True  # Vorläufig behalten
    return not any(re.match(p, ticker, re.IGNORECASE) for p in FILTER_PATTERNS)
```

> **Wichtige Hinweise zur Implementierung:**
> - Die `.*\d$`-Regel filtert alle Trailing-Digit-Ticker (`1`/`2`-Suffix). Die Whitelist stellt sicher, dass `BRK-A`, `STZ-B` und `LEN-B` nicht fälschlicherweise ausgeschlossen werden.
> - Das Mutual-Fund-Muster (`^[A-Z]{5,}X$`) kann False Positives erzeugen. Empfehlung: nur in Kombination mit Mindestlänge ≥ 5 Zeichen anwenden oder gegen eine bekannte Fondsliste prüfen.
> - `LGF-A` ist ein valider NYSE-Ticker; das `-A` ist Bestandteil des Symbols, keine Preferred-Kennzeichnung. Separat via `yf.Ticker("LGF.A")` debuggen.
