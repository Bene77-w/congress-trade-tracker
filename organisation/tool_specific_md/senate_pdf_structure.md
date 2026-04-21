# Senate PTR Paper Form – Strukturanalyse für Extraction Tool

**Analysierte PDFs:**
| Disclosure Date | UUID | Seiten |
|---|---|---|
| 02/12/2026 | 068274e1-4b7a-4453-a242-563dde4c10d8 | 5 |
| 12/22/2025 | 13a0fc2a-bbbb-4c1b-b380-d315e198d2a3 | 5 |
| 09/12/2025 | e08ef39c-87e8-4381-9f92-41218ecc7cd4 | 5 |

Alle analysierten PDFs stammen von **Richard Blumenthal** (eingereicht über Elias Law Group).

---

## 1. Technisches Format

### Rendering
- PDFs werden **nicht** als .pdf ausgeliefert, sondern als **GIF-Bilder** pro Seite
- Kein selektierbarer Text — **OCR ist zwingend erforderlich**
- Bildquelle: `https://efd-media-public.senate.gov/media/{YYYY}/{M}/000/000/{seq_id}.gif`
- Die `seq_id` ist eine 12-stellige laufende Nummer (z.B. `000000000452`)

### URL-Schema
```
View-URL:   https://efdsearch.senate.gov/search/view/paper/{uuid}/
Print-URL:  https://efdsearch.senate.gov/search/print/paper/{uuid}/
```
Die Print-URL rendert alle Seiten als `<img>`-Tags auf einer einzigen HTML-Seite.
Die GIF-URLs sind per JavaScript aus dem DOM extrahierbar:
```javascript
Array.from(document.querySelectorAll('img'))
  .map(i => i.src)
  .filter(s => s.includes('efd-media-public'))
// → ["https://efd-media-public.senate.gov/media/2026/2/000/000/000000025.gif", ...]
```

### Seitenanzahl
- Alle drei Dokumente: exakt **5 Seiten**
- Seite 1: Anschreiben (Cover Letter)
- Seiten 2–N: PTR-Formular ("PERIODIC DISCLOSURE OF FINANCIAL TRANSACTIONS")
  - Seite 2: Hauptseite (Page 1) mit bis zu ~14 Zeilen
  - Seiten 3–N: "TRANSACTIONS (continued)" für Folgeseiten (Page 2, 3, …)
- Letzte Seite(n): Leer oder nur Fußnoten

---

## 2. Seite 1 – Cover Letter

**Inhalt (nicht für Extraktion relevant):**
- Briefkopf der einreichenden Kanzlei (z.B. Elias Law Group, 250 Massachusetts Ave NW, Suite 400)
- Datum des Schreibens
- Empfänger: Secretary of the Senate, Office of Public Records
- Betreff: Senator [Name]
- Text: "PTR for transactions dated [from] through [to]"
- Unterschrift des Counsels

**Hinweis:** Der im Brief genannte Transaktionszeitraum (z.B. "January 8, 2026 through January 28, 2026") kann zur Validierung genutzt werden.

---

## 3. Empfangsstempel (jede Seite, ganz oben)

Jede Seite trägt einen maschinengedruckten Stempel:
```
[seq_id]    RECEIVED BY: SECRETARY OF THE SENATE    Date: [Disclosure Date]
```
Beispiel: `0000000000452    RECEIVED BY: SECRETARY OF THE SENATE    Date: September 12, 2025`

**Relevanz:** Das `Date:` hier entspricht dem `disclosure_date` im Scraper-Output.

---

## 4. PTR-Formular – Header-Felder

```
┌─────────────────────────────────────────────────────────────────────┐
│ PERIODIC DISCLOSURE OF FINANCIAL TRANSACTIONS          (Time/Date)  │
├──────────────────────────────┬────────────────────────┬─────────────┤
│ Reporting Individual's Name  │ Amendment (checkbox)   │ Page Number │
│ [Name]                       │ Senate Office/Agency   │             │
└──────────────────────────────┴────────────────────────┴─────────────┘
```

| Feld | Inhalt | Extraction-Relevanz |
|---|---|---|
| Reporting Individual's Name | z.B. "Richard Blumenthal" | Senator-Name |
| Amendment | Checkbox (angekreuzt = Korrektur) | Pflichtfeld |
| Senate Office / Agency | z.B. "United States Senate" | Validierung |
| Page Number | 1, 2, 3, … | Seitenzuordnung |
| (Time/Date) | Optional, meist leer | Ignorieren |

---

## 5. Transaktionstabelle – Spaltenstruktur

```
┌──┬─────────────────────────────────┬──────────────────────┬────────────────┬────────────────────────────────────────────────────────┐
│# │ Identification of Assets        │ Transaction Type (x) │ Transaction    │ Amount of Transaction ($)                              │
│  │                                 │ Purchase | Sale | Ex │ Date           │ A | B | C | D | E | F | G | H | I | J                  │
│  │                                 │                      │ (Mo., Day, Yr.)│                                                        │
└──┴─────────────────────────────────┴──────────────────────┴────────────────┴────────────────────────────────────────────────────────┘
```

### Transaktionstypen
| Kürzel | Bedeutung |
|---|---|
| Purchase | Kauf |
| Sale | Verkauf |
| Exchange | Tausch (zwei Assets tauschen) |

### Betragsspalten (A–J)
| Spalte | Bereich |
|---|---|
| A | $1,001 – $15,000 |
| B | $15,001 – $50,000 |
| C | $50,001 – $100,000 |
| D | $100,001 – $250,000 |
| E | $250,001 – $500,000 |
| F | $500,001 – $1,000,000 |
| G | $1,000,001 – $5,000,000 |
| H | $5,000,001 – $25,000,000 |
| I | $25,000,001 – $50,000,000 |
| J | Over $50,000,000 |

> ⚠️ Hinweis: Ab Spalte G erscheint im Original-Formular oft der Zusatz `***` — bedeutet, dass bei Beträgen über $1 Mio. ein gesondertes Formular fällig sein kann. Für die Extraktion ignorieren.

---

## 6. Asset-Identifikation – Format & Muster

### Owner-Präfix-Codes (in Klammern vor dem Asset-Namen)
| Kürzel | Bedeutung |
|---|---|
| (S) | Self (Senator selbst) |
| (SP) | Spouse (Ehepartner) |
| (DC) | Dependent Child |
| (J) | Joint |

### Asset-Format nach Typ

**Aktien / börsennotiert:**
```
[footnote?][owner] [Company Name] (STOCK) ([TICKER])
```
Beispiel: `*(S) Rhythm Technologies Inc (STOCK) (RTRC)`
Beispiel: `(S) Sara Prognostics Inc-A (STOCK) (SERA)`

→ Ticker steht **immer in der letzten Klammer**, direkt nach `(STOCK)`

**Private Fonds / LLCs / LPs:**
```
[owner] [Fund/LLC Name]
```
Beispiel: `(S) ELCM2 LLC`
Beispiel: `(S) Not Fade Away LLC`
Beispiel: `(S) Falcon Edge Global LP`

→ **Kein Ticker**, kein Hinweis auf Börse

**Anleihen / Bonds:**
```
[owner] [Issuer] Rate/Coupon: [X%] Matures: [Date]
```
(Diese kommen eher in elektronischen PTRs vor, aber möglich)

### Hierarchische Struktur (Parent/Child)
Einige Einträge sind **Gruppenköpfe** ohne eigene Transaktion:
```
[owner] [Parent Fund/LLC Name]:        ← Doppelpunkt am Ende!
    [owner] [Child Asset Name]         ← Eingerückt, mit Transaktion
```
Beispiel:
```
Row 1: (S) 3M's Partners LLC:          ← Parent, kein X in Transaction-Spalte
Row 2:   (S) ELCM2 LLC    X  1/9/26  X   ← Child mit Transaktion
Row 3:   (S) Not Fade Away LLC  X  1/21/26  X
Row 7: (S) Peter L. Malkin Family 9 LLC - 3M's Partners LLC:  ← Parent
Row 8:   (S) ELCM2 LLC   X  1/9/26  X   ← Child (identische Transaktion!)
```

> ⚠️ Wichtig: Parent-Einträge enthalten **keine Transaktionsdaten** — nur Child-Zeilen extrahieren! Parent-Name kann als Kontextfeld gespeichert werden.

---

## 7. Fußnoten

Fußnoten erscheinen **am unteren Rand** jeder Seite:
```
¹ Shares were distributed on 07/14/23 from Parian Global Special Opportunity Fund II LP
² Shares were distributed on 07/14/23 from Parian Global Special Opportunity Fund II LP
```

Referenziert durch Hochzahlen ¹ ² ³ oder `*` direkt vor dem Asset-Namen in der Tabelle.

**Für Extraktion:** Fußnotentext als optionales `comment`-Feld speichern, referenziert über die Nummer.

---

## 8. Besonderheiten & Edge Cases

| Fall | Beschreibung | Handling |
|---|---|---|
| **Amendment** | Checkbox angekreuzt → korrigierte Version eines früheren PTRs | Flag setzen, ggf. alten Eintrag ersetzen |
| **Leere Zeilen** | Nummerierte Zeilen ohne Inhalt | Überspringen |
| **Parent-Zeilen** | Enden auf `:`, keine Transaktion | Nur als Kontext speichern |
| **Footnote-Marker** | `*`, `¹`, `²` vor Asset-Name | Marker entfernen, Fußnote separat speichern |
| **Exchange** | Beide Assets müssen identifiziert werden | Aufpassen: Exchange-Zeilen evtl. mehrzeilig |
| **Seite "TRANSACTIONS (continued)"** | Folgeseiten wenn >14 Zeilen | Identisch extrahieren wie Seite 1 |

---

## 9. Vorgeschlagene Ausgabespalten für Extraction Tool

| Spalte | Quelle |
|---|---|
| `senator_name` | Formular-Header "Reporting Individual's Name" |
| `disclosure_date` | Empfangsstempel "Date:" |
| `transaction_date` | Tabellenspalte "Transaction Date" |
| `owner` | Asset-Präfix-Code (S/SP/DC/J) |
| `asset_name` | Asset-Name ohne Präfix, ohne Ticker-Klammer |
| `asset_type` | Aus Klammer: "STOCK", "Bond", "Municipal Security", etc. |
| `ticker` | Letzte Klammer nach (STOCK), sonst leer |
| `transaction_type` | Purchase / Sale / Exchange |
| `amount` | Betragsrange aus angekreuzter Spalte (A–J → String) |
| `parent_fund` | Name des Parent-Eintrags (falls vorhanden) |
| `comment` | Fußnotentext (falls referenziert) |
| `is_amendment` | Boolean aus Amendment-Checkbox |
| `ptr_link` | Quell-URL des PDFs |

---

## 10. OCR-Strategie

Da alle Seiten GIF-Bilder sind, muss OCR verwendet werden:

- **Empfohlenes Tool:** `pytesseract` (Tesseract OCR) oder Google Vision API
- **Bildqualität:** GIFs sind 3400×4400 Pixel — hohe Auflösung, OCR-freundlich
- **Vorverarbeitung:** Binarisierung (Schwellenwert) erhöht Erkennungsgenauigkeit
- **Tabellenerkennung:** Da keine HTML-Tabellen vorhanden, muss die Spaltenposition per X-Koordinate bestimmt werden
  - Purchase-Spalte: ca. x = 560–580 (Pixel bei 3400px Breite)
  - Sale-Spalte: ca. x = 590–610
  - Exchange-Spalte: ca. x = 620–640
  - Transaction Date: ca. x = 650–750
  - Betragsspalten A–J: ca. x = 760–850 (gleichmäßig verteilt)
- **Alternativstrategie:** Layout-Analyse mit `pdfplumber` oder `camelot` wenn die GIFs zuerst zu PDF konvertiert werden

---

## 11. Offene Fragen für Implementation

1. **OCR-Bibliothek:** pytesseract (lokal, kostenlos) vs. Google Vision API (genauer, kostenpflichtig)?
2. **GIF-Download:** Direkter HTTP-Request ausreichend oder Playwright für Auth nötig? (Bilder scheinen öffentlich zugänglich zu sein)
3. **Spaltenposition:** Koordinaten müssen auf tatsächlicher GIF-Auflösung (3400×4400) kalibriert werden
4. **Parent-Fund-Erkennung:** Zuverlässig via Doppelpunkt am Zeilenende erkennbar?
5. **Exchange-Transaktionen:** Wie werden beide Assets bei Exchange-Zeilen dargestellt?
