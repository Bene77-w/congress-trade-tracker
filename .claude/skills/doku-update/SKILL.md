---
name: doku-update
description: Aktualisiert die Projekt-Dokumentation nach Abschluss eines Moduls oder einer größeren Code-Änderung. Bündelt git-diff-Scope, DOKU_INDEX-Routing und ein Briefing an den Sonnet-doku-agent. Verwenden, wenn ein Modul/Feature fertig ist, ein Branch vor dem Merge dokumentiert werden soll, oder der User "Doku aktualisieren / dokumentieren" verlangt.
---

# Doku-Update

Tokenintensives, niedrigkomplexes Doku-Schreiben wird an den `doku-agent` (Modell **Sonnet**)
ausgelagert — das hält die teure Haupt-Session (oft Opus) frei. Dieser Skill bereitet den
Sub-Agent-Aufruf vor.

## Leitregel — drei Quellen, klare Rollen (NICHT invertieren)

| Quelle | Rolle | |
|---|---|---|
| **Pitch** (du, mit Session-Kontext) | **primär** — *Bedeutung*: was/warum gebaut | git kennt das nie |
| **`git diff`/`git status`** | **Scope-/Komplettheits-Wächter** — vollständige Datei-Liste | fängt ab, was der Pitch vergisst |
| **`organisation/DOKU_INDEX.md`** | **Routing** — Änderungstyp → Ziel-Doku | git sagt nicht, *wohin* |

> Der `doku-agent` (schwächeres Modell als Opus) darf Semantik **nie allein aus dem Diff
> rückschließen**. Bedeutung kommt aus dem Pitch; der Diff ist Checkliste, nicht Verständnis-Ersatz.

## Ablauf

1. **Scope aus git** (Ground Truth, was dieser Branch an **Code** angefasst hat):
   ```bash
   git diff main...HEAD --stat   # Branch-Footprint ggü. Merge-Base (committed)
   git status                    # noch uncommitted
   ```
   > **Wer nutzt das:** nur der **Main-Agent**, als Komplettheits-Check beim Briefing-Bauen — er
   > geht **nicht** an den doku-agent. Zweck: objektive Gegenliste zum Pitch (Gedächtnis vergisst);
   > jede hier gelistete Code-Datei muss im Briefing eine Doku-Konsequenz haben oder als
   > „doku-irrelevant" begründet sein. Das ist der **Input** (geänderte Code-Dateien), nicht der
   > Doku-Output — den zeigt Schritt 5.
2. **Routing** — `organisation/DOKU_INDEX.md` lesen: jede geänderte Datei / jeden Änderungstyp
   (neue Spalte, neue Stage, neues Signal, neues CLI-Flag …) der „Aktualisieren bei"-Spalte
   zuordnen → Liste der zu pflegenden Doku-Dateien.
   - **Bei Modul-Abschluss** (Modul war „In Arbeit", ist jetzt fertig): WIP-Detail aus dem
     „Aktueller Stand (WIP)"-Block von `AGENTS.md` nach `CHANGELOG.md` migrieren **und den
     WIP-Block leeren/auf das nächste aktive Modul umstellen**. So bleibt der WIP-Block die
     einzige handgepflegte Stelle nicht dauerhaft veraltet.
2b. **Abschnitts-Anker bestimmen** (Token-Hebel — die Doku-Dateien sind groß, z. B.
   `DATA_STRUCTURE.md` ~1750 Zeilen): pro Ziel-Datei aus Schritt 2 das relevante Heading grepen,
   damit der Subagent nur den Abschnitt statt der ganzen Datei lädt:
   ```bash
   grep -n '^#' organisation/DATA_STRUCTURE.md   # Heading → Zeilennummer je Ziel-Datei
   ```
   Den so ermittelten Anker (`→ Abschnitt …, ca. Zeile N`) in die Briefing-Dateiliste übernehmen.
   Selbst grep'en kostet ~nichts; es spart dem Subagent den Whole-File-Read (~22k Tokens pro Datei).
3. **Briefing bauen** (Struktur s. `.claude/agents/doku-agent.md`): semantische Zusammenfassung
   (Pitch) + die git-diff-Dateiliste als Komplettheits-Check + **pro Ziel-Datei den Abschnitts-Anker
   aus 2b**. Jede im Diff geänderte Code-Datei muss entweder im Briefing eine Doku-Konsequenz haben
   **oder** explizit als „doku-irrelevant" begründet sein — keine stille Lücke.
   - **Der Hauptagent liest die großen Doku-Dateien dabei NICHT selbst** — Routing (DOKU_INDEX, 49 Z.)
     + `git diff --stat` (nur Dateinamen) + Session-Kontext genügen fürs Briefing. Whole-File-Reads
     der Doku sind Sache des Sonnet-Subagents, nicht des (teuren) Hauptagenten.
4. **Sub-Agent rufen** (Default Sonnet; in einer **DeepSeek-Session** `model: "flash"` übergeben):
   ```
   Agent({
     subagent_type: "doku-agent",
     description: "Doku-Update nach <Modul/Änderung>",
     prompt: "## Briefing: …"            // + model: "flash" nur in DeepSeek-Sessions
   })
   ```
5. **Review vor Merge** — nach dem Lauf die Doku-Änderungen **dem User** vorlegen:
   ```bash
   git diff -- organisation/ CHANGELOG.md AGENTS.md
   ```
   > **Wer nutzt das:** nur der **User**. Das ist der **Output** (welche Doku-Dateien der Subagent
   > geupdatet hat) — nicht zu verwechseln mit dem Code-Scope aus Schritt 1.
   **Der User prüft den Diff, nicht der Hauptagent** — den vollen Doku-Diff selbst einzulesen kostet
   Hauptagent-Tokens zum vollen Preis und ist unnötig (das Review ist menschliche Aufgabe). Der
   Hauptagent gibt das `git diff`-Kommando aus bzw. meldet nur „Doku aktualisiert, bitte prüfen".
   So schützt git die Single-Source-Dateien vor Drift durch ein schwächeres Modell.
   **Kein Commit/Merge ohne ausdrücklichen Befehl** (s. AGENTS.md Regel 8).

## Worauf der Skill NICHT zielt
- Kein Auto-Trigger: „Modul fertig" ist eine semantische Bewertung, kein git-Ereignis — der Skill
  wird aufgerufen (vom User oder wenn der Haupt-Agent den Abschluss erkennt), nicht automatisch.
- git diff **erzeugt** keine Doku — es liefert nur Scope. Bedeutung + Routing bleiben Pflicht.
