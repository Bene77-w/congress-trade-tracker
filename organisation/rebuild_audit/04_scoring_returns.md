# Audit 04: Scoring und Renditen

Stand: 2026-10-17, Branch `agent/committe_matcher`. Nachrechnungen: Hilfsskripte in `/tmp/audit_scoring/`. Keine yfinance-Aufrufe, keine Repo-Änderungen. Master nur per `usecols` gelesen (114.245 Trades, Datei vom 31.07.2026, Preis-Cache bis 2026-07-15).

## 1. Features

| Feature | Fundstelle | Kern |
|---|---|---|
| Trade-Score | `scoring/trade_score.py:129` (`compute_trade_score`), `:267` (Wrapper) | Hard-Gate: ohne `committee_full` oder `swarm_buy` ist der Score 0. Sonst Summe aus Gewichten getriggerter Signale + Schwarm-Dämpfer (`paradox`) + optional Volumen-Tier + max. Ex-Officio-Bonus. |
| Gewichte | `config/settings.py:171` (`SCORING_WEIGHTS`), `:196` Primärsignale | Default: `committee_full=5`, `swarm_buy=20`, alles andere 0. Ex-Officio-Boni alle 0. `trade_volume` aus. |
| Neuberechnung im UI | `app/score_recompute.py:62` (Settings-Proxy, Overrides) | Liest Defaults live aus `config.settings`, überlagert UI-Werte. Keine zweite Gewichtskopie. |
| Presets | `app/scoring_presets.py`, `data/config/scoring_presets.json` | Benannte Konstellationen. `Default` wird synthetisch aus Settings erzeugt (`:96`). Gespeichert ist nur Preset `Bene2` (`committee_full=35`, `floor_action=15`, `swarm_sell=20`, `paradox_damper=0.5`, Ex-Officio-Boni 8 bis 15). |
| Post-Trade-Renditen | `scoring/post_trade_returns.py:640` (`compute_post_trade_returns`) | Baseline = erster Close ab `transaction_date` (`:512`, max. 5 Tage Lücke). `post_return_Xd = (P(t0+X)/P(t0)-1)*100*sell_sign`, X = 30/60/90/120/180. |
| Verkaufs-Vorzeichen | `post_trade_returns.py:623` (`_sell_sign_series`) | Sale = -1, sonst +1. Gilt für alle Return-Spalten. |
| Exit-Detection / realisierte Rendite | `:1051`, `:~800` | Gegentrade je Issuer ergibt `closed_full/partial`. `effective_return_pct` = Exit-Preis/Baseline. |
| Mark-to-Market | `:926` (`compute_paper_returns`) | Letzter Close gegen live aus dem Cache abgeleitete Baseline, nur Trades der letzten 180 Tage. |
| Preis-Cache | `:65`, `:192`, `:455`, `data/cache/price_history.parquet` | yfinance `auto_adjust=True` (`:172`), TTL-Refresh, Delisting-Marker (close=NaN, 1900-01-01). 4,03 Mio. Zeilen, 4.864 Ticker. |
| Abnormal Returns | `scoring/abnormal_returns.py:139` | `signed_ar = sell_sign*(R_ticker - R_SPY)`, Anker Transaktions- (A) und Meldedatum (B), Cluster-Bootstrap (`:267`, `:292`). Benchmark `AR_BENCHMARK_TICKER='SPY'` (`settings.py:640`). |
| Volumen-Tiers | `scoring/asset_volume.py`, `trade_score.py` | Stufen auf `trade_amount_max_usd`, per Default aus. |
| Score-Korrelation | `app/score_correlation.py` | Spearman Score vs. Return mit Bootstrap, UI-Tab. |
| Event-Study | `scripts/analyse_event_study.py` | Eigenständiges Skript: Diskriminator Score gegen AR, Signal-Effekte, Ausreißer. Keine Pipeline-Stage. |

Score-Formel (Default): `score = 5*[committee_full] + 20*[swarm_buy]` (Werte 0, 5, 20, 25). Alle übrigen Terme sind Hints mit Gewicht 0. Verteilung im Master: 86.234 mal 0, 16.493 mal 5, 8.945 mal 20, 2.573 mal 25.

## 2. Funktionsprüfung

**Belegt, korrekt**
- AR-Formel: 1.489 Trades ab 2022 aus Cache neu gerechnet (Ticker minus SPY, Verkauf gespiegelt): mittlere Abweichung zur Spalte `signed_ar_90d` 1e-6, Maximum 3e-4. Vorzeichen und Benchmark stimmen. Sell-Sign wird nur einmal auf das Abnormal angewendet, nicht doppelt.
- Buy/Sell-Vorzeichen: Kaufmittel +1,47, Verkaufsmittel -1,51 bei `post_return_90d`. Das entspricht der Konvention (positiv = gutes Timing).
- `post_return_30d` für 1.310 Stichprobentrades aus dem Cache nachgerechnet: 0 Abweichungen über 0,5 Prozentpunkte (mit aktuellem Cache).
- Wochenenden/Feiertage: "erster Close ab Zieldatum" mit 5-Tage-Lücke ist konsistent und im Code getestet. Zukunftsziele werden zu NaN (Zensierung).
- Gewichtsdrift: nein. `score_recompute` liest `config.settings` live. Die Presets sind bewusst abweichende Nutzerwerte, keine Kopie. Doku (`Filter_Instanz_Parameter.md:252`) und Settings stimmen überein.
- Tests: 140 gezielte Tests (trade_score, post_trade, abnormal, presets, recompute, asset_volume) bestehen.

**Belegt, kritisch**
1. **Gespeicherte Master-Renditen vor Mai 2021 sind Artefakte.** Der Preis-Cache beginnt für 3.108 von 3.339 Tickern am 2021-05-28 (trotz `POST_TRADE_PRICE_FETCH_YEARS=5`). Trades 2013 bis 2021-05 (ca. 64.400) haben dennoch Baseline und Horizontpreise: sie sind alle identisch gleich dem ersten Cache-Close (Beispiel WFC 2020-01-08: Baseline = P30 = P90 = 41,4198 = Cache-Zeile vom 2021-05-28). Folge: 76 % dieser Trades haben Return und AR exakt 0, über alle Trades 53 %. Median und Quartile von `signed_ar_90d` sind daher 0. Die aktuelle Funktion würde hier NaN liefern (Lückenprüfung), der Master stammt also aus einem Lauf ohne wirksame Lückenprüfung oder einem Delta-Merge. Die Spalten sind für ca. 56 % des Datensatzes wertlos, und das Korrelationsergebnis ist dadurch massiv verdünnt.
2. **Gemischte Adjustierungsbasis.** `auto_adjust=True` rechnet Splits und Dividenden ein; jeder Refresh ändert die gesamte Historie. Gespeicherte Baselines weichen vom aktuellen Cache bei Dividendenzahlern um 1 bis 2 % ab (VZ, ACN, GIS, T: z. B. VZ 42,06 gespeichert gegen 41,36 im Cache). In der Stichprobe 0,8 % der Trades über 1 %, keiner über 10 %. Also klein, aber systematisch; die Doku adressiert es nur für `paper_return_pct`. Der AR rechnet beide Beine aus demselben Cache, ist also konsistent, aber nur wo der Cache reicht.
3. **Konstrukt-Test (Trades ab 2021-07, n=40.929, saubere Daten, nur 0,07 % Nullen):**

| Score | n | mittlerer Roh-Return 90d | mittlerer signed AR 90d |
|---|---|---|---|
| 0 | 27.583 | -0,37 % | +0,0020 |
| 5 (committee_full) | 8.868 | -1,53 % | -0,0078 |
| 20 (swarm_buy) | 3.198 | +4,42 % | -0,0006 |
| 25 | 1.280 | +6,84 % | +0,0137 (SE 0,005) |

   Spearman Score vs. Roh-Return +0,050, vs. AR -0,016. Der Roh-Return-Effekt von `swarm_buy` verschwindet nach Marktbereinigung (Markt-Timing/Beta, kein Edge). Der Score hat auf sauberen Daten keine Vorhersagekraft für AR. Auf dem vollen Master ist Spearman 0,035 (Return) bzw. -0,015 (AR).
4. AR-Streuung ist groß (SD 0,17 bei 90 Tagen); Schwellen ±3 % (`settings.py:657`) markieren damit einen Großteil aller Trades als Ausreißer.
5. 16 % aller Trades (18.095) sind `delisted`, 1.529 NaN-Marker im Cache. Delisting-Rendite (Totalverlust) wird nicht modelliert: solche Trades fallen aus Horizonten (NaN), was Survivorship-Bias erzeugt.
6. Zeitanker: Rendite ab `transaction_date` (Insider-Sicht). Die Meldeverzögerung (bis 45 Tage) ist nur im sekundären Konstrukt B (`signed_ar_disc_*`) abgebildet. Spalten dafür fehlen im Master (nur `signed_ar_30..180d`); B wird nur im Skript gerechnet.
7. Cache veraltet: letzter Kurs 2026-07-15, Stand heute 2026-10-17. `paper_return_pct` ist damit Monate alt (`price_as_of` zeigt es).

**Vermutet:** Die Master-Artefakte stammen vom Cache-Reset (Commit `68b09e6`); Gegenprobe über Pipeline-Log wurde nicht geführt.

## 3. Code-Qualität

- `post_trade_returns.py` (1.215 Z.) mischt Netzwerk/Retry/Canary, Cache-Hygiene, Rendite-Mathe, Exit-Detection und Paper-Return. Preislogik ist nur über Pandas-Zeilenschleifen (`df.at`) umgesetzt (Baseline- und Horizontphase), obwohl `abnormal_returns.py` dieselbe Aufgabe vektorisiert in ca. 100 Zeilen löst. Zwei Implementierungen derselben Preissuche (`_price_at_or_after_fast` und `_prices_for_dates`).
- `trade_score.py`: sauber, rein, testbar; Aufwand für drei Mechanismen (Paradox-Dämpfer, Ex-Officio-Bonus, Volumen-Tiers) ohne Default-Wirkung ist Totlast (alle auf 0/aus). `getattr`-Fallbacks wiederholen Settings-Defaults mehrfach.
- Schleife über Zeilen in `compute_trade_scores` statt Vektorisierung; bei 114k Zeilen akzeptabel.
- Doku-Konsistenz gut (Preis_Aktualisierung.md, Filter_Instanz_Parameter.md), aber lang und mit Datums-Annotationen durchsetzt.
- Keine Integritätsprüfung zwischen Cache-Abdeckung und Trade-Datum: Der Fehler aus 2.1 blieb unbemerkt, obwohl Tests grün sind.
- `scipy` fehlt in der venv; `score_correlation` nutzt eigene Rangkorrelation (ok).

## 4. Wiederverwendbares Wissen

- AR-Kern (`abnormal_returns.py:51-137`): vektorisierter `searchsorted`-Preislookup mit Lückenlimit, Sell-Sign genau einmal, Cluster-Bootstrap nach Member (`:267`). Eins zu eins übernehmbar.
- Erkenntnis: Roh-Return-Korrelation eines Kauf-Signals ist ohne Marktbereinigung irreführend (Swarm-Buys clustern in Hausse-Phasen).
- Richtungskonvention: positiv = gutes Timing, Verkäufe invertiert.
- yfinance-Robustheit: Ticker-Regex (`:47`), Close-Series-Extraktion (`:88`), Delisting-Marker, atomares Parquet-Speichern.
- Meldeverzug macht Konstrukt B (Anker Meldedatum) zum einzig handelbaren Maß für Follower.
- Exit-Detection auf Issuer-Ebene mit Teil-Close-Zerlegung (idee nützlich, Komplexität prüfen).

## 5. Rebuild-Empfehlung

**Technisch:**
- Kleiner Kern behalten: AR-Modul, Sell-Sign-Konvention, Ticker-Validierung, Parquet-Cache mit Atomik. `post_trade_returns.py` neu schreiben und in Fetch / Cache / Returns trennen, vektorisiert (ein Lookup für alles, inkl. Paper-Return).
- Preise als **unadjustierte Close plus Split-Faktoren** (oder `Adj Close` mit Vollrefresh je Ticker und gespeichertem Fetch-Datum) halten. Jede Return-Zeile aus genau einem Cache-Stand rechnen, Returns nicht in den Master persistieren, sondern bei Bedarf berechnen.
- Harte Integritätstests: Cache-Beginn je Ticker <= erstes Trade-Datum, sonst NaN und Warnung; Anteil Null-Returns als Alarm; Cache-Alter anzeigen.
- Delistings: Endwert/Totalverlust (z. B. letzter Kurs oder -100 %) ausweisen oder separat markieren.
- Beide Anker (Transaktion und Meldung) als Standardspalten.

**Sofort (auch ohne Rebuild):** Preis-Cache für 2013 bis 2021 nachladen (`fetch_years` auf >= 14 oder `period='max'`), dann Stage 9 komplett neu laufen lassen; bis dahin Auswertungen auf Trades ab 2021-07 beschränken.

**Fachlich zum Score:** Der Score ist faktisch `5*committee_full + 20*swarm_buy`, also ein Kategorie-Label mit drei Stufen, kein kontinuierlicher Alpha-Score. Die Gewichte sind nicht aus Daten kalibriert (Preset `Bene2` verwendet 35/20/15 etc. beliebig). Der Konstrukt-Konflikt: Der Score misst "ungewöhnliches Muster" (Ausschuss-Match, Schwarm), nicht "erwarteter Überrenditen". Auf sauberen Daten liegt der AR der Hochscore-Klassen bei null (+1,4 % bei 25 mit SE 0,5 %, nur marginal). Empfehlung: (a) Score als reine Flag-/Rangliste "Auffälligkeit" definieren statt Rendite-Erwartung zu versprechen; (b) Gewichte nicht von Hand, sondern per Regression/Walk-Forward auf AR (mit member-geclusterten Standardfehlern) schätzen, nur falls nach Datenreparatur ein Effekt bleibt; (c) Hint-Signale und Dämpfer-/Bonus-Mechanik streichen, solange sie Gewicht 0 haben; (d) Buy und Sell getrennt bewerten, Sells sind schwer interpretierbar (Liquidität, Steuern); (e) erst nach Reparatur der Preishistorie (2.1) und Neuauswertung über 2013 bis 2026 entscheiden, ob es überhaupt einen Edge gibt, den ein Rebuild transportieren soll.
