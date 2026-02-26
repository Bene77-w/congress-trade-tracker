## Spalten
- **Name**: `Vorname Nachname`
- **Party**: 1 Buchstabe (`D`, `R`)
- **State**: 2 Buchstaben (`IA`, `TX`)
- **Committees**: Verschachtelte Struktur
- **ExOfficio**: `true`/`false`

## Committees-Syntax

**Separatoren:**
- `;` = trennt Committees
- `|` = trennt Name von Role
- `:` = trennt Committee von seinen Subcommittees

**Roles:** `Chairman`, `Ranking Member`, `Cochairman`, `Vice Chairman`, `EXOfficio`, oder leer

## ExOfficio-Regel

**ExOfficio** ist `true`, wenn ein Mitglied `Chairman` oder `Ranking Member` eines **Hauptcommittees** ist (nicht von Subcommittees).

## Beispiel

Judiciary|Chairman:Criminal Justice|;Immigration|Chairman;Armed Services|:Seapower|Ranking Member;Finance|Ranking Member