# 09 Frontend-Audit (Streamlit)

Stand: Commit `875ea55` (Branch `agent/committe_matcher`). Scope: `app/` (Streamlit-Frontend) und die Frontend-Tests. Keine Code- oder Datenänderungen im Repo; Hilfsdateien nur unter `/tmp/audit_frontend/`.

Kurzbefund: Das Frontend ist funktional breit (6 Tabs, Sidebar mit Scoring-Editor, Presets, Popups, Analyse-Tab), aber es ist ein Monolith mit eingebetteter Fachlogik. Ein Teil der Score-, Filter- und Analyse-Logik sitzt im UI-Layer, die ganze Master-CSV wird ungefiltert in den Speicher geladen, und die App selbst ist nicht end-to-end getestet.

---

## 1. Features (Katalog je Tab)

Datenquelle für alle Tabs: `data/processed/trades_master_complete.csv` (642 MB, ca. 114 k Zeilen, 89 Spalten), geladen in `load_trades()` (`app/streamlit_app.py:190-220`, `@st.cache_data`). Es gibt keine `usecols`-Einschränkung.

### Sidebar (global, gilt für alle Tabs außer wo angegeben)

| Feature | Fundstelle | Wirkung |
|---|---|---|
| Datumsbasis (`disclosure_date` / `transaction_date`, Default transaction) | `app/streamlit_app.py:606` | Bezugsspalte für Zeitfenster und Aggregation |
| Zeitfenster 7 / 30 / 365 Tage / Alles (Default 30) | `:616`, Anker in `_window_anchor` `:581` | Cutoff ab Datenstand (max. `disclosure_date`) |
| Suche (Token-UND, reihenfolgeunabhängig; Name, Asset, Ticker, Issuer, Hash-ID) | `:643`, `_search_mask` `:968` | Wirkt in allen Tabs |
| Sortierrichtung trade_score | `:653` | Sortierung Trades/Assets/Members/Detail |
| Min-Score-Slider | `:660` | Filter auf `trade_score` bzw. `score_max` |
| Primärer Return-Horizont (Selectbox, Default 90d) | `:668` | Spalte `post_return_{X}d` in Asset-/Member-Aggregaten; Grenze für Horizont-Filter |
| Horizont-Filter aktiv (Checkbox) | `:687` | Blendet Trades aus, deren erster Close nach dem Horizont liegt (`apply_prefilter` `:993`); im Tab "Score-Analyse" automatisch deaktiviert |
| Spalten-Auswahl je Tab (Multiselect) | `:702-721`, Listen `:44-110` | Sichtbare Spalten Trades/Assets/Members |
| Signale ausschließen (Multiselect) | `:722` | Nullt Gewicht, `score_recompute.recompute_subset` |
| Score-Gewichte (Expander, Form) | `:730-899` | Gewicht je Signal, trade_volume-Tier-Editor (`st.data_editor`), committee_ex_officio-Boni, swarm_paradox-Damper; Anwenden/Defaults/Speichern |
| Presets laden / löschen / speichern | `:730-786`, `:747-786`, `app/scoring_presets.py` | JSON-Datei `data/config/scoring_presets.json`, Backup `.bak` bei Korruption |

### Tab "Trades" (`app/streamlit_app.py:1714-1763`)

- Kennzahlen: Anzahl gefilterter Trades, Mean/Max Score, Anzahl ausgeschlossener Signale, "Override aktiv".
- Interaktive Tabelle (`st.dataframe`, Single-Row-Selektion), Ticker als Google-Link, Signale nur als Primärsignale (`_primary_only`).
- Klick auf Zeile öffnet Popup "Trade-Detail" (`render_trade_detail`, `:1331`): Scope-Toggle "Alle Trades", Toggle "Andere Politiker einbeziehen", Kurschart mit Trade-Markern/Exit-Markern, Related-Trades-Tabelle, Post-Trade-Returns, Partial-Close-Zerlegung (`chart_helpers.build_partial_close_rows`).

### Tab "Assets" (`:1765-1797`)

- Aggregation je `issuer_ticker` (`aggregate_assets`, `:271`): Trade-Count, Member-Count, score_max/avg/total, Ø/Max Return des primären Horizonts, % profitabel, Signale, Volumen, Sektoren, Kammern, Daten.
- Kennzahlen: Anzahl Assets, Max score_max, Σ Volumen. Spaltenauswahl aus Sidebar.
- Kein Klick-Popup, kein Export.

### Tab "Members" (`:1799-1875`)

- Aggregation je `(name, chamber)` (`aggregate_members`, `:319`).
- Klick auf Zeile öffnet Popup "Member-Detail" mit `render_member_detail` (`app/member_detail.py:722`). Popup nutzt die volle Member-Historie (Sidebar-Zeitfenster und Horizont-Filter wirken dort bewusst nicht), Score wird für den Member neu gerechnet, wenn Overrides aktiv sind.
- Member-Detail-Popup hat vier Unter-Tabs (`member_detail.py:737`):
  - **Übersicht**: Header, Gremien (`_render_committees`, `:197`), Red Flags (`_render_red_flags`, `:612`), Sektor-Konzentration (Z3, `_fig_z3`), Top-Trades.
  - **Performance**: Aggregationsart (Radio), D1 Treffsicherheit je Horizont, D5 Verteilung terminaler Returns, D6 Score -> Ø Return, D2 Member vs. Andere vs. Committee-Full (mit Datentabelle), D7 Score -> Anteil > Schwellwert X %.
  - **Verläufe**: D4 alle Trade-Trajektorien (Spaghetti, Top-N-Auswahl plus Toggle "Alle Verläufe"), D3b nur committee_full-Trades.
  - **Zeit**: Z1+Z2 Kalender-Timing.
  - Abnormal Return (`_render_abnormal_return`, `:636`) mit Horizont-Radio und Ausreißer-Quote.
- Steuerelemente im Popup: Quartal-Range-Slider, "Nur ≤180d"-Toggle, Schwellwert-Slider (0-50 %), Trajektorien-Cap.

### Tab "Post Trade Performance" (`:1877-1966`)

- Tabelle der Top-N Trades nach Score (Default 25, "+100" und "Alle anzeigen"), Selectbox für Einzeltrade, dann `render_trade_detail` (Chart + Kontext). Kennzahl `effective_return_pct` als Hauptwert.
- Hinweis: Tab fehlt ein Export.

### Tab "Recent Trades" (`:1968-2132`)

- Eigenes Zeitfenster (Selectbox aus `settings.RECENT_MONITORING_WINDOW_OPTIONS`), umgeht Sidebar-Zeitfenster, Horizont-Filter und Min-Score; nur die globale Suche wirkt.
- Kennzahlen: Anzahl, offene Positionen (held), Anzahl mit Paper Return (Mark-to-Market gegen letzten Close).
- Multi-Row-Auswahl; CSV-Export "alle gefilterten Zeilen" oder "Auswahl" über `staging_export.export_selection` (`app/staging_export.py:114`). Schreibt nach `data/agent_exports/trade_selection_<ts>.csv`, kein Browser-Download.

### Tab "Score-Analyse" (`:2148-2336`)

- Validierung des trade_score gegen `post_return_{X}d` (X wählbar). Kammer- und Partei-Filter wirken auf alle Auswertungen.
- Kennzahlen: Spearman rho (Leitkennzahl), Pearson r, n, davon held.
- Scatter (`Scattergl`, Y auf 1.-99. Perzentil geklippt, Sample-Limit), Score-Bucket-Balkendiagramm mit Median-Hover, Pro-Signal-Tabelle (n gefeuert, Δ, Trefferquote, CI 95 % per member-geclusterter Bootstrap, p, Spearman, Pearson).
- Limitationen (Delisting-Bias, Reifebeschränkung) stehen als Caption im Tab.

### Kein Feature

- Kein CSV-Download im Browser (nur Schreiben auf Platte, s.o.).
- Keine Login-/Auth-Schicht (lokale App).
- Keine Seite für Pipeline-Status oder Datenaktualität außer dem Caption "Wirksames Fenster / Datenstand" in der Sidebar.

---

## 2. Funktionsprüfung

### Belegt

- **Tests grün:** 10 Frontend-nahe Testdateien (`tests/test_streamlit_app_fixes.py`, `test_chart_helpers.py`, `test_member_metrics.py`, `test_member_detail_coherence.py`, `test_member_detail_fixes.py`, `test_member_detail_recompute_apptest.py`, `test_score_correlation.py`, `test_score_recompute.py`, `test_scoring_presets_corrupt.py`, `test_staging_export.py`): **143 passed in 3.96 s**. Lauf in `/tmp/audit_frontend/pytest.log`.
- **App startet headless:** `streamlit run app/streamlit_app.py --server.headless true --server.port 8599` antwortete mit HTTP 200; Log ohne Error/Traceback/Exception (`/tmp/audit_frontend/streamlit.log`). Der Prozess wurde danach beendet. Der Startcheck prüft nur den Server-Start und die Seite, nicht die Rendering-Pfade aller Tabs.
- **Score-Recompute nutzt die Pipeline-Engine:** `app/score_recompute.py` ruft `scoring.trade_score.compute_trade_scores` mit einem Settings-Proxy auf (kein Duplikat der Score-Formel).
- **Kein End-to-End-Test der Haupt-App:** `tests/test_member_detail_recompute_apptest.py` (Zeile 10) vermerkt, dass `streamlit_app.py` per AppTest nicht fahrbar ist; getestet wird eine Inline-Mini-App. Die 6 Tabs der Hauptdatei sind damit ungetestet.

### Vermutet (nicht gemessen)

- **Speicher:** `pd.read_csv` lädt die komplette 642-MB-CSV mit 89 Spalten (viele JSON-Strings) in den RAM. Schätzung: mehrere GB, nicht gemessen. Parsing der JSON-Spalten `Relevant_Sectors` per Python-`map` (`:191-220`) bei jedem Cache-Miss.
- **Laufzeit pro Rerun:** Jeder Widget-Klick führt das Skript neu aus. Bei aktiven Gewichts-Overrides (oder ausgeschlossenen Signalen) wird `recompute_subset` auf der gesamten `corr_base` gerechnet (`:1047-1054`), also auf allen ca. 114 k Trades. Assets-/Members-Aggregation mit Lambda-`agg` läuft ebenfalls pro Rerun ungecacht (`:271`, `:319`).
- **Ungecachter Price-Cache im Trade-Detail:** `render_trade_detail` importiert `_load_price_cache` direkt aus `scoring.post_trade_returns` und ruft es ohne `st.cache_data` auf (`app/streamlit_app.py:1340` Import, `:1371` Aufruf im `with spinner_ctx`-Block). Jede Detail-Interaktion liest die 25-MB-Parquet-Datei neu. Die Member-Variante ist dagegen korrekt gecacht (`app/member_detail.py:138-141`). Das ist ein belegtes Inkonsistenz-Problem mit vermutlich spürbarer Latenz.
- **Bootstrap-Kosten:** `per_signal_return_table` mit clustered Bootstrap (`app/score_correlation.py`) läuft bei jedem Rerun des Score-Analyse-Tabs, ohne Cache. Laufzeit nicht gemessen.
- **Hardcodierte 200:** `_max_score = max(..., 200)` in Detail- und Recent-Tab (`:1915`, `:2041`); der Trades-Tab nutzt `max_observed_score` aus dem Datenstand. Inkonsistent, aber harmlos bei aktuellem Score-Bereich.
- **Hidden Coupling:** `_ar_sorted_dist` (`:223-234`) liest die Modul-Globale `trades`. Funktioniert, ist aber nicht gegen Umbenennung oder Aufruf vor dem Laden abgesichert.
- **Abhängigkeit zu einer Variablen aus Schleife:** Im Score-Gewichte-Form werden `form_vol_enabled`, `form_vol_trigger`, `tier_df_out` nur im Schleifenkörper für `trade_volume` definiert (`:808-845`) und später im Apply-Block (`:922`) verwendet. Praktisch unkritisch, da `trade_volume` immer in `DEFAULTS['weights']` liegt (`config/settings.py:186`), aber fragil.
- **Export-Pfad:** `export_selection` schreibt nach `data/agent_exports/` (`config/settings.py:692`). Es gibt keine Prüfung auf Überschreiben; der Dateiname enthält einen Zeitstempel, daher unkritisch.

---

## 3. Code-Qualität

### Monolith

- `app/streamlit_app.py` = 2336 Zeilen, Hauptskript ohne Funktionsstruktur für die Tabs. Enthalten: ca. 200 Zeilen CSS-Inline (`:372-526`), Loader, Aggregation, Sidebar mit Scoring-Editor (~320 Zeilen), Prefilter, sechs Tab-Blöcke mit eigenen Dialogen, Column-Configs, Score-Analyse. Tests können nur die wenigen extrahierten Helfer prüfen.
- Bereits extrahiert und sauber getrennt: `chart_helpers.py`, `score_correlation.py` (pure pandas, ohne Streamlit), `score_recompute.py`, `scoring_presets.py`, `staging_export.py`, `glossary.py`.
- `app/member_detail.py` (834 Z.) und `app/member_metrics.py` (785 Z.): Mischung aus Streamlit-Rendering und reiner Berechnungslogik. `member_metrics.py` ist laut Docstring "pandas-only", der Rest ist UI.

### Fachlogik im UI-Layer (wichtigster Befund)

Diese Logik gehört nach Konzept in die Pipeline oder in ein eigenes Analyse-Modul, nicht in die Anzeige:

| Logik | Fundstelle | Bewertung |
|---|---|---|
| Sichtbarkeitsregel Horizont-Filter (`first_close_offset_days <= horizon`) | `streamlit_app.py:993-1020` | Fachregel, im UI definiert |
| Zeitfenster-Anker = max `disclosure_date` (Datenstand) | `:581-600` | Fachregel mit Begründung im Docstring; gehört in Datenstand-Export |
| Primär-vs-Hint-Signal-Trennung für Tabellen | `:245-275` | Fachregel, dupliziert Wissen aus `SCORING_PRIMARY_SIGNALS` |
| Score-Neuberechnung mit Ausschluss-Semantik (Exclude setzt Damper 1.0, leert Ex-Officio-Boni, entfernt Primärsignal aus Gate) | `app/score_recompute.py:52-115` | Entscheidungen der Scoring-Engine im UI-Modul; Risiko von Divergenz zur Pipeline |
| Analyse-Population (Dedup, Exchange-Ausschluss), Committee-Eligibility-Maske | `app/score_correlation.py:131-170` | Analysefachlogik, testbar, aber im App-Ordner |
| Bootstrap, Spearman, Bucket-Definition, Trefferquote | `app/score_correlation.py` | Statistik im UI-Ordner |
| Trajektorien, Red Flags, AR-Perzentil, Sektor-Konzentration | `app/member_metrics.py` (`:194`, `:692`, `:726`, `:765`, `:597`) | Fachlogik in 785 Zeilen |
| Ex-Officio-Bonus aus Breakdown-String parsen (`ex_officio:+N`) | `app/score_correlation.py:28`, `:110` | Parst Engine-Output per Regex, Fragilität bei Formatänderung |

### Duplikate

- **Richtungs-Mapping:** `app/staging_export.py:40` (`_normalize_direction`) und `app/member_metrics.py:149` (wrapper) bzw. `signals/committee_signal.py:52` (`_direction_from_transaction_type`). Die Logik `'purchase' -> buy`, `'sale'/'sell' -> sell` steht in zwei Varianten; die Pipeline-Funktion ist Quelle, `staging_export` hat eine eigene Kopie.
- **Scope-Helfer:** `_scope_label` / `_scope_help` in `streamlit_app.py:570-578` und `member_detail.py:91-104` identisch.
- **Ticker-Google-Link:** `_TICKER_LINK_REGEX` und `_ticker_google_url` in `streamlit_app.py:1076-1090` und `member_detail.py:35, 111`.
- **Price-Cache-Laden:** gecacht in `member_detail.py:138`, ungecacht in `streamlit_app.py:1341`. Ein Code-Pfad, zwei Verhaltensweisen.
- **Trade-Aggregation:** `aggregate_assets` und `aggregate_members` sind strukturell gleich (Groupby + gleiche Agg-Spec-Bausteine), unterscheiden sich in Gruppierungskey und Feldern.
- **Konstanten:** `COMMITTEE_SIGNALS` (`score_correlation.py:156`) und die Committee-Signal-Liste im Scoring sind separat gepflegt.

### Weitere Auffälligkeiten

- Kein Typing-Schutz für `st.session_state`-Keys; ca. 40 String-Keys verteilt über das Skript.
- Die CSS-Datei ist als Python-String eingebettet (nicht wartbar, keine Theme-Variablen).
- Dialoge (`@st.dialog`) sind innerhalb des Tab-Blocks definiert (`:1750`, `:1839`), dadurch schwer testbar.
- Emojis: keine gefunden; Deutsch mit korrekten Umlauten.

---

## 4. Wiederverwendbares Wissen

Kandidaten, die in einen Rebuild übernommen werden sollten, weil sie Fachwissen oder UX-Entscheidungen kodieren:

- **Glossar** (`app/glossary.py`, 210 Zeilen): Erklärtexte zu Score, Spearman, Pearson, Gewicht, Preset, Horizont, Override, Partial-Close, Datumsbasis. Eine Quelle für alle `help=`-Texte. `help_for(key)` ist tolerant.
- **Scope-Präfixe** (`settings.UI_SCOPE_PREFIX` / `UI_SCOPE_HELP`): Die Labels zeigen an, ob ein Widget global, im Tab oder im Detail-Popup wirkt. Das ist ein gutes UX-Konzept, weil die Geltungsbereiche sonst unklar sind.
- **Scoring-Presets** (`app/scoring_presets.py`): JSON-Store mit Default-Payload, Korruptions-Backup (`.bak`) und einmaliger Warnung. Gewichte, Damper, Trade-Volume-Tiers und Ex-Officio-Boni als ein Payload.
- **Score-Analyse-Methodik**: Spearman als Leitkennzahl (robust gegen Ausreißer), Bootstrap mit Member-Clustering für CI, Delisting-Bias und Reifebeschränkung als offen ausgewiesene Limitationen, "marginal, nicht isoliert" als Hinweis bei Pro-Signal-Korrelation. Methodisch sauber und wertvoll.
- **Recent-Monitoring-Konzept**: Papier-Return (Mark-to-Market) getrennt vom Objective Return; eigenes Fenster unabhängig vom Horizont-Filter.
- **Member-Detail-Konzept**: Trajektorien-Spaghetti mit Top-N-Cap, Benchmark (Andere Politiker, Committee-Full), Red-Flag-Ampel, Kalender-Timing.
- **Zeitfenster-Anker-Regel** (Datenstand = max `disclosure_date`, für beide Datumsbasen): gute Entscheidung mit Begründung im Code.

---

## 5. Rebuild-Empfehlung

Das Frontend ist nicht der Grund für einen Komplett-Rebuild. Es lohnt sich, die UI-Schicht neu zu schneiden, sobald die Fachlogik sauber aus ihr herausgelöst ist. Konkret:

1. **Datenschicht zuerst.** Die Pipeline schreibt eine schlanke, typisierte Frontend-Tabelle (nur benötigte Spalten, Parquet, JSON-Felder bereits als Anzeige-Spalten aufbereitet). `load_trades()` liest diese Tabelle. Das beseitigt Speicherrisiko und Parse-Zeit beim Start.
2. **Fachlogik in ein Analyse-Paket.** `score_correlation`, `member_metrics`-Berechnung, `score_recompute` und die Filterregeln (Horizont, Zeitfenster-Anker, Primär/Hint) gehören in `analysis/` ohne Streamlit-Import. Das Frontend ruft nur auf.
3. **Score-Neuberechnung an einer Stelle.** Entweder Engine bietet einen expliziten "Konfiguration -> Score"-Pfad, oder die UI ruft nur noch eine Service-Funktion auf. Die Ausschluss-Semantik (Damper 1.0 etc.) gehört ins Scoring-Modul und ist dort zu testen.
4. **UI in Seiten zerlegen.** Ein Modul je Tab mit Funktion `render(ctx)`, Sidebar als eigene Komponente, CSS als Datei, Dialoge als Top-Level-Funktionen. Ziel: Tabs einzeln testbar.
5. **Caching konsequent.** Price-Cache und Trajektorien einheitlich über `st.cache_data`, Aggregation je Filterzustand cachen (Key = Filter-Hash).
6. **End-to-End-Test.** Mindestens ein `streamlit.testing.v1.AppTest` über die Haupt-App (alle Tabs rendern, Filter setzen, Selektion simulieren). Heute fehlt dies.
7. **Duplikate beseitigen** (Richtung, Scope-Helfer, Ticker-Link, Price-Cache-Lader) als Quick-Win bereits vor dem Rebuild.

Empfehlung für den Owner: Die Frontend-Features (Katalog oben) sind als Anforderungsliste wiederverwendbar. Der Rebuild lohnt sich vor allem als Entkopplung von Daten, Analyse und UI. Eine Neuschreibung der UI allein würde die Fachlogik-Probleme nicht lösen.

---

Belege: `/tmp/audit_frontend/pytest.log` (143 passed), `/tmp/audit_frontend/streamlit.log` (Startcheck ohne Fehler).
