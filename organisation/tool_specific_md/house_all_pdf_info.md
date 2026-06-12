# house_info_all.pdf - Formatierungsdokumentation

Quelle: `Zwischenlager/house_info_all.pdf`
Titel: List of Standing Committees and Select Committees and Their Subcommittees
119th Congress, January 6, 2026

## Allgemeine Seiteninfos

| Eigenschaft | Wert |
|---|---|
| Seiten gesamt | 80 |
| Seitenbreite | 612 pt |
| Seitenhoehe | 792 pt |
| Font | NewCenturySchlbk (Roman, Italic, Bold) |

## Seitenstruktur

| Seiten | Inhalt |
|---|---|
| 1 | Titelseite |
| 2 | Leer |
| 3 | Inhaltsverzeichnis (CONTENTS) |
| 4 | Leer |
| 5-56 | Standing Committees + Subcommittees |
| 57-58 | Select Committees |
| 59 | Joint Committees of the Congress |
| 60-80 | Alphabetical List of Members |

## Zwei-Spalten-Layout (Seiten 5-58)

Mitglieder stehen in zwei Spalten nebeneinander:

| Spalte | Position (x0) | Partei | Font |
|---|---|---|---|
| Links | x0 ~137-157 | Republican | NewCenturySchlbk-**Roman** (sz 8.0) |
| Rechts | x0 ~332-356 | Democrat | NewCenturySchlbk-**Italic** (sz 8.0) |

**Trennlinie** (fuer Parsing): x0 < 330 = Links/Republican, x0 > 330 = Rechts/Democrat

> **Parser-Update (2026-06-12):** `LEFT_COL_THRESHOLD` im Code ist **318**
> (statt 330) — die Select-Committee-Spalte D startet bereits bei x0≈325,
> Standing-Committees liegen nie unter 330. Siehe Tabelle „Parsing-Relevante
> Schwellenwerte" unten.

## Font-Groessen und Bedeutung

| Font-Groesse | Font-Stil | Bedeutung |
|---|---|---|
| 13.5 | Bold | Sektions-Ueberschrift ("STANDING COMMITTEES") |
| 10.0 | Bold | Committee-Name ("AGRICULTURE") |
| 10.0 | Roman | "SUBCOMMITTEES OF THE COMMITTEE ON..." / Seitenzahlen |
| 8.0 | Roman | Republicans (Mitglieder), Rollen, Metadaten |
| 8.0 | Italic | Democrats (Mitglieder) |
| 7.5 | Roman | Subcommittee-Header ("COMMODITY MARKETS, DIGITAL ASSETS...") — Standing Committees |
| 8.0 | Roman, ALL-CAPS, zentriert | **NEU (2026-06-12):** Select-Committee-Sub-Header (z. B. Intelligence-Subs, China) — sz 8.0 statt 7.5 |

## Zeilen-Aufbau

### Committee-Header (Hauptkommittee)
- ALL CAPS, Bold, sz 10.0
- Zentriert auf Seite
- Immer in den ersten 5 Zeilen einer neuen Committee-Seite
- Beispiel: `AGRICULTURE` bei y=186.5, x0=266

### Ratio-Zeile
- Direkt nach Committee-Header
- Format: `Ratio XX/YY` (Republicans/Democrats)
- Font: Roman, sz 8.0
- Beispiel: `Ratio 29/25`

### Mitglieder-Zeilen (Main Committee)
- Nummeriert: `1. GlennThompson,PA, Chair`
- Nummer beginnt bei x0=137-142 (links) bzw. x0=332-337 (rechts)
- Name+State beginnt bei x0=157 (links) bzw. x0=352 (rechts)
- y-Abstand zwischen Zeilen: ~9.0 pt
- Format: `Name,STATE` (kein Leerzeichen vor State-Code)
- Rollen: `, Chair` / `, Vice Chair` / `, Chairman` am Ende angehaengt

### Subcommittee-Seiten
- Header: `SUBCOMMITTEES OF THE COMMITTEE ON [NAME]` (Roman, sz 10.0)
- Ex-officio Hinweis: `[The chairman and ranking minority member are ex officio members of all subcommittees.]` (Roman, sz 8.0)
- Subcommittee-Name: ALL CAPS, Roman, sz **7.5** (kleiner als Committee-Header!)
- Mitglieder: gleicher Aufbau wie Main Committee, aber OHNE Nummerierung in manchen Faellen

### Rollen-Erkennung
| Rolle | Erkennung |
|---|---|
| Chairman | `, Chair` oder `, Chairman` am Zeilenende |
| Vice Chair | `, Vice Chair` am Zeilenende |
| Ranking Member | Erster Democrat (Italic-Font) in jedem Committee/Subcommittee |
| Member | Alle anderen |

## Sonder-Formate

### Namen mit Spitznamen
- Anfuehrungszeichen: `EricA. "Rick"Crawford,AR`
- Unicode-Zeichen moeglich: `"Rick"` als spezielle Zeichen

### Namen mit Leerzeichen
- `MonicaDe La Cruz,TX` - Vorname und Nachname koennen Leerzeichen enthalten
- `DerrickVan Orden,WI` - Praefix im Nachnamen
- `RobertP.Bresnahan,Jr., PA` - Jr./Sr. suffix, State auf separatem Word

### Joint Committees (Seite 59, 0-indiziert Seite 58)
- Sonderformat: `DavidSchweikert, Representative, AZ, Chairman`
- Enthaelt Senatoren UND Repraesentanten
- Explizites `HOUSE` / `SENATE` Label zur Trennung
- Separate Nummerierung pro Kammer

> **Parser-Update (2026-06-12):** dedizierter `parse_joint(pdf_path,
> page_index=58, members)`-Pfad (`scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py`).
> Eigenheiten: Leadership-Zeilen mit `Representative|Senator`-Kennung; das
> `HOUSE`/`SENATE`-Spaltenlabel **flippt real pro Committee** und muss pro
> Committee neu gelesen werden (zwei Spalten = zwei Kammern, nicht Parteien);
> Senatoren werden uebersprungen (kommen aus dem Senate-XML, s.
> `scrapers/senate_committee_xml/`); Joint Committees haben nur
> Chair+Vice Chair, **kein** Ranking Member — `_assign_ranking` laeuft hier
> NICHT; Partei der House-Member wird ueber den Italic-Font bestimmt, nicht
> ueber die Spaltenseite.

### Alphabetical List (Seiten 60-80)
- Komplett anderes Format
- Drei Spalten: Name, State, Committee-Zuordnungen
- Punkte als Fueller zwischen Spalten (`.....`)
- Nicht relevant fuer Committee-Extraktion

## Committee -> Seiten Mapping

| Seite | Committee |
|---|---|
| 5-7 | AGRICULTURE (+ Subcommittees) |
| 8-11 | APPROPRIATIONS (+ Subcommittees) |
| 12-15 | ARMED SERVICES (+ Subcommittees) |
| 16-17 | EDUCATION AND WORKFORCE (+ Subcommittees) |
| 18-22 | ENERGY AND COMMERCE (+ Subcommittees) |
| 23-25 | FINANCIAL SERVICES (+ Subcommittees) |
| 26-28 | FOREIGN AFFAIRS (+ Subcommittees) |
| 29-31 | HOMELAND SECURITY (+ Subcommittees) |
| 32 | HOUSE ADMINISTRATION |
| 33-35 | JUDICIARY (+ Subcommittees) |
| 36-38 | NATURAL RESOURCES (+ Subcommittees) |
| 39-41 | OVERSIGHT AND GOVERNMENT REFORM (+ Subcommittees) |
| 42 | RULES |
| 43-45 | SCIENCE, SPACE, AND TECHNOLOGY (+ Subcommittees) |
| 46-47 | SMALL BUSINESS (+ Subcommittees) |
| 48-51 | TRANSPORTATION AND INFRASTRUCTURE (+ Subcommittees) |
| 52-53 | VETERANS' AFFAIRS (+ Subcommittees) |
| 54-56 | WAYS AND MEANS (+ Subcommittees) |
| 57-58 | SELECT COMMITTEES (Intelligence, China) |
| 59 | JOINT COMMITTEES (Economic, Library, Printing, Taxation) |

## Parsing-Relevante Schwellenwerte

| Parameter | Wert | Verwendung |
|---|---|---|
| LEFT_COL_THRESHOLD | **318** (Code, seit 2026-06-12; vorher 330) | x0 < 318 = linke Spalte (Republican). Select-Spalte D startet bei x0≈325, Standing-Committees liegen nie unter 330. |
| GAP_THRESHOLD | 30 px | Mindestabstand fuer Spalten-Split |
| SPLIT_RANGE | 200-500 | Gueltigkeitsbereich fuer dynamischen Split-Punkt |
| y-Rundung | 0.1 | Gruppierung von Woertern zur selben Zeile |

## Skip-Pattern (zu ignorierende Zeilen)

- `Ratio XX/YY` - Verhaeltniszeile
- `SUBCOMMITTEES` (wenn < 25 Zeichen) - Sektions-Label
- `CONGRESS` - Seitenkopf-Metadaten
- `ALPHABETICAL LIST` - Beginn des alphabetischen Teils
- `[The chairman and ranking...]` - Ex-officio Hinweis
- `ex officio` - Fussnotentext

> **Parser-Update (2026-06-12):** Ex-Officio-Erkennung fordert jetzt explizit
> das Wort „officio" im Zeilentext (z. B. `[the chair...` + `officio` oder
> `ex officio members of all`). False-Positive-Fix: Notizen wie „may attend"
> oder „authorized to sit" (z. B. Foreign Affairs) loesen KEINE
> Ex-Officio-Regel mehr aus.
- Zeilen mit `(` am Anfang - Fussnoten wie `(1)`
