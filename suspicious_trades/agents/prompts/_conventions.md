# Template-Konventionen (Basis, §5g)

Dieses Dokument ist KEIN Agent-Prompt, sondern die bindende Konvention fuer alle
Rollen-Templates der Varianten. Es dient im Prompt-Editor des Control-UI als
Referenz und erster Editor-Testfall.

## Regeln

1. Ein Template = ein System-Prompt; Platzhalter im Format `{{snake_case}}`,
   gefuellt vom aufrufenden Code.
2. Dynamischer Lauf-Kontext (Trade-Tabellen, Briefings, User-Hinweise,
   Registry-Auszuege) wird NIE ins Template interpoliert, sondern als
   User-Message uebergeben (Cache-Stabilitaet).
3. Einheitliche Gliederung jedes Templates:
   1. Rolle & Auftrag
   2. Eingaben (was die User-Message liefert)
   3. Arbeitsweise & Tool-Nutzung (erlaubte Tools, Budget-Hinweise)
   4. Output-Format (Schema/Struktur, hartes Laengen-Budget)
   5. Qualitaetsregeln & Anti-Patterns
   6. Abschlussbedingung (wann fertig / wie abgeben)
4. Keine Emojis. Keine Behauptung ohne Quelle in Recherche-Rollen.
5. Tool-Wahl-Regel fuer berechtigte Rollen: Standard ist Search -> Score-Filter
   -> Extract; `web_map`/`web_crawl` nur bei bekannter Domain und
   Vollstaendigkeits-Bedarf, im Rahmen des `max_calls`-Budgets.
