[12:08, 19.8.2026] Bene: # Ablaufplan Modulimplementierung

## Ziel
- Optimierung des workflows von der Produktidee bis hin zur abgenommenen Integration.
- durchdachte, verständliche Planung
- klare anweisungen an Implemetierungsagents um fehlimplementierungen aufgrund von Missverständissen und lückenhafter planung zu minimieren.
- gezielte, regelmäßige reviews um Fehler früh zu identifizieren und zu beheben

## Vorgehen
- Um Missverständisse zwischen User und mehreren Agents vorzubeugen, pflegen wir je Phase eine Single Source of Truth:
   - Phase 1-3: *Produktbrief*: single source of truth für Implementierungsagents
      1. Zielvorstellung definieren
      2. Architekturentscheidungen bennen und kurz begründen (warum pros der enstcheidung den cons überwiegen), falls finale Enstcheidung noch offen ist denkbare Alternativen, mit pro und contra benennen.
      - kompakt halten ->  kompakte Sprache und auf Recherche Berichte in extra Datei verweise
   - Phase 4 *Implementierungsplan*: single source of truth für Implementierungsagents:
      - konkrete durchdachte Anweisungen
      - eingeteilt in phasen, nach jedem phasenabschluss automatisches agentreview (skill: erstelle ich später)

<—passende Modell- und Effortwahl je nach phase um effizient vorzugehen siehe @Ablaufplan_Modulimplementierung_Modelle.md —>


<!--Kommentare Ziele und Vorgehen:
Ziele zu beginn vollständig definieren (du hast teile ausgelassen) und Neues Vorgehen zur Zielerreichung kompakt nennen und komprimiert erläutern. Fokus auf das Ziel und der Weg dorthin statt auf dont's, damit mein ziel erreicht wird ist es wichtiger das du es verstehst und umsetzen kannst sattt dir zu sagen was du nicht machen sollst. Es ist nutzlos wenn man skifahren lernen möchte und der lehrer dir sagt fall nicht hin statt dir zu erklären das du das Gleichgewicht halten sollst und wie man das gleichgewicht hält.

Wichtig ist das du dich an der überschrift orierntierst und keine überflüssigen informationen nennst die total fehl am platz sind. regeln die in der globalen oder lokalen agents.md sind müssen nicht unter ziele und vorgehen genannt werden, da sie in jedem projekt divergieren oder eh schon gelten (je nach lokal oder global agenst.md). solche punkte sorgen nur für zeitverschwendung und unnötigen overhead

Wichtig du musst lernen wie ich die antworten auf meine fragen am effizientesten verstehe und wie man Erklärungen auf die relevanten punkte reduziert sodass sie verständlich und kompakt sind. du wirst dementsprechend deine kommunikation anpassen müssen.

-->




## Phase 1 – Produkt und Anforderungen

1. *User-Brainstorming*
   - keine ai, User definiert für sich selbst gewünschtes Endprodukt.
2. *Projektpitch* an AI
   - User pitcht die Idee an einen Ai Agent
   - Projektbrief erstellen -> Ziel definieren
   - Ai research & Brainstorming welche Systeme und Achritekturmöglichkeiten es gibt bsp:
      - Bsp: openrouter sdk vs openai sdk; können wir APIs nutzen um arbeit zu sparen, welchen api anbieter nehmen wir etc, gibt es bereits eine opensource AÖternative zu meiner Projektidee etc.
   - verschiedene Architekturmöglichkeiten und systeme besprechen, grobes Konstrukt festhalten, ob finale Achritektur bereits entschieden wird liegt beim User.
<!--h -->



## Phase 2 – Architektur und konkurrierende Pläne

3. *Projektbestand untersuchen*
   - ground zero analyse: Code, Datenmodelle, Schnittstellen, Konfiguration, Doku etc. prüfen
   - bestehende Konventionen und Rückwärtskompatibilität in Projektbrief festhalten
4. *Primärentwurf erstellen*
   - Pros & cons verbleibender Architekturvarianten abwägen
   - finale Achritekturvariante wählen und in Prokjektbrief festhalten, irrelevante entwürfe entfernen
   - Plan A erstellen
5. *Unabhängigen Gegenentwurf Plan B erstellen* von einem frischen Agent
   - identischen Produktbrief und Projektstand verwenden
   - Plan A und dessen Überlegungen nicht bereitstellen
6. *Pläne vergleichen und synthetisieren* – frischer starker Agent
   - beide Pläne zuerst getrennt nach einehitlichem Schema bewerten (später verlinkung hier)
   - neuen Plan verfassen, basierend auf den learnings aus der Bewertung der ersten 2 Pläne.


## Phase 3 – Planreview und Freigabe

7. **Vorprüfung durch einen frischen Review-Agent**
   - Vollständiges Plan-review und korrektur durch frischen Review agent
   - korrigierten Plan als Review-Kandidat einfrieren
8. **Plan in unabhängige Reviewabschnitte teilen**
   - Hauptreviewer zerlegt den eingefrorenen Plan entlang fachlicher Zusammenhänge und Abhängigkeiten in lückenlose, möglichst überschneidungsfreie Abschnitte, um diese von seperaten subagents einzelnd analysieren zu lassen
   - der Hauptreviewer teilt einen frischen Subagent mit passnedem modell und effort je Abschnitt ein, um diesen einzelnd zu reviewn
   - Subagent prüft Arbeitsschritte, Abhängigkeiten, Umsetzbarkeit, Risiken,
     Testbarkeit und Übergabe an den nächsten Abschnitt; er verändert den Plan nicht
9. **Findings konsolidieren und Plan überarbeiten**
   - Subagent reporten in `projektname_review_report.md`
   - Hauptreviewer klassifiziert reports als `akzeptiert`, `abgelehnt`,
     `verschoben` oder `bereits abgedeckt`
   - Lösungskonflikte anhand des Projektziels entscheiden, bei unsicherheit user fragen
   - akzeptierte Änderungen gemeinsam einarbeiten und Plan kompakt halten
   - letzer technischer konssitenzcheck von hauptreviewer
   - Weitere Reviewrunde nach Konsistenzcheck nur bei neuem blockierendem/schwerwiegendem Finding.
   - Ziel: ausreichend vollständig, konsistent und kontrolliert betreibbar – nicht theoretisch „objektiv fehlerlos“

**Planoragnisation**: Die Implementierungsanleitung des plans soll in einzelne Phasen eingeteilt werden. vor folgenschweren Arbeitsschritten ein gate einbauen
   - Phase: Eine Gruppe fachlich oder technisch zusammenhängender Arbeitsschritte, die gemeinsam ein klar abgrenzbares und testbares Zwischenergebnis erzeugen.
   - Gate: Die Grenze vor einer nachfolgenden Arbeitsgruppe, wenn deren Beginn eine bewusste Freigabe erfordert – etwa wegen Kosten, externen Calls, irreversiblen Änderungen, erhöhtem Risiko oder einer noch offenen Entscheidung. kann auf anweisung des users von den agents ignoriert/azsgelassen werden.



### Phase 3 - Details

- Hauptreview Agent führt ein vollständiges Review durch, dh. er konzentriert sich nicht nur auf eine rein technische Planprüfung sondern ua. auch auf:
   - Zielbezug
   - Scope und unnötige Komplexität
   - globale Widerspruchsfreiheit
   - insbesondere auf übergreifende Architektur
   - Vollständigkeit des GEsmatplans
- Subagents konzentrieren sich auf eine technische prüfung ihrer abschnitte, dazu gehört ua.:
   - Vollständigkeit: Fehlen notwendige technische Arbeitsschritte?
   - Reihenfolge: Sind Voraussetzungen und Abhängigkeiten korrekt angeordnet?
   - Umsetzbarkeit: Lassen sich die Anweisungen mit dem vorhandenen Projektstand umsetzen?
   - Architekturkonsistenz: Passen Änderungen zu bestehenden Modulen, Datenstrukturen und Schnittstellen?
   - Technische Risiken: Welche Fehlerzustände, Seiteneffekte oder Integrationsprobleme können auftreten?



## Phase 4 – Implementierung

- Implementierung in der Phasen Reihenfolge des freigegebenen Implementierungsplans umsetzen.
   - Falls alle Voraussetzungen für eine Phase erfüllt sind und ihrer Implementierung keine offenen Phasen beeinflusst darf sie auf useranweisung parallel implementiert werden
- nach jeder erfolgreicher phasenimplementierung ein agentreiview durchführen



### Meilenstein 3 – Endabnahme

- nach Abschluss aller Phasen vollständige Akzeptanz- und Regressionstests ausführen
- Gesamtintegration sowie Konsistenz von Plan, Entscheidungen, Code und Doku prüfen
- Abschluss erst ohne blockierende oder schwerwiegende Findings



