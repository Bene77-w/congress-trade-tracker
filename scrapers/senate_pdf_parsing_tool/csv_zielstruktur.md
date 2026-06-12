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

### Committees (komplexes Feld) — F2-Format (eindeutig)
Verschachtelte Struktur mit **vier** Trennzeichen, jedes auf genau einer Ebene:

```
Committee1|Rolle:Subcommittee1|SubRolle~Subcommittee2|SubRolle;Committee2|Rolle:Subcommittee3|SubRolle
```

**Trennzeichen (F2):**
| Zeichen | Ebene | Bedeutung |
|---|---|---|
| `;` | Top-Level | Trennt **nur Committees** voneinander |
| `:` | Header → Sub-Liste | Trennt Hauptcommittee von seiner Subcommittee-Liste |
| `~` | innerhalb eines Committees | Trennt **Subcommittees** (NEU — war früher `;`) |
| `\|` | Name → Rolle | Trennt Eintrags-Name von Rolle |

> **Warum F2?** Früher trennte `;` sowohl Committees als auch Subcommittees und `:` war optional →
> ein sub-loses Committee war von einem Subcommittee nicht unterscheidbar. Mit `~` als eigenem
> Subcommittee-Trennzeichen ist **jeder `;`-Teil immer ein Committee** — die Mehrdeutigkeit ist
> strukturell beseitigt, kein verpflichtendes Trailing-`:` mehr nötig. `~` ist empirisch in **keinem**
> Committee-/Subcommittee-/Member-Namen vorhanden (einzige Sonderzeichen in Namen: `, | ; " : . ' -`).

**Rollen (ebenen-abhängig normalisiert — interne Bezeichnung zur Vermeidung von Verwechslungen):**
| Ebene | Rolle | Bedeutung |
|---|---|---|
| Committee | `Chairman` | Vorsitzender des Hauptcommittees |
| Committee | `Vice Chairman` | Stellvertreter auf Committee-Ebene |
| Committee | `Ranking Member` | Erster Democrat im Committee |
| **Subcommittee** | `Chair` | Vorsitzender eines **Subcommittees** |
| **Subcommittee** | `Vice Chair` | Stellvertreter eines **Subcommittees** |
| **Subcommittee** | `Ranking Member` | Erster Democrat im Subcommittee |
| beide | `Ex Officio` | Automatisches Mitglied kraft Amtes (Chairman/Ranking des Hauptcommittees) |
| beide | _(leer)_ | Reguläres Mitglied ohne besondere Rolle |

> **Ebenen-Unterscheidung ist bewusst:** `Chairman`/`Vice Chairman` markieren ausschließlich
> Committee-Führung, `Chair`/`Vice Chair` ausschließlich Subcommittee-Führung. `Ranking Member`
> existiert auf beiden Ebenen und wird über die Position im String (vor/nach `:`/`~`) unterschieden.
> Das ist die Datengrundlage für die rollen-differenzierten Ex-Officio-Boni im Scoring.

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
Dusty Johnson,R,SD,"Agriculture|:Commodity Markets, Digital Assets, And Rural Development|Chair~General Farm Commodities, Risk Management, And Credit|",false
```
Erklärung:
- `Agriculture|` = Agriculture Committee, keine besondere Rolle im Hauptcommittee
- `:` = es folgt die Subcommittee-Liste
- `Commodity Markets...|Chair` = **Chair** dieses Subcommittees (Sub-Ebene → `Chair`, nicht `Chairman`)
- `~` = nächstes Subcommittee
- `General Farm...|` = Mitglied ohne Rolle

### Mit Ex-Officio in allen Subcommittees
```
Glenn Thompson,R,PA,"Agriculture|Chairman:Commodity Markets, Digital Assets, And Rural Development|Ex Officio~Conservation, Research, And Biotechnology|Ex Officio~Forestry And Horticulture|Ex Officio~...",true
```
Erklärung: Als `Chairman` von Agriculture (Committee-Ebene) ist er `Ex Officio`-Mitglied aller
Subcommittees (mit `~` getrennt).

### Mehrere Committees
```
Mary E. Miller,R,IL,"Agriculture|:Conservation, Research, And Biotechnology|;Joint Committee On Printing|",false
```
Das zweite Committee (Joint Committee On Printing) wird mit `;` angehängt — es ist eindeutig ein
eigenes Committee (steht hinter `;`, nicht hinter `~`). Der `:` nach dem ersten Committee leitet
dessen Subcommittee-Liste ein.

### Ranking Member (beide Ebenen)
```
Angie Craig,D,MN,"Agriculture|Ranking Member:Commodity Markets...|Ex Officio~...",true
```
Erster Democrat im Committee = `Ranking Member` (Committee-Ebene, vor dem `:`). Als Ranking Member
ist sie `Ex Officio` in allen Subcommittees. `Ranking Member` kann auch auf Subcommittee-Ebene
stehen (nach `:`/`~`) — die Ebene ergibt sich aus der Position im String.

## Regeln für Committees-Feld

### Aufbau-Logik (F2)
```
Für jeden Member:
  Für jedes Committee:
    CommitteeName|Rolle          (Rolle: Chairman/Vice Chairman/Ranking Member/Ex Officio/'')
    Falls Subcommittees vorhanden:
      :SubName1|SubRolle~SubName2|SubRolle~...   (SubRolle: Chair/Vice Chair/Ranking Member/Ex Officio/'')
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

### Rollen-Normalisierung (ebenen-abhängig)
Der PDF-Text wird je nach Ebene (Committee-Header vs. Subcommittee) unterschiedlich normalisiert:

| PDF-Text | Committee-Ebene → | Subcommittee-Ebene → |
|---|---|---|
| `Chair` / `Chairman` / `Chairwoman` | `Chairman` | `Chair` |
| `Vice Chair` / `Vice Chairman` / `Vice Chairwoman` | `Vice Chairman` | `Vice Chair` |
| `Ranking Member` | `Ranking Member` | `Ranking Member` |
| `Cochairman` (Senate) | `Cochairman` (Rolle bleibt; ExOfficio-Spalte=true) | — |
| `Ex Officio` | `Ex Officio` | `Ex Officio` |
| _(leer)_ | _(leer)_ | _(leer)_ |

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
