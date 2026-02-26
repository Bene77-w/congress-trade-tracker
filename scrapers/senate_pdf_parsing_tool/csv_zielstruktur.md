# CSV Zielstruktur - Committee Parser

## Überblick

Beide Parser (House + Senate) erzeugen CSVs mit identischer Struktur.
Das Ziel ist eine einheitliche Datenbasis für den Congress Trade Tracker.

## Dateien

| Datei | Inhalt |
|---|---|
| `house_member&(sub)committees.csv` | Alle House-Mitglieder mit Committees + Subcommittees |
| `senate_committees.csv` | Alle Senatoren mit Committees + Subcommittees |
| `joint_committees_senators.csv` | Senatoren aus Joint Committees (aus House-PDF extrahiert) |

## CSV Spalten

```
Name,Party,State,Committees,ExOfficio
```

### Name
- Format: `Vorname Nachname` (z.B. `Glenn Thompson`)
- Mit Mittelnamen/Initialen: `James R. Baird`, `Frank D. Lucas`
- Mit Suffix: `Robert P. Bresnahan,Jr.` (Jr./Sr. direkt nach Name, mit Komma)
- Mit Spitznamen: `Eric A. "Rick" Crawford` (Anführungszeichen beibehalten)
- Mehrteilige Nachnamen: `Monica De La Cruz`, `Derrick Van Orden`
- PDFs liefern oft zusammengeklebte Namen (`GlennThompson`) - Parser muss Leerzeichen einfügen

### Party
- `R` = Republican
- `D` = Democrat
- `I` = Independent (selten, z.B. Senate)

### State
- 2-Buchstaben US State Code: `PA`, `TX`, `CA`, etc.
- Territories: `AS`, `DC`, `GU`, `MP`, `PR`, `VI`

### Committees (komplexes Feld)
Verschachtelte Struktur mit Trennzeichen:

```
Committee1|Rolle:Subcommittee1|SubRolle;Subcommittee2|SubRolle;Committee2|Rolle:Subcommittee3|SubRolle
```

**Trennzeichen:**
| Zeichen | Bedeutung |
|---|---|
| `;` | Trennt Einträge (Committees UND Subcommittees auf gleicher Ebene) |
| `\|` | Trennt Name von Rolle |
| `:` | Trennt Hauptcommittee von seinen Subcommittees |

**Rollen:**
| Rolle | Bedeutung |
|---|---|
| `Chairman` | Vorsitzender (normalisiert aus Chair/Chairman/Chairwoman) |
| `Vice Chairman` | Stellvertreter (normalisiert aus Vice Chair/Vice Chairman/Vice Chairwoman) |
| `Ranking Member` | Erster Democrat im jeweiligen Committee/Subcommittee |
| `Ex Officio` | Automatisches Mitglied kraft Amtes (Chairman/Ranking des Hauptcommittees) |
| _(leer)_ | Reguläres Mitglied ohne besondere Rolle |

### ExOfficio
- `true` = Mitglied hat mindestens eine Ex-Officio-Position
- `false` = keine Ex-Officio-Position

## Beispiele

### Einfach: Mitglied ohne besondere Rolle, ein Committee, keine Subcommittees
```
Chellie Pingree,D,ME,Agriculture|,false
```

### Mit Rolle im Hauptcommittee
```
Glenn Thompson,R,PA,Agriculture|Chairman,false
```

### Mit Subcommittees
```
Dusty Johnson,R,SD,"Agriculture|:Commodity Markets, Digital Assets, And Rural Development|Chairman;General Farm Commodities, Risk Management, And Credit|",false
```
Erklärung:
- `Agriculture|` = Agriculture Committee, keine besondere Rolle im Hauptcommittee
- `:` = es folgen Subcommittees
- `Commodity Markets...|Chairman` = Chairman dieses Subcommittees
- `;` = nächstes Subcommittee
- `General Farm...|` = Mitglied ohne Rolle

### Mit Ex-Officio in allen Subcommittees
```
Glenn Thompson,R,PA,"Agriculture|Chairman:Commodity Markets, Digital Assets, And Rural Development|Ex Officio;Conservation, Research, And Biotechnology|Ex Officio;Forestry And Horticulture|Ex Officio;...",true
```
Erklärung: Als Chairman von Agriculture ist er Ex-Officio-Mitglied aller Subcommittees.

### Mehrere Committees
```
Mary E. Miller,R,IL,"Agriculture|:Conservation, Research, And Biotechnology|;...;Joint Committee On Printing|",false
```
Das zweite Committee (Joint Committee On Printing) wird mit `;` angehängt. Der `:` nach dem ersten Committee leitet dessen Subcommittees ein.

### Ranking Member
```
Angie Craig,D,MN,"Agriculture|Ranking Member:Commodity Markets...|Ex Officio;...",true
```
Erster Democrat im Committee = Ranking Member. Als Ranking Member ist sie Ex-Officio in allen Subcommittees.

## Regeln für Committees-Feld

### Aufbau-Logik
```
Für jeden Member:
  Für jedes Committee:
    CommitteeName|Rolle
    Falls Subcommittees vorhanden:
      :SubName1|SubRolle;SubName2|SubRolle;...
    Falls weiteres Committee folgt:
      ;CommitteeName2|Rolle2:...
```

### Ranking Member Erkennung
- Der **erste Democrat** (nach Listenreihenfolge im PDF) in jedem Committee/Subcommittee = `Ranking Member`
- NUR wenn dieser Democrat keine andere explizite Rolle hat (Chairman, Vice Chairman)
- Pro Committee/Subcommittee gibt es genau einen Ranking Member

### Ex-Officio Regel
- Wenn im PDF der Hinweis steht: `[The chairman and ranking minority member are ex officio members of all subcommittees.]`
- Dann: Chairman UND Ranking Member des Hauptcommittees werden automatisch allen Subcommittees hinzugefügt mit Rolle `Ex Officio`
- AUSNAHME: Wenn sie bereits mit einer anderen Rolle im Subcommittee stehen, wird die bestehende Rolle NICHT überschrieben
- ExOfficio-Spalte wird auf `true` gesetzt

### Rollen-Normalisierung
| PDF-Text | Normalisiert zu |
|---|---|
| `Chair` | `Chairman` |
| `Chairman` | `Chairman` |
| `Chairwoman` | `Chairman` |
| `Vice Chair` | `Vice Chairman` |
| `Vice Chairman` | `Vice Chairman` |
| `Vice Chairwoman` | `Vice Chairman` |
| `Ranking Member` | `Ranking Member` |
| `Ex Officio` | `Ex Officio` |

### Committee-Namen
- Title Case: `Agriculture`, `Armed Services`, `Energy And Commerce`
- Subcommittees auch Title Case: `Commodity Markets, Digital Assets, And Rural Development`
- Ursprünglich ALL CAPS im PDF, werden zu Title Case konvertiert

## Validierung

### Erwartete Zahlen (119th Congress, ca.)
- House: ~435 Mitglieder (inkl. Delegates)
- Senate: ~100 Mitglieder
- Standing Committees House: 20
- Standing Committees Senate: 16
- Select Committees: je 2-3
- Joint Committees: 4

### Prüfpunkte
- Jedes Committee hat genau 1 Chairman (R)
- Jedes Committee hat genau 1 Ranking Member (D)
- Ratio-Zeilen im PDF geben R/D-Verhältnis vor
- ExOfficio=true nur bei Mitgliedern die tatsächlich Ex-Officio-Positionen haben
- Kein Mitglied darf doppelt vorkommen (Name = eindeutiger Key)
