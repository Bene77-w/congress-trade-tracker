# Committee-Mapping-Agent — Architektur- und Kostenanalyse

**Zweck dieses Dokuments:** Briefing für einen **externen, unabhängigen Agenten**.
Er soll die Frage unten eigenständig einschätzen. Alle Fakten sind so notiert,
dass die Bewertung **ohne Repository-Zugriff** möglich ist; Pfade sind trotzdem
angegeben, falls Zugriff besteht.

**Stand:** 2026-08-04 · Repo: `congress_tracker`, Branch `agent/committe_matcher`
· Agent-Code: `scripts/committee_mapping_agent/` (13 Module, 2.321 Zeilen)

---

## 1. Die Frage

> Der Workflow „Subcommittee → Sektor/Industry" soll **effektiver** werden.
> Ist eine **grundlegend neue Architektur** sinnvoll, oder die **Anpassung der
> bestehenden**, um die Output-Qualität bei geringeren Kosten zu halten oder zu
> erhöhen?

Nebenbedingungen aus dem Projekt:
- Alle Filter-/Verfahrensparameter müssen konfigurierbar bleiben (`config.py`).
- Der menschliche Review-Schritt (xlsx-Round-Trip) ist gesetzt und soll bleiben.
- Das Ergebnis ist ein **Master-Datensatz**, kein Laufzeit-Service.

---

## 2. Fachlicher Kontext — wozu dient das Mapping?

Das Projekt trackt Aktien-Trades von US-Kongressmitgliedern und bewertet sie mit
einem Score. Ein Signal darin ist der **Committee-Match**: Handelt ein Mitglied
eine Aktie, deren Branche in die Zuständigkeit („Jurisdiction") eines Ausschusses
fällt, in dem es sitzt? Das ist der Insiderwissen-Verdachtsmoment.

Dafür braucht es eine Tabelle
`(Kammer, Committee, Subcommittee) → {(Sektor, Industry)}`.
Das ist der **Master** `data/mappings/subcommittee_sector_industry_mapping.csv`
mit 7 Spalten: `Chamber, Committee, Subcommittee, Sector, Industry, Sector_Rank,
Konfidenz`.

### Wie der Master konsumiert wird (wichtig für die Zielfunktion)

`config/committee_mapping.py` (`CommitteeMapper`) lädt ihn in zwei Dicts und
`tags/committee_tag.py` gleicht ab. Der Abgleich ist ein **exakter String-Match**
von Sektor und Industry des gehandelten Tickers gegen die Mapping-Zeilen des
Mitglieds. Der Default-Modus ist `RELEVANT_SECTORS_MATCH_MODE =
'sector_and_industry'` — es zählt also nur ein Treffer, wenn **Sektor UND
Industry** übereinstimmen. Die Industry-Ebene ist somit produktiv wirksam, nicht
dekorativ.

**Zwei Befunde zur tatsächlichen Wirkung der Spalten:**

| Spalte | Downstream-Wirkung |
|---|---|
| `Sector` + `Industry` | **Voll wirksam.** Binäre Mengen-Entscheidung: Zeile da → Match möglich; Zeile fehlt → kein Match. |
| `Sector_Rank` | **Marginal.** Einzige Nutzung: `tags/committee_tag.py:253` — bei Dedup identischer (Sektor, Industry)-Paare gewinnt der niedrigere Rank. Ändert nie, *ob* gematcht wird, nur welcher Rollen-Eintrag überlebt. |
| `Konfidenz` | **Keine.** Kein Konsument im gesamten Produktivpfad (`tags/`, `signals/`, `scoring/`, `app/`) liest sie. Sie dient ausschließlich dem menschlichen xlsx-Review. |

Konsequenz: Die Optimierungs-Zielfunktion des Agents ist **Precision/Recall einer
Mengenzuordnung**. Alles, was Modell-Tokens in die Kalibrierung der
Konfidenz-Stufen investiert, zahlt derzeit nur auf den manuellen Review ein.

---

## 3. Ist-Architektur

### 3.1 Pipeline (CLI: `python -m scripts.committee_mapping_agent.run <cmd>`)

```
enrich-taxonomy   (einmalig)  11 LLM-Calls → Taxonomie mit Beschreibungen
      │
research   ──►  je Committee 1 agentischer Tool-Loop (web_search + fetch_page)
      │         Output: Jurisdiktionstext + wörtliches Zitat + Quell-URL
      │         + grounding_quality ∈ {direct, fallback, none}
      ▼
map        ──►  je Einheit: Stufe 1 (Sektoren wählen+ranken)
      │                     Stufe 2 (je gewähltem Sektor: Industries wählen)
      │         danach deterministische validate-Schicht
      ▼
review     ──►  DERZEIT DEAKTIVIERT (config.REVIEW_ENABLED = False)
      ▼
export-review ─►  3 xlsx-Dateien mit `comment`-Spalte
      │
   [Mensch editiert die xlsx]
      ▼
finalize   ──►  Kommentare einarbeiten → Staging-CSV
promote    ──►  Staging → Master (mit Timestamp-Backup)
```

Jede Stufe cacht ihr Ergebnis als JSON (`cache/research|mapping|review/`) und ist
wiederaufsetzbar; `--force` erzwingt Neuberechnung. `--chamber` / `--committee`
filtern.

### 3.2 Bausteine, die die Qualität sichern (bewusste Design-Entscheidungen)

1. **Allow-Liste als harte Grenze.** Die kanonische Menge (11 Sektoren, 145
   Industries) kommt immer aus `yf_sectors_with_industries.json`. Die LLM-
   Anreicherung fügt nur Beschreibungen hinzu und kann die Menge nicht
   korrumpieren. Jede Modellzeile außerhalb der Allow-Liste wird verworfen.
2. **Grounding-Pflicht.** Jede Einheit braucht ein wörtliches Zitat von der
   Primärquelle. Zitat < 15 Zeichen → `grounding_quality = 'none'`.
3. **Konfidenz-Deckel.** `GROUNDING_CONFIDENCE_CAP`: `direct → max high`,
   `fallback → max medium`, `none → max low`. Deterministisch in `validate.py`,
   nicht dem Modell überlassen.
4. **Zweistufiges Mapping.** Stufe 1 sieht die volle Taxonomie, Stufe 2 nur die
   Industries des jeweiligen Sektors — das Modell sieht nie sektorfremde
   Industries.
5. **Byte-stabile Prompt-Prefixe** (`taxonomy.build_prefix_block` /
   `build_sector_block`) für DeepSeeks Auto-Prompt-Caching.
6. **Stabile `mapping_id`** (SHA1 über Identitätsfelder) für den xlsx-Round-Trip.
7. **Repair-Reprompt-Loop** mit jsonschema-Validierung clientseitig, weil DeepSeek
   kein striktes `json_schema` unterstützt.

Diese sieben Punkte sind aus meiner Sicht der wertvolle Teil der Architektur und
sollten bei jeder Umbau-Variante erhalten bleiben.

### 3.3 Modell-/Effort-Konfiguration (`config.py`)

```python
MODELS = {
    "research":  ("deepseek-v4-flash", "high"),
    "mapping":   ("deepseek-v4-pro",   "high"),
    "review":    ("deepseek-v4-pro",   "max"),   # deaktiviert
    "finalize":  ("deepseek-v4-pro",   "max"),
}
MAX_TOKENS = {alle Rollen: 16384}   # Reasoning-Tokens zählen dagegen
REVIEW_ENABLED    = False
MAX_SEARCH_TURNS  = 16              # Tool-Turns je Research-Committee
FETCH_MAX_CHARS   = 20000           # je fetch_page-Antwort
MAP_EMPTY_RETRIES = 5
MAP_EMPTY_BACKOFF = (2, 4, 8, 16, 32)   # Sekunden
MAP_RETRY_ONLY_GROUNDED = True
MAP_CONCURRENCY   = 6               # Threads
```

Der Effort ist **pro Rolle**, nicht pro Stufe konfigurierbar — Stufe 1 und
Stufe 2 des Mappings laufen zwangsläufig mit demselben Modell und Effort.

---

## 4. Skalen und Kostenrechnung

### 4.1 Verifizierte Mengengerüste

| Größe | Wert |
|---|---|
| Committees House / Senate | 26 / 12 = **38** |
| Subcommittees House / Senate | 108 / 70 = **178** |
| Committees ohne Subcommittees (Budget, Ethics, 4× Joint, 1× Select) | 7 |
| **Zu mappende Einheiten** (178 Subs + 7 Committee-Ebenen) | **185** |
| Taxonomie | 11 Sektoren, 145 Industries |
| Voller Taxonomie-Prefix (angereichert) | 21.616 Zeichen ≈ **6.200 Tokens** |
| Sektor-Block (Stufe 2), Median | 1.884 Zeichen ≈ **540 Tokens** |
| Vorgänger-Master (nicht vom Agent erzeugt) | 861 Zeilen / 147 Einheiten |
| … davon ø Sektoren je Einheit | **2,16** (Median 2, max 6) |
| … davon ø Zeilen je Einheit | 5,9 (Median 5, max 35) |

### 4.2 Call-Budget eines Full-Runs (Ist-Architektur)

| Stufe | Calls | Bemerkung |
|---|---|---|
| Research | 38 Tool-Loops à 1–16 API-Calls | = 38–608 Calls |
| Mapping Stufe 1 | 185 | volle Taxonomie im Prompt |
| Mapping Stufe 2 | ≈ 400 | 185 × 2,16 Sektoren (Hochrechnung aus Vorgänger-Master) |
| Leer-Retries | bis +5 volle Stufe-1+2-Durchläufe je betroffener Einheit | |
| Review | 0 | deaktiviert |
| **Summe Mapping** | **≈ 585** | alle auf `deepseek-v4-pro`, `effort=high` |

### 4.3 Token-Hochrechnung

**Research — der dominante Input-Posten.** Der Tool-Loop hängt jedes
`fetch_page`-Ergebnis (bis 20.000 Zeichen ≈ 5.700 Tokens) an die Historie, und
die **gesamte Historie geht in jeden Folge-Turn erneut als Input**. Bei n Turns
mit je einem Fetch wächst der kumulierte Input quadratisch:
`≈ 5.700 × n(n+1)/2`.

| Turns je Committee | Input/Committee | × 38 Committees |
|---|---|---|
| 6 | ≈ 120.000 | ≈ **4,5 Mio.** |
| 10 | ≈ 314.000 | ≈ **11,9 Mio.** |
| 16 (Limit) | ≈ 775.000 | ≈ **29,5 Mio.** |

**Mapping.**

| Posten | Tokens |
|---|---|
| Stufe-1-Input | 185 × ≈ 6.700 ≈ **1,24 Mio.** (Prefix identisch → cachefähig) |
| Stufe-2-Input | 400 × ≈ 1.100 ≈ **0,44 Mio.** (11 verschiedene Prefixe → cachefähig) |
| Output inkl. Reasoning | 585 × 1.500–3.000 ≈ **0,9–1,8 Mio.** |

**Preise sind hier bewusst nicht eingesetzt** — bitte mit den aktuellen
DeepSeek-V4-Tarifen (Input Cache-Miss / Cache-Hit / Output) selbst bewerten.
Strukturell gilt: Reasoning-Tokens werden als Output abgerechnet und sind der
teuerste Posten pro Token; Cache-Hit-Input ist der billigste.

### 4.4 Es gibt keine Ist-Messung

`llm_client.USAGE_LOG` sammelt die `usage`-Objekte aller Calls, und
`config.REPORT_PATH` (`run_report.json`) ist definiert — **aber nichts schreibt
den Report jemals**. Kein Modul außer der Definition referenziert beides
(verifiziert per Grep über das gesamte Repo).

Zusätzlich: Der Probelauf über 3 Committees ist abgeschlossen, die zugehörigen
Caches existieren im Arbeitsverzeichnis **nicht mehr** (`cache/` fehlt komplett).
Es liegen also **keinerlei empirische Kosten- oder Laufzeitdaten** vor. Alle
Zahlen in 4.3 sind Hochrechnungen aus Code und Datenmengen, keine Messungen.

---

## 5. Beobachtungen

Faktisch notiert, ohne Wertung — die Bewertung ist Aufgabe des externen Agenten.

**B1 — Research ist ein agentischer Loop für eine im Kern statische Aufgabe.**
Die gesuchten Jurisdiktionstexte stehen an stabilen Orten: den Jurisdiction-/
About-/Rules-Seiten der 38 Committee-Websites, ergänzt um House Rule X bzw.
Senate Rule XXV. Diese URLs ändern sich im Wesentlichen einmal pro Kongress
(2 Jahre). Aktuell sucht das Modell sie bei **jedem** Lauf per DuckDuckGo neu.
Folgen: quadratisch wachsender Input (4.3), Nicht-Reproduzierbarkeit (die
Suchergebnisse ändern sich), keine Auditierbarkeit der verwendeten Quelle.

> **Design-Begründung des Projektinhabers (bewusste Entscheidung, kein Versehen):**
> Der agentische Loop wurde gewählt, weil sich die Committee-Seiten **inhaltlich**
> ändern — Zuständigkeitsbeschreibungen werden je nach politischer Agenda
> angepasst, auch unabhängig von Wahlen —, und weil sich Layout und Formatierung
> ändern. Ein starrer Parser (CSS-Selektor/Regex) wäre nach der ersten
> Neugestaltung unbrauchbar; deshalb sollte ein LLM die Seite lesen. Die
> **Adresse** hingegen wurde als stabil eingeschätzt.
>
> Diese Begründung trifft die Extraktion, nicht die Suche: O1 belässt das Lesen
> und Zitieren beim LLM und ersetzt nur das wiederholte Auffinden der Seite. Ein
> zusätzliches Argument in dieselbe Richtung: Bei fester Adresse ist eine
> inhaltliche Änderung des Jurisdiktionstexts als Diff gegen den gecachten
> Rohtext **erkennbar**; bei jedesmaliger Neusuche sind „Ausschuss hat
> umformuliert" und „Suchmaschine lieferte eine andere Seite" nicht
> unterscheidbar. Das reale Restrisiko des Katalogs ist **URL-Rot** bei
> Seitenumbau — adressierbar über HTTP-Status-/Leerextrakt-Prüfung mit Rückfall
> auf den bestehenden agentischen Loop, der die neue Adresse in den Katalog
> zurückschreibt (selbstheilend, Loop wird vom Regelfall zur Ausnahme).

**B2 — Der Research-Prompt fordert immer auch die Committee-Ebene an.**
`research._expected_keys()` setzt `__committee__` unbedingt auf die Liste. Für
die 31 Committees **mit** Subcommittees wird dieses Ergebnis aber nie gemappt:
`run._units_to_map()` gibt in dem Fall nur die Subcommittees zurück, und
`CommitteeMapper.get_all_sectors_for_committee()` aggregiert die Committee-Ebene
downstream ohnehin aus den Subcommittee-Zeilen. Der Text wird recherchiert und
verworfen (er dient allenfalls indirekt als Suchkontext).

**B3 — Der Fan-out im Mapping ist 1 + N Calls je Einheit.**
Bei ø 2,16 Sektoren sind das ≈ 3,2 Calls je Einheit, ≈ 585 gesamt. Jeder Call
trägt den vollen Reasoning-Overhead von `effort=high` auf dem Pro-Modell.

**B4 — Subcommittees werden ohne Eltern-Kontext gemappt.**
`mapping._unit_context()` übergibt nur `text` + `quote` der Einheit selbst. Bei
knappen Subcommittee-Jurisdiktionen („Oversight and Investigations") fehlt dem
Modell die Verankerung im Zuständigkeitsbereich des Mutter-Committees. Es gibt
auch keine Konsistenzbindung: die Subcommittees eines Committees können
untereinander widersprüchliche Sektor-Mengen erhalten.

**B5 — Die Leer-Retry-Politik ist aggressiv und methodisch gerichtet.**
Bleibt eine Einheit ohne Zeile, werden bis zu 5 volle Stufe-1+2-Durchläufe mit
Backoff bis 32 s wiederholt (bis 62 s Wartezeit, 6× Kosten für genau die
schwierigsten Einheiten). Wiederholt wird nur bei `grounding_quality == 'direct'`,
und abgebrochen wird beim ersten **nicht-leeren** Ergebnis — das Verfahren
würfelt also gezielt, bis Zeilen entstehen, und schafft dadurch einen
systematischen Zug zu Falsch-Positiven bei den Einheiten mit dem schwächsten
Signal.

**B6 — Effort und Modell sind pro Rolle, nicht pro Stufe steuerbar.**
Stufe 2 (Auswahl aus 6–25 beschriebenen Industries eines einzigen Sektors) läuft
zwangsläufig mit derselben Modell-/Effort-Kombination wie Stufe 1 (Auswahl aus
11 Sektoren über 145 Industries Kontext). `finalize` — das mechanische Einarbeiten
menschlicher xlsx-Kommentare — steht auf `effort="max"`.

**B7 — Konfidenz hat keinen produktiven Konsumenten** (siehe Tabelle in Abschnitt
2). Der Apparat dahinter ist erheblich: eine Kalibrierungs-Rubrik in beiden
Mapping-Prompts, `normalize_confidence`, die Cap-Logik in `validate.py`, die
gesamte Review-Rolle mit `effort="max"`, sowie Spalten im xlsx.

**B8 — Es existiert ein Vorgänger-Master als Referenzdatensatz.**
861 Zeilen über 147 Einheiten, nicht vom Agent erzeugt. Er deckt 147 der 185
Einheiten ab (38 fehlen). Er ist damit als Silber-Referenz für Regressions-
Vergleiche verfügbar — ein neuer Lauf lässt sich gegen ihn diffen statt blind
bewertet zu werden.

**B9 — Es gibt kein Gold-Set und keine Qualitätsmetrik.**
Weder eine manuell verifizierte Referenz-Teilmenge noch ein Skript, das zwei
Läufe gegeneinander auswertet (Precision/Recall, Zeilen-Diff, Verteilungs-
Vergleich). „Qualität halten oder erhöhen" ist im gegenwärtigen Setup **nicht
messbar**, nur behauptbar.

**B10 — Nutzungsprofil.** Der Master ist ein Batch-Artefakt, kein Laufzeit-
Dienst. Fachlich notwendig sind Läufe bei Kongress-Wechsel (alle 2 Jahre) und bei
Umbenennungen von Subcommittees. Die praktisch relevante Lauf-Häufigkeit ergibt
sich hingegen aus der **Entwicklung**: jede Prompt-, Taxonomie- oder
Verfahrensänderung, die man validieren will, bedeutet einen (Teil-)Full-Run.

**B11 — Mikro-Ineffizienz.** `taxonomy.is_valid_industry()` ruft für jede geprüfte
Industry `allowed_pairs()` auf, das die 145er-Menge jedes Mal neu aufbaut. Reine
CPU-Kosten, kein API-Geld; `validate.validate_rows()` nimmt den Set optional
bereits vorberechnet entgegen, `mapping.py` nutzt diesen Pfad aber nicht.

---

## 6. Zu bewertende Optionen

Der externe Agent möge diese Optionen prüfen, verwerfen oder ergänzen. Sie sind
**nicht** vorsortiert und nicht alle miteinander verträglich.

**O1 — Nur die Quellen-*Suche* ersetzen, nicht die Extraktion.**
Einmalig eine CSV `committee_sources.csv` mit den Jurisdiction-URLs der 38
Committees (+ Rule X / Rule XXV als Fallback) erzeugen und einchecken. Der
Research-Schritt wird dann: deterministischer Fetch (Code, keine LLM-Kosten) →
**ein** Extraktions-Call je Committee auf dem Seitentext. Das Lesen, Verstehen
und Zitieren bleibt ausdrücklich beim LLM (siehe Design-Begründung unter B1) —
entfernt wird nur das wiederholte Auffinden bekannter Seiten. Der agentische
Loop bleibt als selbstheilender Fallback für Katalog-Misses (404, Redirect,
leerer Extrakt) und schreibt gefundene Adressen in den Katalog zurück.

**O2 — Mapping auf einen Call je Einheit reduzieren.**
Volle Taxonomie im gecachten Prefix, das Modell gibt direkt `(Sektor, Industry,
Rank, Konfidenz)`-Zeilen aus. 185 statt 585 Calls. Offene Frage: sinkt der Recall,
wenn 145 Industries in einem Zug statt sektorweise betrachtet werden?

**O3 — Hierarchische Vererbung Committee → Subcommittee.**
Erst das Mutter-Committee auf voller Taxonomie mappen, dann die Subcommittees nur
noch innerhalb des Sektor-Kandidatenraums des Committees. Fachlich gedeckt
(Subcommittee-Jurisdiktion ⊆ Committee-Jurisdiktion), erzeugt Konsistenz innerhalb
eines Committees und verkleinert den Stufe-1-Prompt drastisch. Risiko: Fehler auf
Committee-Ebene propagieren nach unten.

**O4 — Batching mehrerer Einheiten je Call.**
Ein Committee hat im Mittel 4,7 Subcommittees. Ein Call pro Committee mit allen
Subcommittee-Jurisdiktionen amortisiert den 6.200-Token-Prefix über alle
Einheiten. Risiko: Verwechslungen zwischen Einheiten, längerer Output, ein
JSON-Fehler betrifft mehrere Einheiten.

**O5 — Embedding-Vorfilter statt Stufe 1.**
Jurisdiktionstext und die 145 Industry-Beschreibungen einbetten,
Cosine-Similarity, Top-K (z. B. 25) als Kandidatenraum, dann ein LLM-Call zur
Auswahl. Ersetzt die Sektor-Vorauswahl durch ein deterministisches, praktisch
kostenloses Retrieval. Preis: eine neue Abhängigkeit (lokale
sentence-transformers oder ein Embedding-Endpoint) und die Recall-Abhängigkeit
vom Embedding-Modell.

**O6 — Effort-/Modell-Kaskade.**
Effort pro **Stufe** konfigurierbar machen; Standardpfad auf dem Flash-Modell,
Eskalation auf Pro nur bei leerem oder unsicherem Ergebnis. `finalize` von `max`
herunterstufen.

**O7 — Retry-Politik entschärfen.**
1 Retry statt 5, anschließend `no_mapping` in den menschlichen Review geben.
Backoff als Rate-Limit-Behandlung führen, nicht als Qualitätssicherung.

**O8 — Messbarkeit herstellen (Voraussetzung, keine Alternative).**
`run_report.json` tatsächlich schreiben (Tokens je Rolle/Stufe, Cache-Hit-Anteil,
Calls, Dauer, Fehlerquote); ein Diff-Skript gegen den Vorgänger-Master (B8); ein
manuell verifiziertes Gold-Set von 15–20 Einheiten.

**O9 — Konfidenz-Apparat entscheiden.**
Entweder produktiv nutzen (z. B. Score-Gewichtung nach Konfidenz oder Filterung
von `Niedrig`-Zeilen im `CommitteeMapper`) oder auf ein Minimum verschlanken, das
den menschlichen Review trägt, ohne Reasoning-Effort zu binden.

---

## 7. Offene Fragen an die Einschätzung

1. **Neubau oder Anpassung?** Falls Neubau: welche der sieben
   Qualitätsbausteine aus 3.2 müssen zwingend übernommen werden?
2. Welche Reihenfolge maximiert Nutzen pro Aufwand — angesichts der Tatsache,
   dass es (B9) noch keine Qualitätsmetrik gibt?
3. Wie ist der Trade-off O2/O3/O4 gegen den möglichen Recall-Verlust zu
   bewerten? Gibt es ein Verfahren, das ohne Gold-Set entscheidbar wäre?
4. Ist die Zielfunktion richtig gefasst? Der produktive Effekt ist eine binäre
   Mengenzugehörigkeit (Abschnitt 2). Sollte das Verfahren dann bewusst auf
   **Recall** optimieren (lieber eine Zeile zu viel, weil der Score ohnehin nur
   ein Signal unter mehreren ist) — oder auf **Precision** (weil
   Falsch-Positive den Committee-Match als Signal entwerten)?
5. Angesichts B10: Wie viel Optimierungsaufwand rechtfertigt ein Artefakt, das
   fachlich alle zwei Jahre neu erzeugt werden muss? Wo liegt die Grenze, ab der
   die Optimierung teurer ist als das, was sie spart?

---

## Anhang — Einschätzung des Erst-Agenten

> **Anker-Warnung:** Dieser Abschnitt ist bewusst ans Ende gestellt. Bitte erst
> eine eigene Einschätzung bilden, dann hier gegenlesen. Abweichung ist erwünscht.

**Empfehlung: Anpassung, kein Neubau.** Die Architektur ist strukturell richtig
für ihren Zweck — die sieben Bausteine aus 3.2 (Allow-Liste, Grounding-Pflicht,
deterministische Validierung, Cache je Stufe, stabile Row-IDs, xlsx-Round-Trip)
sind genau das, was ein einmalig erzeugtes, menschlich abzunehmendes
Master-Artefakt braucht, und sie sind teuer erarbeitet. Die Kosten sitzen nicht
in der Architektur, sondern in drei lokal behebbaren Stellen: dem agentischen
Research-Loop (B1, quadratischer Input), dem Call-Fan-out im Mapping (B3) und dem
undifferenzierten Reasoning-Effort samt aggressiver Retries (B5, B6).

**Priorisierung:** O8 zuerst — ohne Ist-Messung und Referenz ist jede
Effizienzaussage Spekulation, und man kann eine Effort-Senkung nicht von einer
Qualitätsregression unterscheiden. Danach O1 (größter Einzelhebel, senkt Kosten
*und* erhöht Reproduzierbarkeit — die seltene Maßnahme ohne Zielkonflikt), dann
O3 kombiniert mit O6, zuletzt O7 und O9.

**Der ökonomisch entscheidende Punkt** ist meines Erachtens Frage 5, und zwar mit
einer Wendung: Nicht der eine Produktivlauf alle zwei Jahre ist der Kostentreiber,
sondern die Validierungsläufe während der Entwicklung. Optimierungen sollten
deshalb primär danach beurteilt werden, ob sie **die Iterationsschleife
verbilligen und verkürzen** — genau das leisten O8 (Messung), O1
(Reproduzierbarkeit: gleicher Input → gleiches Ergebnis, damit ein Diff
überhaupt aussagekräftig ist) und die Cache-Struktur, die bereits existiert.
Ein Umbau, der nur den Produktivlauf verbilligt, amortisiert sich bei dieser
Lauf-Frequenz vermutlich nie.
