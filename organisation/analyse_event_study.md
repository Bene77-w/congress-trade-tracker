# Event-Study / Abnormal Return — Insider-Aufdeckung (Discovery)

_Erzeugt: 2026-06-26 18:03 von `scripts/analyse_event_study.py`._

Signierter, marktbereinigter Abnormal Return (signed BHAR) als schaerferes Lineal gegenueber dem rohen Kalender-Return. `signed_ar` ist ein Bruchteil (0.03 = 3%), Verkauf invertiert das Vorzeichen (positiv = gut getimt).

## 1. Datenstand & evaluierbares N (Right-Censoring)

- Trades gesamt: **113,675**
- Benchmark: **SPY**, Bootstrap: 2000 Iter (member-geclustert ueber `bio_guide_id`, Seed 0)
- Outlier-Modus: **absolute** auf `signed_ar_90d`

| Konstrukt | Spalte | N evaluierbar | Median | Mittel |
|---|---|--:|--:|--:|
| A (transaction) | `signed_ar_30d` | 93,557 | +0.00% | -0.05% |
| A (transaction) | `signed_ar_60d` | 92,885 | +0.00% | +0.01% |
| A (transaction) | `signed_ar_90d` | 92,056 | +0.00% | -0.00% |
| A (transaction) | `signed_ar_120d` | 91,282 | +0.00% | -0.02% |
| A (transaction) | `signed_ar_180d` | 89,565 | +0.00% | -0.09% |
| B (disclosure) | `signed_ar_disc_30d` | 93,174 | +0.00% | +0.04% |
| B (disclosure) | `signed_ar_disc_90d` | 91,722 | +0.00% | +0.03% |

## 2. Diskriminator — trade_score gegen rohen Return vs. AR (H=90)

Identische Population (Zeilen, in denen `trade_score`, `post_return_90d` UND `signed_ar_90d` vorhanden sind). Nur die Marktbereinigung unterscheidet die beiden Lineale; der Horizont ist konstant.

- Population N = **92,056**
- Spearman(trade_score, `post_return_90d` — roh, signiert): **-0.0006**
- Spearman(trade_score, `signed_ar_90d` — marktbereinigt): **-0.0029**

_Sanity-Check (eigene Population): Spearman(trade_score, post_return_180d) = +0.0029 (N=89,565) — reproduziert den Report-Wert (~+0.003)._

## 3. Pro Signal — mittlerer `signed_ar_90d` mit vs. ohne (member-geclustert)

Differenz = Mittel(mit Signal) - Mittel(ohne Signal). 95%-CI + p aus member-geclustertem Block-Bootstrap (resamplet Member, nicht Trades).

| Signal | N mit | Mittel mit | Mittel ohne | Differenz | 95%-CI | p |
|---|--:|--:|--:|--:|---|--:|
| `committee_ex_officio` | 9,391 | -0.57% | +0.06% | -0.63% | [-1.10%, +0.41%] | 0.554 |
| `floor_action` | 3,890 | -0.52% | +0.02% | -0.54% | [-1.69%, +1.51%] | 0.746 |
| `swarm_buy` | 9,479 | +0.14% | -0.02% | +0.16% | [-0.14%, +0.52%] | 0.296 |
| `swarm_sell` | 9,217 | -0.04% | +0.00% | -0.04% | [-0.38%, +0.22%] | 0.741 |
| `accumulative` | 46,618 | +0.00% | -0.00% | +0.00% | [-0.18%, +0.21%] | 0.950 |

_Der Vorgaenger-Report wies `committee_ex_officio` als robust signifikant aus (t=-2.55). Unter dem member-geclusterten Bootstrap ist KEIN Signal von 0 unterscheidbar (alle CIs umschliessen 0, p oben) — die fruehere Signifikanz war ein Clustering-Artefakt (Schwarm-Korrelation als Schein-N)._

## 4. Schwellen-Kalibrierung (Ausreisser-Definition)

`signed_ar_90d` ist stark heavy-tailed (90d Single-Name vs. SPY). Die aktuelle absolute Schwelle (±3%) flaggt **38%** der evaluierbaren Trades (19% unter -3%, 19% ueber +3%) — das ist KEIN Tail.

| Perzentil | Wert |
|---|--:|
| p1 | -34.82% |
| p5 | -17.45% |
| p10 | -10.48% |
| p50 | -0.00% |
| p90 | +10.65% |
| p95 | +17.51% |
| p99 | +33.83% |

Fuer eine echte Tail-Isolation: engere absolute Schwellen (~±18% entspricht p95) oder `AR_OUTLIER_MODE='percentile'` (p95/p99). `ar_outliers.csv` ist nach `signed_ar_90d` absteigend sortiert — die extremsten Trades stehen oben.

## 5. Ausreisser-Charakterisierung

Ausreisser auf `signed_ar_90d` (Modus absolute): **34,908** ( +17,553 / -17,355 ), von 92,056 evaluierbaren.

**Nach Signal** (ein Ausreisser kann mehrere Signale tragen)

| Signal | Ausreisser | positiv | negativ | Anteil an Signal-Trades |
|---|--:|--:|--:|--:|
| `committee_ex_officio` | 4,393 | 2,127 | 2,266 | 46.8% |
| `floor_action` | 3,307 | 1,690 | 1,617 | 85.0% |
| `swarm_buy` | 3,466 | 1,665 | 1,801 | 36.6% |
| `swarm_sell` | 3,740 | 1,973 | 1,767 | 40.6% |
| `accumulative` | 17,278 | 8,741 | 8,537 | 37.1% |

**Nach Kammer**

| Kammer | Ausreisser | positiv | negativ |
|---|--:|--:|--:|
| House | 32,465 | 16,281 | 16,184 |
| Senate | 2,443 | 1,272 | 1,171 |

**Nach Sektor**

| Sektor | Ausreisser | positiv | negativ |
|---|--:|--:|--:|
| Technology | 7,374 | 3,689 | 3,685 |
| Financial Services | 4,761 | 2,382 | 2,379 |
| Healthcare | 4,733 | 2,411 | 2,322 |
| Consumer Cyclical | 3,994 | 1,994 | 2,000 |
| Industrials | 3,973 | 1,989 | 1,984 |
| Consumer Defensive | 2,555 | 1,290 | 1,265 |
| Communication Services | 2,413 | 1,216 | 1,197 |
| Basic Materials | 1,305 | 678 | 627 |
| Real Estate | 1,267 | 656 | 611 |
| Energy | 1,159 | 588 | 571 |
| (leer) | 821 | 391 | 430 |
| Utilities | 553 | 269 | 284 |

**Nach Member (Top 20)**

| Member (Top 20) | Ausreisser | positiv | negativ |
|---|--:|--:|--:|
| Ro Khanna | 12,371 | 6,119 | 6,252 |
| Michael T. McCaul | 4,393 | 2,255 | 2,138 |
| Josh Gottheimer | 1,834 | 896 | 938 |
| Lisa Mcclain | 1,138 | 554 | 584 |
| Gilbert Cisneros | 926 | 463 | 463 |
| Daniel Goldman | 916 | 525 | 391 |
| Tuberville, Tommy | 816 | 420 | 396 |
| Diana Harshbarger | 752 | 398 | 354 |
| Kathy Manning | 535 | 244 | 291 |
| Rob Bresnahan | 511 | 260 | 251 |
| Jefferson Shreve | 479 | 263 | 216 |
| Julie Johnson | 413 | 226 | 187 |
| Lois Frankel | 355 | 175 | 180 |
| Kevin Hern | 338 | 162 | 176 |
| Carper, Thomas R. | 324 | 157 | 167 |
| Jared Moskowitz | 319 | 145 | 174 |
| Mullin, Markwayne | 316 | 159 | 157 |
| Valerie Hoyle | 294 | 140 | 154 |
| Virginia Foxx | 284 | 152 | 132 |
| Marjorie Taylor Greene | 280 | 128 | 152 |

## 6. Grenzen

- **Right-Censoring:** Preis-Cache endet 2026-06-24; Trades mit `t0 + H` dahinter sind nicht evaluierbar (NaN). N pro Horizont siehe Abschnitt 1.
- **Member-Konzentration:** 69% der `committee_ex_officio`-Ausreisser entfallen auf einen einzigen Member (Ro Khanna) — der Punkt-Effekt ist member-getrieben, nicht zwingend ex-officio-getrieben (Filter-Rekonstruktion droht "ist das <Member>" zu lernen).
- **Geltungsbereich:** getestet ist nur das marktbereinigte **Kalender**-Lineal bei H=90. Event-verankerte Lineale (Votes, Konstrukt C) sind aufgeschoben — kein Urteil ueber die Signale insgesamt, nur ueber den 90d-Kalender-Edge.
- **Konstrukt B** (disclosure) ist staerker zensiert als A (Melde-Latenz).
- **Niedrige Decke:** Der Vorgaenger-Report zeigt R^2 aller Signale ~1.2% — ein echtes ~0 ist ein moegliches, valides Ergebnis (Diskriminator).
- **Konstrukt B misst Copycat**, nicht Insider-Information (skaliert mit Sichtbarkeit/Trade-Groesse) — nicht als Informationsbeweis lesen.
