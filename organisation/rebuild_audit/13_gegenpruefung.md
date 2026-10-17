# 13 Gegenprüfung des Gesamtberichts

**Stand:** 2026-10-17
**Prüfgegenstand:** `00_GESAMTBERICHT.md` und die Teilberichte 01 bis 12
**Methode:**
- Unabhängige, read-only Nachprüfung im Code und in beiden Master-Dateien
- Keine kostenpflichtigen Aufrufe
- Prüfskripte: `/tmp/audit_gegenpruefung/v1.py` bis `v4.py`

**Datenbasis:** Es gibt zwei Master-Stände, und die Teilberichte mischen sie.

| Datei | Datum | Zeilen | Größe | Genutzt von |
|---|---|---:|---:|---|
| `congress_tracker/data/processed/trades_master_complete.csv` (Haupt-Worktree) | 2026-08-11 | 114.347 | 645 MB | 02 |
| `congress_tracker-agent-committe_matcher/data/processed/…` (dieser Worktree) | 2026-07-31 | 114.245 | 643 MB | 01, 04, 11 (03 laut 00 ebenfalls) |
| `congress_tracker/data/cache/price_history.parquet` | 2026-08-11 | Kurse bis 2026-08-10 | 26 MB | – |
| Worktree-`price_history.parquet` | 2026-07-31 | Kurse bis 2026-07-15 | 25 MB | 04 |

Die Prüfungen unten liefen auf beiden Mastern. Die Ergebnisse unterscheiden sich nur in der dritten Stelle. An den Aussagen ändert die Mischung nichts, an einzelnen Zahlen im Gesamtbericht schon (siehe Abschnitt 5).

---

## 1. Verifikation der Befunde

| # | Befund laut 00 | Urteil | Beleg der Gegenprüfung |
|---|---|---|---|
| K1 | Renditen vor 2021-05 sind Platzhalter, „53 % aller Trades“ | **bestätigt, aber falsch beziffert und lokal behebbar** | Haupt-Master: 49.532 Trades mit `post_return_90d == 0`. Das sind 52,9 % der Trades **mit** Rendite und 43,3 % **aller** Trades. In den Jahren 2012 bis 2020 sind 100 % der vorhandenen Renditen exakt 0 (Anteil Null = Füllquote, z. B. 2020: 0,853/0,853). Ab 2022 liegt der Anteil unter 1,2 %. Der Cache beginnt bei 3.033 von 4.867 Tickern am 2021-05-28 (AAPL, MSFT, SPY, WFC). Das geschieht **wegen** und nicht „trotz“ `POST_TRADE_PRICE_FETCH_YEARS=5` (`config/settings.py:561`, `period=f'{fetch_years}y'` in `scoring/post_trade_returns.py:171`). Die Lückenprüfung (`_max_gap`, `POST_TRADE_MAX_PRICE_GAP_DAYS`) steht bereits im Worktree, allerdings **uncommittet** (`git diff HEAD -- scoring/post_trade_returns.py`). Damit ist K1 eine Kombination aus Setting, schon geschriebenem Fix und Neulauf. Strukturell ist das nicht. |
| K2 | Falscher Abgeordneter, 6.859 Trades | **bestätigt in der Zahl, abgeschwächt in der Deutung** | Unabhängig nachgerechnet: `Member_State` weicht bei 6.859 Zeilen (6,0 %, 54 BioGuideIDs) vom Quiver-State derselben BioGuideID ab. Lamar Smith (S000583) steht mit NJ im Master (507 Zeilen). Zusätzlich steht „Mr. Lamar Smith“ mit WA (133 Zeilen); das fehlt im Bericht. Rooney steht mit TN (625). `utils/name_matcher.py:335` (WRatio, Schwelle 85, `settings.py:321`) prüft keinen Bundesstaat. `import_trades.py:612-613` überschreibt `Member_Party`/`Member_State`, auf die die Quiver-Spalten `Party`/`State` gemappt sind (`:385-405`). **Aber:** `name` und `bio_guide_id` bleiben korrekt. Falsch sind die angereicherten Stammdaten und Ausschüsse, nicht die Person. „Trades hängen am falschen Abgeordneten“ überzeichnet das. Abhilfe im Bestand: ein State-Guard im Matcher oder ein BioGuide-Mapping für die Roster-CSV (die heute keine BioGuideID hat). Nebenbefund: `Member_State` mischt Kürzel und ausgeschriebene Namen (18.899 Zeilen mit „New Jersey“ usw. bei Ungematchten). |
| K3 | Ausschüsse ohne Zeitachse | **bestätigt** | Die Roster-CSVs (`data/mappings/house_member&(sub)committees.csv`, `senate_…`) haben nur `Name,Party,State,Committees,ExOfficio`, keine Gültigkeit und keine ID. Das ist der einzige wirklich strukturelle Befund. Seine Lösung hängt aber an einer **ungeklärten Datenquelle** (12: `committee-membership-current` nur aktuell, Historie „possibly partial“). Ein Rebuild löst K3 also nicht von selbst. |
| K4 | Ticker ohne Handelsdatum | **bestätigt (Stichprobe)** | Der Cache für FB beginnt am 2025-06-26 (Preisreihe des ProShares-ETF), META am 2021-05-28. Die übrigen Fälle (PCLN, UN …) wurden nicht nachgeprüft. |
| K5 | trade_id-Duplikate, Ursache „Inhaltshash, Quiver-Korrekturen erzeugen neue Zeilen“ | **Zahl bestätigt, Ursache widerlegt** | Haupt-Master: 102.012 eindeutige IDs bei 114.347 Zeilen, also 12.335 überzählige Zeilen. **Die Quiver-XLSX selbst enthält 11.152 über alle 20 Spalten identische Zeilen.** Master und XLSX haben exakt gleich viele Zeilen (114.347). Die Pipeline erzeugt im Vollauf also keine Dubletten, sie reicht die Quelle durch. Konzentration: McCaul 14.529 Duplikat-Zeilen, Khanna 2.886. Größter Cluster: McCaul, EOG, Sale, 2016-05-18, 44-mal identisch, `Subholding` leer. In den Duplikat-Zeilen ist `Subholding` nur zu 3,4 % gefüllt. Wahrscheinlichste Lesart: Mehrfach-Lots oder Treuhandkonten, deren Unterscheidungsmerkmal Quiver nicht exportiert. Endgültig klären lässt sich das nur über die PTR-PDFs. Der Mechanismus „Korrektur erzeugt neue ID“ (02, Nr. 6) bleibt für den Delta-Pfad theoretisch richtig, erklärt aber keine der gemessenen Duplikate. |
| K6 | Import steht still | **bestätigt** | Die letzte XLSX im Haupt-Worktree ist vom 2026-08-03 03:01. |
| H1 | ETF-Erkennung greift nie | **bestätigt** | `utils/ticker_normalizer.py:1082-1084` prüft `raw_security_type2 == "ETP"`. Im Master: 0 Zeilen `etf`, 3.500 Zeilen mit `security_type_figi == "ETP"`, davon 3.491 als `stock` (Haupt-Master). Ein lokaler Fix in einer Zeile. Er wirkt aber nur zusammen mit H2. |
| H2 | Asset-Gate setzt nur ein Flag | **bestätigt, leicht abgeschwächt** | `gates/asset_class_gate.py:45` setzt nur `asset_class_excluded`. Der einzige Leser ist `tags/vote_timing_tag.py:349`. Von 1.140 ausgeschlossenen Zeilen haben 988 Signale, 17 einen Score > 0. Die Formulierung „Signale feuern auf 1.139“ sollte „auf 987 von 1.139“ lauten. Lokal behebbar: Filter vor den Signal-Stages. |
| H3 | Share-Classes ohne FIGI | **bestätigt** | `ticker_normalizer.py:815-816` erzeugt `BRK.B`. Im Master haben 898 von 898 `shareclass`-Zeilen (Worktree: 897) keinen FIGI. Lokaler Fix in einer Zeile. |
| H4 | Delta ≠ Vollauf | **bestätigt** | `import_trades.py:2716-2718` schreibt PTR- und Exit-Spalten nur für die Delta-Zeilen zurück. `_merge_into_master` (`:1961-1976`) ist ein reines `concat` ohne Update alter Zeilen. Lokale Abhilfe: Delta abschaffen und immer voll rechnen. Genau das sieht auch die Zielarchitektur vor. Es geht aber ebenso im Bestand. |

**Nachrechnung der Zeilenzahlen (Abschnitt 1.3):** 49.244 Python-Zeilen ohne Tests, davon `committee_matcher/` 19.221 und Tests 28.911. Das passt zu den Berichtswerten. Die Abweichung kommt von den laufenden uncommitteten Änderungen.

---

## 2. Bewertung der Empfehlung

### 2.1 Struktureller oder lokaler Defekt

| Befund | lokal behebbar? | Aufwand im Bestand | Erzwingt den Kernumbau? |
|---|---|---|---|
| K1 | ja: Setting `period='max'`, Lückenprüfung (schon geschrieben), Neulauf | Stunden plus Laufzeit | nein |
| K2 | ja: State-Guard oder BioGuide-Mapping für den Roster | ca. 1 Tag | nein |
| K3 | nur mit neuer Datenquelle und neuem Roster-Format | hoch, unabhängig vom Weg | **Datenproblem, nicht Codeproblem** |
| K4 | teilweise: Namensabgleich `asset_name` gegen `asset_name_figi`, Umbenennungs-Mapping | mittel | nein, aber der Normalizer wird ohnehin umgebaut |
| K5 | ja: Report plus Aggregationsregel (Lots je Tag) | klein | nein |
| K6 | ja: Overlay plus Alarm, oder Abo | klein, Kostenfrage | nein |
| H1, H3 | ja: je eine Zeile, die Tests mit echten Antworten neu schreiben | klein | nein |
| H2 | ja: Filter vor den Signalen | klein | nein |
| H4 | ja: Delta-Pfad stilllegen, nur `run()` behalten | mittel; **verkleinert** die God-Class | nein |

**Fazit:** Von den zehn geprüften Befunden ist nur K3 strukturell. Auch K3 ist vor allem eine Frage der Datenbeschaffung. Das Argument im Gesamtbericht, „ein Fix im Bestand müsste fast jedes Modul anfassen und dieselbe God-Class dreimal ändern“, trifft so nicht zu. K1, K2, H1, H3 und K4 liegen in `utils/` bzw. `scoring/`, nicht in `import_trades.py`. Und H4 wird durch **Löschen** des Delta- und Einzel-Stage-Pfads behoben, nicht durch dreifaches Ändern.

Die Aussage „Die Ergebnisse sind aus strukturellen Gründen nicht belastbar“ (Abschnitt 0, Nr. 1) ist damit nicht gedeckt. Richtig wäre: Die Ergebnisse sind heute nicht belastbar. Die meisten Ursachen sind lokal. Strukturell sind das Datenmodell (Flat-CSV, keine Zeitachse) und die Wartbarkeit.

### 2.2 Ist die Gegenposition fair abgewogen?

Nur teilweise. Die Tabelle in 6.1 vergleicht „Reparatur“ mit einem idealisierten Rebuild:
- Die Reparatur wird mit dem vollen Altbestand bewertet („bleibt schwer: 48.800 Z.“).
- Der Rebuild wird mit seinem Zielumfang bewertet („8.000–10.000 Z.“).

Fair wäre eine **dritte Option**, die im Bericht fehlt: **„Sanieren durch Löschen“.**
1. Sofortmaßnahmen 6.5 umsetzen.
2. Den Ballast aus Abschnitt 5 entfernen: Committee-Matcher-Infrastruktur archivieren, tote Scraper, Delta-Pfad, Einzel-Stage-Modus.
3. Erst danach entscheiden, ob Datenmodell und Zeitachse einen Neuaufbau brauchen.

Damit schrumpft die Codebasis ebenfalls Richtung 15.000 bis 20.000 Zeilen. Bene bekommt außerdem nach Tagen statt Wochen belastbare Zahlen und kann den zentralen fachlichen Befund (Score ohne Edge, 04) erneut prüfen, bevor er in einen Neubau investiert.

Für einen Rebuild spricht weiterhin:
- Datenmodell (Normalisierung, `NULL` statt 0, versionierte Rohdaten) und Filter-Engine mit `not_evaluable` lassen sich in der Flat-CSV nur mühsam nachrüsten.
- Die Testsuite (28.900 Zeilen Mocks) bremst einen Umbau eher, als dass sie schützt.

### 2.3 Reihenfolge

Der Bericht stellt die Frage nach dem **Produktzweck** (6.4 Nr. 4, Phase 7) an das Ende. Teilbericht 04 zeigt aber: Auch auf sauberen Daten ab 2021-07 hat der Score keinen Vorteil gegenüber dem Markt (Spearman gegen AR −0,016). Ob ein Rebuild lohnt, hängt davon ab, was das Produkt leisten soll: Auffälligkeiten erklären oder Renditen vorhersagen. Diese Entscheidung gehört **vor** Phase 1 und nicht in Phase 7. Sonst wird ein sauberes System für ein Konstrukt gebaut, das danach gestrichen wird.

---

## 3. Fehlende Risiken und Leitplanken für den Rebuild

### 3.1 Risiken, die im Bericht fehlen

| Risiko | Warum relevant |
|---|---|
| Second-System-Effekt | Die Zielarchitektur bringt viel Neues auf einmal: zehn Tabellen, Versionierung, Regel-Engine mit Evidence-JSON, Golden-Set, Drift-Monitor, Fixtures aus echten API-Antworten, Health-Check, mehrere Kongresse. Genau dieser Ehrgeiz hat den Committee Matcher auf 18.800 Zeilen für ca. 80 benötigte Zeilen gebracht. |
| Erneuter Slop durch AI-Coding | Der Bestand ist das Ergebnis von Agent-Sessions ohne Größenbudget: drei Orchestrierungen, Mocks statt echter Antworten, 17.700 Zeilen Doku. Ohne neue Leitplanken wiederholt sich das. Der Bericht nennt die Ursache nicht. |
| Verlust impliziten Wissens | Viele Sonderfälle stecken nur im Code: Datums-Fallbacks, Namensformat-Bereinigung, 16 Aliase, Muni-Override, Delisting-Marker, Exit-Toleranzen, Schwellen in `settings.py`. Für einen Teil davon gibt es eine Übernahmeliste (4.1 bis 4.3), aber keinen Schritt, der das Wissen **vollständig** erfasst (z. B. ein Grep aller `if`-Sonderfälle im Altcode). |
| Parallelbetrieb | Solange der Rebuild läuft, braucht Bene das Dashboard. Ob der Altbestand eingefroren, minimal repariert oder abgeschaltet wird, ist nicht geregelt. |
| Abnahme gegen einen fehlerhaften Altbestand | Gates wie „Swarm-Treffer ≈ Altbestand“ und „AR-Stichprobe = Altbestand“ vergleichen mit einem Bestand, der selbst kontaminiert ist (Duplikate, ETFs, falsche States). Für jedes Gate muss feststehen, welche Abweichung **erwünscht** ist. |
| Daten-Blocker | K6 (Quelle), K3 (historische Mitgliedschaften) und die Kursquelle sind offen. Bleiben sie ungelöst, wird der Rebuild mit denselben Lücken fertig. |
| Quiver-Lizenz | Die kommerzielle Nutzung und der Export sind laut 12 ungeklärt. Das ist relevant, wenn das Deployment öffentlich wird. |

### 3.2 Leitplanken, die in den Bericht gehören

1. **Größenbudget je Schicht**, im Repo als CI-Check:
   - z. B. `ingest` ≤ 400 Zeilen, `reference` ≤ 600, `securities` ≤ 900, `metrics` ≤ 600, `flags`/`score` ≤ 900, `app` ≤ 2.500
   - Gesamtgrenze 8.000 Zeilen Produktivcode
   - Wird ein Budget überschritten, braucht es eine schriftliche Begründung im PR.
2. **Datei- und Funktionsgrenzen:**
   - Dateien ≤ 400 Zeilen, Funktionen ≤ 60 Zeilen
   - keine Klasse als Orchestrator
   - genau ein Pipeline-Einstiegspunkt
3. **Definition of Done je Phase:**
   - (a) Datenintegritäts-Gate auf dem Endprodukt grün
   - (b) mindestens ein Test mit aufgezeichneter echter API-Antwort statt Mock
   - (c) eine Doku-Seite pro Modul mit höchstens 150 Zeilen
   - (d) eine Zeile im CHANGELOG
   - (e) kein toter Code; `vulture` oder `ruff` ohne Befund
   - (f) Review durch einen Subagent mit dem Auftrag „Was kann weg?“
4. **Keine Infrastruktur ohne Konsumenten:**
   - kein Framework, keine CLI-Subbefehle und keine Settings, die nicht ein bestehendes Feature braucht
   - kein Feature ohne Eintrag im Feature-Katalog (Abschnitt 2)
5. **Settings-Disziplin:**
   - typisierte Konfiguration (pydantic) mit Pflichtfeldern, ohne `getattr`-Defaults
   - höchstens ca. 60 Parameter
   - ein Parameter ohne Leser lässt den Start fehlschlagen
6. **Mock-Verbot für externe Formate:** OpenFIGI-, yfinance-, Quiver- und Congress.gov-Antworten nur als aufgezeichnete Fixtures. Die H1-Ursache war ein erfundener Mock.
7. **Agent-Arbeitsweise:**
   - eine Phase pro Branch
   - der Plan nennt das Zeilenbudget und die Löschliste
   - Agents dürfen keine neuen Module anlegen, ohne die Modulliste in `AGENTS.md` zu ändern
8. **Wissensinventur vor dem Löschen:** Aus dem Altcode die Sonderfälle als Tabelle extrahieren (Sonderfall, Fundort, übernehmen ja/nein). Erst danach darf der Altcode ins Archiv.
9. **Freeze des Altbestands:** Nur noch die Sofortmaßnahmen aus 6.5 sind erlaubt, keine neuen Features.
10. **Abbruchkriterium:** Ist nach Phase 3 das Budget um mehr als 50 % überschritten oder sind die Gates nicht grün, wird neu entschieden, ob doch saniert wird.

---

## 4. Fehlerhafte Verweise

Geprüft wurden gut 50 Zeilenangaben aus Abschnitt 2, nicht nur 10. Fast alle zeigen auf die genannte Funktion oder Konstante. Abweichungen:

| Verweis in 00 | Befund |
|---|---|
| `utils/trade_id.py:33` (Hashfelder, auch K5) | Zeile 33 ist ein Kommentar. `TRADE_ID_SOURCE_COLUMNS` beginnt bei `:32`. Unkritisch. |
| `utils/committee_parser.py:35` | zeigt in den Docstring, die Funktion beginnt eine Zeile davor. Unkritisch. |
| `tags/committee_tag.py:105-240` | `calculate_relevance` liegt bei `:108-231`, dazu kommt `_match_pass` ab `:232`. Weitgehend korrekt. |
| `config/settings.py:196` (Primärsignale) | Kommentar. `SCORING_PRIMARY_SIGNALS` steht bei `:199`. |
| `config/settings.py:457-486` (Selektoren) | Der Block beginnt bei `:457` mit einem Kommentar und reicht bis `:486`. Korrekt. Der Dienste-Tabelle `:455-489` fehlt dagegen nichts. |
| `utils/issuer_group_mapper.py:90` | Trennkommentar vor dem Block. Unkritisch. |
| `tags/vote_timing_tag.py:298-403` | Docstring von `apply` (`:293`). Grob korrekt. |
| `config/committee_mapping.py:62` | Docstring einer Klasse, keine Definitionszeile. Unkritisch. |
| `utils/ticker_normalizer.py:803` (H3, 6.5 Nr. 2) | `:803` ist `_normalize_for_openfigi`. Die fehlerhafte Zeile ist **`:816`** (`return f"{parts[0]}.{parts[1]}"`). Für die „Ein-Zeilen-Fix“-Anweisung sollte `:816` stehen. |
| `utils/ticker_normalizer.py:1082` (6.5 Nr. 1) | korrekt; die Bedingung steht in `:1082` und `:1084`. |

Korrekt geprüft wurden unter anderem: `quiver_scraper.py:44/128/225`, `trade_id.py:68/83`, `amount_parser.py:35`, `import_trades.py:478/1993/2549/2901/612-613/2716-2718`, `name_matcher.py:270/335`, `parse_house_committee_pdf.py:587/718/765`, `ticker_classifier.py:89/140/225/260/270`, `abnormal_returns.py:51/139/267/292`, `post_trade_returns.py:172/623/640/1051`, `trade_score.py:129`, `committee_revision.py:137/310`, `settings.py:29/171/451`, `streamlit_app.py:190/581/1331/1877/2148`, `staging_export.py:114`, `member_detail.py:197`, `fetch_congress_data.py:53`, `fetch_senate_xml.py:43`, `parse_senate_xml.py:25`.

---

## 5. Ungeklärte Widersprüche

1. **Datenbasis:**
   - Der Kopf von 00 nennt den „Master vom 2026-08-11 aus dem Haupt-Worktree“. Die Teilberichte 01, 04 und 11 rechnen aber auf dem Worktree-Master vom 31.07. (114.245 Zeilen, 643 MB). Nur 02 nutzt den Master vom 11.08. (114.347, 645 MB).
   - K5 (12.329, 11.149) und die 643 MB in Abschnitt 1.1 stammen vom 31.07., die Zeilenzahl 114.347 in Abschnitt 1.1 vom 11.08.
2. **Cache-Stand:** „veraltet bis 2026-07-15“ (2.6, aus 04) gilt nur für den Worktree-Cache. Der Cache im Haupt-Worktree reicht bis 2026-08-10. Dort haben außerdem 1.511 Ticker ihren ersten Kurs erst im August 2026. Das ist ungeklärt; vermutlich Fetches mit kurzem Fenster oder nur Delisting-Marker.
3. **K1-Prozentangaben:**
   - 00 und 04 sprechen von „53 % aller Trades“, korrekt sind 53 % der Trades mit Rendite bzw. 43 % aller Trades.
   - 04 nennt außerdem „ca. 56 % des Datensatzes wertlos“, 01 nennt 47.591 Null-Zeilen, 11 nennt 44,2 % (30d) bzw. 50.459 Zeilen mit Preis == Baseline. Diese Zahlen sind nie auf eine Definition gebracht worden.
4. **K1-Ursache:** 04 vermutet einen Cache-Reset (Commit `68b09e6`). 00 schreibt „trotz FETCH_YEARS=5“. Tatsächlich ist das 5-Jahres-Fenster selbst die Ursache. Dass die Lückenprüfung im Worktree schon uncommittet existiert, erwähnt 00 nicht.
5. **K5-Deutung:**
   - 02 nennt 22.781 Zeilen, die sich eine trade_id teilen (beteiligte Zeilen).
   - 11 nennt 12.329 überzählige Zeilen und vermutet „Re-Uploads, Amendments, Pipeline dedupliziert nicht“.
   - 00 übernimmt die Korrektur-These aus 02.
   - Keiner der Berichte hat die Quell-XLSX geprüft. Sie enthält die Duplikate bereits (Abschnitt 1, K5).
6. **01, Abschnitt 2.3:** „9.126 von 119.415 Zeilen mit FIGI“. 119.415 ist größer als die Zeilenzahl des Masters (114.245). Die Basis der 7,6 % aus K4 ist damit unklar.
7. **H1-Zahlen:** 00 nennt „3.938 Fonds-Zeilen (SPY, QQQ …)“. In 01 sind 3.957 Fondszeilen, davon 3.938 als `stock`, darunter ETP 3.482. Die Gegenprüfung findet 3.482 bzw. 3.491 ETP-Zeilen als `stock`. Das ist konsistent, aber in 00 verkürzt.
8. **Committee-Matcher-Bedarf:** Abschnitt 0 nennt „etwa 650 Zeilen zur Laufzeit gebraucht“, 2.7 nennt „Loader etwa 80 Zeilen“. Gemeint ist offenbar: Heute laufen 650 Zeilen, nötig wären 80. Das sollte so dastehen.
9. **Teilbericht 09** sagt, das Frontend sei kein Grund für einen Rebuild, und eine UI-Neuschreibung allein löse nichts. 00 setzt dennoch alle Tabs auf „portieren“ statt „übernehmen“. Wer das Frontend neu schreibt, bezahlt Aufwand ohne den Nutzen, den 09 sieht.
10. **Teilbericht 10** hält Aufräumen im Bestand für machbar, 06 nennt den Rebuild des eigenen Bereichs „leicht“. Beides stützt eher die Sanierungs-Option aus 2.2, wird in 6.1 aber nicht gewürdigt.
11. **Teilbericht 12** nennt die eigenen PTR-Scraper „Primärquelle“, 00 erklärt das nur als Formulierung (Abschnitt 7). Offen bleibt die Konsequenz: Fällt Quiver weg, wird der als „verwerfen“ bzw. „nur bei Ausfall portieren“ markierte Scraper zum kritischen Pfad. Dafür fehlen Aufwand und Phase im Phasenplan.

---

## 6. Korrekturvorschläge für 00_GESAMTBERICHT.md

1. **Kopf, Datenbasis:** Beide Master-Stände (31.07. Worktree und 11.08. Haupt-Worktree) samt Zuordnung zu den Teilberichten nennen, wie in der Tabelle oben.
2. **Abschnitt 0, Nr. 1:**
   - „etwa die Hälfte der Trades“ ersetzen durch „43 % aller Trades bzw. 53 % der Trades mit Rendite; 2012–2020 sind alle vorhandenen Renditen 0“.
   - „aus strukturellen Gründen“ ersetzen durch „überwiegend aus lokal behebbaren Gründen; strukturell sind Datenmodell und fehlende Zeitachse“.
3. **Abschnitt 0, Nr. 1 und K2:** „falscher Abgeordneter“ ersetzen durch „falsche Stammdaten und Ausschüsse bei korrekter Person (Name und BioGuideID stimmen)“. Den Fall „Mr. Lamar Smith“ → WA ergänzen.
4. **K1:**
   - „trotz `POST_TRADE_PRICE_FETCH_YEARS=5`“ ersetzen durch „wegen `period='5y'` (`settings.py:561`, `post_trade_returns.py:171`)“.
   - Ergänzen, dass die Lückenprüfung im Worktree uncommittet vorliegt (`_max_gap`).
5. **K5:** Die Ursache ersetzen durch „11.152 Zeilen sind bereits in der Quiver-XLSX vollständig identisch; Master = XLSX = 114.347 Zeilen; die Pipeline erzeugt im Vollauf keine Dubletten; vermutlich Mehrfach-Lots ohne exportiertes Unterscheidungsmerkmal (Subholding nur zu 3,4 % gefüllt)“. In Abschnitt 7 den Punkt „ungeklärt“ entsprechend präzisieren.
6. **H2:** „Signale feuern auf 1.139“ ersetzen durch „987 von 1.139 ausgeschlossenen Zeilen tragen Signale“.
7. **H3 und 6.5 Nr. 2:** Den Verweis auf `utils/ticker_normalizer.py:816` korrigieren.
8. **2.6 Preis-Cache:** „veraltet bis 2026-07-15 (Worktree) bzw. 2026-08-10 (Haupt-Worktree)“.
9. **Abschnitt 0, Absatz „Was gegen einen Rebuild spricht“:** Den Satz „dieselbe 3.277-Zeilen-God-Class dreimal ändern“ streichen oder belegen. Die meisten Fixes liegen in `utils/` und `scoring/`, und H4 wird durch Löschen des Delta-Pfads behoben.
10. **6.1:** Eine dritte Spalte **„Sanieren durch Löschen“** aufnehmen (Sofortmaßnahmen, Ballast entfernen, Delta stilllegen, dann neu entscheiden). Bei K1, K2, H1 bis H4 ehrlich „lokal, klein“ eintragen.
11. **6.1, Ergebnis:** „Drei der sechs kritischen Befunde (K2, K3, K4) und H4 erfordern im Bestand Kernumbauten“ korrigieren. K2 und H4 sind lokal. K3 ist primär ein Datenbeschaffungsproblem und blockiert beide Wege gleichermaßen.
12. **6.3:** Die Entscheidungen 6.4 Nr. 1 (Datenquelle), Nr. 2 (historische Mitgliedschaften) und Nr. 4 (Zweck des Scores) als **Gate vor Phase 1** festschreiben. Phase 7 an den Anfang ziehen, mindestens als Vorstudie auf den sanierten Altdaten.
13. **6.3, Gates:** Bei jedem Vergleich mit dem Altbestand die erwartete Abweichung angeben (z. B. Swarm-Treffer sinken durch die Duplikat-Aggregation; Committee-Treffer ändern sich bei 6.859 Zeilen).
14. **Neuer Abschnitt „Leitplanken gegen erneuten Slop“:** Inhalt wie oben in 3.2 (Größenbudget mit CI-Check, Datei- und Funktionsgrenzen, Definition of Done je Phase, Mock-Verbot für externe Formate, Settings-Disziplin, Wissensinventur, Freeze des Altbestands, Abbruchkriterium).
15. **Neuer Abschnitt „Risiken des Rebuilds“:** Second-System-Effekt, AI-Slop, Wissensverlust, Parallelbetrieb, Abnahme gegen fehlerhaften Altbestand, Daten-Blocker, Quiver-Lizenz.
16. **Abschnitt 1.1:** Den Hinweis „Master (Stand 31.07.) nutzt Legacy-Mapping“ mit der Kopfangabe 11.08. abgleichen.
17. **K4:** Den Nenner „119.415 Zeilen“ aus 01 prüfen und korrigieren oder die 7,6 % als Heuristik mit unklarer Basis kennzeichnen.
18. **Abschnitt 0 und 2.7:** „650 Zeilen zur Laufzeit gebraucht“ und „Loader etwa 80 Zeilen“ sprachlich auseinanderhalten (heute genutzt vs. künftig nötig).
19. **Abschnitt 2.8:** Für Tabs ohne Fachlogik „übernehmen“ statt „portieren“ erwägen (siehe 09). Das senkt den Rebuild-Umfang.
