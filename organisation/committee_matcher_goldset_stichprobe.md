# Committee Matcher V2 — Stichprobe des Entwicklungs-Gold-Sets (P9)

Stand 2026-08-28. Auswahl der zu labelnden Units; die Labels selbst setzt Bene.
Umsetzung der Evaluation: `organisation/committee_matcher_v2_implementierungsplan.md`, Abschnitt P9.

## Auswahlregel

Ausschliesslich Subcommittees, weil sie mit 179 von 225 Units die Grundgesamtheit
tragen und den schwierigsten Fall stellen: enger Zuschnitt bei gleichzeitig
kurzem Jurisdiktionstext. In der Regel ein Subcommittee je Committee, damit kein
Themenfeld die Stichprobe dominiert; drei Committees stellen ein zweites, weil
nur dort Zuschnitte liegen, die vier oder fuenf Zuordnungen verlangen.

Die 22 Units bilden 19 verschiedene Committees ab und treffen die Verteilung der
Grundgesamtheit:

| Merkmal | Grundgesamtheit | Stichprobe |
|---|---|---|
| House / Senate | 109 / 70 (61 % / 39 %) | 13 / 9 (59 % / 41 %) |
| Appropriations-Subcommittees | 24 von 179 (13 %) | 2 von 22 (9 %) |
| `name_only` ohne Jurisdiktionstext | 8 von 179 (4 %) | 2 von 22 (9 %) |
| Textlaenge Median | 524 Zeichen | 648 Zeichen |

`name_only` ist bewusst ueberrepraesentiert: der Fall ist selten, aber er
entscheidet, ob das Modell ohne Evidenz halluziniert. Der Median liegt ueber dem
der Grundgesamtheit, weil die vier nachtraeglich ergaenzten Mehrfachfaelle
durchweg lange Zustaendigkeitstexte tragen.

Der Plan nennt 15 bis 20 Units; die Stichprobe liegt mit 22 darueber, weil sonst
entweder die Mehrfachzuordnung oder ein bereits abgedeckter Fall haette
entfallen muessen. Der Mehraufwand liegt beim Labeln, nicht bei den Kosten: die
Evaluation bleibt auch mit 22 Units unter 0,10 USD.

## Stichprobe

| # | Kammer | Committee | Subcommittee | Basis | Zeichen | Geprueft wird |
|---|---|---|---|---|---|---|
| 1 | house | Transportation and Infrastructure | Aviation | Text | 1691 | eindeutige Mehrfachzuordnung: Airlines, Airports, Aerospace |
| 2 | house | Energy and Commerce | Energy | Text | 949 | Versorger gegen Oel/Gas trennen |
| 3 | house | Financial Services | Financial Institutions | Text | 2040 | Banken gegen Kreditdienste trennen |
| 4 | house | Natural Resources | Energy and Mineral Resources | Text | 2443 | Rohstoffkette: Bergbau, Kohle, Oel/Gas |
| 5 | house | Ways and Means | Health | Text | 636 | Falle: Medicare heisst Versicherer, nicht Pharma |
| 6 | house | Science, Space, and Technology | Space and Aeronautics | Text | 919 | Raumfahrt ohne eigene Industry in der Taxonomie |
| 7 | house | Homeland Security | Cybersecurity and Infrastructure Protection | Text | 524 | Falle: Behoerdenaufgabe gegen Softwarebranche |
| 8 | house | Judiciary | Courts, Intellectual Property, Artificial Intelligence, and the Internet | Text | 344 | Falle: KI und Internet als Rechtsthema, nicht als Branche |
| 9 | house | Rules | Legislative and Budget Process | Text | 562 | `no_mapping` erwartet: reines Parlamentsverfahren |
| 10 | house | Appropriations | Transportation, Housing and Urban Development, and Related Agencies | Text | 13 | Extremfall: Jurisdiktionstext unter der Mindestlaenge |
| 11 | house | Foreign Affairs | Africa | Text | 417 | geografischer Zuschnitt ohne Branchenbezug |
| 12 | senate | Finance | Health Care | Name | 0 | `name_only` bei einem Namen, der eine Branche nahelegt |
| 13 | senate | Banking, Housing, and Urban Affairs | Digital Assets | Text | 524 | Falle: Krypto gegen Software und Kapitalmaerkte |
| 14 | senate | Commerce, Science, and Transportation | Telecommunications and Media | Text | 808 | zwei benachbarte Branchen im selben Text |
| 15 | senate | Environment and Public Works | Chemical Safety, Waste Management, Environmental Justice, and Regulatory Oversight | Text | 1523 | Regulierung gegen regulierte Branche |
| 16 | senate | Agriculture, Nutrition, and Forestry | Livestock, Dairy, Poultry, and Food Safety | Text | 233 | Landwirtschaft gegen Lebensmittelverarbeitung |
| 17 | senate | Homeland Security and Governmental Affairs | Border Management, Federal Workforce, and Regulatory Affairs | Name | 0 | `name_only` mit `no_mapping` als erwarteter Antwort |
| 18 | senate | Appropriations | Department of Homeland Security | Text | 31 | kurzer Text bei breitem Haushaltstitel |
| 19 | house | Energy and Commerce | Health | Text | 660 | vier bis fuenf Zuordnungen: Versicherer, Pharma, Medizintechnik, Kliniken, Gesundheits-IT |
| 20 | house | Financial Services | Housing and Insurance | Text | 1663 | vier bis fuenf Zuordnungen: Sach- und Spezialversicherer, Hypothekenfinanzierung, Wohn-REITs, Wohnungsbau |
| 21 | senate | Energy and Natural Resources | Public Lands, Forests, and Mining | Text | 710 | vier bis fuenf Zuordnungen: Industriemetalle, Gold, Kohle, Holz, Oel/Gas-Foerderung |
| 22 | senate | Commerce, Science, and Transportation | Surface Transportation, Freight, Pipelines, and Safety | Text | 712 | vier Zuordnungen: Speditionen, Bahn, Pipelines, Logistik |

## Abgedeckte Faelle

- eindeutige Einzel- und Mehrfachzuordnung (1, 2, 3, 4, 14, 16)
- Zuschnitte, die vier oder fuenf Zuordnungen verlangen und damit die Obergrenze
  des Modellvertrags treffen (19, 20, 21, 22); regulaer sind zwei, mehr nur mit
  Broad-Scope-Ausnahme, eigener Evidenz und eigener Begruendung je Zuordnung
- breiter Zuschnitt, der die Broad-Scope-Regel ausloest (4, 10, 15, 18)
- `no_mapping` als richtige Antwort (9, 11, 17)
- Falsche Freunde, bei denen der Name eine Branche nahelegt, die Zustaendigkeit
  aber eine andere ist (5, 6, 7, 8, 13)
- fehlende Evidenz: `name_only` (12, 17) und Text unter der Mindestlaenge (10, 18)
- Aufsichts- und Verfahrenszuschnitte ohne Branchenbezug (9, 11, 17)

Die drei Zweitbelegungen liegen jeweils im entferntesten Feld ihres Committees:
Energy and Commerce mit Energie und Gesundheit, Financial Services mit Banken
und Wohnen/Versicherung, Senate Commerce mit Telekommunikation und
Landverkehr. Eine thematische Verdopplung entsteht dadurch nicht.

Nicht in der Stichprobe: House Committee on Agriculture, dessen sieben Units im
P8-Lauf `p8-run2` bereits gemappt und im Review bewertet wurden. Ein Gold-Set,
das dieselben Units traegt, wuerde die Evaluation gegen bereits gesehene Faelle
messen.

## Labelvorlage

`data/committee_matcher_v2/goldset/development_goldset.xlsx`, Blatt `labels`:
eine Zeile je Unit, bis zu fuenf `industry_id_*` (Auswahlliste aus dem Snapshot
`run-p4-spike`), `no_mapping_reason` und `notiz`. Blatt `jurisdiction` traegt den
vollstaendigen Zustaendigkeitstext je Unit, Blatt `taxonomy` alle 145 Industries.

Label heisst: die vollstaendige erwartete Industry-Menge oder ausdruecklich
`no_mapping`. Eine unvollstaendige Menge bewertet der Recall spaeter als
Modellfehler.

Das endgueltige Dateiformat legt `goldset-export` in P9 fest; diese Vorlage
dient dem Labeln und wird beim Import auf das dortige Format abgebildet.
