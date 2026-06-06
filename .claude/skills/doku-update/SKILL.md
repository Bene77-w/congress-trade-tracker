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

1. **Scope aus git** (Ground Truth, was dieser Branch angefasst hat):
   ```bash
   git diff main...HEAD --stat   # Branch-Footprint ggü. Merge-Base (committed)
   git status                    # noch uncommitted
   ```
2. **Routing** — `organisation/DOKU_INDEX.md` lesen: jede geänderte Datei / jeden Änderungstyp
   (neue Spalte, neue Stage, neues Signal, neues CLI-Flag …) der „Aktualisieren bei"-Spalte
   zuordnen → Liste der zu pflegenden Doku-Dateien.
   - **Bei Modul-Abschluss** (Modul war „In Arbeit", ist jetzt fertig): WIP-Detail aus dem
     „Aktueller Stand (WIP)"-Block von `AGENTS.md` nach `CHANGELOG.md` migrieren **und den
     WIP-Block leeren/auf das nächste aktive Modul umstellen**. So bleibt der WIP-Block die
     einzige handgepflegte Stelle nicht dauerhaft veraltet.
3. **Briefing bauen** (Struktur s. `.claude/agents/doku-agent.md`): semantische Zusammenfassung
   (Pitch) + die git-diff-Dateiliste als Komplettheits-Check. Jede im Diff geänderte Code-Datei
   muss entweder im Briefing eine Doku-Konsequenz haben **oder** explizit als „doku-irrelevant"
   begründet sein — keine stille Lücke.
4. **Sub-Agent rufen** (Default Sonnet; in einer **DeepSeek-Session** `model: "flash"` übergeben):
   ```
   Agent({
     subagent_type: "doku-agent",
     description: "Doku-Update nach <Modul/Änderung>",
     prompt: "## Briefing: …"            // + model: "flash" nur in DeepSeek-Sessions
   })
   ```
5. **Review vor Merge** — nach dem Lauf die Doku-Änderungen zeigen:
   ```bash
   git diff -- organisation/ CHANGELOG.md AGENTS.md
   ```
   Der User prüft, bevor gemergt wird. So schützt git die Single-Source-Dateien vor Drift durch
   ein schwächeres Modell. **Kein Commit/Merge ohne ausdrücklichen Befehl** (s. AGENTS.md Regel 8).

## Worauf der Skill NICHT zielt
- Kein Auto-Trigger: „Modul fertig" ist eine semantische Bewertung, kein git-Ereignis — der Skill
  wird aufgerufen (vom User oder wenn der Haupt-Agent den Abschluss erkennt), nicht automatisch.
- git diff **erzeugt** keine Doku — es liefert nur Scope. Bedeutung + Routing bleiben Pflicht.
