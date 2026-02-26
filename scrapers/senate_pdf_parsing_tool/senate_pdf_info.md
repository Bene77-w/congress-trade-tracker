# Senate PDF Struktur-Analyse

**PDF:** `us_senat_(sub)committees_all_pdf.pdf`
**Gesamt-Seiten:** 47
**Seitenformat:** 594.96 x 841.92 pt (A4)
**Analysierte Seiten:** 1-3, 20-21, 40-47

---

## 1. Font-Sizes und ihre Bedeutung

| Size  | Fontname                        | Verwendung                          | Beispiel                            |
|-------|---------------------------------|-------------------------------------|-------------------------------------|
| 16.0  | SourceSansPro-Semibold          | Seiten-Header (immer gleich)        | `Committee Membership List`         |
| 10.5  | SourceSansPro-Bold              | Committee-Name (Haupttitel)         | `Committee on Agriculture, Nutrition, and Forestry` |
| 11.2  | SourceSansPro-Bold              | Subcommittee-Name                   | `Subcommittee on Commodities, Derivatives, Risk Management, and Trade` |
| 12.7  | SourceSansPro-Bold              | Abschnitts-Labels                   | `Total Members:`, `Majority Members (12)`, `Minority Members (11)`, `Subcommittees (5)` |
| 12.7  | SourceSansPro-Regular           | Member-Namen, URLs, Subcommittee-Liste, Zahlenwerte | `Boozman, John (AR)`, `http://...`, `23` |
| 12.7  | SourceSansPro-It (Italic)       | Rollen-Bezeichnungen                | `Chairman`, `Ranking Member`, `Ex Officio`, `Vice Chairman` |
| 12.5  | (gleiche Fonts, leicht kleiner) | Seite 45 (Select Committee on Intelligence) hat minimal abweichende Sizes (15.8/12.5/10.3 statt 16.0/12.7/10.5) |

### Zusammenfassung Font-Logik
- **Committee-Header:** Size 10.5 + Bold
- **Subcommittee-Header:** Size 11.2 + Bold
- **Member-Namen:** Size 12.7 + Regular
- **Rollen (Chairman etc.):** Size 12.7 + **Italic** (SourceSansPro-It)
- **Labels (Majority/Minority etc.):** Size 12.7 + Bold

> **Wichtig:** Die Font-Prefixes (AAAAAA+, BAAAAA+, etc.) sind PDF-Subset-Prefixes und irrelevant. Der relevante Teil ist nach dem `+`.

---

## 2. Spaltenaufteilung (x0-Schwellenwerte)

### Zwei-Spalten-Layout: Majority (links) | Minority (rechts)

| Element                 | x0 links (Majority) | x0 rechts (Minority) |
|-------------------------|---------------------|----------------------|
| Majority/Minority-Label | ~83                 | ~287-297             |
| Member-Namen            | ~80-83              | ~276-297             |
| Rollen (Italic)         | variabel (nach Name)| variabel (nach Name) |

**Schwellenwert-Empfehlung:** `x0 < 200` = Majority (links), `x0 >= 250` = Minority (rechts)

### Beobachtete x0-Bereiche:
- **Seiten-Header** ("Committee Membership List"): x0 = 80.8
- **Committee-Name** (Bold, 10.5pt): x0 beginnt bei ~80 oder ~294 (zentriert/rechtsbündig auf Seite)
- **URL**: x0 = 80.8
- **Total Members / Subcommittees (N)**: x0 = 80.8
- **Subcommittee-Liste** (Regular, 12.7pt): x0 = 80.8
- **Majority Members**: x0 = ~80-83
- **Minority Members**: x0 = ~263-297 (variiert je nach Committee-Größe!)
- **"XML"-Link**: x0 = 486.0 (ganz rechts, ignorieren)

### Achtung: Variable Minority-Spaltenposition
Die x0-Position der Minority-Spalte ist NICHT fix:
- Seite 2 (Agriculture): Minority bei x0 ≈ 294-297
- Seite 20 (EPW): Minority bei x0 ≈ 287
- Seite 21 (Subcommittee Transportation): Minority bei x0 ≈ 276
- Seite 43 (Joint Economic): Minority bei x0 ≈ 263
- Seite 46 (Aging): Minority bei x0 ≈ 279

**Empfehlung:** Statt fester Schwellenwerte die "Majority Members" / "Minority Members"-Zeile pro Block parsen und deren x0 als Referenz verwenden.

---

## 3. Republicans vs Democrats Unterscheidung

### Mechanismus: **Nur Position (links/rechts), NICHT Font**

- **Majority (Republicans) = Linke Spalte** (x0 ≈ 80-83)
- **Minority (Democrats) = Rechte Spalte** (x0 ≈ 263-297)

Alle Member-Namen verwenden **SourceSansPro-Regular** - unabhängig von Partei.
Es gibt KEINEN Font-Unterschied zwischen R und D bei den Namen selbst.

### Italic wird NUR für Rollen verwendet:
- `SourceSansPro-It` (Italic) = Rollen-Text, NICHT Partei-Indikator
- Rollen stehen **bei beiden Parteien** in Italic

### Konkrete Beispiele:

**Majority (R) mit Rolle:**
```
x0=83.1  Regular  "Boozman, John (AR),"
x0=196.5 Italic   "Chairman"
```

**Minority (D) mit Rolle:**
```
x0=296.6 Regular  "Klobuchar, Amy (MN),"
x0=413.6 Italic   "Ranking Member"
```

**Majority (R) ohne Rolle:**
```
x0=83.1  Regular  "McConnell, Mitch (KY)"
```

**Minority (D) ohne Rolle:**
```
x0=296.6 Regular  "Bennet, Michael F. (CO)"
```

---

## 4. Ex-Officio-Hinweise

### Formatierung
Ex-Officio wird als **Italic-Text direkt nach dem Member-Namen** angezeigt:

```
x0=194.3  Italic  "Ex"
x0=208.6  Italic  "Officio"
```

### Wo Ex-Officio vorkommt:
1. **In Subcommittee-Mitgliederlisten** - Chairman/Ranking Member des Hauptcommittees erscheinen als Ex-Officio in Subcommittees
2. **In Select/Special Committees** - Parteiführer (Majority/Minority Leader) als Ex-Officio

### Beispiel Agriculture Subcommittee (Seite 2):
```
Majority:
  Boozman, John (AR), Ex Officio      ← Chairman des Hauptcommittees

Minority:
  Klobuchar, Amy (MN), Ex Officio     ← Ranking Member des Hauptcommittees
```

### Beispiel Select Committee on Intelligence (Seite 45):
```
Majority:
  Thune, John (SD), Ex Officio        ← Majority Leader
  Wicker, Roger F. (MS), Ex Officio   ← President pro tempore

Minority:
  Reed, Jack (RI), Ex Officio
  Schumer, Charles E. (NY), Ex Officio ← Minority Leader
```

### Kein expliziter Ex-Officio-Hinweistext!
**Wichtiger Unterschied zum House-PDF:** Es gibt KEINEN Satz wie
`[The chairman and ranking minority member are ex officio members of all subcommittees.]`
im Senate-PDF. Stattdessen werden die Ex-Officio-Mitglieder **direkt in jeder Subcommittee-Liste aufgeführt** mit der Italic-Rolle "Ex Officio".

---

## 5. Mehrzeilige Headers

### Ja, Committee- und Subcommittee-Namen sind häufig mehrzeilig

**Committee-Name (size 10.5, Bold) - mehrzeilig:**
```
Seite 1:
  x0=294.4  top=188.9  "Commission on Security and Cooperation"
  x0=80.8   top=206.1  "in Europe"

Seite 20:
  x0=294.4  top=188.9  "Committee on Environment and Public"
  x0=80.8   top=206.1  "Works"

Seite 47:
  x0=294.4  top=188.9  "United States Senate Caucus on"
  x0=80.8   top=206.1  "International Narcotics Control"
```

**Subcommittee-Name (size 11.2, Bold) - mehrzeilig:**
```
Seite 2:
  x0=296.7  top=531.8  "Subcommittee on Commodities,"
  x0=80.8   top=549.1  "Derivatives, Risk Management, and Trade"

Seite 20:
  x0=296.7  top=582.8  "Subcommittee on Chemical Safety, Waste"
  x0=80.8   top=599.3  "Management, Environmental Justice, and Regulatory Oversight"
```

**Subcommittee-Liste im Header-Block (size 12.7, Regular) - auch mehrzeilig:**
```
Seite 20:
  x0=80.8   top=300.5  "Subcommittee on Chemical Safety, Waste Management, Environmental Justice,"
  x0=80.8   top=316.3  "and Regulatory Oversight"
```

### Erkennung mehrzeiliger Headers:
- Gleicher Font (Bold) + gleiche Size (10.5 oder 11.2)
- Zweite Zeile beginnt bei x0 ≈ 80.8 (linksbündig)
- top-Differenz ≈ 17.2 pt zwischen den Zeilen

---

## 6. Seitenstruktur / Block-Aufbau

Jede Committee-Seite folgt diesem Schema:

```
1. "Committee Membership List"          (16.0pt, Semibold, jede Seite)
2. "Please Make a Selection"            (12.7pt, Regular)
3. "XML"                                (12.7pt, Regular, x0=486, rechts - ignorieren)
4. Committee-Name                       (10.5pt, Bold, ggf. mehrzeilig)
5. URL                                  (12.7pt, Regular)
6. "Total Members: N"                   (12.7pt, Bold + Regular)
7. "Subcommittees (N)"                  (12.7pt, Bold) - NUR wenn Subcommittees existieren
8. Subcommittee-Liste                   (12.7pt, Regular) - Liste aller Sub-Namen
9. "Majority Members (N)" / "Minority Members (N)"  (12.7pt, Bold)
10. Member-Listen (links: Majority, rechts: Minority)
11. [Wiederholt sich für jedes Subcommittee:]
    - Subcommittee-Name                 (11.2pt, Bold)
    - "Total Members: N"
    - "Majority Members (N)" / "Minority Members (N)"
    - Member-Listen
```

### Committees ohne Subcommittees (z.B. Joint Committees, Seiten 40-43):
Schritte 7-8 fehlen, es gibt nur den Hauptcommittee-Block.

---

## 7. Besondere Beobachtungen

### Namensformat
- **Nachname, Vorname MiddleInitial. (State)**
- Kein Zusammenkleben von Namen (anders als im House-PDF!)
- State in Klammern, mit Komma wenn Rolle folgt: `(AR),` vs `(AR)`
- Beispiele: `Boozman, John (AR)`, `Durbin, Richard J. (IL)`, `Capito, Shelley Moore (WV)`

### Rollen-Erkennung
Eine Rolle folgt direkt nach `(State),` (mit Komma!):
- `(AR), Chairman` - Komma nach State = es folgt eine Rolle
- `(AR)` - kein Komma = keine Rolle (reguläres Mitglied)

### Verfügbare Rollen im Senate-PDF:
| Rolle           | Italic? | Beispiel                                        |
|-----------------|---------|--------------------------------------------------|
| Chairman        | Ja      | `Boozman, John (AR), Chairman`                   |
| Vice Chairman   | Ja      | `McConnell, Mitch (KY), Vice Chairman`           |
| Ranking Member  | Ja      | `Klobuchar, Amy (MN), Ranking Member`            |
| Ex Officio      | Ja      | `Boozman, John (AR), Ex Officio`                 |
| Cochairman      | Ja      | `Wicker, Roger F. (MS), Cochairman` (Seite 1)   |

> **Neue Rolle: Cochairman** - kommt bei der "Commission on Security and Cooperation in Europe" vor (Seite 1). Nicht im House-PDF vorhanden.

### Fehlender "ex officio members of all subcommittees"-Hinweis
Im Gegensatz zum House-PDF gibt es **keinen** automatischen Ex-Officio-Hinweis.
Ex-Officio-Mitglieder sind **explizit in jeder Subcommittee-Liste** aufgeführt.
Das vereinfacht das Parsing: Man muss nicht raten, sondern liest einfach was dasteht.

### Committees ohne URL
Einige Committees (Seite 1: Commission on Security and Cooperation in Europe) haben keine URL-Zeile. Stattdessen folgt direkt "Total Members:".

### Seite 45 - Leicht abweichende Font-Sizes
Select Committee on Intelligence hat minimal abweichende Sizes (15.8, 12.5, 10.3 statt 16.0, 12.7, 10.5). Vermutlich leicht anders generierte PDF-Seite. Die Differenz ist < 0.5pt - im Parser mit Toleranz arbeiten!

### "XML"-Marker auf jeder Seite
Auf jeder Committee-Startseite steht "XML" bei x0=486. Dies ist ein Link/Button der Website und sollte ignoriert werden.

### Joint Committees (Seiten 40-43)
- Haben keine Subcommittees
- Haben teilweise keine URL (z.B. Joint Committee of Congress on the Library)
- Rollen: Chairman + Vice Chairman (keine Ranking Member-Bezeichnung im PDF)
- Bei Joint Committees steht der Minority-Erste ohne explizite "Ranking Member"-Rolle

### Select/Special Committees (Seiten 44-47)
- Select Committee on Ethics (S. 44): Chairman + Vice Chairman
- Select Committee on Intelligence (S. 45): Chairman + Vice Chairman + mehrere Ex-Officio
- Special Committee on Aging (S. 46): Chairman + Ranking Member
- Senate Caucus on International Narcotics Control (S. 47): Beide Seiten haben einen "Chairman"!

### Zwei Chairmen (Seite 47)
Die "United States Senate Caucus on International Narcotics Control" hat je einen **Chairman** pro Seite:
```
Majority:  Cornyn, John (TX), Chairman
Minority:  Whitehouse, Sheldon (RI), Chairman
```
Dies ist ein Sonderfall - ein Co-Chairman-Modell.

---

## 8. Parser-Empfehlungen

1. **Font-basierte Erkennung** statt x0-basiert für Strukturelemente:
   - Size 10.5 + Bold → Committee-Name
   - Size 11.2 + Bold → Subcommittee-Name
   - Size 12.7 + Italic → Rolle
   - Size 12.7 + Regular → Member-Name oder Fließtext

2. **Majority/Minority via x0:** Pro Block den x0-Wert der "Majority Members"/"Minority Members"-Zeile als Referenz nehmen (NICHT hartcodiert).

3. **Rolle erkennen:** Wenn nach `(XX),` (State + Komma) ein Italic-Wort folgt → das ist die Rolle.

4. **Mehrzeilige Titel:** Gleicher Font + Size in aufeinanderfolgenden Zeilen mit ähnlichem top-Abstand zusammenfügen.

5. **Seite 45 Toleranz:** Font-Size-Vergleich mit Toleranz ±0.5pt durchführen.

6. **Ex-Officio:** Direkt aus der Subcommittee-Liste lesen, KEIN Inferenz-Schritt nötig.
