# 12 - Externe Alternativen zum Eigenbau (Stand Oktober 2026)

Zeitbudget ca. 15 Minuten. Die Recherche stützt sich auf Web-Fetches der offiziellen Seiten und Websuche. Wo Quellen fehlen, widersprüchlich sind oder nicht abrufbar waren, ist das in der Spalte "Aktualität/Status" vermerkt. Alle Preise und Limits ohne Gewähr, vor einer Entscheidung auf der jeweiligen Seite gegenprüfen.

Legende Spalte "Einschätzung": **ja** = ersetzt Eigenbau vollständig, **teilweise** = ersetzt Teilbereich, **nein** = kein Ersatz.

## 1. Abgeordnete und Ausschussmitgliedschaften

| Quelle | Was sie liefert | Kosten | Aktualität/Status | Quelle-URL | Einschätzung |
|---|---|---|---|---|---|
| unitedstates/congress-legislators: `legislators-current` | Aktuell amtierende Mitglieder (YAML/JSON/CSV) mit bioguide-ID u.a. | Kostenlos, CC0-1.0 | Gepflegt; Update-Zeitpunkt "periodisch", kein fester Zeitplan. Current/Historical-Split kann bei Ausscheiden verzögert sein | https://github.com/unitedstates/congress-legislators | ja (Identitäten) |
| unitedstates/congress-legislators: `committees-current` | Aktuelle House-, Senate- und Joint-Committees **inkl. Subcommittees** (YAML/JSON) | Kostenlos, CC0-1.0 | Wie oben; Subcommittee-IDs = Parent-ID + Sub-ID | https://github.com/unitedstates/congress-legislators | ja (Struktur) |
| unitedstates/congress-legislators: `committee-membership-current` | Zuordnung Committee/Subcommittee -> Mitglieder, mit `bioguide`, `party` (majority/minority), `rank` (1 = Chair bzw. Ranking Member), `title` (nicht normalisiert). Formate YAML/JSON/CSV | Kostenlos, CC0-1.0 | Aktuell, gleiche Pflegeart. Keine Amtszeit-Historie in der Current-Datei | https://github.com/unitedstates/congress-legislators | ja für aktuellen Stand, **teilweise** für Historie |
| unitedstates/congress-legislators: `committees-historical` / `legislators-historical` | Historie seit 1973 (Committees) bzw. seit 1789 (Legislators). Historische Committee-Liste ist laut Doku "possibly partial" (nur Ausschüsse mit überwiesenen Bills) | Kostenlos, CC0-1.0 | Historische Mitgliedschaftsdaten nur eingeschränkt; Membership-Historie nicht als eigene Datei verifiziert | https://github.com/unitedstates/congress-legislators | teilweise |
| Congress.gov API (Member/Committee-Endpunkte) | Offizielle Mitglieder-/Ausschussdaten, Bills | Kostenloser API-Key, Limit 5.000 Requests/Stunde | Repo aktiv (v3). Ein Web-Artikel berichtet von einem Ausfall (Redirect-Loop bei api.congress.gov/v3, Key-Erzeugung defekt); Datum unklar, **nicht verifiziert** | https://api.congress.gov/ , https://github.com/LibraryOfCongress/api.congress.gov | teilweise (Member/Bills ja, Ausschuss-Feinheiten ungeprüft) |
| ProPublica Congress API | Mitglieder, Ausschüsse, Votes (früher) | - | **Abgeschaltet**, keine neuen Keys. Nur historische Referenz | https://projects.propublica.org/api-docs/congress-api/ | nein |
| GovTrack Bulk Data/API | Mitglieder, Bills | - | **Eingestellt** (laut congressionaldata.org) | https://congressionaldata.org/ending-govtracks-bulk-data-and-api/ | nein |

Fazit Bereich 1: `committee-membership-current` plus `committees-current` ist die plausibelste Ersatzquelle für den aktuellen Stand inkl. Subcommittees und CC0-Lizenz. Historische Mitgliedschaften (Zeitfenster pro Trade) bleiben voraussichtlich Eigenbau oder Auswertung über Git-Historie des Repos (nicht geprüft).

## 2. Kongress-Trading-Daten

| Quelle | Was sie liefert | Kosten | Aktualität/Status | Quelle-URL | Einschätzung |
|---|---|---|---|---|---|
| Quiver Quantitative (Congress-Trading-API) | Trades Abgeordnete, Endpunkt `api.quiverquant.com/beta/historical/congresstrading/{TICKER}`, Bearer-Token | Preise **widersprüchlich**: Altes PyPI-Paket nennt ab 10 $/Monat; Konkurrenz-Seite (Disclosed Capitol) nennt Hobbyist ab ca. 30 $/Monat (Stand Juli 2026) und schließt kommerzielle Nutzung in Hobbyist/Trader aus. Offizielle Preisseite war nicht abrufbar | Quiver-Seite aktuell erreichbar, Preisdetails nicht verifiziert | https://pypi.org/project/quiverquant/ , https://www.disclosedcapitol.com/vs/quiverquant , https://www.quiverquant.com/api/ | teilweise (Kosten/Lizenz vor Nutzung klären) |
| Capitol Trades | Trades, Mitglieder, Emittenten, Filing-Datum, Betragsklassen. Laut Web-Quelle als Alternative genannt | Nicht verifiziert | Seite lieferte HTTP 429 (Rate Limit beim Abruf). Ob es eine offizielle API/Export gibt: **nicht verifiziert** | https://www.capitoltrades.com | unklar |
| Senate Stock Watcher / House Stock Watcher | Früher kostenlose Rohdaten (JSON) | kostenlos | Laut einer Skill-Seite **dekommissioniert** (kein DNS, S3-Mirror 403). Nur eine Quelle, **nicht unabhängig bestätigt**; Live-Check nicht erfolgt | https://housestockwatcher.com , https://senatestockwatcher.com | nein (als Quelle nicht verlässlich) |
| Financial Modeling Prep (Senate/House Trading) | Endpunkt `stable/senate-trades?symbol=...` (Trade-Level, STOCK-Act-Daten) | Free-Plan mit Tageslimit (laut FAQ 250 Requests/Tag, allgemein). Ob Congress-Endpunkte im Free-Plan enthalten sind: **nicht verifiziert** | Dokumentation vorhanden; Preisseite nicht abrufbar | https://site.financialmodelingprep.com/developer/docs/senate-trading-api | teilweise (Test mit kostenlosem Key nötig) |
| Finnhub (Congressional Trading) | Kongress-Trades laut Anbieterseite (Feldumfang nicht verifiziert) | Free-Tier vorhanden; Congress-Endpunkt-Verfügbarkeit im Free-Tier **nicht verifiziert** | Docs-Seite nicht auslesbar (nur Titel/Beschreibung) | https://finnhub.io/docs/api/congressional-trading | unklar |
| Unusual Whales | Kongress-Trades (Web/API) | Kostenpflichtig, Preis nicht verifiziert | Nicht geprüft | - | unklar |
| House Clerk Financial Disclosure / Senate eFD | Offizielle PTR-Filings (PDF/Web), Primärquelle | Kostenlos | Eigenbau-Scraper bleibt nötig, da keine strukturierte API; Primärquelle für Verifikation | https://disclosures-clerk.house.gov , https://efdsearch.senate.gov | nein (Primärquelle, kein Ersatz) |

Fazit Bereich 2: Kein verifiziert kostenloser, vollständiger und stabiler Ersatz gefunden. Der kostenpflichtige Quiver-Pfad ist die naheliegendste Option, aber Lizenz (kommerziell oder nicht) und Preis müssen vor Kauf auf quiverquant.com geprüft werden. Offizielle Filings bleiben Primärquelle.

## 3. Ticker-/Wertpapier-Identifikation

| Quelle | Was sie liefert | Kosten | Aktualität/Status | Quelle-URL | Einschätzung |
|---|---|---|---|---|---|
| OpenFIGI | FIGI-Mapping (Ticker/ISIN/CUSIP -> FIGI, Exchange) | Offizielle Seite: API "free to use without daily, weekly or monthly limitations"; mit Key deutlich höhere Job-Limits | Limits laut Drittquellen **widersprüchlich** (Ruby-Gem: 25 Req/min ohne Key, 25 Req/6s mit Key; VS-Code-Extension: 5 Req/min ohne, 250 mit Key). Offizielle Zahlen nicht auf der Seite. Key-Anforderung (institutionelle Mail?) nicht verifiziert | https://www.openfigi.com/api , https://github.com/phacks/vscode-isin-code | ja (bleibt bewährt; Limits vorab testen) |
| SEC `company_tickers_exchange.json` | JSON mit `fields` [cik, name, ticker, exchange] und `data`-Array. Exchange enthalten (u.a. Nasdaq, NYSE, OTC, CBOE, TXSE) | Kostenlos | Vorhanden und strukturiert; Vollständigkeit/Genauigkeit laut SEC nicht garantiert. Umfang ca. 500 KB+ (nur Teil gelesen) | https://www.sec.gov/files/company_tickers_exchange.json | ja (für US-Ticker -> CIK -> Name) |
| SEC `company_tickers.json` | Ticker, CIK, Name (ohne Exchange) | Kostenlos | Verfügbar; SEC: Accuracy/Scope nicht garantiert | https://www.sec.gov/files/company_tickers.json | ja (Alternative) |
| Ticker-Historie (Umbenennungen, Delistings) | Nicht von den oben genannten Quellen gelöst | - | Hier kein Ersatz gefunden, bleibt Eigenbau (ggf. OpenFIGI) | - | nein |

## 4. Sektor-/Branchenklassifikation

| Quelle | Was sie liefert | Kosten | Aktualität/Status | Quelle-URL | Einschätzung |
|---|---|---|---|---|---|
| yfinance (`sector`, `industry`) | Yahoo-Sektor/Industrie-Labels pro Ticker | Kostenlos (inoffizielle API) | Lib-Version 1.7.0 (26.08.2026). Yahoo-Nutzung laut PyPI "personal use only", Lib "for research and educational purposes", nicht von Yahoo geprüft | https://pypi.org/project/yfinance/ | teilweise (Lizenzrisiko, keine Garantie der Verfügbarkeit) |
| SEC SIC-Code (submissions JSON, `data.sec.gov/submissions/CIK##########.json`) | SIC-Code und Beschreibung pro Unternehmen | Kostenlos | Laut meiner Kenntnis enthalten; auf der SEC-Seite nicht explizit verifiziert (SIC war nicht Teil der abgerufenen Seite) | https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data | teilweise (grob, Branchen-Mapping nötig) |
| GICS (MSCI/S&P) | Standard-Branchenklassifikation | Kostenpflichtig/Lizenz | Nicht frei verfügbar (aus Kenntnis, nicht geprüft) | - | nein (ohne Lizenz) |

## 5. Kursdaten

| Quelle | Was sie liefert | Kosten | Aktualität/Status | Quelle-URL | Einschätzung |
|---|---|---|---|---|---|
| yfinance | Historische EOD-Kurse, Adj. Close | Kostenlos | Stabil nutzbar laut Lib-Version 2026; Yahoo-Nutzung "personal use only", kein Vertrag, Ausfälle/Rate-Limits möglich (nicht im Detail geprüft) | https://pypi.org/project/yfinance/ | teilweise (Prototyp ok, Produktion Risiko) |
| Tiingo (Starter) | EOD-Historie 30+ Jahre, 50 Req/h, 1.000 Req/Tag, 500 Symbole/Monat, 1 GB/Monat | Free: 0 $; Power 30 $/Monat; kommerzielle interne Nutzung 50 $/Monat | Lizenz Starter: "Internal Use Only". Redistribution gesondert | https://www.tiingo.com/about/pricing | ja (als bezahlbarer, lizenzsauberer Ersatz, 500-Symbol-Limit beachten) |
| Alpha Vantage (Free) | Kurse, Company Overview | Free: 25 Requests/Tag; Premium ab 49,99 $/Monat (75 Req/min) | Free-Tier für Vollbestand zu klein; Verfügbarkeit Overview im Free-Tier **nicht verifiziert** | https://www.alphavantage.co/premium/ | teilweise (nur Zusatzquelle) |
| Stooq | Kurse CSV, kostenlos | kostenlos | **Nicht geprüft** (Abruf nicht erfolgt) | https://stooq.com | unklar |

## 6. Abstimmungen und Bills

| Quelle | Was sie liefert | Kosten | Aktualität/Status | Quelle-URL | Einschätzung |
|---|---|---|---|---|---|
| Congress.gov API | Bills, Actions, Member, Committees (Votes: laut Repo nicht bestätigt) | Kostenloser Key, 5.000 Req/h | Aktiv (v3, 831 Commits laut Repo). Ausfallbericht (Web-Artikel, Datum unklar) **nicht verifiziert** | https://api.congress.gov/ , https://github.com/LibraryOfCongress/api.congress.gov | ja für Bills/Actions; Votes unklar |
| unitedstates/congress (Scraper) | Roll-Call-Votes (House/Senate), Bill-Status aus Bulk-Daten | Kostenlos (Repo, Lizenz nicht geprüft) | Repo aktiv (783 Commits laut Seite); Aktualität der Vote-Daten **nicht verifiziert** | https://github.com/unitedstates/congress | teilweise (Roll-Call-Votes möglich, eigene Prüfung nötig) |
| GovInfo Bulk Data | Bill-Texte, Dokumente | Kostenlos | Laut unitedstates-Repo genutzt; Details nicht geprüft | https://www.govinfo.gov | teilweise |
| ProPublica Congress API | Votes, Members | - | Abgeschaltet, keine neuen Keys | https://projects.propublica.org/api-docs/congress-api/ | nein |
| GovTrack | Bills, Votes | - | Bulk-Daten und API eingestellt | https://congressionaldata.org/ending-govtracks-bulk-data-and-api/ | nein |

Fazit Bereich 6: Congress.gov API für Bills/Actions ist der offizielle Ersatz. Roll-Call-Votes sind über das unitedstates/congress-Repo möglich, ob sie aktuell gepflegt werden, war nicht prüfbar.

## Gesamteinschätzung

- **Ersetzbar, lizenzsauber und kostenlos:** Ausschussstruktur/Mitgliedschaften (unitedstates/congress-legislators, CC0), SEC-Tickerliste, Congress.gov für Bills.
- **Teilweise ersetzbar:** Kursdaten (Tiingo bezahlbar als Option, yfinance nur mit Lizenzrisiko), Branchen (SIC grob), Roll-Call-Votes (unitedstates/congress, Aktualität prüfen).
- **Nicht verlässlich ersetzbar:** Kongress-Trading-Daten. Kostenlose Quellen (Stock Watcher) sind laut Hinweisen weg; kommerzielle Anbieter (Quiver, FMP, Finnhub) brauchen einen Test mit kostenlosem Key bzw. eine Lizenzprüfung. Die offiziellen PTR-Scraper bleiben Primärquelle.
- **Entfallen:** ProPublica Congress API und GovTrack API sind nicht mehr nutzbar.

## Offene Punkte (nicht verifiziert)

1. Quiver-Preise und kommerzielle Lizenz auf quiverquant.com/pricing direkt prüfen (Preisseite war nicht abrufbar).
2. FMP-Free-Plan: prüfen, ob `senate-trades` ohne Bezahlung funktioniert (Test mit kostenlosem Key).
3. Capitol Trades: offizielle Nutzungsbedingungen und Export/API-Existenz, Abruf hatte HTTP 429.
4. Senate/House Stock Watcher: Dekommissionierung durch Live-Check bestätigen.
5. Congress.gov-API-Ausfall: aktueller Status.
6. unitedstates/congress: letzte Commit-Aktivität und Vote-Aktualität im Repo prüfen.
7. SIC-Code im SEC-Submissions-JSON: Feldvorhandensein am Beispiel-CIK testen.
8. OpenFIGI: Limits mit eigenem Test ermitteln, Key-Berechtigung (institutionelle Mail?) klären.
