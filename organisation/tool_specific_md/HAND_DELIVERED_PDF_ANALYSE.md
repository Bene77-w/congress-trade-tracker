# Analyse: Hand-Delivered PTR PDFs (House of Representatives)

## Zusammenfassung

**59 PDFs** in `house_scraper_fehler.csv` sind gescannte Papierformulare ("HAND DELIVERED") ohne Textlayer.
- **58x** `empty_text` (Bild-Scan, kein extrahierbarer Text)
- **1x** `download_error` (Scott H. Peters, DocID 20032283 — separates Problem)
- **14 verschiedene Abgeordnete** betroffen

### Technische Bestätigung
- pdfplumber findet: **0 Chars, 0 Text, 0 Tabellen** auf allen Seiten
- Jede Seite = **1 ganzseitiges Bild** (volle Seitenbreite/-höhe)
- PDF-Struktur: Nur `/Image` + `/XObject` Referenzen, **keine `/Font`**
- → **OCR ist zwingend erforderlich**

---

## Identifizierte Formular-Typen

### Typ A: "Rotated Handwritten Form" (90° gedreht)
**Betroffene Members:** Adrian Smith, Gus Bilirakis, Chuck Fleischmann (teilweise)
**Orientierung:** 90° im Uhrzeigersinn gedreht (muss für OCR rotiert werden)
**Ausfüllung:** Handschriftlich + Checkboxen (X-Markierungen)
**Seitenformat:** Portrait-orientiert im PDF, aber Inhalt ist Landscape

**Layout Seite 1 (Header-Seite):**
```
┌─────────────────────────────────────────────────────────────┐
│ "HAND DELIVERED" Stempel (rechts oben)                       │
│ "LEGISLATIVE RESOURCE CENTER" + Datumsstempel                │
│ "OFFICE OF THE CLERK, U.S. HOUSE OF REPRESENTATIVES"        │
│                                                              │
│ UNITED STATES HOUSE OF REPRESENTATIVES                       │
│ Periodic Transaction Report                                  │
│                                                              │
│ ┌─────────────────────────────────────────────────┐          │
│ │ NAME: [handschriftlich]                          │          │
│ │ State: [XX]  District: [##]                      │          │
│ │ OFFICE TELEPHONE: [###-###-####]                 │          │
│ │ Member of U.S. House [X] / Officer or Employee [ ]│         │
│ │ File an original and 2 copies                    │          │
│ │ Initial Report [X] / Amendment [ ]               │          │
│ └─────────────────────────────────────────────────┘          │
│                                                              │
│ ┌─────────────────────────────────────────────────────────┐  │
│ │ SP/JT/DC │ FULL ASSET NAME │ TYPE OF │ DATE OF │ DATE  │  │
│ │          │ (full name,     │ TRANS-  │ TRANS-  │ NOTI- │  │
│ │          │  not ticker)    │ ACTION  │ ACTION  │ FIED  │  │
│ │          │                 │ Purch.  │(MM/DD/YY│(MM/DD/│  │
│ │          │                 │ Sale    │         │  YY)  │  │
│ │          │                 │ Part.S  │         │       │  │
│ │          │                 │ Exch.   │         │       │  │
│ ├──────────┼─────────────────┼─────────┼─────────┼───────┤  │
│ │ [owner]  │ [handschriftl.] │  [X]    │[Datum]  │[Datum]│  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
│ ┌── AMOUNT OF TRANSACTION ──────────────────────────┐        │
│ │ A: $1,001-$15,000          [X] markiert            │        │
│ │ B: $15,001-$50,000         [ ]                     │        │
│ │ C: $50,001-$100,000        [ ]                     │        │
│ │ D: $100,001-$250,000       [ ]                     │        │
│ │ E: $250,001-$500,000       [ ]                     │        │
│ │ F: $500,001-$1,000,000     [ ]                     │        │
│ │ G: $1,000,001-$5,000,000   [ ]                     │        │
│ │ H: $5,000,001-$25,000,000  [ ]                     │        │
│ │ I: $25,000,001-$50,000,000 [ ]                     │        │
│ │ J: Over $50,000,000        [ ]                     │        │
│ │ K: Spouse/DC >$1,000,000   [ ]                     │        │
│ └────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

**Besonderheiten:**
- **NUR 1 Transaktion pro Seite** (bei Typ A rotated)
- Amount wird über **Checkbox-Buchstaben** (A-K) angegeben, NICHT als Dollar-Range-Text
- Owner-Code steht in separater Spalte links (SP/JT/DC oder leer=Self)
- Transaction Type: Checkboxen für Purchase/Sale/Partial Sale/Exchange
- Daten im Format MM/DD/YY (2-stellige Jahreszahl!) ODER MM/DD/YYYY
- FILER NOTES am unteren Rand (z.B. "My dependent child sold shares of GE Vernova LLC (GEV).")
- NOTE NUMBER Spalte für Querverweise

**Fortsetzungsseiten (Page 2+):**
- Gleiche Formularstruktur, aber OHNE Header-Sektion (kein Name/State/etc.)
- Beginnt direkt mit der Transaktionstabelle
- Kann leer sein (nur Formularvorlage ohne ausgefüllte Daten)

---

### Typ B: "Landscape Table Form" (maschinengetippt)
**Betroffene Members:** Michael T. McCaul, Harold Dallas Rogers
**Orientierung:** Seite 1 kann rotiert sein (Typ A Header), Folgeseiten sind Landscape/Normal
**Ausfüllung:** Maschinengetippt (sauberer Text, gut lesbar)
**Seitenformat:** Landscape (breiter als hoch)

**Layout Seite 1 (McCaul):**
- Kombiniert: **Rotierter Header** (Typ A Stil) mit eingebetteter Transaktionstabelle
- McCaul Seite 1 enthält ~20+ Transaktionen direkt auf der Header-Seite
- Owner-Code links neben jedem Asset-Namen

**Layout Folgeseiten (McCaul Page 2-9):**
```
┌─────────────────────────────────────────────────────────────────────┐
│ NAME: MICHAEL MCCAUL                                                │
│                                                                     │
│ ┌────────┬──────────────────────┬──────────┬────────┬──────────┬──┐ │
│ │        │                      │ Type of  │ Date of│ Date     │  │ │
│ │        │ FULL ASSET NAME      │ trans-   │ Trans- │ Notified │  │ │
│ │        │                      │ action   │ action │ of Trans │  │ │
│ │        │                      │          │        │ action   │  │ │
│ │ JT/SP/ │                      │P  S  Exch│(MM/DD/ │(MM/DD/YY)│  │ │
│ │ DC     │ Provide full name,   │U  A  HA  │  YY)   │          │  │ │
│ │        │ not ticker symbol    │R  L  NG  │        │          │  │ │
│ │        │                      │C  E  E   │        │          │  │ │
│ │        │                      │H         │        │          │  │ │
│ ├────────┼──────────────────────┼──┬──┬────┼────────┼──────────┤  │ │
│ │ SP     │ ALPHABET INC-CL C    │  │X │    │10/27/25│ 11/5/25  │  │ │
│ │ SP     │ U.S. TREASURY BILL   │  │X │    │10-16-25│ 11/5/25  │  │ │
│ │ SP     │ META PLATFORMS INC-A  │  │X │    │10/8/25 │ 11/5/25  │  │ │
│ │ ...    │ ...                   │  │  │    │ ...    │ ...      │  │ │
│ └────────┴──────────────────────┴──┴──┴────┴────────┴──────────┘  │ │
│                                                                     │
│ ┌── AMOUNT OF TRANSACTION ──────────────────────────────────────┐   │
│ │ A │ B │ C │ D │ E │ F │ G │ H │ I │ J │ K                    │   │
│ │ X │   │   │   │   │   │   │   │   │   │  ← eine Spalte pro   │   │
│ │ X │   │   │   │   │   │   │   │   │   │     Transaktion      │   │
│ └───────────────────────────────────────────────────────────────┘   │
│                                                              X of 9 │
└─────────────────────────────────────────────────────────────────────┘
```

**Besonderheiten McCaul:**
- **Viele Transaktionen pro Seite** (~25 pro Seite, 9 Seiten = ~200+ Trades)
- Amount-Buchstaben als **Spalten-Header** (A-K), X-Markierung in der jeweiligen Spalte
- Seitennummer unten ("2 of 9", "3 of 9", etc.)
- Name auf jeder Folgeseite wiederholt: "NAME: MICHAEL MCCAUL"
- Alle Daten maschinengetippt → **sehr gut für OCR geeignet**
- Spezialfall Seite 9: "LINDA MAYS MCCAUL PARTNERS II LP" als Subholding (ohne Owner-Code)
  und "MICHAEL MCCAUL" als Zeile → scheint Namenswechsel/Subholding zu sein

**Besonderheiten Rogers:**
- **1 Seite**, Landscape-orientiert
- Maschinengetippt mit sauberem Layout
- Enthält "Nothing to report for December 2025" + Beispielzeile
- "JT" als Owner in der Beispielzeile
- Spezialfall: "Nothing to report" → 0 Transaktionen

---

### Typ C: "Hybrid" (Fleischmann)
**Betroffene Members:** Chuck Fleischmann
**Besonderheit:** Formular wie Typ A (rotiert), aber maschinengetippt
- Seite 1: Header + mehrere Transaktionen (6-7 pro Seite)
- Seite 2-3: Fortsetzungsseiten mit weiteren Transaktionen
- Owner-Codes: JT und DC sichtbar
- "FOOTNOTE NUMBER" Spalte vorhanden
- Alle Seiten 90° gedreht
- Gut lesbar für OCR (maschinengetippt trotz Scan)

---

## Datenfelder pro Transaktion

| Feld | Verfügbarkeit | Anmerkungen |
|------|---------------|-------------|
| **Owner** | Immer | SP/JT/DC oder leer (=Self), steht links vom Asset-Name |
| **Full Asset Name** | Immer | Vollständiger Name, NICHT Ticker-Symbol |
| **Transaction Type** | Immer | Purchase / Sale / Partial Sale / Exchange (Checkbox) |
| **Date of Transaction** | Immer | MM/DD/YY oder MM/DD/YYYY |
| **Date Notified** | Immer | MM/DD/YY oder MM/DD/YYYY (= disclosure_date) |
| **Amount** | Immer | Buchstabe A-K → Dollar-Range Mapping |
| **Ticker** | NIE | Formular fordert explizit "full name, not ticker symbol" |
| **Asset Type** | NIE | Kein Feld im Papierformular |
| **Subholding** | Selten | Nur implizit (z.B. Bilirakis Filer Notes: "GE Vernova LLC (GEV)") |
| **Filer Notes** | Optional | Am unteren Rand, NOTE NUMBER für Querverweise |

### Amount Letter → Dollar Range Mapping
```
A = $1,001 - $15,000
B = $15,001 - $50,000
C = $50,001 - $100,000
D = $100,001 - $250,000
E = $250,001 - $500,000
F = $500,001 - $1,000,000
G = $1,000,001 - $5,000,000
H = $5,000,001 - $25,000,000
I = $25,000,001 - $50,000,000
J = Over $50,000,000
K = Spouse/DC Amount over $1,000,000
```

---

## Spezialfälle & Herausforderungen

1. **Rotation:** Typ A/C Seiten sind 90° im Uhrzeigersinn gedreht → müssen vor OCR rotiert werden
2. **Handschrift (Typ A):** Asset-Namen handschriftlich = höhere Fehlerrate bei OCR
3. **"Nothing to report":** Rogers DocID 8221310 enthält keine Transaktionen
4. **Datumsformat:** 2-stellige Jahreszahl (MM/DD/YY) → muss in MM/DD/YYYY konvertiert werden
5. **Subholdings:** Nur in Filer Notes erwähnt, nicht als separates Feld
6. **McCaul Seite 1:** Kombination aus Header + Tabelle auf einer rotierten Seite
7. **Kein Ticker:** Papierformular enthält keine Ticker-Symbole (nur vollständige Namen)
8. **Kein Asset Type:** Kein ST/OP/GS/OT/CS/DC/EF/MF Feld im Papierformular
9. **Multiple Amount-Markierungen:** Jede Transaktion hat eine eigene A-K Zeile bei Typ A,
   aber bei Typ B sind die Amounts als Spalten pro Zeile

---

## Betroffene Members (aus house_scraper_fehler.csv)

| Name | Anzahl PDFs | Formular-Typ | Geschätzte Trades |
|------|------------|--------------|-------------------|
| Rohit Khanna | 12 | ? | Unbekannt |
| Harold Dallas Rogers | 12 | B (Landscape Table) | 0 pro PDF ("Nothing to report") |
| Michael T. McCaul | 12 | B (Landscape Table) | ~200 pro PDF |
| Adrian Smith | 3 | A (Rotated Handwritten) | 1-2 pro PDF |
| Chuck Fleischmann | 4 | C (Hybrid rotated+typed) | 5-15 pro PDF |
| Vicente Gonzalez | 3 | ? | Unbekannt |
| Brad Sherman | 3 | ? | Unbekannt |
| Ann Wagner | 2 | ? | Unbekannt |
| Keith Alan Self | 2 | ? | Unbekannt |
| Nicole Malliotakis | 2 | ? | Unbekannt |
| Gus M. Bilirakis | 1 | A (Rotated Handwritten) | 1 pro PDF |
| Jamie Raskin | 1 | ? | Unbekannt |
| Lisa McClain | 1 | ? | Unbekannt |
| Scott H. Peters | 1 | download_error | N/A |

---

## Technische Analyse der PDFs

| DocID | Name | Seiten | Größe | Orientierung | Ausfüllung |
|-------|------|--------|-------|-------------|------------|
| 8220764 | Adrian Smith | 2 | 54 KB | Rotiert 90° | Handschriftlich |
| 8221310 | Harold Dallas Rogers | 1 | 25 KB | Landscape | Maschinengetippt |
| 9115670 | Chuck Fleischmann | 3 | 85 KB | Rotiert 90° | Maschinengetippt |
| 8220747 | Gus Bilirakis | 2 | 55 KB | Rotiert 90° | Handschriftlich + Notiz |
| 8221228 | Michael McCaul | 9 | 243 KB | S1 rotiert, S2-9 Landscape | Maschinengetippt |

---

## Empfehlung für Implementierung

### OCR-Ansatz: Claude Vision API (Anthropic)
**Begründung:**
1. Das Projekt nutzt bereits die Anthropic API (senate_pdf_extractor.py existiert als Referenz)
2. Claude Vision kann sowohl Handschrift als auch Maschinentext zuverlässig lesen
3. Tabellarische Daten werden gut erkannt
4. Rotation wird automatisch erkannt und kompensiert
5. Kein zusätzliches OCR-Setup nötig (kein Tesseract, kein Google Vision)

### Alternative: Tesseract OCR + Tabellenextraktion
- Vorteil: Kostenlos, keine API-Kosten
- Nachteil: Schlechte Handschrift-Erkennung, komplexes Tabellen-Parsing nötig
- Nachteil: Rotation muss manuell behandelt werden

### Empfehlung: **Claude Vision API**
- Bereits im Projekt vorhanden (senate_pdf_extractor.py)
- Prompt-basiert: Kann präzise Anweisungen zur Datenextraktion erhalten
- Kann Amount-Buchstaben (A-K) direkt in Dollar-Ranges übersetzen
- Kann "Nothing to report" erkennen und leere Ergebnisse liefern
